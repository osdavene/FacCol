import hashlib
import hmac
import re
import requests
from django.db import transaction
from django.utils import timezone

from apps.core.mail_service import enviar_correo_sistema
from apps.core.platform_settings import decrypt_secret, get_platform_setting, mask_secret
from apps.contabilidad.services import registrar_asiento_recibo
from apps.empresas.models import Empresa, EmpresaModulo
from apps.facturacion.models import ReciboCaja, TransaccionWompi
from apps.usuarios.models import User, EmpresaUsuario


_REQUIRED_WOMPI_SIG_PROPS = {
    "transaction.id",
    "transaction.status",
    "transaction.amount_in_cents",
}


def get_platform_wompi_config():
    """
    Configuración Wompi EXCLUSIVA de la plataforma FacCol (página principal y
    compra de planes/suscripciones SaaS). Nunca se usa para facturas de inquilinos.
    """
    pub = get_platform_setting("wompi_public_key", "").strip()
    priv = get_platform_setting("wompi_private_key", "").strip()
    integ = get_platform_setting("wompi_integrity_secret", "").strip()
    events = get_platform_setting("wompi_events_secret", "").strip()
    active_flag = get_platform_setting("wompi_active_flag", "1").strip()
    has_required = bool(pub and integ)
    is_prod = pub.startswith("pub_prod_")

    return {
        "source": "plataforma",
        "enabled": has_required and active_flag != "0",
        "wompi_active_flag": active_flag != "0",
        "has_required_keys": has_required,
        "public_key": pub,
        "private_key": priv,
        "integrity_secret": integ,
        "events_secret": events,
        "is_production": is_prod,
        "api_base": "https://production.wompi.co/v1" if is_prod else "https://sandbox.wompi.co/v1",
        "masked_private_key": mask_secret(priv),
        "masked_integrity_secret": mask_secret(integ),
        "masked_events_secret": mask_secret(events),
    }


def get_wompi_runtime_config(empresa=None):
    """
    Obtiene configuración Wompi estrictamente aislada:
    - Si se pasa una `empresa` inquilina, lee ÚNICAMENTE las llaves propias de esa empresa.
      Jamás hace fallback a la pasarela de la plataforma (para no mezclar recaudos).
    - Si `empresa` es None, devuelve la pasarela global de la plataforma (para landing/planes).
    """
    if empresa is None:
        return get_platform_wompi_config()

    pub = (empresa.wompi_public_key or "").strip()
    priv = decrypt_secret(empresa.wompi_private_key).strip()
    integ = decrypt_secret(empresa.wompi_integrity_secret).strip()
    events = decrypt_secret(empresa.wompi_events_secret).strip()
    has_required = bool(pub and integ)
    is_prod = pub.startswith("pub_prod_")

    return {
        "source": "empresa",
        "enabled": has_required,
        "has_required_keys": has_required,
        "public_key": pub,
        "private_key": priv,
        "integrity_secret": integ,
        "events_secret": events,
        "is_production": is_prod,
        "api_base": "https://production.wompi.co/v1" if is_prod else "https://sandbox.wompi.co/v1",
        "masked_private_key": mask_secret(priv),
        "masked_integrity_secret": mask_secret(integ),
        "masked_events_secret": mask_secret(events),
    }


def build_integrity_signature(reference: str, amount_in_cents: int, currency: str, integrity_secret: str) -> str:
    raw = f"{reference}{amount_in_cents}{currency}{integrity_secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_nested_value(data, dotted_path: str) -> str:
    current = data
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = None
        if current is None:
            return ""
    return str(current)


def verify_wompi_event_checksum(payload, header_checksum: str, events_secret: str) -> bool:
    if not events_secret or not isinstance(payload, dict):
        return False
    signature = payload.get("signature")
    data = payload.get("data")
    if not isinstance(signature, dict) or not isinstance(data, dict):
        return False
    properties = signature.get("properties")
    if not isinstance(properties, list) or not properties:
        return False
    if not _REQUIRED_WOMPI_SIG_PROPS.issubset({str(p) for p in properties}):
        return False
    timestamp = payload.get("timestamp")
    received = str(signature.get("checksum") or header_checksum or "").strip().lower()
    if timestamp is None or not received:
        return False

    concat_parts = [_resolve_nested_value(data, str(p)) for p in properties]
    concat_parts.extend([str(timestamp), events_secret])
    calculated = hashlib.sha256("".join(concat_parts).encode("utf-8")).hexdigest().lower()
    return hmac.compare_digest(calculated, received)


def activate_wompi_transaction(tx: TransaccionWompi) -> bool:
    """
    Aplica idempotentemente una transacción Wompi APPROVED:
    - Si está asociada a una factura de inquilino -> crea ReciboCaja y asiento contable del inquilino.
    - Si está asociada a un Plan SaaS en la página principal -> activa/crea empresa y suscripción del cliente.
    """
    from datetime import date
    from decimal import Decimal
    from django.utils.crypto import get_random_string

    with transaction.atomic():
        now = timezone.now()
        claimed = TransaccionWompi.objects.filter(id=tx.id, activated_at__isnull=True).update(
            activated_at=now, updated_at=now
        )
        if not claimed:
            return False

        tx.refresh_from_db()
        if tx.factura:
            fac = tx.factura
            ultimo_rc = ReciboCaja.objects.filter(empresa=tx.empresa).order_by("-consecutivo").first()
            consec_rc = (ultimo_rc.consecutivo + 1) if ultimo_rc else 1

            rc = ReciboCaja.objects.create(
                empresa=tx.empresa,
                consecutivo=consec_rc,
                factura=fac,
                tercero=fac.tercero,
                fecha=date.today(),
                valor=Decimal(tx.amount_in_cents) / Decimal("100"),
                metodo_pago="WOMPI",
                referencia_pago=tx.wompi_transaction_id or tx.reference,
                observaciones=f"Pago en línea Wompi ({tx.payment_method_type or 'Checkout'})",
            )
            fac.recalcular_totales()
            registrar_asiento_recibo(rc)
        elif tx.plan and tx.empresa is None:
            plan = tx.plan
            emp = None
            temp_pwd = None
            email = (tx.customer_email or "").strip().lower()
            nit = (tx.customer_nit or "").strip()

            if nit:
                emp = Empresa.objects.filter(nit=nit).first()

            if emp is None and tx.usuario and not tx.usuario.es_superadmin_user():
                rel = tx.usuario.empresas_asignadas.filter(activo=True).select_related("empresa").first()
                if rel:
                    emp = rel.empresa

            if emp is not None:
                emp.plan = plan
                emp.activa = True
                emp.save(update_fields=["plan", "activa"])
            else:
                nombre_emp = (tx.customer_name or "").strip() or (email.split("@")[0] if email else f"Cliente {tx.id}")
                nit_final = nit or f"SAAS-{tx.id}-{get_random_string(4).upper()}"
                emp = Empresa.objects.create(
                    razon_social=nombre_emp,
                    nombre_comercial=nombre_emp,
                    nit=nit_final,
                    email=email,
                    plan=plan,
                    activa=True,
                )
                if email:
                    user = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=email).first()
                    if not user:
                        temp_pwd = get_random_string(12)
                        user = User.objects.create_user(
                            username=email,
                            email=email,
                            password=temp_pwd,
                            first_name=nombre_emp[:30],
                        )
                    EmpresaUsuario.objects.get_or_create(
                        user=user,
                        empresa=emp,
                        defaults={"rol": "propietario", "activo": True},
                    )
                    tx.usuario = user

            emp.sincronizar_modulos_con_plan(plan)

            tx.empresa_creada = emp
            tx.save(update_fields=["empresa_creada", "usuario"])

            if email:
                detalle_clave = (
                    f"<p><b>Usuario:</b> {email}<br><b>Contraseña temporal:</b> <code>{temp_pwd}</code></p>"
                    if temp_pwd
                    else f"<p>Tu empresa <b>{emp.display_name}</b> ya tiene activo el plan <b>{plan.nombre}</b>.</p>"
                )
                enviar_correo_sistema(
                    email,
                    f"Activación FacCol — Plan {plan.nombre}",
                    f"<h2>¡Bienvenido a FacCol!</h2><p>Confirmamos tu pago por Wompi ({tx.reference}).</p>{detalle_clave}",
                    fail_silently=True,
                )
    return True


def sync_wompi_transaction_by_id(wompi_tx_id: str, wompi_cfg: dict, expected_empresa=None, platform_only: bool = False):
    clean_tx_id = str(wompi_tx_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", clean_tx_id):
        return None
    headers = {}
    if wompi_cfg.get("private_key"):
        headers["Authorization"] = f"Bearer {wompi_cfg['private_key']}"

    resp = requests.get(f"{wompi_cfg['api_base']}/transactions/{clean_tx_id}", headers=headers, timeout=15)
    if resp.status_code != 200:
        return None

    tx_data = (resp.json() or {}).get("data") or {}
    ref = str(tx_data.get("reference") or "").strip()
    if not ref:
        return None

    q = TransaccionWompi.objects.filter(reference=ref)
    if platform_only:
        q = q.filter(empresa__isnull=True)
    elif expected_empresa is not None:
        q = q.filter(empresa=expected_empresa)
    tx = q.first()
    if not tx:
        return None

    wompi_status = str(tx_data.get("status") or "PENDING").upper()
    paid_cents = int(tx_data.get("amount_in_cents") or 0)
    paid_currency = str(tx_data.get("currency") or "").upper()
    real_id = str(tx_data.get("id") or clean_tx_id).strip()

    if tx.activated_at is not None:
        if wompi_status == "VOIDED" and tx.status != "VOIDED":
            tx.status = "VOIDED"
            tx.save(update_fields=["status", "updated_at"])
        return tx

    if real_id and TransaccionWompi.objects.filter(wompi_transaction_id=real_id, activated_at__isnull=False).exclude(id=tx.id).exists():
        return tx

    tx.wompi_transaction_id = real_id
    tx.payment_method_type = str(tx_data.get("payment_method_type") or tx.payment_method_type or "")
    tx.raw_payload = tx_data

    if wompi_status == "APPROVED":
        if tx.amount_in_cents > 0 and paid_cents >= tx.amount_in_cents and paid_currency == tx.currency:
            tx.status = "APPROVED"
            tx.save()
            activate_wompi_transaction(tx)
        else:
            tx.status = "AMOUNT_MISMATCH"
            tx.save()
    else:
        tx.status = wompi_status
        tx.save()

    return tx

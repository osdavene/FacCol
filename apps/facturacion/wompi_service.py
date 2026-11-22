import hashlib
import hmac
import html as _html
import re
import requests
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.config import mail_settings
from apps.core.mail import is_dummy_or_invalid_email, send_password_reset_email
from apps.core.mail_service import enviar_correo_sistema
from apps.core.notifications import (
    destinatarios_propietarios_empresa,
    destinatarios_superadmins,
    notificar_evento_seguridad,
    notificar_usuarios_app,
)
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
        pago_info = None
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
            pago_info = ("factura", fac, rc)
        elif tx.plan and tx.empresa is None:
            plan = tx.plan
            emp = None
            user_creado = None
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
                        user = User.objects.create_user(
                            username=email,
                            email=email,
                            password=get_random_string(20),
                            first_name=nombre_emp[:30],
                            email_verified_at=now,
                        )
                        user_creado = user
                    EmpresaUsuario.objects.get_or_create(
                        user=user,
                        empresa=emp,
                        defaults={"rol": "propietario", "activo": True},
                    )
                    tx.usuario = user

            emp.sincronizar_modulos_con_plan(plan)

            tx.empresa_creada = emp
            tx.save(update_fields=["empresa_creada", "usuario"])
            pago_info = ("plan", emp, plan, email, user_creado)

    app_base = (mail_settings.APP_URL or "").rstrip("/")
    if pago_info and pago_info[0] == "factura":
        _, fac, rc = pago_info
        correo_cli = (getattr(fac.tercero, "email", "") or tx.customer_email or "").strip().lower()
        if correo_cli and not is_dummy_or_invalid_email(correo_cli):
            html_recibo = (
                f"<h3>Pago recibido correctamente</h3>"
                f"<p>Hola <b>{_html.escape(fac.tercero.razon_social)}</b>,</p>"
                f"<p>Confirmamos tu pago en línea vía Wompi por <b>${rc.valor:,.0f} COP</b> "
                f"aplicado a <b>{_html.escape(fac.numero_completo)}</b>.</p>"
                f"<p><b>Recibo de Caja:</b> RC-{rc.consecutivo}<br>"
                f"<b>Referencia Wompi:</b> {_html.escape(tx.reference)}</p>"
            )
            enviar_correo_sistema(
                correo_cli,
                f"Comprobante de pago {fac.numero_completo} — {tx.empresa.display_name}",
                html_recibo,
                empresa=tx.empresa,
                fail_silently=True,
            )

        fac_url = reverse("facturacion:factura_detalle", args=[fac.id])
        resumen_pago = (
            f"Pago aprobado vía Wompi por ${rc.valor:,.0f} COP para {fac.numero_completo} "
            f"(Recibo RC-{rc.consecutivo}, Ref. {tx.reference})."
        )
        notificar_usuarios_app(
            f"Pago Wompi recibido: {fac.numero_completo}",
            resumen_pago,
            empresa=tx.empresa,
            roles=("propietario", "admin", "contador"),
            nivel="success",
            categoria="wompi",
            url=fac_url,
        )
        notificar_evento_seguridad(
            None,
            f"Pago Wompi recibido: {fac.numero_completo}",
            resumen_pago,
            destinatarios=destinatarios_propietarios_empresa(tx.empresa),
            empresa=tx.empresa,
            action_url=f"{app_base}{fac_url}" if app_base else fac_url,
            action_text="Ver Factura y Recibo",
            categoria="wompi",
            nivel="success",
            registrar_in_app=False,
        )
    elif pago_info and pago_info[0] == "plan":
        _, emp, plan, email, user_creado = pago_info
        login_url = f"{app_base}{reverse('usuarios:login')}" if app_base else reverse("usuarios:login")
        if user_creado is not None:
            uid = urlsafe_base64_encode(force_bytes(user_creado.pk))
            token = default_token_generator.make_token(user_creado)
            reset_url = f"{app_base}{reverse('usuarios:password_reset_confirm', args=[uid, token])}" if app_base else reverse("usuarios:password_reset_confirm", args=[uid, token])
            send_password_reset_email(user_creado, reset_url)
        elif email and not is_dummy_or_invalid_email(email):
            enviar_correo_sistema(
                email,
                f"Activación FacCol — Plan {plan.nombre}",
                (
                    f"<h2>¡Bienvenido a FacCol!</h2>"
                    f"<p>Confirmamos tu pago por Wompi ({_html.escape(tx.reference)}).</p>"
                    f"<p>Tu empresa <b>{_html.escape(emp.display_name)}</b> ya tiene activo el plan <b>{_html.escape(plan.nombre)}</b>.</p>"
                    f"<p><a href='{_html.escape(login_url, quote=True)}'>Ingresar a FacCol</a></p>"
                ),
                fail_silently=True,
            )

        resumen_saas = (
            f"Se aprobó suscripción Wompi al plan '{plan.nombre}' para '{emp.display_name}' "
            f"(NIT {emp.nit}, Ref. {tx.reference}, contacto {email or 'no registrado'})."
        )
        bo_url = reverse("backoffice:empresas_lista")
        notificar_usuarios_app(
            f"Nueva venta SaaS Wompi: {plan.nombre}",
            resumen_saas,
            empresa=None,
            roles=(),
            superadmins=True,
            nivel="success",
            categoria="wompi",
            url=bo_url,
        )
        notificar_evento_seguridad(
            None,
            f"Nueva suscripción pagada por Wompi: {plan.nombre}",
            resumen_saas,
            destinatarios=destinatarios_superadmins(),
            action_url=f"{app_base}{bo_url}" if app_base else bo_url,
            action_text="Ver Empresas en Backoffice",
            categoria="wompi",
            nivel="success",
            registrar_in_app=False,
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

"""Servicio unificado de notificaciones in-app y alertas por correo electrónico en FacCol."""

import ipaddress
import logging
import socket
from datetime import date
from types import SimpleNamespace
from typing import Iterable, Optional
from urllib.parse import urlparse

import httpx
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.audit import _extraer_ip, registrar_actividad
from apps.core.mail import is_dummy_or_invalid_email, send_notification_email

logger = logging.getLogger("faccol.notifications")

CATEGORIAS_PREFERENCIAS = (
    ("wompi", "Pagos Wompi y recaudos"),
    ("dian", "DIAN y Documentos Soporte"),
    ("facturacion", "Notas Crédito y facturación"),
    ("caja", "Cierres y descuadres de caja POS"),
    ("inventario", "Stock crítico y salidas de Kardex"),
    ("plan", "Cupo mensual y estado de plan"),
    ("nomina", "Liquidaciones de nómina"),
)


def usuario_quiere_correo(user, categoria: str) -> bool:
    """Seguridad es siempre obligatoria; categorías operativas respetan preferencias del usuario."""
    if not categoria or categoria == "seguridad":
        return True
    prefs = getattr(user, "preferencias_notificaciones", None)
    if not isinstance(prefs, dict):
        return True
    return bool(prefs.get(f"email_{categoria}", True))


def es_url_webhook_valida(url: str) -> bool:
    """Valida URL http/https externa bloqueando loopback, redes privadas e IPs internas (SSRF)."""
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.hostname:
        return False
    host = parsed.hostname.lower().strip(".")
    if host in ("localhost", "ip6-localhost") or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ip_obj = ipaddress.ip_address(host)
        return ip_obj.is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            addr = info[4][0]
            ip_obj = ipaddress.ip_address(addr.split("%", 1)[0])
            if not ip_obj.is_global:
                return False
    except socket.gaierror:
        return True
    except Exception:
        return False
    return True


def _enviar_webhook_empresa(
    empresa,
    titulo: str,
    resumen: str,
    *,
    categoria: str,
    nivel: str,
    url: Optional[str] = None,
) -> bool:
    wh_url = (getattr(empresa, "webhook_notificaciones_url", "") or "").strip() if empresa else ""
    if not wh_url or not es_url_webhook_valida(wh_url):
        return False
    payload = {
        "source": "faccol",
        "empresa": empresa.display_name,
        "nit": empresa.nit,
        "categoria": categoria,
        "nivel": nivel,
        "titulo": titulo,
        "mensaje": resumen,
        "text": f"[{empresa.display_name}] {titulo}: {resumen}",
        "url": url or "",
        "timestamp": timezone.localtime().isoformat(),
    }
    try:
        httpx.post(wh_url, json=payload, timeout=4.0, follow_redirects=False)
        return True
    except Exception as exc:
        logger.warning("Error enviando webhook externo de empresa %s: %s", empresa.id, exc)
        return False


def _as_recipient(email: str, nombre: str = ""):
    clean = (email or "").strip().lower()
    lbl = (nombre or "").strip() or clean.split("@", 1)[0] or "Usuario"
    return SimpleNamespace(email=clean, first_name=lbl, username=clean)


def _normalizar_ruta_local(url: Optional[str]) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("/"):
        return raw[:255]
    try:
        parsed = urlparse(raw)
        if parsed.path and parsed.path.startswith("/"):
            return (parsed.path + (f"?{parsed.query}" if parsed.query else ""))[:255]
    except Exception:
        pass
    return ""


def destinatarios_propietarios_empresa(
    empresa,
    *,
    incluir_empresa_email: bool = True,
    incluir_roles: tuple[str, ...] = ("propietario",),
    excluir_emails: Optional[Iterable[str]] = None,
) -> list[str]:
    """Obtiene correos válidos de propietarios/roles activos indicados y del correo oficial de la empresa."""
    if not empresa:
        return []

    excluir = {(e or "").strip().lower() for e in (excluir_emails or []) if e}
    candidatos = []

    try:
        rels = empresa.usuarios_asignados.filter(
            activo=True,
            rol__in=incluir_roles,
            user__activo=True,
            user__is_active=True,
        ).select_related("user")
        for rel in rels:
            em = (rel.user.email or "").strip().lower()
            if em:
                candidatos.append(em)
    except Exception:
        pass

    if incluir_empresa_email and getattr(empresa, "email", ""):
        candidatos.append(empresa.email.strip().lower())

    vistos = set()
    resultado = []
    for em in candidatos:
        if em and em not in vistos and em not in excluir and not is_dummy_or_invalid_email(em):
            vistos.add(em)
            resultado.append(em)
    return resultado


def destinatarios_superadmins() -> list[str]:
    """Obtiene correos válidos de SuperAdmins activos de la plataforma."""
    from apps.usuarios.models import User

    candidatos = list(
        User.objects.filter(
            Q(is_superadmin=True) | Q(is_superuser=True),
            is_active=True,
            activo=True,
        ).values_list("email", flat=True)
    )
    vistos = set()
    resultado = []
    for raw in candidatos:
        em = (raw or "").strip().lower()
        if em and em not in vistos and not is_dummy_or_invalid_email(em):
            vistos.add(em)
            resultado.append(em)
    return resultado


def notificar_usuarios_app(
    titulo: str,
    mensaje: str,
    *,
    empresa=None,
    roles: tuple[str, ...] = ("propietario", "admin"),
    superadmins: bool = False,
    nivel: str = "info",
    categoria: str = "sistema",
    url: str = "",
    extra_usuarios=None,
) -> int:
    """Crea notificaciones persistentes in-app (campanita) por cada usuario destinatario."""
    from apps.core.models import Notificacion
    from apps.usuarios.models import User

    usuarios_map = {}

    if empresa is not None and roles:
        try:
            rels = empresa.usuarios_asignados.filter(
                activo=True,
                rol__in=roles,
                user__activo=True,
                user__is_active=True,
            ).select_related("user")
            for rel in rels:
                usuarios_map[rel.user_id] = rel.user
        except Exception:
            pass

    if superadmins:
        for u in User.objects.filter(
            Q(is_superadmin=True) | Q(is_superuser=True),
            is_active=True,
            activo=True,
        ):
            usuarios_map[u.id] = u

    for u in (extra_usuarios or []):
        if u is not None and getattr(u, "id", None) and getattr(u, "is_active", True):
            usuarios_map[u.id] = u

    if not usuarios_map:
        return 0

    clean_url = _normalizar_ruta_local(url)
    registros = [
        Notificacion(
            empresa=empresa,
            usuario=u,
            categoria=categoria[:40],
            nivel=nivel if nivel in ("info", "success", "warning", "danger") else "info",
            titulo=titulo[:180],
            mensaje=mensaje,
            url=clean_url,
        )
        for u in usuarios_map.values()
    ]
    Notificacion.objects.bulk_create(registros)
    return len(registros)


def _espejar_en_campanita(
    destinatarios: list[str],
    titulo: str,
    resumen: str,
    *,
    empresa=None,
    nivel: str = "info",
    categoria: str = "seguridad",
    url: str = "",
):
    from apps.usuarios.models import User

    emails_validos = [d for d in destinatarios if d]
    if not emails_validos:
        return
    try:
        usuarios = list(User.objects.filter(email__in=emails_validos, is_active=True, activo=True))
        if usuarios:
            notificar_usuarios_app(
                titulo,
                resumen,
                empresa=empresa,
                roles=(),
                nivel=nivel,
                categoria=categoria,
                url=url,
                extra_usuarios=usuarios,
            )
    except Exception as exc:
        logger.warning("No se pudo guardar notificación in-app '%s': %s", titulo, exc)


def notificar_evento_seguridad(
    request,
    titulo: str,
    resumen: str,
    *,
    destinatarios: Iterable[str],
    actor=None,
    empresa=None,
    action_url: Optional[str] = None,
    action_text: Optional[str] = None,
    extra_lineas: Optional[Iterable[str]] = None,
    categoria: str = "seguridad",
    nivel: str = "warning",
    registrar_in_app: bool = True,
) -> int:
    """
    Envía notificaciones de seguridad/auditoría por correo y opcionalmente en campanita in-app
    sin bloquear el flujo principal ante fallos de red. Retorna correos enviados.
    """
    actor_obj = actor
    if actor_obj is None and request is not None:
        u_req = getattr(request, "user", None)
        if u_req is not None and getattr(u_req, "is_authenticated", False):
            actor_obj = u_req

    if actor_obj is not None:
        actor_nom = (
            (actor_obj.get_full_name() if hasattr(actor_obj, "get_full_name") else "") or ""
        ).strip() or getattr(actor_obj, "email", "") or getattr(actor_obj, "username", "Usuario")
        if hasattr(actor_obj, "es_superadmin_user") and actor_obj.es_superadmin_user():
            actor_rol = "SuperAdmin"
        elif empresa is not None and hasattr(actor_obj, "rol_en_empresa"):
            actor_rol = actor_obj.rol_en_empresa(empresa) or "usuario"
        else:
            actor_rol = "usuario"
        quien = f"{actor_nom} ({actor_rol})"
    else:
        quien = "Sistema / Usuario"

    emp_nombre = empresa.display_name if empresa is not None else "Plataforma SaaS FacCol"
    fecha_txt = timezone.localtime().strftime("%d/%m/%Y %H:%M") + " (COT)"
    ip_txt = _extraer_ip(request) or "No registrada"

    partes = [
        resumen,
        "",
        f"Empresa: {emp_nombre}",
        f"Ejecutado por: {quien}",
        f"Fecha y hora: {fecha_txt}",
        f"Dirección IP: {ip_txt}",
    ]
    for ln in (extra_lineas or []):
        if ln:
            partes.append(ln)
    partes.append("")
    partes.append(
        "Si no reconoces esta actividad, restablece tu contraseña o comunícate de inmediato con el administrador de tu empresa."
    )
    cuerpo = "\n".join(partes)

    enviados = 0
    vistos = []
    seen_set = set()
    for raw_dest in destinatarios:
        dest = (raw_dest or "").strip().lower()
        if not dest or dest in seen_set:
            continue
        seen_set.add(dest)
        vistos.append(dest)

    optout_emails = set()
    if categoria != "seguridad" and vistos:
        from apps.usuarios.models import User

        for u in User.objects.filter(email__in=vistos, is_active=True, activo=True):
            if not usuario_quiere_correo(u, categoria):
                optout_emails.add((u.email or "").strip().lower())

    for dest in vistos:
        if dest in optout_emails or is_dummy_or_invalid_email(dest):
            continue
        try:
            nombre_dest = empresa.display_name if (empresa and getattr(empresa, "email", "").strip().lower() == dest) else ""
            if send_notification_email(
                _as_recipient(dest, nombre_dest),
                f"FacCol Seguridad — {titulo}",
                titulo,
                cuerpo,
                action_url=action_url,
                action_text=action_text,
            ):
                enviados += 1
        except Exception as exc:
            logger.warning("Error enviando alerta de seguridad '%s' a %s: %s", titulo, dest, exc)

    if registrar_in_app and vistos:
        _espejar_en_campanita(
            vistos,
            titulo,
            resumen,
            empresa=empresa,
            nivel=nivel,
            categoria=categoria,
            url=_normalizar_ruta_local(action_url),
        )

    if empresa is not None:
        _enviar_webhook_empresa(
            empresa,
            titulo,
            resumen,
            categoria=categoria,
            nivel=nivel,
            url=action_url,
        )

    return enviados


def verificar_alertas_cupo_plan(empresa, request=None) -> Optional[str]:
    """
    Alerta una sola vez al mes a propietarios y en campanita cuando el consumo
    mensual de documentos alcanza el 80% o el 100% del plan contratado.
    """
    from apps.core.models import RegistroActividad
    from apps.facturacion.models import Factura

    if not empresa or not empresa.plan or not empresa.plan.limite_facturas_mes:
        return None

    limite = int(empresa.plan.limite_facturas_mes or 0)
    if limite <= 0:
        return None

    hoy = date.today()
    usados = (
        Factura.objects.filter(
            empresa=empresa,
            tipo_documento__in=("FACTURA", "POS_ELECTRONICO"),
            fecha_emision__gte=hoy.replace(day=1),
        )
        .exclude(estado="ANULADA")
        .count()
    )
    porcentaje = int((usados * 100) / limite)

    if porcentaje >= 100:
        umbral = ("ALERTA_CUPO_100", "danger", "Límite mensual de documentos alcanzado (100%)",
                  f"{empresa.display_name} alcanzó {usados} de {limite} documentos mensuales del plan '{empresa.plan.nombre}'.")
    elif porcentaje >= 80:
        umbral = ("ALERTA_CUPO_80", "warning", f"Consumo del {porcentaje}% de tu plan mensual",
                  f"{empresa.display_name} lleva {usados} de {limite} documentos emitidos este mes en el plan '{empresa.plan.nombre}'.")
    else:
        return None

    accion, nivel, titulo, resumen = umbral
    ya_alertado = RegistroActividad.objects.filter(
        empresa=empresa,
        accion=accion,
        created_at__year=hoy.year,
        created_at__month=hoy.month,
    ).exists()
    if ya_alertado:
        return None

    registrar_actividad(request, "planes", accion, resumen, empresa=empresa, objeto=empresa)
    url_plan = reverse("core:mi_plan")
    action_url = request.build_absolute_uri(url_plan) if request else url_plan
    notificar_usuarios_app(
        titulo,
        resumen,
        empresa=empresa,
        roles=("propietario", "admin"),
        nivel=nivel,
        categoria="plan",
        url=url_plan,
    )
    notificar_evento_seguridad(
        request,
        titulo,
        resumen,
        destinatarios=destinatarios_propietarios_empresa(empresa),
        empresa=empresa,
        action_url=action_url,
        action_text="Ver Mi Plan y Ampliar Cupo",
        categoria="plan",
        nivel=nivel,
        registrar_in_app=False,
    )
    return accion


def notificar_stock_critico_si_aplica(empresa, producto, request=None) -> bool:
    """
    Si un producto cae en o por debajo de stock_minimo, alerta máximo 1 vez al día
    por producto a propietarios/administradores (campanita + correo).
    """
    from apps.core.models import RegistroActividad

    if (
        not empresa
        or not producto
        or not getattr(producto, "maneja_inventario", False)
        or not producto.stock_minimo
        or producto.stock_minimo <= 0
        or producto.stock_actual > producto.stock_minimo
    ):
        return False

    hoy = date.today()
    ya_hoy = RegistroActividad.objects.filter(
        empresa=empresa,
        accion="ALERTA_STOCK_MINIMO",
        objeto_id=str(producto.id),
        created_at__date=hoy,
    ).exists()
    if ya_hoy:
        return False

    titulo = f"Stock crítico: {producto.nombre}"
    resumen = (
        f"El producto '{producto.nombre}' (código {producto.codigo}) quedó en "
        f"{producto.stock_actual} unidades (mínimo configurado: {producto.stock_minimo})."
    )
    registrar_actividad(
        request,
        "inventario",
        "ALERTA_STOCK_MINIMO",
        resumen,
        empresa=empresa,
        objeto=producto,
    )
    url_inv = reverse("inventario:productos_lista")
    action_url = request.build_absolute_uri(url_inv) if request else url_inv
    notificar_usuarios_app(
        titulo,
        resumen,
        empresa=empresa,
        roles=("propietario", "admin", "operador"),
        nivel="warning",
        categoria="inventario",
        url=url_inv,
    )
    notificar_evento_seguridad(
        request,
        titulo,
        resumen,
        destinatarios=destinatarios_propietarios_empresa(empresa),
        empresa=empresa,
        action_url=action_url,
        action_text="Revisar Inventario",
        categoria="inventario",
        nivel="warning",
        registrar_in_app=False,
    )
    return True

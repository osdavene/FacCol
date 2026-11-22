"""Servicio unificado de notificaciones de seguridad y cambios críticos en FacCol."""

import logging
from types import SimpleNamespace
from typing import Iterable, Optional

from django.utils import timezone

from apps.core.audit import _extraer_ip
from apps.core.mail import is_dummy_or_invalid_email, send_notification_email

logger = logging.getLogger("faccol.notifications")


def _as_recipient(email: str, nombre: str = ""):
    clean = (email or "").strip().lower()
    lbl = (nombre or "").strip() or clean.split("@", 1)[0] or "Usuario"
    return SimpleNamespace(email=clean, first_name=lbl, username=clean)


def destinatarios_propietarios_empresa(
    empresa,
    *,
    incluir_empresa_email: bool = True,
    excluir_emails: Optional[Iterable[str]] = None,
) -> list[str]:
    """Obtiene correos válidos de propietarios activos y del correo oficial de la empresa."""
    if not empresa:
        return []

    excluir = {(e or "").strip().lower() for e in (excluir_emails or []) if e}
    candidatos = []

    try:
        rels = empresa.usuarios_asignados.filter(
            activo=True,
            rol="propietario",
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
) -> int:
    """
    Envía notificaciones de seguridad/auditoría sin bloquear el flujo principal ante fallos de red.
    Retorna cuántos correos se entregaron con éxito.
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
    vistos = set()
    for raw_dest in destinatarios:
        dest = (raw_dest or "").strip().lower()
        if not dest or dest in vistos or is_dummy_or_invalid_email(dest):
            continue
        vistos.add(dest)
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

    return enviados

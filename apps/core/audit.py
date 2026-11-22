"""Bitácora centralizada de auditoría multi-tenant para FacCol."""

import logging

from apps.core.models import RegistroActividad

logger = logging.getLogger("faccol.audit")


def _extraer_ip(request):
    if not request or not hasattr(request, "META"):
        return None
    xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if xff:
        return xff.split(",")[0].strip()[:45]
    return (request.META.get("REMOTE_ADDR") or "").strip()[:45] or None


def registrar_actividad(
    request_or_user,
    modulo: str,
    accion: str,
    descripcion: str,
    *,
    empresa=None,
    objeto=None,
    datos_extra=None,
    user=None,
):
    """
    Registra de forma segura quién ejecutó cada acción en FacCol sin interrumpir
    el flujo principal ante errores transitorios.
    """
    try:
        request = request_or_user if hasattr(request_or_user, "META") else None
        actor = user
        if actor is None:
            if request is not None:
                u_req = getattr(request, "user", None)
                if u_req is not None and getattr(u_req, "is_authenticated", False):
                    actor = u_req
            elif hasattr(request_or_user, "pk") and hasattr(request_or_user, "username"):
                actor = request_or_user

        emp = empresa
        if emp is None and request is not None:
            emp = getattr(request, "empresa_activa", None)
        if emp is None and objeto is not None and hasattr(objeto, "empresa"):
            emp = getattr(objeto, "empresa", None)

        nombre_actor = "Sistema / Anónimo"
        rol_actor = "sistema"
        if actor is not None:
            nombre_actor = (
                (actor.get_full_name() if hasattr(actor, "get_full_name") else "") or ""
            ).strip() or getattr(actor, "email", "") or getattr(actor, "username", "Usuario")
            if hasattr(actor, "es_superadmin_user") and actor.es_superadmin_user():
                rol_actor = "superadmin"
            elif emp is not None and hasattr(actor, "rol_en_empresa"):
                rol_actor = actor.rol_en_empresa(emp) or "usuario"
            else:
                rol_actor = "usuario"
        elif request is not None and isinstance(datos_extra, dict) and datos_extra.get("login_intentado"):
            nombre_actor = str(datos_extra.get("login_intentado"))[:150]
            rol_actor = "anonimo"

        obj_tipo = objeto.__class__.__name__[:60] if objeto is not None else ""
        obj_id = str(getattr(objeto, "pk", "") or "")[:60] if objeto is not None else ""

        return RegistroActividad.objects.create(
            usuario=actor if (actor is not None and getattr(actor, "pk", None)) else None,
            usuario_nombre=str(nombre_actor)[:150],
            rol=str(rol_actor)[:40],
            empresa=emp,
            modulo=str(modulo)[:40],
            accion=str(accion)[:80],
            descripcion=str(descripcion),
            objeto_tipo=obj_tipo,
            objeto_id=obj_id,
            ip_address=_extraer_ip(request),
            datos_extra=datos_extra or {},
        )
    except Exception as exc:
        logger.warning("No se pudo registrar auditoría (%s/%s): %s", modulo, accion, exc)
        return None

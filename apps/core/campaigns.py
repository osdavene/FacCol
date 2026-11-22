"""Servicios de comunicados masivos por Resend (anti-spam por tandas) y banner de novedades FacCol."""

import hashlib
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from django.utils import timezone

from apps.core.config import mail_settings
from apps.core.mail import (
    build_campaign_email_html,
    is_dummy_or_invalid_email,
    send_resend_only_email,
)
from apps.core.platform_settings import get_platform_setting, set_platform_setting
from apps.usuarios.models import User

CAMPAIGN_SETTING_KEY = "email_campaign_state"
BANNER_SETTING_KEY = "student_announcement_banner"

DAILY_SAFE_CAP = 50
DEFAULT_BATCH_SIZE = 45

DEFAULT_CAMPAIGN_STATE: Dict[str, Any] = {
    "campaign_id": "novedades-faccol-2026",
    "subject": "Novedades en FacCol: Facturación DIAN, Caja Express POS y Control Empresarial en la Nube",
    "body_text": (
        "Tu plataforma contable y de facturación electrónica en la nube sigue evolucionando para facilitar la operación diaria de tu negocio.\n\n"
        "¿Qué tienes disponible hoy dentro de FacCol?\n"
        "- Facturación Electrónica y DEE POS DIAN: emite facturas y tiquetes térmicos de 58/80mm con QR, CUFE y validación de tope 5 UVT.\n"
        "- Caja Express POS y Fiados: registra ventas ágiles con Consumidor Final en 1 clic y controla saldos y abonos de tus clientes.\n"
        "- Compras, Kardex e Impuestos: gestiona compras por pacas, Documento Soporte Electrónico DIAN, retenciones automáticas e IBUA/ICUI.\n"
        "- Contabilidad PUC y Nómina Electrónica: consulta Balance de Prueba, Libro Auxiliar por Tercero y liquida nómina en pocos clics.\n\n"
        "Ingresa ahora con tu correo y mantén toda tu empresa al día desde cualquier lugar."
    ),
    "cta_label": "Ingresar a Mi Empresa en FacCol",
    "cta_url": f"{mail_settings.APP_URL}/auth/login/",
    "batch_size": DEFAULT_BATCH_SIZE,
    "tested_fingerprint": "",
    "tested_email": "",
    "tested_at": "",
    "sent_log": {},
    "daily_counts": {},
    "last_run_at": "",
    "last_run_summary": "",
}

DEFAULT_BANNER_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "campaign_id": "faccol-novedades-2026-v1",
    "badge": "Novedad FacCol",
    "title": "¡Nuevas herramientas activas en tu cuenta FacCol!",
    "message": (
        "Ya puedes gestionar tu Caja Express POS, Compras con Documento Soporte DIAN, Estados Financieros PUC y tus solicitudes de plan desde un solo lugar."
    ),
    "cta_label": "Explorar Mi Plan",
    "cta_url": "/mi-plan/",
    "secondary_label": "Ir a Configuración",
    "secondary_url": "/configuracion/",
}


def compute_campaign_fingerprint(
    subject: str, body_text: str, cta_label: str, cta_url: str
) -> str:
    """Calcula una huella determinística SHA-256 del borrador actual para exigir prueba previa."""
    raw = json.dumps(
        [
            (subject or "").strip(),
            (body_text or "").strip(),
            (cta_label or "").strip(),
            (cta_url or "").strip(),
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _normalize_cta_url(raw_url: str, *, allow_relative: bool, default_url: str) -> str:
    clean = (raw_url or "").strip()
    if not clean:
        return default_url
    if clean.lower().startswith(("https://", "http://")):
        return clean
    if allow_relative and clean.startswith("/") and not clean.startswith("//"):
        return clean
    return default_url


def get_campaign_state() -> Dict[str, Any]:
    """Lee el estado persistido de la campaña desde ConfiguracionPlataforma."""
    raw = get_platform_setting(CAMPAIGN_SETTING_KEY, "")
    state = dict(DEFAULT_CAMPAIGN_STATE)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                state.update(parsed)
        except json.JSONDecodeError:
            pass
    if not isinstance(state.get("sent_log"), dict):
        state["sent_log"] = {}
    if not isinstance(state.get("daily_counts"), dict):
        state["daily_counts"] = {}
    return state


def save_campaign_state(state: Dict[str, Any]) -> None:
    set_platform_setting(
        CAMPAIGN_SETTING_KEY,
        json.dumps(state, ensure_ascii=False),
        is_secret=False,
    )


def _eligible_users() -> List[User]:
    users = (
        User.objects.filter(
            is_active=True,
            activo=True,
            is_superadmin=False,
            is_superuser=False,
            email_verified_at__isnull=False,
        )
        .order_by("id")
    )
    return [u for u in users if not is_dummy_or_invalid_email(u.email)]


def get_campaign_summary() -> Dict[str, Any]:
    """Calcula métricas en tiempo real de elegibles verificados, enviados, pendientes y cupo diario."""
    state = get_campaign_state()
    cid = str(state.get("campaign_id") or DEFAULT_CAMPAIGN_STATE["campaign_id"])
    sent_for_cid: Dict[str, str] = state["sent_log"].get(cid, {})
    if not isinstance(sent_for_cid, dict):
        sent_for_cid = {}

    eligible = _eligible_users()
    pending = [u for u in eligible if str(u.id) not in sent_for_cid]

    today_key = timezone.localdate().strftime("%Y-%m-%d")
    sent_today = int(state["daily_counts"].get(today_key, 0) or 0)
    remaining_today = max(0, DAILY_SAFE_CAP - sent_today)

    current_fp = compute_campaign_fingerprint(
        state.get("subject", ""),
        state.get("body_text", ""),
        state.get("cta_label", ""),
        state.get("cta_url", ""),
    )
    test_passed = bool(
        state.get("tested_fingerprint") and state.get("tested_fingerprint") == current_fp
    )

    return {
        "campaign_id": cid,
        "subject": state.get("subject", ""),
        "body_text": state.get("body_text", ""),
        "cta_label": state.get("cta_label", ""),
        "cta_url": state.get("cta_url", ""),
        "batch_size": max(1, min(DAILY_SAFE_CAP, int(state.get("batch_size") or DEFAULT_BATCH_SIZE))),
        "eligible_total": len(eligible),
        "sent_in_campaign": len(eligible) - len(pending),
        "pending_count": len(pending),
        "sent_today": sent_today,
        "daily_cap": DAILY_SAFE_CAP,
        "remaining_today": remaining_today,
        "test_passed": test_passed,
        "tested_email": state.get("tested_email", ""),
        "tested_at": state.get("tested_at", ""),
        "last_run_at": state.get("last_run_at", ""),
        "last_run_summary": state.get("last_run_summary", ""),
    }


def _update_draft_fields(
    state: Dict[str, Any],
    *,
    subject: str,
    body_text: str,
    cta_label: str,
    cta_url: str,
    batch_size: int,
) -> Dict[str, Any]:
    state["subject"] = (subject or "").strip() or DEFAULT_CAMPAIGN_STATE["subject"]
    state["body_text"] = (body_text or "").strip() or DEFAULT_CAMPAIGN_STATE["body_text"]
    state["cta_label"] = (cta_label or "").strip() or DEFAULT_CAMPAIGN_STATE["cta_label"]
    state["cta_url"] = _normalize_cta_url(
        cta_url,
        allow_relative=False,
        default_url=DEFAULT_CAMPAIGN_STATE["cta_url"],
    )
    state["batch_size"] = max(1, min(DAILY_SAFE_CAP, int(batch_size or DEFAULT_BATCH_SIZE)))
    return state


def save_campaign_draft(
    *,
    subject: str,
    body_text: str,
    cta_label: str,
    cta_url: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Dict[str, Any]:
    state = _update_draft_fields(
        get_campaign_state(),
        subject=subject,
        body_text=body_text,
        cta_label=cta_label,
        cta_url=cta_url,
        batch_size=batch_size,
    )
    save_campaign_state(state)
    return state


def send_campaign_test_email(
    *,
    test_email: str,
    admin_name: str,
    subject: str,
    body_text: str,
    cta_label: str,
    cta_url: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sender_fn: Optional[Callable[[str, str, str, str], bool]] = None,
) -> Tuple[bool, str]:
    """Guarda el borrador, envía correo de prueba vía Resend y habilita el envío de tanda."""
    state = _update_draft_fields(
        get_campaign_state(),
        subject=subject,
        body_text=body_text,
        cta_label=cta_label,
        cta_url=cta_url,
        batch_size=batch_size,
    )

    clean_test_email = (test_email or "").strip().lower()
    if not clean_test_email or is_dummy_or_invalid_email(clean_test_email):
        save_campaign_state(state)
        return False, "Ingresa un correo de prueba real y válido antes de continuar."

    html_body = build_campaign_email_html(
        recipient_name=admin_name or "Administrador",
        subject=state["subject"],
        body_text=state["body_text"],
        cta_label=state["cta_label"],
        cta_url=state["cta_url"],
    )

    mailer = sender_fn or (
        lambda to_e, to_n, subj, html_b: send_resend_only_email(to_e, to_n, subj, html_b)
    )
    ok = mailer(
        clean_test_email,
        admin_name or "Administrador",
        f"[PRUEBA] {state['subject']}",
        html_body,
    )
    if ok:
        state["tested_fingerprint"] = compute_campaign_fingerprint(
            state["subject"],
            state["body_text"],
            state["cta_label"],
            state["cta_url"],
        )
        state["tested_email"] = clean_test_email
        state["tested_at"] = timezone.localtime().strftime("%Y-%m-%d %H:%M")
        save_campaign_state(state)
        return (
            True,
            f"Prueba enviada vía Resend a {clean_test_email}. El envío de tanda quedó habilitado para este texto.",
        )

    save_campaign_state(state)
    return (
        False,
        "No se pudo enviar la prueba por Resend API. Verifica RESEND_API_KEY en .env o Backoffice antes de enviar tandas.",
    )


def send_campaign_batch(
    *,
    subject: str,
    body_text: str,
    cta_label: str,
    cta_url: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sender_fn: Optional[Callable[[str, str, str, str], bool]] = None,
    sleep_seconds: float = 0.52,
) -> Dict[str, Any]:
    """Envía una tanda acotada anti-spam únicamente por Resend tras validar prueba previa."""
    state = _update_draft_fields(
        get_campaign_state(),
        subject=subject,
        body_text=body_text,
        cta_label=cta_label,
        cta_url=cta_url,
        batch_size=batch_size,
    )

    current_fp = compute_campaign_fingerprint(
        state["subject"],
        state["body_text"],
        state["cta_label"],
        state["cta_url"],
    )
    if state.get("tested_fingerprint") != current_fp:
        save_campaign_state(state)
        return {
            "ok": False,
            "error": "pretest_required",
            "message": "Primero debes enviar y confirmar una prueba del mensaje actual antes de enviar una tanda.",
            "sent_count": 0,
            "failed_count": 0,
            "pending_remaining": get_campaign_summary()["pending_count"],
        }

    today_key = timezone.localdate().strftime("%Y-%m-%d")
    sent_today = int(state["daily_counts"].get(today_key, 0) or 0)
    remaining_today = max(0, DAILY_SAFE_CAP - sent_today)
    if remaining_today <= 0:
        save_campaign_state(state)
        return {
            "ok": False,
            "error": "daily_cap_reached",
            "message": f"Ya alcanzaste el tope seguro de {DAILY_SAFE_CAP} correos hoy para proteger tu reputación ante spam. Continúa mañana.",
            "sent_count": 0,
            "failed_count": 0,
            "pending_remaining": get_campaign_summary()["pending_count"],
        }

    cid = str(state.get("campaign_id") or DEFAULT_CAMPAIGN_STATE["campaign_id"])
    sent_for_cid: Dict[str, str] = state["sent_log"].setdefault(cid, {})
    eligible = _eligible_users()
    pending = [u for u in eligible if str(u.id) not in sent_for_cid]

    effective_limit = min(state["batch_size"], remaining_today)
    targets = pending[:effective_limit]
    if not targets:
        save_campaign_state(state)
        return {
            "ok": True,
            "error": "",
            "message": "Todos los usuarios verificados ya recibieron este comunicado.",
            "sent_count": 0,
            "failed_count": 0,
            "pending_remaining": 0,
        }

    mailer = sender_fn or (
        lambda to_e, to_n, subj, html_b: send_resend_only_email(to_e, to_n, subj, html_b)
    )

    sent_count = 0
    failed_count = 0
    now_iso = timezone.now().isoformat()

    try:
        for idx, target in enumerate(targets):
            target_name = (target.get_full_name() or "").strip() or target.first_name or target.username
            html_body = build_campaign_email_html(
                recipient_name=target_name,
                subject=state["subject"],
                body_text=state["body_text"],
                cta_label=state["cta_label"],
                cta_url=state["cta_url"],
            )
            if mailer(target.email, target_name, state["subject"], html_body):
                sent_count += 1
                sent_for_cid[str(target.id)] = now_iso
                sent_today += 1
            else:
                failed_count += 1

            if sleep_seconds > 0 and idx < (len(targets) - 1):
                time.sleep(sleep_seconds)
    finally:
        state["daily_counts"][today_key] = sent_today
        state["last_run_at"] = timezone.localtime().strftime("%Y-%m-%d %H:%M")
        pending_remaining = max(0, len(pending) - sent_count)
        state["last_run_summary"] = (
            f"Enviados: {sent_count} | Fallidos: {failed_count} | Pendientes: {pending_remaining}"
        )
        save_campaign_state(state)

    return {
        "ok": sent_count > 0 or failed_count == 0,
        "error": "" if sent_count > 0 else "send_failed",
        "message": (
            f"Tanda completada vía Resend: {sent_count} entregados, {failed_count} fallidos. "
            f"Pendientes en cola: {max(0, len(pending) - sent_count)} (llevas {sent_today}/{DAILY_SAFE_CAP} hoy)."
        ),
        "sent_count": sent_count,
        "failed_count": failed_count,
        "pending_remaining": max(0, len(pending) - sent_count),
        "sent_today": sent_today,
    }


def reset_campaign_cycle(custom_slug: Optional[str] = None) -> str:
    """Inicia un nuevo ciclo de campaña cambiando campaign_id y limpiando tested_fingerprint."""
    state = get_campaign_state()
    if custom_slug and custom_slug.strip():
        slug = re.sub(r"[^a-z0-9-]+", "-", custom_slug.strip().lower()).strip("-")
    else:
        slug = f"comunicado-{timezone.localtime().strftime('%Y%m%d-%H%M%S')}"
    state["campaign_id"] = slug or f"comunicado-{timezone.localtime().strftime('%Y%m%d-%H%M%S')}"
    state["tested_fingerprint"] = ""
    state["tested_at"] = ""
    save_campaign_state(state)
    return state["campaign_id"]


def get_tenant_banner() -> Dict[str, Any]:
    """Obtiene la configuración actual del banner informativo del panel del usuario."""
    raw = get_platform_setting(BANNER_SETTING_KEY, "")
    banner = dict(DEFAULT_BANNER_CONFIG)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                banner.update(parsed)
        except json.JSONDecodeError:
            pass
    banner["enabled"] = bool(banner.get("enabled", False))
    return banner


def save_tenant_banner(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Guarda y sanea la configuración del banner informativo dentro del dashboard."""
    current = get_tenant_banner()
    raw_enabled = payload.get("enabled")
    enabled = (
        raw_enabled
        if isinstance(raw_enabled, bool)
        else str(raw_enabled or "").strip().lower() in ("1", "true", "on", "yes")
    )
    campaign_id = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "-",
        str(payload.get("campaign_id") or current["campaign_id"]).strip(),
    ).strip("-") or DEFAULT_BANNER_CONFIG["campaign_id"]

    updated = {
        "enabled": enabled,
        "campaign_id": campaign_id,
        "badge": str(payload.get("badge") or current["badge"]).strip()[:60] or DEFAULT_BANNER_CONFIG["badge"],
        "title": str(payload.get("title") or current["title"]).strip()[:140] or DEFAULT_BANNER_CONFIG["title"],
        "message": str(payload.get("message") or current["message"]).strip()[:500] or DEFAULT_BANNER_CONFIG["message"],
        "cta_label": str(payload.get("cta_label") or current["cta_label"]).strip()[:60] or DEFAULT_BANNER_CONFIG["cta_label"],
        "cta_url": _normalize_cta_url(
            str(payload.get("cta_url") or ""),
            allow_relative=True,
            default_url=DEFAULT_BANNER_CONFIG["cta_url"],
        ),
        "secondary_label": str(payload.get("secondary_label") or "").strip()[:60],
        "secondary_url": _normalize_cta_url(
            str(payload.get("secondary_url") or ""),
            allow_relative=True,
            default_url="",
        )
        if str(payload.get("secondary_label") or "").strip()
        else "",
    }
    set_platform_setting(
        BANNER_SETTING_KEY,
        json.dumps(updated, ensure_ascii=False),
        is_secret=False,
    )
    return updated


get_student_banner = get_tenant_banner
save_student_banner = save_tenant_banner

import base64
import html as _html
import re
from email.utils import formatdate, make_msgid
import httpx
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

from apps.core.config import mail_settings
from apps.core.mail import (
    _html_to_plain_text,
    _sanitize_header,
    get_last_mail_error,
    is_dummy_or_invalid_email,
    send_password_reset_email,
    send_system_email,
)
from apps.core.platform_settings import decrypt_secret, get_platform_setting, mask_secret


def get_mail_runtime_config(empresa=None):
    """
    Resuelve la configuración de correo:
    1. Si la empresa tiene su propia Resend API Key o su propio servidor SMTP completo, usa el de la empresa.
    2. Si no, usa el correo oficial de la plataforma ServiGest (Resend .env/Backoffice o SMTP).
    """
    if empresa:
        emp_resend = decrypt_secret(getattr(empresa, "resend_api_key", "") or "").strip()
        emp_pwd = decrypt_secret(empresa.smtp_password).strip()
        has_own_smtp = bool(empresa.smtp_host and empresa.smtp_user and emp_pwd)
        if emp_resend or has_own_smtp:
            from_addr = _sanitize_header(
                empresa.smtp_from_address or empresa.email or mail_settings.MAIL_FROM_ADDRESS
            )
            from_name = _sanitize_header(empresa.smtp_from_name or empresa.display_name)
            return {
                "source": "empresa",
                "host": (empresa.smtp_host or "").strip(),
                "port": int(empresa.smtp_port or 587),
                "username": (empresa.smtp_user or "").strip(),
                "password": emp_pwd,
                "use_tls": empresa.smtp_encryption == "tls",
                "use_ssl": empresa.smtp_encryption == "ssl",
                "from_address": from_addr,
                "from_name": from_name,
                "resend_api_key": emp_resend,
                "masked_password": mask_secret(emp_pwd),
                "masked_resend_key": mask_secret(emp_resend),
            }

    host = mail_settings.MAIL_HOST
    port = mail_settings.MAIL_PORT
    username = mail_settings.MAIL_USERNAME
    password = mail_settings.MAIL_PASSWORD
    enc = mail_settings.MAIL_ENCRYPTION
    from_addr = _sanitize_header(mail_settings.MAIL_FROM_ADDRESS)
    from_name = _sanitize_header(mail_settings.MAIL_FROM_NAME)
    resend_key = mail_settings.RESEND_API_KEY

    return {
        "source": "plataforma",
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_tls": enc in ("tls", "true", "1"),
        "use_ssl": enc == "ssl",
        "from_address": from_addr or "notificaciones@servigest.online",
        "from_name": from_name or "FacCol — ServiGest",
        "resend_api_key": resend_key,
        "masked_password": mask_secret(password),
        "masked_resend_key": mask_secret(resend_key),
    }


_last_service_error = ""


def _send_via_resend(cfg, to_email, subject, html_body, text_body, reply_to=None, attachments=None):
    global _last_service_error
    api_key = cfg.get("resend_api_key")
    if not api_key:
        return False

    from_addr = cfg["from_address"]
    from_header = f"{cfg['from_name']} <{from_addr}>" if cfg["from_name"] else from_addr
    payload = {
        "from": from_header,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    if reply_to:
        payload["reply_to"] = reply_to[0] if isinstance(reply_to, list) else reply_to
    if attachments:
        payload["attachments"] = [
            {
                "filename": fname,
                "content": base64.b64encode(fbytes).decode("ascii"),
            }
            for fname, fbytes, _mime in attachments
        ]

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=12.0,
        )
        if resp.status_code in (200, 201, 202):
            _last_service_error = ""
            return True
        _last_service_error = f"Resend HTTP {resp.status_code}: {resp.text[:240]}"
        return False
    except Exception as exc:
        _last_service_error = f"Excepción Resend: {exc}"
        return False


def enviar_correo_sistema(
    to_email: str,
    subject: str,
    html_body: str,
    *,
    text_body: str = None,
    reply_to=None,
    attachments=None,
    empresa=None,
    fail_silently: bool = False,
):
    """
    Envía correo usando la configuración propia de la empresa (Resend o SMTP propio si tiene)
    o el correo oficial unificado de ServiGest en caso contrario.
    """
    clean_to = _sanitize_header(to_email).lower()
    if is_dummy_or_invalid_email(clean_to):
        if fail_silently:
            return False
        raise ValueError(f"Dirección de correo destino inválida: {to_email}")

    clean_subject = _sanitize_header(subject)
    plain_text = text_body or _html_to_plain_text(html_body)
    cfg = get_mail_runtime_config(empresa=empresa)
    backend = getattr(settings, "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
    is_locmem = "locmem" in backend.lower()

    if cfg["source"] == "plataforma" and not attachments and not reply_to:
        ok = send_system_email(clean_to, "", clean_subject, html_body)
        if not ok and not fail_silently:
            detalle = get_last_mail_error() or "Verifica RESEND_API_KEY o respaldo SMTP en .env o Backoffice."
            raise RuntimeError(f"No se pudo enviar el correo de plataforma ({detalle}).")
        return ok

    # Canal Resend (propio de empresa o de plataforma ServiGest) cuando no estamos en locmem de tests
    if not is_locmem and cfg.get("resend_api_key"):
        if _send_via_resend(cfg, clean_to, clean_subject, html_body, plain_text, reply_to=reply_to, attachments=attachments):
            return True
        # Si es empresa/plataforma con Resend pero sin SMTP propio completo, reporta error real
        if not (cfg["host"] and cfg["username"] and cfg["password"]):
            if fail_silently:
                return False
            raise RuntimeError(_last_service_error or "Error enviando por Resend API.")

    # SMTP (Empresa propia o respaldo de plataforma o locmem en pruebas)
    if "smtp" in backend.lower() and (not cfg["host"] or not cfg["username"] or not cfg["password"]):
        msg = "El servidor de correo aún no tiene RESEND_API_KEY ni credenciales SMTP configuradas."
        if fail_silently:
            return False
        raise RuntimeError(msg)

    conn = get_connection(
        backend=backend,
        host=cfg["host"],
        port=cfg["port"],
        username=cfg["username"],
        password=cfg["password"],
        use_tls=cfg["use_tls"],
        use_ssl=cfg["use_ssl"],
        fail_silently=fail_silently,
        timeout=15,
    )
    from_header = f"{cfg['from_name']} <{cfg['from_address']}>" if cfg["from_name"] else cfg["from_address"]
    reply_list = [reply_to] if isinstance(reply_to, str) else (reply_to or [cfg["from_address"]])
    domain_msg = cfg["from_address"].split("@", 1)[1] if "@" in (cfg.get("from_address") or "") else "servigest.online"
    mime_headers = {
        "Date": formatdate(localtime=True),
        "Message-ID": make_msgid(domain=domain_msg),
        "X-Mailer": "FacCol Mailer v2.0",
        "Auto-Submitted": "auto-generated",
    }

    email = EmailMultiAlternatives(
        subject=clean_subject,
        body=plain_text,
        from_email=from_header,
        to=[clean_to],
        reply_to=reply_list,
        headers=mime_headers,
        connection=conn,
    )
    email.attach_alternative(html_body, "text/html")
    for fname, fbytes, mime in (attachments or []):
        email.attach(fname, fbytes, mime)

    email.send(fail_silently=fail_silently)
    return True


def enviar_recuperacion_contrasena(user, reset_url: str) -> bool:
    return send_password_reset_email(user, reset_url)


def enviar_notificacion_bienvenida_empresa(empresa, admin_email: str, admin_nombre: str, temp_password: str, login_url: str) -> bool:
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;background:#0f172a;color:#e2e8f0;border-radius:16px;">
      <h2 style="color:#f59e0b;margin-top:0;">¡Bienvenido a FacCol!</h2>
      <p>Hola <b>{_html.escape(admin_nombre)}</b>,</p>
      <p>Tu empresa <b>{_html.escape(empresa.razon_social)}</b> (NIT {_html.escape(empresa.nit_completo)}) ya está activa en la plataforma.</p>
      <div style="background:#1e293b;padding:14px;border-radius:10px;margin:16px 0;font-size:13px;">
        <div><b>Usuario:</b> {_html.escape(admin_email)}</div>
        <div><b>Clave temporal:</b> <code>{_html.escape(temp_password)}</code></div>
      </div>
      <p><a href="{_html.escape(login_url, quote=True)}" style="background:#10b981;color:#000;padding:10px 18px;border-radius:10px;text-decoration:none;font-weight:bold;">Ingresar a FacCol</a></p>
    </div>
    """
    return enviar_correo_sistema(
        admin_email,
        f"Activación de cuenta FacCol — {empresa.display_name}",
        html_body,
        fail_silently=True,
    )

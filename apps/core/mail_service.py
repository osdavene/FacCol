import base64
import html as _html
import re
import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils.html import strip_tags

from apps.core.platform_settings import decrypt_secret, get_platform_setting, mask_secret

DUMMY_RECIPIENTS = {
    "test@test.com",
    "test@example.com",
    "usuario@test.com",
    "admin@dian.gov.co",
}
DUMMY_DOMAIN_SUFFIXES = ("@example.com", "@test.com", "@localhost", ".local", "@fakemail.com")


def _sanitize_header(val):
    return re.sub(r"[\r\n]+", " ", str(val or "")).strip()


def is_dummy_or_invalid_email(email: str) -> bool:
    clean = (email or "").strip().lower()
    if not clean or "@" not in clean or "\r" in clean or "\n" in clean:
        return True
    user, _, domain = clean.partition("@")
    if not user or not domain or "." not in domain:
        return True
    if clean in DUMMY_RECIPIENTS or any(clean.endswith(s) for s in DUMMY_DOMAIN_SUFFIXES):
        return True
    return False


def get_mail_runtime_config(empresa=None):
    """
    Resuelve la configuración de correo:
    1. Si la empresa tiene su propio servidor SMTP completo, usa el de la empresa.
    2. Si no, usa el correo cifrado de la plataforma en ConfiguracionPlataforma.
    """
    if empresa and empresa.smtp_host and empresa.smtp_user and empresa.smtp_password:
        pwd = decrypt_secret(empresa.smtp_password)
        if pwd:
            from_addr = _sanitize_header(
                empresa.smtp_from_address or empresa.email or get_platform_setting("mail_from_address", "notificaciones@servigest.online")
            )
            from_name = _sanitize_header(empresa.smtp_from_name or empresa.display_name)
            return {
                "source": "empresa",
                "host": empresa.smtp_host.strip(),
                "port": int(empresa.smtp_port or 587),
                "username": empresa.smtp_user.strip(),
                "password": pwd,
                "use_tls": empresa.smtp_encryption == "tls",
                "use_ssl": empresa.smtp_encryption == "ssl",
                "from_address": from_addr,
                "from_name": from_name,
                "resend_api_key": "",
            }

    host = get_platform_setting("mail_host", getattr(settings, "EMAIL_HOST", "email-smtp.us-east-1.amazonaws.com")).strip()
    port_raw = get_platform_setting("mail_port", str(getattr(settings, "EMAIL_PORT", 587))).strip()
    port = int(port_raw) if port_raw.isdigit() else 587
    username = get_platform_setting("mail_username", getattr(settings, "EMAIL_HOST_USER", "")).strip()
    password = get_platform_setting("mail_password", getattr(settings, "EMAIL_HOST_PASSWORD", "")).strip()
    enc = get_platform_setting("mail_encryption", "tls").strip().lower()
    from_addr = _sanitize_header(
        get_platform_setting("mail_from_address", getattr(settings, "DEFAULT_FROM_EMAIL", "notificaciones@servigest.online"))
    )
    from_name = _sanitize_header(get_platform_setting("mail_from_name", "FacCol — ServiGest"))
    resend_key = get_platform_setting("resend_api_key", getattr(settings, "RESEND_API_KEY", "")).strip()

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


def _send_via_resend(cfg, to_email, subject, html_body, text_body, reply_to=None, attachments=None):
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
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=12,
        )
        return resp.status_code in (200, 201, 202)
    except Exception:
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
    Envía correo usando el SMTP de la empresa (si tiene configurado) o el correo
    unificado de la plataforma (Resend / Amazon SES SMTP) en caso contrario.
    """
    clean_to = _sanitize_header(to_email).lower()
    if is_dummy_or_invalid_email(clean_to):
        if fail_silently:
            return False
        raise ValueError(f"Dirección de correo destino inválida: {to_email}")

    clean_subject = _sanitize_header(subject)
    plain_text = text_body or strip_tags(html_body)
    cfg = get_mail_runtime_config(empresa=empresa)

    # Prioridad Resend cuando se usa correo de plataforma y existe RESEND_API_KEY
    if cfg["source"] == "plataforma" and cfg.get("resend_api_key"):
        if _send_via_resend(cfg, clean_to, clean_subject, html_body, plain_text, reply_to=reply_to, attachments=attachments):
            return True

    # SMTP (Empresa propia o Amazon SES de plataforma)
    backend = getattr(settings, "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
    if "smtp" in backend.lower() and (not cfg["host"] or not cfg["username"] or not cfg["password"]):
        msg = "El servidor de correo de la plataforma aún no tiene credenciales SMTP/Resend configuradas en Backoffice."
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

    email = EmailMultiAlternatives(
        subject=clean_subject,
        body=plain_text,
        from_email=from_header,
        to=[clean_to],
        reply_to=reply_list,
        connection=conn,
    )
    email.attach_alternative(html_body, "text/html")
    for fname, fbytes, mime in (attachments or []):
        email.attach(fname, fbytes, mime)

    email.send(fail_silently=fail_silently)
    return True


def enviar_recuperacion_contrasena(user, reset_url: str) -> bool:
    nombre = _html.escape(user.get_full_name() or user.username)
    url_safe = _html.escape(reset_url, quote=True)
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;background:#0f172a;color:#e2e8f0;border-radius:16px;">
      <h2 style="color:#f59e0b;margin-top:0;">Recuperación de Contraseña — FacCol</h2>
      <p>Hola <b>{nombre}</b>,</p>
      <p>Recibimos una solicitud para restablecer la contraseña de tu cuenta.</p>
      <p style="margin:24px 0;">
        <a href="{url_safe}" style="background:#f59e0b;color:#000;padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:bold;">
          Restablecer mi Contraseña
        </a>
      </p>
      <p style="font-size:12px;color:#94a3b8;">Si no solicitaste este cambio, puedes ignorar este mensaje con tranquilidad. El enlace expirará pronto por seguridad.</p>
    </div>
    """
    return enviar_correo_sistema(
        user.email,
        "Recuperación de contraseña — FacCol",
        html_body,
        fail_silently=False,
    )


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

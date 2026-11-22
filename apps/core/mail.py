"""Motor de correo electrónico unificado FacCol (doble canal Resend + SMTP con protección anti-spam)."""

import html as _html
import logging
import os
import re
import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional

import httpx

from apps.core.config import mail_settings

logger = logging.getLogger("faccol.mail")
_last_mail_error = ""


def get_last_mail_error() -> str:
    return _last_mail_error

DUMMY_RECIPIENTS = {
    "test@test.com",
    "test@example.com",
    "usuario@test.com",
    "admin@dian.gov.co",
}
DUMMY_DOMAIN_SUFFIXES = ("@example.com", "@test.com", "@localhost", ".local", "@fakemail.com")


def _sanitize_header(val: Optional[str]) -> str:
    """Elimina saltos CR/LF para bloquear inyección de cabeceras MIME."""
    return re.sub(r"[\r\n]+", " ", str(val or "")).strip()


def _safe_http_url(url: Optional[str]) -> str:
    """Acepta únicamente URLs http:// o https:// y escapa comillas; devuelve '#' en otro caso."""
    clean = str(url or "").strip()
    if clean.lower().startswith(("http://", "https://")):
        return _html.escape(clean, quote=True)
    return "#"


def _html_to_plain_text(html_body: str) -> str:
    """Convierte HTML a texto plano limpio conservando texto y URL de enlaces."""
    text = re.sub(r"<\s*br\s*/?>", "\n", str(html_body or ""), flags=re.IGNORECASE)
    text = re.sub(r"</\s*p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</\s*h[1-6]\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</\s*div\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r'<\s*a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)<\s*/a\s*>',
        lambda m: f"{re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', m.group(2))).strip()} ({m.group(1)})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def is_dummy_or_invalid_email(email: Optional[str]) -> bool:
    """Valida sintaxis mínima y filtra direcciones ficticias o de prueba."""
    clean = str(email or "").strip().lower()
    if not clean or "@" not in clean or "\r" in clean or "\n" in clean:
        return True
    user, _, domain = clean.partition("@")
    if not user or not domain or "." not in domain:
        return True
    if clean in DUMMY_RECIPIENTS:
        return True
    if any(clean.endswith(suffix) for suffix in DUMMY_DOMAIN_SUFFIXES):
        return True
    return False


def _authenticated_from_address() -> str:
    from_email = _sanitize_header(mail_settings.MAIL_FROM_ADDRESS or "notificaciones@servigest.online").lower()
    if not from_email.endswith("@servigest.online"):
        return "notificaciones@servigest.online"
    return from_email


def _send_resend_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_content: str,
    *,
    extra_headers: Optional[dict] = None,
) -> bool:
    """Envía correo mediante la API oficial de Resend con dominio autenticado @servigest.online."""
    api_key = (mail_settings.RESEND_API_KEY or "").strip()
    if not api_key:
        return False

    from_name = _sanitize_header(mail_settings.MAIL_FROM_NAME or "FacCol — ServiGest")
    from_email = _authenticated_from_address()
    reply_to_addr = _sanitize_header(mail_settings.MAIL_ADMIN_RECIPIENT or from_email)
    plain_text = _html_to_plain_text(html_content)

    payload = {
        "from": f"{from_name} <{from_email}>",
        "to": [_sanitize_header(to_email)],
        "reply_to": reply_to_addr,
        "subject": _sanitize_header(subject),
        "html": html_content,
        "text": plain_text,
    }
    if extra_headers:
        payload["headers"] = {
            _sanitize_header(k): _sanitize_header(v) for k, v in extra_headers.items()
        }

    global _last_mail_error
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=12.0,
        )
        if resp.status_code in (200, 201, 202):
            _last_mail_error = ""
            return True
        _last_mail_error = f"Resend HTTP {resp.status_code}: {resp.text[:240]}"
        logger.warning("Error Resend API (%s): %s", resp.status_code, resp.text)
        return False
    except Exception as exc:
        _last_mail_error = f"Excepción Resend: {exc}"
        logger.warning("Excepción enviando vía Resend API a %s: %s", to_email, exc)
        return False


def _dispatch_to_django_locmem_if_testing(
    from_addr: str,
    from_name: str,
    clean_to: str,
    reply_to_addr: str,
    clean_subject: str,
    plain_text: str,
    html_body: str,
    mime_headers: dict,
) -> Optional[bool]:
    """Permite capturar correos en django.core.mail.outbox cuando Django TestCase activa locmem."""
    if os.getenv("DJANGO_TEST_ALLOW_RAW_SMTP") == "1":
        return None
    try:
        from django.conf import settings as dj_settings
        from django.core.mail import EmailMultiAlternatives

        backend = getattr(dj_settings, "EMAIL_BACKEND", "")
        if "locmem" not in backend.lower():
            return None

        email = EmailMultiAlternatives(
            subject=clean_subject,
            body=plain_text,
            from_email=formataddr((from_name, from_addr)),
            to=[clean_to],
            reply_to=[reply_to_addr],
            headers=mime_headers,
        )
        email.attach_alternative(html_body, "text/html")
        email.send(fail_silently=False)
        return True
    except Exception:
        return None


def _send_smtp_message(to_address: str, subject: str, html_body: str) -> bool:
    """Envía un correo mediante SMTP estándar con cabeceras RFC completas y parte texto/HTML."""
    host = mail_settings.MAIL_HOST
    port = mail_settings.MAIL_PORT
    user = mail_settings.MAIL_USERNAME
    pwd = mail_settings.MAIL_PASSWORD
    enc = mail_settings.MAIL_ENCRYPTION
    from_addr = _sanitize_header(mail_settings.MAIL_FROM_ADDRESS or "notificaciones@servigest.online")
    from_name = _sanitize_header(mail_settings.MAIL_FROM_NAME or "FacCol — ServiGest")
    reply_to_addr = _sanitize_header(mail_settings.MAIL_ADMIN_RECIPIENT or from_addr)
    clean_to = _sanitize_header(to_address)
    clean_subject = _sanitize_header(subject)

    domain_msg = from_addr.split("@", 1)[1] if "@" in from_addr else "servigest.online"
    msg_id = make_msgid(domain=domain_msg)
    date_hdr = formatdate(localtime=True)
    plain_text = _html_to_plain_text(html_body)
    mime_headers = {
        "Date": date_hdr,
        "Message-ID": msg_id,
        "X-Mailer": "FacCol Mailer v2.0",
        "Auto-Submitted": "auto-generated",
    }

    captured = _dispatch_to_django_locmem_if_testing(
        from_addr, from_name, clean_to, reply_to_addr, clean_subject, plain_text, html_body, mime_headers
    )
    if captured is not None:
        return captured

    global _last_mail_error
    if not host or not user or not pwd:
        _last_mail_error = "Falta RESEND_API_KEY o credenciales SMTP completas."
        logger.warning("Credenciales SMTP no configuradas en .env ni Backoffice.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(clean_subject, "utf-8")
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = clean_to
    msg["Reply-To"] = reply_to_addr
    msg["Date"] = date_hdr
    msg["Message-ID"] = msg_id
    msg["X-Mailer"] = "FacCol Mailer v2.0"
    msg["Auto-Submitted"] = "auto-generated"

    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if enc == "ssl":
            with smtplib.SMTP_SSL(host, port, timeout=12, context=ssl.create_default_context()) as s:
                s.login(user, pwd)
                s.sendmail(from_addr, [clean_to], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=12) as s:
                if enc in ("tls", "true", "1"):
                    s.starttls(context=ssl.create_default_context())
                s.login(user, pwd)
                s.sendmail(from_addr, [clean_to], msg.as_string())
        _last_mail_error = ""
        return True
    except smtplib.SMTPResponseException as exc:
        _last_mail_error = f"SMTP ({exc.smtp_code}): {exc.smtp_error}"
        logger.warning("Respuesta SMTP al enviar a %s: (%s) %s", clean_to, exc.smtp_code, exc.smtp_error)
        return False
    except Exception as exc:
        _last_mail_error = f"Error SMTP: {exc}"
        logger.warning("Error SMTP enviando a %s: %s", clean_to, exc)
        return False


def send_system_email(to_email: str, to_name: str, subject: str, html_content: str) -> bool:
    """
    Canal transaccional: omite direcciones dummy retornando True; prioriza Resend y conmuta a SMTP si falla.
    """
    clean_to = _sanitize_header(to_email).lower()
    clean_name = _sanitize_header(to_name)
    clean_subject = _sanitize_header(subject)
    if is_dummy_or_invalid_email(clean_to):
        logger.info("Omitiendo envío transaccional a correo dummy/inválido: %s", to_email)
        return True

    if mail_settings.RESEND_API_KEY:
        sent = _send_resend_email(clean_to, clean_name, clean_subject, html_content)
        if not sent:
            logger.info("Fallo envío vía Resend a %s; activando respaldo SMTP...", clean_to)
            sent = _send_smtp_message(clean_to, clean_subject, html_content)
        return sent

    return _send_smtp_message(clean_to, clean_subject, html_content)


def send_resend_only_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_content: str,
    *,
    unsubscribe_mailto: Optional[str] = None,
) -> bool:
    """
    Canal de campañas masivas por tandas: requiere RESEND_API_KEY y nunca conmuta a SMTP personal.
    """
    clean_to = _sanitize_header(to_email).lower()
    clean_name = _sanitize_header(to_name)
    clean_subject = _sanitize_header(subject)
    if is_dummy_or_invalid_email(clean_to):
        return False

    if not mail_settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY no configurada; abortando envío de comunicado sin usar SMTP.")
        return False

    unsub_addr = _sanitize_header(
        unsubscribe_mailto or mail_settings.MAIL_ADMIN_RECIPIENT or mail_settings.MAIL_FROM_ADDRESS or "notificaciones@servigest.online"
    )
    return _send_resend_email(
        clean_to,
        clean_name,
        clean_subject,
        html_content,
        extra_headers={"List-Unsubscribe": f"<mailto:{unsub_addr}?subject=BAJA>"},
    )


def _email_header_html() -> str:
    return """
    <div style="text-align: center; margin-bottom: 24px;">
        <div style="display: inline-block; background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color: #ffffff; font-weight: 900; padding: 12px 26px; border-radius: 12px; font-size: 17px; letter-spacing: 0.5px; border: 1px solid #334155;">
            FACCOL <span style="color: #f59e0b; font-weight: 800; font-size: 13px; margin-left: 6px;">| SERVIGEST</span>
        </div>
    </div>
    """


def _email_footer_html() -> str:
    app_url = _safe_http_url(mail_settings.APP_URL or "https://faccol.servigest.online")
    return f"""
    <div style="text-align: center; margin-top: 24px; font-size: 12px; color: #94a3b8; line-height: 1.6;">
        &copy; 2026 ServiGest Soluciones Tecnológicas &bull; FacCol — Facturación Electrónica y ERP Colombia<br>
        <a href="{app_url}" style="color: #2563eb; text-decoration: none; font-weight: 600;">faccol.servigest.online</a>
        <div style="margin-top: 10px; font-size: 10px; color: #64748b; max-width: 500px; margin-left: auto; margin-right: auto; line-height: 1.4; border-top: 1px solid #e2e8f0; padding-top: 10px;">
            Plataforma en la nube para Facturación Electrónica DIAN, Caja Express POS, Inventario y Nómina Electrónica en Colombia.
        </div>
    </div>
    """


def _user_display_name(user) -> str:
    full = ""
    if hasattr(user, "get_full_name"):
        full = (user.get_full_name() or "").strip()
    return full or getattr(user, "first_name", "") or getattr(user, "username", "") or "Usuario"


def send_verification_email(user, verify_url: str) -> bool:
    """Envía correo de verificación de cuenta con botón CTA y enlace plano de respaldo."""
    name = _html.escape(_user_display_name(user), quote=True)
    safe_url = _safe_http_url(verify_url)
    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; padding: 28px 14px;">
        <div style="max-width: 580px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 18px; padding: 28px;">
            {_email_header_html()}
            <h2 style="color: #0f172a; margin: 0 0 12px 0; font-size: 20px;">Activa tu cuenta en FacCol</h2>
            <p style="color: #334155; font-size: 15px; line-height: 1.6;">Hola, <strong>{name}</strong>:</p>
            <p style="color: #334155; font-size: 14px; line-height: 1.6;">
                Gracias por registrar tu empresa en <strong>FacCol — ServiGest</strong>. Para activar tu acceso y verificar tu correo electrónico, haz clic en el botón:
            </p>
            <div style="text-align: center; margin: 26px 0;">
                <a href="{safe_url}" style="display: inline-block; background-color: #2563eb; color: #ffffff; text-decoration: none; font-weight: 800; font-size: 14px; padding: 14px 28px; border-radius: 12px;">
                    Verificar y Activar Cuenta
                </a>
            </div>
            <p style="color: #64748b; font-size: 12px; line-height: 1.5; word-break: break-all;">
                Si el botón no funciona, copia y pega este enlace en tu navegador:<br>
                <a href="{safe_url}" style="color: #2563eb;">{safe_url}</a>
            </p>
            {_email_footer_html()}
        </div>
    </div>
    """
    return send_system_email(
        getattr(user, "email", ""),
        _user_display_name(user),
        "Activa tu cuenta en FacCol — Verificación de correo",
        html_body,
    )


def send_password_reset_email(user, reset_url: str) -> bool:
    """Envía correo de restablecimiento de contraseña con token de un solo uso."""
    name = _html.escape(_user_display_name(user), quote=True)
    safe_url = _safe_http_url(reset_url)
    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; padding: 28px 14px;">
        <div style="max-width: 580px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 18px; padding: 28px;">
            {_email_header_html()}
            <h2 style="color: #0f172a; margin: 0 0 12px 0; font-size: 20px;">Restablecimiento de Contraseña</h2>
            <p style="color: #334155; font-size: 15px; line-height: 1.6;">Hola, <strong>{name}</strong>:</p>
            <p style="color: #334155; font-size: 14px; line-height: 1.6;">
                Recibimos una solicitud para cambiar la contraseña de tu cuenta en FacCol. Este enlace es de un solo uso y caduca automáticamente por seguridad.
            </p>
            <div style="text-align: center; margin: 26px 0;">
                <a href="{safe_url}" style="display: inline-block; background-color: #f59e0b; color: #0f172a; text-decoration: none; font-weight: 800; font-size: 14px; padding: 14px 28px; border-radius: 12px;">
                    Restablecer mi Contraseña
                </a>
            </div>
            <p style="color: #64748b; font-size: 12px; line-height: 1.5; word-break: break-all;">
                Enlace directo de respaldo:<br>
                <a href="{safe_url}" style="color: #2563eb;">{safe_url}</a>
            </p>
            <p style="color: #64748b; font-size: 12px;">Si no solicitaste este cambio, puedes ignorar este mensaje.</p>
            {_email_footer_html()}
        </div>
    </div>
    """
    return send_system_email(
        getattr(user, "email", ""),
        _user_display_name(user),
        "Recuperación de contraseña — FacCol",
        html_body,
    )


def send_notification_email(
    user,
    subject: str,
    headline: str,
    message: str,
    action_url: Optional[str] = None,
    action_text: Optional[str] = None,
) -> bool:
    """Envía notificación transaccional general manteniendo diseño y sanitización segura."""
    name = _html.escape(_user_display_name(user), quote=True)
    safe_headline = _html.escape(_sanitize_header(headline), quote=True)
    safe_msg = "<br>".join(_html.escape(ln, quote=True) for ln in str(message or "").splitlines())
    cta_html = ""
    if action_url and action_text:
        safe_url = _safe_http_url(action_url)
        safe_txt = _html.escape(action_text, quote=True)
        if safe_url != "#":
            cta_html = f"""
            <div style="text-align: center; margin: 24px 0;">
                <a href="{safe_url}" style="display: inline-block; background-color: #2563eb; color: #ffffff; text-decoration: none; font-weight: 800; font-size: 14px; padding: 12px 24px; border-radius: 10px;">
                    {safe_txt}
                </a>
            </div>
            """

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; padding: 28px 14px;">
        <div style="max-width: 580px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 18px; padding: 28px;">
            {_email_header_html()}
            <h2 style="color: #0f172a; margin: 0 0 12px 0; font-size: 20px;">{safe_headline}</h2>
            <p style="color: #334155; font-size: 15px; line-height: 1.6;">Hola, <strong>{name}</strong>:</p>
            <p style="color: #334155; font-size: 14px; line-height: 1.6;">{safe_msg}</p>
            {cta_html}
            {_email_footer_html()}
        </div>
    </div>
    """
    return send_system_email(getattr(user, "email", ""), _user_display_name(user), subject, html_body)


def _format_campaign_line_html(line_text: str) -> str:
    if ":" in line_text:
        head, tail = line_text.split(":", 1)
        if 2 <= len(head.strip()) <= 65 and tail.strip():
            return (
                f"<strong style='color: #0f172a;'>{_html.escape(head.strip(), quote=True)}:</strong> "
                f"{_html.escape(tail.strip(), quote=True)}"
            )
    return _html.escape(line_text, quote=True)


def build_campaign_email_html(
    recipient_name: str,
    subject: str,
    body_text: str,
    cta_label: str = "Ingresar a FacCol",
    cta_url: str = "https://faccol.servigest.online/auth/login/",
) -> str:
    """Construye la plantilla HTML institucional anti-spam para comunicados por tandas."""
    safe_name = _html.escape((recipient_name or "Empresario").strip() or "Empresario", quote=True)
    safe_subject = _html.escape(_sanitize_header(subject), quote=True)
    safe_cta_label = _html.escape((cta_label or "Ingresar a FacCol").strip(), quote=True)
    safe_cta_url = _safe_http_url(cta_url)

    blocks = [
        b.strip() for b in re.split(r"\r?\n\s*\r?\n", (body_text or "").strip()) if b.strip()
    ]
    if not blocks:
        blocks = ["Descubre las novedades de FacCol para potenciar tu facturación electrónica y punto de venta."]

    sections_html: list[str] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        bullet_lines = [
            re.sub(r"^[-•✓✔*]+\s*", "", ln).strip()
            for ln in lines
            if re.match(r"^[-•✓✔*]+\s+", ln)
        ]
        if bullet_lines:
            non_bullets = [ln for ln in lines if not re.match(r"^[-•✓✔*]+\s+", ln)]
            if non_bullets:
                sections_html.append(
                    "<p style='color: #0f172a; font-size: 15px; font-weight: 800; line-height: 1.5; margin: 18px 0 10px 0;'>"
                    + "<br>".join(_html.escape(ln, quote=True) for ln in non_bullets)
                    + "</p>"
                )
            items_html = "".join(
                "<tr>"
                "<td style='width: 22px; vertical-align: top; color: #059669; font-weight: 900; font-size: 15px; line-height: 1.45; padding-bottom: 10px;'>&#10003;</td>"
                f"<td style='color: #334155; font-size: 14px; line-height: 1.55; padding-bottom: 10px;'>{_format_campaign_line_html(bl)}</td>"
                "</tr>"
                for bl in bullet_lines
            )
            sections_html.append(
                "<div style='background-color: #f8fafc; border: 1px solid #e2e8f0; border-left: 4px solid #10b981; border-radius: 12px; padding: 16px 18px 6px 18px; margin: 8px 0 18px 0;'>"
                "<table role='presentation' style='width: 100%; border-collapse: collapse;'>"
                + items_html
                + "</table>"
                + "</div>"
            )
        else:
            sections_html.append(
                "<p style='color: #334155; font-size: 15px; line-height: 1.65; margin: 0 0 14px 0;'>"
                + "<br>".join(_html.escape(ln, quote=True) for ln in lines)
                + "</p>"
            )

    body_html = "\n".join(sections_html)

    cta_block = ""
    if safe_cta_url != "#":
        plans_url = _safe_http_url(f"{mail_settings.APP_URL}/#planes")
        cta_block = f"""
        <div style="text-align: center; margin: 26px 0 18px 0;">
            <a href="{safe_cta_url}"
               style="display: inline-block; background: linear-gradient(135deg, #059669 0%, #2563eb 100%); color: #ffffff; text-decoration: none; font-weight: 800; font-size: 15px; padding: 15px 32px; border-radius: 12px;">
                {safe_cta_label} &rarr;
            </a>
            <div style="margin-top: 10px; font-size: 12px; color: #64748b;">
                Consulta también nuestros <a href="{plans_url}" style="color: #2563eb; text-decoration: none; font-weight: 600;">Planes SaaS DIAN y POS</a>
            </div>
        </div>
        """

    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; padding: 28px 14px;">
        <div style="max-width: 620px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 18px; overflow: hidden; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);">
            <div style="background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #065f46 100%); padding: 28px 24px 24px 24px; text-align: center;">
                <div style="display: inline-block; background-color: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); color: #ffffff; font-weight: 900; padding: 7px 18px; border-radius: 999px; font-size: 12px; letter-spacing: 0.8px; margin-bottom: 14px;">
                    FACCOL <span style="color: #fbbf24; margin-left: 4px;">| SERVIGEST</span>
                </div>
                <h1 style="color: #ffffff; font-size: 22px; font-weight: 900; margin: 0 0 8px 0; line-height: 1.3;">
                    {safe_subject}
                </h1>
                <p style="color: #cbd5e1; font-size: 13px; margin: 0; font-weight: 600;">
                    Facturación Electrónica DIAN &bull; Caja Express POS &bull; Inventario &amp; Nómina
                </p>
            </div>
            <div style="padding: 28px 26px 24px 26px;">
                <p style="color: #0f172a; font-size: 16px; font-weight: 700; margin: 0 0 14px 0;">
                    Hola, {safe_name}:
                </p>
                {body_html}
                {cta_block}
                <p style="color: #64748b; font-size: 12px; line-height: 1.5; margin: 22px 0 0 0; border-top: 1px solid #f1f5f9; padding-top: 14px;">
                    Recibes este aviso informativo porque tienes una cuenta registrada en FacCol (ServiGest). Si prefieres no recibir más novedades por correo, simplemente responde este mensaje con la palabra <strong>BAJA</strong>.
                </p>
                {_email_footer_html()}
            </div>
        </div>
    </div>
    """

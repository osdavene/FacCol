"""Configuración centralizada de correo FacCol con prioridad estricta en .env y respaldo en Backoffice."""

import os
from pathlib import Path

from dotenv import load_dotenv

from apps.core.platform_settings import get_platform_setting

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_first(*names: str) -> str:
    for name in names:
        val = (os.getenv(name) or "").strip()
        if val:
            return val
    return ""


def _resolve_setting(env_names: tuple[str, ...], db_key: str, default: str = "") -> str:
    """Prioridad estricta en .env; si falta o está vacía, usa ConfiguracionPlataforma como respaldo."""
    env_val = _env_first(*env_names)
    if env_val:
        return env_val
    db_val = (get_platform_setting(db_key, "") or "").strip()
    if db_val:
        return db_val
    return default


class MailSettings:
    @property
    def APP_URL(self) -> str:
        return (_env_first("APP_URL") or "https://faccol.servigest.online").rstrip("/")

    @property
    def RESEND_API_KEY(self) -> str:
        return _resolve_setting(("RESEND_API_KEY",), "resend_api_key", "")

    @property
    def MAIL_MAILER(self) -> str:
        return _env_first("MAIL_MAILER") or "smtp"

    @property
    def MAIL_HOST(self) -> str:
        return _resolve_setting(("MAIL_HOST", "EMAIL_HOST"), "mail_host", "email-smtp.us-east-1.amazonaws.com")

    @property
    def MAIL_PORT(self) -> int:
        raw = _resolve_setting(("MAIL_PORT", "EMAIL_PORT"), "mail_port", "587")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 587

    @property
    def MAIL_USERNAME(self) -> str:
        return _resolve_setting(("MAIL_USERNAME", "EMAIL_HOST_USER"), "mail_username", "")

    @property
    def MAIL_PASSWORD(self) -> str:
        return _resolve_setting(("MAIL_PASSWORD", "EMAIL_HOST_PASSWORD"), "mail_password", "")

    @property
    def MAIL_ENCRYPTION(self) -> str:
        env_enc = _env_first("MAIL_ENCRYPTION")
        if env_enc:
            return env_enc.lower()
        env_tls = _env_first("EMAIL_USE_TLS")
        if env_tls:
            return "tls" if env_tls.lower() in ("true", "1", "t", "yes") else ""
        return (_resolve_setting((), "mail_encryption", "tls") or "tls").lower()

    @property
    def MAIL_FROM_ADDRESS(self) -> str:
        return _resolve_setting(
            ("MAIL_FROM_ADDRESS", "DEFAULT_FROM_EMAIL"),
            "mail_from_address",
            "notificaciones@servigest.online",
        )

    @property
    def MAIL_FROM_NAME(self) -> str:
        return _resolve_setting(("MAIL_FROM_NAME",), "mail_from_name", "FacCol — ServiGest")

    @property
    def MAIL_ADMIN_RECIPIENT(self) -> str:
        return _resolve_setting(
            ("MAIL_ADMIN_RECIPIENT",),
            "mail_admin_recipient",
            "servigestonline@gmail.com",
        )


mail_settings = MailSettings()

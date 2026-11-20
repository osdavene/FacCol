import base64
import hashlib
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings


def _fernet_from_secret(raw_secret: str) -> Fernet:
    digest = hashlib.sha256(raw_secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def _get_fernet() -> MultiFernet:
    keys = []
    enc_key = getattr(settings, "SETTINGS_ENCRYPTION_KEY", "")
    if enc_key:
        keys.append(_fernet_from_secret(enc_key))
    secret_key = getattr(settings, "SECRET_KEY", "") or "faccol-fallback-key"
    if not keys or secret_key != enc_key:
        keys.append(_fernet_from_secret(secret_key))
    return MultiFernet(keys)


def encrypt_secret(plain_text: str) -> str:
    if not plain_text:
        return ""
    if plain_text.startswith("enc:"):
        return plain_text
    token = _get_fernet().encrypt(plain_text.encode("utf-8")).decode("utf-8")
    return f"enc:{token}"


def decrypt_secret(stored_value: Optional[str]) -> str:
    if not stored_value:
        return ""
    if not stored_value.startswith("enc:"):
        return stored_value
    raw_token = stored_value[4:]
    try:
        return _get_fernet().decrypt(raw_token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return f"{value[:4]}••••••••{value[-4:]}"


def get_platform_setting(key: str, default: str = "") -> str:
    from apps.core.models import ConfiguracionPlataforma
    try:
        row = ConfiguracionPlataforma.objects.filter(key=key).first()
    except Exception:
        return default
    if not row or row.value is None:
        return default
    if row.value == "":
        return ""
    if row.is_secret:
        decrypted = decrypt_secret(row.value)
        return decrypted if decrypted != "" else default
    return str(row.value)


def set_platform_setting(key: str, value: str, is_secret: bool = False) -> None:
    from apps.core.models import ConfiguracionPlataforma
    stored = encrypt_secret(value) if (is_secret and value) else value
    ConfiguracionPlataforma.objects.update_or_create(
        key=key,
        defaults={
            "value": stored,
            "is_secret": is_secret,
        },
    )

"""Autenticación de dos factores TOTP (RFC 6238) en Python puro."""

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

import qrcode


def generar_secreto_totp() -> str:
    """Genera un secreto Base32 de 160 bits sin padding compatible con Google Authenticator / Authy."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _decode_b32(secret_b32: str) -> bytes:
    clean = (secret_b32 or "").strip().replace(" ", "").upper()
    pad = (-len(clean)) % 8
    return base64.b32decode(clean + ("=" * pad), casefold=True)


def generar_codigo_totp(secret_b32: str, for_time: int | None = None, step: int = 30, digits: int = 6) -> str:
    key = _decode_b32(secret_b32)
    counter = int((for_time if for_time is not None else time.time()) // step)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10**digits)).zfill(digits)


def verificar_codigo_totp(secret_b32: str, code: str, window: int = 1, for_time: int | None = None) -> bool:
    clean_code = "".join(ch for ch in (code or "").strip() if ch.isdigit())
    if len(clean_code) != 6 or not secret_b32:
        return False
    now = int(for_time if for_time is not None else time.time())
    try:
        for offset in range(-window, window + 1):
            candidate = generar_codigo_totp(secret_b32, for_time=now + (offset * 30))
            if hmac.compare_digest(candidate, clean_code):
                return True
    except Exception:
        return False
    return False


def generar_uri_otpauth(secret_b32: str, account_email: str, issuer: str = "FacCol ERP") -> str:
    lbl = quote(f"{issuer}:{account_email}")
    return f"otpauth://totp/{lbl}?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def generar_qr_data_uri(uri: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=5, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"

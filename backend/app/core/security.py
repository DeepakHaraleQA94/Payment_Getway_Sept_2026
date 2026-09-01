"""Password hashing (bcrypt) and JWT token helpers."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, email: str, tenant_id: str | None,
                        sid: str | None = None, tv: int = 0) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "type": "access",
        "sid": sid,
        "tv": tv,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, sid: str | None = None, tv: int = 0) -> str:
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "sid": sid,
        "tv": tv,
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_mfa_token(user_id: str) -> str:
    """Short-lived token issued after password step, exchanged at MFA verify. Not an access token."""
    payload = {
        "sub": str(user_id),
        "type": "mfa",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


import hashlib
import secrets as _secrets

import pyotp


def generate_reset_token() -> tuple[str, str]:
    """Return (plaintext, sha256_hash) for a single-use security token."""
    raw = _secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_reset_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_mfa_secret() -> str:
    return pyotp.random_base32()


def mfa_provisioning_uri(secret: str, email: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name="CloudPay")


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)


def generate_api_key(prefix: str = "sk_test") -> tuple[str, str, str]:
    """Return (plaintext, sha256_hash, last4). Plaintext is shown to the user once."""
    raw = _secrets.token_urlsafe(24)
    plaintext = f"{prefix}_{raw}"
    key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, key_hash, plaintext[-4:]


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_token(length: int = 24) -> str:
    return _secrets.token_urlsafe(length)

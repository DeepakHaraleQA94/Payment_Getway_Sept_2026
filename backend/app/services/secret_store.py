"""Secure secret store for provider credentials.

Provider account rows persist only an opaque `credential_ref`; the raw secret is encrypted
at rest here (Fernet/AES) and is NEVER returned by any API or written to logs/audit. The
`SecretStore` interface is pluggable — a real KMS/Vault backend can replace the default
encrypted-DB implementation without changing callers.
"""
import json
import logging
import uuid
from abc import ABC, abstractmethod

from cryptography.fernet import Fernet
from dotenv import set_key
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ROOT_DIR, settings
from app.models.payment import ProviderSecret

logger = logging.getLogger("cloudpay.secret_store")


def _load_fernet() -> Fernet:
    """Return the Fernet cipher, generating + persisting a master key if none is configured."""
    key = settings.secret_store_key
    if not key:
        key = Fernet.generate_key().decode()
        try:
            set_key(str(ROOT_DIR / ".env"), "SECRET_STORE_KEY", key)
        except Exception:  # pragma: no cover - best effort persistence
            logger.warning("Could not persist SECRET_STORE_KEY to .env; using in-memory key")
        settings.secret_store_key = key
    return Fernet(key.encode() if isinstance(key, str) else key)


class SecretStore(ABC):
    @abstractmethod
    async def put(self, db: AsyncSession, *, tenant_id, secret: dict, ref: str | None = None) -> str:
        ...

    @abstractmethod
    async def get(self, db: AsyncSession, ref: str) -> dict | None:
        ...

    @abstractmethod
    async def delete(self, db: AsyncSession, ref: str) -> None:
        ...


class EncryptedDbSecretStore(SecretStore):
    """Default store: Fernet-encrypted JSON blobs in the `provider_secrets` table."""

    def __init__(self) -> None:
        self._fernet = _load_fernet()

    async def put(self, db: AsyncSession, *, tenant_id, secret: dict, ref: str | None = None) -> str:
        ref = ref or f"sec_{uuid.uuid4().hex}"
        ciphertext = self._fernet.encrypt(json.dumps(secret).encode()).decode()
        res = await db.execute(select(ProviderSecret).where(ProviderSecret.ref == ref))
        row = res.scalar_one_or_none()
        if row:
            row.ciphertext = ciphertext
        else:
            db.add(ProviderSecret(ref=ref, tenant_id=tenant_id, ciphertext=ciphertext))
        await db.flush()
        return ref

    async def get(self, db: AsyncSession, ref: str) -> dict | None:
        res = await db.execute(select(ProviderSecret).where(ProviderSecret.ref == ref))
        row = res.scalar_one_or_none()
        if not row:
            return None
        return json.loads(self._fernet.decrypt(row.ciphertext.encode()).decode())

    async def delete(self, db: AsyncSession, ref: str) -> None:
        res = await db.execute(select(ProviderSecret).where(ProviderSecret.ref == ref))
        row = res.scalar_one_or_none()
        if row:
            await db.delete(row)
            await db.flush()


# Default singleton store. Selected by config so an external KMS/Vault backend can be added
# later WITHOUT changing callers or the payment/provider architecture.
_store: SecretStore | None = None


def get_secret_store() -> SecretStore:
    global _store
    if _store is None:
        backend = (settings.secret_store_backend or "encrypted_db").lower()
        if backend == "encrypted_db":
            _store = EncryptedDbSecretStore()
        else:
            # Extension point: 'aws_kms', 'vault', 'gcp_kms' etc. plug in here in a future phase.
            raise ValueError(f"Unsupported SECRET_STORE_BACKEND '{backend}'")
    return _store

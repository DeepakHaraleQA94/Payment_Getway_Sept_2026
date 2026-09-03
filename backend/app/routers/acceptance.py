"""Payment Acceptance Accounts API (NEW, additive).

Tenant-owned UPI/payment RECEIVING destinations. Separate from the external provider/PSP account
system. Full CRUD + enable/disable/priority with strict tenant isolation, granular permissions,
server-side VPA validation, audit logging (VPA masked, no secrets), and rate limiting. Does NOT
process transactions; a future authorized provider plugin may reference an eligible account.
"""
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_permission, resolve_tenant_id
from app.core.ratelimit import rate_limit
from app.models.acceptance import PaymentAcceptanceAccount
from app.schemas import (
    AcceptanceAccountCreate,
    AcceptanceAccountOut,
    AcceptanceAccountUpdate,
    AcceptancePriorityUpdate,
)

router = APIRouter(prefix="/api/payment-acceptance", tags=["payment-acceptance"])

# Basic, safe UPI VPA format: local@handle (no fake bank verification implied).
_VPA_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.\-_]{1,255}@[a-zA-Z][a-zA-Z0-9.\-_]{1,63}$")


def _normalize_vpa(vpa: str) -> str:
    return vpa.strip().lower()


def _validate_upi(account_type: str, upi_vpa: str | None) -> str | None:
    if account_type != "upi":
        raise HTTPException(status_code=400, detail="Only 'upi' acceptance accounts are supported")
    if not upi_vpa or not upi_vpa.strip():
        raise HTTPException(status_code=400, detail="upi_vpa is required for a UPI account")
    normalized = _normalize_vpa(upi_vpa)
    if not _VPA_RE.match(normalized):
        raise HTTPException(status_code=400, detail="Invalid UPI VPA format (expected name@handle)")
    return normalized


def _mask_vpa(vpa: str | None) -> str | None:
    """Mask the local part for audit logs (never store the full VPA in the audit trail)."""
    if not vpa or "@" not in vpa:
        return None
    local, handle = vpa.split("@", 1)
    shown = local[:2] if len(local) > 2 else local[:1]
    return f"{shown}{'*' * 4}@{handle}"


async def _load(db, account_id, user) -> PaymentAcceptanceAccount:
    acct = await db.get(PaymentAcceptanceAccount, account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Acceptance account not found")
    if not user.is_superadmin and acct.tenant_id != user.tenant_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")
    return acct


@router.get("/accounts", response_model=list[AcceptanceAccountOut])
async def list_accounts(tenant_id: str | None = None, country: str | None = None,
                        currency: str | None = None, environment: str | None = None,
                        enabled: bool | None = None, db: AsyncSession = Depends(get_db),
                        user=Depends(require_permission("payment_acceptance_account.view"))):
    tid = resolve_tenant_id(user, tenant_id)
    stmt = select(PaymentAcceptanceAccount).where(PaymentAcceptanceAccount.tenant_id == tid)
    if country:
        stmt = stmt.where(PaymentAcceptanceAccount.country == country.upper())
    if currency:
        stmt = stmt.where(PaymentAcceptanceAccount.currency == currency.upper())
    if environment:
        stmt = stmt.where(PaymentAcceptanceAccount.environment == environment)
    if enabled is not None:
        stmt = stmt.where(PaymentAcceptanceAccount.enabled.is_(enabled))
    stmt = stmt.order_by(PaymentAcceptanceAccount.priority, PaymentAcceptanceAccount.created_at)
    res = await db.execute(stmt)
    return res.scalars().all()


@router.get("/accounts/eligible", response_model=list[AcceptanceAccountOut])
async def list_eligible_accounts(country: str, currency: str, environment: str = "sandbox",
                                 tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                                 user=Depends(require_permission("payment_acceptance_account.view"))):
    """Read-only eligibility view (additive) a future UPI plugin can use to pick a destination:
    enabled + matching tenant/country/currency/environment, ordered by priority. Does NOT process."""
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(
        select(PaymentAcceptanceAccount).where(
            PaymentAcceptanceAccount.tenant_id == tid,
            PaymentAcceptanceAccount.enabled.is_(True),
            PaymentAcceptanceAccount.country == country.upper(),
            PaymentAcceptanceAccount.currency == currency.upper(),
            PaymentAcceptanceAccount.environment == environment,
        ).order_by(PaymentAcceptanceAccount.priority, PaymentAcceptanceAccount.created_at))
    return res.scalars().all()


@router.get("/accounts/{account_id}", response_model=AcceptanceAccountOut)
async def get_account(account_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                      user=Depends(require_permission("payment_acceptance_account.view"))):
    return await _load(db, account_id, user)


@router.post("/accounts", response_model=AcceptanceAccountOut)
async def create_account(body: AcceptanceAccountCreate, tenant_id: str | None = None,
                         db: AsyncSession = Depends(get_db),
                         user=Depends(require_permission("payment_acceptance_account.manage")),
                         _rl: None = Depends(rate_limit("acceptance_write", 30, 60))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    data = body.model_dump()
    data["upi_vpa"] = _validate_upi(data.get("account_type"), data.get("upi_vpa"))
    data["currency"] = data["currency"].upper()
    data["country"] = data["country"].upper()
    if data["environment"] not in ("sandbox", "live"):
        raise HTTPException(status_code=400, detail="environment must be 'sandbox' or 'live'")
    # Exact-duplicate guard (tenant + VPA + environment).
    dup = await db.execute(select(PaymentAcceptanceAccount).where(
        PaymentAcceptanceAccount.tenant_id == tid,
        PaymentAcceptanceAccount.upi_vpa == data["upi_vpa"],
        PaymentAcceptanceAccount.environment == data["environment"]))
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=400,
                            detail="An acceptance account with this VPA already exists for this environment")
    acct = PaymentAcceptanceAccount(tenant_id=tid, created_by=str(user.id), **data)  # verification_status defaults
    db.add(acct)
    await db.flush()
    await record_audit(db, action="payment_acceptance_account.create", resource_type="payment_acceptance_account",
                       resource_id=acct.id, tenant_id=tid, actor_id=str(user.id), actor_email=user.email,
                       changes={"account_type": acct.account_type, "display_name": acct.display_name,
                                "vpa": _mask_vpa(acct.upi_vpa), "currency": acct.currency,
                                "country": acct.country, "environment": acct.environment,
                                "enabled": acct.enabled, "priority": acct.priority})
    await db.commit()
    await db.refresh(acct)
    return acct


@router.patch("/accounts/{account_id}", response_model=AcceptanceAccountOut)
async def update_account(account_id: uuid.UUID, body: AcceptanceAccountUpdate,
                         db: AsyncSession = Depends(get_db),
                         user=Depends(require_permission("payment_acceptance_account.manage")),
                         _rl: None = Depends(rate_limit("acceptance_write", 30, 60))):
    acct = await _load(db, account_id, user)
    changes = body.model_dump(exclude_none=True)
    if "upi_vpa" in changes:
        changes["upi_vpa"] = _validate_upi(acct.account_type, changes["upi_vpa"])
    if "currency" in changes:
        changes["currency"] = changes["currency"].upper()
    if "country" in changes:
        changes["country"] = changes["country"].upper()
    if changes.get("environment") and changes["environment"] not in ("sandbox", "live"):
        raise HTTPException(status_code=400, detail="environment must be 'sandbox' or 'live'")
    for k, v in changes.items():
        setattr(acct, k, v)
    acct.updated_by = str(user.id)
    audit_changes = {k: (v if k != "upi_vpa" else _mask_vpa(v)) for k, v in changes.items()}
    await record_audit(db, action="payment_acceptance_account.update", resource_type="payment_acceptance_account",
                       resource_id=acct.id, tenant_id=acct.tenant_id, actor_id=str(user.id),
                       actor_email=user.email, changes=audit_changes)
    await db.commit()
    await db.refresh(acct)
    return acct


async def _set_enabled(db, account_id, user, enabled: bool) -> PaymentAcceptanceAccount:
    acct = await _load(db, account_id, user)
    acct.enabled = enabled
    acct.updated_by = str(user.id)
    await record_audit(db, action=f"payment_acceptance_account.{'enable' if enabled else 'disable'}",
                       resource_type="payment_acceptance_account", resource_id=acct.id,
                       tenant_id=acct.tenant_id, actor_id=str(user.id), actor_email=user.email,
                       changes={"enabled": enabled})
    await db.commit()
    await db.refresh(acct)
    return acct


@router.post("/accounts/{account_id}/enable", response_model=AcceptanceAccountOut)
async def enable_account(account_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user=Depends(require_permission("payment_acceptance_account.manage"))):
    return await _set_enabled(db, account_id, user, True)


@router.post("/accounts/{account_id}/disable", response_model=AcceptanceAccountOut)
async def disable_account(account_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                          user=Depends(require_permission("payment_acceptance_account.manage"))):
    return await _set_enabled(db, account_id, user, False)


@router.post("/accounts/{account_id}/priority", response_model=AcceptanceAccountOut)
async def set_priority(account_id: uuid.UUID, body: AcceptancePriorityUpdate,
                       db: AsyncSession = Depends(get_db),
                       user=Depends(require_permission("payment_acceptance_account.manage"))):
    acct = await _load(db, account_id, user)
    acct.priority = body.priority
    acct.updated_by = str(user.id)
    await record_audit(db, action="payment_acceptance_account.update", resource_type="payment_acceptance_account",
                       resource_id=acct.id, tenant_id=acct.tenant_id, actor_id=str(user.id),
                       actor_email=user.email, changes={"priority": body.priority})
    await db.commit()
    await db.refresh(acct)
    return acct


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user=Depends(require_permission("payment_acceptance_account.manage"))):
    acct = await _load(db, account_id, user)
    await record_audit(db, action="payment_acceptance_account.archive",
                       resource_type="payment_acceptance_account", resource_id=acct.id,
                       tenant_id=acct.tenant_id, actor_id=str(user.id), actor_email=user.email,
                       changes={"display_name": acct.display_name, "vpa": _mask_vpa(acct.upi_vpa),
                                "environment": acct.environment})
    await db.delete(acct)
    await db.commit()
    return {"deleted": True}

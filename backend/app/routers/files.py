"""Tenant branding (logo upload + accent color) and file serving."""
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.core.storage import APP_NAME, MIME_TYPES, get_object, put_object
from app.models.commerce import StoredFile
from app.models.tenant import Tenant
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["branding"])

ALLOWED_IMAGE = {"png", "jpg", "jpeg", "webp", "gif", "svg"}


class BrandingUpdate(BaseModel):
    brand_accent: str | None = None


def _check_tenant_access(user, tenant: Tenant):
    if not user.is_superadmin and user.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")


@router.patch("/tenants/{tenant_id}/branding")
async def update_branding(tenant_id: uuid.UUID, body: BrandingUpdate, db: AsyncSession = Depends(get_db),
                          user=Depends(require_permission("checkout.manage"))):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    _check_tenant_access(user, tenant)
    if body.brand_accent:
        tenant.brand_accent = body.brand_accent[:9]
    await record_audit(db, action="branding.update", resource_type="tenant", resource_id=tenant.id,
                       tenant_id=tenant.id, actor_id=str(user.id), actor_email=user.email,
                       changes={"brand_accent": tenant.brand_accent})
    await db.commit()
    return {"brand_accent": tenant.brand_accent,
            "brand_logo_file_id": str(tenant.brand_logo_file_id) if tenant.brand_logo_file_id else None}


@router.post("/tenants/{tenant_id}/logo")
async def upload_logo(tenant_id: uuid.UUID, file: UploadFile = File(...), db: AsyncSession = Depends(get_db),
                      user=Depends(require_permission("checkout.manage"))):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    _check_tenant_access(user, tenant)
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if ext not in ALLOWED_IMAGE:
        raise HTTPException(status_code=400, detail="Unsupported image type")
    data = await file.read()
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo must be under 2MB")
    path = f"{APP_NAME}/logos/{tenant_id}/{uuid.uuid4()}.{ext}"
    content_type = MIME_TYPES.get(ext, file.content_type or "application/octet-stream")
    try:
        result = put_object(path, data, content_type)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Storage upload failed: {exc}")
    sf = StoredFile(tenant_id=tenant_id, storage_path=result["path"], original_filename=file.filename or "logo",
                    content_type=content_type, size=result.get("size", len(data)), kind="logo")
    db.add(sf)
    await db.flush()
    tenant.brand_logo_file_id = sf.id
    await record_audit(db, action="branding.logo_upload", resource_type="tenant", resource_id=tenant.id,
                       tenant_id=tenant.id, actor_id=str(user.id), actor_email=user.email)
    await db.commit()
    return {"file_id": str(sf.id), "logo_url": f"/api/public/files/{sf.id}"}


@router.get("/public/files/{file_id}")
async def public_file(file_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Serve public logo files (used by the unauthenticated checkout page)."""
    sf = await db.get(StoredFile, file_id)
    if not sf or sf.is_deleted or sf.kind != "logo":
        raise HTTPException(status_code=404, detail="File not found")
    data, ct = get_object(sf.storage_path)
    return Response(content=data, media_type=sf.content_type or ct,
                    headers={"Cache-Control": "public, max-age=300"})


@router.get("/files/{file_id}/download")
async def download_file(file_id: uuid.UUID, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    sf = await db.get(StoredFile, file_id)
    if not sf or sf.is_deleted:
        raise HTTPException(status_code=404, detail="File not found")
    if not user.is_superadmin and sf.tenant_id != user.tenant_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")
    data, ct = get_object(sf.storage_path)
    return Response(content=data, media_type=sf.content_type or ct,
                    headers={"Content-Disposition": f'attachment; filename="{sf.original_filename}"'})

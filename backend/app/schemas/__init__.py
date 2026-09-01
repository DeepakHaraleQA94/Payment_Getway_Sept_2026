"""Pydantic request/response schemas."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- Auth ----
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORMBase):
    id: uuid.UUID
    email: str
    name: str
    tenant_id: uuid.UUID | None
    status: str
    auth_provider: str
    is_superadmin: bool
    role_id: uuid.UUID | None
    picture: str | None = None
    created_at: datetime


# ---- Tenant ----
class TenantCreate(BaseModel):
    name: str = Field(max_length=200)
    slug: str = Field(max_length=80)
    country: str | None = None
    default_currency: str = "USD"
    contact_email: EmailStr | None = None


class TenantUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    country: str | None = None
    default_currency: str | None = None
    contact_email: EmailStr | None = None


class TenantOut(ORMBase):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    country: str | None
    default_currency: str
    contact_email: str | None
    is_platform: bool
    brand_accent: str = "#3B82F6"
    brand_logo_file_id: uuid.UUID | None = None
    created_at: datetime


# ---- Roles / permissions ----
class PermissionOut(ORMBase):
    id: uuid.UUID
    code: str
    description: str | None
    module: str


class RoleCreate(BaseModel):
    name: str
    description: str | None = None
    permission_codes: list[str] = []


class RoleOut(ORMBase):
    id: uuid.UUID
    name: str
    description: str | None
    is_system: bool
    tenant_id: uuid.UUID | None
    permissions: list[PermissionOut] = []


# ---- Users management ----
class UserCreate(BaseModel):
    email: EmailStr
    name: str = ""
    password: str = Field(min_length=8, max_length=128)
    role_id: uuid.UUID | None = None


# ---- Feature flags ----
class FeatureFlagCreate(BaseModel):
    key: str
    name: str
    description: str | None = None
    enabled: bool = False
    config: dict = {}


class FeatureFlagUpdate(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    description: str | None = None
    config: dict | None = None


class FeatureFlagOut(ORMBase):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    key: str
    name: str
    description: str | None
    enabled: bool
    config: dict


# ---- Providers ----
class ProviderCreate(BaseModel):
    provider_key: str
    display_name: str
    mode: str = "sandbox"
    enabled: bool = True
    priority: int = 100
    supported_currencies: list[str] = []
    config: dict = {}


class ProviderOut(ORMBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    provider_key: str
    display_name: str
    mode: str
    enabled: bool
    priority: int
    supported_currencies: list
    created_at: datetime


# ---- Payments / refunds ----
class PaymentCreate(BaseModel):
    reference: str = Field(max_length=64)
    amount_minor: int = Field(gt=0)
    currency: str = "USD"
    provider_key: str = "mock"
    description: str | None = None
    customer_email: EmailStr | None = None
    idempotency_key: str | None = None
    metadata: dict = {}


class PaymentOut(ORMBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    reference: str
    provider_key: str
    provider_txn_id: str | None
    amount_minor: int
    currency: str
    fee_minor: int
    net_minor: int
    status: str
    description: str | None
    customer_email: str | None
    risk_score: int
    created_at: datetime


class RefundCreate(BaseModel):
    amount_minor: int = Field(gt=0)
    reason: str | None = None
    idempotency_key: str | None = None


class RefundOut(ORMBase):
    id: uuid.UUID
    payment_id: uuid.UUID
    amount_minor: int
    currency: str
    status: str
    reason: str | None
    created_at: datetime


# ---- Fee rules ----
class FeeRuleCreate(BaseModel):
    name: str
    provider_key: str | None = None
    currency: str | None = None
    percent_bps: int = 0
    fixed_minor: int = 0
    min_fee_minor: int = 0
    priority: int = 100


class FeeRuleOut(ORMBase):
    id: uuid.UUID
    name: str
    provider_key: str | None
    currency: str | None
    percent_bps: int
    fixed_minor: int
    min_fee_minor: int
    active: bool
    priority: int


# ---- API keys ----
class ApiKeyCreate(BaseModel):
    label: str = "Default"


class ApiKeyOut(ORMBase):
    id: uuid.UUID
    label: str
    key_prefix: str
    last4: str
    active: bool
    last_used_at: datetime | None
    created_at: datetime


# ---- Webhooks ----
class WebhookCreate(BaseModel):
    url: str = Field(max_length=500)
    description: str | None = None
    events: list[str] = []


class WebhookOut(ORMBase):
    id: uuid.UUID
    url: str
    description: str | None
    events: list
    enabled: bool
    created_at: datetime


# ---- Checkout ----
class CheckoutCreate(BaseModel):
    reference: str = Field(default="", max_length=64)
    amount_minor: int = Field(gt=0)
    currency: str = "USD"
    description: str | None = None
    customer_email: EmailStr | None = None
    success_url: str | None = None


class CheckoutOut(ORMBase):
    id: uuid.UUID
    token: str
    reference: str
    amount_minor: int
    currency: str
    description: str | None
    customer_email: str | None
    status: str
    created_at: datetime


class CheckoutPay(BaseModel):
    customer_email: EmailStr | None = None
    card_number: str | None = None  # sandbox only; never stored

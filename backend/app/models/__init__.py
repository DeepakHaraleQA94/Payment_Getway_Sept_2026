"""Import all models so Alembic autogenerate & metadata see them."""
from app.core.database import Base  # noqa: F401
from app.models.tenant import Tenant  # noqa: F401
from app.models.iam import (  # noqa: F401
    AuthSession,
    LoginAttempt,
    Permission,
    Role,
    User,
    role_permissions,
)
from app.models.feature import FeatureFlag  # noqa: F401
from app.models.payment import Payment, PaymentProvider, Refund  # noqa: F401
from app.models.finance import (  # noqa: F401
    FeeRule,
    LedgerAccount,
    LedgerEntry,
    Settlement,
    TurnoverSnapshot,
)
from app.models.platform import AuditLog, FxRate, KycRecord, SystemConfig  # noqa: F401

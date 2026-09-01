"""Central configuration with environment separation (development/qa/staging/production).

All secrets are read from environment variables only. Nothing is hard-coded.
"""
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

VALID_ENVS = {"development", "qa", "staging", "production"}


class Settings:
    def __init__(self) -> None:
        self.app_env: str = os.environ.get("APP_ENV", "development").lower()
        if self.app_env not in VALID_ENVS:
            self.app_env = "development"

        # Database
        self.database_url: str = os.environ["DATABASE_URL"]
        self.database_url_sync: str = os.environ["DATABASE_URL_SYNC"]

        # Auth
        self.jwt_secret: str = os.environ["JWT_SECRET"]
        self.jwt_algorithm: str = "HS256"
        self.access_token_minutes: int = int(os.environ.get("ACCESS_TOKEN_MINUTES", "30"))
        self.refresh_token_days: int = int(os.environ.get("REFRESH_TOKEN_DAYS", "7"))

        # Admin seeding (no hard-coded credentials; sourced from the environment only)
        self.admin_email: str = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io").lower()
        self.admin_password: str = os.environ.get("ADMIN_PASSWORD", "")

        # CORS / frontend
        self.frontend_url: str = os.environ.get("FRONTEND_URL", "http://localhost:3000")
        self.cors_origins: str = os.environ.get("CORS_ORIGINS", "*")

        # Emergent Google auth
        self.emergent_auth_session_url: str = os.environ.get(
            "EMERGENT_AUTH_SESSION_URL",
            "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
        )

        # Object storage + integrations
        self.emergent_llm_key: str = os.environ.get("EMERGENT_LLM_KEY", "")
        self.integration_proxy_url: str = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() \
            or "https://integrations.emergentagent.com"

        # Email adapter (provider-agnostic; noop until a provider is configured)
        self.email_provider: str = os.environ.get("EMAIL_PROVIDER", "noop").lower()

        # Webhook retry policy (configurable)
        self.webhook_max_attempts: int = int(os.environ.get("WEBHOOK_MAX_ATTEMPTS", "8"))
        self.webhook_base_delay_sec: int = int(os.environ.get("WEBHOOK_BASE_DELAY_SEC", "30"))
        self.webhook_max_backoff_sec: int = int(os.environ.get("WEBHOOK_MAX_BACKOFF_SEC", "3600"))

        # Secret store master key (Fernet). Encrypts provider credentials at rest.
        # Auto-generated + persisted by the secret store if absent (see services/secret_store.py).
        self.secret_store_key: str = os.environ.get("SECRET_STORE_KEY", "")
        # Secret store backend selector. 'encrypted_db' now; 'aws_kms'/'vault'/etc. pluggable later.
        self.secret_store_backend: str = os.environ.get("SECRET_STORE_BACKEND", "encrypted_db")

        # Provider health alerting thresholds + optional operator email recipient.
        self.alert_success_rate_threshold: float = float(os.environ.get("ALERT_SUCCESS_RATE_THRESHOLD", "0.5"))
        self.alert_min_sample: int = int(os.environ.get("ALERT_MIN_SAMPLE", "5"))
        self.alert_email_to: str = os.environ.get("ALERT_EMAIL_TO", "")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cookie_secure(self) -> bool:
        # Always secure over HTTPS preview/prod; kept True for samesite=none cookies.
        return True

    def validate(self) -> list[str]:
        """Security configuration check. Returns a list of warnings; in production any
        finding is a hard blocker and raises, so an insecure prod deploy fails fast."""
        problems: list[str] = []
        if len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be at least 32 characters")
        if (self.cors_origins or "").strip() == "*":
            problems.append("CORS_ORIGINS is a wildcard '*' (set explicit origins)")
        if not self.admin_password:
            problems.append("ADMIN_PASSWORD is not set")
        if not self.secret_store_key:
            problems.append("SECRET_STORE_KEY is not set (auto-generated in non-production only)")
        if self.is_production and self.frontend_url.startswith("http://"):
            problems.append("FRONTEND_URL must use https in production")
        if self.is_production and problems:
            raise RuntimeError("Insecure production configuration: " + "; ".join(problems))
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

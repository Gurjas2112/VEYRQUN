"""All configuration comes from env vars, validated once at startup (fail fast)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "VEYRQUN"
APP_TITLE = "VEYRQUN | Governance Layer for Financial Agents"

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    veyrqun_env: Literal["dev", "test", "production"] = "dev"
    database_url: str = "postgresql+psycopg://veyrqun_owner:veyrqun_owner_dev@localhost:55432/veyrqun"
    migration_database_url: str | None = None
    redis_url: str = "redis://localhost:56379/0"
    allowed_origins: str = "http://localhost:5173"

    # Comma-separated `kid:base64(32 bytes)`; the first is active. Required outside dev/test.
    veyrqun_master_key: str | None = None

    policy_wasm_path: Path = REPO_ROOT / "policies" / "dist" / "policy.wasm"
    lua_dir: Path = REPO_ROOT / "policies" / "lua"

    veyrqun_simulator: bool = True
    default_workspace_slug: str = "default"
    default_workspace_env: Literal["production", "staging", "sandbox"] = "production"

    jwt_issuer: str = "https://veyrqun.local"
    agent_token_audience: str = "veyrqun-gateway"  # noqa: S105 (an audience, not a secret)
    agent_token_ttl_s: int = Field(900, ge=60, le=3600)
    # Shared secret the gateway presents to fetch policy bundles.
    bundle_service_token: str | None = None

    # --- human identity (OIDC BFF) ---
    oidc_issuer: str = "http://localhost:8180/realms/veyrqun"
    oidc_discovery_url: str | None = None      # internal URL when the IdP is reached via a private hostname
    oidc_public_authorization_endpoint: str | None = None
    oidc_client_id: str = "veyrqun-dashboard"
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str = "http://localhost:8000/auth/callback"
    oidc_scopes: str = "openid email profile"
    oidc_mfa_amr: str = "otp,hwk,mfa,swk"      # amr values that count as multi-factor
    oidc_mfa_acr: str = ""                     # optional acr values that count as multi-factor
    app_base_url: str = "http://localhost:5173"
    session_cookie_secure: bool = True
    step_up_max_age_s: int = 300
    veyrqun_lookup_key: str | None = None      # base64 32 bytes, keyed hash for email lookups
    turnstile_secret: str | None = None
    sandbox_ttl_days: int = 7
    sandbox_quota_agents: int = 5
    sandbox_quota_requests_per_day: int = 2000
    max_body_bytes: int = 1_048_576
    metrics_token: str | None = None

    rate_window_ms: int = 60_000
    counter_ttl_s: int = 2 * 86_400

    @model_validator(mode="after")
    def _production_requirements(self) -> Settings:
        if self.veyrqun_env == "production":
            missing = [n for n in ("veyrqun_master_key", "bundle_service_token", "veyrqun_lookup_key",
                                   "oidc_client_secret", "metrics_token") if not getattr(self, n)]
            if missing:
                raise ValueError(f"missing required settings for production: {missing}")
            if self.allowed_origins.strip() == "*":
                raise ValueError("ALLOWED_ORIGINS must not be '*' in production")
        return self

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

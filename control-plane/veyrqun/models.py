from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

ActionName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_:.\-]{1,63}$")]
DataClass = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,31}$")]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PolicyFields(Strict):
    allowed_actions: list[ActionName] = Field(default_factory=list, max_length=200)
    blocked_actions: list[ActionName] = Field(default_factory=list, max_length=200)
    spend_cap_daily: Money
    max_per_transaction: Money | None = None
    rate_limit_per_minute: int | None = Field(None, ge=1, le=100_000)
    action_limits: dict[ActionName, Money] = Field(default_factory=dict, max_length=200)
    action_daily_caps: dict[ActionName, Money] = Field(default_factory=dict, max_length=200)
    approval_threshold: Money | None = None
    redact_data_classes: list[DataClass] = Field(default_factory=list, max_length=50)
    block_data_classes: list[DataClass] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _business_rules(self) -> PolicyFields:
        check_policy_rules(self.model_dump())
        return self


def check_policy_rules(policy: dict) -> None:
    overlap = set(policy.get("allowed_actions") or []) & set(policy.get("blocked_actions") or [])
    if overlap:
        raise ValueError(f"actions cannot be both allowed and blocked: {sorted(overlap)}")
    cap, per_tx = policy.get("spend_cap_daily"), policy.get("max_per_transaction")
    if cap is not None and per_tx is not None and per_tx > cap:
        raise ValueError("max_per_transaction must be <= spend_cap_daily")


class AgentCreate(PolicyFields):
    agent_id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_\-]{2,63}$")]
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    timezone: str = Field("UTC", max_length=64)
    currency: Currency = "USD"
    mode: Literal["observe", "enforce"] = "enforce"
    accountable_owner: str | None = Field(None, max_length=200)
    business_service: str | None = Field(None, max_length=200)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v


class AgentPolicyUpdate(Strict):
    """Partial update. Explicitly sending null for an optional limit clears it."""

    allowed_actions: list[ActionName] | None = Field(None, max_length=200)
    blocked_actions: list[ActionName] | None = Field(None, max_length=200)
    spend_cap_daily: Money | None = None
    max_per_transaction: Money | None = None
    rate_limit_per_minute: int | None = Field(None, ge=1, le=100_000)
    action_limits: dict[ActionName, Money] | None = None
    action_daily_caps: dict[ActionName, Money] | None = None
    approval_threshold: Money | None = None
    redact_data_classes: list[DataClass] | None = None
    block_data_classes: list[DataClass] | None = None
    note: str | None = Field(None, max_length=280)


class ActionAttempt(Strict):
    agent_id: str = Field(..., max_length=64)
    action_type: ActionName
    amount: Money = Decimal("0")
    data_classes: list[DataClass] = Field(default_factory=list, max_length=20)
    approval_id: str | None = Field(None, max_length=64)


class StatusChange(Strict):
    reason: str | None = Field(None, max_length=280)


class ApprovalDecision(Strict):
    reason: str | None = Field(None, max_length=280)


class TokenRequest(Strict):
    grant_type: Literal["client_credentials"]
    client_id: str = Field(..., max_length=128)
    client_secret: str = Field(..., max_length=256)
    scope: str | None = Field(None, max_length=512)

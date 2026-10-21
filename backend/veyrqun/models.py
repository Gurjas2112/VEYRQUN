from pydantic import BaseModel, Field

ACTION_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"


class AgentCreate(BaseModel):
    agent_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_\-]{2,63}$")
    display_name: str = Field(..., min_length=1, max_length=120)
    allowed_actions: list[str] = Field(default_factory=list)
    blocked_actions: list[str] = Field(default_factory=list)
    spend_cap_daily: float = Field(..., ge=0)
    max_per_transaction: float | None = Field(None, ge=0)
    rate_limit_per_minute: int | None = Field(None, ge=1, le=10_000)
    action_limits: dict[str, float] = Field(default_factory=dict)


class AgentPolicyUpdate(BaseModel):
    """Partial update. Explicitly sending null for an optional limit clears it."""

    allowed_actions: list[str] | None = None
    blocked_actions: list[str] | None = None
    spend_cap_daily: float | None = Field(None, ge=0)
    max_per_transaction: float | None = Field(None, ge=0)
    rate_limit_per_minute: int | None = Field(None, ge=1, le=10_000)
    action_limits: dict[str, float] | None = None
    note: str | None = Field(None, max_length=280)


class ActionAttempt(BaseModel):
    agent_id: str
    action_type: str = Field(..., pattern=ACTION_PATTERN)
    amount: float = Field(0.0, ge=0)


class StatusChange(BaseModel):
    reason: str | None = Field(None, max_length=280)

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

ACTION_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"


class AgentCreate(BaseModel):
    agent_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_\-]{2,63}$")
    display_name: str = Field(..., min_length=1, max_length=120)
    allowed_actions: List[str] = Field(default_factory=list)
    blocked_actions: List[str] = Field(default_factory=list)
    spend_cap_daily: float = Field(..., ge=0)
    max_per_transaction: Optional[float] = Field(None, ge=0)
    rate_limit_per_minute: Optional[int] = Field(None, ge=1, le=10_000)
    action_limits: Dict[str, float] = Field(default_factory=dict)


class AgentPolicyUpdate(BaseModel):
    """Partial update. Explicitly sending null for an optional limit clears it."""

    allowed_actions: Optional[List[str]] = None
    blocked_actions: Optional[List[str]] = None
    spend_cap_daily: Optional[float] = Field(None, ge=0)
    max_per_transaction: Optional[float] = Field(None, ge=0)
    rate_limit_per_minute: Optional[int] = Field(None, ge=1, le=10_000)
    action_limits: Optional[Dict[str, float]] = None
    note: Optional[str] = Field(None, max_length=280)


class ActionAttempt(BaseModel):
    agent_id: str
    action_type: str = Field(..., pattern=ACTION_PATTERN)
    amount: float = Field(0.0, ge=0)


class StatusChange(BaseModel):
    reason: Optional[str] = Field(None, max_length=280)

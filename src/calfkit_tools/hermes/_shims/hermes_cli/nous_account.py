"""Shim for hermes-agent ``hermes_cli.nous_account`` (Nous-portal account info).

Irrelevant to calfkit's runtime; only referenced by the managed-tool-gateway path
inside try/except, so the entitlement helpers are safe no-ops. The account-info
dataclasses ARE preserved verbatim from upstream (pure data, MIT) because vendored
test files import them at module scope -- an absent class is a collection-time
ImportError. Kept byte-for-byte aligned with the upstream definitions.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

NousAccountInfoSource = Literal["jwt", "account_api", "inference_key", "none", "error"]

TOOL_COVERAGE_CATEGORIES = (
    "firecrawl",
    "fal",
    "fal-video",
    "openai-audio",
    "browser-use",
    "modal",
)


@dataclass(frozen=True)
class NousPortalSubscriptionInfo:
    plan: Optional[str] = None
    tier: Optional[int] = None
    monthly_charge: Optional[float] = None
    monthly_credits: Optional[float] = None
    current_period_end: Optional[str] = None
    credits_remaining: Optional[float] = None
    rollover_credits: Optional[float] = None


@dataclass(frozen=True)
class NousPaidServiceAccessInfo:
    allowed: Optional[bool] = None
    paid_access: Optional[bool] = None
    reason: Optional[str] = None
    organisation_id: Optional[str] = None
    effective_at_ms: Optional[int] = None
    has_active_subscription: Optional[bool] = None
    active_subscription_is_paid: Optional[bool] = None
    subscription_tier: Optional[int] = None
    subscription_monthly_charge: Optional[float] = None
    subscription_credits_remaining: Optional[float] = None
    purchased_credits_remaining: Optional[float] = None
    total_usable_credits: Optional[float] = None


@dataclass(frozen=True)
class NousToolAccessInfo:
    enabled: bool = False
    coverage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NousPortalAccountInfo:
    logged_in: bool
    source: NousAccountInfoSource
    fresh: bool
    user_id: Optional[str] = None
    org_id: Optional[str] = None
    client_id: Optional[str] = None
    product_id: Optional[str] = None
    nous_client: Optional[str] = None
    portal_base_url: Optional[str] = None
    inference_base_url: Optional[str] = None
    inference_credential_present: bool = False
    credential_source: Optional[str] = None
    expires_at: Optional[datetime] = None
    email: Optional[str] = None
    privy_did: Optional[str] = None
    subscription: Optional[NousPortalSubscriptionInfo] = None
    paid_service_access: Optional[bool] = None
    paid_service_access_info: Optional[NousPaidServiceAccessInfo] = None


def get_nous_portal_account_info(*args, **kwargs):
    return None


def format_nous_portal_entitlement_message(*args, **kwargs) -> str:
    return ""

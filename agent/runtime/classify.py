"""CLASSIFY: decline class/family + mandate_status inference (F2) + mandate category."""
from __future__ import annotations

import re

# (regex on error_description | event_type, decline_class, decline_family)
KEYWORDS: list[tuple[str, str, str]] = [
    (r"insufficient|balance", "insufficient_funds", "soft"),
    (r"expired", "card_expired", "hard"),
    (r"mandate.*(cancel|revok)|(cancel|revok).*mandate", "mandate_cancelled", "hard"),
    (r"do not hono|do_not_hono|declined by (the )?bank|transaction declined", "do_not_honour", "soft"),
    (r"after all retry|retries exhausted|max(imum)? retries", "retries_exhausted", "soft"),
]

# F2: mandate_status must be inferred from event / subscription status before the shield sees it.
STATUS_TO_MANDATE = {
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "halted": "halted",
    "paused": "paused",
    "pending": "active",      # gateway still auto-retrying => mandate live
    "active": "active",
    "authenticated": "active",
    "charged": "active",
    "resumed": "active",
    "created": "none",
    "expired": "cancelled",
    "completed": "none",
}
EVENT_TO_MANDATE = {
    "subscription.cancelled": "cancelled",
    "subscription.halted": "halted",
    "subscription.paused": "paused",
    "subscription.resumed": "active",
    "mandate.registered": "active",
}


def classify(norm: dict, profile: dict, prior_case: dict | None = None) -> dict:
    text = f"{norm.get('error_description') or ''} {norm.get('error_code') or ''}".lower()
    decline_class, family, hit = "unknown", "unknown", None
    domain = norm.get("domain", "recurring")

    if norm["event_type"].startswith("risk."):
        # Detector-originated: class comes from detectors.json; nothing has failed yet.
        decline_class = norm.get("risk_class") or "unknown"
        family = "pre_failure" if domain == "recurring" else "non_recurring"
        if decline_class in ("insufficient_funds",):  # stale-pending reuses the reactive class
            family = "soft"
        hit = f"detector:{norm['event_type']}"
    elif norm["event_type"] == "subscription.halted":
        decline_class, family, hit = "retries_exhausted", "soft", "event:subscription.halted"
    else:
        for pat, cls, fam in KEYWORDS:
            if re.search(pat, text):
                decline_class, family, hit = cls, fam, pat
                break

    # Tick/recovery/state events carry no decline info: inherit from the open case.
    if decline_class == "unknown" and prior_case and norm["kind"] != "failure":
        decline_class = prior_case.get("decline_class", "unknown")
        family = prior_case.get("decline_family", "unknown")
        hit = "inherited_from_case"

    if domain == "recurring":
        mandate_status = infer_mandate_status(norm, prior_case)
        if mandate_status == "active" and decline_class == "mandate_cancelled":
            mandate_status = "cancelled"  # description wins over a stale subscription status
    else:
        mandate_status = "n/a"  # no mandate concept for checkout / receivables

    return {
        "decline_class": decline_class,
        "decline_family": family,
        "domain": domain,
        "rail": norm.get("rail", "unknown"),
        "mandate_status": mandate_status,
        "mandate_category": norm.get("context", {}).get("mandate_category")
        or profile.get("mandate_category", "general"),
        "keyword_hit": hit,
        "mandate_status_source": _mandate_source(norm, prior_case),
    }


def infer_mandate_status(norm: dict, prior_case: dict | None) -> str:
    ctx = norm.get("context", {})
    if ctx.get("mandate_status"):
        return ctx["mandate_status"]
    if norm.get("mandate_status_raw"):
        return str(norm["mandate_status_raw"]).lower()
    if norm["event_type"] in EVENT_TO_MANDATE:
        return EVENT_TO_MANDATE[norm["event_type"]]
    status = (norm.get("subscription_status") or "").lower()
    if status in STATUS_TO_MANDATE:
        return STATUS_TO_MANDATE[status]
    if prior_case and prior_case.get("mandate_status"):
        return prior_case["mandate_status"]
    return "unknown"


def _mandate_source(norm: dict, prior_case: dict | None) -> str:
    if norm.get("context", {}).get("mandate_status"):
        return "context_override"
    if norm.get("mandate_status_raw"):
        return "webhook_field"
    if norm["event_type"] in EVENT_TO_MANDATE:
        return f"event:{norm['event_type']}"
    if (norm.get("subscription_status") or "").lower() in STATUS_TO_MANDATE:
        return f"inferred_from_subscription.status={norm['subscription_status']}"
    if prior_case and prior_case.get("mandate_status"):
        return "inherited_from_case"
    return "unknown"

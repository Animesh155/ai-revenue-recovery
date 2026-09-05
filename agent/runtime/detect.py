"""DETECT: evaluate detectors.json against snapshot entities -> risk.* events (revenue at risk, pre-failure)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import AGENT_DIR, iso, load_json, parse_ts
from .shield import MISSING, _match_cond

DETECTORS_PATH = AGENT_DIR / "detectors.json"

RAIL_BY_METHOD = {"card": "card", "upi": "upi", "netbanking": "netbanking", "emandate": "netbanking"}


def _days(now: datetime, ts) -> float | None:
    if not ts:
        return None
    return (parse_ts(ts) - now).total_seconds() / 86400.0


def _card_expiry_days(now: datetime, exp: str | None) -> float | None:
    """exp as 'YYYY-MM' or 'MM/YY'; card is valid through the last day of that month."""
    if not exp:
        return None
    exp = str(exp)
    if "/" in exp:
        mm, yy = exp.split("/")
        year, month = 2000 + int(yy), int(mm)
    else:
        year, month = int(exp[:4]), int(exp[5:7])
    if month == 12:
        year, month = year + 1, 1
    else:
        month += 1
    end = datetime(year, month, 1, tzinfo=now.tzinfo)
    return (end - now).total_seconds() / 86400.0


def derive_entity_state(entity_type: str, e: dict, now: datetime) -> dict:
    s = dict(e)
    s["entity_type"] = entity_type
    if entity_type == "subscription":
        s["card_expiry_days_from_now"] = _card_expiry_days(now, e.get("card_expiry"))
        s["next_charge_days_from_now"] = _days(now, e.get("next_charge_at"))
        s["mandate_end_days_from_now"] = _days(now, e.get("mandate_end_at"))
        s["hours_in_status"] = -(_days(now, e.get("status_since")) or 0) * 24 if e.get("status_since") else None
        s.setdefault("mandate_category", "general")
        s.setdefault("afa_scheduled", False)
    elif entity_type == "invoice":
        s["due_days_from_now"] = _days(now, e.get("due_at"))
    elif entity_type == "order":
        s["minutes_since_created"] = -(_days(now, e.get("created_at")) or 0) * 1440 if e.get("created_at") else None
        s.setdefault("has_contact", bool(e.get("contact") or e.get("email")))
    return s


def detect(snapshot: dict, detectors: dict | None = None, now: datetime | None = None) -> list[dict]:
    """snapshot: {event:'snapshot', occurred_at, entities:[{entity_type, entity:{...}}]} or a single entity_type/entity."""
    detectors = detectors or load_json(DETECTORS_PATH)
    now = now or parse_ts(snapshot.get("occurred_at"))
    items = snapshot.get("entities") or [{"entity_type": snapshot["entity_type"], "entity": snapshot["entity"]}]
    risks = []
    for it in items:
        et, e = it["entity_type"], it["entity"]
        state = derive_entity_state(et, e, now)
        for rule in detectors["rules"]:
            if rule["entity_type"] != et:
                continue
            if all(_match_cond(state.get(k, MISSING), cond, state) for k, cond in rule["when"].items()):
                risks.append(_risk_event(rule, et, e, state, now))
    return risks


def _risk_event(rule: dict, et: str, e: dict, state: dict, now: datetime) -> dict:
    amount = state.get(rule["amount_field"])
    entity_id = e.get("id")
    ev = {
        "event": f"risk.{rule['id']}",
        "rule_id": rule["id"],
        "domain": rule["domain"],
        "risk_class": rule["risk_class"],
        "entity_type": et,
        "subscription_id": entity_id,  # case key for every domain
        "amount_paise": int(amount) if amount is not None else None,
        "currency": e.get("currency", "INR"),
        "method": e.get("payment_method"),
        "occurred_at": iso(now),
        "source": rule["source"],
        "suggested_path": rule.get("suggested_path"),
        "context": dict(e.get("context", {})),
        "payment_id": f"risk_{rule['id']}_{entity_id}_{now.date().isoformat()}",  # dedupe per entity+rule+day
        "subscription_status": e.get("status") if et == "subscription" else None,
    }
    if et == "subscription":
        ev["context"].setdefault("mandate_status", "active")
        ev["context"].setdefault("mandate_category", state.get("mandate_category", "general"))
    return ev

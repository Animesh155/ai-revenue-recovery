"""Case ledger: persistent per-subscription trajectory state + context derivation for the shield."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .config import (
    CONTACT,
    DEFAULT_NETWORK_CAP,
    IST,
    MONEY_MOVING,
    NETWORK_CAPS,
    Specs,
    dump_json,
    hours_between,
    iso,
    load_json,
    parse_ts,
)

TERMINAL_STATUSES = {"recovered", "stopped_horizon", "stopped_ev", "stopped_tiers", "human", "noop_closed"}


def case_path(cases_dir: Path, case_id: str) -> Path:
    return cases_dir / f"{case_id}.json"


def load_case(cases_dir: Path, case_id: str) -> dict | None:
    p = case_path(cases_dir, case_id)
    if not p.exists():
        return None
    case = load_json(p)
    # Older case files (pre-domain/flags) still load for demo re-runs.
    case.setdefault("flags", {"fraud_flag": "unknown", "customer_dnd": "unknown", "customer_opted_out": False})
    case.setdefault("domain", "recurring")
    case.setdefault("origin", "webhook")
    case.setdefault("action_uses", {})
    case.setdefault("contacts", [])
    case.setdefault("cost_paise", 0)
    return case


def save_case(cases_dir: Path, case: dict) -> None:
    dump_json(case_path(cases_dir, case["case_id"]), case)


def open_case(norm: dict, cls: dict, specs: Specs, now: datetime) -> dict:
    ctx = norm.get("context", {})
    return {
        "case_id": norm["subscription_id"],
        "subscription_id": norm["subscription_id"],
        "opened_at": iso(now),
        "horizon_days": specs.horizon_days(),
        "status": "active",
        "terminal_reason": None,
        "domain": cls.get("domain", "recurring"),
        "origin": norm.get("origin", "webhook"),
        "risk_rule": norm.get("event_type") if norm.get("origin") == "detector" else None,
        "decline_class": cls["decline_class"],
        "decline_family": cls["decline_family"],
        "amount_paise": norm.get("amount_paise"),
        "rail": norm.get("rail", "unknown"),
        "card_network": norm.get("card_network") or ctx.get("card_network"),
        "mandate_status": cls["mandate_status"],
        "mandate_category": cls["mandate_category"],
        "flags": {
            "customer_dnd": ctx.get("customer_dnd", "unknown"),
            "customer_opted_out": ctx.get("customer_opted_out", False),
            "fraud_flag": ctx.get("fraud_flag", "unknown"),
        },
        "failed_debits": 0,
        "gateway_retries": 0,
        "network_retries_30d": ctx.get("network_retries_30d", 0),
        "action_uses": {},
        "active_action": None,
        "active_action_at": None,
        "contacts": [],
        "last_failed_debit_at": None,
        "pre_debit_sent_at": None,
        "processed_payment_ids": [],
        "next_eligible_at": None,
        "recovered_at": None,
        "recovered_amount_paise": 0,
        "recovery_via": None,
        "attribution": None,
        "cost_paise": 0,
        "history": [],
    }


def apply_event(case: dict, norm: dict, cls: dict, now: datetime) -> dict:
    """Fold an incoming event into the case BEFORE deciding. Returns an `event_effect` dict."""
    effect = {"duplicate": False, "recovered": False, "state_change": None}
    ctx = norm.get("context", {})
    for k in ("customer_dnd", "customer_opted_out", "fraud_flag"):
        if k in ctx:
            case["flags"][k] = ctx[k]
    if "network_retries_30d" in ctx:
        case["network_retries_30d"] = ctx["network_retries_30d"]
    if norm.get("card_network"):
        case["card_network"] = norm["card_network"]

    pid = norm.get("payment_id")
    if pid and pid in case["processed_payment_ids"]:
        effect["duplicate"] = True
        return effect
    if pid:
        case["processed_payment_ids"].append(pid)

    if norm["kind"] == "failure":
        case["failed_debits"] += 1
        case["last_failed_debit_at"] = iso(now)
        if case["active_action"] == "defer_to_gateway":
            case["gateway_retries"] += 1
        if case["rail"] == "card":
            case["network_retries_30d"] = int(case.get("network_retries_30d", 0)) + 1
        if norm.get("amount_paise"):
            case["amount_paise"] = norm["amount_paise"]
        # Keep the ORIGINAL cause. `retries_exhausted` (subscription.halted) is a state, not a decline
        # cause; it must not overwrite insufficient_funds/card_expired for reporting or priors.
        if cls["decline_class"] not in ("unknown", "retries_exhausted") or case.get("decline_class") in (None, "unknown"):
            case["decline_class"], case["decline_family"] = cls["decline_class"], cls["decline_family"]
        case["mandate_status"] = cls["mandate_status"]
        effect["state_change"] = f"failure:{norm['event_type']}"

    elif norm["kind"] == "recovery":
        case["status"] = "recovered"
        case["terminal_reason"] = norm["event_type"]
        case["recovered_at"] = iso(now)
        case["recovered_amount_paise"] = int(norm.get("amount_paise") or case.get("amount_paise") or 0)
        case["recovery_via"] = norm.get("via")
        if case.get("domain", "recurring") == "recurring":
            case["mandate_status"] = "active"
        effect["recovered"] = True

    elif norm["kind"] == "state":
        case["mandate_status"] = cls["mandate_status"]
        effect["state_change"] = f"state:{norm['event_type']}"

    # tick: nothing to fold
    return effect


def derive_context(case: dict, specs: Specs, now: datetime) -> dict:
    """Flat state dict the shield evaluates. Time conditions are evaluated at the INTENDED action time
    (constraints.json meta.evaluation.timezone): a merchant debit is scheduled `latency_hours` out."""
    retry_latency = float(specs.tier("schedule_retry").get("latency_hours", 24))
    intended_debit_at = now + timedelta(hours=retry_latency)

    profile = specs.profile
    can_notice = profile.get("can_send_pre_debit_notice", True)

    last_fail = parse_ts(case["last_failed_debit_at"]) if case.get("last_failed_debit_at") else None
    contacts = [parse_ts(c["at"]) for c in case.get("contacts", [])]
    last_contact = max(contacts) if contacts else None
    contacts_7d = sum(1 for c in contacts if hours_between(now, c) <= 24 * 7)

    state = {
        # normalized_event / classification
        "event_type": case.get("last_event_type"),
        "amount_paise": case.get("amount_paise"),
        "rail": case.get("rail"),
        "mandate_status": case.get("mandate_status"),
        "decline_class": case.get("decline_class"),
        "decline_family": case.get("decline_family"),
        "domain": case.get("domain", "recurring"),
        "origin": case.get("origin", "webhook"),
        "mandate_category": case.get("mandate_category", "general"),
        # merchant profile
        "retry_owner": profile.get("retry_owner", "gateway"),
        # context derived
        "fraud_flag": case.get("flags", {}).get("fraud_flag", "unknown"),
        "customer_opted_out": case.get("flags", {}).get("customer_opted_out", False),
        "customer_dnd": case.get("flags", {}).get("customer_dnd", "unknown"),
        # A-PD: schedule_retry sends the pre-debit notice at T and debits at T+latency.
        "pre_debit_notice_sent": True if can_notice else False,
        "hours_since_pre_debit_notice": retry_latency if can_notice else None,
        "hours_since_last_failed_debit": hours_between(intended_debit_at, last_fail) if last_fail else None,
        "intended_debit_at": iso(intended_debit_at),
        "intended_send_ist_hour": now.astimezone(IST).hour,
        "contacts_last_7d": contacts_7d,
        "hours_since_last_contact": hours_between(now, last_contact) if last_contact else None,
        "card_network": case.get("card_network"),
        "network_retries_30d": int(case.get("network_retries_30d", 0)),
        "attempts_used": int(case.get("failed_debits", 0)),
        "payment_id_in_processed": False,  # set by loop when a duplicate is detected
        # symbolic thresholds
        "network_cap": NETWORK_CAPS.get((case.get("card_network") or "").lower(), DEFAULT_NETWORK_CAP),
        "max_attempts_for_class": specs.max_attempts_for_class(case.get("decline_class", "unknown")),
    }
    return state


def record_action(case: dict, action: str, ev: dict | None, reason: str, specs: Specs, now: datetime, run_id: str) -> None:
    tier = specs.tier(action)
    case["action_uses"][action] = case["action_uses"].get(action, 0) + 1
    case["active_action"] = action
    case["active_action_at"] = iso(now)
    case["cost_paise"] = int(case.get("cost_paise", 0)) + int(tier.get("cost_paise", 0))
    if action in CONTACT:
        case["contacts"].append({"at": iso(now), "action": action})
    if action == "schedule_retry":
        case["pre_debit_sent_at"] = iso(now)
    latency = tier.get("latency_hours")
    case["next_eligible_at"] = iso(now + timedelta(hours=float(latency))) if latency else None
    case["history"].append(
        {
            "at": iso(now),
            "run_id": run_id,
            "action": action,
            "ev_paise": ev.get("ev_paise") if ev else None,
            "p_recover": ev.get("p_recover") if ev else None,
            "reason": reason,
            "outcome": "pending",
        }
    )


def close_case(case: dict, status: str, reason: str, now: datetime) -> None:
    case["status"] = status
    case["terminal_reason"] = reason
    case["closed_at"] = iso(now)
    case["next_eligible_at"] = None


def days_elapsed(case: dict, now: datetime) -> float:
    return (now - parse_ts(case["opened_at"])).total_seconds() / 86400.0


def is_terminal(case: dict) -> bool:
    return case.get("status") in TERMINAL_STATUSES

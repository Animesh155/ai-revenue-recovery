"""INGEST: raw webhook / tick / outcome event -> NormalizedEvent dict."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import iso, parse_ts

FAILURE_EVENTS = {"subscription.pending", "payment.failed", "subscription.halted"}
STATE_EVENTS = {"subscription.cancelled", "subscription.paused", "subscription.resumed", "mandate.registered"}
RECOVERY_EVENTS = {"subscription.charged", "payment.captured", "invoice.paid", "payment_link.paid", "order.paid"}
TICK_EVENTS = {"tick"}
# risk.* events (from detectors.json) open a case exactly like a failure, with origin=detector

RAIL_BY_METHOD = {
    "card": "card",
    "upi": "upi",
    "netbanking": "netbanking",
    "emandate": "netbanking",
    "nach": "netbanking",
}


def _get(d: dict | None, *path: str, default: Any = None) -> Any:
    cur: Any = d or {}
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def normalize(raw: dict, now: datetime | None = None) -> dict:
    """Map a Razorpay-shaped webhook (or our internal tick/outcome event) to NormalizedEvent."""
    event_type = raw.get("event") or raw.get("event_type") or "unknown"
    payload = raw.get("payload", {})
    sub = _get(payload, "subscription", "entity", default={})
    pay = _get(payload, "payment", "entity", default={})
    inv = _get(payload, "invoice", "entity", default={})

    occurred = raw.get("occurred_at") or raw.get("created_at") or (now.isoformat() if now else None)
    if occurred is None:
        raise ValueError("event has no occurred_at/created_at and no `now` supplied")

    method = (pay.get("method") or raw.get("method") or "").lower()
    rail = RAIL_BY_METHOD.get(method, "unknown")

    amount = pay.get("amount") or inv.get("amount") or raw.get("amount_paise") or sub.get("amount")

    norm = {
        "event_type": event_type,
        "gateway": raw.get("gateway", "razorpay"),
        "subscription_id": sub.get("id") or raw.get("subscription_id") or pay.get("subscription_id"),
        "payment_id": pay.get("id") or raw.get("payment_id"),
        "invoice_id": inv.get("id") or raw.get("invoice_id") or pay.get("invoice_id"),
        "amount_paise": int(amount) if amount is not None else None,
        "currency": pay.get("currency") or inv.get("currency") or raw.get("currency", "INR"),
        "rail": rail,
        "error_description": pay.get("error_description") or raw.get("error_description"),
        "error_code": pay.get("error_code") or raw.get("error_code"),
        "card_network": (_get(pay, "card", "network") or raw.get("card_network") or "").lower() or None,
        "subscription_status": sub.get("status") or raw.get("subscription_status"),
        "mandate_status_raw": raw.get("mandate_status") or _get(payload, "token", "entity", "recurring_status"),
        "occurred_at": iso(parse_ts(occurred)),
        "via": raw.get("via"),  # recovery events: gateway_retry | merchant_retry | customer_action | organic
        "context": raw.get("context", {}),  # scenario/context overrides: customer_dnd, fraud_flag, ...
        "kind": _kind(event_type),
        "domain": raw.get("domain") or _domain(event_type, inv, raw),
        "origin": "detector" if event_type.startswith("risk.") else "webhook",
        "risk_class": raw.get("risk_class"),
        "risk_source": raw.get("source"),
    }
    return norm


def _domain(event_type: str, inv: dict, raw: dict) -> str:
    if event_type.startswith("order.") or raw.get("entity_type") == "order":
        return "checkout"
    if event_type.startswith("invoice.") or inv or raw.get("entity_type") == "invoice":
        return "receivables"
    return "recurring"


def _kind(event_type: str) -> str:
    if event_type in FAILURE_EVENTS or event_type.startswith("risk."):
        return "failure"
    if event_type in RECOVERY_EVENTS:
        return "recovery"
    if event_type in STATE_EVENTS:
        return "state"
    if event_type in TICK_EVENTS:
        return "tick"
    return "unknown"

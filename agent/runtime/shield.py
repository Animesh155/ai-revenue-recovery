"""SHIELD: evaluate constraints.json against a flat state -> feasible set + firings.

Pure interpreter of the declarative catalog. Adds the fail-closed `unknown_handling` rules as
pseudo-constraints with ids `unknown.<field>` so they appear in the audit like any other veto.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import ACTIONS, MONEY_MOVING

MISSING = object()
UNKNOWN_VALUES = {None, "unknown", "", MISSING}


@dataclass
class Firing:
    constraint_id: str
    group: str
    verdict: str
    blocks_actions: list[str]
    source: str
    requires: str | None = None
    wait_hours: float | None = None


@dataclass
class ShieldResult:
    feasible: list[str]
    blocked: dict[str, list[str]] = field(default_factory=dict)  # action -> constraint ids
    deferred_only: list[str] = field(default_factory=list)      # actions blocked ONLY by `defer` verdicts
    firings: list[Firing] = field(default_factory=list)
    require_action: bool = False
    requires: str | None = None
    min_wait_hours: float | None = None

    def to_dict(self) -> dict:
        return {
            "feasible_actions": self.feasible,
            "blocked": self.blocked,
            "deferred_only": self.deferred_only,
            "require_action": self.require_action,
            "requires": self.requires,
            "min_wait_hours": self.min_wait_hours,
            "firings": [f.__dict__ for f in self.firings],
        }


def _resolve(value: Any, state: dict) -> Any:
    """Thresholds may be symbolic names (e.g. 'network_cap') resolved from state."""
    if isinstance(value, str) and value in state and isinstance(state[value], (int, float)):
        return state[value]
    return value


def _match_cond(actual: Any, cond: Any, state: dict) -> bool:
    if actual in UNKNOWN_VALUES:
        return False  # unknowns never positively match a `when`; handled by unknown_handling
    if not isinstance(cond, dict):
        return actual == cond
    for op, raw in cond.items():
        v = _resolve(raw, state)
        if op in ("gt", "gte", "lt", "lte", "outside") and (v is None or isinstance(v, str)):
            return False  # unresolvable symbolic threshold -> constraint cannot be shown to apply
        if op == "in" and actual not in v:
            return False
        if op == "not_in" and actual in v:
            return False
        if op == "gt" and not actual > v:
            return False
        if op == "gte" and not actual >= v:
            return False
        if op == "lt" and not actual < v:
            return False
        if op == "lte" and not actual <= v:
            return False
        if op == "outside":
            lo, hi = v
            if lo <= actual < hi:
                return False
    return True


def _wait_hours(constraint: dict, state: dict) -> float | None:
    """Best-effort: how long until a `defer` constraint would stop firing."""
    cid = constraint["id"]
    if cid == "rbi_min_24h_before_represent":
        h = state.get("hours_since_last_failed_debit") or 0
        return max(0.0, 24 - float(h))
    if cid == "rbi_pre_debit_window_not_elapsed":
        h = state.get("hours_since_pre_debit_notice") or 0
        return max(0.0, 24 - float(h))
    if cid == "comms_min_gap":
        h = state.get("hours_since_last_contact") or 0
        return max(0.0, 12 - float(h))
    if cid == "mandate_paused_defer":
        return 24.0
    if cid == "gateway_owns_soft_retry":
        return None  # not a wait: gateway owns it; defer_to_gateway is the action
    return 24.0


def evaluate(state: dict, constraints_doc: dict) -> ShieldResult:
    blocked: dict[str, set[str]] = {a: set() for a in ACTIONS}
    verdict_by_block: dict[str, set[str]] = {a: set() for a in ACTIONS}
    firings: list[Firing] = []
    require_action, requires = False, None
    waits: list[float] = []

    def fire(cid: str, group: str, verdict: str, blocks: list[str], source: str, req: str | None = None, wait: float | None = None):
        nonlocal require_action, requires
        firings.append(Firing(cid, group, verdict, list(blocks), source, req, wait))
        for a in blocks:
            blocked[a].add(cid)
            verdict_by_block[a].add(verdict)
        if verdict == "require_action":
            require_action = True
            requires = requires or req
        if wait is not None:
            waits.append(wait)

    # --- declared constraints -------------------------------------------------
    for c in constraints_doc["constraints"]:
        when = c.get("when", {})
        if all(_match_cond(state.get(k, MISSING), cond, state) for k, cond in when.items()):
            wait = _wait_hours(c, state) if c["verdict"] == "defer" else None
            fire(c["id"], c["group"], c["verdict"], c["blocks_actions"], c["source"], c.get("requires"), wait)

    # --- fail-closed unknown handling (constraints.json meta.unknown_handling) -
    U = "unknown_handling"
    recurring = state.get("domain", "recurring") == "recurring"
    if recurring and state.get("mandate_status", MISSING) in UNKNOWN_VALUES:
        fire("unknown.mandate_status", U, "require_action", sorted(MONEY_MOVING),
             "Fail-closed: mandate_status unknown -> cannot prove debit authority.", "mandate_reregister")
    if state.get("pre_debit_notice_sent", MISSING) in UNKNOWN_VALUES:
        fire("unknown.pre_debit_notice_sent", U, "block", ["schedule_retry"],
             "Fail-closed: cannot prove 24h pre-debit notice for a merchant-initiated debit (F1: defer_to_gateway exempt).")
    if state.get("customer_dnd", MISSING) in UNKNOWN_VALUES:
        fire("unknown.customer_dnd", U, "block", ["dunning_whatsapp"],
             "Fail-closed: DND unknown -> block promotional WhatsApp; email + transactional re-register allowed.")
    if state.get("amount_paise", MISSING) in UNKNOWN_VALUES:
        fire("unknown.amount_paise", U, "block", sorted(MONEY_MOVING),
             "Fail-closed: amount unknown -> AFA threshold checks cannot be cleared.")
    if state.get("fraud_flag", MISSING) in UNKNOWN_VALUES:
        firings.append(Firing("unknown.fraud_flag", U, "allow", [], "fraud_flag unknown -> allow (absence is not a positive flag); logged."))

    feasible = [a for a in ACTIONS if not blocked[a]]
    deferred_only = [a for a in ACTIONS if blocked[a] and verdict_by_block[a] == {"defer"}]
    return ShieldResult(
        feasible=feasible,
        blocked={a: sorted(ids) for a, ids in blocked.items() if ids},
        deferred_only=deferred_only,
        firings=firings,
        require_action=require_action,
        requires=requires,
        min_wait_hours=min(waits) if waits else None,
    )

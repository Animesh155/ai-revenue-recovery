"""STOP? + PLAN: cost-ordered positive-EV selection inside the shield's feasible set.

Decisions: act | wait | stop | noop.  Never chooses an action outside shield.feasible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .case import days_elapsed
from .config import CONTACT, MONEY_MOVING, TERMINAL, Specs
from .shield import ShieldResult

# `block`-verdict constraints that are actually rolling windows -> treat as wait, not permanent
ROLLING_BLOCKS = {"comms_frequency_cap": 24.0, "trai_quiet_hours": 12.0}


@dataclass
class Plan:
    decision: str                      # act | wait | stop | noop
    action: str | None = None
    status: str | None = None          # for stop: stopped_horizon | stopped_ev | stopped_tiers | human | noop_closed
    reason: str = ""
    ev_table: list[dict] = field(default_factory=list)
    wait_hours: float | None = None
    days_elapsed: float = 0.0
    horizon_days: int = 21

    def to_dict(self) -> dict:
        return self.__dict__


def _attempt_index(case: dict, action: str) -> int:
    if action in MONEY_MOVING:
        return max(0, int(case.get("failed_debits", 0)) - 1)
    return int(case.get("action_uses", {}).get(action, 0))


def ev_table(case: dict, shield: ShieldResult, specs: Specs) -> list[dict]:
    amount = int(case.get("amount_paise") or 0)
    rows = []
    for t in specs.tiers_by_order():
        a = t["action"]
        uses = int(case.get("action_uses", {}).get(a, 0))
        idx = _attempt_index(case, a)
        p, src = specs.prior(case.get("decline_class", "unknown"), a, idx)
        if a == "noop":
            p, src = 0.0, "noop recovers nothing"
        cost = int(t.get("cost_paise", 0))
        ev = round(p * amount - cost)
        feasible = a in shield.feasible
        exhausted = uses >= int(t.get("max_uses", 1))
        row = {
            "action": a, "tier": t["order"], "class": t.get("class"),
            "cost_paise": cost, "p_recover": p, "p_source": src, "attempt_index": idx,
            "ev_paise": ev, "uses": uses, "max_uses": t.get("max_uses", 1),
            "feasible": feasible, "blocked_by": shield.blocked.get(a, []),
            "eligible": feasible and not exhausted and ev > 0 and a not in TERMINAL,
        }
        if not feasible:
            row["reason"] = "blocked by shield"
        elif a in TERMINAL:
            row["reason"] = "terminal"
        elif exhausted:
            row["reason"] = "max_uses reached"
        elif ev <= 0:
            row["reason"] = "EV<=0 (fails EV floor)"
        rows.append(row)
    return rows


def _wait_for(actions: list[str], shield: ShieldResult) -> float | None:
    """If every blocker on these actions is a defer or rolling block, return the wait in hours."""
    waits = []
    for a in actions:
        ids = shield.blocked.get(a, [])
        if not ids:
            continue
        for cid in ids:
            f = next((x for x in shield.firings if x.constraint_id == cid), None)
            if f is None:
                return None
            if f.verdict == "defer":
                waits.append(f.wait_hours if f.wait_hours is not None else 24.0)
            elif cid in ROLLING_BLOCKS:
                waits.append(ROLLING_BLOCKS[cid])
            else:
                return None
    return min(waits) if waits else None


def plan(case: dict, state: dict, shield: ShieldResult, specs: Specs, now: datetime) -> Plan:
    horizon = specs.horizon_days()
    elapsed = days_elapsed(case, now)
    table = ev_table(case, shield, specs)
    P = Plan(decision="stop", ev_table=table, days_elapsed=round(elapsed, 2), horizon_days=horizon)

    if state.get("payment_id_in_processed"):
        P.decision, P.action, P.reason = "noop", "noop", "duplicate payment_id (idempotency_duplicate_event)"
        return P

    if elapsed > horizon:
        P.status, P.reason = "stopped_horizon", f"days_elapsed {elapsed:.1f} > horizon {horizon}"
        return P

    rows = {r["action"]: r for r in table}
    eligible = [r for r in table if r["eligible"]]

    # --- unknown decline: fall back to policy.decline-actions defaults (no prior to reason with) ---
    if case.get("decline_class", "unknown") not in specs.escalation["priors"] or case.get("decline_class") == "unknown":
        fb = specs.decline_policy.get("defaults", {}).get("unknown_decline", "escalate_human")
        if fb in shield.feasible:
            P.decision, P.action, P.status = "act", fb, "human" if fb == "escalate_human" else None
            P.reason = f"decline_class unknown -> policy.decline-actions default {fb}"
        else:
            P.decision, P.status, P.reason = "stop", "noop_closed", f"decline_class unknown and fallback {fb} infeasible"
        return P

    # --- require_action collapse: only the customer-action path can make progress ---------------
    if shield.require_action:
        req = shield.requires or "mandate_reregister"
        r = rows[req]
        if r["eligible"]:
            P.decision, P.action = "act", req
            P.reason = f"require_action -> {req} (EV={r['ev_paise']}, tier {r['tier']})"
            return P
        wait = _wait_for([req], shield) if not r["feasible"] else None
        if wait is not None:
            P.decision, P.wait_hours, P.reason = "wait", wait, f"require_action -> {req} temporarily blocked ({r['blocked_by']})"
            return P
        status = "stopped_tiers" if r.get("reason") == "max_uses reached" else "stopped_ev"
        return _fallback_terminal(P, rows, why=f"require_action but {req} unavailable ({r.get('reason')}, {r['blocked_by']})", default_status=status)

    # --- normal path: cheapest feasible positive-EV, not exhausted ---------------------------------
    candidates = [r for r in eligible if r["action"] not in TERMINAL]
    if candidates:
        best = min(candidates, key=lambda r: r["tier"])
        P.decision, P.action = "act", best["action"]
        P.reason = f"cheapest feasible positive-EV tier {best['tier']} (EV={best['ev_paise']} paise, p={best['p_recover']})"
        return P

    progress = [a for a in (MONEY_MOVING | CONTACT)]
    temporarily = [a for a in progress if a not in shield.feasible]
    wait = _wait_for(temporarily, shield) if temporarily else None
    # only wait if nothing feasible could act AND at least one blocked action would be eligible once unblocked
    could_act_later = any(
        (rows[a]["uses"] < rows[a]["max_uses"] and rows[a]["ev_paise"] > 0) for a in temporarily
    )
    if wait is not None and could_act_later:
        P.decision, P.wait_hours = "wait", wait
        P.reason = f"no feasible progress action now; earliest window in {wait:.1f}h"
        return P

    feasible_progress = [rows[a] for a in progress if a in shield.feasible]
    if feasible_progress and all(r["uses"] >= r["max_uses"] for r in feasible_progress):
        return _fallback_terminal(P, rows, why="all feasible tiers exhausted (max_uses)", default_status="stopped_tiers")
    if feasible_progress:
        return _fallback_terminal(P, rows, why="no feasible action clears the EV floor", default_status="stopped_ev")
    return _fallback_terminal(P, rows, why="no progress action feasible", default_status="noop_closed")


def _fallback_terminal(P: Plan, rows: dict, why: str, default_status: str = "stopped_ev") -> Plan:
    h = rows.get("escalate_human")
    if h and h["feasible"] and h["ev_paise"] > 0 and h["uses"] < h["max_uses"]:
        P.decision, P.action, P.status = "act", "escalate_human", "human"
        P.reason = f"{why}; escalate_human clears EV floor (EV={h['ev_paise']})"
        return P
    P.decision, P.status = "stop", default_status
    P.reason = f"{why}; escalate_human EV={h['ev_paise'] if h else 'n/a'} does not clear floor" if h else why
    return P

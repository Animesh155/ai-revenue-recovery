"""The agent loop: INGEST -> CLASSIFY -> (fold event into case) -> SHIELD -> STOP?/PLAN -> EXECUTE -> AUDIT."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import audit
from .case import (
    apply_event,
    close_case,
    derive_context,
    is_terminal,
    load_case,
    open_case,
    record_action,
    save_case,
)
from .classify import classify
from .config import REPO_ROOT, Specs, dump_json, iso, parse_ts
from .ingest import normalize
from .ledger import attribute
from .planner import Plan, plan
from .shield import ShieldResult, evaluate


@dataclass
class Runtime:
    specs: Specs
    cases_dir: Path = REPO_ROOT / "cases"
    runs_dir: Path = REPO_ROOT / "runs"
    write_runs: bool = True
    audit_path: Path | None = None  # batch-level audit log (used when write_runs=False)
    stats: dict = field(default_factory=lambda: {"events": 0, "violations": 0, "actions": 0})


@dataclass
class RunResult:
    run_id: str
    norm: dict
    cls: dict
    case: dict | None
    state: dict | None
    shield: ShieldResult | None
    plan: Plan | None
    note: str = ""

    @property
    def action(self) -> str | None:
        return self.plan.action if self.plan else None


def _run_id(norm: dict, now: datetime) -> str:
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{norm['event_type'].replace('.', '-')}-{norm.get('subscription_id') or 'nosub'}"


def _archive_closed(rt: Runtime, case: dict) -> None:
    arch = rt.cases_dir / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    src = rt.cases_dir / f"{case['case_id']}.json"
    if src.exists():
        shutil.move(str(src), str(arch / f"{case['case_id']}__{(case.get('closed_at') or case.get('recovered_at') or 'x')[:19].replace(':', '')}.json"))


def process_event(raw: dict, rt: Runtime, now: datetime | None = None) -> RunResult:
    rt.stats["events"] += 1
    norm = normalize(raw, now)
    now = parse_ts(norm["occurred_at"])
    run_id = _run_id(norm, now)
    sub = norm.get("subscription_id")
    case = load_case(rt.cases_dir, sub) if sub else None
    cls = classify(norm, rt.specs.profile, case)

    # A new failure on a closed case starts a fresh trajectory.
    if case and is_terminal(case) and norm["kind"] == "failure":
        _archive_closed(rt, case)
        case = None
    if case is None:
        if norm["kind"] != "failure":
            return _finish(rt, RunResult(run_id, norm, cls, None, None, None, None, note="no open case for non-failure event -> ignored"), now)
        case = open_case(norm, cls, rt.specs, now)

    case["last_event_type"] = norm["event_type"]
    effect = apply_event(case, norm, cls, now)

    if effect["recovered"]:
        case["attribution"] = attribute(case, norm, now)
        if case["history"]:
            case["history"][-1]["outcome"] = f"recovered:{case['attribution']}"
        save_case(rt.cases_dir, case)
        return _finish(rt, RunResult(run_id, norm, cls, case, None, None, None, note=f"recovered -> {case['attribution']}"), now)

    if norm["kind"] == "failure" and not effect["duplicate"] and case["history"] and case["history"][-1]["outcome"] == "pending":
        case["history"][-1]["outcome"] = "failed"

    if is_terminal(case):
        save_case(rt.cases_dir, case)
        return _finish(rt, RunResult(run_id, norm, cls, case, None, None, None, note=f"case already terminal ({case['status']}) -> ignored"), now)

    # ---- SHIELD ---------------------------------------------------------------------------------
    state = derive_context(case, rt.specs, now)
    state["payment_id_in_processed"] = effect["duplicate"]
    shield = evaluate(state, rt.specs.constraints)

    # ---- STOP? / PLAN ---------------------------------------------------------------------------
    P = plan(case, state, shield, rt.specs, now)

    # ---- EXECUTE (record; the only side effects are ledger + artifacts) -------------------------
    if P.decision == "act":
        if P.action not in shield.feasible:  # invariant: planner never escapes the shield
            rt.stats["violations"] += 1
            case["violations"] = int(case.get("violations", 0)) + 1
            P.reason = "VIOLATION: planner chose infeasible action; overridden to noop. " + P.reason
            P.decision, P.action = "noop", "noop"
        else:
            rt.stats["actions"] += 1
            ev = next((r for r in P.ev_table if r["action"] == P.action), None)
            record_action(case, P.action, ev, P.reason, rt.specs, now, run_id)
            if P.status == "human":
                close_case(case, "human", P.reason, now)
    elif P.decision == "wait":
        case["next_eligible_at"] = iso(now + timedelta(hours=float(P.wait_hours or 24)))
        case["history"].append({"at": iso(now), "run_id": run_id, "action": "wait", "reason": P.reason, "outcome": "n/a"})
    elif P.decision == "stop":
        close_case(case, P.status or "stopped_ev", P.reason, now)
        case["history"].append({"at": iso(now), "run_id": run_id, "action": f"stop:{P.status}", "reason": P.reason, "outcome": "n/a"})
    # noop: no case mutation beyond what apply_event did

    save_case(rt.cases_dir, case)
    return _finish(rt, RunResult(run_id, norm, cls, case, state, shield, P), now)


def _finish(rt: Runtime, res: RunResult, now: datetime) -> RunResult:
    entry = {
        "at": iso(now),
        "run_id": res.run_id,
        "event_type": res.norm["event_type"],
        "subscription_id": res.norm.get("subscription_id"),
        "payment_id": res.norm.get("payment_id"),
        "decline_class": res.cls.get("decline_class"),
        "mandate_status": res.cls.get("mandate_status"),
        "feasible": res.shield.feasible if res.shield else None,
        "vetoes": [f"{f.constraint_id}->{','.join(f.blocks_actions)}" for f in res.shield.firings if f.blocks_actions] if res.shield else None,
        "decision": res.plan.decision if res.plan else None,
        "action": res.plan.action if res.plan else None,
        "status": res.case.get("status") if res.case else None,
        "note": res.note or (res.plan.reason if res.plan else ""),
    }
    if rt.write_runs:
        d = rt.runs_dir / res.run_id
        dump_json(d / "input.json", {"normalized_from": "see normalized.json"})
        dump_json(d / "normalized.json", res.norm)
        dump_json(d / "classification.json", res.cls)
        dump_json(d / "context.json", {"profile_snapshot": rt.specs.profile, "case_id": res.case["case_id"] if res.case else None})
        if res.shield is not None:
            dump_json(d / "feasible.json", {"state_at_decision": res.state, **res.shield.to_dict()})
        if res.plan is not None:
            dump_json(d / "plan.json", res.plan.to_dict())
            dump_json(d / "execution.json", _execution_doc(res))
        audit.append(d / "audit.log", entry)
    if rt.audit_path is not None:
        audit.append(rt.audit_path, entry)
    return res


def _execution_doc(res: RunResult) -> dict:
    P = res.plan
    case = res.case or {}
    doc = {"decision": P.decision, "action": P.action, "reason": P.reason, "next_eligible_at": case.get("next_eligible_at"), "case_status": case.get("status")}
    steps = {
        "defer_to_gateway": ["Do NOT call any charge API (would duplicate gateway retry).", "Watch for subscription.charged / subscription.pending / subscription.halted on this subscription."],
        "schedule_retry": ["Send RBI pre-debit notification now.", "Schedule merchant-initiated charge at next_eligible_at (>=24h).", "Handle the payment webhook as the outcome."],
        "dunning_email": ["Send transactional email with payment link (Razorpay Payment Links API).", "Log contact in case ledger (done)."],
        "dunning_whatsapp": ["Send approved WhatsApp utility template with payment link.", "Respect DND/quiet-hours (already shield-checked)."],
        "mandate_reregister": ["Send re-authentication link (fresh AFA / mandate registration).", "On mandate.registered -> resume billing; the next subscription.charged closes the case."],
        "escalate_human": ["Create ops ticket with the case ledger attached.", "No automated action follows."],
        "noop": ["No action."],
    }
    doc["concrete_steps"] = steps.get(P.action or "noop", [])
    return doc

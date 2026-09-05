"""Closed-loop batch simulation.

Generates a population of failed recurring payments, runs every case through the agent loop,
and produces outcome events from an OutcomeSource. The default source is a DECLARED response
model (synthetic). A RazorpaySandboxSource stub shows where test-mode webhooks plug in.
"""
from __future__ import annotations

import heapq
import json
import random
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from .case import is_terminal, load_case
from .config import CONTACT, IST, MONEY_MOVING, REPO_ROOT, Specs, dump_json, iso, load_json, parse_ts
from .ledger import build_report, render_markdown
from .loop import Runtime, process_event

MODEL_PATH = Path(__file__).with_name("declared-outcome-model.json")


class OutcomeSource:
    tag = "abstract"

    def respond(self, case: dict, action: str, at: datetime, rng: random.Random, model_ctx: dict) -> dict | None:
        raise NotImplementedError


class DeclaredModelSource(OutcomeSource):
    def __init__(self, model: dict):
        self.model = model
        self.tag = model.get("outcome_source_tag", "synthetic_declared")

    def respond(self, case, action, at, rng, model_ctx):
        cls = case.get("decline_class", "unknown")
        spec = self.model["response"].get(cls, {}).get(action)
        sub = case["subscription_id"]
        uses = int(case.get("action_uses", {}).get(action, 1)) - 1
        delay = float(spec["delay_hours"]) if spec else 24.0
        when = at + timedelta(hours=delay)
        p_arr = spec["p_success"] if spec else [0.0]
        p = p_arr[min(uses, len(p_arr) - 1)]
        organic = self.model.get("organic_recovery_per_day", 0.0) * (delay / 24.0)

        if rng.random() < p:
            via = "gateway_retry" if action == "defer_to_gateway" else "merchant_retry" if action == "schedule_retry" else "customer_action"
            return _recovery_event(case, when, via, model_ctx["seq"]())
        if rng.random() < organic:
            return _recovery_event(case, when, "organic", model_ctx["seq"]())
        if action in MONEY_MOVING:
            halt_after = int(self.model["gateway"]["halt_after_failed_debits"])
            if action == "defer_to_gateway" and case.get("failed_debits", 0) + 1 >= halt_after:
                return _failure_event(case, when, "subscription.halted", "Payment failed after all retry attempts", model_ctx["seq"](), status="halted")
            return _failure_event(case, when, "subscription.pending", model_ctx["error_desc"], model_ctx["seq"]())
        return _tick(case, when)


class RazorpaySandboxSource(OutcomeSource):
    """Placeholder: drive outcomes from Razorpay test-mode webhooks.
    Needs merchant.profile.has_sandbox_keys=true + a webhook receiver. Not implemented here."""
    tag = "sandbox"

    def respond(self, *a, **k):
        raise NotImplementedError("RazorpaySandboxSource requires test-mode keys and a webhook receiver; see DECISION-MODEL.md")


# ---- event constructors -------------------------------------------------------------------------
def _recovery_event(case, when, via, seq):
    domain = case.get("domain", "recurring")
    event = {"recurring": "subscription.charged", "receivables": "invoice.paid", "checkout": "order.paid"}[domain]
    return {"event": event, "occurred_at": iso(when), "via": via, "domain": domain,
            "subscription_id": case["subscription_id"],
            "payload": {"subscription": {"entity": {"id": case["subscription_id"], "status": "active"}},
                        "payment": {"entity": {"id": f"pay_{seq}", "amount": case["amount_paise"], "currency": "INR",
                                               "status": "captured", "method": case["rail"]}}}}


def _failure_event(case, when, event, error_desc, seq, status="pending"):
    return {"event": event, "occurred_at": iso(when),
            "payload": {"subscription": {"entity": {"id": case["subscription_id"], "status": status}},
                        "payment": {"entity": {"id": f"pay_{seq}", "amount": case["amount_paise"], "currency": "INR",
                                               "status": "failed", "method": case["rail"], "error_description": error_desc}}}}


def _tick(case, when):
    return {"event": "tick", "occurred_at": iso(when), "subscription_id": case["subscription_id"]}


def _pick(rng: random.Random, mix: dict) -> str:
    return rng.choices(list(mix.keys()), weights=list(mix.values()), k=1)[0]


def _risk_event_for(s: dict, seq: int) -> dict:
    """Detector-originated opening event (what detect.py would emit)."""
    return {"event": f"risk.sim_{s['decline_class']}", "risk_class": s["decline_class"], "domain": s["domain"],
            "subscription_id": s["subscription_id"], "amount_paise": s["amount_paise"], "method": s["rail"],
            "occurred_at": iso(s["start"]), "payment_id": f"risk_{seq}", "context": dict(s["context"]),
            "subscription_status": "active" if s["domain"] == "recurring" else None,
            "entity_type": {"recurring": "subscription", "receivables": "invoice", "checkout": "order"}[s["domain"]]}


def generate_population(n: int, rng: random.Random, model: dict, t0: datetime) -> list[dict]:
    pop = model["population"]
    subs = []
    for i in range(n):
        origin = _pick(rng, pop.get("origin_mix", {"webhook": 1.0}))
        if origin == "detector":
            cls = _pick(rng, pop["detector_class_mix"])
            domain = pop["detector_domain"][cls]
        else:
            cls = _pick(rng, pop["decline_class_mix"])
            domain = "recurring"
        rail = _pick(rng, pop["rail_mix"])
        amount = rng.choices(pop["amount_paise_choices"], weights=pop["amount_weights"], k=1)[0]
        f = pop["flags"]
        u = rng.random()
        dnd = True if u < f["customer_dnd_true"] else ("unknown" if u < f["customer_dnd_true"] + f["customer_dnd_unknown"] else False)
        ctx = {"customer_dnd": dnd,
               "customer_opted_out": rng.random() < f["customer_opted_out"],
               "fraud_flag": rng.random() < f["fraud_flag"]}
        if rail == "card":
            ctx["card_network"] = _pick(rng, pop["card_network_mix"])
        start = t0 + timedelta(hours=rng.uniform(0, 48))
        subs.append({"subscription_id": f"sub_{i:04d}", "decline_class": cls, "rail": rail, "amount_paise": amount,
                     "context": ctx, "start": start, "origin": origin, "domain": domain,
                     "error_description": pop["error_descriptions"].get(cls, "Payment failed")})
    return subs


def run_batch(n: int, seed: int, batch_id: str, out_root: Path = REPO_ROOT / "batches",
              specs: Specs | None = None, model_path: Path = MODEL_PATH, t0: datetime | None = None) -> dict:
    specs = specs or Specs.load()
    model = load_json(model_path)
    rng = random.Random(seed)
    t0 = t0 or datetime(2026, 9, 1, 9, 0, tzinfo=IST)
    out = out_root / batch_id
    if out.exists():
        shutil.rmtree(out)
    (out / "cases").mkdir(parents=True)
    shutil.copy(model_path, out / "declared-outcome-model.json")

    rt = Runtime(specs=specs, cases_dir=out / "cases", runs_dir=out / "runs", write_runs=False, audit_path=out / "audit.log")
    source = DeclaredModelSource(model)
    horizon_end = t0 + timedelta(days=specs.horizon_days() + 4)

    seq_n = [0]
    def seq():
        seq_n[0] += 1
        return seq_n[0]

    heap: list[tuple[datetime, int, dict]] = []
    events_log = open(out / "events.jsonl", "w", encoding="utf-8")
    error_by_sub = {}

    for s in generate_population(n, rng, model, t0):
        error_by_sub[s["subscription_id"]] = s["error_description"]
        if s["origin"] == "detector":
            first = _risk_event_for(s, seq())
        else:
            first = _failure_event({"subscription_id": s["subscription_id"], "amount_paise": s["amount_paise"], "rail": s["rail"]},
                                   s["start"], "subscription.pending", s["error_description"], seq())
            first["context"] = s["context"]
        heapq.heappush(heap, (s["start"], seq(), first))

    processed = 0
    while heap:
        at, _, ev = heapq.heappop(heap)
        if at > horizon_end:
            continue
        events_log.write(json.dumps(ev) + "\n")
        res = process_event(ev, rt)
        processed += 1
        case = res.case
        if case is None or is_terminal(case):
            continue
        sub = case["subscription_id"]
        model_ctx = {"seq": seq, "error_desc": error_by_sub.get(sub, "Payment failed")}
        P = res.plan
        if P is None:
            continue
        if P.decision == "act" and P.action in (MONEY_MOVING | CONTACT):
            nxt = source.respond(case, P.action, at, rng, model_ctx)
            if nxt:
                heapq.heappush(heap, (parse_ts(nxt["occurred_at"]), seq(), nxt))
        elif P.decision == "wait" or (P.decision == "act" and P.action == "noop"):
            when = parse_ts(case["next_eligible_at"]) if case.get("next_eligible_at") else at + timedelta(hours=24)
            heapq.heappush(heap, (when, seq(), _tick(case, when)))
        # ensure horizon can close lingering cases
        if not case.get("next_eligible_at"):
            when = at + timedelta(hours=24)
            heapq.heappush(heap, (when, seq(), _tick(case, when)))

    events_log.close()

    report = build_report(out / "cases", batch_id, source.tag, specs,
                          extra={"seed": seed, "events_processed": processed, "runtime_stats": rt.stats,
                                 "declared_model": str(out / "declared-outcome-model.json")})
    dump_json(out / "report.json", report)
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return report

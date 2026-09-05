"""Recovery ledger: attribution of recovered money + batch report.

Never one headline number. Recovered paise is split by WHO caused it:
  gateway_attributed  - gateway's own retry succeeded while we were deferring (not our win)
  agent_attributed    - merchant retry succeeded, or customer acted within the attribution window after our contact
  organic             - recovered with no agent action active in the window
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .config import CONTACT, Specs, hours_between, load_json, parse_ts

ATTRIBUTION_WINDOW_HOURS = 72.0


def attribute(case: dict, norm: dict, now: datetime, window_hours: float = ATTRIBUTION_WINDOW_HOURS) -> str:
    via = norm.get("via")
    active = case.get("active_action")
    at = case.get("active_action_at")
    within = bool(at) and hours_between(now, parse_ts(at)) <= window_hours

    if via == "gateway_retry":
        return "gateway_attributed"
    if via == "merchant_retry":
        return "agent_attributed"
    if via == "customer_action":
        return "agent_attributed" if (active in CONTACT and within) else "organic"
    if via == "organic":
        return "organic"
    # no `via` supplied: infer from what the agent had in flight
    if active == "defer_to_gateway" and within:
        return "gateway_attributed"
    if active == "schedule_retry" and within:
        return "agent_attributed"
    if active in CONTACT and within:
        return "agent_attributed"
    return "organic"


def load_cases(cases_dir: Path) -> list[dict]:
    return [load_json(p) for p in sorted(cases_dir.glob("*.json"))]


def build_report(cases_dir: Path, batch_id: str, outcome_source: str, specs: Specs, extra: dict | None = None) -> dict:
    cases = load_cases(cases_dir)
    n = len(cases)
    at_risk = sum(int(c.get("amount_paise") or 0) for c in cases)
    rec = Counter()
    rec_n = Counter()
    cost = sum(int(c.get("cost_paise") or 0) for c in cases)
    contacts_agent = 0
    violations = sum(int(c.get("violations", 0)) for c in cases)

    def _bucket():
        return {"n": 0, "at_risk": 0, "recovered_total": 0, "agent_attributed": 0, "gateway_attributed": 0, "organic": 0, "cost": 0}
    by_class: dict[str, dict] = defaultdict(_bucket)
    by_origin: dict[str, dict] = defaultdict(_bucket)
    by_domain: dict[str, dict] = defaultdict(_bucket)
    by_status = Counter()
    by_last_action = Counter()
    recovered_amount_by_last_action = Counter()

    for c in cases:
        cls = c.get("decline_class", "unknown")
        buckets = [by_class[cls], by_origin[c.get("origin", "webhook")], by_domain[c.get("domain", "recurring")]]
        for b in buckets:
            b["n"] += 1
            b["at_risk"] += int(c.get("amount_paise") or 0)
            b["cost"] += int(c.get("cost_paise") or 0)
        by_status[c.get("status")] += 1
        if c.get("status") == "recovered":
            amt = int(c.get("recovered_amount_paise") or 0)
            attr = c.get("attribution") or "organic"
            rec[attr] += amt
            rec_n[attr] += 1
            for b in buckets:
                b["recovered_total"] += amt
                b[attr] += amt
            la = c.get("active_action") or "none"
            by_last_action[la] += 1
            recovered_amount_by_last_action[la] += amt
            if attr == "agent_attributed":
                contacts_agent += len(c.get("contacts", []))

    total_rec = sum(rec.values())
    agent_rec = rec["agent_attributed"]
    report = {
        "batch_id": batch_id,
        "outcome_source": outcome_source,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "n_cases": n,
        "amount_at_risk_paise": at_risk,
        "recovered_total_paise": total_rec,
        "recovered_rate": round(total_rec / at_risk, 4) if at_risk else 0.0,
        "recovered_by_attribution_paise": dict(rec),
        "recovered_cases_by_attribution": dict(rec_n),
        "cost_paise": cost,
        "net_agent_contribution_paise": agent_rec - cost,
        "contacts_per_agent_recovery": round(contacts_agent / rec_n["agent_attributed"], 2) if rec_n["agent_attributed"] else None,
        "constraint_violations": violations,
        "by_decline_class": dict(by_class),
        "by_origin": dict(by_origin),
        "by_domain": dict(by_domain),
        "by_terminal_status": dict(by_status),
        "recovered_cases_by_last_action": dict(by_last_action),
        "recovered_amount_by_last_action_paise": dict(recovered_amount_by_last_action),
        "provenance": {
            "p_recover": "prior (validated:false) from policy.escalation-path.json; replaced by measured where n >= "
            + str(specs.escalation["meta"].get("min_n_to_replace_prior")),
            "outcomes": outcome_source,
            "attribution_window_hours": ATTRIBUTION_WINDOW_HOURS,
        },
    }
    if extra:
        report.update(extra)
    return report


def render_markdown(r: dict) -> str:
    def rs(p: int | float) -> str:
        return f"₹{p / 100:,.2f}"

    rec = r["recovered_by_attribution_paise"]
    lines = [
        f"# Batch report — `{r['batch_id']}`",
        "",
        f"**outcome_source:** `{r['outcome_source']}`  ·  **cases:** {r['n_cases']}  ·  **constraint violations:** **{r['constraint_violations']}**",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| ₹ at risk | {rs(r['amount_at_risk_paise'])} |",
        f"| ₹ recovered (total) | {rs(r['recovered_total_paise'])} ({r['recovered_rate']*100:.1f}%) |",
        f"| &nbsp;&nbsp;gateway_attributed | {rs(rec.get('gateway_attributed', 0))} — *not our win* |",
        f"| &nbsp;&nbsp;agent_attributed | {rs(rec.get('agent_attributed', 0))} |",
        f"| &nbsp;&nbsp;organic | {rs(rec.get('organic', 0))} |",
        f"| ₹ cost (comms + ops) | {rs(r['cost_paise'])} |",
        f"| **net agent contribution** | **{rs(r['net_agent_contribution_paise'])}** |",
        f"| contacts per agent recovery | {r['contacts_per_agent_recovery']} |",
        "",
        "## By decline class",
        "",
        "| class | n | at risk | recovered | agent | gateway | organic | cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cls, b in sorted(r["by_decline_class"].items()):
        lines.append(f"| {cls} | {b['n']} | {rs(b['at_risk'])} | {rs(b['recovered_total'])} | {rs(b['agent_attributed'])} | {rs(b['gateway_attributed'])} | {rs(b['organic'])} | {rs(b['cost'])} |")
    lines += ["", "## Detected (pre-failure) vs reactive (webhook)", "", "| origin | n | at risk | recovered | agent-attributed | cost |", "|---|---|---|---|---|---|"]
    for o, b in sorted(r.get("by_origin", {}).items()):
        label = "detector (revenue at risk, pre-failure)" if o == "detector" else "webhook (already failed)"
        lines.append(f"| {label} | {b['n']} | {rs(b['at_risk'])} | {rs(b['recovered_total'])} | {rs(b['agent_attributed'])} | {rs(b['cost'])} |")
    lines += ["", "## By domain", "", "| domain | n | at risk | recovered | agent-attributed | cost |", "|---|---|---|---|---|---|"]
    for d, b in sorted(r.get("by_domain", {}).items()):
        lines.append(f"| {d} | {b['n']} | {rs(b['at_risk'])} | {rs(b['recovered_total'])} | {rs(b['agent_attributed'])} | {rs(b['cost'])} |")
    lines += ["", "## Terminal status", "", "| status | n |", "|---|---|"]
    for s, n in sorted(r["by_terminal_status"].items(), key=lambda x: -x[1]):
        lines.append(f"| {s} | {n} |")
    lines += ["", "## Recovered cases by action in flight", "", "| action | n | ₹ |", "|---|---|---|"]
    for a, n in sorted(r["recovered_cases_by_last_action"].items(), key=lambda x: -x[1]):
        lines.append(f"| {a} | {n} | {rs(r['recovered_amount_by_last_action_paise'].get(a, 0))} |")
    prov = r["provenance"]
    lines += [
        "",
        "## Provenance",
        "",
        f"- **p_recover:** {prov['p_recover']}",
        f"- **outcomes:** `{prov['outcomes']}` — " + (
            "synthetic: generated from a DECLARED response model (parameters in the batch dir), NOT observed behaviour. "
            "Demonstrates the measurement pipeline + attribution split; numbers are not a performance claim."
            if prov["outcomes"].startswith("synthetic") else "observed outcomes."),
        f"- **attribution window:** {prov['attribution_window_hours']}h after the last agent action",
        "- Every figure traces to `cases/*.json` and the hash-chained `audit.log`.",
    ]
    return "\n".join(lines) + "\n"

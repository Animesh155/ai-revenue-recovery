AI Revenue Recovery** — find revenue that’s slipping away and win it back.

It runs a bounded loop:

**detect → classify → shield (compliance) → plan → execute → audit → measure money recovered**

Designed around India / Razorpay-shaped recurring payments (e-mandate / AFA / soft vs hard declines), plus thin coverage for checkout abandonment and receivables.

> Batch ₹ figures are **synthetic** (`outcome_source: synthetic_declared`). They demonstrate measurement and attribution, not a live merchant recovery rate.

## What it does

| Step | Role |
|------|------|
| **Detect** | Flags revenue at risk before failure (card/mandate expiry, AFA gap, stale pending, invoices, abandoned checkout) and reacts to failure webhooks |
| **Shield** | Deterministic constraint check — only legal actions stay feasible (no LLM in the compliance path) |
| **Plan** | Cheapest positive-EV action inside the feasible set, or **stop** |
| **Audit** | Hash-chained log of decisions |
| **Ledger** | Batch report: ₹ at risk / recovered, split **gateway / agent / organic** |


## Layout

```
agent/
  constraints.json          # hard compliance rules
  detectors.json            # pre-failure risk rules
  policy.escalation-path.json
  runtime/                  # Python loop (shield, planner, simulate, ledger)
  samples/                  # example webhook + snapshot
batches/demo-n100-s3/       # sample report (regenerate with simulate)
```

## Run

Needs **Python 3.11+** (`python3` on macOS).

```bash
python3 -m agent.runtime test

python3 -m agent.runtime run-event \
  agent/samples/subscription.pending.insufficient_funds.json \
  --now 2026-09-04T13:19:00+05:30

python3 -m agent.runtime detect agent/samples/snapshot.mixed.json --run

python3 -m agent.runtime simulate --n 100 --seed 3 --batch demo-n100-s3

python3 -m agent.runtime verify-audit batches/demo-n100-s3/audit.log
```

## Notes

- Soft declines often **defer to the gateway** (Razorpay retries); that recovery is attributed to the gateway, not the agent.  
- Pre-failure / checkout / receivables cases are **contact-only** — no silent debit.  
- Stopping appears on cases as `stopped_tiers`, `stopped_ev`, or `noop_closed` when escalation no longer clears the EV floor.

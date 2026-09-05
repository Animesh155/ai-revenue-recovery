# Agent Construction

Declarative-spec agent for revenue recovery. Decisions live in JSON (`constraints.json`, `policy.escalation-path.json`); a thin deterministic runtime (`agent/runtime/`, stdlib only, no ML) executes them. The LLM orchestrator can still walk the loop by hand for a single event — the runtime exists because batch measurement (hundreds of loop executions) and the L0 golden tests need it. See `DECISION-MODEL.md` for the decision architecture.

---

## Design principles

1. **Shield before policy** — `constraints.json` defines the feasible action set; nothing downstream can re-enable a blocked action
2. **Cheapest legal action, then escalate** — `policy.escalation-path.json` picks the lowest-cost feasible action with positive EV; escalation happens across ticks
3. **Context before action** — no runs without `merchant.profile.json`
4. **Fail closed** — missing data never unlocks a money-moving or promotional action
5. **Artifacts over prose** — JSON per step; every decision hash-chained in `audit.log`
6. **India rails are constraints** — mandate halt, pre-debit, cancelled mandate, AFA thresholds = hard stops
7. **No untagged numbers** — every `p_recover` is `prior (validated:false)` until measured; batch reports state `outcome_source`

---

## Roles (you wear all hats)

| Role | You do | You don't |
|---|---|---|
| **Orchestrator** | Create run dir, load profile + policy | Invent merchant facts |
| **Ingestor** | Raw input → `normalized.json` | Classify or plan |
| **Classifier** | Decline class, rail, soft/hard | Pick retry timing freely |
| **Planner** | First matching policy rule → `plan.json` | Override policy |
| **Executor** | Document exact next action (API, dashboard, message) | Run code sandboxes |
| **Auditor** | Append `audit.log` every step | Skip logging |

---

## Persistent files

| File | Purpose |
|---|---|
| `agent/merchant.profile.json` | Integration facts incl. `recovery_horizon_days` (gitignored) |
| `agent/merchant.profile.json.example` | Template |
| `agent/detectors.json` | **Detect** — rules over snapshots (subscriptions/tokens/invoices/orders) → `risk.*` events, pre-failure |
| `agent/constraints.json` | **Shield** — sourced, non-overridable constraints → feasible set |
| `agent/policy.escalation-path.json` | **Planner** — cost tiers, `p_recover` priors (`validated:false`), stopping |
| `agent/policy.decline-actions.json` | Legacy fallback: default action for unknown decline classes; `max_attempts` per class |
| `agent/runtime/` | Deterministic executor: ingest → classify → shield → plan → execute → audit; batch simulate; report |
| `agent/runtime/declared-outcome-model.json` | Synthetic response model for batch simulation (NOT observed; distinct from priors) |
| `agent/samples/*.json` | Example webhooks |
| `cases/<subscription_id>.json` | Trajectory ledger per open case (closed cases → `cases/archive/`) |

---

## Per-run artifacts

```
runs/<run-id>/
  audit.log            hash-chained
  context.json         profile snapshot
  input.json
  normalized.json
  classification.json
  feasible.json        shield output: state, firings, feasible set
  plan.json            EV table, decision, reason
  execution.json       concrete next steps
```

`run-id`: `YYYYMMDD-HHMMSS-<event-type>-<subscription_id>`

Batch runs write `batches/<id>/{cases/, events.jsonl, audit.log, report.json, report.md}` (gitignored).

## Runtime commands

```
python -m agent.runtime detect agent/samples/snapshot.mixed.json --run     # revenue at risk -> cases
python -m agent.runtime run-event agent/samples/subscription.pending.insufficient_funds.json --now 2026-09-04T13:19:00+05:30
python -m agent.runtime simulate --n 100 --seed 3 --batch demo-n100-s3
python -m agent.runtime report --batch demo-n60-s1
python -m agent.runtime verify-audit batches/demo-n60-s1/audit.log
python -m agent.runtime test
```

---

## I/O contracts

See `agent/schemas/` for NormalizedEvent, Classification, RecoveryAction shapes.

**Actions:** `noop` | `defer_to_gateway` | `schedule_retry` | `dunning_email` | `dunning_whatsapp` | `mandate_reregister` | `escalate_human`

---

## Domains and origins

| `domain` | Opened by | Money-moving | Mandate rules | Recovery event |
|---|---|---|---|---|
| `recurring` | webhook (failed) **or** detector (`pre_failure`) | webhook cases only | yes | `subscription.charged` |
| `receivables` | detector (`invoice_due_soon`, `invoice_overdue`) | never | n/a | `invoice.paid` |
| `checkout` | detector (`checkout_abandoned`) | never | n/a | `order.paid` |

`origin ∈ {webhook, detector}` is carried on the case and split out in every batch report ("detected vs reactive").

---

## Gates (now implemented by the shield + planner)

| Gate | Where | Behaviour |
|---|---|---|
| **G0_CONTEXT** | `Specs.load` | profile missing → falls back to `.example`; intake still required for real runs |
| **G3_IDEMPOTENT** | `idempotency_duplicate_event` | duplicate `payment_id` → `noop`, case untouched |
| **G4_MANDATE** | `mandate_lifecycle` + `rbi_emandate` groups | cancelled/halted/none → `require_action`; paused → defer |
| **STOP** | planner | horizon · EV floor · tiers exhausted · feasible set empty |
| **Invariant** | loop | chosen action ∉ feasible → counted as a violation and overridden to `noop` (must be 0) |

---

## Classifier (ingest → classify), `agent/runtime/classify.py`

| Signal in `error_description` or event | `decline_class` | `decline_family` |
|---|---|---|
| insufficient / balance | `insufficient_funds` | soft |
| expired | `card_expired` | hard |
| mandate + cancel/revoke | `mandate_cancelled` | hard |
| do not honour / declined by bank | `do_not_honour` | soft |
| `subscription.halted` | `retries_exhausted` (**state**, does not overwrite the original cause) | soft |
| anything else | `unknown` → policy default `escalate_human` | — |

**`mandate_status` inference (F2):** never left `unknown` when the webhook tells us: `subscription.status` `pending/active/authenticated → active`, `halted → halted`, `cancelled → cancelled`, `paused → paused`; `mandate_cancelled` description overrides a stale status. Unknown only when truly unknown → fail-closed `require_action`.

---

## Findings log (bugs found by dry-run / batch, fixed in the specs)

| # | Found by | Issue | Fix |
|---|---|---|---|
| F1 | dry-run | pre-debit constraints blocked `defer_to_gateway` | scoped to `schedule_retry` (gateway owns its own notices) |
| F2 | dry-run | `mandate_status` unknown collapsed active subs to `require_action` | classifier infers from `subscription.status` |
| F3 | smoke-test | `attempts_exhausted_stop` was `require_action` → forced re-register on a live mandate | now `block` on money-moving; planner picks cheapest contact |
| F4 | batch n=60 | hard declines got `defer_to_gateway` via default prior | new constraint `hard_decline_no_retry` |
| F5 | batch n=60 | `subscription.halted` overwrote original `decline_class` | halted treated as state, cause preserved |
| F6 | golden test | idempotency left `escalate_human` feasible; duplicate marked in-flight action failed | catalog fixed; loop skips outcome marking on duplicates |
| F7 | detector smoke-test | RBI AFA threshold fired on a ₹48k **invoice**; `mandate_reregister` offered for invoices/orders | AFA/UPI-cap constraints scoped `domain: recurring`; `non_recurring_no_debit` also blocks re-register |

---

## Extending

1. New decline → keyword in `classify.py` + a `priors` block in `policy.escalation-path.json` (+ `max_attempts` rule if it differs)
2. New constraint → entry in `constraints.json` with `source` + a golden test that fires it and one that doesn't
3. New gateway → ingest field mapping in `ingest.py`, documented in a spike note first
4. Prototype ideas → `spikes/<slug>/` as notes first
5. Any change to specs → `python -m agent.runtime test` must stay green

---

## Anti-patterns

- Quoting `p_recover` priors or a `synthetic_declared` batch as a recovery rate
- Adding a term to the EV objective to "encourage" compliance — compliance is the shield, never a weight
- Retry on hard declines / cancelled / halted mandates
- One headline "recovered" number without the gateway/agent/organic split
- Treating Razorpay blog features as enabled without checking profile

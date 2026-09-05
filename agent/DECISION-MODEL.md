# Agent Decision Model

How the agent turns a failed payment into a **sequence of actions over time**, choosing inside the feasible region the shield allows. This is the "brain" that sits between `CLASSIFY` and `EXECUTE` in the loop (`voila.md`).

Derived from `spikes/constraint-path-optimization/`. Formal frame: a **shielded, cost-ordered path with an EV-floor stop** (safe-RL shielding + debt-collections optimal stopping). **No ML now** — priors + arithmetic; the same shape absorbs measured rates later.

---

## 1. Three layers, strict order

Every decision passes through these in order. A later layer can only ever *narrow* what an earlier layer allowed.

```
  s (case state)
   │
   ▼
┌──────────────────────────────────────────────┐
│ 1. SHIELD    constraints.json                 │  → A_feas(s) + vetoes
│    hard, sourced, non-overridable             │     (feasible.json)
└──────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────┐
│ 2. STOP CHECK   escalation-path.json          │  → continue | terminal
│    horizon? feasible set empty of progress?   │
│    no positive-EV action? (EV floor)          │
└──────────────────────────────────────────────┘
   │ continue
   ▼
┌──────────────────────────────────────────────┐
│ 3. PATH PLANNER   escalation-path.json        │  → chosen action a*
│    cheapest feasible action with EV>0,        │     (plan.json)
│    not already tried this cycle               │
└──────────────────────────────────────────────┘
   │
   ▼  EXECUTE → AUDIT (hash-chained)
```

**Invariant:** the planner never re-enables an action the shield removed. `a* ∈ A_feas(s)` always. Compliance is the feasible region, never a term traded against reward.

---

## 2. Why sequential — the case, not the event

A failure is not one decision; it is a **trajectory over ~21 days**. The agent is invoked repeatedly for the same subscription:
- on the **webhook** (payment/subscription failed), and
- on a **tick** (a scheduler re-invokes when a `defer` window elapses — 24h pre-debit, 12h min-gap, payday).

Each invocation is stateless *given* a persisted **case ledger**. That ledger is what makes the shield's timing/cap constraints and the planner's escalation meaningful.

### Case state — `cases/<subscription_id>.json`

```json
{
  "case_id": "sub_ABC123",
  "subscription_id": "sub_ABC123",
  "opened_at": "2026-09-04T06:00:00+05:30",
  "horizon_days": 21,
  "status": "active",                    // active | recovered | stopped_horizon | stopped_ev | require_action | human
  "decline_class": "insufficient_funds",
  "amount_paise": 49900,
  "rail": "upi",
  "attempts_used": 1,                    // money-moving attempts consumed
  "tiers_attempted": ["defer_to_gateway"],
  "contacts_last_7d": 1,
  "last_contact_at": "2026-09-04T09:00:00+05:30",
  "last_failed_debit_at": "2026-09-04T06:00:00+05:30",
  "pre_debit_notice_sent": true,
  "pre_debit_sent_at": "2026-09-03T09:00:00+05:30",
  "processed_payment_ids": ["pay_1"],
  "next_eligible_at": "2026-09-05T09:00:00+05:30",   // when the next tick should fire
  "history": [
    {"at": "2026-09-04T06:00:00+05:30", "action": "defer_to_gateway", "reason": "gateway_owns_soft_retry", "outcome": "pending"}
  ]
}
```

The shield reads its `context_derived` fields (attempts_used, contacts_last_7d, hours_since_*, processed_payment_ids…) **from this ledger**. The planner reads `tiers_attempted` to escalate. Terminal statuses close the case.

---

## 3. The selection rule (planner)

Among feasible actions, the planner computes a one-step **expected value**:

```
EV(a) = p_recover(decline_class, a, attempt_index) × amount_paise  −  cost_paise(a)
```

and applies **cost-ordered positive-EV selection**:

1. Keep feasible actions with `EV(a) > 0` **and** not in `tiers_attempted`.
2. Among those, pick the **cheapest** (lowest tier order) — *not* the max-EV.
3. If none qualify → **STOP** (terminal; see §4).

**Why cheapest-positive-EV, not argmax-EV:**
- Robust to bad priors — `p_recover` is unvalidated; ordering by cost is a decision we're actually confident in, magnitude of `p` is not.
- Minimizes spend and harassment — try the free/cheap legal lever first, escalate only after it fails on a later tick. This is exactly how collections escalates (letter → call → legal), and it respects our anti-harassment stance.
- Escalation is **across ticks**, not within one: tier N fails → next tick tries tier N+1 (now cheapest untried positive-EV feasible action).

`amount_paise` enters directly, so escalation is **amount-scaled** (Chehrazi threshold): a ₹50 human touch clears the EV floor only for high-value subscriptions.

---

## 4. Stopping — three terminal conditions

Close the case when **any** holds (mirrors the shield's `idempotency_stopping` group + optimal-stopping theory):

| Terminal | Trigger | status |
|---|---|---|
| **Recovered** | a debit succeeds / subscription reactivates | `recovered` |
| **Horizon** | `days_elapsed > horizon_days` (default 21) | `stopped_horizon` |
| **EV floor** | no feasible action has `p_recover × amount > cost` | `stopped_ev` |
| **Require-action** | shield returned `require_action` and `mandate_reregister` infeasible/exhausted | `require_action` → `human` |
| **Feasible-empty** | `A_feas(s) ⊆ {noop, escalate_human}` | `human` |

Stopping is the **real optimization lever in India** (per thesis) — knowing *when to quit* a case, not *when to retry*.

---

## 5. What's deterministic vs learned (ties to learnability map)

| Concern | Source | Now | Later |
|---|---|---|---|
| Is an action legal? | `constraints.json` (shield) | deterministic | unchanged |
| Which cheap legal action first? | tier order in `escalation-path.json` | fixed cost order | unchanged |
| `p_recover` | `escalation-path.json` priors | **conservative priors, `validated:false`** | replace **cell-by-cell** by measured `recovered/attempted` at `min_n`, keyed `decline_class × action × attempt_index` |
| When to stop | EV floor + horizon | fixed thresholds | tune horizon from data |

The **only** thing that ever "learns" is a low-dimensional `p_recover` table — `decline_class × action × attempt_index`. Everything else is shield or arithmetic. No neural policy, no high-dim state (see §feasibility + §learnability in the spike).

---

## 6. Audit — every decision is reconstructable

Each invocation appends to `runs/<run-id>/audit.log` and the case `history`:
- shield firings (constraint id + source) → `feasible.json`
- EV table for feasible actions (`p`, `amount`, `cost`, `EV`) → `plan.json`
- chosen action + which terminal/continue branch
- **hash chain**: `entry_hash = sha256(prev_hash + canonical(entry))` so the ledger is tamper-evident (audit requirement for a payments decision system).

---

## 7. Loop integration (updates `voila.md`)

```
INTAKE → INGEST → CLASSIFY → SHIELD → STOP? → PLAN → EXECUTE → AUDIT → (schedule next tick)
                              └ constraints.json ┘   └ escalation-path.json ┘
```

`SHIELD` + `STOP?` replace the old single `GATE`/`PLAN` where `policy.decline-actions.json` did keyword matching. `policy.decline-actions.json` becomes a **fallback/legacy** action map; the EV path supersedes it for cases where the feasible set has >1 action. (Migration handled separately — see §8.)

---

## 8. Migration from the current agent

1. `constraints.json` — already the shield. Keep.
2. `policy.escalation-path.json` — **new**, the planner (this design).
3. `policy.decline-actions.json` — demote to fallback: used only when the escalation path has no prior for a `decline_class` (returns a safe default action). Not deleted.
4. `cases/` — **new** persistent trajectory ledger.
5. `voila.md` / `CONSTRUCTION.md` — update loop to `SHIELD → STOP? → PLAN` once the above are agreed.

Open decisions before wiring in: horizon (21 vs 45), whether ticks are simulated-by-user or assumed external, and the exact cost figures. See spike §6.

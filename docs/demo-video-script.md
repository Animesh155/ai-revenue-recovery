# Demo video script — Revenue recovery (India)

**Length:** ~8–10 min (cut to 5 min using the “Short cut” column)  
**Claim level:** structural + measurement pipeline. Outcomes are **synthetic** / `validated:false`. Do not say “we recover X% in production.”  
**Combine story (no code merge):** agent = compliance + measured money; behaviour-aware spike = customer barrier / least-intrusive help. Show both; one decision authority is *not* claimed.

---

## Where to keep the commands during the demo

Don’t type from memory. Use one of these:

1. **Best:** open `docs/demo-commands.txt` in a **second editor tab** (or split pane). Copy one line → paste into Terminal → Return. Hide that tab if the camera shows the IDE.
2. **Notes / Stickies** on the other desktop / second monitor — same paste flow.
3. **This script** (`docs/demo-video-script.md`) scrolled to the command blocks.
4. **Avoid:** typing live; a long shell history search mid-take; putting secrets or `.env` on screen.

All demo commands use **`python3`**, not `python`.

---

## Before you hit record

| Ready | Check |
|---|---|
| Terminal | cwd = repo root; font large; clear scrollback |
| Commands | `docs/demo-commands.txt` open beside Terminal |
| Agent samples | `agent/samples/subscription.pending.insufficient_funds.json`, `agent/samples/snapshot.mixed.json` |
| Batch | Prefer existing `batches/demo-n100-s3/` **or** re-run seed 3 so numbers match the canvas |
| Canvas | Open [recovery-agent-batch](../../.cursor/projects/Users-animeshswet-razorpay-exp/canvases/recovery-agent-batch.canvas.tsx) *or* keep `batches/demo-n100-s3/report.md` full-screen |
| Optional UI | Behaviour spike: `cd spikes/behaviour-aware-payment-recovery && make seed && make dev` → http://127.0.0.1:3000 |
| Title card | “Constraint-following recovery · synthetic batch · 0 violations” |

**Cold open (say once):**  
“We detect revenue at risk, choose only interventions that pass a hard compliance shield, run a bounded recovery path, and measure recovered money with attribution. Batch outcomes are synthetic — this demo proves the loop and the audit, not a live recovery rate.”

---

## Beat sheet

| # | Time | Segment | Short cut? |
|---|---|---|---|
| 1 | 0:00–0:40 | Cold open + architecture one-liner | Keep |
| 2 | 0:40–2:30 | Live webhook → shield → plan | Keep |
| 3 | 2:30–4:00 | Detect pre-failure snapshot | Keep |
| 4 | 4:00–6:00 | Batch money + attribution + audit | Keep |
| 5 | 6:00–7:30 | Optional: behaviour-aware portal flip | Cut if &lt;8 min |
| 6 | 7:30–8:30 | One case trajectory | Keep if no §5 |
| 7 | 8:30–9:30 | Close: what we proved / what we didn’t | Keep |

---

## 1. Cold open (40s)

**Show:** Title card, then folder `agent/` (constraints.json, detectors.json, runtime/).

**Say:**  
“Two layers. A deterministic shield reads sourced constraints — RBI e-mandate, AFA thresholds, pre-debit notice, TRAI — and outputs a feasible action set. A planner picks the cheapest positive-EV action inside that set, or stops. No LLM in the compliance path.”

**Do not say:** “AI decides whether a retry is legal.”

---

## 2. Live failure walkthrough (~1:50)

**Commands:**

```bash
python -m agent.runtime run-event \
  agent/samples/subscription.pending.insufficient_funds.json \
  --now 2026-09-04T13:19:00+05:30
```

**Show on screen (pause on each):**

1. CLI summary: `decision=act`, `action=defer_to_gateway`, feasible list.  
2. Open the run dir under `runs/…/` — `classification.json` then `feasible.json` then `plan.json`.

**Say:**

- Classify: insufficient funds, soft decline, mandate inferred **active** from `pending` (not dead).  
- Shield: `schedule_retry` blocked — gateway owns soft retry; WhatsApp blocked — DND unknown, fail closed.  
- Plan: `defer_to_gateway` — cheapest tier, EV positive, cost ₹0.  
- “Constraints are evaluated in Python, not pasted into a prompt.”

**If asked live:** open `agent/constraints.json` on `gateway_owns_soft_retry` for 5 seconds.

---

## 3. Detect revenue at risk (~1:30)

**Commands:**

```bash
python -m agent.runtime detect agent/samples/snapshot.mixed.json --run
```

**Show:** count of risk events + ₹ at risk; healthy sub / fresh order **not** flagged; first actions (email / re-register / defer for stale pending).

**Say:**  
“Detect opens cases *before* a failure webhook — card or mandate expiry, AFA gap, invoices, abandoned checkout. Pre-failure and non-recurring domains are contact-only: the shield blocks silent debits.”

---

## 4. Measured money across a batch (~2:00)

**Commands (if re-running):**

```bash
python -m agent.runtime simulate --n 100 --seed 3 --batch demo-n100-s3
python -m agent.runtime verify-audit batches/demo-n100-s3/audit.log
```

**Show:** `report.md` headline table **or** canvas — then “Detected vs reactive” and attribution.

**Say:**

- ₹ at risk, recovered total, **gateway vs agent vs organic**.  
- “Gateway-attributed is Razorpay’s retry — we do not count it as our win.”  
- **0 constraint violations**; audit chain verifies.  
- Optional 15s: “A different cohort mix changes the ₹ headline; the violation count stays zero” — flash Exp B one-liner if you have `batches/exp-b-n150-s42/report.md` open.

**Do not say:** “Our agent recovers ~25% of revenue.”

**Say instead:** “Under this declared synthetic population, the pipeline reports X recovered and attributes Y to the agent.”

---

## 5. Optional — behaviour-aware cutaway (~1:30)

*Skip if short on time. Narrative combine only — not one codebase.*

**Show:** http://127.0.0.1:3000 → start **Authentication and trust** → portal → submit security concern → action becomes verifiable portal help; message `preview_only`.

**Say:**  
“Separately, a behaviour-aware sandbox maps payment facts and voluntary customer feedback to the least intrusive *help* action — COM-B barriers, no automatic debit from a reminder click. Together in a product story: understand the barrier when the customer speaks; still only execute what the compliance shield allows. We have not merged those decision engines in this demo.”

---

## 6. One case trajectory (~1:00)

**Show:** one file under `batches/demo-n100-s3/cases/` with 2–3 history steps (prefer soft decline → defer → later contact/re-register, **or** a detector case).

**Say:** each row = shield already applied → planner reason → outcome → attribution when recovered.

---

## 7. Close (60s)

**Show:** blank slide or report provenance footer.

**Three claims only:**

1. **Shield before policy** — illegal actions never reach the planner.  
2. **Bounded path** — cheapest legal action, then stop.  
3. **Measured money** — attribution + audit; rates not validated on live data.

**One limitation (say it):**  
“Priors and batch outcomes are declared synthetic. Next step is sandbox or merchant outcomes to validate `p_recover`.”

**End card:** repo commands to reproduce:

```text
python -m agent.runtime run-event agent/samples/subscription.pending.insufficient_funds.json --now 2026-09-04T13:19:00+05:30
python -m agent.runtime detect agent/samples/snapshot.mixed.json --run
python -m agent.runtime simulate --n 100 --seed 3 --batch demo-n100-s3
python -m agent.runtime verify-audit batches/demo-n100-s3/audit.log
python -m agent.runtime test
```

---

## 5-minute cut

Keep §1, §2, §4, §7. Drop detect (§3) *or* behaviour (§5). Prefer keeping detect if the brief says “detects revenue at risk.”

---

## B-roll / cutaways (optional)

| Shot | File / UI |
|---|---|
| Constraint source line | `agent/constraints.json` → `rbi_afa_high_value_general` |
| Detector rule | `agent/detectors.json` → `card_expiring_before_next_charge` |
| Findings | `agent/CONSTRUCTION.md` → F1–F7 table (2s) |
| Tests | `python -m agent.runtime test` → `23 … OK` |

---

## Phrases to avoid

| Avoid | Prefer |
|---|---|
| “AI ensures compliance” | “A deterministic shield enforces constraints in code” |
| “We recover 30%” | “This synthetic batch recovered … under declared outcomes” |
| “Behaviour and agent are fully integrated” | “Two demos, one product story; decision engines not merged” |
| “RAG / LLM reads RBI and decides” | “Catalog is sourced JSON; interpreter is Python” |

---

## Recording checklist

- [ ] Mic check; 1080p; hide bookmarks bar  
- [ ] No `.env` / secrets on screen (behaviour spike `.env` exists — don’t open it)  
- [ ] Pause 1s after each command so viewers can read  
- [ ] Export: `recovery-agent-demo.mp4` + attach `batches/demo-n100-s3/report.md` if judges can’t run code  

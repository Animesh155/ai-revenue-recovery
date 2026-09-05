# AI Revenue Recovery Agent

Hackathon Track 03–style agent: find revenue at risk and win it back under hard constraints.

**Detect → classify → shield → plan → execute → audit → measure recovered money.**

Demo batch numbers are **synthetic** (`outcome_source: synthetic_declared`). This shows the workflow, compliant escalation, stopping rules, attribution, and audit trail — not a live recovery rate.

## Layout

| Path | Purpose |
|------|---------|
| `agent/` | Constraints, detectors, policies, Python runtime |
| `docs/demo-commands.txt` | Copy-paste demo commands (`python3`) |
| `docs/demo-video-script.md` | Pitch / video script |
| `batches/demo-n100-s3/report.*` | Sample batch report |

Kept simple: no research clones, no large UI spikes, no secrets.

## Run

```bash
python3 -m agent.runtime test
python3 -m agent.runtime run-event agent/samples/subscription.pending.insufficient_funds.json --now 2026-09-04T13:19:00+05:30
python3 -m agent.runtime detect agent/samples/snapshot.mixed.json --run
python3 -m agent.runtime simulate --n 100 --seed 3 --batch demo-n100-s3
python3 -m agent.runtime verify-audit batches/demo-n100-s3/audit.log
```

Python **3.11+** required (use `python3` on macOS).

## The bar

Measured money across a batch · compliant escalation · stopping rules · audit trail.

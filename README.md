# AI Revenue Recovery Agent

Detect revenue at risk → shield (compliance) → plan → audit → measure recovered money across a batch.

Batch outcomes are **synthetic** (demo measurement pipeline, not a live recovery rate).

## Run

```bash
python3 -m agent.runtime test
python3 -m agent.runtime run-event agent/samples/subscription.pending.insufficient_funds.json --now 2026-09-04T13:19:00+05:30
python3 -m agent.runtime detect agent/samples/snapshot.mixed.json --run
python3 -m agent.runtime simulate --n 100 --seed 3 --batch demo-n100-s3
python3 -m agent.runtime verify-audit batches/demo-n100-s3/audit.log
```

Python 3.11+.

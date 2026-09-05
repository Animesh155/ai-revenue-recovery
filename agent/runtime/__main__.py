"""CLI:  python -m agent.runtime <command>

  run-event <event.json> [--now ISO]      run one webhook/tick through the loop (writes runs/ + cases/)
  simulate --n 60 --seed 1 --batch <id>   closed-loop batch with the declared outcome model -> batches/<id>/
  report --batch <id>                     rebuild report.json/report.md from batches/<id>/cases
  verify-audit <audit.log>                re-verify the hash chain
  test                                    run the L0 golden tests
"""
from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

from . import audit
from .config import REPO_ROOT, Specs, dump_json, load_json
from .detect import detect
from .ledger import build_report, render_markdown
from .loop import Runtime, process_event
from .simulate import run_batch


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agent.runtime")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run-event"); p.add_argument("path"); p.add_argument("--now"); p.add_argument("--cases", default=str(REPO_ROOT / "cases")); p.add_argument("--runs", default=str(REPO_ROOT / "runs"))
    p = sub.add_parser("simulate"); p.add_argument("--n", type=int, default=60); p.add_argument("--seed", type=int, default=1); p.add_argument("--batch", default=None); p.add_argument("--model", default=None, help="path to declared-outcome / experiment model JSON")
    p = sub.add_parser("detect"); p.add_argument("path"); p.add_argument("--run", action="store_true", help="also process emitted risk events through the loop"); p.add_argument("--cases", default=str(REPO_ROOT / "cases")); p.add_argument("--runs", default=str(REPO_ROOT / "runs"))
    p = sub.add_parser("report"); p.add_argument("--batch", required=True)
    p = sub.add_parser("verify-audit"); p.add_argument("path")
    sub.add_parser("test")
    a = ap.parse_args(argv)

    if a.cmd == "run-event":
        rt = Runtime(specs=Specs.load(), cases_dir=Path(a.cases), runs_dir=Path(a.runs), write_runs=True)
        raw = load_json(Path(a.path))
        if a.now:
            raw["occurred_at"] = a.now
        r = process_event(raw, rt)
        print(json.dumps({"run_id": r.run_id, "decision": r.plan.decision if r.plan else None, "action": r.action,
                          "feasible": r.shield.feasible if r.shield else None, "status": r.case.get("status") if r.case else None,
                          "reason": (r.plan.reason if r.plan else r.note)}, indent=2))
    elif a.cmd == "detect":
        snap = load_json(Path(a.path))
        risks = detect(snap)
        total = sum(r.get("amount_paise") or 0 for r in risks)
        print(f"{len(risks)} risk events; ₹{total/100:,.2f} at risk")
        for r in risks:
            print(f"  {r['event']:<45} {r['domain']:<12} {r['subscription_id']:<22} ₹{(r['amount_paise'] or 0)/100:>12,.2f}")
        if a.run:
            rt = Runtime(specs=Specs.load(), cases_dir=Path(a.cases), runs_dir=Path(a.runs), write_runs=True)
            print()
            for r in risks:
                res = process_event(r, rt)
                print(f"  {r['subscription_id']:<22} -> {res.plan.decision if res.plan else '-':<5} {res.action or '-':<20} feasible={res.shield.feasible if res.shield else None}")
    elif a.cmd == "simulate":
        batch = a.batch or f"batch-n{a.n}-s{a.seed}"
        kwargs = {}
        if a.model:
            kwargs["model_path"] = Path(a.model)
        rep = run_batch(a.n, a.seed, batch, **kwargs)
        print((REPO_ROOT / "batches" / batch / "report.md").read_text())
    elif a.cmd == "report":
        d = REPO_ROOT / "batches" / a.batch
        specs = Specs.load()
        tag = load_json(d / "report.json").get("outcome_source", "unknown") if (d / "report.json").exists() else "unknown"
        rep = build_report(d / "cases", a.batch, tag, specs)
        dump_json(d / "report.json", rep); (d / "report.md").write_text(render_markdown(rep))
        print(render_markdown(rep))
    elif a.cmd == "verify-audit":
        ok, bad, n = audit.verify(Path(a.path))
        print(f"{'OK' if ok else 'TAMPERED'}: {n} entries" + ("" if ok else f", chain breaks at line {bad}"))
        sys.exit(0 if ok else 1)
    elif a.cmd == "test":
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / "tests"), top_level_dir=str(REPO_ROOT))
        res = unittest.TextTestRunner(verbosity=1).run(suite)
        sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == "__main__":
    main()

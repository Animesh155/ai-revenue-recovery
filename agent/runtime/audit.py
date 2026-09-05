"""Hash-chained audit log: entry_hash = sha256(prev_hash + canonical(entry))."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

GENESIS = "GENESIS"


def _canonical(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)


def _last_hash(path: Path) -> str:
    if not path.exists():
        return GENESIS
    last = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last = line
    return json.loads(last)["entry_hash"] if last else GENESIS


def append(path: Path, entry: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = _last_hash(path)
    body = {k: v for k, v in entry.items() if k not in ("prev_hash", "entry_hash")}
    h = hashlib.sha256((prev + _canonical(body)).encode()).hexdigest()
    out = dict(body)
    out["prev_hash"], out["entry_hash"] = prev, h
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(out, separators=(",", ":"), default=str) + "\n")
    return h


def verify(path: Path) -> tuple[bool, int | None, int]:
    """Return (ok, first_bad_line_index, n_entries)."""
    prev = GENESIS
    n = 0
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            n += 1
            e = json.loads(line)
            body = {k: v for k, v in e.items() if k not in ("prev_hash", "entry_hash")}
            expect = hashlib.sha256((prev + _canonical(body)).encode()).hexdigest()
            if e.get("prev_hash") != prev or e.get("entry_hash") != expect:
                return False, i, n
            prev = e["entry_hash"]
    return True, None, n

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agent"
IST = timezone(timedelta(hours=5, minutes=30))

ACTIONS = [
    "schedule_retry",
    "defer_to_gateway",
    "dunning_email",
    "dunning_whatsapp",
    "mandate_reregister",
    "escalate_human",
    "noop",
]
MONEY_MOVING = {"schedule_retry", "defer_to_gateway"}
CONTACT = {"dunning_email", "dunning_whatsapp", "mandate_reregister"}
TERMINAL = {"escalate_human", "noop"}

NETWORK_CAPS = {"visa": 15, "mastercard": 10, "rupay": 10, "amex": 10}
DEFAULT_NETWORK_CAP = 10
DEFAULT_MAX_ATTEMPTS = 3


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")


def parse_ts(value: Any) -> datetime:
    """Accept ISO-8601 strings or epoch seconds; always return tz-aware (IST if naive)."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(IST).isoformat(timespec="seconds")


def hours_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


@dataclass
class Specs:
    constraints: dict
    escalation: dict
    decline_policy: dict
    profile: dict
    normalized_schema: dict = field(default_factory=dict)

    @classmethod
    def load(cls, agent_dir: Path = AGENT_DIR, profile_path: Path | None = None) -> "Specs":
        profile_path = profile_path or (agent_dir / "merchant.profile.json")
        if not profile_path.exists():
            profile_path = agent_dir / "merchant.profile.json.example"
        return cls(
            constraints=load_json(agent_dir / "constraints.json"),
            escalation=load_json(agent_dir / "policy.escalation-path.json"),
            decline_policy=load_json(agent_dir / "policy.decline-actions.json"),
            profile=load_json(profile_path),
            normalized_schema=load_json(agent_dir / "schemas" / "normalized-event.json"),
        )

    # ---- convenience lookups -------------------------------------------------
    def tier(self, action: str) -> dict:
        for t in self.escalation["tiers"]:
            if t["action"] == action:
                return t
        raise KeyError(action)

    def tiers_by_order(self) -> list[dict]:
        return sorted(self.escalation["tiers"], key=lambda t: t["order"])

    def max_attempts_for_class(self, decline_class: str) -> int:
        for rule in self.decline_policy.get("rules", []):
            if rule.get("when", {}).get("decline_class") == decline_class and "max_attempts" in rule:
                return int(rule["max_attempts"])
        return DEFAULT_MAX_ATTEMPTS

    def horizon_days(self) -> int:
        return int(self.profile.get("recovery_horizon_days", self.escalation["meta"].get("horizon_days", 21)))

    def prior(self, decline_class: str, action: str, attempt_index: int) -> tuple[float, str]:
        priors = self.escalation["priors"]
        arr = priors.get(decline_class, {}).get(action)
        if not arr:
            p = float(self.escalation["defaults"]["prior_when_missing"])
            return p, f"default prior_when_missing (validated:false)"
        idx = min(max(attempt_index, 0), len(arr) - 1)
        return float(arr[idx]), f"prior:{decline_class}.{action}[{idx}] (validated:false)"

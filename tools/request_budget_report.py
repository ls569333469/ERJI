"""Print a secret-free snapshot of active cross-process HTTP budgets."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = ROOT.parents[2]
sys.path.insert(0, str(ROOT))

from engine.config import Settings

settings = Settings(_env_file=SHARED_ROOT / ".env")
os.environ["ERJI_DATABASE_URL"] = settings.database_url

from engine.adapters.base import now_ms
from engine.db import Session
from engine.models import UserPreference
from engine.pd2.request_budget import budget_spec
from sqlalchemy import select


def main():
    now = now_ms()
    result = []
    with Session() as session:
        rows = session.scalars(
            select(UserPreference).where(UserPreference.key.like("http_budget:%"))
        ).all()
    for row in rows:
        host = row.key.removeprefix("http_budget:")
        payload = row.payload or {}
        entries = [entry for entry in payload.get("entries", []) if entry[0] > now - 60_000]
        _, _, default_cap, default_gap = budget_spec(f"https://{host}/", {})
        cap = payload.get("budget_cap", default_cap)
        gap = payload.get("minimum_gap_ms", default_gap)
        result.append(
            {
                "host": host,
                "requests_1m": len(entries),
                "weight_1m": sum(entry[1] for entry in entries),
                "budget_cap": cap,
                "minimum_gap_ms": gap,
                "reported_weight_1m": payload.get("reported_used_weight_1m"),
                "blocked_for_seconds": max(
                    0, round((payload.get("blocked_until", 0) - now) / 1000, 1)
                ),
            }
        )
    print(json.dumps(sorted(result, key=lambda item: item["host"]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

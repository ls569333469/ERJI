"""Monotonic observations attached to immutable decision snapshots."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation


def advance(previous, opportunity, phase, protection, observed_at, price_fresh):
    old = previous.get("tracking") or {}
    tracking = deepcopy(old)
    tracking.setdefault("target_first_observed_at", {})
    tracking.setdefault("protection_history", [])

    if price_fresh:
        reached = opportunity.get("target_reached") or {}
        for name in ("t1", "daily", "weekly"):
            if reached.get(name) and name not in tracking["target_first_observed_at"]:
                # A prior snapshot may already have reached it; its exact first time is unknown.
                was_reached = (previous.get("opportunity_plan") or {}).get("target_reached") or {}
                if not was_reached.get(name):
                    tracking["target_first_observed_at"][name] = observed_at

    if phase == "invalidated" and "first_invalidation_observed_at" not in tracking:
        if previous.get("phase") != "invalidated":
            tracking["first_invalidation_observed_at"] = observed_at
    if phase == "ended" and previous.get("phase") != "ended":
        tracking.setdefault("first_ended_observed_at", observed_at)

    try:
        current = Decimal(str(protection))
        last = Decimal(str(tracking["protection_history"][-1]["price"])) if tracking["protection_history"] else None
    except (InvalidOperation, TypeError, ValueError):
        current = last = None
    if current is not None and current.is_finite() and current > 0 and (last is None or current > last):
        tracking["protection_history"].append({"price": str(current), "observed_at": observed_at})
    return tracking

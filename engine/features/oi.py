from decimal import Decimal

def quantity_change(start: Decimal | None, end: Decimal | None, start_set: set[str], end_set: set[str]):
    if start_set != end_set:
        return None, "coverage_changed"
    if start is None or end is None or start <= 0:
        return None, "insufficient_history"
    return (end / start - 1) * 100, "fresh"

def nominal_decomposition(q0: Decimal, q1: Decimal, p0: Decimal, p1: Decimal):
    return (q1-q0)*p0, q1*(p1-p0)

def funding_window(records: list[dict], start_exclusive: int, end_inclusive: int):
    settled = [r for r in records if r.get("kind") == "settled" and start_exclusive < r["time"] <= end_inclusive]
    rates = [Decimal(r["rate"]) for r in settled]
    return {"count":len(rates), "settled_sum":sum(rates, Decimal(0)), "short_net_cost":-sum(rates, Decimal(0))}


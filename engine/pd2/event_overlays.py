"""P33 event-owned auxiliaries; no new entry signals or exchange requests."""
from decimal import Decimal as D, InvalidOperation
from engine.pd2.analysis import closed_bars, DURATIONS

VERSION = "event-overlays-v2"


def pivots(rows, key):
    """Two bars on each side; first equal extreme owns a plateau."""
    result = []
    for i in range(2, len(rows)-2):
        sample = rows[i-2:i+3]
        values = [D(b[key]) for b in sample]
        extreme = (max if key == "high" else min)(values)
        if values[2] != extreme or extreme in values[:2]:
            continue
        result.append({"price": str(extreme), "pivot_time": rows[i]["open_time"],
                       "known_at": max(max(b["close_time"], b.get("received_at", 0)) for b in sample),
                       "index": i, "kind": key})
    return result


def event_avwap(rows, anchor, resolution, now):
    duration = DURATIONS[resolution]
    segment = [b for b in closed_bars(rows, now) if b["open_time"] >= anchor]
    result = {"version": VERSION, "anchor_time": anchor, "timeframe": resolution,
              "method": "HLC3_base_volume", "status": "missing_anchor", "series": []}
    if not segment or segment[0]["open_time"] != anchor:
        return result
    total = weighted = D(0)
    failed = False
    for i, bar in enumerate(segment):
        try:
            volume = D(str(bar.get("base_volume")))
            typical = (D(bar["high"])+D(bar["low"])+D(bar["close"]))/3
            valid = volume.is_finite() and volume >= 0 and typical.is_finite()
        except (InvalidOperation, TypeError, ValueError):
            valid = False
        if not valid or i and bar["open_time"] != segment[i-1]["open_time"]+duration:
            failed = True
        if not failed:
            total += volume
            weighted += typical*volume
        result["series"].append({"time": bar["open_time"], "known_at": max(bar["close_time"], bar.get("received_at", 0)),
            "event_avwap": str(weighted/total) if not failed and total > 0 else None,
            "support_eligible": i > 0 and not failed and total > 0})
    result["status"] = "missing_volume_or_interval" if failed else "available" if total > 0 else "zero_volume"
    return result


def event_fibonacci(rows, trigger_close, now, frozen=None):
    rows = closed_bars(rows, now)
    if not rows or any(a['close_time']!=b['open_time'] for a,b in zip(rows,rows[1:])):
        return {'version':VERSION,'status':'source_gap','anchors':[],'levels':[]}
    before = [b for b in rows if max(b["close_time"], b.get("received_at", 0)) <= trigger_close]
    highs, lows = pivots(before, "high"), pivots(before, "low")
    points = sorted(highs+lows, key=lambda p: p["pivot_time"])
    pairs = [(a, b) for a, b in zip(points, points[1:])
             if a["kind"] == "low" and b["kind"] == "high" and D(a["price"]) < D(b["price"])]
    result = {"version": VERSION, "status": "missing_anchors", "anchors": [], "levels": []}
    if frozen is not None:
        stored = frozen.get("anchors") or []
        if any(p.get('known_at',now+1)>now for p in stored):
            return result
        pairs = [(stored[0], stored[1])] if len(stored) >= 2 else []
    if not pairs:
        return result
    a, b = pairs[-1]
    av, bv = D(a["price"]), D(b["price"])
    result.update(status="retracement", anchors=[a, b])
    result["levels"] = [{"ratio": str(r), "price": str(bv-r*(bv-av)), "kind": "retracement",
                         "known_at": trigger_close, "formula": "B-r*(B-A)"}
                        for r in (D(".382"), D(".5"), D(".618"))]
    # First confirmed qualifying post-event low, never replaced after it fails.
    candidates = [p for p in pivots(rows, "low") if p["pivot_time"] >= trigger_close and av < D(p["price"]) < bv]
    if frozen and len(frozen.get("anchors", [])) >= 3:
        candidates = [frozen["anchors"][2]]
    if not candidates:
        return result
    c = candidates[0]
    result["anchors"].append(c)
    after = [bar for bar in rows if bar["close_time"] > c["known_at"]]
    trigger = next((bar for bar in after if D(bar["close"]) > bv), None)
    invalid = next((bar for bar in rows if bar["open_time"] > c["pivot_time"] and D(bar["low"]) <= av), None)
    if invalid:
        result["status"] = "invalidated"
        return result
    if trigger:
        known = max(trigger["close_time"], trigger.get("received_at", 0))
        result["status"] = "extension"
        result["levels"] += [{"ratio": str(k), "price": str(D(c["price"])+k*(bv-av)),
            "kind": "conditional_extension", "known_at": known, "formula": "C+k*(B-A)"}
            for k in (D(1), D("1.618"))]
    return result


def attach_event_overlays(assessments, bars, evidence, now, frozen=None):
    """Only attach to the actual triggering timeframe; no cross-timeframe invention."""
    trigger = evidence.get("trigger") or {}
    tf = trigger.get("resolution")
    if tf not in DURATIONS or trigger.get("open_time") is None or trigger.get("close_time", now+1) > now:
        return {}
    rows = bars.get(tf, [])
    avwap = event_avwap(rows, trigger["open_time"], tf, now)
    fib = event_fibonacci(rows, trigger["close_time"], now, (frozen or {}).get("fibonacci"))
    by_time = {p["time"]: p["event_avwap"] for p in avwap["series"]}
    for assessment in assessments:
        if assessment["timeframe"] == tf:
            for point in assessment.get("series", []):
                point["event_avwap"] = by_time.get(point["time"])
            assessment["event_avwap"] = {k: v for k, v in avwap.items() if k != "series"}
    return {"avwap": {k: v for k, v in avwap.items() if k != "series"},
            "fibonacci": {**fib, "timeframe": tf}}

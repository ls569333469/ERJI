"""Drawing facts frozen with a decision snapshot, never inferred by the chart."""

from decimal import Decimal, InvalidOperation

from engine.strategies.structures import VERSION as BOX_VERSION


BOX_STRATEGIES = {"confirmed_breakout", "early_breakout", "box_breakout", "trend_restart"}
ACTIVE_ACTIONS = {"participate", "trial"}


def candidate_contract(event_id, market_id, evidence, phase, as_of, quality=None):
    """Only recorded candidate boundaries; never synthesize a confirmed trigger."""
    from hashlib import sha256
    import json
    if evidence.get('price_pattern'):
        from engine.strategies.p35_candidates import candidate_drawing
        return candidate_drawing(event_id,market_id,evidence,phase,as_of)
    if phase!='structure_pending' or evidence.get('trigger') or evidence.get('instrument_id')!=market_id:return None
    if not quality or quality.get('status')!='verified':return None
    box=evidence.get('box') or {}
    upper,lower,boundary=(_price(box.get(k)) for k in ('upper','lower','trigger_price'))
    start,finish=_time(box.get('start_time')),_time(box.get('end_time'))
    if not all((upper,lower,boundary,start,finish,_time(as_of))) or not lower<upper<boundary or not start<finish<=as_of:return None
    if box.get('rule_version')!=BOX_VERSION:return None
    revision=sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()
    return {'snapshot_id':None,'market_id':market_id,'event_id':event_id,'series_revision':revision,
        'strategy_id':'box_breakout','strategy_version':BOX_VERSION,'timeframe':'240','as_of':as_of,
        'closed_at':finish,'coverage_start':start,'coverage_end':finish,'status':'candidate',
        'required_indicators':[],'entry_zone':None,'protection':None,'first_target':None,'protection_history':[],
        'anchors':[{'kind':'box','start_time':start,'end_time':finish,'last_open_time':quality['last_open_time'],'upper':box['upper'],'lower':box['lower'],'known_at':finish},
                   {'kind':'trigger_boundary','price':box['trigger_price'],'known_at':finish}]}


def _price(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number > 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _time(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def drawing_contract(snapshot):
    evidence = snapshot.get("evidence") or {}
    box = evidence.get("box") or {}
    strategy_id = snapshot.get("strategy_id") or snapshot.get("template") or evidence.get("template")
    if strategy_id is None and evidence.get("box"):
        strategy_id = "confirmed_breakout" if evidence.get("confirmation") else "early_breakout"
    event_id = snapshot.get("event_id") or snapshot.get("episode_id")
    trigger = evidence.get("trigger") or {}
    contract = {
        "snapshot_id": snapshot.get("id"),
        "market_id": snapshot.get("instrument_id"),
        "timeframe": "240" if strategy_id in BOX_STRATEGIES else None,
        "strategy_id": strategy_id,
        "strategy_version": snapshot.get("strategy_version") or box.get("rule_version") or snapshot.get("rule_version"),
        "event_id": event_id,
        "series_revision": snapshot.get("series_revision"),
        "as_of": snapshot.get("as_of"),
        "closed_at": trigger.get("close_time"),
        "coverage_start": box.get("start_time"),
        "coverage_end": box.get("end_time"),
        "price_unit": (snapshot.get("market") or {}).get("quote_asset"),
        "status": "missing_anchors",
        "anchors": [],
        "entry_zone": None,
        "protection": None,
        "protection_history": [],
        "first_target": None,
        "required_indicators": [],
    }
    if strategy_id not in BOX_STRATEGIES:
        # New templates supply their own typed, versioned anchors. Never borrow a box.
        from engine.strategies.p33 import VERSION, NAMES
        pattern = snapshot.get("price_pattern") or {}
        if strategy_id in NAMES and pattern.get("strategy_id") == strategy_id and pattern.get("strategy_version") == VERSION:
            if pattern.get("market_id") != contract["market_id"] or pattern.get("event_id") != event_id or pattern.get("status") != "triggered":
                return contract
            anchors = pattern.get("anchors") or []
            as_of = _time(snapshot.get("as_of"))
            if not as_of or not anchors:
                return contract
            for anchor in anchors:
                known = _time(anchor.get("known_at"))
                at = _time(anchor.get("time") or anchor.get("start_time") or anchor.get("open_time"))
                if anchor.get("kind") not in ("boundary", "pivot", "trigger_bar") or not _price(anchor.get("price")) or not known or not at or not at <= known <= as_of:
                    return contract
                if anchor["kind"] == "trigger_bar" and not (at < (anchor.get("close_time") or 0) <= known):
                    return contract
            contract.update(status="confirmed", anchors=anchors+([{'kind':'trigger_boundary','price':pattern['threshold'],'known_at':pattern['trigger']['close_time']} ] if _price(pattern.get('threshold')) else []), strategy_version=VERSION,
                            timeframe=pattern.get("source_timeframe") or pattern["timeframe"],
                            closed_at=pattern["trigger"]["close_time"],
                            coverage_start=min(a.get("time") or a.get("start_time") or a.get("open_time") for a in anchors),
                            coverage_end=pattern["trigger"]["close_time"],
                            required_indicators=pattern.get("required_indicators", []))
        return _plan_layers(contract, snapshot, trigger) if contract["status"] == "confirmed" else contract
    upper, lower, boundary = (_price(box.get(key)) for key in ("upper", "lower", "trigger_price"))
    start, finish = _time(box.get("start_time")), _time(box.get("end_time"))
    opened, closed = _time(trigger.get("open_time")), _time(trigger.get("close_time"))
    as_of = _time(snapshot.get("as_of"))
    if not all((upper, lower, boundary, start, finish, opened, closed, as_of)):
        return contract
    if not (lower < upper < boundary and start < finish <= opened < closed <= as_of):
        return contract
    if box.get("rule_version", BOX_VERSION) != BOX_VERSION:
        return contract
    allowed_resolutions = {"60"} if strategy_id == "early_breakout" else {"60", "240"} if strategy_id == "trend_restart" else {"240"}
    if _price(trigger.get("close")) is None or trigger.get("resolution") not in allowed_resolutions:
        return contract
    contract["strategy_version"] = BOX_VERSION
    contract["status"] = "confirmed"
    contract["anchors"] = [
        {"kind": "box", "start_time": box["start_time"], "end_time": box["end_time"],
         "last_open_time": max(box["start_time"],box["end_time"]-14400000),
         "upper": box["upper"], "lower": box["lower"], "known_at": box["end_time"]},
        {"kind": "trigger_boundary", "price": box["trigger_price"], "known_at": box["end_time"]},
        {"kind": "trigger_bar", "open_time": trigger["open_time"], "close_time": trigger["close_time"],
         "price": trigger["close"], "timeframe": trigger["resolution"], "known_at": trigger["close_time"]},
    ]
    return _plan_layers(contract, snapshot, trigger)


def _plan_layers(contract, snapshot, trigger):
    evidence = snapshot.get("evidence") or {}
    as_of = snapshot["as_of"]
    closed = trigger["close_time"]
    # Each step starts when the system observed the adjustment, never at the
    # original trigger. A malformed history cannot be stitched into a fake path.
    history = (snapshot.get("tracking") or {}).get("protection_history") or []
    previous_at = closed - 1
    valid_history = []
    for point in history:
        observed = _time(point.get("observed_at"))
        if not _price(point.get("price")) or not observed or not previous_at < observed <= as_of or (valid_history and _price(point['price'])<=_price(valid_history[-1]['price'])):
            valid_history = []
            break
        valid_history.append({"price": point["price"], "observed_at": observed})
        previous_at = observed
    contract["protection_history"] = valid_history
    action = (snapshot.get("action") or {}).get("code")
    valid_until = _time(snapshot.get("valid_until"))
    if action not in ACTIVE_ACTIONS or valid_until is None or as_of > valid_until:
        return contract
    entry = evidence.get("entry_zone")
    protection = snapshot.get("current_protection")
    target = (snapshot.get("opportunity_plan") or {}).get("t1")
    entry_low, entry_high = (_price(value) for value in entry) if isinstance(entry, (list, tuple)) and len(entry) == 2 else (None, None)
    stop = _price(protection)
    if entry_low and entry_high and stop and stop < entry_low <= entry_high:
        contract["entry_zone"] = {"low": entry[0], "high": entry[1], "known_at": trigger["close_time"],
                                  "valid_from": max(trigger["close_time"], snapshot.get("first_seen_at", trigger["close_time"])), "valid_to": snapshot.get("valid_until"),
                                  "method": "frozen_plan_entry_zone", "timeframe": trigger["resolution"]}
        protection_known = valid_history[-1]["observed_at"] if valid_history and _price(valid_history[-1]["price"]) == stop else as_of
        contract["protection"] = {"price": protection, "known_at": protection_known,
                                  "valid_from": protection_known, "method": "current_plan_protection"}
    if contract["entry_zone"] and isinstance(target, dict):
        target_low = _price(target.get("low"))
        target_high = _price(target.get("high")) if target.get("high") is not None else target_low
        known_at = _time(target.get("known_at"))
        if target_low and target_high and known_at and known_at <= as_of and entry_high < target_low <= target_high:
            contract["first_target"] = {"low": target["low"], "high": target.get("high") or target["low"],
                                        "known_at": known_at, "timeframe": target.get("timeframe"),
                                        "method": target.get("method")}
    return contract

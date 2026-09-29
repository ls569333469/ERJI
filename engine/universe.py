"""Deterministic universe selection; exchange units are never guessed from tickers."""
from decimal import Decimal, InvalidOperation

STABLE_BASES = {"USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "BUSD"}

def select_binance_universe(specs, tickers, limit):
    volumes = {}
    for row in tickers:
        try:
            volume = Decimal(str(row.get("quoteVolume", "0")))
            if volume.is_finite() and volume > 0:
                volumes[row["symbol"]] = volume
        except (InvalidOperation, KeyError):
            continue
    candidates = [s for s in specs if s.get("status") == "TRADING"
                  and s.get("contractType") == "PERPETUAL"
                  and s.get("quoteAsset") in ("USDT", "USDC")
                  and s.get("underlyingType", "COIN") == "COIN"
                  and s.get("baseAsset") not in STABLE_BASES
                  and s.get("symbolType",0)==0
                  and not ({"STOCK","COMMODITY","FOREX","INDEX"}&set(s.get("underlyingSubType",[])))]
    candidates.sort(key=lambda s: (-volumes.get(s["symbol"],Decimal(0)), s["symbol"]))
    # Asset budget chooses bases; retain all eligible quote contracts for their OI.
    bases=list(dict.fromkeys(s['baseAsset'] for s in candidates))
    allowed=set(bases[:limit])
    selected=list({s['symbol']:s for s in candidates if s['baseAsset'] in allowed}.values())
    for spec in selected:volumes.setdefault(spec['symbol'],Decimal(0))
    return selected,len(bases),volumes


def select_deep_ids(specs, limit, reserved_assets):
    """Reserve the already mapped DEX assets inside the shared asset budget."""
    assets=set(reserved_assets)
    selected=[]
    for spec in specs:
        base=spec["baseAsset"]
        if base in assets or len(assets)<limit:
            selected.append("binance:perp:"+spec["symbol"])
            assets.add(base)
    return selected

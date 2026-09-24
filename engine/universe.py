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
                  and s.get("quoteAsset") == "USDT"
                  and s.get("underlyingType", "COIN") == "COIN"
                  and s.get("baseAsset") not in STABLE_BASES
                  and s["symbol"] in volumes]
    candidates.sort(key=lambda s: (-volumes[s["symbol"]], s["symbol"]))
    # Multiple contracts on the same native base cannot consume two asset slots.
    selected, seen = [], set()
    for spec in candidates:
        if spec["baseAsset"] in seen:
            continue
        seen.add(spec["baseAsset"])
        selected.append(spec)
    return selected[:limit], len(selected), volumes

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

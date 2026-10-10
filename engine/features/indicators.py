import numpy as np
import talib

def closed_indicators(candles: list[dict]):
    bars = sorted((c for c in candles if c["is_closed"]), key=lambda c:c["open_time"])
    if len(bars) < 15:
        return {"atr14": None, "ema20": None, "quality": "insufficient_history"}
    h,l,c = (np.array([float(b[k]) for b in bars], dtype="float64") for k in ("high","low","close"))
    def output(v):
        return None if np.isnan(v) else format(float(v), ".15g")
    return {"atr14":output(talib.ATR(h,l,c,14)[-1]), "ema20":output(talib.EMA(c,20)[-1]), "quality":"fresh", "bar_close_time":bars[-1]["close_time"]}


"""Pure numeric primitives. Callers explicitly choose the window and baseline."""
from decimal import Decimal as D
from statistics import median

def change(a,b):
    return float((D(str(a))/D(str(b))-1)*100) if a is not None and b and D(str(b))>0 else None

def relative_volume(current,previous,count,method='mean'):
    values=list(previous)[-count:]
    if current is None or len(values)!=count or any(v is None for v in values):return None
    values=[D(str(v)) for v in values]
    baseline=median(values) if method=='median' else sum(values)/count
    return D(str(current))/baseline if baseline>0 else None

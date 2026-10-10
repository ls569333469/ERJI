"""Bounded windows keyed by native instrument and resolution; millisecond facts."""
from collections import OrderedDict
import threading

class CandleWindow:
    def __init__(self, capacity):
        if capacity<1:raise ValueError('positive_window_required')
        self.capacity=capacity;self.rows={}
    def merge(self,rows):
        for row in rows:
            key=int(row['open_time']);old=self.rows.get(key)
            if old and (old['is_closed'] and not row['is_closed'] or old['is_closed']==row['is_closed'] and old.get('received_at',0)>row.get('received_at',0)):
                continue
            self.rows[key]=dict(row)
        self.rows=dict(sorted(self.rows.items())[-self.capacity:])
    def missing(self,expected_close,step,required,onboard=0):
        start=expected_close-required*step
        if onboard:start=max(start,((onboard+step-1)//step)*step)
        for at in range(start,expected_close,step):
            row=self.rows.get(at)
            if not row or not row['is_closed'] or row['close_time']!=at+step:return at
        return None
    def ready(self,expected_close,step,required):
        return self.missing(expected_close,step,required) is None

class Windows:
    def __init__(self,max_windows=4096):self.items=OrderedDict();self.max_windows=max_windows;self.lock=threading.RLock()
    def update(self,key,rows,capacity=300):
        with self.lock:
            return self._update(key,rows,capacity)
    def _update(self,key,rows,capacity):
        window=self.items.pop(key,None)
        if window is None or window.capacity!=capacity:window=CandleWindow(capacity)
        window.merge(rows);self.items[key]=window
        while len(self.items)>self.max_windows:self.items.popitem(last=False)
        return window

windows=Windows()

"""Spot REST has no exchange clock. Never label receive time as source time."""
def effective_time(book):
    if book.get('source_time_kind')=='snapshot_received':
        if book.get('venue')!='binance_spot' or not isinstance(book.get('last_update_id'),int):return None
        latency=book.get('roundtrip_ms')
        if not isinstance(latency,(int,float)) or not 0<=latency<=1000:return None
        return book.get('received_at')
    return book.get('observed_at')

def fresh(book,now,max_age=90000):
    if not book:return False
    at=effective_time(book);received=book.get('received_at')
    return bool(at is not None and received is not None and 0<=now-at<=max_age and 0<=now-received<=max_age)

"""Shared native-market representative; never merge another market's facts."""
def representative_key(row):
    return (not row.payload.get('pinned'),row.payload.get('trade_status')!='active',-row.last_at,-(row.visible_at or 0),row.id)

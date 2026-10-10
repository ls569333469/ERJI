"""Current engineering clocks; strategy windows and historical facts are unchanged."""
import asyncio
import hashlib
import threading

VERSION = 'p39-priority-capacity-v1'
ANALYSIS_SLOTS = {'protect':1,'entry':1,'projection':1,'trend':2,'structure':2}
ORDINARY_MS = 300000
HOT_MS = 60000
T3_MS = 30000
REFERENCE_MS = 600000
OI_TRIGGER_MS = 90000
QUOTE_MS = 90000
BOOK_MS = 90000
HEARTBEAT_MS = 60000
ROLE_MAX_AGE_MS = 180000
REVIEW_MS = 600000
WAIT_REVIEW_MS = 120000
HISTORY_MS = 1800000
COLLECTION_LEASE_MS = 120000
COLLECTION_RENEW_SECONDS = 30


def cycle(at):
    return at // ORDINARY_MS * ORDINARY_MS


def phase(native):
    # Stable per-contract phases do not change when ranking or tiers change.
    return int.from_bytes(hashlib.sha256(native.encode()).digest()[:4], 'big') % 240 * 1000


def scheduled_at(native, at, interval):
    offset = phase(native) if interval == ORDINARY_MS else phase(native) % interval
    return (at-offset)//interval*interval+offset


def due_at(native, visited, at, interval):
    return not visited or visited < scheduled_at(native, at, interval)


def current_execution(config):
    """Override current engineering TTLs without editing frozen strategy evidence."""
    from engine.pd2.config import ExecutionConfig
    value = config if isinstance(config, ExecutionConfig) else ExecutionConfig.model_validate(config or {})
    return value.model_copy(update={'quote_max_age_seconds': QUOTE_MS//1000,
                                   'depth_max_age_seconds': BOOK_MS//1000})


class Wakeup:
    """Coalescing local notification, with a timeout for cross-process recovery."""
    def __init__(self):
        self.loop = None
        self.event = None
        self.pending = False
        self.lock = threading.Lock()

    def notify(self):
        with self.lock:
            self.pending = True
            if self.loop and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self.event.set)

    async def wait(self, seconds):
        loop = asyncio.get_running_loop()
        with self.lock:
            if self.loop is not loop:
                self.loop, self.event = loop, asyncio.Event()
            if self.pending:
                self.pending = False
                self.event.clear()
                return
        try:
            await asyncio.wait_for(self.event.wait(), seconds)
        except asyncio.TimeoutError:
            pass
        with self.lock:
            self.pending = False
            self.event.clear()


analysis_wakeup = Wakeup()
jobs_wakeup = Wakeup()

"""One writer for the hosted state, across containers.

The hosted API keeps SQLite (jobs, reviews, the US$25 ledger) on a shared volume. That is safe
for one writer only. During a redeploy, or after a crash, an old and a new API container can run
at the same time, and the per-container `worker.lock` does not reach across them. Each container
calls `run()`. Only the holder of this lease commits writes or runs jobs. The other one serves
reads and refuses writes until the holder stops or its heartbeat lapses.

`store` is any mapping with `get`, `put(key, value, skip_if_exists=...)` and `pop`, such as a
`modal.Dict`.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("ymir.lease")


class WriterLease:
    def __init__(self, store, owner, *, ttl=60.0, beat=10.0, wait=2.0, key="writer", clock=time.time):
        self.store, self.owner, self.key, self.clock = store, owner, key, clock
        self.ttl, self.beat, self.wait = ttl, beat, wait
        self.held = threading.Event()   # this container owns the lease
        self.ready = threading.Event()  # ...and has finished taking over: writes may commit

    def _record(self):
        return {"owner": self.owner, "at": self.clock()}

    def try_acquire(self) -> bool:
        if self.store.put(self.key, self._record(), skip_if_exists=True):
            self.held.set()
            return True
        current = self.store.get(self.key)
        if current is None:
            return False  # released between the two calls; try again next round
        if current.get("owner") == self.owner:
            self.store.put(self.key, self._record())
            self.held.set()
            return True
        if self.clock() - float(current.get("at", 0)) > self.ttl:
            # The previous holder stopped without releasing. Take over only if nobody else did.
            self.store.pop(self.key, None)
            if self.store.put(self.key, self._record(), skip_if_exists=True):
                self.held.set()
                return True
        return False

    def renew(self) -> bool:
        current = self.store.get(self.key)
        if current is not None and current.get("owner") != self.owner:
            self.ready.clear()
            self.held.clear()
            return False
        self.store.put(self.key, self._record())
        return True

    def release(self):
        # Stop writing first, then free the lease for the next container.
        self.ready.clear()
        self.held.clear()
        try:
            current = self.store.get(self.key)
            if current is not None and current.get("owner") == self.owner:
                self.store.pop(self.key, None)
        except Exception:
            log.warning("Could not release the writer lease; it lapses after %ss", self.ttl, exc_info=True)

    def run(self, on_acquire, on_lose, stop: threading.Event):
        """Acquire, then renew until `stop`. `on_acquire` must succeed before writes are allowed."""
        while not stop.is_set():
            try:
                if self.held.is_set():
                    if not self.renew():
                        log.error("Writer lease taken by another container; this container stops writing")
                        on_lose()
                elif self.try_acquire():
                    try:
                        on_acquire()
                        self.ready.set()
                    except Exception:
                        log.exception("Writer start-up failed; releasing the lease and retrying")
                        self.release()
            except Exception:
                log.warning("Writer lease check failed", exc_info=True)
            stop.wait(self.beat if self.held.is_set() else self.wait)

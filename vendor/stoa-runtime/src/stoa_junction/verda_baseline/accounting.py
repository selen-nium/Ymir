"""Separate atomic US$20 ledger, persistent once-only claims and rate pacing."""
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
import sqlite3
import threading
import time

from .common import now

SCALE = 1_000_000_000


class BudgetStop(RuntimeError):
    pass


def units(value):
    d = Decimal(str(value))
    if not d.is_finite() or d < 0 or isinstance(value, bool):
        raise ValueError('Cost must be finite and nonnegative')
    return int((d * SCALE).to_integral_value(rounding=ROUND_CEILING))


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            db = sqlite3.connect(self.path, timeout=30)
            try:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                db.close()
            if {'calls', 'attempts', 'events', 'pacing', 'starts', 'pace_tickets'} <= tables:
                return
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, reserved INTEGER NOT NULL, actual INTEGER, state TEXT NOT NULL, at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, state TEXT NOT NULL, at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS pacing (id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL NOT NULL)')
            db.execute('INSERT OR IGNORE INTO pacing VALUES (1, 0)')
            db.execute('CREATE TABLE IF NOT EXISTS starts (at REAL NOT NULL, tokens INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS pace_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, tokens INTEGER NOT NULL)')

    @contextmanager
    def db(self):
        with self._lock:
            db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def event(self, db, kind, obj):
        db.execute('INSERT INTO events(at,kind,detail) VALUES (?,?,?)', (now(), kind, json.dumps(obj)))

    def claim(self, ident):
        with self.db() as db:
            cur = db.execute('INSERT OR IGNORE INTO attempts VALUES (?, ?, ?)', (ident, 'claimed', now()))
            if cur.rowcount:
                self.event(db, 'claim', {'id': ident})
            return bool(cur.rowcount)

    def finish(self, ident, state):
        with self.db() as db:
            db.execute('UPDATE attempts SET state=? WHERE id=?', (state, ident))
            self.event(db, 'finish', {'id': ident, 'state': state})

    def reserve(self, key, cost):
        amount = units(cost)
        if amount <= 0:
            raise ValueError('Reservation must be positive')
        with self.db() as db:
            used = db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM calls').fetchone()[0]
            if db.execute('SELECT 1 FROM calls WHERE actual>reserved').fetchone() or used + amount > 20 * SCALE:
                raise BudgetStop('US$20 cap: insufficient balance or a prior bound was exceeded')
            db.execute('INSERT INTO calls VALUES (?, ?, NULL, ?, ?)', (key, amount, 'reserved', now()))
            self.event(db, 'reserve', {'key': key, 'units': amount})

    def settle(self, key, cost):
        amount = None if cost is None else units(cost)
        with self.db() as db:
            prior = db.execute('SELECT reserved,actual FROM calls WHERE key=?', (key,)).fetchone()
            if prior is None:
                raise ValueError('Missing reservation')
            if prior[1] is not None and prior[1] != amount:
                raise ValueError('Settled charge is immutable')
            db.execute('UPDATE calls SET actual=?,state=? WHERE key=?', (amount, 'unknown' if amount is None else 'settled', key))
            self.event(db, 'settle', {'key': key, 'units': amount})
        if amount is not None and amount > prior[0]:
            raise BudgetStop('Actual cost exceeded conservative reservation; dispatch stopped')

    def pace(self, rpm, tpm, tokens, deadline):
        # Reserve a conservative weight shared across threads and restarts.
        # Known returned usage may settle that weight; unknown usage retains it.
        if tokens > tpm:
            raise ValueError('A request bound exceeds the frozen provider TPM limit')
        while True:
            with self.db() as db:
                wall = time.time()
                db.execute('DELETE FROM pace_tickets WHERE at<=?', (wall - 60,))
                starts = db.execute('SELECT at,tokens FROM pace_tickets ORDER BY at').fetchall()
                next_at = db.execute('SELECT next_at FROM pacing WHERE id=1').fetchone()[0]
                wait = max(0, next_at - wall)
                if len(starts) >= rpm or sum(t for _, t in starts) + tokens > tpm:
                    wait = max(wait, starts[0][0] + 60 - wall)
                if not wait:
                    cur = db.execute('INSERT INTO pace_tickets(at,tokens) VALUES (?,?)', (wall, tokens))
                    return cur.lastrowid
            if time.monotonic() + wait >= deadline:
                raise TimeoutError('Trajectory deadline during provider pacing')
            time.sleep(min(wait, 1))

    def defer(self, seconds):
        with self.db() as db:
            db.execute('UPDATE pacing SET next_at=MAX(next_at,?) WHERE id=1', (time.time() + seconds,))

    def settle_pacing(self, ident, actual_tokens):
        if type(actual_tokens) is not int or actual_tokens < 0:
            raise ValueError('Pacing usage must be a nonnegative integer')
        with self.db() as db:
            # Missing rows have already expired from the rolling minute. Unknown
            # usage never calls this method and retains the full initial weight.
            db.execute('UPDATE pace_tickets SET tokens=? WHERE id=?', (actual_tokens, ident))

    def snapshot(self):
        with self.db() as db:
            calls = [{'key': k, 'reserved_usd': r / SCALE, 'actual_usd': None if a is None else a / SCALE,
                      'state': s, 'at': at} for k, r, a, s, at in db.execute('SELECT * FROM calls ORDER BY at,key')]
            attempts = {i: s for i, s in db.execute('SELECT id,state FROM attempts')}
        spent = sum(c['actual_usd'] or 0 for c in calls)
        held = sum(c['reserved_usd'] for c in calls if c['actual_usd'] is None)
        return {'cap_usd': 20, 'spent_usd': spent, 'held_usd': held,
                'remaining_usd': max(0, 20 - spent - held), 'calls': calls, 'attempts': attempts}


def cost(provider, prompt_tokens, completion_tokens):
    # Charge all prompt tokens at the higher cached/uncached rate. Reasoning is
    # already included in completion_tokens and must never be subtracted.
    incoming = max(Decimal(str(provider['input_usd_per_million'])),
                   Decimal(str(provider.get('cached_input_usd_per_million', 0))))
    return (incoming * prompt_tokens + Decimal(str(provider['output_usd_per_million'])) * completion_tokens) / 1_000_000

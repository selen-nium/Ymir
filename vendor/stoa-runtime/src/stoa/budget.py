"""Persistent reservations: a timeout retains its full reservation, never a free retry."""
import sqlite3
import time
import uuid
from pathlib import Path

from .config import CAP_USD, check_model

class BudgetExhausted(RuntimeError):
    pass

class Budget:
    def __init__(self, path: Path, cap: float = CAP_USD):
        if not 0 < cap <= CAP_USD:
            raise ValueError("The session budget must be positive and at most US$5.")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.cap = path, cap
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS charges (
                id TEXT PRIMARY KEY, created REAL, model TEXT, case_id TEXT,
                reserved REAL, actual REAL, status TEXT, generation_id TEXT,
                prompt_tokens INTEGER, completion_tokens INTEGER)""")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def reserve(self, model: str, case_id: str, amount: float) -> str:
        check_model(model)
        if amount <= 0:
            raise ValueError("Reservation must be positive")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            committed = db.execute("SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual ELSE reserved END),0) FROM charges").fetchone()[0]
            if committed + amount > self.cap + 1e-10:
                raise BudgetExhausted(f"US$5 budget: ${committed:.4f} committed; next request needs ${amount:.4f} reserved.")
            ident = uuid.uuid4().hex
            db.execute("INSERT INTO charges VALUES (?,?,?,?,?,NULL,'reserved',NULL,NULL,NULL)", (ident, time.time(), model, case_id, amount))
        return ident

    def settle(self, ident: str, actual: float | None, generation_id=None, usage=None):
        usage = usage or {}
        if actual is not None and (actual < 0 or actual != actual):
            raise ValueError("Invalid reported cost")
        with self.connect() as db:
            row = db.execute("SELECT status,reserved FROM charges WHERE id=?", (ident,)).fetchone()
            if row is None or row[0] not in ('reserved', 'unknown'):
                raise ValueError("Unknown or already settled reservation")
            db.execute("UPDATE charges SET actual=?,status=?,generation_id=?,prompt_tokens=?,completion_tokens=? WHERE id=?", (actual, 'settled' if actual is not None else 'unknown', generation_id, usage.get('prompt_tokens'), usage.get('completion_tokens'), ident))
            if actual is not None and actual > row[1] + 1e-8:
                # Preserve the reported charge and stop before making another request.
                raise BudgetExhausted("Provider cost exceeded the conservative reservation; execution stopped.")

    def summary(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute("SELECT * FROM charges ORDER BY created")]
        actual = sum(r['actual'] or 0 for r in rows if r['status'] == 'settled')
        unresolved = sum(r['reserved'] for r in rows if r['status'] != 'settled')
        return {'cap_usd': self.cap, 'actual_usd': actual, 'unresolved_reserved_usd': unresolved,
                'remaining_usd': max(0, self.cap-actual-unresolved), 'requests': len(rows), 'charges': rows}

"""Persistent, independent spending ceilings for the Junction pilot.

Unknown charges remain reserved across restarts. A call key can be used once,
including when it fails, so resuming a run cannot silently issue paid retries.
Amounts are rounded upwards to nano-dollars before accounting.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
from pathlib import Path
import sqlite3
import uuid

CEILINGS = {"modal": 50, "openrouter": 5}
SCALE = 1_000_000_000


class BudgetExhausted(RuntimeError):
    pass


class DuplicateReservation(RuntimeError):
    pass


class BudgetOverrun(RuntimeError):
    pass


def _units(value):
    if isinstance(value, bool):
        raise ValueError("A cost must be a finite, nonnegative number")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError("A cost must be finite and nonnegative")
        return int((amount * SCALE).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, TypeError, OverflowError):
        raise ValueError("Invalid cost") from None


def _now():
    return datetime.now(timezone.utc).isoformat()


class Ledger:
    def __init__(self, path, service, cap_usd=None):
        if service not in CEILINGS:
            raise ValueError("Service must be modal or openrouter")
        cap = _units(CEILINGS[service] if cap_usd is None else cap_usd)
        if not 0 < cap <= _units(CEILINGS[service]):
            raise ValueError("Cap exceeds the user's service-specific authorization")
        self.path, self.service, self.cap_units = Path(path), service, cap
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS pilot_caps (
                    service TEXT PRIMARY KEY, cap_units INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS pilot_reservations (
                    id TEXT PRIMARY KEY, service TEXT NOT NULL, call_key TEXT NOT NULL,
                    reserved_units INTEGER NOT NULL, actual_units INTEGER,
                    state TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, UNIQUE(service, call_key));
                CREATE TABLE IF NOT EXISTS pilot_budget_events (
                    seq INTEGER PRIMARY KEY, reservation_id TEXT, service TEXT NOT NULL,
                    action TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL);
            """)
            # executescript commits its input first; start the cap check atomically.
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT cap_units FROM pilot_caps WHERE service=?", (service,)).fetchone()
            if prior and prior[0] != cap:
                raise ValueError("An existing pilot ledger cap is immutable")
            db.execute("INSERT OR IGNORE INTO pilot_caps VALUES (?,?)", (service, cap))

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def _event(self, db, ident, action, detail):
        db.execute("INSERT INTO pilot_budget_events(reservation_id,service,action,detail,at) VALUES (?,?,?,?,?)",
                   (ident, self.service, action, json.dumps(detail, sort_keys=True), _now()))

    def _rows(self, db):
        return db.execute("SELECT * FROM pilot_reservations WHERE service=? ORDER BY created_at,id", (self.service,)).fetchall()

    def reserve(self, key, amount_usd):
        if not isinstance(key, str) or not key.strip():
            raise ValueError("A stable nonempty call key is required")
        amount = _units(amount_usd)
        if amount <= 0:
            raise ValueError("Reserve a positive worst-case amount")
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM pilot_reservations WHERE service=? AND call_key=?", (self.service, key)).fetchone():
                raise DuplicateReservation(f"Call key already attempted: {key}")
            rows = self._rows(db)
            used = sum(r["actual_units"] if r["actual_units"] is not None else r["reserved_units"] for r in rows)
            # An earlier under-reserved response stops dispatch even if the total
            # is below the cap; this means the reservation bound was invalid.
            if any(r["actual_units"] is not None and r["actual_units"] > r["reserved_units"] for r in rows):
                raise BudgetExhausted("Prior charge exceeded its reservation; reconcile the cost bound before dispatch")
            if used + amount > self.cap_units:
                raise BudgetExhausted(f"{self.service} US${self.cap_units / SCALE:g} cap: insufficient unreserved balance")
            ident, now = uuid.uuid4().hex, _now()
            db.execute("INSERT INTO pilot_reservations VALUES (?,?,?,?,?,?,?,?,?)",
                       (ident, self.service, key, amount, None, "reserved", None, now, now))
            self._event(db, ident, "reserve", {"key": key, "amount_units": amount})
            return ident

    def _reservation(self, db, ident):
        row = db.execute("SELECT * FROM pilot_reservations WHERE id=? AND service=?", (ident, self.service)).fetchone()
        if row is None:
            raise ValueError("Unknown reservation for this service")
        return row

    def settle(self, ident, actual_usd):
        if actual_usd is None:
            self.fail_unknown(ident, "Final billing is unavailable")
            return
        actual = _units(actual_usd)
        overrun = False
        with self._transaction() as db:
            row = self._reservation(db, ident)
            if row["state"] == "settled":
                if row["actual_units"] != actual:
                    raise ValueError("A settled charge cannot be overwritten")
                return
            db.execute("UPDATE pilot_reservations SET state='settled',actual_units=?,updated_at=? WHERE id=?",
                       (actual, _now(), ident))
            self._event(db, ident, "settle", {"actual_units": actual})
            overrun = actual > row["reserved_units"]
        if overrun:
            raise BudgetOverrun("Actual charge recorded, but it exceeded its reservation; further dispatch is stopped")

    def fail_unknown(self, ident, reason):
        with self._transaction() as db:
            row = self._reservation(db, ident)
            if row["state"] == "settled":
                raise ValueError("A settled charge cannot become unknown")
            db.execute("UPDATE pilot_reservations SET state='unknown',reason=?,updated_at=? WHERE id=?",
                       (str(reason), _now(), ident))
            self._event(db, ident, "unknown", {"reason": str(reason)})

    def recover(self):
        """Explicit restart audit, after confirming no old workers are running."""
        with self._transaction() as db:
            rows = db.execute("SELECT id FROM pilot_reservations WHERE service=? AND state='reserved'", (self.service,)).fetchall()
            for row in rows:
                db.execute("UPDATE pilot_reservations SET state='interrupted',reason=?,updated_at=? WHERE id=?",
                           ("Coordinator restarted; billing unresolved", _now(), row["id"]))
                self._event(db, row["id"], "interrupted", {"reservation_retained": True})
            return len(rows)

    def snapshot(self):
        with self._transaction() as db:
            rows = [dict(r) for r in self._rows(db)]
        spent = sum(r["actual_units"] or 0 for r in rows)
        held = sum(r["reserved_units"] for r in rows if r["actual_units"] is None)
        for row in rows:
            row["reserved_usd"] = row.pop("reserved_units") / SCALE
            actual = row.pop("actual_units")
            row["actual_usd"] = actual / SCALE if actual is not None else None
        return {"service": self.service, "cap_usd": self.cap_units / SCALE,
                "spent_usd": spent / SCALE, "reserved_usd": held / SCALE,
                "remaining_usd": max(0, self.cap_units - spent - held) / SCALE,
                "reservations": rows}

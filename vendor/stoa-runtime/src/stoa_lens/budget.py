"""Durable, once-only atomic metered budget for one Lens job collection."""
from __future__ import annotations
import json
import math
import sqlite3
import uuid
from pathlib import Path

MODELS=('deepseek-4-1-flash-1017','kimi-k3-1-pro')

class BudgetError(RuntimeError): pass


def _micros(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError('Invalid dollar amount')
    return math.ceil(value*1_000_000)


class LensBudget:
    def __init__(self,path,cap,checkpoint=None):
        self.checkpoint=checkpoint or (lambda:None)
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True); amount=_micros(cap)
        if amount<=0:raise ValueError('Budget ceiling must be positive')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), cap_micros INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS charges (id TEXT PRIMARY KEY, model TEXT NOT NULL, case_id TEXT NOT NULL, reserved_micros INTEGER NOT NULL, actual_micros INTEGER, status TEXT NOT NULL, generation_id TEXT, usage_json TEXT)')
            row=db.execute('SELECT cap_micros FROM settings WHERE id=1').fetchone()
            if row and row[0]!=amount:raise BudgetError('Existing budget ceiling cannot be changed or reset')
            if not row:db.execute('INSERT INTO settings VALUES (1,?)',(amount,))
            db.commit()

    def _db(self):
        db=sqlite3.connect(self.path,timeout=5,isolation_level=None)
        db.execute('PRAGMA busy_timeout=5000')
        return db

    def reserve(self,model,case_id,amount,key=None):
        if model not in MODELS:raise ValueError('Invalid model reservation')
        return self._reserve(model,case_id,amount,key)

    def reserve_compute(self,case_id,amount,key=None):
        """Explicit Modal validation hold in the same cap; never an LLM price."""
        return self._reserve('modal-compute',case_id,amount,key)

    def _reserve(self,model,case_id,amount,key=None):
        reserved=_micros(amount)
        if not isinstance(case_id,str) or not case_id or reserved<=0:raise ValueError('Invalid reservation')
        ident=key if key is not None else uuid.uuid4().hex
        if not isinstance(ident,str) or not ident:raise ValueError('Invalid reservation key')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM charges WHERE id=?',(ident,)).fetchone():raise BudgetError('Duplicate request key; replay prohibited')
            cap=db.execute('SELECT cap_micros FROM settings WHERE id=1').fetchone()[0]
            if db.execute("SELECT 1 FROM charges WHERE status='settled' AND actual_micros>reserved_micros LIMIT 1").fetchone():raise BudgetError('Prior charge exceeded reservation; dispatch stopped')
            committed=db.execute("SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual_micros ELSE reserved_micros END),0) FROM charges").fetchone()[0]
            if committed+reserved>cap:raise BudgetError('Budget ceiling exhausted or reservations held')
            db.execute('INSERT INTO charges VALUES (?,?,?,?,NULL,?,?,?)',(ident,model,case_id,reserved,'reserved',None,None));db.commit()
        self.checkpoint()
        return ident

    def settle(self,ident,actual_orNone,generation_id=None,usage=None):
        actual=None if actual_orNone is None else _micros(actual_orNone)
        if generation_id is not None and not isinstance(generation_id,str):raise ValueError('Invalid generation ID')
        usage_json=json.dumps(usage,sort_keys=True,allow_nan=False) if usage is not None else None
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT reserved_micros,status FROM charges WHERE id=?',(ident,)).fetchone()
            if not row or row[1]!='reserved':raise BudgetError('Reservation is not pending; repeat settlement prohibited')
            status='unknown' if actual is None else 'settled'
            db.execute('UPDATE charges SET actual_micros=?,status=?,generation_id=?,usage_json=? WHERE id=?',(actual,status,generation_id,usage_json,ident));db.commit()
        self.checkpoint()
        if actual is not None and actual>row[0]:raise BudgetError('Charge exceeded reservation; retained and dispatch stopped')

    def summary(self):
        with self._db() as db:
            cap=db.execute('SELECT cap_micros FROM settings WHERE id=1').fetchone()[0]
            rows=db.execute('SELECT id,model,case_id,reserved_micros,actual_micros,status,generation_id,usage_json FROM charges ORDER BY rowid').fetchall()
        charges=[{'id':r[0],'model':r[1],'case_id':r[2],'reserved_usd':r[3]/1e6,'actual_usd':r[4]/1e6 if r[4] is not None else None,'status':r[5],'generation_id':r[6],'usage':json.loads(r[7]) if r[7] else None} for r in rows]
        actual=sum(r[4] for r in rows if r[4] is not None);held=sum(r[3] for r in rows if r[4] is None)
        return {'cap_usd':cap/1e6,'actual_usd':actual/1e6,'unresolved_reserved_usd':held/1e6,'remaining_usd':max(0,cap-actual-held)/1e6,'requests':len(rows),'charges':charges}

"""Atomic project-wide pacing, active-request leases and provider feedback.

Each request retains its full input/output admission weight until completion.
No API request is retried. 429 feedback only delays subsequent new requests.
"""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time
from .protocol import TENANT_RPM,TENANT_TPM

class Pacer:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY, model TEXT, at REAL, weight INTEGER, active INTEGER, expires REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS limits (scope TEXT PRIMARY KEY, rpm INTEGER, tpm INTEGER, deferred REAL, min_gap REAL)')
            db.execute('INSERT OR IGNORE INTO limits VALUES (?,?,?,?,?)',('tenant',TENANT_RPM,TENANT_TPM,0,60/TENANT_RPM))
            db.execute('CREATE TABLE IF NOT EXISTS observed (model TEXT PRIMARY KEY, requests INTEGER, tokens INTEGER, request_reset REAL, token_reset REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS feedback (at REAL, model TEXT, status INTEGER, headers TEXT)')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def acquire(self,model,rpm,tpm,weight,deadline):
        if type(weight) is not int or weight<=0:raise ValueError('Invalid admission weight')
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Deadline before API admission')
            with self.db() as db:
                now=time.time()
                # A lease expires only after the request's hard deadline plus cleanup grace.
                db.execute('UPDATE tickets SET active=0 WHERE active=1 AND expires<=?',(now,))
                db.execute('INSERT OR IGNORE INTO limits VALUES (?,?,?,?,0)',(model,rpm,tpm,0))
                scopes=[('tenant',None), (model,model)];wait=0
                for scope,filter_model in scopes:
                    cap_rpm,cap_tpm,deferred,gap=db.execute('SELECT rpm,tpm,deferred,min_gap FROM limits WHERE scope=?',(scope,)).fetchone()
                    cap_rpm=min(cap_rpm,rpm) if filter_model else cap_rpm
                    cap_tpm=min(cap_tpm,tpm) if filter_model else cap_tpm
                    if weight>cap_tpm:raise ValueError('Request admission bound exceeds configured TPM; reduce request before dispatch')
                    rows=db.execute('SELECT at,weight FROM tickets WHERE at>?'+(' AND model=?' if filter_model else '')+' ORDER BY at', (now-60,model) if filter_model else (now-60,)).fetchall()
                    wait=max(wait,deferred-now)
                    if rows:
                        wait=max(wait,rows[-1][0]+gap-now)
                        if len(rows)>=cap_rpm or sum(r[1] for r in rows)+weight>cap_tpm:wait=max(wait,rows[0][0]+60-now)
                observed=db.execute('SELECT requests,tokens,request_reset,token_reset FROM observed WHERE model=?',(model,)).fetchone()
                if observed:
                    req,tok,req_reset,tok_reset=observed
                    if req is not None and req<=0 and req_reset>now:wait=max(wait,req_reset-now)
                    if tok is not None and tok<weight and tok_reset>now:wait=max(wait,tok_reset-now)
                # Serialize network requests while permitting local tools to run in parallel.
                if db.execute('SELECT 1 FROM tickets WHERE active=1 LIMIT 1').fetchone():wait=max(wait,.1)
                if wait<=0:
                    if observed:db.execute('UPDATE observed SET requests=MAX(0,requests-1),tokens=MAX(0,tokens-?) WHERE model=?',(weight,model))
                    cursor=db.execute('INSERT INTO tickets(model,at,weight,active,expires) VALUES (?,?,?,1,?)',(model,now,weight,now+remaining+5))
                    return cursor.lastrowid
            if wait>=remaining:raise TimeoutError('Deadline before API admission')
            time.sleep(min(max(wait,.01),.25))

    def finish(self,ticket,actual_tokens=None):
        if actual_tokens is not None and (type(actual_tokens) is not int or actual_tokens<0):raise ValueError('Invalid usage')
        with self.db() as db:
            if actual_tokens is None:db.execute('UPDATE tickets SET active=0 WHERE id=?',(ticket,))
            else:db.execute('UPDATE tickets SET active=0,weight=? WHERE id=?',(actual_tokens,ticket))

    def observe(self,model,headers,status=200,retry_after=None):
        headers={str(k).lower():str(v) for k,v in headers.items()}
        def integer(key):
            try:return max(0,int(headers[key]))
            except (KeyError,ValueError):return None
        def seconds(value):
            try:return max(0,float(str(value).removesuffix('s')))
            except (ValueError,TypeError):return 60.
        with self.db() as db:
            now=time.time();db.execute('INSERT INTO feedback VALUES (?,?,?,?)',(now,model,status,json.dumps(headers)))
            for field,key in (('rpm','x-ratelimit-limit-requests'),('tpm','x-ratelimit-limit-tokens')):
                value=integer(key)
                if value:db.execute(f'UPDATE limits SET {field}=MIN({field},?) WHERE scope=?',(value,model))
            delays=[]
            for kind in ('requests','tokens'):
                remaining=integer('x-ratelimit-remaining-'+kind)
                if remaining is not None and remaining==0:delays.append(seconds(headers.get('x-ratelimit-reset-'+kind)))
            if status==429:
                delays.append(seconds(retry_after or headers.get('retry-after'))+1)
                db.execute("UPDATE limits SET min_gap=MAX(min_gap,20) WHERE scope='tenant'")
            db.execute('INSERT OR REPLACE INTO observed VALUES (?,?,?,?,?)',(model,integer('x-ratelimit-remaining-requests'),integer('x-ratelimit-remaining-tokens'),now+seconds(headers.get('x-ratelimit-reset-requests')),now+seconds(headers.get('x-ratelimit-reset-tokens'))))
            if delays:
                # Conservative tenant-wide cooldown also covers limits shared with other consumers.
                db.execute('UPDATE limits SET deferred=MAX(deferred,?) WHERE scope IN (?,?)',(now+max(delays),model,'tenant'))

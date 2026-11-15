"""Concurrent credentials with the existing shared tenant/model admission limits."""
import time

from stoa_vista_specialists.pacing import Pacer as SerialPacer


class Pacer(SerialPacer):
    credential_scope = 'legacy'

    def __init__(self, path):
        super().__init__(path)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS lens_ticket_keys (ticket INTEGER PRIMARY KEY, credential TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS lens_client_policy (id INTEGER PRIMARY KEY, concurrency INTEGER NOT NULL, gap REAL)')
            db.execute('INSERT OR IGNORE INTO lens_client_policy VALUES (1,1,NULL)')

    def configure(self,credential_concurrency,tenant_rpm,tenant_tpm):
        if any(type(v) is not int or v<1 for v in (credential_concurrency,tenant_rpm,tenant_tpm)):
            raise ValueError('Positive integer pacing settings required')
        with self.db() as db:
            db.execute('UPDATE lens_client_policy SET concurrency=?,gap=? WHERE id=1',(credential_concurrency,60/tenant_rpm))
            # Preserve active provider cooldowns and observed per-model limits.
            db.execute("UPDATE limits SET rpm=?,tpm=?,min_gap=? WHERE scope='tenant'",(tenant_rpm,tenant_tpm,60/tenant_rpm))

    def observe(self,*args,**kwargs):
        super().observe(*args,**kwargs)
        with self.db() as db:
            gap=db.execute('SELECT gap FROM lens_client_policy WHERE id=1').fetchone()[0]
            if gap is not None:
                # Retry-After remains in deferred; a past 429 must not impose a permanent 20-second gap.
                db.execute("UPDATE limits SET min_gap=? WHERE scope='tenant'",(gap,))

    def acquire(self, model, rpm, tpm, weight, deadline):
        if type(weight) is not int or weight <= 0:
            raise ValueError('Invalid admission weight')
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Deadline before API admission')
            with self.db() as db:
                now=time.time()
                db.execute('UPDATE tickets SET active=0 WHERE active=1 AND expires<=?',(now,))
                db.execute('INSERT OR IGNORE INTO limits VALUES (?,?,?,?,0)',(model,rpm,tpm,0))
                wait=0
                for scope, filtered in (('tenant',False),(model,True)):
                    cap_rpm,cap_tpm,deferred,gap=db.execute('SELECT rpm,tpm,deferred,min_gap FROM limits WHERE scope=?',(scope,)).fetchone()
                    if filtered:cap_rpm,cap_tpm=min(cap_rpm,rpm),min(cap_tpm,tpm)
                    if weight>cap_tpm:raise ValueError('Request admission bound exceeds configured TPM; reduce request before dispatch')
                    rows=db.execute('SELECT at,weight FROM tickets WHERE at>?'+(' AND model=?' if filtered else '')+' ORDER BY at',
                                    (now-60,model) if filtered else (now-60,)).fetchall()
                    wait=max(wait,deferred-now)
                    if rows:
                        wait=max(wait,rows[-1][0]+gap-now)
                        if len(rows)>=cap_rpm or sum(r[1] for r in rows)+weight>cap_tpm:
                            wait=max(wait,rows[0][0]+60-now)
                observed=db.execute('SELECT requests,tokens,request_reset,token_reset FROM observed WHERE model=?',(model,)).fetchone()
                if observed:
                    req,tok,req_reset,tok_reset=observed
                    if req is not None and req<=0 and req_reset>now:wait=max(wait,req_reset-now)
                    if tok is not None and tok<weight and tok_reset>now:wait=max(wait,tok_reset-now)
                # Older consumers without a key receipt remain globally exclusive.
                legacy=db.execute('SELECT 1 FROM tickets t LEFT JOIN lens_ticket_keys k ON k.ticket=t.id '
                    'WHERE t.active=1 AND k.credential IS NULL LIMIT 1').fetchone()
                active=db.execute('SELECT COUNT(*) FROM tickets t JOIN lens_ticket_keys k ON k.ticket=t.id '
                    'WHERE t.active=1 AND k.credential=?',(self.credential_scope,)).fetchone()[0]
                concurrency=db.execute('SELECT concurrency FROM lens_client_policy WHERE id=1').fetchone()[0]
                if legacy or active>=concurrency:wait=max(wait,.1)
                if wait<=0:
                    if observed:db.execute('UPDATE observed SET requests=MAX(0,requests-1),tokens=MAX(0,tokens-?) WHERE model=?',(weight,model))
                    ticket=db.execute('INSERT INTO tickets(model,at,weight,active,expires) VALUES (?,?,?,1,?)',
                                      (model,now,weight,now+remaining+5)).lastrowid
                    db.execute('INSERT INTO lens_ticket_keys VALUES (?,?)',(ticket,self.credential_scope))
                    return ticket
            if wait>=remaining:raise TimeoutError('Deadline before API admission')
            time.sleep(min(max(wait,.01),.25))

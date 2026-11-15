"""Persistent single-worker queue. Replays events, never paid inference."""
from __future__ import annotations

import asyncio
import copy
import fcntl
import json
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from stoa_lens.budget import LensBudget, BudgetError
from stoa_lens.model_config import resolve, validate
from stoa_lens.registry import Registry, sha
from stoa_lens.service import Cancelled, run
from . import settings
from .lens_answers import answer_for

class RouteUnavailable(RuntimeError): pass

TERMINAL={'completed','failed','cancelled','interrupted'}

def now():return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, drawings, documents, root=None, runner=None, budget=None):
        self.drawings,self.documents=drawings,documents
        self.root=Path(root or settings.DATA_DIR/'lens').resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.db_path=self.root/'jobs.sqlite'
        self.budget=budget if budget is not None else LensBudget(self.root/'budget.sqlite',25)
        self.checkpoint=lambda:None
        self.runner=runner or run
        self.lock=threading.RLock();self.wake=threading.Event();self.stopping=threading.Event();self.thread=None;self.worker_lock=None
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, signature TEXT NOT NULL, project TEXT NOT NULL,
                conversation TEXT NOT NULL, request TEXT NOT NULL, manifest TEXT NOT NULL, status TEXT NOT NULL,
                cancel INTEGER NOT NULL DEFAULT 0, answer TEXT, created TEXT NOT NULL, finished TEXT);
                CREATE TABLE IF NOT EXISTS events (job TEXT NOT NULL, seq INTEGER NOT NULL, event TEXT NOT NULL, PRIMARY KEY(job,seq));''')

    def db(self):
        db=sqlite3.connect(self.db_path,timeout=10);db.row_factory=sqlite3.Row
        return db

    def event(self, ident, event, db=None):
        if db is None:
            with self.lock,self.db() as conn:
                conn.execute('BEGIN IMMEDIATE');return self.event(ident,event,conn)
        seq=db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE job=?',(ident,)).fetchone()[0]
        ev={**event,'seq':seq,'job_id':ident}
        db.execute('INSERT INTO events VALUES (?,?,?)',(ident,seq,json.dumps(ev,allow_nan=False)))
        return ev

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():return
            handle=(self.root/'worker.lock').open('a')
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:handle.close();return  # another API process owns inference
            self.worker_lock=handle
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                for row in db.execute("SELECT id FROM jobs WHERE status='running'").fetchall():
                    self._terminal(row['id'],'interrupted',{'status':'error','text':'Backend interrupted. This job will not run again.','job_id':row['id'],'citations':[]},db)
            self.checkpoint()
            self.stopping.clear();self.thread=threading.Thread(target=self._worker,name='lens-inference',daemon=True);self.thread.start()

    def stop(self):
        self.stopping.set();self.wake.set()
        if self.thread:self.thread.join(timeout=1)

    def _row(self, ident):
        with self.db() as db:row=db.execute('SELECT * FROM jobs WHERE id=?',(ident,)).fetchone()
        if not row:raise KeyError(ident)
        return row

    def lookup(self, ident):
        row=self._row(ident)
        return {'id':row['id'],'request_id':row['request_id'],'status':row['status'],'cancel_requested':bool(row['cancel']),
                'request':json.loads(row['request']),'manifest':self.public_manifest(json.loads(row['manifest'])),
                'answer':json.loads(row['answer']) if row['answer'] else None,'created_at':row['created'],'finished_at':row['finished']}

    def public_manifest(self, manifest):
        from stoa_lens.harness import public
        out=public(manifest)
        # Source paths are backend-owned; the allowlist exposes only named artifacts.
        for p in out.get('pages',[]):
            for k in ('image','pdf','native_source'):p.pop(k,None)
        return out

    def list(self, project, conversation=None):
        with self.db() as db:
            rows=db.execute('SELECT id FROM jobs WHERE project=?'+(' AND conversation=?' if conversation else '')+' ORDER BY rowid',(project,conversation) if conversation else (project,)).fetchall()
        return [self.lookup(r['id']) for r in rows]

    def submit(self, request):
        normalized=copy.deepcopy(request)
        configuration=resolve(normalized.setdefault('routing_mode','smart'))
        request=normalized
        signature=json.dumps(normalized,sort_keys=True,allow_nan=False)
        with self.lock,self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM jobs WHERE request_id=?',(request['request_id'],)).fetchone()
            if old:
                old_request=json.loads(old['signature'])
                # A matching legacy Smart/default replay retains its original events,
                # manifest and Verda identity. Never infer or rewrite old model labels.
                old_request.setdefault('routing_mode','smart')
                if old_request!=normalized:raise ValueError('Request ID already belongs to a different request')
                return old['id']
            if self.runner is run:
                from .lens_readiness import readiness
                if not readiness(self.budget)['modes'][configuration['routing_mode']]['ready']:
                    raise RouteUnavailable(configuration['routing_mode'].capitalize()+' is unavailable. Try again when it is ready.')
            ids=request['selected_drawing_ids']
            if not 1<=len(ids)<=3 or len(set(ids))!=len(ids):raise ValueError('Select one to three unique sheets')
            ident=uuid.uuid4().hex;folder=self.root/'jobs'/ident;snapshot=folder/'sources';snapshot.mkdir(parents=True)
            pages=[];drawings=[]
            for index,drawing_id in enumerate(ids):
                d=self.drawings.raw(drawing_id);dest=snapshot/str(index);dest.mkdir()
                row=copy.deepcopy(d.get('lens_source') or {})
                image=Path(row.get('image') or self.drawings.image_path(drawing_id))
                if row.get('image_sha256') and sha(image)!=row['image_sha256']:raise ValueError('Source image changed before snapshot')
                target=dest/'image.png';shutil.copyfile(image,target)
                row.update(image=str(target),width=d['image']['size'][0],height=d['image']['size'][1],image_sha256=sha(target),source_page=row.get('source_page',1),drawing_id=drawing_id)
                if row.get('pdf'):
                    pdf=dest/'page.pdf';shutil.copyfile(row['pdf'],pdf);row['pdf']=str(pdf);row['pdf_sha256']=sha(pdf)
                if row.get('document_id'):
                    original=self.documents.original(row['document_id']);shutil.copyfile(original,dest/original.name)
                if row.get('native_source'):
                    native=row['native_source'];native['path']=str(target)
                    for key in ('path','extraction_path'):
                        src=Path(native['native_geometry'][key]);target_asset=dest/src.name;shutil.copyfile(src,target_asset);native['native_geometry'][key]=str(target_asset)
                else:
                    row.setdefault('project_identity',request['project_id'])
                    row.update(dataset=d['source'].get('dataset','Upload'),exposure=row.get('exposure','bundled_exposed_sample' if not d.get('lens_source') else 'user_upload'))
                (dest/'drawing.json').write_text(json.dumps(d,indent=2))
                pages.append(row);drawings.append({'drawing_id':drawing_id,'title':d['title'],'image_sha256':row['image_sha256']})
            for p in pages:
                if p.get('document_page_count'):
                    p['packet_included_pages']=sum(q.get('document_id')==p.get('document_id') for q in pages)
            registry=Registry(pages,folder/'registration')
            page_sheets={v['page_id']:ids[i] for i,v in enumerate(registry.initial_views)}
            # Prior IDs must belong to this project AND this conversation. Read only server answers.
            prior=[]
            for pid in request.get('prior_job_ids',[]):
                old=db.execute('SELECT * FROM jobs WHERE id=?',(pid,)).fetchone()
                if not old or old['project']!=request['project_id'] or old['conversation']!=request['conversation_id']:raise ValueError('Prior job is outside this conversation')
            for old in db.execute("SELECT * FROM jobs WHERE project=? AND conversation=? AND status='completed' ORDER BY rowid DESC LIMIT 6",(request['project_id'],request['conversation_id'])).fetchall()[::-1]:
                prior.append({'job_id':old['id'],'question':json.loads(old['request'])['question'],'answer':json.loads(old['answer'])})
            snapshot_hashes={str(p.relative_to(folder)):sha(p) for p in snapshot.rglob('*') if p.is_file()}
            from .lens_intent import bind_count_scope
            count_scope=bind_count_scope(request['question'],pages)
            manifest={'routing_mode':configuration['routing_mode'],'configuration':configuration,'models':{'orchestrator':configuration['orchestrator'],'untrained_detector':configuration['detector']},'snapshot_sha256':snapshot_hashes,'schema':'ymir-lens-job-v1','request':request,'pages':pages,'sheets':drawings,'page_sheets':page_sheets,'conversation_context':prior,'created_at':now(),'build_context_is_calibration':False,'count_scope':count_scope}
            (folder/'manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False))
            db.execute('INSERT INTO jobs (id,request_id,signature,project,conversation,request,manifest,status,created) VALUES (?,?,?,?,?,?,?,?,?)',
                       (ident,request['request_id'],signature,request['project_id'],request['conversation_id'],json.dumps(request),json.dumps(manifest),'queued',now()))
            self.event(ident,{'type':'job','status':'queued','request_id':request['request_id'],'routing_mode':configuration['routing_mode'],'configuration':configuration},db)
            self.event(ident,{'type':'stage','stage':'question','state':'done',
                             'summary':f'{len(ids)} selected sheet'+('s' if len(ids)!=1 else ''),
                             'detail':request['question']+'\n'+', '.join(d['title'] for d in drawings)},db)
            self.event(ident,{'type':'stage','stage':'queue','state':'done',
                             'summary':f'{len(ids)} sheet'+('s' if len(ids)!=1 else '')+' snapshotted · queued',
                             'detail':f'{len(snapshot_hashes)} snapshot files hashed; source copies and manifest written. Queued for the single inference worker.'},db)
            self.event(ident,{'type':'stage','stage':'route','state':'done',
                             'summary':'Compact counter · '+', '.join(count_scope['target_classes']) if count_scope else 'General harness',
                             'detail':'compact_counter; bound page index 0; classes: '+', '.join(count_scope['target_classes']) if count_scope else 'general; no count scope bound'},db)
        self.checkpoint()
        self.start();self.wake.set()
        return ident

    def cancel(self, ident):
        with self.lock,self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT status FROM jobs WHERE id=?',(ident,)).fetchone()
            if not row:raise KeyError(ident)
            if row['status'] not in TERMINAL:
                db.execute('UPDATE jobs SET cancel=1 WHERE id=?',(ident,))
                self.event(ident,{'type':'job','status':'cancelling'},db)
                if row['status']=='queued':self._terminal(ident,'cancelled',{'status':'error','text':'Cancelled before dispatch.','job_id':ident,'citations':[]},db)
        self.checkpoint()
        return self.lookup(ident)

    def _terminal(self, ident, state, answer, db, checks=None):
        row=db.execute('SELECT manifest FROM jobs WHERE id=?',(ident,)).fetchone()
        configuration=json.loads(row[0]).get('configuration') if row else None
        if configuration:
            answer=dict(answer,routing_mode=configuration['routing_mode'])
            if not answer.get('configuration'):answer['configuration']=configuration
        db.execute('UPDATE jobs SET status=?,answer=?,finished=? WHERE id=?',(state,json.dumps(answer),now(),ident))
        self.event(ident,{'type':'job','status':state},db)
        if checks is not None:self.event(ident,checks,db)
        self.event(ident,{'type':'answer','answer':answer},db)

    @staticmethod
    def _checks(result):
        accepted=len(result.get('accepted_proposal_ids',[]));errors=result.get('validation_errors',[])
        counts=result.get('counts',[]);status=result['status']
        detail=[f'Final evidence status: {status}',
                'Accepted proposal IDs: '+(', '.join(result.get('accepted_proposal_ids',[])) or 'none')]
        detail += [f"{c['page_id']} · {c['object_class']}: "+(str(c['count']) if c['count'] is not None else 'unconfirmed') for c in counts]
        detail += [f"{len(result.get('rooms',[]))} rooms; {len(result.get('measurements',[]))} measurement results."]
        detail += errors
        return {'type':'stage','stage':'checks','state':'done',
                'summary':f'{accepted} accepted · {len(errors)} error'+('s' if len(errors)!=1 else '')+f' · {status}', 'detail':'\n'.join(detail)}

    def events(self, ident, after=0):
        self._row(ident)
        with self.db() as db:rows=db.execute('SELECT event FROM events WHERE job=? AND seq>? ORDER BY seq',(ident,after)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def _poll(self, ident, after):
        # Status first: terminal status and its final events commit together, so a terminal
        # status read here guarantees the events query below already includes the answer.
        with self.db() as db:
            row=db.execute('SELECT status FROM jobs WHERE id=?',(ident,)).fetchone()
            if not row:raise KeyError(ident)
            rows=db.execute('SELECT event FROM events WHERE job=? AND seq>? ORDER BY seq',(ident,after)).fetchall()
        return row['status'],[json.loads(r[0]) for r in rows]

    async def stream(self, ident, after=0, poll=.25, heartbeat=15):
        """NDJSON events until the job ends. Async, so a watcher holds no worker thread for the
        minutes a job can run; each poll is a short SQLite read off the event loop."""
        await asyncio.to_thread(self._row,ident)
        last=time.monotonic()
        while True:
            status,events=await asyncio.to_thread(self._poll,ident,after)
            for ev in events:
                after=ev['seq'];yield json.dumps(ev,ensure_ascii=False)+'\n'
            if status in TERMINAL:return
            if events:last=time.monotonic()
            elif time.monotonic()-last>=heartbeat:
                yield '\n';last=time.monotonic()
            await asyncio.sleep(poll)

    def _worker(self):
        try:
            while not self.stopping.is_set():
                with self.lock,self.db() as db:
                    db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT * FROM jobs WHERE status='queued' AND cancel=0 ORDER BY rowid LIMIT 1").fetchone()
                    if row:
                        db.execute("UPDATE jobs SET status='running' WHERE id=?",(row['id'],));self.event(row['id'],{'type':'job','status':'running'},db)
                if row:self.checkpoint()
                if not row:self.wake.wait(.5);self.wake.clear();continue
                ident=row['id'];manifest=json.loads(row['manifest']);folder=self.root/'jobs'/ident
                def cancelled():return self.stopping.is_set() or bool(self._row(ident)['cancel'])
                checks=None
                try:
                    if cancelled():raise Cancelled('Cancelled before dispatch')
                    if any(sha(folder/path)!=expected for path,expected in manifest.get('snapshot_sha256',{}).items()):raise ValueError('Immutable job source snapshot changed')
                    if 'configuration' not in manifest:raise ValueError('Legacy job has no executable routing snapshot; replay only')
                    configuration=validate(manifest['configuration'])
                    hooks={'configuration':configuration,'budget':self.budget,'conversation_context':manifest['conversation_context'],
                           'progress':lambda ev:self._progress(ident,ev),'cancelled':cancelled}
                    if manifest.get('count_scope') is not None:
                        hooks.update(agent='compact_counter',count_scope=manifest['count_scope'])
                    result=self.runner(manifest['pages'],json.loads(row['request'])['question'],folder/('lens-'+ident),**hooks)
                    checks=self._checks(result)
                    if cancelled():raise Cancelled('Cancelled after current operation')
                    answer=answer_for(ident,result,manifest['page_sheets']);state='completed'
                except Cancelled:
                    state='interrupted' if self.stopping.is_set() else 'cancelled';answer={'status':'error','text':'Cancelled after the current operation. No further calls were dispatched.','job_id':ident,'citations':[]}
                except BudgetError:
                    state='failed';answer={'status':'error','text':'Request incomplete. The recorded evidence is available for review.','job_id':ident,'citations':[]}
                except BaseException as exc:
                    state='interrupted' if self.stopping.is_set() else 'failed'
                    answer={'status':'error','text':'Ymir stopped ('+type(exc).__name__+'). This job will not be retried.','job_id':ident,'citations':[]}
                with self.lock,self.db() as db:
                    db.execute('BEGIN IMMEDIATE');self._terminal(ident,state,answer,db,checks)
                self.checkpoint()
        finally:
            if self.worker_lock:self.worker_lock.close();self.worker_lock=None

    def _progress(self, ident, ev):
        # The host executes operations serially. Persist that order rather than
        # attaching every tool call to a reused model node.
        with self.lock,self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT manifest FROM jobs WHERE id=?',(ident,)).fetchone()
            manifest=json.loads(row[0]) if row else {}
            configuration=manifest.get('configuration')
            ev=copy.deepcopy(ev)
            if ev.get('operation')=='response' and configuration:
                identity=configuration['orchestrator']
                ev.update(routing_mode=configuration['routing_mode'],model=identity['model'],provider=identity['provider'],endpoint=identity['endpoint'])
            previous=db.execute("SELECT event FROM events WHERE job=? AND json_extract(event,'$.type')='node' ORDER BY seq DESC LIMIT 1",(ident,)).fetchone()
            parent=json.loads(previous[0])['id'] if previous else None
            index=ev.get('response_index')
            if index is None:
                response=db.execute("SELECT event FROM events WHERE job=? AND json_extract(event,'$.type')='node' AND json_extract(event,'$.id') LIKE 'response-%' ORDER BY seq DESC LIMIT 1",(ident,)).fetchone()
                index=int(json.loads(response[0])['id'].split('-')[-1])-1 if response else 0
            if ev.get('operation')=='tool':
                node=f"tool-{index+1}-{ev.get('call_id') or ev['tool']}"
                result=ev.get('result') or {};route=ev.get('routing') or result.get('routing') or {}
                model=ev.get('model') or result.get('model')
                detector={'dfine':'D-FINE-L','argon':'Argon','kimi':'Kimi'}.get(route.get('backend'),model or 'Detector')
                tool=detector if ev['tool']=='detect_objects' else ev['tool']
                name=ev['tool'].replace('_',' ').capitalize()
                kind=route.get('requested_class') or route.get('canonical_class')
                summary=' · '.join(str(v) for v in (tool if ev['tool']=='detect_objects' else None,kind,result.get('error_type') or result.get('status')) if v) or ev['tool']
                state='running' if ev['phase']=='started' else 'failed' if result.get('error_type') else 'done'
            else:
                node=f'response-{index+1}';name=f'Ymir response {index+1}'
                model=ev.get('model')
                tool={'deepseek/deepseek-v4.1-flash-1017':'DeepSeek','openai/gpt-6.1-sol':'Sol'}.get(model,model or 'DeepSeek')
                summary=ev.get('reason') or ev.get('error_type') or f'Response {index+1}'
                state={'started':'running','failed':'failed'}.get(ev['phase'],'done')
            old=db.execute("SELECT event FROM events WHERE job=? AND json_extract(event,'$.type')='node' AND json_extract(event,'$.id')=? ORDER BY seq LIMIT 1",(ident,node)).fetchone()
            prior=json.loads(old[0]) if old else {}
            parents=prior.get('parents',[]) if old else [parent] if parent else []
            if ev.get('operation')=='tool' and ev['tool']=='detect_objects' and not model and not route:
                tool=prior.get('tool',tool);model=prior.get('detail')
            if ev['phase']=='started' or ev.get('operation')=='tool':
                self.event(ident,{'type':'node','id':node,'name':name,'tool':tool,'detail':model,'parents':parents},db)
            self.event(ident,{'type':'status','id':node,'state':state,'summary':summary},db)
            self.event(ident,{'type':'progress','activity':ev},db)
        self.checkpoint()

    def image_artifacts(self, ident):
        row=self._row(ident);folder=self.root/'jobs'/ident
        manifest=json.loads(row['manifest']);items={}
        for index,sheet in enumerate(manifest['sheets']):
            items['sheet-'+sheet['drawing_id']]=folder/'sources'/str(index)/'image.png'
        # Only known PNG image roles, never API journals or arbitrary user paths.
        for path in sorted(folder.glob('**/overlay.png')):
            rel=path.relative_to(folder)
            import hashlib
            items['overlay-'+hashlib.sha256(str(rel).encode()).hexdigest()[:20]]=path
        return items

    def artifact_inventory(self, ident):
        self._row(ident)
        return [{'name':n,'url':f'/api/lens/jobs/{ident}/artifacts/{n}'} for n in ['manifest','result','events',*self.image_artifacts(ident)]]

    def artifact(self, ident, name):
        self._row(ident);folder=self.root/'jobs'/ident
        if name=='manifest':return self.public_manifest(json.loads((folder/'manifest.json').read_text()))
        if name=='result':return self.lookup(ident)['answer']
        if name=='events':return self.events(ident)
        image=self.image_artifacts(ident).get(name)
        if image is not None and image.is_file():return image
        raise KeyError(name)

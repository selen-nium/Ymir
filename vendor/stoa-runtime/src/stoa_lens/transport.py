"""Deadline-bound, once-only Verda transport for Lens."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import math
import os
import tempfile
import time
import uuid
from datetime import datetime,timezone
from pathlib import Path

from stoa_junction.verda_baseline.transport import stream_sdk
from .pacing import Pacer
from .budget import MODELS, BudgetError

MAX_TOKENS=65536
MAX_PRICE_AGE_SECONDS=86400


def _env(root):
    env=dict(os.environ);path=Path(root)/'.env.verda'
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1);env.setdefault(k.strip().removeprefix('export '),v.strip().strip('"').strip("'"))
    return env


def _write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)



def _secrets(env):
    return sorted({value for key,value in env.items() if isinstance(value,str) and len(value)>=8
                   and 'VERDA' in key.upper() and any(part in key.upper() for part in ('KEY','SECRET','TOKEN')) and '://' not in value}, key=len, reverse=True)


def _redact(value,secrets):
    if isinstance(value,str):
        for secret in secrets:value=value.replace(secret,'[REDACTED]')
        return value
    if isinstance(value,list):return [_redact(x,secrets) for x in value]
    if isinstance(value,dict):return {k:_redact(v,secrets) for k,v in value.items()}
    return value

def _catalog_record(raw):
    """Normalize model catalog metadata without credentials or provider secrets."""
    return {'code':raw['code'],'status':raw['status'],'input_modalities':raw['input_modalities'],
            'pricing':{k:str(raw['pricing'][k]) for k in ('input','output')},
            'context_length':raw['context_length'],'max_output_length':raw['max_output_length'],
            'reasoning':raw.get('reasoning',{}),
            'quota_rpm':raw.get('quota_rpm') or raw.get('request_rate_limit_per_min') or 3,
            'quota_tpm':raw.get('quota_tpm') or 300000}


def refresh_pricing(root,provider_home,deadline=None):
    """Refresh authenticated, read-only catalog metadata; never call inference."""
    import requests
    env=_env(root)
    if not env.get('VERDA_CLIENT_ID') or not env.get('VERDA_CLIENT_SECRET'):raise ValueError('Verda catalog credentials absent')
    def timeout():
        remaining=20 if deadline is None else min(20,deadline-time.monotonic())
        if remaining<=0:raise TimeoutError('Deadline before catalog refresh')
        return remaining
    response=requests.post('https://api.verda.com/v1/oauth2/token',json={'grant_type':'client_credentials','client_id':env['VERDA_CLIENT_ID'],'client_secret':env['VERDA_CLIENT_SECRET']},timeout=timeout(),allow_redirects=False)
    if not response.ok:raise RuntimeError('Catalog authentication HTTP '+str(response.status_code))
    token=response.json()['access_token'];models={}
    for model in MODELS:
        response=requests.get('https://api.verda.com/v1/inference/models/'+model,headers={'Authorization':'Bearer '+token},timeout=timeout(),allow_redirects=False)
        if not response.ok:raise RuntimeError('Catalog lookup HTTP '+str(response.status_code))
        models[model]=_catalog_record(response.json())
    result={'observed_at':datetime.now(timezone.utc).isoformat(),'catalog_verified':True,'source':'authenticated Verda catalog GET','models':models}
    _validate_pricing(result)
    home=Path(provider_home);home.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='catalog-',suffix='.tmp',dir=home)
    try:
        with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(name,home/'catalog.json')
    finally:
        if os.path.exists(name):os.unlink(name)
    return result


def _validate_pricing(data):
    if not isinstance(data,dict) or data.get('catalog_verified') is not True:raise ValueError('Unverified provider catalog')
    try:
        stamp=datetime.fromisoformat(data['observed_at'].replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError('Catalog timestamp must be timezone-aware')
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
        if age < -60 or age>MAX_PRICE_AGE_SECONDS:raise ValueError('Provider catalog is stale')
        for model in MODELS:
            row=data['models'][model]
            if row['code']!=model or row['status']!='available' or 'image' not in row['input_modalities']:raise ValueError('Unsupported provider model')
            if int(row['context_length'])<MAX_TOKENS or int(row['max_output_length'])<MAX_TOKENS:raise ValueError('Provider model context insufficient')
            if 'high' not in row['reasoning']['supported_efforts']:raise ValueError('Provider high reasoning unavailable')
            for k in ('input','output'):
                price=float(row['pricing'][k])
                if not math.isfinite(price) or price<=0:raise ValueError('Invalid provider pricing')
            for k in ('quota_rpm','quota_tpm'):
                if type(row[k]) is not int or row[k]<=0:raise ValueError('Invalid pacing quota')
    except (KeyError,TypeError,OverflowError) as e:raise ValueError('Incomplete provider catalog') from e
    return data



def _partial_receipt(path):
    generation=None;usage=None
    path=Path(path)
    if path.exists():
        for line in path.read_text().splitlines():
            try:chunk=json.loads(line)
            except ValueError:continue
            if isinstance(chunk,dict):
                generation=generation or chunk.get('id')
                if isinstance(chunk.get('usage'),dict):usage=chunk['usage']
    return generation,usage

def _filter_headers(headers):
    return {str(k):str(v) for k,v in (headers or {}).items() if str(k).lower().startswith(('x-ratelimit','retry-after','x-request-id'))}


def _admission(body,model):
    def strip(v):
        if isinstance(v,dict):
            if v.get('type')=='image_url':return {'type':'image_url'}
            return {k:strip(x) for k,x in v.items()}
        if isinstance(v,list):return [strip(x) for x in v]
        return v
    images=sum(sum(isinstance(b,dict) and b.get('type')=='image_url' for b in m['content']) for m in body['messages'] if isinstance(m.get('content'),list))
    return MAX_TOKENS+len(json.dumps(strip(body),ensure_ascii=False).encode())+256*len(body['messages'])+images*(16384 if model==MODELS[0] else 65536)


class VerdaTransport:
    """requester, when provided, is callable(body, deadline, directory) -> response dict."""
    def __init__(self,root,directory,budget,provider_home=None,pricing=None,requester=None,
                 credential_env='VERDA_INFERENCE_API_KEY',json_mode=False):
        self.root=Path(root).resolve();self.directory=Path(directory).resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.budget=budget;self.provider_home=Path(provider_home) if provider_home else self.root/'data/stoa-lens/provider'
        self.pricing=pricing;self.requester=requester
        if credential_env not in ('VERDA_INFERENCE_API_KEY','VERDA_INFERENCE_API_KEY_2'):
            raise ValueError('Unsupported Verda credential slot')
        self.credential_env,self.json_mode=credential_env,bool(json_mode)
        # One product-wide DB for all Lens jobs, separate from the study ledger.
        self.pacing=Pacer(self.root/'data/stoa-lens/provider-pacing.sqlite')
        self.case_id=self.directory.name

    def _prices(self,deadline):
        if self.pricing is not None:return _validate_pricing(self.pricing)
        path=self.provider_home/'catalog.json'
        if path.exists():
            try:return _validate_pricing(json.loads(path.read_text()))
            except (ValueError,KeyError):pass
        return refresh_pricing(self.root,self.provider_home,deadline)

    def _reserve(self,model,amount,key,deadline):
        """Queue temporary reservation contention without retrying an API call."""
        while True:
            try:return self.budget.reserve(model,self.case_id,amount,key=key)
            except BudgetError as exc:
                if str(exc)!='Budget ceiling exhausted or reservations held':raise
                summary=self.budget.summary()
                active=[c for c in summary['charges'] if c['status']=='reserved']
                unknown=sum(c['reserved_usd'] for c in summary['charges'] if c['status']=='unknown')
                eventual=summary['cap_usd']-summary['actual_usd']-unknown
                if not active or eventual+1e-9<amount:raise
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Deadline waiting for active budget reservations') from exc
                time.sleep(min(.1,remaining))

    def _call(self,model,messages,tools,deadline,ident,final=False):
        if model not in MODELS:raise ValueError('Unsupported model')
        if time.monotonic()>=deadline:raise TimeoutError('Deadline before dispatch')
        data=self._prices(deadline);row=data['models'][model];rates=row['pricing']
        body={'model':model,'messages':messages,'reasoning_effort':'high','max_tokens':MAX_TOKENS,'stream':True,'stream_options':{'include_usage':True}}
        if tools:body.update(tools=tools,tool_choice='none' if final else 'auto')
        if self.json_mode or (not tools and (final or model==MODELS[1])):
            body['response_format']={'type':'json_object'}
        path=self.directory/'api'/ident
        path.mkdir(parents=True,exist_ok=True)
        # Deterministic per-trajectory key blocks repeats across process restarts.
        key='lens-'+hashlib.sha256((str(self.directory)+'/'+ident).encode()).hexdigest()
        reserve=(int(row['context_length'])*float(rates['input'])+MAX_TOKENS*float(rates['output']))*1.05+.001
        reservation=self._reserve(model,reserve,key,deadline)
        sent=False;settled=False;ticket=None;headers={};secret=None;secrets=[]
        try:
            environment=_env(self.root);secrets=_secrets(environment)
            secret=environment.get(self.credential_env) or (environment.get('VERDA_API_KEY') if self.credential_env=='VERDA_INFERENCE_API_KEY' else None)
            self.pacing.credential_scope=hashlib.sha256(secret.encode()).hexdigest() if secret else 'injected'
            _write(path/'request.json',_redact(body,secrets))
            _write(path/'reservation.json',_redact({'id':reservation,'amount_usd':reserve,'catalog':data},secrets))
            weight=_admission(body,model)
            ticket=self.pacing.acquire(model,row['quota_rpm'],row['quota_tpm'],weight,deadline)
            if time.monotonic()>=deadline:raise TimeoutError('Deadline before request')
            _validate_pricing(data)  # catalog must remain fresh at network admission
            if self.requester is not None:
                sent=True;response=self.requester(body,deadline,path)
                if not isinstance(response,dict):raise ValueError('Invalid mock/transport response')
                headers=_redact(_filter_headers(response.pop('_headers',{})),secrets)
                _write(path/'headers.json',headers)
                if not (path/'chunks.jsonl').exists():_write(path/'stream-receipt.json',_redact({'transport':'injected','response':response},secrets))
            else:
                base=environment.get('VERDA_INFERENCE_BASE_URL','https://inference.verda.com/v1').rstrip('/')
                if not secret or base!='https://inference.verda.com/v1':raise ValueError('Verda inference configuration unavailable')
                with tempfile.TemporaryDirectory(prefix='lens-stream-') as temp:
                    chunks=Path(temp)/'chunks.jsonl';head=Path(temp)/'headers.json'
                    try:
                        sent=True;response=asyncio.run(stream_sdk(body,secret,base,deadline,chunks,head))
                    finally:
                        if chunks.exists():
                            # Keep partial streams on failure while removing configured credentials.
                            (path/'chunks.jsonl').write_text(_redact(chunks.read_text(),secrets))
                        if head.exists():headers=_redact(_filter_headers(json.loads(head.read_text())),secrets);_write(path/'headers.json',headers)
            self.pacing.observe(model,headers)
            response=_redact(response,secrets)
            usage=response.get('usage') or {};p,c=usage.get('prompt_tokens'),usage.get('completion_tokens')
            if type(p) is not int or type(c) is not int or min(p,c)<0:raise ValueError('Missing usage; reservation retained')
            cost=p*float(rates['input'])+c*float(rates['output'])
            partial_generation,_=_partial_receipt(path/'chunks.jsonl')
            generation=response.get('id') or response.get('generation_id') or partial_generation
            try:
                self.budget.settle(reservation,cost,generation_id=generation,usage=usage)
            except BudgetError:
                settled=True  # over-reservation charges are committed before raising
                raise
            settled=True
            self.pacing.finish(ticket,p+c);ticket=None
            receipt={'model':model,'reasoning':'high','max_tokens':MAX_TOKENS,'usage':usage,'generation_id':generation,
                     'credential_slot':'secondary' if self.credential_env.endswith('_2') else 'primary',
                     'actual_usd_upper_bound':cost,'reservation_usd':reserve,'finish_reason':response.get('finish_reason')}
            _write(path/'response.json',_redact(response,secrets));_write(path/'billing.json',_redact(receipt,secrets))
            message=response.get('message')
            if not isinstance(message,dict) or message.get('role')!='assistant':raise ValueError('Malformed assistant response')
            if response.get('finish_reason')=='length':raise ValueError('Model output ceiling reached')
            return message,receipt
        except BaseException as exc:
            status=getattr(exc,'status_code',None);http=getattr(exc,'response',None)
            headers=_redact(_filter_headers(dict(http.headers) if http is not None and hasattr(http,'headers') else headers),secrets)
            if status is not None:self.pacing.observe(model,headers,status)
            if not settled:
                # 429 is an explicit provider rejection; unknown stream failure retains its hold.
                generation,partial_usage=_partial_receipt(path/'chunks.jsonl')
                self.budget.settle(reservation,0 if not sent or status==429 else None,generation_id=_redact(generation,secrets),usage=_redact(partial_usage,secrets))
            _write(path/'failure.json',{'error_type':type(exc).__name__,'http_status':status,'api_dispatch_started':sent,'headers':headers,'reservation_state':'settled' if settled or not sent or status==429 else 'unknown'})
            raise
        finally:
            if ticket is not None:self.pacing.finish(ticket)

    def complete(self,messages,tools,deadline,response_index,final=False):
        if type(response_index) is not int or response_index<0:raise ValueError('Invalid response index')
        return self._call(MODELS[0],messages,tools,deadline,f'deepseek-{response_index:02d}',final)[0]

    def kimi(self,**kwargs):
        if kwargs.get('model')!=MODELS[1] or kwargs.get('reasoning')!='high' or kwargs.get('max_tokens')!=MAX_TOKENS or kwargs.get('stream_usage') is not True:
            raise ValueError('Kimi protocol mismatch')
        timeout=kwargs.get('timeout')
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:raise ValueError('Invalid timeout')
        path=Path(kwargs['image_path']);mime='image/png' if path.suffix.lower()=='.png' else 'image/jpeg'
        image={'type':'image_url','image_url':{'url':'data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode()}}
        messages=[{'role':'user','content':[{'type':'text','text':kwargs['prompt']},image]}]
        ident='kimi-'+str(time.time_ns())+'-'+uuid.uuid4().hex[:8]
        message,receipt=self._call(MODELS[1],messages,None,time.monotonic()+timeout,ident)
        return {'text':message.get('content'),'message':message,**receipt}

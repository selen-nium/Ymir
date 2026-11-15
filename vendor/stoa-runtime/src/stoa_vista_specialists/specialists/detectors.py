"""Object specialists operating on a single crop with native-page receipts.

Transports are injected so orchestration owns budget, deadlines, and provider pacing.
Coordinates emitted by VLMs are normalized to 0..1000 on the supplied crop.
"""
from __future__ import annotations
import hashlib
import fcntl
import signal
import shutil
import json
import math
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time
from PIL import Image

CLASSES = ('door', 'window', 'column', 'staircase', 'elevator', 'toilet_bowl',
           'urinal', 'bathtub', 'shower', 'sink', 'appliance', 'bed', 'chair',
           'table', 'sofa', 'cabinet_wardrobe')
CHECKPOINT = Path('/media/administrator/Justins 500gb/stoa-rfdetr-vista-v1/best.ckpt')
CHECKPOINT_SHA256 = '326af6e243f889bdb343d4c9c944c0e0b04d393d4f712d1ab56f4c98e461e562'
PROMPT = ('Find all visible instances of the requested object in this single drawing crop. '
          'Return JSON with status (complete or partial), instances (label, bbox_2d, confidence), '
          'and uncertainties. bbox_2d is [x0,y0,x1,y1] in crop coordinates normalized to 0..1000. '
          'Give tight boxes, include clipped instances and do not combine or deduplicate instances. '
          'Confidence is your uncalibrated estimate from 0 to 1; use null if unknown. '
          'Do not use tools or inspect other images. Request: ')
SCHEMA = {'type':'object','additionalProperties':False,'required':['status','instances','uncertainties'],
 'properties': {'status': {'type':'string','enum':['complete','partial']},
 'instances': {'type':'array','items':{'type':'object','additionalProperties':False,
  'required':['label','bbox_2d','confidence'], 'properties':{'label':{'type':'string'},
  'bbox_2d':{'type':'array','items':{'type':'number'},'minItems':4,'maxItems':4},
  'confidence':{'anyOf':[{'type':'number'}, {'type':'null'}]}}}},
 'uncertainties':{'type':'array','items':{'type':'string'}}}}


def _matrix(view):
    t = view['transform']
    if isinstance(t, dict):
        if all(k in t for k in ('scale_x', 'scale_y', 'origin_x', 'origin_y')):
            return [[float(t['scale_x']), 0., float(t['origin_x'])], [0., float(t['scale_y']), float(t['origin_y'])]]
        t = t.get('matrix', t.get('view_to_native'))
    if t is None: raise ValueError('Missing view-to-native transform')
    if len(t) == 6 and not isinstance(t[0], (list, tuple)):
        return [[float(t[0]),float(t[1]),float(t[2])],[float(t[3]),float(t[4]),float(t[5])]]
    if len(t) in (2,3) and all(len(row) == 3 for row in t):
        if len(t)==3 and list(t[2]) != [0,0,1]: raise ValueError('Non-affine transform')
        return [[float(x) for x in row] for row in t[:2]]
    raise ValueError('Expected affine view-to-native transform')


def map_proposals(view, region, rows, model, confidence_kind, normalized=True):
    """Map boxes via all four corners; invalid boxes are errors, never repaired."""
    x0,y0,x1,y1 = map(float, region)
    w,h = int(view['width']),int(view['height'])
    if not all(math.isfinite(v) for v in (x0,y0,x1,y1)) or not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise ValueError('Invalid crop region')
    m = _matrix(view)
    if not all(math.isfinite(v) for row in m for v in row) or abs(m[0][0]*m[1][1]-m[0][1]*m[1][0]) < 1e-12:
        raise ValueError('Invalid affine transform')
    out=[]
    for index, row in enumerate(rows):
        raw = list(map(float, row['bbox_2d']))
        if len(raw)!=4 or not all(math.isfinite(v) for v in raw) or raw[0]>=raw[2] or raw[1]>=raw[3]:
            raise ValueError('Invalid proposal geometry')
        score=row.get('confidence')
        if score is not None and (isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError('Invalid proposal confidence')
        if not isinstance(row.get('label'),str) or not row['label'].strip(): raise ValueError('Invalid label')
        scale_x,scale_y=((x1-x0)/1000,(y1-y0)/1000) if normalized else (1.,1.)
        box=[x0+raw[0]*scale_x,y0+raw[1]*scale_y,x0+raw[2]*scale_x,y0+raw[3]*scale_y]
        clipped = box[0]<x0 or box[1]<y0 or box[2]>x1 or box[3]>y1
        edge = clipped or box[0]<=x0 or box[1]<=y0 or box[2]>=x1 or box[3]>=y1
        clipped_box=[max(x0,box[0]),max(y0,box[1]),min(x1,box[2]),min(y1,box[3])]
        if clipped_box[0]>=clipped_box[2] or clipped_box[1]>=clipped_box[3]: raise ValueError('Proposal outside crop')
        points=[(m[0][0]*x+m[0][1]*y+m[0][2],m[1][0]*x+m[1][1]*y+m[1][2]) for x in (clipped_box[0],clipped_box[2]) for y in (clipped_box[1],clipped_box[3])]
        native=[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
        if native[0]<-1e-6 or native[1]<-1e-6 or native[2]>view['original_width']+1e-6 or native[3]>view['original_height']+1e-6:
            raise ValueError('Mapped proposal outside registered page')
        identity=json.dumps([view['source_sha256'],view['view_id'],list(region),model,index,raw,row['label']],separators=(',',':'))
        out.append({'id':'proposal_'+hashlib.sha256(identity.encode()).hexdigest()[:20], 'label':row['label'],
         'raw_box_xyxy':raw,'view_box_xyxy':box,'native_box_xyxy':native,'confidence':score,
         'confidence_kind':confidence_kind,'clipped':clipped,'touches_crop_edge':edge,
         'requires_wider_inspection':edge,'source_sha256':view['source_sha256'], 'view_id':view['view_id'], 'model':model})
    return out


def _validate(value):
    import jsonschema
    jsonschema.validate(value, SCHEMA)
    return value

def _extract_object(text):
    """Accept a single JSON object or one fenced JSON object with prose around it."""
    try:
        value=json.loads(text)
    except json.JSONDecodeError:
        blocks=list(re.finditer(r'```(?:json)?\s*\n([\s\S]*?)\n\s*```',text,re.IGNORECASE))
        if len(blocks)!=1:
            raise ValueError('Expected exactly one fenced JSON object') from None
        surrounding=text[:blocks[0].start()]+text[blocks[0].end():]
        if '{' in surrounding or '}' in surrounding:
            raise ValueError('Ambiguous JSON outside fenced response')
        value=json.loads(blocks[0].group(1))
    return _validate(value)

class SpecialistResponseError(ValueError):
    """Malformed response with provider receipt retained for the host transcript."""
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt

def _partial_text(value):
    if value is None: return ''
    return value.decode('utf-8', errors='replace') if isinstance(value,bytes) else value


def _cleanup_process(process, previous=None, terminate=True):
    """Bound cleanup even when a descendant holds inherited stdout/stderr open."""
    stdout=_partial_text(getattr(previous,'output',None)); stderr=_partial_text(getattr(previous,'stderr',None))
    def send(sig):
        try: os.killpg(process.pid,sig)
        except ProcessLookupError: pass
    if terminate: send(signal.SIGTERM)
    try:
        out,err=process.communicate(timeout=1)
        return {'stdout':_partial_text(out) or stdout,'stderr':_partial_text(err) or stderr,
                'returncode':process.returncode,'cleanup_unconfirmed':False}
    except subprocess.TimeoutExpired as error:
        stdout=_partial_text(error.output) or stdout; stderr=_partial_text(error.stderr) or stderr
    send(signal.SIGKILL)
    try:
        out,err=process.communicate(timeout=1)
        return {'stdout':_partial_text(out) or stdout,'stderr':_partial_text(err) or stderr,
                'returncode':process.returncode,'cleanup_unconfirmed':False}
    except subprocess.TimeoutExpired as error:
        stdout=_partial_text(error.output) or stdout; stderr=_partial_text(error.stderr) or stderr
        # A surviving process may retain the pipe. Never wait without a deadline.
        for stream in (process.stdin,process.stdout,process.stderr):
            if stream is not None:
                try: stream.close()
                except (OSError,ValueError): pass
        try: process.wait(timeout=.5)
        except subprocess.TimeoutExpired: pass
        return {'stdout':stdout,'stderr':stderr,'returncode':process.returncode,'cleanup_unconfirmed':True}


def _bounded_process(cmd, *, input=None, timeout, env=None):
    """Terminate the process group, retaining partial output with bounded cleanup."""
    if timeout <= 0:
        raise SpecialistResponseError('Deadline exhausted', {'stdout':'', 'stderr':'', 'timed_out':True})
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    try:
        stdout, stderr = process.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        receipt=_cleanup_process(process,error); receipt['timed_out']=True
        raise SpecialistResponseError('Deadline exhausted', receipt) from None
    except BaseException as error:
        receipt=_cleanup_process(process); receipt['interrupted']=True
        raise SpecialistResponseError('Worker interrupted: '+str(error),receipt) from error
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)

class Kimi:
    model='kimi-k3-1-pro'
    def __init__(self, transport): self.transport=transport
    def predict(self, image_path, reason, timeout):
        # Transport contract: callable(model, reasoning, prompt, image_path, max_tokens,
        # stream_usage, timeout) -> {'text': raw reply, ... receipt}. No automatic retry.
        from ..protocol import OUTPUT_TOKENS
        cap=OUTPUT_TOKENS['kimi']
        receipt=self.transport(model=self.model, reasoning='high', prompt=PROMPT+reason,
                               image_path=str(image_path), max_tokens=cap, stream_usage=True, timeout=timeout)
        receipt=dict(receipt,requested_model=self.model,requested_reasoning='high',requested_max_tokens=cap)
        try:
            value=_extract_object(receipt['text'])
        except (ValueError, KeyError, TypeError) as error:
            raise SpecialistResponseError(str(error), receipt) from error
        except Exception as error:
            raise SpecialistResponseError(str(error), receipt) from error
        return value,receipt

class Sol:
    model='gpt-6.1-sol'
    def __init__(self, runner=None): self.runner=runner
    def predict(self,image_path,reason,timeout):
        from stoa.codex import DISABLED, read_events
        with tempfile.TemporaryDirectory(prefix='stoa-sol-box-') as temp:
            schema=Path(temp)/'schema.json';schema.write_text(json.dumps(SCHEMA))
            instructions=Path(temp)/'instructions.txt';instructions.write_text('Only perform the supplied localization task. External tools, files, and network are unavailable.')
            cmd=['codex','exec','--ignore-user-config','--ignore-rules','--ephemeral','--skip-git-repo-check',
                 '--sandbox','read-only','--model',self.model,'--json','--color','never','--cd',temp,
                 '--output-schema',str(schema),'-c','model_reasoning_effort="high"',
                 '-c','model_reasoning_summary="none"','-c','web_search="disabled"',
                 '-c','project_doc_max_bytes=0','-c','forced_login_method="chatgpt"',
                 '-c','model_instructions_file='+json.dumps(str(instructions)),
                 '--enable','omit_skill_descriptions','--enable','skip_host_skill_discovery']
            for feature in DISABLED: cmd+=['--disable',feature]
            cmd+=['--image',str(Path(image_path).resolve()),'-']
            env={k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENROUTER_API_KEY')}
            start=time.monotonic()
            p=(self.runner(cmd,input=PROMPT+reason,text=True,capture_output=True,timeout=timeout,env=env)
               if self.runner else _bounded_process(cmd,input=PROMPT+reason,timeout=timeout,env=env))
            try:
                final,usage,errors,thread_id=read_events(p.stdout)
            except Exception as error:
                raise SpecialistResponseError('Invalid Sol event stream: '+str(error), {'raw_events':p.stdout,'stderr':p.stderr,'returncode':p.returncode}) from error
            receipt={'raw_events':p.stdout,'stderr':p.stderr,'usage':usage,
               'thread_id':thread_id,'latency_s':time.monotonic()-start,'cost_usd':None,
               'billing':'ChatGPT subscription; dollar cost unavailable','output_ceiling':'not enforceable by CLI',
               'model':self.model,'reasoning':'high','service_tier':'default'}
            if p.returncode or not final: raise SpecialistResponseError('Sol invocation failed: '+(' / '.join(errors) or p.stderr[-700:]), receipt)
            try: return _extract_object(final), receipt
            except Exception as error: raise SpecialistResponseError(str(error), receipt) from error

class RFDetr:
    model='rf-detr-large-epoch18-ema'
    def __init__(self, runner=None, checkpoint=CHECKPOINT, in_process=False,
                 python='/media/administrator/Justins 500gb/stoa-dataset-merge/rfdetr-six-class-v1/environment/bin/python',
                 gpu_lock=None):
        self.runner=runner; self.checkpoint=Path(checkpoint); self.module=None
        self.in_process=in_process; self.python=python
        self.gpu_lock=Path(gpu_lock or Path(__file__).resolve().parents[3]/'data/vista-specialists/gpu.lock')
    def load(self):
        import torch
        from rfdetr.training import RFDETRModelModule
        from stoa_dataset_merge.expanded_training_runtime import make_configs
        root=Path(__file__).resolve().parents[3]
        with self.checkpoint.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
        if digest!=CHECKPOINT_SHA256: raise ValueError('RF-DETR checkpoint hash mismatch')
        if not torch.cuda.is_available(): raise RuntimeError('RF-DETR requires CUDA and BF16 support')
        initial=Path('/media/administrator/Justins 500gb/stoa-dataset-merge/rfdetr-six-class-v1/pretrained/rf-detr-large-2026.pth')
        model_config,train_config=make_configs(root/'datasets/expanded-fast-training-v5',initial,root/'data/vista-specialists/rfdetr-runtime')
        module=RFDETRModelModule(model_config,train_config).cuda().eval()
        saved=torch.load(self.checkpoint,map_location='cpu',weights_only=False)
        if saved.get('epoch')!=17: raise ValueError('Expected epoch 18 checkpoint')
        ema=[v for v in saved.get('callbacks',{}).values() if isinstance(v,dict) and 'average_model_state_dict' in v]
        if len(ema)!=1 or not ema[0].get('latest_update_step'): raise ValueError('EMA weights missing')
        prefix='module.model.'
        weights={k[len(prefix):]:v for k,v in ema[0]['average_model_state_dict'].items() if k.startswith(prefix)}
        module.model.load_state_dict(weights,strict=True)
        self.module=module
        return {'checkpoint_sha256':digest,'epoch':18,'ema_update_step':ema[0]['latest_update_step'],'strict':True}
    def predict(self,image_path,reason,timeout):
        if self.runner is not None: return self.runner(image_path,reason,timeout)
        if not self.in_process:
            started=time.monotonic(); self.gpu_lock.parent.mkdir(parents=True,exist_ok=True)
            with self.gpu_lock.open('a') as lock:
                while True:
                    try:
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); break
                    except BlockingIOError:
                        if time.monotonic()-started >= timeout:
                            raise SpecialistResponseError('GPU wait exhausted deadline', {'timed_out':True, 'phase':'gpu_wait'})
                        time.sleep(min(.05, max(0.,timeout-(time.monotonic()-started))))
                waited=time.monotonic()-started
                with tempfile.TemporaryDirectory(prefix='stoa-rfdetr-worker-') as temp:
                    output=Path(temp)/'result.json'
                    root=Path(__file__).resolve().parents[3]
                    env=os.environ.copy(); env['PYTHONPATH']=str(root/'src')+os.pathsep+env.get('PYTHONPATH','')
                    cmd=[self.python,'-m','stoa_vista_specialists.specialists.rf_worker',str(image_path),str(self.checkpoint),str(output)]
                    p=_bounded_process(cmd, timeout=timeout-(time.monotonic()-started),env=env)
                    logs={'stdout':p.stdout,'stderr':p.stderr,'returncode':p.returncode,'gpu_wait_seconds':waited}
                    if p.returncode or not output.exists(): raise SpecialistResponseError('RF-DETR worker failed',logs)
                    try: value,receipt=json.loads(output.read_text())
                    except Exception as error: raise SpecialistResponseError(str(error),logs) from error
                    receipt['worker']=logs
                    return value,receipt
        import torch
        from torchvision.transforms import functional as TF
        from rfdetr.utilities.tensors import nested_tensor_from_tensor_list
        if self.module is None: self.load()
        with Image.open(image_path) as image:
            image=image.convert('RGB'); width,height=image.size
            tensor=TF.to_tensor(image.resize((960,960),Image.Resampling.BILINEAR))
        tensor=TF.normalize(tensor,[0.485,0.456,0.406],[0.229,0.224,0.225])
        samples=nested_tensor_from_tensor_list([tensor]).to('cuda')
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
            outputs=self.module.model(samples)
            if outputs['pred_logits'].shape[-1]!=17: raise ValueError('Expected 16 foreground logits and background slot')
            outputs={**outputs,'pred_logits':outputs['pred_logits'][...,:16]}
            result=self.module.postprocess(outputs,torch.tensor([[height,width]],device='cuda'))[0]
        torch.cuda.synchronize()
        instances=[{'label':CLASSES[int(label)],'confidence':float(score),'bbox_2d':list(map(float,box))}
          for label,score,box in zip(result['labels'].cpu().tolist(),result['scores'].cpu().tolist(),result['boxes'].cpu().tolist(),strict=True) if float(score)>=.25]
        return {'status':'complete','instances':instances,'uncertainties':[]}, {'checkpoint_sha256':CHECKPOINT_SHA256,'epoch':18,'weights':'EMA','resolution':960,'precision':'BF16','threshold':.25,'background_removed_before_topk':True}


def detect(adapter, view, region, reason, timeout=600, artifacts_dir=None, target_kind=None, target_labels=None):
    """Run exactly one crop request. Region is integer view-pixel xyxy, no retries."""
    if not isinstance(reason,str) or not reason.strip(): raise ValueError('A localization reason is required')
    if len(region)!=4 or any(isinstance(v,bool) or not isinstance(v,int) for v in region): raise ValueError('Crop must use integer pixel boundaries')
    classes_by_target={'toilets':('toilet_bowl',),'windows':('window',),'doors':('door',),'baths':('bathtub','shower')}
    if target_labels is not None:
        if not isinstance(target_labels,(list,tuple,set)) or not target_labels or not set(target_labels).issubset(CLASSES): raise ValueError('Invalid target labels')
        selected=set(target_labels)
    elif target_kind is not None:
        if target_kind not in classes_by_target: raise ValueError('Unsupported target kind')
        selected=set(classes_by_target[target_kind])
    else: selected=None

    # Validate before any model call, including transform and view registration.
    map_proposals(view,region,[],adapter.model,'uncalibrated')
    with Image.open(view['path']) as image:
        if image.size != (view['width'],view['height']): raise ValueError('View dimensions differ')
        with tempfile.TemporaryDirectory(prefix='stoa-specialist-crop-') as temp:
            path=Path(temp)/'crop.png'
            image.convert('RGB').crop(tuple(region)).save(path)
            crop_sha=hashlib.sha256(path.read_bytes()).hexdigest()
            if artifacts_dir is not None:
                artifact_path=Path(artifacts_dir); artifact_path.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,artifact_path/'crop.png')
            try:
                value,receipt=adapter.predict(path,reason,timeout)
            except SpecialistResponseError as error:
                if artifacts_dir is not None:
                    (artifact_path/'failure.json').write_text(json.dumps({'error':str(error),'receipt':error.receipt},ensure_ascii=False,indent=2,default=str))
                raise
    try:
        _validate(value)
        rfdetr=isinstance(adapter,RFDetr)
        all_proposals=map_proposals(view,region,value['instances'],adapter.model,'detector_score_uncalibrated' if rfdetr else 'vlm_self_reported_uncalibrated',not rfdetr)
        proposals=[p for p in all_proposals if not rfdetr or selected is None or p['label'] in selected]
        selection={'target_kind':target_kind,'target_labels':sorted(selected) if selected else None,
                   'applied':rfdetr and selected is not None,'all_class_proposal_count':len(all_proposals),
                   'selected_proposal_count':len(proposals)}
    except Exception as error:
        failure={'raw':value, 'receipt':receipt, 'crop_sha256':crop_sha}
        if artifacts_dir is not None:
            (artifact_path/'failure.json').write_text(json.dumps({'error':str(error),'receipt':failure},ensure_ascii=False,indent=2,default=str))
        raise SpecialistResponseError(str(error), failure) from error
    if artifacts_dir is not None:
        from PIL import ImageDraw
        with Image.open(artifact_path/'crop.png') as image:
            image=image.convert('RGB'); draw=ImageDraw.Draw(image)
            for proposal in proposals:
                box=proposal['view_box_xyxy']; local=[box[0]-region[0],box[1]-region[1],box[2]-region[0],box[3]-region[1]]
                draw.rectangle(local,outline='red',width=2); draw.text((local[0],local[1]),proposal['label'],fill='red')
            image.save(artifact_path/'overlay.png')
        (artifact_path/'receipt.json').write_text(json.dumps({'raw':value,'receipt':receipt,'proposals':proposals,'all_class_proposals':all_proposals,'selection':selection},ensure_ascii=False,indent=2,default=str))
    result={'model':adapter.model,'status':value['status'],'uncertainties':value['uncertainties'],
       'proposals':proposals,'selection':selection,'receipt':receipt,'crop_sha256':crop_sha,'region':list(region)}
    # Keep unfiltered RF raw records on disk rather than returning unrelated classes
    # to the orchestrator. Other specialist raw responses are unchanged.
    if not selection['applied']: result['raw']=value
    return result

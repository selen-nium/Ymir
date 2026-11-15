"""Bounded, warm D-FINE-L detector for the fourteen trained Junction heads."""
from __future__ import annotations
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import threading
import time

ROOT=Path(os.environ.get('YMIR_STOA_ROOT',Path(__file__).resolve().parents[2])).resolve()
CHECKPOINT=ROOT/'checkpoints/junction-focused-dfine-l-baseline-v1/best.pth'
CHECKPOINT_SHA256='0f76bfabb562647a98f34d5dd3dd0d240eb5ef5ff7c1072fe14a4c667147cb8d'
UPSTREAM=ROOT/'data/dfine-v5/upstream'
PYTHON=os.environ.get('YMIR_DFINE_PYTHON','/media/administrator/Justins 500gb/stoa-dfine-v1/environment/bin/python')
GPU_LOCK=ROOT/'data/vista-specialists/gpu.lock'
RECIPE_SHA256='37d69d30bc5003f270cacff4f67281bbed3d52e79eba196e1a2c0622670b9ab2'
BEST_METADATA_SHA256='aafde53267dfe77a137917bc5e80aa77381ea79a8ce67cbce91066b23fb0dcca'
HEADS=('door_single','door_double','door_sliding','window','toilet_bowl','sink','bathtub','shower',
       'cooker_stove','refrigerator','washing_machine','dishwasher','door','appliance')

def verified_head_policy():
    if os.environ.get('YMIR_GPU_BACKEND')=='modal':
        from .cloud import manifest
        policy=manifest()['models']['dfine']['receipt']['head_policy']
        if policy.get('recipe_sha256')!=RECIPE_SHA256 or policy.get('best_metadata_sha256')!=BEST_METADATA_SHA256 or policy.get('heads')!=list(HEADS):
            raise ValueError('Remote trained-head policy differs')
        return policy
    folder=CHECKPOINT.parent
    recipe_bytes=(folder/'recipe.json').read_bytes(); best_bytes=(folder/'best.json').read_bytes()
    if hashlib.sha256(recipe_bytes).hexdigest()!=RECIPE_SHA256 or hashlib.sha256(best_bytes).hexdigest()!=BEST_METADATA_SHA256:
        raise ValueError('D-FINE recipe or best-selection metadata hash mismatch')
    recipe=json.loads(recipe_bytes);best=json.loads(best_bytes)
    if (recipe.get('architecture')!='D-FINE-L' or recipe.get('resolution')!=960 or
        recipe.get('precision')!='bf16' or recipe.get('head_classes')!=list(HEADS) or best.get('epoch')!=18):
        raise ValueError('D-FINE trained-head policy differs')
    return {'recipe_sha256':RECIPE_SHA256,'best_metadata_sha256':BEST_METADATA_SHA256,'heads':list(HEADS)}

class DFineError(RuntimeError):
    def __init__(self,message,receipt):super().__init__(message);self.receipt=receipt

class DFine:
    model='d-fine-l-junction-ema'
    coordinates='pixels'
    confidence_kind='detector_score_uncalibrated'
    trained_classes=HEADS
    def __init__(self, *, python=PYTHON, checkpoint=CHECKPOINT, upstream=UPSTREAM, gpu_lock=GPU_LOCK, threshold=.25, runner=None):
        if isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or not 0<=threshold<=1:raise ValueError('Invalid threshold')
        self.python=str(python);self.checkpoint=Path(checkpoint);self.upstream=Path(upstream);self.gpu_lock=Path(gpu_lock);self.threshold=float(threshold)
        self.runner=runner;self.process=None;self.stderr=None;self.guard=threading.Lock()
    def _stderr(self):
        if self.stderr is None:return ''
        self.stderr.flush();self.stderr.seek(0);return self.stderr.read().decode('utf-8','replace')
    def close(self):
        process=self.process;self.process=None
        if process is not None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                try:process.wait(timeout=1)
                except subprocess.TimeoutExpired:pass
            for stream in (process.stdin,process.stdout):
                if stream:
                    try:stream.close()
                    except (OSError,ValueError):pass
        if self.stderr:self.stderr.close();self.stderr=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def _start(self):
        if self.process is not None and self.process.poll() is None:return
        self.close();self.stderr=tempfile.TemporaryFile()
        env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])+os.pathsep+env.get('PYTHONPATH','')
        self.process=subprocess.Popen([self.python,'-m','stoa_lens.dfine_worker',str(self.checkpoint),str(self.upstream)],
             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,env=env,start_new_session=True,bufsize=0)
    def predict(self,image_path,object_class,timeout):
        if object_class not in HEADS:raise ValueError('D-FINE class is not trained: '+str(object_class))
        if isinstance(timeout,bool) or not isinstance(timeout,(float,int)) or not math.isfinite(timeout) or timeout<=0:raise ValueError('Positive finite timeout required')
        start=time.monotonic();deadline=start+timeout
        if not self.guard.acquire(timeout=timeout):raise DFineError('Detector wait exceeded deadline',{'phase':'thread_wait'})
        try:
            self.gpu_lock.parent.mkdir(parents=True,exist_ok=True)
            with self.gpu_lock.open('a') as lock:
                while True:
                    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:
                        if time.monotonic()>=deadline:raise DFineError('GPU wait exceeded deadline',{'phase':'gpu_wait'})
                        time.sleep(min(.05,max(0.,deadline-time.monotonic())))
                if time.monotonic()>=deadline:raise DFineError('GPU wait exceeded deadline',{'phase':'gpu_wait'})
                if self.runner is not None:return self.runner(str(image_path),object_class,self.threshold,max(0.,deadline-time.monotonic()))
                self._start();process=self.process
                message=json.dumps({'image_path':str(Path(image_path).resolve()),'object_class':object_class,'threshold':self.threshold},allow_nan=False).encode()+b'\n'
                try:
                    process.stdin.write(message);process.stdin.flush()
                    with selectors.DefaultSelector() as select:
                        select.register(process.stdout,selectors.EVENT_READ)
                        data=b''
                        while b'\n' not in data:
                            remain=deadline-time.monotonic()
                            if remain<=0 or not select.select(remain):raise TimeoutError('D-FINE worker deadline exceeded')
                            chunk=os.read(process.stdout.fileno(),65536)
                            if not chunk:raise RuntimeError('D-FINE worker exited')
                            data+=chunk
                        payload=json.loads(data.split(b'\n',1)[0])
                    if payload.get('error'):raise RuntimeError(payload['error'])
                    payload['receipt']['wall_seconds']=time.monotonic()-start
                    payload['receipt']['worker_stderr']=self._stderr()
                    return payload['value'],payload['receipt']
                except BaseException as exc:
                    receipt={'stderr':self._stderr(),'worker_pid':process.pid,'error':str(exc)}
                    self.close()
                    raise DFineError('D-FINE worker failed: '+str(exc),receipt) from exc
        finally:self.guard.release()

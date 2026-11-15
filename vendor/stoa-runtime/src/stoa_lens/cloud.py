"""Private, once-only Modal GPU calls. OpenRouter owns every language-model call."""
from pathlib import Path
import hashlib
import json
import os


def enabled():return os.environ.get('YMIR_GPU_BACKEND')=='modal'


def manifest():
    path=Path(os.environ['YMIR_MODEL_READINESS'])
    report=json.loads(path.read_text())
    if report.get('passed') is not True:raise ValueError('Modal GPU smoke has not passed')
    from .dfine import CHECKPOINT_SHA256
    from stoa_room_area_specialists.raster import CHECKPOINT_SHA256 as raster_sha
    models=report['models']
    if not models['dfine'].get('passed') or models['dfine']['receipt']['checkpoint_sha256']!=CHECKPOINT_SHA256:
        raise ValueError('Unverified Modal detector')
    if not models['raster2seq'].get('passed') or models['raster2seq']['checkpoint_sha256']!=raster_sha:
        raise ValueError('Unverified Modal room model')
    if models['dfine']['receipt'].get('model_state_loaded_strictly') is not True:
        raise ValueError('Detector weights were not strictly verified')
    room=models['raster2seq'];coverage=room.get('checkpoint_coverage',{})
    if coverage.get('missing_keys')!=[] or coverage.get('unexpected_keys')!=[]:
        raise ValueError('Incomplete room-model weight coverage')
    worker=Path(__file__).resolve().parents[1]/'stoa_room_area_specialists/raster_worker.py'
    if hashlib.sha256(worker.read_bytes()).hexdigest()!=room.get('worker_sha256'):
        raise ValueError('Room worker changed since GPU verification')
    return report


class RemoteModels:
    def __init__(self,budget,lookup=None,*,case_id=None):
        if budget is None:raise ValueError('Durable budget required for GPU dispatch')
        self.budget,self.lookup=budget,lookup
        self.case_id=case_id

    def call(self,name,payload,timeout,**kwargs):
        if name not in ('detect','rooms'):raise ValueError('Unknown GPU operation')
        if timeout<=0:raise TimeoutError('GPU deadline expired before dispatch')
        manifest()  # readiness never triggers inference
        if self.lookup is None:
            import modal
            function=modal.Function.from_name('ymir-models',name)
        else:function=self.lookup(name)
        # Fixed 180s execution + 180s startup + 2s idle at capped 2 CPU/8GiB.
        # Billing is reconciled separately; unknown usage retains the whole hold.
        reservation=self.budget.reserve_compute(self.case_id or 'ymir-gpu-'+name,.25)
        call=None
        try:
            call=function.spawn(payload,**kwargs)
            return call.get(timeout=timeout)
        except BaseException:
            if call is not None:
                try:call.cancel(terminate_containers=True)
                except Exception:pass
            raise
        finally:
            self.budget.settle(reservation,None,generation_id=call.object_id if call is not None else None,
                usage={'provider':'Modal','function':name,'billing':'unresolved; full reservation held','automatic_retries':False})

    def detect(self,path,object_class,threshold,timeout):
        result=self.call('detect',Path(path).read_bytes(),timeout,object_class=object_class,threshold=threshold)
        expected=manifest()['models']['dfine']['receipt']['checkpoint_sha256']
        if result['receipt']['checkpoint_sha256']!=expected:raise ValueError('Remote checkpoint changed')
        return result['value'],result['receipt']

    def raster(self,root,folder):
        from stoa_vista_specialists.geometry.tools import IsolatedRasterBackend
        remote=self
        class Worker:
            def request(self,path,timeout):
                result=remote.call('rooms',Path(path).read_bytes(),timeout)
                expected=manifest()['models']['raster2seq']
                for key in ('checkpoint_sha256','source_tree_sha256','cuda_extensions','worker_sha256'):
                    if result[key]!=expected[key]:raise ValueError('Remote room-model proof changed: '+key)
                return result
            def close(self):pass
        class Backend(IsolatedRasterBackend):
            def readiness(self):
                report=manifest()['models']['raster2seq']
                identity=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()
                return {'ready':True,'model_identity':identity}
            def make_worker(self):return Worker()
        return Backend(root,folder)

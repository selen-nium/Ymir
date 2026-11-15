"""Backend entry point for an isolated, reference-free Lens job."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
from dataclasses import asdict

from .registry import Registry

ROOT=Path(os.environ.get('YMIR_STOA_ROOT',Path(__file__).resolve().parents[2])).resolve()


def _sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):digest.update(block)
    return digest.hexdigest()


def _atomic_new(path,value):
    """Publish complete JSON exactly once, without replacing an existing receipt."""
    path=Path(path);fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        # Modal volumes do not support hard links. Claim the destination once,
        # then atomically rename the complete temporary file. A crash after the
        # claim leaves it reserved rather than allowing a later replay.
        claim=path.with_name('.'+path.name+'.claim')
        handle=os.open(claim,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(handle)
        if path.exists():raise FileExistsError(str(path))
        os.rename(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def _implementation(root):
    code_base=Path(__file__).resolve().parents[1]
    paths=list((code_base/'stoa_lens').glob('*.py'))
    # Native extraction is independently owned and may change between jobs.
    # Record the actual adapter/helper bytes used by this job, without editing
    # or freezing another session's worktree.
    for package in ('stoa_native_geometry', 'stoa_vista_specialists/geometry'):
        paths += list((code_base/package).rglob('*.py'))
    # The inherited geometry and VISTA adapters are execution dependencies, not labels.
    paths += [code_base/'stoa_vista_specialists/specialists/detectors.py',code_base/'stoa_vista_specialists/geometry/tools.py',
              code_base/'stoa_vista_specialists/pacing.py',code_base/'stoa_vista_specialists/protocol.py',
              code_base/'stoa_vista_specialists/json_response.py',code_base/'stoa_vista_specialists/text/association.py',
              code_base/'stoa_room_area_specialists/raster.py',code_base/'stoa_room_area_specialists/raster_worker.py',
              code_base/'stoa_room_area_specialists/native.py',code_base/'stoa_room_area_specialists/adapter.py',
              code_base/'stoa/vision.py',code_base/'stoa/packets.py']
    return {"src/"+str(p.relative_to(code_base)):_sha(p) for p in sorted(set(paths)) if p.is_file()}


def _model_manifest(root,detector_injected,geometry_injected,configuration):
    models={'orchestrator':configuration['orchestrator'], 'untrained_detector':configuration['detector']}
    from .cloud import enabled,manifest as cloud_manifest
    if enabled() and not (detector_injected and geometry_injected):
        verified=cloud_manifest()
        models.update(object_detector=verified['models']['dfine'],room_geometry=verified['models']['raster2seq'])
        return models
    if detector_injected:
        models['object_detector']={'injected':True}
    else:
        from .dfine import CHECKPOINT,CHECKPOINT_SHA256,verified_head_policy
        actual=_sha(CHECKPOINT)
        if actual!=CHECKPOINT_SHA256:raise ValueError('D-FINE checkpoint hash mismatch')
        models['object_detector']={'model':'d-fine-l-junction-ema','checkpoint_path':str(CHECKPOINT),
                                   'checkpoint_sha256':actual,'head_policy':verified_head_policy()}
    if geometry_injected:
        models['room_geometry']={'injected':True}
    else:
        path=root/'data/room-area-specialists/raster/readiness.json'
        if not path.is_file():raise ValueError('Raster2Seq readiness receipt unavailable')
        ready=json.loads(path.read_text())
        if not ready.get('ready'):raise ValueError('Raster2Seq readiness receipt not ready')
        models['room_geometry']={'model':'Raster2Seq','readiness_sha256':_sha(path),'model_identity':ready.get('model_identity'),
                                 'checkpoint_sha256':ready.get('checkpoint_sha256'),'source_commit':ready.get('source_commit'),
                                 'verification':'recorded readiness; prediction backend revalidates assets on use'}
    return models


class Cancelled(RuntimeError):
    """Cooperative cancellation at an operation boundary; never retries work."""


def run(pages,question,directory,*,root=None,budget=None,transport=None,detector=None,geometry=None,limits=None,
        progress=None,conversation_context=None,cancelled=None,agent='general',count_scope=None,configuration=None):
    """Run one immutable job. Caller-provided resources are closed on exit."""
    from .model_config import resolve,validate
    configuration=validate(configuration if configuration is not None else resolve())
    if not isinstance(question,str) or not question.strip():raise ValueError('A nonempty question is required')
    if agent not in ('general','counter','rooms','compact_counter'):raise ValueError('Unknown Lens agent')
    if agent=='compact_counter' and count_scope is None:raise ValueError('compact_counter requires an explicit count scope')
    if not isinstance(pages,(list,tuple)) or not pages:raise ValueError('At least one page is required')
    if count_scope is not None:
        if agent not in ('general','compact_counter') or not isinstance(count_scope,dict) or set(count_scope)-{'page_index','target_classes','description','backend'}:
            raise ValueError('Bound counting requires a valid explicit scope and the general agent')
        if count_scope.get('page_index')!=0 or type(count_scope.get('page_index')) is not int or len(pages)!=1:
            raise ValueError('Bound counting requires page_index 0 and exactly one page')
        if count_scope.get('backend','routed') not in ('routed','argon_atomic'):
            raise ValueError('Bound backend must be routed or explicit argon_atomic')
        classes=count_scope.get('target_classes');description=count_scope.get('description')
        if not isinstance(classes,list) or not classes or any(not isinstance(c,str) or not c.strip() for c in classes) or not isinstance(description,str) or not description.strip():
            raise ValueError('Bound scope requires nonempty target classes and description')
    if transport is None and budget is None:raise ValueError('A durable LensBudget is required for metered transport')
    from .contracts import Limits
    if limits is None:limits=Limits()
    elif isinstance(limits,dict):limits=Limits(**limits)
    elif not isinstance(limits,Limits):raise ValueError('Invalid Lens limits')
    context=conversation_context or []
    if not isinstance(context,list) or len(context)>6 or any(not isinstance(t,dict) or not isinstance(t.get('question'),str) or not isinstance(t.get('answer'),dict) for t in context):
        raise ValueError('Conversation context requires at most six completed question/answer turns')
    root=Path(root or ROOT).resolve();folder=Path(directory).resolve()
    folder.mkdir(parents=True,exist_ok=False)
    owned=[];result=None;failure=None;cleanup_errors=[]
    try:
        registry=Registry(pages,folder/'views',root=root)
        agent_profile=None
        if agent in ('counter','rooms'):
            from .agents import profile,UnusedTools
            agent_profile=profile(agent,registry)
        if transport is None:
            from .transport import OpenRouterTransport
            transport=OpenRouterTransport(root,folder,budget,configuration=configuration)
        if getattr(transport,'configuration',configuration)!=configuration:raise ValueError('Transport does not match recorded routing snapshot')
        from .cloud import enabled,RemoteModels
        atomic_bound=bool(count_scope and count_scope.get('backend','routed')=='argon_atomic')
        remote=RemoteModels(budget,case_id=folder.name) if enabled() and not atomic_bound else None
        if atomic_bound and detector is None:
            from .bound_counter import ArgonAtomicDetector
            detector=ArgonAtomicDetector(transport.argon(),folder/'detections');detector_injected=True
        elif detector is None and agent=='rooms':
            detector=UnusedTools();detector_injected=True
        elif detector is None:
            from .dfine import DFine
            from .detection import DetectionService
            detector=DetectionService(DFine(runner=remote.detect) if remote else DFine(),transport.argon(),folder/'detections')
            detector_injected=False
        else:detector_injected=True
        if count_scope and not atomic_bound:
            from .bound_counter import RoutedBoundDetector
            from .routing import ClassRouter
            router=ClassRouter();canonical=[router.route(c)['canonical_class'] for c in count_scope['target_classes']]
            detector=RoutedBoundDetector(detector,canonical)
        owned.append(('detector',detector))
        if geometry is None and (agent=='counter' or count_scope is not None):
            from .agents import UnusedTools
            geometry=UnusedTools();geometry_injected=True
        elif geometry is None:
            from .geometry import Geometry
            geometry=Geometry(root,folder/'geometry',registry,**({'raster_factory':remote.raster} if remote else {}))
            geometry_injected=False
        else:
            geometry_injected=True
            if callable(geometry) and not hasattr(geometry,'predict_room_polygons'):
                geometry=geometry(root,folder/'geometry',registry)
        owned.append(('geometry',geometry))
        source_views=[{k:v for k,v in view.items() if k not in ('path','pdf_path')} for view in registry.initial_views]
        sources=[dict(registry.sources[v['page_id']]) for v in registry.initial_views]
        manifest={'schema':'stoa-lens-job-v1','question':question,'sources':sources,'initial_views':source_views,
                  'limits':asdict(limits),'implementation_sha256':_implementation(root),
                  'models':_model_manifest(root,detector_injected,geometry_injected or bool(agent_profile and 'predict_room_polygons' not in agent_profile.tools),configuration),
                  'routing_mode':configuration['routing_mode'],'configuration':configuration,
                  'text_reader':'native PDF only','reference_annotations_provided':False}
        if count_scope:
            from .routing import ClassRouter
            router=ClassRouter();canonical=[router.route(c)['canonical_class'] for c in count_scope['target_classes']]
            if len(canonical)!=len(set(canonical)):raise ValueError('Duplicate canonical target classes')
            actual_backend='argon' if atomic_bound else detector.backend
            manifest['count_scope']={'page_id':registry.initial_views[0]['page_id'],'page_index':0,'target_classes':canonical,
                                     'description':count_scope['description'],'mode':count_scope.get('backend','routed'),'backend':actual_backend}
            manifest['models']['room_geometry']={'used':False}
            if atomic_bound:manifest['models']['object_detector']=dict(configuration['detector'],mode='explicit_argon_atomic')
        if agent_profile:
            manifest['agent_profile']=agent_profile.manifest()
            if agent=='rooms':manifest['models']['object_detector']={'used':False}
            else:manifest['models']['room_geometry']={'used':False}
            if agent=='rooms' and 'predict_room_polygons' not in agent_profile.tools:
                manifest['models']['room_geometry']={'model':'verified native geometry','raster_used':False,'injected':geometry_injected}
        if context:manifest['conversation_context']=context
        _atomic_new(folder/'request-manifest.json',manifest)
        from .harness import LensHarness
        hooks={'configuration':configuration}
        if progress is not None:hooks['progress']=progress
        if context:hooks['conversation_context']=context
        if cancelled is not None:hooks['cancelled']=cancelled
        if agent_profile:hooks['agent_profile']=agent_profile
        planning=configuration.get('planning',{}).get('enabled',False)
        if count_scope:
            from .bound_counter import BoundCounter
            if planning:
                from .planning import PlanningCounter
                counter=PlanningCounter
            else:counter=BoundCounter
            result=counter(registry,detector,transport,folder,limits=limits,**hooks,
                page_id=manifest['count_scope']['page_id'],target_classes=manifest['count_scope']['target_classes'],
                description=count_scope['description'],backend=actual_backend).run(question)
        else:
            if planning:
                from .planning import PlanningHarness
                harness=PlanningHarness
            else:harness=LensHarness
            result=harness(registry,detector,geometry,transport,folder,limits=limits,**hooks).run(question)
        if not isinstance(result,dict):raise ValueError('Lens harness must return a result object')
    except BaseException as exc:
        failure=exc
    finally:
        for name,obj in reversed(owned):
            closer=getattr(obj,'close',None)
            if callable(closer):
                try:closer()
                except Exception as exc:cleanup_errors.append({'resource':name,'error_type':type(exc).__name__})
    if failure is not None:
        _atomic_new(folder/'service-failure.json',{'status':'failed','error_type':type(failure).__name__,'cleanup_errors':cleanup_errors})
        raise failure
    _atomic_new(folder/'service-result.json',{'status':'completed' if not cleanup_errors else 'cleanup_failed',
                                               'result':result,'cleanup_errors':cleanup_errors})
    if cleanup_errors:raise RuntimeError('Lens resource cleanup failed')
    return result

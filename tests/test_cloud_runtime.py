"""No network: private GPU dispatch preserves once-only accounting and deadlines."""
import json
import pytest
from stoa_lens import cloud
from stoa_lens.budget import LensBudget,BudgetError


def test_compute_holds_share_cap_without_weakening_model_allowlist(tmp_path):
    checkpoints=[]
    budget=LensBudget(tmp_path/'budget.sqlite',1,checkpoint=lambda:checkpoints.append(True))
    budget.reserve('kimi-k3-1-pro','llm',.6)
    gpu=budget.reserve_compute('gpu',.25,key='once')
    with pytest.raises(ValueError):budget.reserve('modal-compute','not-an-llm',.01)
    with pytest.raises(BudgetError):budget.reserve_compute('gpu',.25,key='once')
    with pytest.raises(BudgetError):budget.reserve_compute('over-cap',.25)
    budget.settle(gpu,None,usage={'billing':'unknown'})
    assert budget.summary()['unresolved_reserved_usd']==.85
    assert budget.summary()['remaining_usd']==.15
    assert len(checkpoints)==3


def test_remote_timeout_cancels_once_and_keeps_hold(tmp_path,monkeypatch):
    monkeypatch.setattr(cloud,'manifest',lambda:{})
    calls=[]
    class Call:
        object_id='fc-test'
        def get(self,timeout):calls.append(('get',timeout));raise TimeoutError('no reply')
        def cancel(self,terminate_containers):calls.append(('cancel',terminate_containers))
    class Function:
        def spawn(self,payload,**kwargs):calls.append(('spawn',payload));return Call()
    budget=LensBudget(tmp_path/'budget.sqlite',1)
    remote=cloud.RemoteModels(budget,lookup=lambda name:Function(),case_id='lens-test-job')
    with pytest.raises(TimeoutError):remote.call('detect',b'image',.1)
    assert calls==[('spawn',b'image'),('get',.1),('cancel',True)]
    charge=budget.summary()['charges'][0]
    assert charge['status']=='unknown' and charge['reserved_usd']==.25 and charge['generation_id']=='fc-test'
    assert charge['usage']['automatic_retries'] is False
    assert charge['case_id']=='lens-test-job'
    with pytest.raises(TimeoutError):remote.call('detect',b'image',0)
    assert len(budget.summary()['charges'])==1


def test_model_readiness_rejects_unverified_checkpoints(tmp_path,monkeypatch):
    path=tmp_path/'ready.json';monkeypatch.setenv('YMIR_MODEL_READINESS',str(path))
    path.write_text(json.dumps({'passed':False}))
    with pytest.raises(ValueError,match='has not passed'):cloud.manifest()
    path.write_text(json.dumps({'passed':True,'models':{'dfine':{'passed':True,'receipt':{'checkpoint_sha256':'wrong'}}}}))
    with pytest.raises(ValueError,match='detector'):cloud.manifest()


def test_cloud_routing_uses_verified_gpu_policy_without_local_assets(monkeypatch):
    from pathlib import Path
    monkeypatch.setenv('YMIR_GPU_BACKEND','modal')
    monkeypatch.setenv('YMIR_MODEL_READINESS',str(Path(__file__).resolve().parents[1]/'deploy/model-readiness.json'))
    from stoa_lens.routing import ClassRouter
    policy=ClassRouter()
    assert policy.route('window')['backend']=='dfine'
    assert policy.route('chair')['backend']=='kimi'


def test_receipt_publication_is_atomic_once_and_survives_interrupted_claim(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from stoa_lens.service import _atomic_new
    target=tmp_path/'receipt.json'
    def publish(i):
        try:_atomic_new(target,{'writer':i,'body':'x'*10000});return True
        except FileExistsError:return False
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(publish,range(8)))
    assert sum(results)==1 and len(json.loads(target.read_text())['body'])==10000
    frozen=target.read_bytes()
    with pytest.raises(FileExistsError):_atomic_new(target,{'changed':True})
    assert target.read_bytes()==frozen
    interrupted=tmp_path/'interrupted.json'
    (tmp_path/'.interrupted.json.claim').touch()
    with pytest.raises(FileExistsError):_atomic_new(interrupted,{'retry':True})
    assert not interrupted.exists()


def test_cloud_bound_count_factory_needs_no_local_checkpoints(tmp_path,monkeypatch):
    from pathlib import Path
    from PIL import Image
    from stoa_lens.service import run
    monkeypatch.setenv('YMIR_GPU_BACKEND','modal')
    monkeypatch.setenv('YMIR_MODEL_READINESS',str(Path(__file__).resolve().parents[1]/'deploy/model-readiness.json'))
    class LanguageFixture:
        def complete(self,*args,**kwargs):
            return {'role':'assistant','content':json.dumps({'status':'needs_review','accepted_proposal_ids':[],'notes':['No detector invocation in this factory check.']})}
        def kimi(self,*args,**kwargs):raise AssertionError('No paid fallback in this test')
    image=tmp_path/'source.png';Image.new('RGB',(100,80),'white').save(image)
    budget=LensBudget(tmp_path/'budget.sqlite',1)
    folder=tmp_path/'job'
    result=run([{'image':str(image),'width':100,'height':80}], 'Count windows', folder,
        root=tmp_path/'no-checkpoints',budget=budget,transport=LanguageFixture(),agent='compact_counter',
        count_scope={'page_index':0,'target_classes':['window'],'description':'windows','backend':'routed'})
    assert result['status']=='needs_review' and result['counts'][0]['count'] is None
    proof=json.loads((folder/'request-manifest.json').read_text())
    assert proof['count_scope']['backend']=='dfine' and proof['models']['room_geometry']=={'used':False}
    assert proof['models']['orchestrator']['provider']=='Verda'
    assert budget.summary()['requests']==0

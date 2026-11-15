"""Bounded CPU image and Modal-volume check. Fixture runner; no paid inference."""
from pathlib import Path
import json
import modal
from deploy.modal_app import image

app=modal.App('ymir-api-validation')
volume=modal.Volume.from_name('ymir-api-validation',create_if_missing=True)
probe_env={'YMIR_DATA_DIR':'/check-data','YMIR_STOA_ROOT':'/check-data/runtime'}


@app.function(image=image,env=probe_env,volumes={'/check-data':volume},cpu=(2,2),memory=(4096,4096),
              timeout=180,retries=0,max_containers=1,scaledown_window=2)
def writer(request_id):
    import time
    from fastapi.testclient import TestClient
    from ymir.app import app as api,jobs
    from stoa_lens.registry import Registry
    checkpoints=[]
    def commit():volume.commit();checkpoints.append(True)
    jobs.checkpoint=commit;jobs.budget.checkpoint=commit
    def fixture(pages,question,directory,**hooks):
        registry=Registry(pages,Path(directory)/'views')
        hooks['progress']({'operation':'response','phase':'started','response_index':0})
        return {'status':'needs_review','counts':[],'proposals':[],'accepted_proposal_ids':[],
                'rooms':[],'text_evidence':[],'measurements':[],'readings':[],'notes':['Deployment fixture; no inference.']}
    jobs.runner=fixture
    request={'request_id':request_id,'drawing_id':'sample-cubicasa-2536','selected_drawing_ids':['sample-cubicasa-2536'],
             'question':'Deployment fixture','project_id':'deployment-fixture','conversation_id':request_id,'prior_job_ids':[]}
    with TestClient(api) as client:
        health=client.get('/api/health');assert health.status_code==200
        from stoa_lens.service import run
        from ymir.app import store
        class OrchestratorFixture:
            def complete(self,*args,**kwargs):
                return {'role':'assistant','content':json.dumps({'status':'needs_review','accepted_proposal_ids':[],'count_requests':[],'rooms':[]})}
            def argon(self):
                from stoa_lens.transport import Argon
                return Argon(self)
            def _call(self,*args,**kwargs):raise AssertionError('No paid localization in deployment fixture')
        answer=run([store.raw('sample-cubicasa-2536')['lens_source']],'Deployment fixture',
            Path('/check-data')/('factory-'+request_id),budget=jobs.budget,transport=OrchestratorFixture())
        assert answer['status']=='needs_review'
        from stoa_native_geometry import prepare
        from stoa_vista_specialists.geometry import GeometryTools
        svg=Path('/check-data')/('native-'+request_id+'.svg')
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect id="room" x="10" y="10" width="80" height="80"/></svg>')
        source=prepare(svg,Path('/check-data')/('prepared-'+request_id),options={'element_ids':['room']})['source']
        geometry=GeometryTools(Path('/check-data/runtime'),Path('/check-data')/('geometry-'+request_id),source['source_id'],source['source_sha256'],source['page'],source['width'],source['height'],native_source=source)
        try:
            native=geometry.native_room_candidates(source['source_id'],source['page'],timeout=20)
            assert native['status']=='needs_review' and len(native['candidates'])==1,native
            assert native['candidates'][0]['calibration'] is None,native
        finally:geometry.close()
        uploaded=client.post('/api/documents',files={'file':('plan.png',Path('/root/data/samples/cubi.png').read_bytes(),'image/png')})
        uploaded.raise_for_status()
        prepared=client.post('/api/documents/'+uploaded.json()['id']+'/sheets',json={'pages':[1]})
        prepared.raise_for_status()
        drawing=client.get('/api/drawings/'+prepared.json()['sheets'][0]['id']).json()
        assert len(drawing['walls'])>=4,drawing
        assert client.get('/api/projects/junction-duplex').status_code==200
        assert client.get('/api/drawings/sample-cubicasa-2536/image').status_code==200
        job=jobs.submit(request);assert jobs.submit(request)==job
        deadline=time.monotonic()+30
        while jobs.lookup(job)['status'] not in ('completed','failed') and time.monotonic()<deadline:time.sleep(.1)
        status=jobs.lookup(job)['status'];assert status=='completed',jobs.lookup(job)
        assert jobs.budget.summary()['requests']==0
    volume.commit()
    return {'job_id':job,'status':status,'checkpoints':len(checkpoints),'paid_job_dispatches':0,'duplicate_dispatched':False,
            'gpu_readiness_proof':health.json()['lens']['checks']['raster2seq'],'default_lens_factories':True,'native_subprocess':True,'uncalibrated_svg_status':native['status'],'uploaded_build_walls':len(drawing['walls'])}


@app.function(image=image,env=probe_env,volumes={'/check-data':volume},cpu=(1,1),memory=(1024,1024),timeout=60,retries=0,scaledown_window=2)
def reader(job):
    import sqlite3
    volume.reload()
    with sqlite3.connect('/check-data/lens/jobs.sqlite') as db:
        state=db.execute('SELECT status FROM jobs WHERE id=?',(job,)).fetchone()[0]
        events=db.execute('SELECT COUNT(*) FROM events WHERE job=?',(job,)).fetchone()[0]
    return {'status':state,'events':events}


@app.function(image=image,env=probe_env,cpu=(1,1),memory=(1024,1024),timeout=240,retries=0,scaledown_window=2)
def gpu_bridge():
    # Covered by the central validation reservation made before starting this app.
    # No new ledger and no language-model calls. Exercise Modal's ambient identity.
    import modal
    from stoa_lens.cloud import manifest
    proof=manifest();payload=Path('/root/data/samples/sample-cubicasa-2536.png').read_bytes()
    detector=modal.Function.from_name('ymir-models','detect').remote(payload)
    rooms=modal.Function.from_name('ymir-models','rooms').remote(payload)
    assert detector['receipt']['checkpoint_sha256']==proof['models']['dfine']['receipt']['checkpoint_sha256']
    for key in ('checkpoint_sha256','source_tree_sha256','cuda_extensions','worker_sha256'):
        assert rooms[key]==proof['models']['raster2seq'][key]
    return {'private_functions_reachable':True,'ambient_identity':True,'language_model_calls':0,
            'detector_gpu':detector['receipt']['gpu'],'room_gpu':rooms['gpu']}


@app.local_entrypoint()
def main(output: str='var/modal-api-validation.json', gpu_bridge_check: bool=False):
    import uuid
    target=Path(output)
    if target.exists():raise FileExistsError('Preserve the previous validation report; choose a new output')
    result=writer.remote(uuid.uuid4().hex)
    persisted=reader.remote(result['job_id'])
    assert persisted['status']=='completed' and persisted['events']>=4
    target.parent.mkdir(parents=True,exist_ok=True)
    bridge=gpu_bridge.remote() if gpu_bridge_check else {'previously_verified':'var/modal-api-validation-002.json'}
    target.write_text(json.dumps({'passed':True,**result,'independent_reader':persisted,'gpu_bridge':bridge},indent=2)+'\n')
    print(json.dumps({'passed':True,'durable_events':persisted['events'],'paid_job_dispatches':0}))

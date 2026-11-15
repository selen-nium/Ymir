"""CPU API on Modal; private GPU functions in modal_models.py; LLMs on Verda.

Deploy models and pass their smoke receipt before deploying this API. See docs/hosting.md.
"""
from __future__ import annotations
import os
from pathlib import Path
import modal

ROOT=Path(__file__).resolve().parents[1]
app=modal.App('ymir')
data=modal.Volume.from_name('ymir-data',create_if_missing=False)
requirements=[line.strip() for line in (ROOT/'requirements.txt').read_text().splitlines()
              if line.strip() and not line.lstrip().startswith(('#','-e ','torch'))] if modal.is_local() else []
if modal.is_local():
    weights=ROOT/'weights/wallnet.pt'
    if not weights.is_file() or weights.stat().st_size<1_000_000:
        raise RuntimeError('Run git lfs pull before deploying: WallNet weights are missing or still an LFS pointer.')
receipt=ROOT/'deploy/model-readiness.json'
if modal.is_local() and not receipt.is_file():
    raise RuntimeError('Run the Modal GPU smoke check and publish deploy/model-readiness.json first.')

image=(modal.Image.debian_slim(python_version='3.12').apt_install('libgl1','libglib2.0-0')
    .uv_pip_install('torch==2.10.0','torchvision==0.25.0',index_url='https://download.pytorch.org/whl/cpu')
    .uv_pip_install(*requirements,'modal==1.6.4')
    .add_local_dir(ROOT/'vendor/stoa-runtime','/opt/ymir-runtime',copy=True,
                   ignore=['**/__pycache__/**','**/*.egg-info/**','**/build/**'])
    .uv_pip_install('/opt/ymir-runtime')
    .env({'PYTHONPATH':'/root/backend','YMIR_DATA_DIR':'/data','YMIR_STOA_ROOT':'/data/runtime',
          'YMIR_WEIGHTS_DIR':'/root/weights','YMIR_GPU_BACKEND':'modal','YMIR_MODEL_READINESS':'/root/model-readiness.json'})
    .add_local_file(receipt,'/root/model-readiness.json')
    .add_local_dir(ROOT/'weights','/root/weights')
    .add_local_dir(ROOT/'data/samples','/root/data/samples')
    .add_local_dir(ROOT/'backend','/root/backend',ignore=['**/__pycache__/**']))


# Function environment values bypass Dockerfile ENV escaping (notably regex \.).
@app.function(image=image,volumes={'/data':data},cpu=(2,2),memory=(4096,4096),timeout=1800,
              env={'YMIR_CORS_ORIGINS':os.environ.get('YMIR_CORS_ORIGINS',''),
                   'YMIR_CORS_ORIGIN_REGEX':os.environ.get('YMIR_CORS_ORIGIN_REGEX','')},
              min_containers=1,max_containers=1,
              secrets=[modal.Secret.from_name('ymir-verda',required_keys=['VERDA_INFERENCE_API_KEY','VERDA_CLIENT_ID','VERDA_CLIENT_SECRET'])])
# Job streams are async and hold no worker thread, so many viewers can watch jobs at once.
# Uploads and builds still share the server's thread pool and the container's two CPUs.
@modal.concurrent(max_inputs=128)
@modal.asgi_app()
def web():
    import threading
    import time
    import uuid
    if not Path('/data/lens/budget.sqlite').is_file():
        raise RuntimeError('Import the existing Ymir state and $25 ledger before starting the hosted API.')
    from ymir.app import app as api,jobs
    from ymir.writer_lease import WriterLease
    from stoa_lens.service import ROOT as runtime_root
    from stoa_lens.transport import refresh_pricing

    # A single warm CPU container owns all SQLite writers and the inference thread, but a redeploy
    # or crash can briefly run two containers on the same volume. Only the lease holder commits or
    # runs jobs; the other serves reads and answers writes with 503 until it takes over.
    lease=WriterLease(modal.Dict.from_name('ymir-writer-lease',create_if_missing=True),
                      os.environ.get('MODAL_TASK_ID') or uuid.uuid4().hex)

    def commit():
        if lease.ready.is_set():data.commit()

    def take_over():
        # See everything the previous writer committed before writing anything.
        for attempt in range(10):
            try:data.reload();break
            except Exception:
                if attempt==9:raise
                time.sleep(1)
        jobs.start()  # marks jobs the previous container left running as interrupted
        data.commit()
        def pricing():
            try:refresh_pricing(runtime_root,runtime_root/'data/stoa-lens/provider')
            except Exception:return  # readiness reports unavailable; dispatch rechecks catalog freshness
            commit()
        threading.Thread(target=pricing,name='pricing-refresh',daemon=True).start()

    def step_down():
        jobs.stop()

    jobs.checkpoint=commit
    jobs.budget.checkpoint=commit
    api.state.start_jobs=False
    api.state.write_gate=lambda:None if lease.ready.is_set() else 'Ymir is finishing an update. Try again in a minute.'
    stop=threading.Event()
    def shutdown():
        stop.set();lease.release()
    api.state.after_shutdown=[shutdown]
    threading.Thread(target=lease.run,args=(take_over,step_down,stop),name='writer-lease',daemon=True).start()

    @api.middleware('http')
    async def persist_writes(request,call_next):
        response=await call_next(request)
        if request.method in ('POST','DELETE') and lease.ready.is_set():
            await data.commit.aio()
        return response
    return api

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
          'YMIR_WEIGHTS_DIR':'/root/weights','YMIR_GPU_BACKEND':'modal','YMIR_MODEL_READINESS':'/root/model-readiness.json',
          'YMIR_CORS_ORIGINS':os.environ.get('YMIR_CORS_ORIGINS',''),
          'YMIR_CORS_ORIGIN_REGEX':os.environ.get('YMIR_CORS_ORIGIN_REGEX','')})
    .add_local_file(receipt,'/root/model-readiness.json')
    .add_local_dir(ROOT/'weights','/root/weights')
    .add_local_dir(ROOT/'data/samples','/root/data/samples')
    .add_local_dir(ROOT/'backend','/root/backend',ignore=['**/__pycache__/**']))


@app.function(image=image,volumes={'/data':data},cpu=(2,2),memory=(4096,4096),timeout=1800,
              min_containers=1,max_containers=1,
              secrets=[modal.Secret.from_name('ymir-verda',required_keys=['VERDA_INFERENCE_API_KEY','VERDA_CLIENT_ID','VERDA_CLIENT_SECRET'])])
@modal.concurrent(max_inputs=16)
@modal.asgi_app()
def web():
    if not Path('/data/lens/budget.sqlite').is_file():
        raise RuntimeError('Import the existing Ymir state and $25 ledger before starting the hosted API.')
    from ymir.app import app as api,jobs
    from stoa_lens.service import ROOT as runtime_root
    from stoa_lens.transport import refresh_pricing
    # A single warm CPU container owns all SQLite writers and the inference thread.
    # GPU functions scale to zero independently. No browser connection is required.
    jobs.checkpoint=data.commit
    jobs.budget.checkpoint=data.commit
    try:refresh_pricing(runtime_root,runtime_root/'data/stoa-lens/provider')
    except Exception:pass  # readiness reports unavailable; dispatch rechecks catalog freshness
    data.commit()

    @api.middleware('http')
    async def persist_writes(request,call_next):
        response=await call_next(request)
        if request.method in ('POST','DELETE'):
            await data.commit.aio()
        return response
    return api

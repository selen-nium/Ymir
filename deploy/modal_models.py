"""Private GPU functions; language models remain on OpenRouter.

First run scripts/prepare_modal_assets.py. Then smoke-test with
python -m modal run deploy/modal_models.py --output var/modal-smoke.json
or deploy with python -m modal deploy deploy/modal_models.py.
"""
from pathlib import Path
import json
import hashlib
import os
import modal

ROOT = Path(__file__).resolve().parents[1]
app = modal.App('ymir-models')
assets = modal.Volume.from_name('ymir-models-v1', create_if_missing=False)
RUNTIME = ROOT/'vendor/stoa-runtime/src'
IGNORE = ['**/__pycache__/**', '**/*.pyc']
ASSET_SHA = hashlib.sha256((ROOT/'deploy/model-assets.json').read_bytes()).hexdigest() if modal.is_local() else os.environ.get('YMIR_MODEL_BUNDLE_SHA256','')


def compile_raster(expected_sha):
    import os
    import shutil
    import subprocess
    from pathlib import Path
    import hashlib
    import json
    proof=Path('/assets/verified-manifest.json').read_bytes()
    if hashlib.sha256(proof).hexdigest()!=expected_sha:raise ValueError('Uploaded model bundle changed')
    for relative,expected in json.loads(proof)['files'].items():
        if not relative.startswith('raster/upstream/'):continue
        if hashlib.sha256((Path('/assets')/relative).read_bytes()).hexdigest()!=expected:
            raise ValueError('Raster source changed: '+relative)
    # Compile into the image, preserving the immutable uploaded source bundle.
    target = Path('/opt/raster/data/room-area-specialists/raster/upstream')
    shutil.copytree('/assets/raster/upstream', target)
    env = dict(os.environ, TORCH_CUDA_ARCH_LIST='8.6', MAX_JOBS='2', CC='/usr/bin/gcc', CXX='/usr/bin/g++', CUDA_HOME='/usr/local/cuda')
    for folder in ('models/ops', 'diff_ras'):
        subprocess.run(['python', 'setup.py', 'build_ext', '--inplace'], cwd=target/folder, env=env, check=True)


dfine_image = (modal.Image.debian_slim(python_version='3.12')
    .apt_install('libgl1','libglib2.0-0')
    .uv_pip_install('torch==2.10.0','torchvision==0.25.0','numpy==2.3.5','scipy==1.17.1',
                    'Pillow==12.1.1','PyYAML==6.0.2','faster-coco-eval==1.7.2','calflops==0.3.2',
                    'transformers==5.3.0','loguru==0.7.3','tensorboard==2.20.0')
    .env({'PYTHONPATH':'/runtime','YMIR_STOA_ROOT':'/assets', 'HF_HUB_OFFLINE':'1'})
    .add_local_dir(RUNTIME,'/runtime',ignore=IGNORE))

raster_image = (modal.Image.from_registry('nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04',add_python='3.10')
    .apt_install('g++','libgl1','libglib2.0-0','libcairo2-dev')
    .pip_install('torch==2.3.1','torchvision==0.18.1',index_url='https://download.pytorch.org/whl/cu118')
    .pip_install('numpy==1.26.4','matplotlib==3.6.2','imageio==2.19.3','scipy==1.11.4',
                 'fvcore==0.1.5.post20221221','cloudpickle==2.1.0','omegaconf==2.2.2','fairscale==0.4.6',
                 'timm==0.5.4','shapely==1.8.2','tqdm==4.64.0','pycocotools','descartes','webcolors',
                 'drawsvg','cairosvg','plyfile','scikit-image==0.19.3','svgpathtools==1.2.4',
                 'svgwrite==1.2.1','opencv-python==4.10.0.84','einops','plotly','yacs',
                 'setuptools==68.2.2','wheel','ninja')
    .run_function(compile_raster,args=[ASSET_SHA],volumes={'/assets':assets},gpu='A10G',cpu=2,memory=8192,timeout=600)
    .env({'PYTHONPATH':'/runtime','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','OMP_NUM_THREADS':'1','YMIR_MODEL_BUNDLE_SHA256':ASSET_SHA})
    .add_local_dir(RUNTIME,'/runtime',ignore=IGNORE))


@app.function(image=dfine_image,gpu='A10G',cpu=(2,2),memory=(8192,8192),timeout=180,
              startup_timeout=180,retries=0,max_containers=1,min_containers=0,scaledown_window=2,
              volumes={'/assets':assets})
def detect(image_bytes: bytes, object_class: str='window', threshold: float=.25):
    import tempfile
    import time
    from pathlib import Path
    from stoa_lens.dfine_worker import Engine
    from stoa_lens.dfine import CHECKPOINT, UPSTREAM
    global _detector
    started=time.monotonic()
    if '_detector' not in globals(): _detector=Engine(CHECKPOINT,UPSTREAM)
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'input.png';path.write_bytes(image_bytes)
        value,receipt=_detector.predict(path,object_class,threshold)
    return {'value':value,'receipt':{**receipt,'hosting':'Modal','elapsed_seconds':time.monotonic()-started}}


@app.function(image=raster_image,gpu='A10G',cpu=(2,2),memory=(8192,8192),timeout=180,
              startup_timeout=180,retries=0,max_containers=1,min_containers=0,scaledown_window=2,
              volumes={'/assets':assets})
def rooms(image_bytes: bytes):
    import hashlib
    import tempfile
    from pathlib import Path
    import sys
    from stoa_room_area_specialists.raster import _Worker, canonical_digest
    import stoa_room_area_specialists.raster_worker as worker_module
    # Keep checkpoint verification independent of the CPU API and model output.
    checkpoint=Path('/assets/raster/assets/checkpoint.pth')
    hasher=hashlib.sha256()
    with checkpoint.open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):hasher.update(block)
    digest=hasher.hexdigest()
    if digest!='f7c0ef9379aa8cf11349da8b2179214e0d495af758383984a2361a1309aab3b7':
        raise ValueError('Raster2Seq checkpoint hash mismatch')
    root=Path('/opt/raster');home=root/'data/room-area-specialists/raster'
    if not (home/'assets').exists(): (home/'assets').symlink_to('/assets/raster/assets',target_is_directory=True)
    if not (home/'env/bin/python').exists():
        (home/'env/bin').mkdir(parents=True,exist_ok=True)
        (home/'env/bin/python').symlink_to(sys.executable)
    global _rooms, _raster_proof
    if '_rooms' not in globals():
        def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
        upstream=home/'upstream'
        sources={str(p.relative_to(upstream)):sha(p) for p in sorted(upstream.rglob('*.py'))}
        extensions={str(p.relative_to(upstream)):sha(p) for folder in ('models/ops','diff_ras') for p in sorted((upstream/folder).glob('*.so'))}
        if len(extensions)!=2:raise ValueError('Both Raster2Seq CUDA extensions are required')
        _raster_proof={'source_commit':'d938188027cd4f2bed9f95c883e5cc6d80b507f5',
            'source_tree_sha256':canonical_digest(sources),'cuda_extensions':extensions,
            'worker_sha256':sha(Path(worker_module.__file__))}
        _rooms=_Worker(root)
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'input.png';path.write_bytes(image_bytes)
        result=_rooms.request(path,timeout=160)
    return {**result,**_raster_proof,'checkpoint_sha256':digest,'hosting':'Modal'}


@app.local_entrypoint()
def smoke(output: str='var/modal-smoke.json'):
    import hashlib
    import time
    path=ROOT/'data/samples/sample-cubicasa-2536.png'
    payload=path.read_bytes();started=time.time()
    report={'schema':'ymir-modal-smoke-v1','started_unix':started,'source_sha256':hashlib.sha256(payload).hexdigest(),
            'source':'CubiCasa 2536 cached source image; no reference annotations','automatic_retries':False,'models':{}}
    target=Path(output);target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x') as stream:json.dump(report,stream,indent=2)
    try:
        result=detect.remote(payload)
        assert result['value']['status']=='complete' and result['receipt']['model_state_loaded_strictly']
        report['models']['dfine']={'passed':True,'instances':len(result['value']['instances']),'receipt':result['receipt']}
        target.write_text(json.dumps(report,indent=2)+'\n')
        result=rooms.remote(payload)
        coverage=result['checkpoint_coverage']
        assert not coverage['missing_keys'] and not coverage['unexpected_keys'] and len(result['polygons'])==len(result['labels'])
        report['models']['raster2seq']={'passed':True,**result}
        report['passed']=True
    except BaseException as exc:
        report.update(passed=False,error=type(exc).__name__+': '+str(exc));raise
    finally:
        report['elapsed_seconds']=time.time()-started
        target.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'passed':report.get('passed'), 'models':list(report['models']),'report':str(target)}))

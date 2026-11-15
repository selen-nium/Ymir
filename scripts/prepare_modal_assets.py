"""Upload only pinned model code, weights and licences; no datasets or credentials."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import modal

ROOT=Path(__file__).resolve().parents[1]


def prepare(source, stage):
    if stage.exists():raise FileExistsError('Use a fresh staging directory')
    sources={
      'checkpoints/junction-focused-dfine-l-baseline-v1':source/'checkpoints/junction-focused-dfine-l-baseline-v1',
      'data/dfine-v5/upstream':source/'data/dfine-v5/upstream',
      'raster/upstream':source/'data/room-area-specialists/raster/upstream',
      'raster/assets':source/'data/room-area-specialists/raster/assets'}
    for dest,folder in sources.items():
        for path in sorted(folder.rglob('*')):
            rel=path.relative_to(folder)
            if not path.is_file() or any(p in {'.git','__pycache__','build','outputs','runs'} for p in rel.parts):continue
            if dest.startswith('checkpoints/'):
                keep=str(rel) in ('best.pth','best.json','recipe.json')
            elif dest=='raster/assets':keep=path.name in ('checkpoint.pth','config.json','LICENSE')
            else:keep=path.suffix in ('.py','.yml','.yaml','.txt','.cpp','.cu','.h','.hpp','.cuh','.json') or path.name.startswith('LICENSE')
            if dest=='raster/upstream' and rel.parts[0] in ('assets','data','data_preprocess','evaluations','eval_outputs'):keep=False
            if not keep:continue
            target=stage/dest/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    def digest(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    expected={'checkpoints/junction-focused-dfine-l-baseline-v1/best.pth':'0f76bfabb562647a98f34d5dd3dd0d240eb5ef5ff7c1072fe14a4c667147cb8d',
              'raster/assets/checkpoint.pth':'f7c0ef9379aa8cf11349da8b2179214e0d495af758383984a2361a1309aab3b7'}
    files={str(p.relative_to(stage)):digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}
    for name,value in expected.items():
        if files.get(name)!=value:raise ValueError('Pinned checkpoint mismatch: '+name)
    # Every copied Raster2Seq Python source must match the existing verified setup.
    ready=json.loads((source/'data/room-area-specialists/raster/readiness.json').read_text())
    if ready.get('ready') is not True:raise ValueError('Existing Raster2Seq setup is not verified')
    for name,value in files.items():
        if name.startswith('raster/upstream/') and name.endswith('.py'):
            original='data/room-area-specialists/'+name
            if ready['verified_files'].get(original)!=value:raise ValueError('Unverified Raster2Seq source: '+name)
    manifest={'schema':'ymir-modal-assets-v1','files':files}
    (stage/'verified-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (ROOT/'deploy/model-assets.json').write_text(json.dumps(manifest,indent=2)+'\n')
    volume=modal.Volume.from_name('ymir-models-v1',create_if_missing=True)
    # A second upload must be explicitly reviewed; never overwrite an existing bundle.
    with volume.batch_upload(force=False) as batch:batch.put_directory(stage,'/')
    print(json.dumps({'volume':'ymir-models-v1','files':len(files),'bytes':sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=ROOT.parent/'stoa');parser.add_argument('--stage',type=Path,default=ROOT/'var/modal-assets')
    args=parser.parse_args();prepare(args.source,args.stage)

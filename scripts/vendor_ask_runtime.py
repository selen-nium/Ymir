"""Snapshot the local Ask runtime's code dependencies, never data or credentials."""
import ast
import hashlib
import json
import shutil
import sys
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT.parent/'stoa'
TARGET=ROOT/'vendor/stoa-runtime'
if (TARGET/'PROVENANCE.json').exists():
    raise SystemExit('Runtime already vendored with reviewed Ymir adaptations. Refresh into a separate directory and reconcile those adaptations; never overwrite it in place.')

def module_path(root,name):
    path=root.joinpath(*name.split('.'))
    return path.with_suffix('.py') if path.with_suffix('.py').is_file() else path/'__init__.py' if (path/'__init__.py').is_file() else None

def closure(root,initial,local):
    pending=set(initial);found={}
    while pending:
        name=pending.pop()
        if name in found:continue
        path=module_path(root,name)
        if not path:continue
        found[name]=path
        parts=name.split('.')
        pending.update('.'.join(parts[:i]) for i in range(1,len(parts)) if '.'.join(parts[:i]) not in found)
        package=name if path.name=='__init__.py' else name.rpartition('.')[0]
        for node in ast.walk(ast.parse(path.read_text())):
            names=[]
            if isinstance(node,ast.Import):names=[a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom):
                base='.'.join(package.split('.')[:len(package.split('.'))-node.level+1]+([node.module] if node.module else [])) if node.level else node.module or ''
                names=[base]+[base+'.'+a.name for a in node.names]
            pending.update(n for n in names if local(n) and n not in found and module_path(root,n))
    return found

initial={'stoa_lens.'+p.stem for p in (SOURCE/'src/stoa_lens').glob('*.py') if p.stem!='__init__'}
initial.update(['stoa_native_geometry.backend','stoa_native_geometry.__main__','stoa_room_area_specialists.raster_worker','stoa_vista_specialists.geometry.native_worker'])
files=closure(SOURCE/'src',initial,lambda n:n=='stoa' or n.startswith(('stoa.','stoa_')))
for path in files.values():
    target=TARGET/'src'/path.relative_to(SOURCE/'src');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
vista=SOURCE/'third_party/VISTA'
for path in closure(vista/'src',{'vista.core.session','vista.core.tools'},lambda n:n=='vista' or n.startswith('vista.')).values():
    target=TARGET/'src'/path.relative_to(vista/'src');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
for path in (vista/'src/vista/core/prompts').glob('*.md'):
    target=TARGET/'src'/path.relative_to(vista/'src');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
TARGET.mkdir(parents=True,exist_ok=True);shutil.copyfile(vista/'LICENSE',TARGET/'VISTA-LICENSE')
# Initial packaging adaptations. Subsequent reviewed Ymir changes are listed in PROVENANCE.json.
for relative in ['stoa_lens/service.py','stoa_lens/dfine.py','stoa/config.py']:
    path=TARGET/'src'/relative;text=path.read_text()
    if 'import os' not in text:text='import os\n'+text
    text=text.replace('ROOT=Path(__file__).resolve().parents[2]', "ROOT=Path(os.environ.get('YMIR_STOA_ROOT',Path(__file__).resolve().parents[2])).resolve()")
    text=text.replace('ROOT = Path(__file__).resolve().parents[2]', "ROOT = Path(os.environ.get('YMIR_STOA_ROOT',Path(__file__).resolve().parents[2])).resolve()")
    if relative.endswith('service.py'):
        text=text.replace('def _implementation(root):\n', 'def _implementation(root):\n    code_base=Path(__file__).resolve().parents[1]\n')
        before,after=text.split('def _model_manifest',1)
        before=before.replace("root/'src/","code_base/'").replace("(root/'src'/package)","(code_base/package)").replace('str(p.relative_to(root))','\"src/\"+str(p.relative_to(code_base))')
        text=before+'def _model_manifest'+after
    path.write_text(text)
path=TARGET/'src/stoa/vision.py';text=path.read_text().replace("VISTA_SOURCE = ROOT/'third_party/VISTA/src'", 'VISTA_SOURCE = Path(__file__).resolve().parents[1]');path.write_text(text)
path=TARGET/'src/stoa_lens/dfine.py';text=path.read_text().replace("PYTHON='/media/administrator/Justins 500gb/stoa-dfine-v1/environment/bin/python'", "PYTHON=os.environ.get('YMIR_DFINE_PYTHON','/media/administrator/Justins 500gb/stoa-dfine-v1/environment/bin/python')");path.write_text(text)
path=TARGET/'src/stoa_room_area_specialists/raster.py';text=path.read_text().replace("PYTHONPATH=str(root/'src')","PYTHONPATH=str(Path(__file__).resolve().parents[1])").replace("str(root/'src/stoa_room_area_specialists/raster_worker.py')","str(Path(__file__).with_name('raster_worker.py'))");path.write_text(text)
manifest={'source_repository':'https://github.com/bersamin12/stoahackjunction','source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip(),
          'scope':'Required runtime code only; original project terms retained. No credentials, reference annotations, experiment results, ledgers or model checkpoints.',
          'upstream_sha256':{str(p.relative_to(SOURCE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files.values()))},
          'vendored_sha256':{str(p.relative_to(TARGET)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((TARGET/'src').rglob('*')) if p.is_file() and p.suffix in ('.py','.md')},
          'adaptations':['YMIR_STOA_ROOT asset lookup','YMIR_DFINE_PYTHON environment override','actual vendored implementation receipts','packaged VISTA import path','packaged Raster2Seq worker path']}
(TARGET/'PROVENANCE.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('snapshotted',len(manifest['vendored_sha256']),'runtime files')

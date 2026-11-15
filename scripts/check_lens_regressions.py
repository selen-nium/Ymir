"""Run upstream regression fixtures against Ymir's packaged runtime, without inference."""
from pathlib import Path
import argparse
import hashlib
import importlib
import json
import os
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stoa-root',type=Path,default=ROOT.parent/'stoa')
    parser.add_argument('--report',type=Path,default=ROOT/'var/lens-regression-manifest.json')
    args=parser.parse_args();source=args.stoa_root.resolve();runtime=ROOT/'vendor/stoa-runtime/src'
    os.environ['YMIR_STOA_ROOT']=str(source)
    sys.path[:0]=[str(runtime),str(source/'src'),str(source),str(ROOT)]
    # Upstream test harnesses may import helpers outside the deployed dependency
    # closure. Those helpers may fall back to upstream; core runtime always wins.
    for path in runtime.iterdir():
        if path.is_dir() and path.name.startswith('stoa'):
            package=importlib.import_module(path.name)
            if hasattr(package,'__path__'):package.__path__.append(str(source/'src'/path.name))
    import stoa_lens.service,stoa_vista_specialists.geometry.tools
    for module in (stoa_lens.service,stoa_vista_specialists.geometry.tools):
        assert Path(module.__file__).is_relative_to(runtime),module.__file__
    files=sorted((source/'tests').glob('test_lens_*.py'))
    files += [source/'tests/test_native_geometry.py',source/'tests/test_room_area_specialists_raster.py',source/'tests/test_bound_counter.py']
    before={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    import pytest
    status=pytest.main(['-c',str(ROOT/'pyproject.toml'),'-q',*[str(p) for p in files]])
    after={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    record={'passed':status==0 and before==after,'test_files_sha256':before,'test_sources_unchanged':before==after,
            'runtime_provenance_sha256':hashlib.sha256((runtime.parent/'PROVENANCE.json').read_bytes()).hexdigest(),
            'core_runtime':str(runtime),'paid_inference':False}
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(record,indent=2)+'\n')
    return status if before==after else 1


if __name__=='__main__':raise SystemExit(main())

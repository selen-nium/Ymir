"""Read-only readiness; no inference, catalog refresh, or budget changes."""
from pathlib import Path
from functools import lru_cache
import json
from stoa_lens.service import ROOT
from stoa_lens.transport import _env, _validate_pricing


@lru_cache(maxsize=8)
def verified_checkpoint(path, size, modified, expected):
    from stoa_lens.registry import sha
    return sha(path)==expected


def readiness(budget):
    from stoa_lens.dfine import CHECKPOINT, CHECKPOINT_SHA256, PYTHON, UPSTREAM
    checks={}
    checkpoint=Path(CHECKPOINT)
    try:
        stat=checkpoint.stat();checks['dfine_checkpoint']=verified_checkpoint(str(checkpoint),stat.st_size,stat.st_mtime_ns,CHECKPOINT_SHA256)
    except OSError:checks['dfine_checkpoint']=False
    checks['dfine_environment']=Path(PYTHON).is_file()
    checks['dfine_upstream']=Path(UPSTREAM).is_dir()
    path=ROOT/'data/room-area-specialists/raster/readiness.json'
    try:checks['raster2seq']=path.is_file() and json.loads(path.read_text()).get('ready') is True
    except (ValueError,OSError):checks['raster2seq']=False
    from stoa_lens.cloud import enabled,manifest
    if enabled():
        try:manifest();remote=True
        except (OSError,KeyError,ValueError):remote=False
        checks={key:remote for key in ('dfine_checkpoint','dfine_environment','dfine_upstream','raster2seq')}
    env=_env(ROOT);checks['credentials']=bool(env.get('VERDA_INFERENCE_API_KEY') or env.get('VERDA_API_KEY')) and bool(env.get('VERDA_CLIENT_ID') and env.get('VERDA_CLIENT_SECRET'))
    try:
        _validate_pricing(json.loads((ROOT/'data/stoa-lens/provider/catalog.json').read_text()));checks['catalog']=True
    except (ValueError,OSError,KeyError):checks['catalog']=False
    return {'ready':all(checks.values()),'checks':checks,'budget':{k:v for k,v in budget.summary().items() if k!='charges'},'single_inference_worker':True,'automatic_retries':False}

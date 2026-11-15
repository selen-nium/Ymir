"""Read-only readiness; no inference, catalog refresh, or budget changes."""
from pathlib import Path
from functools import lru_cache
import json
from stoa_lens.service import ROOT
from stoa_lens.transport import validate_catalog, catalog_path
from stoa_lens.model_config import resolve, provider_home
import os


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
    checks['credentials']=bool(os.environ.get('OPENROUTER_API_KEY','').strip())
    modes={}
    for mode in ('smart','frontier'):
        config=resolve(mode)
        route_checks=dict(checks)
        for role in ('orchestrator','detector'):
            model=config[role]['model']
            try:
                validate_catalog(json.loads(catalog_path(provider_home(),model).read_text()),model)
                route_checks[role+'_catalog']=True
            except (ValueError,OSError,KeyError):route_checks[role+'_catalog']=False
        modes[mode]={'ready':all(route_checks.values()),'checks':route_checks,'configuration':config}
    # Existing clients retain the combined readiness/checks and accounting shape;
    # new clients consult the selected route. One ready route is enough overall.
    checks['catalog']=any(all(r['checks'][k] for k in ('orchestrator_catalog','detector_catalog')) for r in modes.values())
    return {'ready':any(r['ready'] for r in modes.values()),'checks':checks,'modes':modes,
            'budget':{k:v for k,v in budget.summary().items() if k!='charges'},
            'single_inference_worker':True,'automatic_retries':False}

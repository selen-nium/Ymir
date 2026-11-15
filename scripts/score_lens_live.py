"""Offline reference comparison. Never imports inference or submits a job."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STOA=Path('/mnt/berstorage/stoa')
HOME=ROOT/'var/live-checks'
selection=json.loads((HOME/'selection.json').read_text())
rows=[]
for case in selection['cases']:
    events=[json.loads(s) for s in (HOME/(case['id']+'.events.jsonl')).read_text().splitlines()]
    answer=next((e['answer'] for e in reversed(events) if e['type']=='answer'),None)
    if answer is None:raise RuntimeError('Wait for terminal results before opening offline references')
    # Official reference inputs are confined to this scorer, never the inference script.
    if case['id']=='aecv-doors':
        reference=json.loads(Path(case['path']).with_name('metadata.json').read_text())['Door']
        observed=answer.get('value');correct=observed==reference
        basis='AECV-Bench reference object counts, generic Door'
    else:
        reference=json.loads((STOA/'data/corpus/gold.json').read_text())['03add-constructability-01-thickness']['value']
        observed=[r['value'] for r in answer.get('readings',[])]
        correct=any(str(reference) in str(v) for v in observed)
        basis='AEC-Bench supplied perturbation annotation; authored value-extraction diagnostic, not engineering review score'
    rows.append({'case':case['id'],'job_id':answer['job_id'],'status':answer['status'],'reference':reference,'observed':observed,'reference_agreement':correct,'reference_basis':basis,
                 'source_sha256':case['sha256'],'project_identity':case['project_identity'],'citations':len(answer.get('citations',[]))})
report={'schema':'ymir-lens-live-validation-v1','diagnostic_only':True,'references_provided_to_inference':False,'cases':rows}
(HOME/'offline-comparison.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))

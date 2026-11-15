"""Once-only local integration checks. Shares var/lens/budget.sqlite ($25).

Only public sources/questions enter the job. Offline references are opened after
completion by score_lens_live.py. These are exposed diagnostics, not new holdouts.
"""
import json
import sys
from pathlib import Path
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from ymir.documents import DocumentStore, SheetSelection
from ymir.drawings import DrawingStore
from stoa_lens.registry import sha

STOA=Path('/mnt/berstorage/stoa')
ROOT=Path(__file__).resolve().parents[1]
HOME=ROOT/'var'/'live-checks'
URL='http://127.0.0.1:8011'


def main():
    HOME.mkdir(parents=True,exist_ok=True)
    selection=HOME/'selection.json'
    if not selection.exists():
        docs=DocumentStore(DrawingStore());cases=[]
        aecv=STOA/'data/sources/aecv-bench/data/Use Case 1 - Object Counting/1 - Full Datasets/0000-0001/0000-0001.png'
        pdf=STOA/'data/corpus/03add-constructability-01.pdf'
        provenance=json.loads((STOA/'data/corpus/provenance.json').read_text())['sources']['03add-constructability-01']
        if sha(pdf)!=provenance['sha256']:raise ValueError('AEC-Bench source hash mismatch')
        for name,path,dataset,project,q in [
            ('aecv-doors',aecv,'AECV-Bench','aecv-0000-0001','How many doors are shown? Count generic doors on the full source drawing.'),
            ('aec-pdf',pdf,'AEC-Bench','03add','In WALL SECTION 1 on A5.03 (the far-right wall section), what is the specified thickness of EACH insulation layer in its MIN R25 roof-assembly note? Report it as a model reading with positioned native PDF evidence.')]:
            d=docs.upload(path.read_bytes(),path.name,project,dataset=dataset,exposure='exposed_integration_diagnostic')
            prepared=docs.prepare(d['id'],SheetSelection(pages=[1]))
            cases.append({'id':name,'dataset':dataset,'project_identity':project,'path':str(path),'sha256':sha(path),'drawing_id':prepared['sheets'][0]['id'],'question':q,
                          'exposure_check':{'status':'exposed_diagnostic','method':'AECV public benchmark; AEC source previously present in Stoa corpus; no held-out claim'},'reference_annotations_provided':False})
        selection.write_text(json.dumps({'schema':'ymir-live-checks-v1','selection_policy':'AECV for counting; AEC source PDF for native text. Existing sources and outputs are preserved.','cases':cases},indent=2))
    cases=json.loads(selection.read_text())['cases']
    for c in cases:
        dest=HOME/(c['id']+'.events.jsonl')
        # A repeated invocation only resubscribes to the same idempotent request.
        body={'request_id':'ymir-live-v1-'+c['id'],'selected_drawing_ids':[c['drawing_id']],'drawing_id':c['drawing_id'],'question':c['question'],
              'project_id':'lens-live-'+c['id'],'conversation_id':'lens-live-'+c['id'],'prior_job_ids':[]}
        print('Watching',c['id'],flush=True)
        with requests.post(URL+'/api/ask',json=body,stream=True,timeout=(10,700)) as response:
            response.raise_for_status()
            with dest.open('w') as out:
                for line in response.iter_lines():
                    if line:
                        out.write(line.decode()+'\n');out.flush();event=json.loads(line)
                        if event['type']=='job':print(c['id'],event['job_id'],event['status'],flush=True)
                        if event['type']=='answer':print(c['id'],event['answer']['status'],event['answer'].get('text'),flush=True)
        print(requests.get(URL+'/api/lens/status').json()['budget'],flush=True)

if __name__=='__main__':main()

"""Exercise the live API check with an injected non-metered runner."""
import importlib.util
import json
from pathlib import Path
import sys
import pytest

from stoa_lens.budget import LensBudget
from stoa_lens.registry import Registry

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('junction_live_check',ROOT/'scripts/check_junction_live_api.py')
script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)


def public_fixture(tmp_path):
    from PIL import Image
    import hashlib
    image=tmp_path/'public-source.png';Image.new('RGB',(100,80),'white').save(image)
    page={'image':str(image),'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'width':100,'height':80}
    categories=[('toilets',['toilet_bowl'],'How many toilets are in the marked area?'),
                ('windows',['window'],'How many windows are there?'),
                ('baths',['bathtub','shower'],'Count bathtubs and showers in the marked area.')]
    rows=[{'case_id':'fixture-'+kind,'target_kind':kind,'target_classes':classes,'question':question,
           'dataset':'synthetic API fixture','group_id':'fixture','exposure':'previously_exposed_diagnostic','pages':[page]}
          for kind,classes,question in categories]
    rows += [{'case_id':f'other-{i}','target_kind':'other','target_classes':[],'question':'Other fixture','pages':[page],
              'exposure':'previously_exposed_diagnostic'} for i in range(5)]
    path=tmp_path/'public-manifest.json';path.write_text(json.dumps({'cases':rows}))
    return path


def test_first_three_categories_and_exact_questions(tmp_path):
    data=json.loads(public_fixture(tmp_path).read_text())
    cases=script.choose_cases(data)
    assert [c['target_kind'] for c in cases]==['toilets','windows','baths']
    assert [c['case_id'] for c in cases]==['fixture-toilets','fixture-windows','fixture-baths']


def test_public_count_evidence_is_balanced_and_source_bound():
    answer={'status':'answered','confidence':None,
            'counts':[{'sheet':'sheet','provenance':'host_derived','count':1,'accepted_proposal_ids':['p1']}],
            'citations':[{'sheet':'sheet','provenance':'source_bound','evidence_id':'p1','proposal_id':'p1','box':[1,2,5,6]}],
            'reviews':[{'sheet':'sheet','boxes':[{'id':'p1','box':[1,2,5,6],'accepted':True}]}]}
    assert script.verify_evidence(answer,'sheet',100,100)['citations']==1
    answer['citations']=[]
    with pytest.raises(ValueError,match='source citations'):script.verify_evidence(answer,'sheet',100,100)


def test_isolated_api_idempotent_replay_without_paid_calls(tmp_path,monkeypatch):
    monkeypatch.setenv('YMIR_DATA_DIR',str(tmp_path/'import-state'))
    monkeypatch.setenv('YMIR_DB',str(tmp_path/'import-state'/'ymir.db'))
    # This test process imports Stoa before the script. The live standalone
    # command performs the real vendored-origin check in a fresh interpreter.
    monkeypatch.setattr(script,'verify_runtime',lambda module:{'path':str(module.__file__),'sha256':'fixture'})
    import ymir.lens_jobs
    calls=[]
    def fake_run(pages,question,folder,**kwargs):
        calls.append(question); reg=Registry(pages,folder/'views');pid=reg.initial['page_id']
        kwargs['progress']({'operation':'tool','phase':'started','tool':'inspect','call_id':'stub'})
        kwargs['progress']({'operation':'tool','phase':'completed','tool':'inspect','call_id':'stub','result':{'status':'complete'}})
        return {'status':'needs_review','accepted_proposal_ids':[],'counts':[], 'proposals':[], 'rooms':[],
                'text_evidence':[],'readings':[],'measurements':[],'calibrations':[], 'validation_errors':[],
                'coverage':{'sources':[reg.public_source(pid)]},'evidence_views':{}}
    monkeypatch.setattr(ymir.lens_jobs,'run',fake_run)
    ledger=tmp_path/'shared.sqlite';LensBudget(ledger,15)
    output=tmp_path/'output';manifest=public_fixture(tmp_path)
    monkeypatch.setattr(sys,'argv',['check_junction_live_api.py','--manifest',str(manifest),'--ledger',str(ledger),'--budget','15','--output',str(output)])
    script.main()
    frozen=json.loads((output/'public-inputs.json').read_text())
    assert [c['question'] for c in frozen['cases']]==calls
    assert [c['route'] for c in frozen['cases']]==['general','compact_counter','general']
    for c in frozen['cases']:
        receipt=json.loads((output/(c['case_id']+'.receipt.json')).read_text())
        assert receipt['replay_same_job'] and receipt['reconnect_tail_verified']
        assert receipt['job_ledger_rows_before_after_replay']==[0,0]
    assert LensBudget(ledger,15).summary()['requests']==0

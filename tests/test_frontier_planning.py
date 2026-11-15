"""Ported planner mechanism checks; fixtures only, no model or GPU dispatch."""
import json
import time
import pytest
import jsonschema
from PIL import Image
from stoa_lens.registry import Registry
from stoa_lens.planning import PlanningCounter, PlanningHarness, CoverageIncomplete
from stoa_lens.model_config import resolve, validate


@pytest.fixture(autouse=True)
def heads(monkeypatch):
    from stoa_lens.dfine import HEADS
    monkeypatch.setattr('stoa_lens.routing.verified_head_policy',lambda:{'heads':list(HEADS)})


def counter(tmp_path,transport=None):
    path=tmp_path/'page.png';Image.new('RGB',(100,80),'white').save(path)
    registry=Registry([{'image':str(path),'width':100,'height':80}],tmp_path/'views')
    def detector(view,region,reason,timeout):
        return {'status':'complete','proposals':[{'id':'p','source_sha256':view['source_sha256'],'view_id':view['view_id'],
                 'native_box_xyxy':[20,20,30,30],'clipped':False,'touches_crop_edge':False}],'receipt':{}}
    value=PlanningCounter(registry,detector,transport,tmp_path/'counter',page_id=registry.initial['page_id'],target_classes=['door'],description='doors',backend='fixture',configuration=resolve('frontier'))
    value.tool_deadline=time.monotonic()+30
    return value


def plan():return {'regions':[{'id':'sheet','name':'Whole sheet','region':[0,0,100,80]}]}


def test_frontier_plan_is_immutable_and_coverage_comes_from_actual_tools(tmp_path):
    h=counter(tmp_path)
    with pytest.raises(ValueError,match='Submit coverage_plan'):h.tool('detect_objects',{'region':[0,0,100,80],'reason':'Find doors'})
    with pytest.raises(ValueError,match='whole registered page'):h.tool('coverage_plan',{'regions':[{'id':'left','name':'Incomplete','region':[0,0,50,80]}]})
    h.tool('coverage_plan',plan())
    assert h.coverage_ledger()['plan_submitted'] and not h.coverage_ledger()['all_detected']
    assert not any(t['function']['name']=='coverage_plan' for t in h.available_tools())
    h.tool('detect_objects',{'region':[0,0,100,80],'reason':'Find doors'})
    with pytest.raises(CoverageIncomplete):h.bind({'status':'answered','accepted_proposal_ids':['p']})
    h.tool('inspect',{'question':'Check sheet','views':[{'turn':0,'label':'whole'}]})
    assert h.coverage_ledger()['all_inspected'] and h.coverage_ledger()['all_detected']
    assert h.bind({'status':'answered','accepted_proposal_ids':['p']})['count']==1
    assert h.limits.responses==8 and h.limits.detector_requests==4 and h.resources()['vista_views_remaining']==8


def test_planned_counter_can_fill_a_coverage_gap_with_remaining_tools(tmp_path):
    def call(name,args):return {'id':name,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}
    class Transport:
        def complete(self,messages,tools,deadline,index,final=False):
            if index==0:return {'role':'assistant','tool_calls':[call('coverage_plan',plan()),call('detect_objects',{'region':[0,0,100,80],'reason':'Find doors'})]}
            if index==2:
                assert not final and any(t['function']['name']=='inspect' for t in tools)
                return {'role':'assistant','tool_calls':[call('inspect',{'question':'Check whole sheet','views':[{'turn':0,'label':'whole'}]})]}
            return {'role':'assistant','content':'{"status":"answered","accepted_proposal_ids":["p"]}'}
    h=counter(tmp_path,Transport());result=h.run('Count doors')
    assert result['count']==1 and result['coverage_plan']['all_inspected']
    assert result['configuration']['coverage_planning']['enabled']


def test_general_plan_uses_registered_pages_and_available_tools_without_evidence_ids(tmp_path):
    base=counter(tmp_path)
    class Geometry:
        calibrations={};candidates={}
        def native_room_candidates(self,*args,**kwargs):return {'status':'unavailable','candidates':[]}
    h=PlanningHarness(base.registry,object(),Geometry(),object(),tmp_path/'general',configuration=resolve('frontier'))
    h.tool_deadline=time.monotonic()+30
    page=base.registry.initial['page_id']
    with pytest.raises(ValueError,match='Submit execution_plan'):h.tool('native_room_candidates',{'page_id':page})
    with pytest.raises(jsonschema.ValidationError):h.tool('execution_plan',{'steps':[{'id':'native','tool':'native_room_candidates','page_ids':['foreign-page'],'reason':'Read rooms'}]})
    value,_=h.tool('execution_plan',{'steps':[{'id':'native','tool':'native_room_candidates','page_ids':[page],'reason':'Read native room evidence'}]})
    assert value['evidence'] is False and h.proposals=={} and h.geometry.candidates=={}
    assert h.tool('native_room_candidates',{'page_id':page})[0]['status']=='unavailable'
    with pytest.raises(ValueError,match='allowance'):h.tool('execution_plan',{'steps':[{'id':'native','tool':'native_room_candidates','page_ids':[page],'reason':'Read native rooms'}]})


def test_mode_mapping_enables_planning_only_on_new_frontier_snapshots():
    assert resolve('smart')['planning']['enabled'] is False
    assert resolve('frontier')['planning']['enabled'] is True
    old=resolve('frontier');old.pop('planning')
    assert validate(old)==old and 'planning' not in validate(old)
    bad=resolve('smart');bad['planning']['enabled']=True
    with pytest.raises(ValueError):validate(bad)

"""Small explicit coverage plans backed by actual VISTA and detector receipts."""
from __future__ import annotations
import copy
import jsonschema
from shapely.geometry import box
from shapely.ops import unary_union
from .bound_counter import BoundCounter
from .harness import LensHarness


class CoverageIncomplete(ValueError):
    allow_tools=True

PLAN_TOOL={'type':'function','function':{'name':'coverage_plan',
    'description':'Before inspecting details or detecting objects, name up to six page regions whose union covers the entire registered page. Coordinates are native page pixel corners. The host tracks subsequent inspection and detection coverage.',
    'parameters':{'type':'object','additionalProperties':False,'required':['regions'],
        'properties':{'regions':{'type':'array','minItems':1,'maxItems':6,'items':{
            'type':'object','additionalProperties':False,'required':['id','name','region'],
            'properties':{'id':{'type':'string','minLength':1},'name':{'type':'string','minLength':1,'maxLength':120},
                'region':{'type':'array','items':{'type':'integer'},'minItems':4,'maxItems':4}}}}}}}}


class PlanningCounter(BoundCounter):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.plan=None;self.inspected=[]
        self.tools=[copy.deepcopy(PLAN_TOOL)]+self.tools
        self.schemas['coverage_plan']=PLAN_TOOL['function']['parameters']
        self.allowances['coverage_plan']=2;self.uses['coverage_plan']=0
        self.system_prompt+=(' First submit a short coverage_plan for the whole page, then inspect and detect until every planned region has recorded coverage. '
            'Use the returned coverage ledger to choose the next unchecked region. Resolve overlaps and edge uncertainty without double-counting. '
            'Stop when supported; if coverage remains incomplete, return needs_review and name the gaps. The plan is within this conversation, not another agent.')

    def resources(self):
        value=super().resources()
        value['vista_views_remaining']=max(0,getattr(self.registry,'view_limit',9)-self.registry.inspection_views)
        return value

    def available_tools(self):
        return [t for t in super().available_tools() if self.plan is None or t['function']['name']!='coverage_plan']

    def coverage_ledger(self):
        inspected=unary_union([box(*r) for r in self.inspected])
        detected=unary_union([box(*d['region']) for d in self.detections if d['status']=='complete'])
        rows=[]
        for region in self.plan or []:
            shape=box(*region['region']);a=shape.area
            rows.append({**region,'inspection_fraction':round(shape.intersection(inspected).area/a,6),
                'detection_fraction':round(shape.intersection(detected).area/a,6),
                'inspected':inspected.buffer(1e-7).covers(shape),'detected':detected.buffer(1e-7).covers(shape)})
        return {'plan_submitted':self.plan is not None,'regions':rows,
                'all_inspected':bool(rows) and all(r['inspected'] for r in rows),
                'all_detected':bool(rows) and all(r['detected'] for r in rows),
                'meaning':'Geometric coverage from tool receipts; it does not prove object recall or resolve ambiguous proposals.'}

    def inventory(self):return dict(super().inventory(),coverage_plan=self.coverage_ledger())

    def tool(self,name,args):
        if name=='coverage_plan':
            self._consume(name);jsonschema.validate(args,self.schemas[name])
            if self.plan is not None:raise ValueError('Recorded plan is immutable; use its coverage ledger')
            source=self.registry.sources[self.page_id];regions=args['regions']
            if len({r['id'] for r in regions})!=len(regions):raise ValueError('Unique planned region IDs required')
            for region in regions:
                x0,y0,x1,y1=region['region']
                if not 0<=x0<x1<=source['width'] or not 0<=y0<y1<=source['height']:raise ValueError('Plan region outside registered page')
            if not unary_union([box(*r['region']) for r in regions]).covers(box(0,0,source['width'],source['height'])):
                raise ValueError('Planned regions must cover the whole registered page without gaps')
            self.plan=copy.deepcopy(regions)
            return {'status':'recorded','coverage_plan':self.coverage_ledger()},[]
        if self.plan is None:
            self._consume(name)
            raise ValueError('Submit coverage_plan before inspection or localization')
        previous=set(self.registry.views)
        value,images=super().tool(name,args)
        if name in ('inspect','read_pixels'):
            inspected_views=value.get('registered_views') or [self.registry.views[i] for i in set(self.registry.views)-previous]
            for view in inspected_views:
                t=view['transform']
                self.inspected.append([t['origin_x'],t['origin_y'],t['origin_x']+view['width']*t['scale_x'],t['origin_y']+view['height']*t['scale_y']])
        return dict(value,coverage_plan=self.coverage_ledger()),images

    def bind(self,value,strict=True):
        ledger=self.coverage_ledger()
        if value.get('status')=='answered' and not (ledger['all_inspected'] and ledger['all_detected']):
            if strict:raise CoverageIncomplete('Whole-page inspection and detection coverage required; check coverage_plan gaps')
            value=dict(value,status='needs_review')
        return dict(super().bind(value,strict=strict),coverage_plan=ledger)

    def _finish(self,result):
        result=super()._finish(result)
        result['configuration']['coverage_planning']={'enabled':True,'vista_view_limit':getattr(self.registry,'view_limit',9),
            'coverage_source':'Actual registered VISTA views and complete detector receipts','separate_router_agent':False}
        self._save(result=result)
        return result


class PlanningHarness(LensHarness):
    """Source-capability-aware task planning for general and geometry questions.

    Adapt the counter's explicit in-conversation planning to a tool checklist;
    native geometry and printed readings do not require object-detection coverage.
    The plan itself is never registered as drawing evidence.
    """
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.plan=None
        names=[t['function']['name'] for t in self.tools]
        definition={'type':'function','function':{'name':'execution_plan',
            'description':'Before using drawing tools, record a short task plan using registered page IDs and available tools. Respect source capabilities. The plan guides this same conversation and is not drawing evidence.',
            'parameters':{'type':'object','additionalProperties':False,'required':['steps'],
                'properties':{'steps':{'type':'array','minItems':1,'maxItems':6,'items':{
                    'type':'object','additionalProperties':False,'required':['id','tool','page_ids','reason'],
                    'properties':{'id':{'type':'string','minLength':1},'tool':{'enum':names},
                        'page_ids':{'type':'array','minItems':1,'maxItems':3,'uniqueItems':True,'items':{'enum':list(self.registry.sources)}},
                        'reason':{'type':'string','minLength':1,'maxLength':240}}}}}}}}
        self.tools=[definition]+self.tools
        self.schemas['execution_plan']=definition['function']['parameters']
        self.allowances['execution_plan']=2;self.uses['execution_plan']=0
        self.system_prompt+=(' First submit a short execution_plan, then use the existing tools to establish evidence. '
            'Use native geometry or PDF evidence when available; inspect images only as needed. '
            'The plan is not evidence and does not permit invented IDs, measurements or counts. '
            'Use needs_review for unsupported findings; do not call tools merely to fill the plan.')

    def available_tools(self):
        return [t for t in super().available_tools() if self.plan is None or t['function']['name']!='execution_plan']

    def inventory(self):return dict(super().inventory(),execution_plan=self.plan)

    def tool(self,name,args):
        if name=='execution_plan':
            self._consume(name);jsonschema.validate(args,self.schemas[name])
            if self.plan is not None:raise ValueError('Recorded execution plan is immutable')
            if len({step['id'] for step in args['steps']})!=len(args['steps']):raise ValueError('Unique plan step IDs required')
            self.plan=copy.deepcopy(args['steps'])
            return {'status':'recorded','execution_plan':self.plan,'evidence':False},[]
        if self.plan is None:
            self._consume(name)
            raise ValueError('Submit execution_plan before drawing tools')
        result,images=super().tool(name,args)
        return dict(result,execution_plan=self.plan),images

    def bind(self,value,strict=True):
        if value.get('status')=='answered' and self.plan is None:
            if strict:raise CoverageIncomplete('Record execution_plan before answering')
            value=dict(value,status='needs_review')
        return dict(super().bind(value,strict=strict),execution_plan=copy.deepcopy(self.plan))

    def _finish(self,result):
        result=super()._finish(result)
        result['configuration']['execution_planning']={'enabled':True,'separate_router_agent':False,'plan_is_evidence':False}
        self._save(result=result)
        return result

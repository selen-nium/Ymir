import {expect,test} from 'vitest';
import {createLensTraceSequence} from '@/lib/lens-trace';
import type {TraceEvent} from '@/lib/types';

const progress=(activity:Record<string,unknown>):TraceEvent=>({type:'progress',activity});
const response=(index:number,phase='started')=>progress({operation:'response',response_index:index,phase});
const tool=(name:string,id:string,phase='started',extra:Record<string,unknown>={})=>progress({operation:'tool',tool:name,call_id:id,phase,...extra});
const nodes=(events:TraceEvent[])=>events.filter((e):e is Extract<TraceEvent,{type:'node'}>=>e.type==='node');
const stage=(stage:Extract<TraceEvent,{type:'stage'}>['stage'],summary:string):TraceEvent=>({type:'stage',stage,state:'done',summary,detail:`Recorded ${stage} facts`});

test.each(['Compact counter · door','General harness'])('pipeline stages surround model work for %s',route=>{
  const events:TraceEvent[]=[{type:'job',job_id:'j',status:'queued'},
    stage('question','1 selected sheet'),stage('queue','1 sheet snapshotted · queued'),stage('route',route),
    {type:'job',job_id:'j',status:'running'},response(0),response(0,'completed'),
    tool('inspect','crop'),tool('inspect','crop','completed'),response(1),response(1,'completed'),
    {type:'job',job_id:'j',status:'completed'},stage('checks','1 accepted · 1 error · needs_review'),
    {type:'answer',answer:{status:'needs_review',text:'Please check the evidence.'}},
    {type:'node',id:'you',name:'Check boxes',tool:'You'},
    {type:'node',id:'ans',name:'Review result',tool:'You',parents:['you']},
    {type:'status',id:'you',state:'done',summary:'No changes'},
    {type:'status',id:'ans',state:'done',summary:'1 · human-verified'}];
  const before=JSON.stringify(events),sequence=createLensTraceSequence();
  const live=events.flatMap(e=>sequence.apply(e));
  const unique=[...new Map(nodes(live).map(n=>[n.id,n])).values()];
  expect(unique.map(n=>n.name)).toEqual(['Question','Queue','Route','Ymir response 1','Inspect','Ymir response 2','Host checks','Evidence needs review','Check boxes','Review result']);
  expect(unique.map(n=>n.parents)).toEqual([[],...unique.slice(0,-1).map(n=>[n.id])]);
  expect(unique[2]).toMatchObject({detail:'Recorded route facts'});
  expect(live).toContainEqual({type:'status',id:'stage-route',state:'done',summary:route});
  expect(live).toContainEqual({type:'status',id:'stage-checks',state:'done',summary:'1 accepted · 1 error · needs_review'});
  // Local receipts survive JSON storage; reconnect continues after seq without
  // replacing the prefix. All split points produce the same projected graph.
  const stored=JSON.parse(JSON.stringify(events.map((e,i)=>({...e,seq:i+1})))) as TraceEvent[];
  for(let after=1;after<stored.length;after++){
    const reloaded=createLensTraceSequence();
    const prefix=stored.slice(0,after).flatMap(e=>reloaded.apply(e));
    const resumed=stored.slice(after).flatMap(e=>reloaded.apply(e));
    expect([...prefix,...resumed]).toEqual(live);
  }
  const artifact=createLensTraceSequence();
  expect(stored.flatMap(e=>artifact.apply(e))).toEqual(live);
  expect(JSON.stringify(events)).toBe(before);
});

test('updating a stage keeps its identity and position',()=>{
  const sequence=createLensTraceSequence();
  sequence.apply(stage('question','1 selected sheet'));
  sequence.apply(stage('queue','Queued'));
  sequence.apply(stage('route','General harness'));
  const update=sequence.apply(stage('queue','Snapshot retained'));
  expect(nodes(update)[0]).toMatchObject({id:'stage-queue',parents:['stage-question']});
  expect(nodes(sequence.apply(response(0)))[0].parents).toEqual(['stage-route']);
});

test('old receipts do not gain stages and repeated box reviews follow the answer',()=>{
  const sequence=createLensTraceSequence();
  const events:TraceEvent[]=[{type:'job',job_id:'old',status:'completed'},response(0),response(0,'completed'),
    {type:'answer',answer:{status:'answered',text:'Recorded answer'}}];
  const output=events.flatMap(e=>sequence.apply(e));
  expect(nodes(output).map(n=>n.id)).toEqual(['response-1','lens-answer']);
  for(let i=1;i<=2;i++){
    const review=sequence.apply({type:'node',id:'you',name:'Check boxes',parents:['ver']});
    expect(nodes(review)[0].parents).toEqual([i===1?'lens-answer':`review-result-${i-1}`]);
    const result=sequence.apply({type:'node',id:'ans',name:'Answer',parents:['you']});
    expect(nodes(result)[0]).toMatchObject({id:`review-result-${i}`,name:'Review result',parents:[`review-${i}`]});
    expect(sequence.apply({type:'status',id:'you',state:'skipped',summary:'Skipped'})[0]).toMatchObject({id:`review-${i}`,state:'skipped'});
  }
});

test('responses and sequential inspections form one execution chain',()=>{
  const sequence=createLensTraceSequence();
  const output=[response(0),response(0,'completed'),tool('inspect','a'),tool('inspect','a','completed'),tool('inspect','b'),tool('inspect','b','completed'),response(1),response(1,'completed')].flatMap(e=>sequence.apply(e));
  const unique=[...new Map(nodes(output).map(n=>[n.id,n])).values()];
  expect(unique.map(n=>n.name)).toEqual(['Ymir response 1','Inspect','Inspect','Ymir response 2']);
  expect(unique.map(n=>n.parents)).toEqual([[],['response-1'],['tool-1-a'],['tool-1-b']]);
  expect(sequence.sequential).toBe(true);
});

test.each([['dfine','D-FINE-L','door','d-fine-l-junction-ema'],['kimi','Kimi','folding door','kimi-k3-1-pro']])('detector %s is visible before completion', (backend,label,kind,model)=>{
  const sequence=createLensTraceSequence();
  const out=sequence.apply(tool('detect_objects','det','started',{routing:{backend,requested_class:kind},model}));
  expect(nodes(out)[0]).toMatchObject({tool:label,detail:model});
  expect(out.at(-1)).toMatchObject({type:'status',state:'running',summary:kind});
});

test('older event receipts recover detector identity without rewriting history',()=>{
  const sequence=createLensTraceSequence();
  const events:TraceEvent[]=[{type:'job',job_id:'job',status:'running'},
    {type:'node',id:'lens',name:'Stoa Lens',parents:[]},response(0),response(0,'completed'),
    {type:'node',id:'det',name:'detect objects',parents:['lens']},tool('detect_objects','det'),
    tool('detect_objects','det','completed',{result:{routing:{backend:'dfine',requested_class:'door'},model:'d-fine-l-junction-ema',status:'complete'}})];
  const before=JSON.stringify(events),out=events.flatMap(e=>sequence.apply(e));
  expect(nodes(out).map(n=>n.id)).not.toContain('lens');
  expect(nodes(out).at(-1)).toMatchObject({id:'tool-1-det',tool:'D-FINE-L',parents:['response-1']});
  expect(JSON.stringify(events)).toBe(before);
});

test('cancellation closes running steps and review follows the machine answer',()=>{
  const sequence=createLensTraceSequence();sequence.apply(response(0));
  expect(sequence.apply({type:'job',job_id:'j',status:'cancelled'})).toEqual([{type:'status',id:'response-1',state:'skipped',summary:'Cancelled'}]);
  const answer=sequence.apply({type:'answer',answer:{status:'error',text:'Cancelled'}});
  expect(answer.at(-1)).toMatchObject({state:'skipped'});
  const review=sequence.apply({type:'node',id:'you',name:'Check boxes',tool:'You',parents:['ver']});
  expect(nodes(review)[0]).toMatchObject({id:'review-1',parents:['lens-answer']});
});

test('static historical graphs retain their original parent links',()=>{
  const sequence=createLensTraceSequence();const e:TraceEvent={type:'node',id:'det',name:'Detector',parents:['plan']};
  expect(sequence.apply(e)).toEqual([e]);expect(sequence.sequential).toBe(false);
});

test('a failed detection retains the selected detector and requested class',()=>{
  const sequence=createLensTraceSequence();
  sequence.apply(tool('detect_objects','det','started',{routing:{backend:'dfine',requested_class:'door'},model:'d-fine-l-junction-ema'}));
  const output=sequence.apply(tool('detect_objects','det','completed',{result:{error_type:'DetectionError'}}));
  expect(nodes(output)[0]).toMatchObject({tool:'D-FINE-L',detail:'d-fine-l-junction-ema'});
  expect(output.at(-1)).toMatchObject({state:'failed',summary:'door · DetectionError'});
});

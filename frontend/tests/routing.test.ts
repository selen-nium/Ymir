import {afterEach, expect, test, vi} from 'vitest';
import {loadRoutingMode, saveRoutingMode, loadThread, saveThread, type KeyValueStorage} from '@/lib/persist';
import {modeReady} from '@/lib/routing';
import {createLensTraceSequence} from '@/lib/lens-trace';
import {createWorkspace} from '@/lib/workspace';
import * as api from '@/lib/api';
import type {Drawing, LensStatus, TraceEvent} from '@/lib/types';

const memory = (): KeyValueStorage => {
  const data=new Map<string,string>();
  return {getItem:k=>data.get(k)??null,setItem:(k,v)=>{data.set(k,v)},removeItem:k=>{data.delete(k)}};
};
const ready: LensStatus={ready:true,checks:{},modes:{smart:{ready:true,checks:{}},frontier:{ready:true,checks:{}}},budget:{cap_usd:25,remaining_usd:1,actual_usd:2,unresolved_reserved_usd:22}};
const drawing=(id:string):Drawing=>({id,title:id,source:{},image:{url:'source.png',size:[100,80]},scale:{m_per_px:.1,source:'estimate'},walls:[],rooms:[],texts:[],symbols:[],openings:[]});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});

test('mode preference belongs to one conversation and entries keep their submitted mode',()=>{
  const storage=memory();
  expect(loadRoutingMode('a',storage)).toBeNull();
  saveRoutingMode('a','frontier',storage);saveRoutingMode('b','smart',storage);
  expect(loadRoutingMode('a',storage)).toBe('frontier');expect(loadRoutingMode('b',storage)).toBe('smart');
  saveThread('a',[{q:'Smart question',sheet:'s',request_id:'r1',routing_mode:'smart'},{q:'Frontier question',sheet:'s',request_id:'r2',routing_mode:'frontier'}],storage);
  expect(loadThread('a',storage).map(e=>e.routing_mode)).toEqual(['smart','frontier']);
  storage.setItem('ymir.routing.a','"arbitrary-model"');expect(loadRoutingMode('a',storage)).toBeNull();
});

test('readiness uses only the selected mode',()=>{
  const partial={...ready,modes:{...ready.modes!,frontier:{ready:false,checks:{catalog:false}}}};
  expect(modeReady(partial,'smart')).toBe(true);expect(modeReady(partial,'frontier')).toBe(false);
  expect(modeReady(null,'smart')).toBe(false);
});

test.each([['smart','deepseek/deepseek-v4.1-flash-1017','DeepSeek'],['frontier','openai/gpt-6.1-sol','Sol']])('response traces use recorded %s identity',(_,model,label)=>{
  const trace=createLensTraceSequence();
  const out=trace.apply({type:'progress',activity:{operation:'response',phase:'started',response_index:0,model}});
  expect(out[0]).toMatchObject({tool:label,detail:model});
  const detector=trace.apply({type:'progress',activity:{operation:'tool',tool:'detect_objects',phase:'started',call_id:'d',routing:{backend:'argon'},model:'google/gemini-4-argon'}});
  expect(detector[0]).toMatchObject({tool:'Argon',detail:'google/gemini-4-argon'});
});

test('workspace persists mode across reloads, snapshots suggestions, and keeps sources on switching',async()=>{
  const storage=memory();vi.stubGlobal('window',{localStorage:storage});
  vi.stubGlobal('requestAnimationFrame',(fn:()=>void)=>{fn();return 1;});
  vi.spyOn(api,'loadDrawing').mockImplementation(async id=>drawing(id));
  vi.spyOn(api,'loadReviews').mockResolvedValue([]);vi.spyOn(api,'loadJobs').mockResolvedValue([]);
  vi.spyOn(api,'loadSuggestions').mockResolvedValue(['Count doors']);vi.spyOn(api,'previewGraph').mockResolvedValue([]);
  vi.spyOn(api,'lensStatus').mockResolvedValue(ready);
  let onEvent:((ev:TraceEvent)=>void)|undefined;
  const ask=vi.spyOn(api,'ask').mockImplementation((body,callback)=>{onEvent=callback;return ()=>{};});
  const ws=createWorkspace({navigate:()=>{}});await ws.actions.init('drawing','a');await ws.actions.refreshLens();
  expect(ws.store.getState().routingMode).toBe('smart');
  const scope=[...ws.store.getState().selectedIds];
  ws.actions.setRoutingMode('frontier');expect(ws.store.getState().selectedIds).toEqual(scope);
  ws.actions.ask('Count doors');expect(ask.mock.calls[0][0].routing_mode).toBe('frontier');
  ws.actions.setRoutingMode('smart');expect(ws.store.getState().routingMode).toBe('frontier');
  expect(ws.store.getState().thread[0].routing_mode).toBe('frontier');
  onEvent!({type:'answer',answer:{status:'answered',text:'Result'}});
  ws.actions.setRoutingMode('smart');ws.actions.ask('Suggestion question');
  expect(ask.mock.calls[1][0].routing_mode).toBe('smart');
  onEvent!({type:'answer',answer:{status:'answered',text:'Result'}});
  ws.actions.setRoutingMode('frontier');ws.actions.dispose();
  const reload=createWorkspace({navigate:()=>{}});await reload.actions.init('drawing','a');
  expect(reload.store.getState().routingMode).toBe('frontier');
  expect(reload.store.getState().thread.map(e=>e.routing_mode)).toEqual(['frontier','smart']);
  await reload.actions.init('drawing','b');expect(reload.store.getState().routingMode).toBe('smart');
  reload.actions.dispose();
});


test('historical spending stop projects plain copy while preserving the receipt',()=>{
  const event:TraceEvent={type:'progress',activity:{operation:'response',phase:'failed',response_index:0,reason:'Budget stopped before dispatch: $25 held'}};
  const before=JSON.stringify(event);
  const out=createLensTraceSequence().apply(event);
  expect(out[0]).toMatchObject({summary:'Request incomplete. The recorded evidence is available for review.'});
  expect(JSON.stringify(event)).toBe(before);
});

import {expect,test} from 'vitest';
import * as THREE from 'three';
import {windowLayout,openingSpans,reviewedWindows,type ModelWindow} from '@/lib/window-model';
import {createWindowLayer,wallGeometry} from '@/lib/window-layer';
import {createSession,type ReviewRecord} from '@/lib/session';
import {buildSteps} from '@/lib/steps';
import type {Drawing,Wall,TraceEvent} from '@/lib/types';

const walls:Wall[]=[{id:'top',a:[0,10],b:[200,10],thickness_px:8},{id:'left',a:[10,0],b:[10,200],thickness_px:8}];
const window:ModelWindow={id:'p1',sheet:'sheet',box:[30,6,90,14],verified:true};
const layout=()=>windowLayout(walls,[window]);

test('review boxes project only to a nearby aligned wall, including diagonal walls',()=>{
  expect(layout().placed[0]).toMatchObject({wallId:'top',start:30,end:90});
  expect(windowLayout(walls,[{...window,box:[6,40,14,100]}]).placed[0]).toMatchObject({wallId:'left',start:40,end:100});
  expect(windowLayout(walls,[{...window,box:[80,80,100,100]}]).unplaced).toHaveLength(1);
  expect(windowLayout(walls,[window],new Set(['top'])).unplaced).toHaveLength(1);
  const diagonal=windowLayout([{id:'diagonal',a:[0,0],b:[100,100]}],[{...window,box:[30,30,50,50]}]);
  expect(diagonal.placed[0].wallId).toBe('diagonal');
  expect(diagonal.placed[0].start).toBeCloseTo(30*Math.SQRT2);
  expect(diagonal.placed[0].end).toBeCloseTo(50*Math.SQRT2);
});

test('wall extrusion has a real opening; overlapping windows share a single hole',()=>{
  expect(openingSpans([[4,6],[2,5],[8,9]])).toEqual([[2,6],[8,9]]);
  const geometry=wallGeometry(10,3,.2,[[2,4],[3,5]]);
  const mesh=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({side:THREE.DoubleSide}));mesh.updateMatrixWorld();
  const hit=(x:number,y:number)=>new THREE.Raycaster(new THREE.Vector3(x,y,2),new THREE.Vector3(0,0,-1)).intersectObject(mesh).length;
  expect(hit(-2,1.5)).toBe(0); // inside the window
  expect(hit(-2,2.8)).toBeGreaterThan(0); // lintel
  expect(hit(-2,.3)).toBeGreaterThan(0); // sill
  expect(hit(2,1.5)).toBeGreaterThan(0); // solid wall
  geometry.dispose();(mesh.material as THREE.Material).dispose();
});

test('window animation handles repeat updates, rapid resize, confirmation and removal',()=>{
  const layer=createWindowLayer();const placed=layout().placed;
  layer.sync(placed,walls,.03,3,true,0);const mesh=layer.group.children[0];
  expect(layer.animating).toBe(true);expect(mesh.scale.x).toBeLessThan(.01);
  layer.frame(100);expect(mesh.scale.x).toBeGreaterThan(.01);expect(mesh.scale.x).toBeLessThan(1.8);
  layer.sync(placed,walls,.03,3,true,110);expect(layer.group.children[0]).toBe(mesh);
  const resized=windowLayout(walls,[{...window,box:[30,6,110,14]}]).placed;
  layer.sync(resized,walls,.03,3,true,120);layer.frame(1000);
  expect(layer.animating).toBe(false);expect(mesh.scale.x).toBeCloseTo(2.4);
  expect(layer.spans('top')[0][0]).toBeCloseTo(.9);expect(layer.spans('top')[0][1]).toBeCloseTo(3.3);
  layer.sync([],walls,.03,3,true,1100);expect(layer.animating).toBe(true);layer.frame(1400);
  expect(layer.group.children).toHaveLength(0);expect(layer.spans('top')).toEqual([]);
});

test('reduced motion settles immediately and a saved restore never replays assembly',()=>{
  const reduced=createWindowLayer(()=>true);reduced.sync(layout().placed,walls,.03,3,true,0);
  expect(reduced.animating).toBe(false);expect(reduced.group.children[0].scale.x).toBeCloseTo(1.8);
  const layer=createWindowLayer();layer.sync(layout().placed,walls,.03,3,true,0);
  layer.sync(layout().placed,walls,.03,3,false,0);expect(layer.animating).toBe(false);
  reduced.reset();layer.reset();
});

test('latest saved labels drive the model; draft edits preview and cancel restores the saved windows',()=>{
  const record={id:1,schema:'drawing-qa-review-v1',sheet_id:'sheet',drawing:{id:'sheet',title:'Sheet',image_size:[200,200]},class:'window',user_count:1,question:'Count windows',model:{value:1,threshold:0,scores:{},boxes:{}},objects:[{id:'p1',proposal_id:'p1',class:'window',bbox_xyxy:window.box,status:'accepted',subtype:''}],reviewed_at:'2026-11-15'} satisfies ReviewRecord;
  const before=JSON.stringify(record);expect(reviewedWindows([record],'other')).toEqual([]);
  expect(reviewedWindows([record,{...record,id:2,objects:[]}],'sheet')).toEqual([]);
  const session=createSession();session.state.d={id:'sheet',title:'Sheet',source:{},image:{url:'',size:[200,200]},scale:{m_per_px:.03,source:'estimate'},walls,rooms:[],texts:[],symbols:[],openings:[]} satisfies Drawing;
  session.setVerifiedWindows(reviewedWindows([record],'sheet'));
  session.startReview({status:'answered',text:'1 window',review:{sheet:'sheet',class:'window',threshold:0,boxes:[{id:'p1',box:window.box,score:null,accepted:true}]}},'',record);
  session.resizeBox('p1',[40,6,100,14]);expect(session.viewState().windows[0]).toMatchObject({box:[40,6,100,14],verified:false});
  session.finishReview(true);expect(session.state.review).not.toBeNull();
  session.skipReview();expect(session.viewState().windows[0]).toMatchObject({box:window.box,verified:true});
  expect(JSON.stringify(record)).toBe(before);
});

test('Build hides retired vision and OCR steps without changing frozen traces',()=>{
  const session=createSession();session.state.d={id:'sheet',title:'Sheet',source:{},image:{url:'',size:[200,200]},scale:{m_per_px:.03,source:'sheet_note'},walls,rooms:[],texts:[],symbols:[],openings:[]};
  const trace:TraceEvent[]=[{type:'node',id:'text',name:'Read text',tool:'RapidOCR'},{type:'node',id:'vision',name:'Claude vision',tool:'fallback'},{type:'node',id:'scale',name:'Read scale',tool:'RapidOCR'}];session.state.build=trace;
  const steps=buildSteps({session,view:null,isBase:false,hasBase:false,baseLabel:'',levelName:()=>'',placingHint:null});
  expect(steps.map(s=>s.id)).not.toContain('b-vision');expect(steps.map(s=>s.id)).not.toContain('b-text');
  expect(steps.find(s=>s.id==='b-scale')?.tool).toBe('Source metadata');expect(session.state.build).toBe(trace);
});

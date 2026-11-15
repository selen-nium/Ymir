import {expect,test} from 'vitest';
import * as THREE from 'three';
import {detectedOpenings,windowLayout,withDetected,type ModelWindow} from '@/lib/window-model';
import {createWindowLayer,wallGeometry,OPENING} from '@/lib/window-layer';
import type {Drawing,Wall} from '@/lib/types';

const wall:Wall={id:'w1',a:[0,10],b:[300,10],thickness_px:8};
const drawing={openings:[
  {id:'o1',kind:'door',box:[40,6,80,14]},
  {id:'o2',kind:'window',box:[150,6,210,14]},
  {id:'o3',kind:'window',box:[5,5,5,9]}, // degenerate: dropped
]} as unknown as Drawing;

test('detected doors and windows become unreviewed model openings',()=>{
  const found=detectedOpenings(drawing,'sheet');
  expect(found.map(o=>[o.id,o.kind,o.verified,o.detected])).toEqual([['detected-o1','door',false,true],['detected-o2','window',false,true]]);
  expect(detectedOpenings(null,'sheet')).toEqual([]);
  const layout=windowLayout([wall],found);
  expect(layout.placed.map(o=>[o.kind,o.start,o.end])).toEqual([['door',40,80],['window',150,210]]);
});

test("a person's reviewed window replaces the detected one under it; doors stay",()=>{
  const reviewed:ModelWindow[]=[{id:'r1',sheet:'sheet',box:[160,5,200,15],verified:true}];
  const merged=withDetected(reviewed,detectedOpenings(drawing,'sheet'));
  expect(merged.map(o=>o.id)).toEqual(['r1','detected-o1']);
});

test('a door is cut from the floor; a window keeps its sill',()=>{
  const height=3,geometry=wallGeometry(10,height,.2,[[6,8]],[[1,2]]);
  const mesh=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({side:THREE.DoubleSide}));mesh.updateMatrixWorld();
  const hit=(x:number,y:number)=>new THREE.Raycaster(new THREE.Vector3(x,y,2),new THREE.Vector3(0,0,-1)).intersectObject(mesh).length;
  // wall runs from -5 to 5 along x; the door spans 1-2 m from its start, the window 6-8 m
  expect(hit(-3.5,.2)).toBe(0); // door opening at floor level
  expect(hit(-3.5,height*OPENING.head+.2)).toBeGreaterThan(0); // above the door head
  expect(hit(2,.3)).toBeGreaterThan(0); // under the window: the sill
  expect(hit(2,1.5)).toBe(0); // through the window
  expect(hit(0,1)).toBeGreaterThan(0); // solid wall between them
  geometry.dispose();(mesh.material as THREE.Material).dispose();
});

test('the layer draws doors as door leaves standing on the floor, apart from window holes',()=>{
  const layer=createWindowLayer(()=>true);
  const placed=windowLayout([wall],detectedOpenings(drawing,'sheet')).placed;
  layer.sync(placed,[wall],.03,3,false);
  expect(layer.count).toBe(2);
  const door=layer.group.children.find(m=>m.userData.leaf)!, window=layer.group.children.find(m=>m.userData.glass)!;
  expect(door.position.y).toBeCloseTo(3*OPENING.head/2); // floor to door head
  expect(window.position.y).toBeCloseTo(3*(OPENING.sill+OPENING.head)/2);
  expect(layer.doorSpans('w1')).toEqual([[40*.03,80*.03]].map(([a,b])=>[expect.closeTo(a),expect.closeTo(b)]));
  expect(layer.spans('w1')).toHaveLength(1);
  layer.reset();
});

test('a bare doorway keeps its kind, is cut like a door and has no leaf',()=>{
  const d={openings:[{id:'o9',kind:'passage',box:[100,6,140,14]}]} as unknown as Drawing;
  const found=detectedOpenings(d,'sheet');
  expect(found[0]).toMatchObject({id:'detected-o9',kind:'passage',detected:true});
  expect(withDetected([{id:'r',sheet:'sheet',box:[90,0,150,20],verified:true}],found).map(o=>o.id)).toEqual(['r','detected-o9']);
  const layer=createWindowLayer(()=>true);
  layer.sync(windowLayout([wall],found).placed,[wall],.03,3,false);
  expect(layer.doorSpans('w1')).toHaveLength(1);expect(layer.spans('w1')).toHaveLength(0);
  const mesh=layer.group.children[0];
  expect(mesh.userData.leaf).toBeDefined();
  expect(mesh.children.some(c=>!c.visible)).toBe(true); // the leaf is hidden
  layer.reset();
});

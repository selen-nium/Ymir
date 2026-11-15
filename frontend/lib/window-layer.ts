// Keyed window meshes keep their identity through label edits and interrupted animations.
import * as THREE from 'three';
import {openingSpans, type WallWindow} from './window-model';
import type {Wall} from './types';

interface Pose { x:number; z:number; center:number; width:number; height:number; sill:number; depth:number; angle:number; opacity:number }
interface Item { id:string; wallId:string; mesh:THREE.Group; from:Pose; to:Pose; current:Pose; started:number; duration:number; removing:boolean; verified:boolean; signature:string; flash:boolean }
const ease=(t:number)=>t===1?1:1-2**(-10*t);
const thickness=(w:Wall,scale:number)=>w.thickness_px && w.thickness_px*scale>=.06 && w.thickness_px*scale<=.6?w.thickness_px*scale:.2;
export const wallThickness=thickness;

function release(group:THREE.Object3D) {
  group.traverse(o=>{const m=o as THREE.Mesh;m.geometry?.dispose();const materials=m.material?(Array.isArray(m.material)?m.material:[m.material]):[];materials.forEach(m=>m.dispose());});
  group.removeFromParent();
}
function makeWindow() {
  const group=new THREE.Group();
  const frame=new THREE.MeshStandardMaterial({color:0x4a5357,roughness:.65});
  const glass=new THREE.MeshStandardMaterial({color:0x94becb,roughness:.2,metalness:.15,transparent:true,opacity:.42,depthWrite:false});
  const pane=new THREE.Mesh(new THREE.BoxGeometry(.96,.96,.08),glass);group.add(pane);
  for(const y of [-.485,.485]){const rail=new THREE.Mesh(new THREE.BoxGeometry(1,.03,1),frame);rail.position.y=y;group.add(rail);}
  for(const x of [-.49,.49]){const stile=new THREE.Mesh(new THREE.BoxGeometry(.02,1,1),frame);stile.position.x=x;group.add(stile);}
  const mullion=new THREE.Mesh(new THREE.BoxGeometry(.014,.96,.65),frame);group.add(mullion);
  group.userData={pane,mullion,frame,glass};
  return group;
}

export function createWindowLayer(reducedMotion:()=>boolean=()=>false) {
  const group=new THREE.Group();group.name='Reviewed windows';
  const items=new Map<string,Item>();
  function render(item:Item,progress:number) {
    const p=item.current;
    item.mesh.position.set(p.x,p.sill+p.height/2,p.z);
    item.mesh.rotation.y=p.angle;item.mesh.scale.set(Math.max(.00001,p.width),p.height,p.depth*.82);
    const {frame,glass,mullion,pane}=item.mesh.userData;
    frame.color.set(item.verified?0x4a5357:0xc94b1f);
    if(item.flash)frame.color.lerp(new THREE.Color(0x2563eb),1-progress);
    frame.transparent=p.opacity<1;frame.opacity=p.opacity;
    glass.opacity=(.42+.18*(item.flash?1-progress:0))*p.opacity;
    // On confirmation the pane closes from its outer edge into the frame.
    const reveal=item.flash?Math.max(.04,ease(progress)):1;
    pane.scale.x=reveal;pane.position.x=-(.96*(1-reveal))/2;
    mullion.visible=p.width>1.2;
    item.mesh.userData.verified=item.verified;
    item.mesh.userData.windowId=item.id;
  }
  function frame(now=performance.now()): Set<string> {
    const changed=new Set<string>();
    for(const [key,item] of items) {
      if(item.duration===0)continue;
      const progress=reducedMotion()?1:Math.min(1,Math.max(0,(now-item.started)/item.duration));
      const k=ease(progress);
      for(const name of Object.keys(item.to) as (keyof Pose)[]) {
        const delta=name==='angle'?Math.atan2(Math.sin(item.to[name]-item.from[name]),Math.cos(item.to[name]-item.from[name])):item.to[name]-item.from[name];
        item.current[name]=item.from[name]+delta*k;
      }
      render(item,progress);changed.add(item.wallId);
      if(progress>=1){item.duration=0;item.flash=false;if(item.removing){release(item.mesh);items.delete(key);}}
    }
    return changed;
  }
  function sync(windows:WallWindow[],walls:Wall[],scale:number,height:number,animate=true,now=performance.now()) {
    frame(now);
    const wanted=new Set<string>();let stagger=0;
    for(const window of windows) {
      const wall=walls.find(w=>w.id===window.wallId);if(!wall)continue;
      const length=Math.hypot(wall.b[0]-wall.a[0],wall.b[1]-wall.a[1]);
      const center=(window.start+window.end)/2;
      const to:Pose={x:(wall.a[0]+(wall.b[0]-wall.a[0])*center/length)*scale,z:(wall.a[1]+(wall.b[1]-wall.a[1])*center/length)*scale,
        center:center*scale,width:(window.end-window.start)*scale,height:height*.46,sill:height*.32,depth:thickness(wall,scale),angle:Math.atan2(-(wall.b[1]-wall.a[1]),wall.b[0]-wall.a[0]),opacity:1};
      const key=`${window.sheet}:${window.id}@${wall.id}`;wanted.add(key);
      const signature=JSON.stringify([to,window.verified]);
      const previous=items.get(key);
      if(previous?.signature===signature && !previous.removing){
        if(!animate || reducedMotion()){previous.current={...to};previous.duration=0;previous.flash=false;render(previous,1);}
        continue;
      }
      const animated=animate && !reducedMotion();
      const mesh=previous?.mesh ?? makeWindow();if(!previous)group.add(mesh);
      const from=previous?{...previous.current}:{...to,width:animated ? .00001 : to.width,opacity:animated?0:1};
      const item:Item={id:window.id,wallId:wall.id,mesh,from,to,current:{...from},started:now+(previous?0:Math.min(120,stagger++*35)),duration:animated?(previous?300:520):0,
        removing:false,verified:window.verified,signature,flash:animated && window.verified && (!previous || !previous.verified)};
      if(!animated)item.current={...to};
      items.set(key,item);render(item,animated?0:1);
    }
    for(const [key,item] of items)if(!wanted.has(key) && !item.removing) {
      if(!animate || reducedMotion()){release(item.mesh);items.delete(key);continue;}
      item.from={...item.current};item.to={...item.current,width:.00001,opacity:0};item.started=now;item.duration=180;item.removing=true;item.flash=false;
    }
  }
  return {group,sync,frame,
    get animating(){return [...items.values()].some(item=>item.duration>0);},
    get count(){return [...items.values()].filter(item=>!item.removing).length;},
    spans(wallId:string):[number,number][]{return openingSpans([...items.values()].filter(item=>item.wallId===wallId).map(item=>[item.current.center-item.current.width/2,item.current.center+item.current.width/2]));},
    reset(){items.forEach(item=>release(item.mesh));items.clear();},
  };
}

/** Extrude the wall face with real openings. Shared openings are merged before triangulation. */
export function wallGeometry(length:number,height:number,depth:number,spans:[number,number][]=[]) {
  if(!spans.length){const geometry=new THREE.BoxGeometry(length+depth,height,depth);geometry.translate(0,height/2,0);return geometry;}
  const shape=new THREE.Shape();const left=-(length+depth)/2,right=(length+depth)/2;
  shape.moveTo(left,0);shape.lineTo(right,0);shape.lineTo(right,height);shape.lineTo(left,height);shape.closePath();
  for(const [a,b] of openingSpans(spans)) {
    const x0=Math.max(left+.00001,a-length/2),x1=Math.min(right-.00001,b-length/2);
    if(x1-x0<.0001)continue;
    const path=new THREE.Path();path.moveTo(x0,height*.32);path.lineTo(x0,height*.78);path.lineTo(x1,height*.78);path.lineTo(x1,height*.32);path.closePath();shape.holes.push(path);
  }
  const geometry=new THREE.ExtrudeGeometry(shape,{depth,bevelEnabled:false,steps:1,curveSegments:1});geometry.translate(0,0,-depth/2);return geometry;
}

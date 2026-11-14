// three.js model view. The active sheet (walls raised from image-space segments, rooms, the drawing as floor)
// sits where its placement puts it; the project's other placed sheets and its ghosts sit around it.
// Imperative; a React component mounts it and calls dispose() on unmount.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { ElevationFrame, GhostKind, Similarity } from './project';
import { wallState, type Selection, type ViewState } from './session';
import type { Drawing, Pt, Wall } from './types';

const css = (name: string, fallback: string) => {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return new THREE.Color(v || fallback);
};
function disposeTree(g: THREE.Object3D) {
  g.traverse((o) => {
    const m = o as THREE.Mesh;
    m.geometry?.dispose();
    const mat = m.material as THREE.Material | undefined;
    if (mat && !(mat as THREE.MeshStandardMaterial).map) mat.dispose();
  });
}

/** Where the active sheet sits: on a level (plan), drawn with the others (elevation), or not in the building. */
export type ActivePlace = { type: 'plan'; t: Similarity; y: number; opacity?: number } | { type: 'elevation' } | { type: 'hidden' } | null;
export interface OtherSheet {
  id: string;
  drawing: Drawing;
  place: { type: 'plan'; t: Similarity; y: number } | { type: 'elevation'; y: number };
  walls?: boolean;
  height?: number;
  faded?: boolean;
  frame?: ElevationFrame | null;
  active?: boolean;
}
export interface GhostShape { id: string; kind: GhostKind; polygon: Pt[]; y0: number; y1: number; active: boolean }
export interface SceneContext { place: ActivePlace; walls: boolean; others: OtherSheet[]; ghosts: GhostShape[] }
export interface CameraView { pos: THREE.Vector3; look: THREE.Vector3; lift: number }

type WallMesh = THREE.Mesh<THREE.BoxGeometry, THREE.MeshStandardMaterial>;

export function createViewer(el: HTMLElement) {
  const colors = () => ({
    bg: css('--viewer-bg', '#f3f3f1'), wall: css('--wall', '#ecebe7'), edge: css('--wall-edge', '#3b4048'),
    flag: css('--flag', '#f0a020'), accent: css('--accent', '#2f6bff'), ok: css('--ok', '#2f9e5b'),
    ghost: css('--ghost', '#9a968e'), answer: css('--answer', '#2563eb'),
  });
  let C = colors();
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.domElement.style.display = 'block';
  el.appendChild(renderer.domElement);
  const markerLayer = document.createElement('div');
  markerLayer.className = 'markers';
  el.appendChild(markerLayer);
  let markers: { el: HTMLDivElement; world: THREE.Vector3 }[] = [];

  const scene = new THREE.Scene();
  scene.background = C.bg;
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 5000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxPolarAngle = Math.PI * 0.48;
  scene.add(new THREE.HemisphereLight(0xffffff, 0xcfc9be, 2.0));
  const sun = new THREE.DirectionalLight(0xffffff, 1.7);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0005;
  scene.add(sun, sun.target);

  const root = new THREE.Group();
  const active = new THREE.Group(); // local coords: sheet px × scale, y up in metres
  const wallsG = new THREE.Group();
  const roomsG = new THREE.Group();
  active.add(roomsG, wallsG);
  const othersG = new THREE.Group();
  const ghostsG = new THREE.Group();
  root.add(active, othersG, ghostsG);
  scene.add(root);

  let d: Drawing | null = null, view: ViewState | null = null, floor: THREE.Mesh | null = null, texture: THREE.Texture | null = null;
  let lift = 1, selected: Selection = null, hover: Selection = null;
  let ctx: SceneContext = { place: null, walls: true, others: [], ghosts: [] };
  // keepView: switching sheets in a project keeps the camera where the user left it.
  // touched: the user (or an answer) moved the camera, so a resize keeps it instead of re-framing.
  let keepView = false, hasView = false, touched = false;
  let dirty = true;
  const invalidate = () => { dirty = true; };
  let W = 1, H = 1, s = 1;
  const handlers: { pick: (sel: Selection) => void; hover: (sel: Selection, e: PointerEvent) => void } = { pick: () => {}, hover: () => {} };
  const toLocal = ([x, y]: Pt): Pt => [x * s, y * s];
  const textures = new Map<string, THREE.Texture>();
  const texFor = (url: string) => {
    let t = textures.get(url);
    if (!t) {
      t = new THREE.TextureLoader().load(url, invalidate);
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = renderer.capabilities.getMaxAnisotropy();
      textures.set(url, t);
    }
    return t;
  };

  function placeActive() {
    const p = ctx.place;
    // an elevation is drawn with the other sheets; an unplaced or attached sheet isn't in the building
    active.visible = p?.type !== 'elevation' && p?.type !== 'hidden';
    if (p?.type === 'plan') {
      active.position.set(p.t[2], p.y ?? 0, p.t[3]);
      active.rotation.set(0, -Math.atan2(p.t[1], p.t[0]), 0);
    } else {
      active.position.set(-W * s / 2, 0, -H * s / 2);
      active.rotation.set(0, 0, 0);
    }
    active.updateMatrixWorld(true);
  }
  const toWorld = (px: Pt, y: number) => { const [x, z] = toLocal(px); return active.localToWorld(new THREE.Vector3(x, y, z)); };

  function buildFloor() {
    if (floor) { active.remove(floor); floor.geometry.dispose(); (floor.material as THREE.Material).dispose(); }
    const op = (ctx.place?.type === 'plan' ? ctx.place.opacity : undefined) ?? 1;
    const mat = new THREE.MeshStandardMaterial({ map: texture, roughness: 1, metalness: 0, transparent: op < 1, opacity: op, depthWrite: op === 1 });
    floor = new THREE.Mesh(new THREE.PlaneGeometry(W * s, H * s), mat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(W * s / 2, 0, H * s / 2);
    floor.receiveShadow = true;
    active.add(floor);
  }

  function wallLook(w: Wall) {
    const st = view && wallState(w, view, selected);
    if (!st) return null;
    const color = { selected: C.accent, ref: C.accent, cand: C.accent.clone().lerp(C.wall, 0.55), pending: C.flag,
      keep: C.ok, removed: C.ghost, answer: C.answer, normal: C.wall }[st];
    return { st, color, opacity: st === 'removed' ? 0.22 : 1 };
  }
  function styleWall(m: WallMesh) {
    const look = wallLook(m.userData.w as Wall);
    if (!look) return;
    m.material.color.copy(look.color);
    m.material.transparent = look.opacity < 1;
    m.material.opacity = look.opacity;
    m.material.depthWrite = look.opacity === 1;
    m.castShadow = look.opacity === 1;
    ((m.children[0] as THREE.LineSegments).material as THREE.LineBasicMaterial).opacity = look.opacity < 1 ? 0.12 : 0.55;
  }

  /** One extruded wall in a sheet's local frame (px × scale). */
  function wallMesh(w: Wall, scale: number, height: number, color: THREE.Color, edgeOpacity: number): WallMesh | null {
    const [ax, az] = [w.a[0] * scale, w.a[1] * scale], [bx, bz] = [w.b[0] * scale, w.b[1] * scale];
    const len = Math.hypot(bx - ax, bz - az);
    if (len < 0.05) return null;
    // drawn thickness from the detector when plausible, else 20 cm
    const tw = w.thickness_px && w.thickness_px * scale >= 0.06 && w.thickness_px * scale <= 0.6 ? w.thickness_px * scale : 0.2;
    const g = new THREE.BoxGeometry(len + tw, height, tw);
    g.translate(0, height / 2, 0);
    const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color, roughness: 0.9 }));
    m.position.set((ax + bx) / 2, 0, (az + bz) / 2);
    m.rotation.y = Math.atan2(-(bz - az), bx - ax);
    m.castShadow = m.receiveShadow = true;
    m.add(new THREE.LineSegments(new THREE.EdgesGeometry(g), new THREE.LineBasicMaterial({ color: C.edge, transparent: true, opacity: edgeOpacity })));
    return m;
  }

  function buildWalls() {
    disposeTree(wallsG);
    wallsG.clear();
    if (d && view && ctx.walls !== false) {
      for (const w of d.walls) {
        if (!wallLook(w)) continue;
        const m = wallMesh(w, s, view.height, C.wall, 0.55);
        if (!m) continue;
        m.scale.y = Math.max(0.02, lift);
        m.userData = { type: 'wall', id: w.id, w };
        styleWall(m);
        wallsG.add(m);
      }
    }
    buildMarkers();
  }

  // HTML badges pinned to 3D points (candidate letters, answer citations, room labels)
  function buildMarkers() {
    markerLayer.replaceChildren();
    markers = [];
    if (!active.visible || !d || !view) return;
    const add = (px: Pt, y: number, text: string, cls: string) => {
      const node = document.createElement('div');
      node.className = `marker ${cls}`;
      node.textContent = text;
      markerLayer.appendChild(node);
      markers.push({ el: node, world: toWorld(px, y) });
    };
    for (const c of view.candidates) {
      const w = d.walls.find((x) => x.id === c.id);
      if (w) add([(w.a[0] + w.b[0]) / 2, (w.a[1] + w.b[1]) / 2], view.height + 0.6, c.letter, c.id === view.ref ? 'on' : '');
    }
    for (const mk of view.answer?.markers ?? []) add(mk.px, mk.y ?? view.height * 0.5, mk.text, mk.cls ?? 'cite');
  }
  const tmp = new THREE.Vector3();
  function placeMarkers() {
    if (!markers.length) return;
    const r = el.getBoundingClientRect();
    for (const m of markers) {
      tmp.copy(m.world).project(camera);
      m.el.style.display = tmp.z > 1 ? 'none' : '';
      m.el.style.transform = `translate(${(tmp.x * 0.5 + 0.5) * r.width}px, ${(-tmp.y * 0.5 + 0.5) * r.height}px) translate(-50%, -50%)`;
    }
  }

  function roomStyle(m: THREE.Mesh<THREE.ShapeGeometry, THREE.MeshBasicMaterial>) {
    const id = m.userData.id as string;
    const on = selected?.type === 'room' && selected.id === id;
    const ans = view?.answer?.rooms?.includes(id);
    const hv = hover?.type === 'room' && hover.id === id;
    m.material.color.copy(ans ? C.answer : C.accent);
    m.material.opacity = ans ? 0.5 : on ? 0.45 : hv ? 0.18 : 0;
  }
  const styleRooms = () => roomsG.children.forEach((m) => roomStyle(m as THREE.Mesh<THREE.ShapeGeometry, THREE.MeshBasicMaterial>));

  function buildRooms() {
    disposeTree(roomsG);
    roomsG.clear();
    for (const r of d?.rooms ?? []) {
      const shape = new THREE.Shape(r.polygon.map((p) => { const [x, z] = toLocal(p); return new THREE.Vector2(x, -z); }));
      const g = new THREE.ShapeGeometry(shape);
      g.rotateX(-Math.PI / 2);
      const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: C.accent, transparent: true, opacity: 0, depthWrite: false }));
      m.position.y = 0.02;
      m.userData = { type: 'room', id: r.id };
      roomStyle(m);
      roomsG.add(m);
    }
  }

  /* ---------- The project's other sheets ---------- */
  function buildOthers() {
    disposeTree(othersG);
    othersG.clear();
    for (const o of ctx.others) {
      const [w, h] = o.drawing.image.size;
      const map = texFor(o.drawing.image.url);
      if (o.place.type === 'plan') {
        const t = o.place.t, L = Math.hypot(t[0], t[1]);
        const g = new THREE.Group();
        g.position.set(t[2], o.place.y, t[3]);
        g.rotation.y = -Math.atan2(t[1], t[0]);
        const plane = new THREE.Mesh(new THREE.PlaneGeometry(w * L, h * L),
          new THREE.MeshStandardMaterial({ map, roughness: 1, transparent: true, opacity: o.faded ? 0.14 : 0.9, depthWrite: !o.faded, side: THREE.DoubleSide }));
        plane.rotation.x = -Math.PI / 2;
        plane.position.set(w * L / 2, 0.01, h * L / 2);
        plane.receiveShadow = !o.faded;
        // sheets above the active level are seen through, not clicked
        plane.userData = o.faded ? {} : { type: 'sheet', id: o.id };
        g.add(plane);
        if (o.walls) {
          for (const wall of o.drawing.walls ?? []) {
            const m = wallMesh(wall, L, o.height ?? 3, C.wall, o.faded ? 0.06 : 0.3);
            if (!m) continue;
            if (o.faded) { m.material.transparent = true; m.material.opacity = 0.12; m.material.depthWrite = false; m.castShadow = false; }
            g.add(m);
          }
        }
        othersG.add(g);
      } else if (o.frame) {
        const f = o.frame, y0 = o.place.y;
        const P = (u: number, v: number) => { const m = f.map([u, v]); return new THREE.Vector3(m.x, y0 + m.h, m.y); };
        const p00 = P(0, 0), p10 = P(w, 0), p01 = P(0, h);
        const xA = p10.clone().sub(p00).normalize(), yA = p00.clone().sub(p01).normalize(), zA = xA.clone().cross(yA);
        const geo = new THREE.PlaneGeometry(w * f.L, h * f.L);
        const plane = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map, side: THREE.DoubleSide, transparent: true, opacity: o.active ? 0.95 : 0.2, depthWrite: !!o.active }));
        plane.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(xA, yA, zA));
        plane.position.copy(p10).add(p01).multiplyScalar(0.5).addScaledVector(zA, 0.8);
        plane.userData = { type: 'sheet', id: o.id };
        plane.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo),
          new THREE.LineBasicMaterial({ color: o.active ? C.answer : C.edge, transparent: true, opacity: o.active ? 1 : 0.4 })));
        othersG.add(plane);
      }
    }
  }

  /* ---------- Ghosts: parts no sheet covers, see-through with dashed edges ---------- */
  function dashed(geo: THREE.BufferGeometry, color: THREE.Color, opacity: number) {
    const l = new THREE.LineSegments(geo, new THREE.LineDashedMaterial({ color, dashSize: 0.6, gapSize: 0.4, transparent: true, opacity }));
    l.computeLineDistances();
    return l;
  }
  function prism(poly: Pt[], y0: number, y1: number) {
    const shape = new THREE.Shape(poly.map(([x, z]) => new THREE.Vector2(x, -z)));
    const g = new THREE.ExtrudeGeometry(shape, { depth: Math.max(0.02, y1 - y0), bevelEnabled: false });
    g.rotateX(-Math.PI / 2);
    g.translate(0, y0, 0);
    return g;
  }
  function buildGhosts() {
    disposeTree(ghostsG);
    ghostsG.clear();
    for (const gh of ctx.ghosts) {
      if (gh.polygon.length < 3) continue;
      if ((gh.kind === 'structure' || gh.kind === 'plumbing') && !gh.active) continue;
      const color = gh.active ? C.answer : C.ghost;
      const face = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: gh.active ? 0.16 : 0.06, depthWrite: false, side: THREE.DoubleSide });
      const edgeOp = gh.active ? 1 : 0.85;
      const g = new THREE.Group();
      g.userData = { ghostId: gh.id };
      const add = (geo: THREE.BufferGeometry) => g.add(new THREE.Mesh(geo, face), dashed(new THREE.EdgesGeometry(geo, 25), color, edgeOp));
      if (gh.kind === 'foundation') {
        const n = gh.polygon.length;
        for (let i = 0; i < n; i++) {
          const [ax, az] = gh.polygon[i], [bx, bz] = gh.polygon[(i + 1) % n];
          const len = Math.hypot(bx - ax, bz - az);
          if (len < 0.1) continue;
          const geo = new THREE.BoxGeometry(len + 0.7, gh.y1 - gh.y0, 0.7);
          geo.rotateY(Math.atan2(-(bz - az), bx - ax));
          geo.translate((ax + bx) / 2, (gh.y0 + gh.y1) / 2, (az + bz) / 2);
          add(geo);
        }
      } else if (gh.kind === 'plumbing') {
        const y = gh.y0 + 0.35;
        const pts = gh.polygon.map(([x, z]) => new THREE.Vector3(x, y, z));
        const segs = pts.flatMap((p, i) => [p, pts[(i + 1) % pts.length]]);
        g.add(dashed(new THREE.BufferGeometry().setFromPoints(segs), color, edgeOp));
        const plate = new THREE.ShapeGeometry(new THREE.Shape(gh.polygon.map(([x, z]) => new THREE.Vector2(x, -z))));
        plate.rotateX(-Math.PI / 2);
        plate.translate(0, y, 0);
        g.add(new THREE.Mesh(plate, face));
      } else {
        add(prism(gh.polygon, gh.y0, gh.y1));
      }
      ghostsG.add(g);
    }
  }

  function bounds() {
    const b = new THREE.Box3();
    if (active.visible && floor) b.expandByObject(active);
    b.expandByObject(othersG);
    ghostsG.children.forEach((g) => b.expandByObject(g));
    if (b.isEmpty()) b.set(new THREE.Vector3(-5, 0, -5), new THREE.Vector3(5, 3, 5));
    if (active.visible && floor && view) b.max.y = Math.max(b.max.y, active.position.y + view.height);
    return b;
  }

  function placeSun() {
    const b = bounds(), c = b.getCenter(new THREE.Vector3()), R = Math.max(b.max.x - b.min.x, b.max.z - b.min.z, 4);
    sun.position.set(c.x + R * 0.4, b.max.y + R * 0.9, c.z + R * 0.6);
    sun.target.position.copy(c);
    const sc = sun.shadow.camera;
    sc.left = sc.bottom = -R; sc.right = sc.top = R; sc.near = 0.1; sc.far = R * 4;
    sc.updateProjectionMatrix();
  }

  const camTarget = { from: new THREE.Vector3(), fromLook: new THREE.Vector3(), pos: new THREE.Vector3(), look: new THREE.Vector3(), t: 1 };
  /** Distance along the view direction so everything fits the current aspect ratio. */
  function fitPose(dir: THREE.Vector3) {
    const b = bounds();
    const corners: THREE.Vector3[] = [];
    for (const x of [b.min.x, b.max.x]) for (const y of [b.min.y, b.max.y]) for (const z of [b.min.z, b.max.z]) corners.push(new THREE.Vector3(x, y, z));
    const look = b.getCenter(new THREE.Vector3());
    look.y = b.min.y + (b.max.y - b.min.y) * 0.3;
    let dist = Math.max(b.max.x - b.min.x, b.max.z - b.min.z) * 1.5;
    const cam = camera.clone();
    for (let i = 0; i < 4; i++) {
      cam.position.copy(dir).multiplyScalar(dist).add(look);
      cam.lookAt(look); cam.updateMatrixWorld(); cam.updateProjectionMatrix();
      let m = 0;
      for (const c of corners) { const p = c.clone().project(cam); m = Math.max(m, Math.abs(p.x), Math.abs(p.y)); }
      dist *= m / 0.9;
    }
    return { pos: dir.clone().multiplyScalar(dist).add(look), look };
  }
  let camKind: 'top' | '3d' = '3d';
  function animateCamera(pos: THREE.Vector3, look: THREE.Vector3) {
    touched = true;
    camTarget.from.copy(camera.position); camTarget.fromLook.copy(controls.target);
    camTarget.pos.copy(pos); camTarget.look.copy(look); camTarget.t = 0;
    dirty = true;
  }
  function moveCamera(kind: 'top' | '3d', animate = true) {
    camKind = kind;
    const p = fitPose(kind === 'top' ? new THREE.Vector3(0, 1, 0.0001).normalize() : new THREE.Vector3(0.42, 0.78, 0.86).normalize());
    if (!animate) { camera.position.copy(p.pos); controls.target.copy(p.look); dirty = true; }
    else animateCamera(p.pos, p.look);
    touched = false;
  }
  /** Frame a world-space box, keeping the current view direction. */
  function frameBox(b: THREE.Box3) {
    const look = b.getCenter(new THREE.Vector3());
    const size = b.getSize(new THREE.Vector3());
    const R = Math.max(size.x, size.z, size.y * 1.5, 4);
    const dir = camera.position.clone().sub(controls.target).normalize();
    animateCamera(look.clone().add(dir.multiplyScalar(R * 2.2 + 6)), look);
  }

  let liftAnim: { from: number; to: number; start: number; ms: number } | null = null;
  const animateLift = (to: number, ms = 900) => { liftAnim = { from: lift, to, start: performance.now(), ms }; };

  // Picking: the active sheet's walls and rooms, and the other sheets (ghosts are picked from the sheet list)
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function pickAt(e: PointerEvent): Selection {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const targets = [...(active.visible ? [...wallsG.children, ...roomsG.children] : []), ...othersG.children];
    const hit = ray.intersectObjects(targets, true).find((x) => x.object.userData?.type);
    return hit ? { type: hit.object.userData.type, id: hit.object.userData.id } : null;
  }
  let down: Pt | null = null;
  const canvas = renderer.domElement;
  canvas.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointerup', (e) => {
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 5) handlers.pick(pickAt(e));
    down = null;
  });
  canvas.addEventListener('pointermove', (e) => {
    if (e.buttons || !d) return;
    const h = pickAt(e);
    if (h?.type !== hover?.type || h?.id !== hover?.id) {
      hover = h;
      styleRooms();
      canvas.style.cursor = h ? 'pointer' : '';
      dirty = true;
    }
    handlers.hover(h, e);
  });
  canvas.addEventListener('pointerleave', (e) => { hover = null; styleRooms(); dirty = true; handlers.hover(null, e); });

  function resize() {
    const { width, height } = el.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    dirty = true;
    // a panel that changed size re-frames the model, unless the user has moved the camera since
    if (d && !touched && camTarget.t >= 1) moveCamera(camKind, false);
  }
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(el);
  resize();

  const ease = (t: number) => 1 - (1 - t) ** 3;
  controls.addEventListener('change', invalidate);
  controls.addEventListener('start', () => { touched = true; });
  renderer.setAnimationLoop(() => {
    if (liftAnim) {
      const t = Math.min(1, (performance.now() - liftAnim.start) / liftAnim.ms);
      lift = liftAnim.from + (liftAnim.to - liftAnim.from) * ease(t);
      wallsG.children.forEach((m) => { m.scale.y = Math.max(0.02, lift); });
      if (t >= 1) liftAnim = null;
    }
    if (camTarget.t < 1) {
      camTarget.t = Math.min(1, camTarget.t + 0.03);
      const k = ease(camTarget.t);
      camera.position.lerpVectors(camTarget.from, camTarget.pos, k);
      controls.target.lerpVectors(camTarget.fromLook, camTarget.look, k);
    }
    const moved = controls.update();
    if (!(dirty || moved || liftAnim || camTarget.t < 1)) return;
    dirty = false;
    renderer.render(scene, camera);
    placeMarkers();
  });

  return {
    load(drawing: Drawing, v: ViewState) {
      dirty = true;
      d = drawing; view = v; selected = null; hover = null;
      [W, H] = d.image.size; s = v.scale;
      texture = texFor(d.image.url);
      const keep = keepView && hasView;
      if (!keep) lift = 0;
      placeActive(); buildFloor(); buildWalls(); buildRooms(); placeSun();
      if (!keep) { moveCamera('3d', false); animateLift(1, 1100); }
      hasView = true;
    },
    update(v: ViewState) {
      dirty = true;
      const rescale = v.scale !== s;
      view = v; s = v.scale;
      if (rescale) {
        placeActive(); buildFloor(); buildRooms(); placeSun();
        if (!keepView) moveCamera(lift > 0.5 ? '3d' : 'top', true);
      }
      buildWalls();
      styleRooms();
    },
    /** The project around the active sheet. Call before load() when switching sheets. */
    setScene(next: Partial<SceneContext>, { fit = false } = {}) {
      dirty = true;
      ctx = { place: null, walls: true, others: [], ghosts: [], ...next };
      if (d) { placeActive(); buildFloor(); buildWalls(); }
      buildOthers(); buildGhosts();
      if (d) { placeSun(); if (fit) moveCamera(camKind, true); }
    },
    highlight(sel: Selection) {
      dirty = true;
      selected = sel;
      wallsG.children.forEach((m) => styleWall(m as WallMesh));
      styleRooms();
    },
    /** Frame a region given in the active sheet's pixels [x0, y0, x1, y1], keeping the current view direction. */
    focusBox([x0, y0, x1, y1]: [number, number, number, number]) {
      const look = toWorld([(x0 + x1) / 2, (y0 + y1) / 2], 0.4);
      const R = Math.max((x1 - x0) * s, (y1 - y0) * s, 4);
      const dir = camera.position.clone().sub(controls.target).normalize();
      animateCamera(look.clone().add(dir.multiplyScalar(R * 3.2 + 9)), look);
    },
    /** Fly to a ghost (a missing sheet's dashed shape), if it is drawn. */
    focusGhost(id: string) {
      const g = ghostsG.children.find((x) => x.userData.ghostId === id);
      if (g) frameBox(new THREE.Box3().setFromObject(g));
    },
    setMode(kind: 'top' | '3d') {
      dirty = true;
      animateLift(kind === 'top' ? 0.02 : 1, 800);
      moveCamera(kind, true);
    },
    fit() { moveCamera(lift > 0.5 ? '3d' : 'top', true); },
    setKeepView(on: boolean) { keepView = on; },
    /** The camera as the user left it, to come back to after a temporary view (the Place step's top view). */
    saveView(): CameraView { return { pos: camera.position.clone(), look: controls.target.clone(), lift }; },
    restoreView(v: CameraView | null | undefined) {
      if (!v) return;
      animateLift(v.lift > 0.5 ? 1 : 0.02, 800);
      animateCamera(v.pos, v.look);
    },
    refreshTheme() { C = colors(); scene.background = C.bg; if (d) { buildWalls(); buildRooms(); } buildOthers(); buildGhosts(); },
    onPick(f: typeof handlers.pick) { handlers.pick = f; },
    onHover(f: typeof handlers.hover) { handlers.hover = f; },
    dispose() {
      renderer.setAnimationLoop(null);
      resizeObserver.disconnect();
      controls.dispose();
      disposeTree(root);
      textures.forEach((t) => t.dispose());
      renderer.dispose();
      canvas.remove();
      markerLayer.remove();
    },
  };
}
export type Viewer = ReturnType<typeof createViewer>;

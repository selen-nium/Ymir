// three.js model view: walls extruded from image-space segments, sitting on the drawing itself.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { wallState } from './session.js';

const css = (name, fallback) => {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return new THREE.Color(v || fallback);
};

export function createViewer(el, opts = {}) {
  const colors = () => ({
    bg: opts.transparent ? null : css('--viewer-bg', '#f3f3f1'),
    wall: css('--wall', '#ecebe7'), edge: css('--wall-edge', '#3b4048'),
    flag: css('--flag', '#f0a020'), accent: css('--accent', '#2f6bff'),
    ok: css('--ok', '#2f9e5b'), ghost: css('--ghost', '#9a968e'), answer: css('--answer', css('--accent', '#2f6bff').getStyle()),
  });
  let C = colors();
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.domElement.style.display = 'block';
  el.appendChild(renderer.domElement);
  const markerLayer = document.createElement('div');
  markerLayer.className = 'markers';
  el.appendChild(markerLayer);
  let markers = []; // {el, world: Vector3}

  const scene = new THREE.Scene();
  scene.background = C.bg;
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 3000);
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
  const wallsG = new THREE.Group();
  const roomsG = new THREE.Group();
  root.add(roomsG, wallsG);
  scene.add(root);

  let d = null, view = null, floor = null, texture = null, lift = 1, selected = null, hover = null;
  // Render on demand: only while the camera moves, an animation runs, or state changed.
  let dirty = true;
  const invalidate = () => { dirty = true; };
  let W = 1, H = 1, s = 1;
  const handlers = { pick: () => {}, hover: () => {} };
  const toWorld = ([x, y]) => [(x - W / 2) * s, (y - H / 2) * s];

  function buildFloor() {
    if (floor) { root.remove(floor); floor.geometry.dispose(); }
    const mat = new THREE.MeshStandardMaterial({ map: texture, roughness: 1, metalness: 0, color: opts.floorTint ?? 0xffffff });
    floor = new THREE.Mesh(new THREE.PlaneGeometry(W * s, H * s), mat);
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    root.add(floor);
  }

  function wallLook(w) {
    const st = wallState(w, view, selected);
    if (!st) return null;
    const color = { selected: C.accent, ref: C.accent, cand: C.accent.clone().lerp(C.wall, 0.55), pending: C.flag,
      keep: C.ok, removed: C.ghost, answer: C.answer, normal: C.wall }[st];
    return { st, color, opacity: st === 'removed' ? 0.22 : 1 };
  }
  function styleWall(m) {
    const look = wallLook(m.userData.w);
    if (!look) return;
    m.material.color.copy(look.color);
    m.material.transparent = look.opacity < 1;
    m.material.opacity = look.opacity;
    m.material.depthWrite = look.opacity === 1;
    m.castShadow = look.opacity === 1;
    m.children[0].material.opacity = look.opacity < 1 ? 0.12 : 0.55;
  }

  function buildWalls() {
    wallsG.children.forEach((m) => { m.geometry.dispose(); m.children[0]?.geometry.dispose(); });
    wallsG.clear();
    const t = 0.2;
    for (const w of d.walls) {
      if (!wallLook(w)) continue;
      const [ax, az] = toWorld(w.a), [bx, bz] = toWorld(w.b);
      const len = Math.hypot(bx - ax, bz - az);
      if (len < 0.05) continue;
      // drawn thickness from the detector when plausible, else 20 cm
      const tw = w.thickness_px && w.thickness_px * s >= 0.06 && w.thickness_px * s <= 0.6 ? w.thickness_px * s : t;
      const g = new THREE.BoxGeometry(len + tw, view.height, tw);
      g.translate(0, view.height / 2, 0);
      const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: C.wall, roughness: 0.9 }));
      m.position.set((ax + bx) / 2, 0, (az + bz) / 2);
      m.rotation.y = Math.atan2(-(bz - az), bx - ax);
      m.castShadow = m.receiveShadow = true;
      m.scale.y = Math.max(0.02, lift);
      m.userData = { type: 'wall', id: w.id, w };
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(g),
        new THREE.LineBasicMaterial({ color: C.edge, transparent: true, opacity: 0.55 }));
      m.add(edges);
      styleWall(m);
      wallsG.add(m);
    }
    buildMarkers();
  }

  // HTML badges pinned to 3D points (candidate letters, answer citations, room labels)
  function buildMarkers() {
    markerLayer.replaceChildren();
    markers = [];
    const add = (px, y, text, cls) => {
      const [x, z] = toWorld(px);
      const node = document.createElement('div');
      node.className = `marker ${cls}`;
      node.textContent = text;
      markerLayer.appendChild(node);
      markers.push({ el: node, world: new THREE.Vector3(x, y, z) });
    };
    for (const c of view.candidates || []) {
      const w = d.walls.find((x) => x.id === c.id);
      add([(w.a[0] + w.b[0]) / 2, (w.a[1] + w.b[1]) / 2], view.height + 0.6, c.letter, c.id === view.ref ? 'on' : '');
    }
    for (const mk of view.answer?.markers || []) add(mk.px, mk.y ?? view.height * 0.5, mk.text, mk.cls || 'cite');
  }
  const tmp = new THREE.Vector3();
  function placeMarkers() {
    if (!markers.length) return;
    const r = el.getBoundingClientRect();
    for (const m of markers) {
      tmp.copy(m.world).project(camera);
      const hidden = tmp.z > 1;
      m.el.style.display = hidden ? 'none' : '';
      m.el.style.transform = `translate(${(tmp.x * 0.5 + 0.5) * r.width}px, ${(-tmp.y * 0.5 + 0.5) * r.height}px) translate(-50%, -50%)`;
    }
  }

  function roomStyle(m) {
    const id = m.userData.id;
    const on = selected?.type === 'room' && selected.id === id;
    const ans = view?.answer?.rooms?.includes(id);
    const hv = hover?.type === 'room' && hover.id === id;
    m.material.color.copy(ans ? C.answer : C.accent);
    m.material.opacity = ans ? 0.5 : on ? 0.45 : hv ? 0.18 : 0;
  }

  function buildRooms() {
    roomsG.children.forEach((m) => m.geometry.dispose());
    roomsG.clear();
    for (const r of d.rooms) {
      const shape = new THREE.Shape(r.polygon.map((p) => { const [x, z] = toWorld(p); return new THREE.Vector2(x, -z); }));
      const g = new THREE.ShapeGeometry(shape);
      g.rotateX(-Math.PI / 2);
      const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: C.accent, transparent: true, opacity: 0, depthWrite: false }));
      m.position.y = 0.02;
      m.userData = { type: 'room', id: r.id };
      roomStyle(m);
      roomsG.add(m);
    }
  }

  function placeSun() {
    const R = Math.max(W, H) * s;
    sun.position.set(R * 0.4, R * 0.9, R * 0.6);
    const c = sun.shadow.camera;
    c.left = c.bottom = -R; c.right = c.top = R; c.near = 0.1; c.far = R * 3;
    c.updateProjectionMatrix();
  }

  const camTarget = { pos: null, look: null, t: 1 };
  // Distance along the view direction so the whole model fits the current aspect ratio.
  function fitPose(dir) {
    const w = W * s, dd = H * s, h = view?.height ?? 3;
    const corners = [];
    for (const x of [-w / 2, w / 2]) for (const y of [0, h]) for (const z of [-dd / 2, dd / 2]) corners.push(new THREE.Vector3(x, y, z));
    const look = new THREE.Vector3(0, h * 0.25, 0);
    let dist = Math.max(w, dd) * 1.5;
    const cam = camera.clone();
    for (let i = 0; i < 4; i++) {
      cam.position.copy(dir).multiplyScalar(dist).add(look);
      cam.lookAt(look); cam.updateMatrixWorld(); cam.updateProjectionMatrix();
      let m = 0;
      for (const c of corners) { const p = c.clone().project(cam); m = Math.max(m, Math.abs(p.x), Math.abs(p.y)); }
      dist *= m / 0.86;
    }
    return { pos: dir.clone().multiplyScalar(dist).add(look), look };
  }
  function moveCamera(kind, animate = true) {
    const p = kind === 'top' ? fitPose(new THREE.Vector3(0, 1, 0.0001).normalize()) : fitPose(new THREE.Vector3(0.42, 0.78, 0.86).normalize());
    if (!animate) { camera.position.copy(p.pos); controls.target.copy(p.look); return; }
    Object.assign(camTarget, { from: camera.position.clone(), fromLook: controls.target.clone(), pos: p.pos, look: p.look, t: 0 });
  }

  let liftAnim = null;
  function animateLift(to, ms = 900) {
    liftAnim = { from: lift, to, start: performance.now(), ms };
  }

  // Picking
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function pickAt(e) {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects([...wallsG.children, ...roomsG.children], false)[0];
    return hit ? { ...hit.object.userData } : null;
  }
  let down = null;
  renderer.domElement.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
  renderer.domElement.addEventListener('pointerup', (e) => {
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 5) handlers.pick(pickAt(e));
    down = null;
  });
  renderer.domElement.addEventListener('pointermove', (e) => {
    if (e.buttons || !d) return;
    const h = pickAt(e);
    if (h?.type !== hover?.type || h?.id !== hover?.id) {
      hover = h;
      roomsG.children.forEach(roomStyle);
      renderer.domElement.style.cursor = h ? 'pointer' : '';
      dirty = true;
    }
    handlers.hover(h, e);
  });
  renderer.domElement.addEventListener('pointerleave', (e) => { hover = null; roomsG.children.forEach(roomStyle); dirty = true; handlers.hover(null, e); });

  function resize() {
    const { width, height } = el.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = '100%';
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    dirty = true;
  }
  new ResizeObserver(resize).observe(el);
  resize();

  const ease = (t) => 1 - (1 - t) ** 3;
  controls.addEventListener('change', invalidate);
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
    load(drawing, v) {
      dirty = true;
      d = drawing; view = v; selected = null; hover = null;
      [W, H] = d.image.size; s = v.scale;
      texture?.dispose();
      texture = new THREE.TextureLoader().load(d.image.url, invalidate);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
      lift = opts.startFlat ? 0.02 : 0;
      buildFloor(); buildWalls(); buildRooms(); placeSun();
      moveCamera(opts.startFlat ? 'top' : '3d', false);
      if (!opts.startFlat) animateLift(1, 1100);
    },
    update(v) {
      dirty = true;
      const rescale = v.scale !== s;
      view = v; s = v.scale;
      if (rescale) {
        buildFloor(); buildRooms(); placeSun();
        moveCamera(lift > 0.5 ? '3d' : 'top', true);
      }
      buildWalls();
      roomsG.children.forEach(roomStyle);
    },
    highlight(sel) {
      dirty = true;
      selected = sel;
      wallsG.children.forEach(styleWall);
      roomsG.children.forEach(roomStyle);
    },
    // Frame a region given in drawing pixels [x0, y0, x1, y1], keeping the current view direction.
    focusBox([x0, y0, x1, y1]) {
      dirty = true;
      const [cx, cz] = toWorld([(x0 + x1) / 2, (y0 + y1) / 2]);
      const R = Math.max((x1 - x0) * s, (y1 - y0) * s, 4);
      const dir = camera.position.clone().sub(controls.target).normalize();
      const look = new THREE.Vector3(cx, 0.4, cz);
      Object.assign(camTarget, { from: camera.position.clone(), fromLook: controls.target.clone(),
        pos: look.clone().add(dir.multiplyScalar(R * 3.2 + 9)), look, t: 0 });
    },
    setMode(kind) {
      dirty = true;
      animateLift(kind === 'top' ? 0.02 : 1, 800);
      moveCamera(kind, true);
    },
    fit() { moveCamera(lift > 0.5 ? '3d' : 'top', true); },
    refreshTheme() { C = colors(); scene.background = C.bg; if (d) { buildWalls(); buildRooms(); } },
    onPick(f) { handlers.pick = f; },
    onHover(f) { handlers.hover = f; },
  };
}

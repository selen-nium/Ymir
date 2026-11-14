// three.js model view. The active sheet (walls raised from image-space segments, rooms, the drawing as floor)
// sits where its placement puts it; the project's other placed sheets and its ghosts sit around it.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { wallState } from './session.js';

const css = (name, fallback) => {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return new THREE.Color(v || fallback);
};
const disposeTree = (g) => g.traverse((o) => { o.geometry?.dispose(); if (o.material && !o.material.map) o.material.dispose?.(); });

export function createViewer(el, opts = {}) {
  const colors = () => ({
    bg: opts.transparent ? null : css('--viewer-bg', '#f3f3f1'),
    wall: css('--wall', '#ecebe7'), edge: css('--wall-edge', '#3b4048'),
    flag: css('--flag', '#f0a020'), accent: css('--accent', '#2f6bff'),
    ok: css('--ok', '#2f9e5b'), ghost: css('--ghost', '#9a968e'), answer: css('--answer', css('--accent', '#2f6bff').getStyle()),
    place: css('--place', '#c98a1b'),
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
  const active = new THREE.Group();   // local coords: sheet px × scale, y up in metres
  const wallsG = new THREE.Group();
  const roomsG = new THREE.Group();
  active.add(roomsG, wallsG);
  const othersG = new THREE.Group();
  const ghostsG = new THREE.Group();
  root.add(active, othersG, ghostsG);
  scene.add(root);

  let d = null, view = null, floor = null, texture = null, lift = 1, selected = null, hover = null;
  // Project context (setScene): where the active sheet goes, the other sheets, the ghosts.
  // place: {type:'plan', t:[a,b,tx,ty], y, opacity?} | {type:'elevation'} | null (on its own, centred)
  let ctx = { place: null, walls: true, others: [], ghosts: [] };
  // keepView: switching sheets in a project keeps the camera where the user left it
  let keepView = false, hasView = false;
  let dirty = true;
  const invalidate = () => { dirty = true; };
  let W = 1, H = 1, s = 1;
  const handlers = { pick: () => {}, hover: () => {} };
  const toLocal = ([x, y]) => [x * s, y * s];
  const textures = new Map();
  const texFor = (url) => {
    if (!textures.has(url)) {
      const t = new THREE.TextureLoader().load(url, invalidate);
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = renderer.capabilities.getMaxAnisotropy();
      textures.set(url, t);
    }
    return textures.get(url);
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
  const toWorld = (px, y) => { const [x, z] = toLocal(px); return active.localToWorld(new THREE.Vector3(x, y, z)); };

  function buildFloor() {
    if (floor) { active.remove(floor); floor.geometry.dispose(); }
    const op = ctx.place?.opacity ?? 1;
    const mat = new THREE.MeshStandardMaterial({ map: texture, roughness: 1, metalness: 0, color: opts.floorTint ?? 0xffffff,
      transparent: op < 1, opacity: op, depthWrite: op === 1 });
    floor = new THREE.Mesh(new THREE.PlaneGeometry(W * s, H * s), mat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(W * s / 2, 0, H * s / 2);
    floor.receiveShadow = true;
    active.add(floor);
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

  // One extruded wall in a sheet's local frame (px × scale).
  function wallMesh(w, scale, height, color, edgeOpacity) {
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
    wallsG.children.forEach((m) => { m.geometry.dispose(); m.children[0]?.geometry.dispose(); });
    wallsG.clear();
    if (ctx.walls !== false) {
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
    if (!active.visible) return;
    const add = (px, y, text, cls) => {
      const node = document.createElement('div');
      node.className = `marker ${cls}`;
      node.textContent = text;
      markerLayer.appendChild(node);
      markers.push({ el: node, world: toWorld(px, y) });
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
        const op = o.faded ? 0.14 : 0.9;
        const plane = new THREE.Mesh(new THREE.PlaneGeometry(w * L, h * L),
          new THREE.MeshStandardMaterial({ map, roughness: 1, transparent: true, opacity: op, depthWrite: !o.faded, side: THREE.DoubleSide }));
        plane.rotation.x = -Math.PI / 2;
        plane.position.set(w * L / 2, 0.01, h * L / 2);
        plane.receiveShadow = !o.faded;
        plane.userData = o.faded ? {} : { type: 'sheet', id: o.id };
        g.add(plane);
        if (o.walls) {
          for (const wall of o.drawing.walls || []) {
            const m = wallMesh(wall, L, o.height ?? 3, C.wall, o.faded ? 0.06 : 0.3);
            if (!m) continue;
            if (o.faded) { m.material.transparent = true; m.material.opacity = 0.12; m.material.depthWrite = false; m.castShadow = false; }
            g.add(m);
          }
        }
        othersG.add(g);
      } else if (o.place.type === 'elevation' && o.frame) {
        const f = o.frame;
        const P = (u, v) => { const m = f.map([u, v]); return new THREE.Vector3(m.x, o.place.y + m.h, m.y); };
        const p00 = P(0, 0), p10 = P(w, 0), p01 = P(0, h);
        const xA = p10.clone().sub(p00).normalize(), yA = p00.clone().sub(p01).normalize(), zA = xA.clone().cross(yA);
        const geo = new THREE.PlaneGeometry(w * f.L, h * f.L);
        const plane = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map, side: THREE.DoubleSide, transparent: true, opacity: o.active ? 0.95 : 0.2, depthWrite: !!o.active }));
        plane.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(xA, yA, zA));
        plane.position.copy(p10).add(p01).multiplyScalar(0.5).addScaledVector(zA, 0.8);
        plane.userData = { type: 'sheet', id: o.id };
        const edge = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: o.active ? C.answer : C.edge, transparent: true, opacity: o.active ? 1 : 0.4 }));
        plane.add(edge);
        othersG.add(plane);
      }
    }
  }

  /* ---------- Ghosts: parts no sheet covers, see-through with dashed edges ---------- */
  function dashed(geo, color, opacity) {
    const l = new THREE.LineSegments(geo, new THREE.LineDashedMaterial({ color, dashSize: 0.6, gapSize: 0.4, transparent: true, opacity }));
    l.computeLineDistances();
    return l;
  }
  function prism(poly, y0, y1) {
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
      if (!gh.polygon || gh.polygon.length < 3) continue;
      if ((gh.kind === 'structure' || gh.kind === 'plumbing') && !gh.active) continue;
      const color = gh.active ? C.answer : C.ghost;
      const face = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: gh.active ? 0.16 : 0.06, depthWrite: false, side: THREE.DoubleSide });
      const edgeOp = gh.active ? 1 : 0.85;
      const g = new THREE.Group();
      g.userData = { type: 'ghost', id: gh.id };
      const add = (geo) => g.add(new THREE.Mesh(geo, face), dashed(new THREE.EdgesGeometry(geo, 25), color, edgeOp));
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
        const loop = new THREE.BufferGeometry().setFromPoints([...pts, pts[0]].flatMap((p, i, a) => (i < a.length - 1 ? [p, a[i + 1]] : [])));
        g.add(dashed(loop, color, edgeOp));
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

  const camTarget = { pos: null, look: null, t: 1 };
  // Distance along the view direction so everything fits the current aspect ratio.
  function fitPose(dir) {
    const b = bounds();
    const corners = [];
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
      dist *= m / 0.86;
    }
    return { pos: dir.clone().multiplyScalar(dist).add(look), look };
  }
  let camKind = '3d';
  function moveCamera(kind, animate = true) {
    camKind = kind;
    const p = kind === 'top' ? fitPose(new THREE.Vector3(0, 1, 0.0001).normalize()) : fitPose(new THREE.Vector3(0.42, 0.78, 0.86).normalize());
    if (!animate) { camera.position.copy(p.pos); controls.target.copy(p.look); return; }
    Object.assign(camTarget, { from: camera.position.clone(), fromLook: controls.target.clone(), pos: p.pos, look: p.look, t: 0 });
  }

  let liftAnim = null;
  function animateLift(to, ms = 900) {
    liftAnim = { from: lift, to, start: performance.now(), ms };
  }

  // Picking: the active sheet's walls and rooms, other sheets, ghosts
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function pickAt(e) {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    // Ghosts and faded sheets above the active level are seen through, not clicked: rows in the tree pick them.
    const targets = [...(active.visible ? [...wallsG.children, ...roomsG.children] : []), ...othersG.children];
    const hit = ray.intersectObjects(targets, true).find((h) => h.object.userData?.type);
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
      texture = texFor(d.image.url);
      const keep = keepView && hasView;
      if (!keep) lift = opts.startFlat ? 0.02 : 0;
      placeActive(); buildFloor(); buildWalls(); buildRooms(); placeSun();
      if (!keep) {
        moveCamera(opts.startFlat ? 'top' : '3d', false);
        if (!opts.startFlat) animateLift(1, 1100);
      }
      hasView = true;
    },
    update(v) {
      dirty = true;
      const rescale = v.scale !== s;
      view = v; s = v.scale;
      if (rescale) {
        placeActive(); buildFloor(); buildRooms(); placeSun();
        if (!keepView) moveCamera(lift > 0.5 ? '3d' : 'top', true);
      }
      buildWalls();
      roomsG.children.forEach(roomStyle);
    },
    // The project around the active sheet. Call before load() when switching sheets.
    setScene(next, { fit = false } = {}) {
      dirty = true;
      ctx = { place: null, walls: true, others: [], ghosts: [], ...next };
      if (d) { placeActive(); buildFloor(); buildWalls(); }
      buildOthers(); buildGhosts();
      if (d) { placeSun(); if (fit) moveCamera(camKind, true); }
    },
    highlight(sel) {
      dirty = true;
      selected = sel;
      wallsG.children.forEach(styleWall);
      roomsG.children.forEach(roomStyle);
    },
    // Frame a region given in the active sheet's pixels [x0, y0, x1, y1], keeping the current view direction.
    focusBox([x0, y0, x1, y1]) {
      dirty = true;
      const look = toWorld([(x0 + x1) / 2, (y0 + y1) / 2], 0.4);
      const R = Math.max((x1 - x0) * s, (y1 - y0) * s, 4);
      const dir = camera.position.clone().sub(controls.target).normalize();
      Object.assign(camTarget, { from: camera.position.clone(), fromLook: controls.target.clone(),
        pos: look.clone().add(dir.multiplyScalar(R * 3.2 + 9)), look, t: 0 });
    },
    setMode(kind) {
      dirty = true;
      animateLift(kind === 'top' ? 0.02 : 1, 800);
      moveCamera(kind, true);
    },
    fit() { moveCamera(lift > 0.5 ? '3d' : 'top', true); },
    setKeepView(on) { keepView = on; },
    // The camera as the user left it, to come back to after a temporary view (the Place step's top view).
    saveView() { return { pos: camera.position.clone(), look: controls.target.clone(), lift }; },
    restoreView(v) {
      if (!v) return;
      dirty = true;
      animateLift(v.lift > 0.5 ? 1 : 0.02, 800);
      Object.assign(camTarget, { from: camera.position.clone(), fromLook: controls.target.clone(), pos: v.pos.clone(), look: v.look.clone(), t: 0 });
    },
    refreshTheme() { C = colors(); scene.background = C.bg; if (d) { buildWalls(); buildRooms(); } buildOthers(); buildGhosts(); },
    onPick(f) { handlers.pick = f; },
    onHover(f) { handlers.hover = f; },
  };
}

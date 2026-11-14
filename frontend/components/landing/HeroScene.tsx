'use client';
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import s from './landing.module.css';

interface HeroSheet { id: string; img: string; z: number; corner: [number, number]; size: [number, number]; walls: [number, number, number, number, number][] }
interface HeroData { sheets: HeroSheet[]; ghost: { z: number; height: number }; room: { label: string; area: number; polygon: [number, number][] } }

const GAP = 3.2; // extra space between levels: the building, exploded
const ease = (t: number) => 1 - (1 - Math.min(1, Math.max(0, t))) ** 3;

/** The sample building assembling itself: sheets drop into their levels, walls rise, the missing level is a ghost. */
export function HeroScene() {
  const host = useRef<HTMLDivElement>(null), label = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = host.current!, tag = label.current!;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 1000);
    scene.add(new THREE.HemisphereLight(0xffffff, 0xd9d2c5, 2.4));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(30, 60, 40);
    scene.add(sun);
    const pivot = new THREE.Group(), building = new THREE.Group(); // pivot turns; building sits centred on it
    pivot.add(building);
    scene.add(pivot);
    const disposables: { dispose(): void }[] = [];
    const keep = <T extends { dispose(): void }>(x: T) => { disposables.push(x); return x; };

    let raf = 0, visible = true, start = 0, ready = false;
    const levels: { group: THREE.Group; z: number; i: number; walls: THREE.Mesh[]; mat: THREE.MeshStandardMaterial }[] = [];
    let ghost: THREE.Group | null = null, pin: THREE.Group | null = null, center = new THREE.Vector3();
    const pinWorld = new THREE.Vector3();

    function resize() {
      const { width, height } = el.getBoundingClientRect();
      if (!width || !height) return;
      renderer.setSize(width, height, false);
      renderer.domElement.style.width = '100%';
      renderer.domElement.style.height = '100%';
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    }
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible && !raf) raf = requestAnimationFrame(frame); });
    io.observe(el);

    fetch('/hero/building.json').then((r) => r.json() as Promise<HeroData>).then((data) => {
      const loader = new THREE.TextureLoader();
      const ys = data.sheets.map((sh) => sh.z);
      const minZ = Math.min(...ys);
      const yOf = (z: number) => (z - minZ) + [...new Set([...ys, data.ghost.z])].sort((a, b) => a - b).indexOf(z) * GAP;
      // the building's middle, so it turns around itself
      const xs = data.sheets.flatMap((sh) => [sh.corner[0], sh.corner[0] + sh.size[0]]), zs = data.sheets.flatMap((sh) => [sh.corner[1], sh.corner[1] + sh.size[1]]);
      center = new THREE.Vector3((Math.min(...xs) + Math.max(...xs)) / 2, 0, (Math.min(...zs) + Math.max(...zs)) / 2);
      building.position.set(-center.x, 0, -center.z);

      data.sheets.forEach((sh, i) => {
        const g = new THREE.Group();
        const tex = keep(loader.load(sh.img, redraw));
        tex.colorSpace = THREE.SRGBColorSpace;
        tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
        const mat = keep(new THREE.MeshStandardMaterial({ map: tex, transparent: true, opacity: 0, side: THREE.DoubleSide, roughness: 1 }));
        const plane = new THREE.Mesh(keep(new THREE.PlaneGeometry(sh.size[0], sh.size[1])), mat);
        plane.rotation.x = -Math.PI / 2;
        plane.position.set(sh.corner[0] + sh.size[0] / 2, 0, sh.corner[1] + sh.size[1] / 2);
        g.add(plane);
        const walls: THREE.Mesh[] = [];
        const wallMat = keep(new THREE.MeshStandardMaterial({ color: 0xf3efe6, roughness: 0.9 }));
        const edgeMat = keep(new THREE.LineBasicMaterial({ color: 0x4a443b, transparent: true, opacity: 0.45 }));
        for (const [x1, z1, x2, z2, t] of sh.walls) {
          const len = Math.hypot(x2 - x1, z2 - z1), tw = Math.min(0.5, Math.max(0.12, t));
          const geo = keep(new THREE.BoxGeometry(len + tw, 2.8, tw));
          geo.translate(0, 1.4, 0);
          const m = new THREE.Mesh(geo, wallMat);
          m.position.set((x1 + x2) / 2, 0, (z1 + z2) / 2);
          m.rotation.y = Math.atan2(-(z2 - z1), x2 - x1);
          m.add(new THREE.LineSegments(keep(new THREE.EdgesGeometry(geo)), edgeMat));
          m.scale.y = 0.001;
          g.add(m);
          walls.push(m);
        }
        building.add(g);
        levels.push({ group: g, z: yOf(sh.z), i, walls, mat });
      });

      // the level no sheet covers: a dashed, see-through box
      const eg = data.sheets.find((sh) => sh.walls.length)!;
      const pts = eg.walls.flatMap(([x1, z1, x2, z2]) => [[x1, z1], [x2, z2]]);
      const minX = Math.min(...pts.map((p) => p[0])), maxX = Math.max(...pts.map((p) => p[0])), minZ2 = Math.min(...pts.map((p) => p[1])), maxZ2 = Math.max(...pts.map((p) => p[1]));
      const boxGeo = keep(new THREE.BoxGeometry(maxX - minX, data.ghost.height, maxZ2 - minZ2));
      ghost = new THREE.Group();
      ghost.add(new THREE.Mesh(boxGeo, keep(new THREE.MeshBasicMaterial({ color: 0xa8a39a, transparent: true, opacity: 0.06, depthWrite: false }))));
      const dashes = new THREE.LineSegments(keep(new THREE.EdgesGeometry(boxGeo)), keep(new THREE.LineDashedMaterial({ color: 0x8f8a80, dashSize: 0.7, gapSize: 0.45, transparent: true, opacity: 0.9 })));
      dashes.computeLineDistances();
      ghost.add(dashes);
      ghost.position.set((minX + maxX) / 2, yOf(data.ghost.z) + data.ghost.height / 2, (minZ2 + maxZ2) / 2);
      ghost.scale.setScalar(0.001);
      building.add(ghost);

      // the answer: the largest room, highlighted, with a citation pin
      const roomLevel = levels.find((l) => l.walls.length)!;
      const shape = new THREE.Shape(data.room.polygon.map(([x, z]) => new THREE.Vector2(x, -z)));
      const roomGeo = keep(new THREE.ShapeGeometry(shape));
      roomGeo.rotateX(-Math.PI / 2);
      const roomMesh = new THREE.Mesh(roomGeo, keep(new THREE.MeshBasicMaterial({ color: 0x2563eb, transparent: true, opacity: 0, depthWrite: false })));
      roomMesh.position.y = 0.03;
      roomLevel.group.add(roomMesh);
      const cx = data.room.polygon.reduce((a, p) => a + p[0], 0) / data.room.polygon.length, cz = data.room.polygon.reduce((a, p) => a + p[1], 0) / data.room.polygon.length;
      pin = new THREE.Group();
      pin.position.set(cx, 4.2, cz);
      pin.userData = { room: roomMesh };
      roomLevel.group.add(pin);
      tag.querySelector('b')!.textContent = `${data.room.area.toFixed(2)} m²`;
      tag.querySelector('span')!.textContent = data.room.label;

      // frame the exploded building
      const top = yOf(Math.max(...ys)) + 3;
      const R = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs)) / 2;
      camera.position.set(R * 1.75, top * 1.05 + R * 0.62, R * 2.4);
      camera.lookAt(0, top * 0.4, 0);
      ready = true;
      start = performance.now() - (still ? 1e5 : 0);
      if (!raf) raf = requestAnimationFrame(frame);
    }).catch(() => { /* the hero is decoration: the page reads fine without it */ });

    // a still frame (reduced motion) is drawn again once each sheet's image arrives
    function redraw() { if (!raf) raf = requestAnimationFrame(frame); }
    function frame(now: number) {
      raf = 0;
      if (!visible) return;
      if (ready) {
        const t = (now - start) / 1000;
        for (const l of levels) {
          const k = ease((t - 0.25 - l.i * 0.45) / 1.1);
          l.group.position.y = l.z + (1 - k) * 9;
          l.mat.opacity = k * (l.i >= 2 ? 0.55 : 0.95);
          const w = ease((t - 2.2 - l.i * 0.35) / 1.0);
          l.walls.forEach((m) => { m.scale.y = Math.max(0.001, w); });
        }
        if (ghost) ghost.scale.setScalar(Math.max(0.001, ease((t - 3.2) / 0.8)));
        if (pin) {
          const k = ease((t - 3.8) / 0.6);
          (pin.userData.room as THREE.Mesh<THREE.ShapeGeometry, THREE.MeshBasicMaterial>).material.opacity = 0.32 * k;
          tag.style.opacity = String(k);
          pin.getWorldPosition(pinWorld).project(camera);
          const r = el.getBoundingClientRect();
          tag.style.transform = `translate(${(pinWorld.x * 0.5 + 0.5) * r.width}px, ${(-pinWorld.y * 0.5 + 0.5) * r.height}px) translate(-50%, -100%)`;
        }
        if (!still) pivot.rotation.y = 0.35 * Math.sin(t * 0.18) - 0.15;
        renderer.render(scene, camera);
      }
      if (!still || !ready) raf = requestAnimationFrame(frame);
    }

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      disposables.forEach((d) => d.dispose());
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  return (
    <div className={s.scene} ref={host} aria-hidden="true">
      <div className={s.pin} ref={label}><i /><b /><span /></div>
    </div>
  );
}

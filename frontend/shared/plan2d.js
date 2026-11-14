// SVG drawing view: the source image with detected walls and rooms on top. Wheel zooms, drag pans.
import { wallState } from './session.js';

const NS = 'http://www.w3.org/2000/svg';
const mk = (tag, attrs = {}, parent) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  parent?.appendChild(n);
  return n;
};

export function createPlan(el) {
  const svg = mk('svg', { class: 'plan2d' }, el);
  const img = mk('image', { preserveAspectRatio: 'none' }, svg);
  const gRooms = mk('g', { class: 'rooms' }, svg);
  const gEvidence = mk('g', { class: 'evidence-layer' }, svg);
  const gWalls = mk('g', { class: 'walls' }, svg);
  const gReview = mk('g', { class: 'review-layer' }, svg);
  const gMarks = mk('g', { class: 'marks' }, svg);
  const preview = mk('line', { class: 'draw-preview', visibility: 'hidden' }, svg);
  const boxPreview = mk('rect', { class: 'box-preview', visibility: 'hidden' }, svg);

  // mode: 'select' (drag pans), 'draw' (click-click wall), 'box' (drag a new review box)
  let d = null, view = null, selected = null, mode = 'select', drawStart = null;
  let vb = { x: 0, y: 0, w: 100, h: 100 };
  const handlers = { pick: () => {}, hover: () => {}, draw: () => {}, drawBox: () => {}, resize: () => {} };

  const rect = () => el.getBoundingClientRect();
  const k = () => vb.w / Math.max(1, rect().width); // svg units per screen px
  // Screen-constant sizes for labels and badges
  function setVB() {
    svg.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
    const kk = k();
    gMarks.querySelectorAll('[data-off]').forEach((g) => {
      const [mx, my, nx, ny, dist] = JSON.parse(g.dataset.off);
      g.setAttribute('transform', `translate(${mx + nx * dist * kk} ${my + ny * dist * kk}) scale(${kk})`);
    });
    gEvidence.querySelectorAll('rect').forEach((r) => r.setAttribute('stroke-width', 2 * kk));
  }
  function fit(box, pad = 0.6) {
    const r = rect();
    if (!d || !r.width || !r.height) return; // hidden or mid-layout: keep the last view
    const [bx, by, bw, bh] = box || [0, 0, d.image.size[0], d.image.size[1]];
    const p = box ? pad : 0.05;
    const sx = bw * (1 + 2 * p), sy = bh * (1 + 2 * p);
    const kk = Math.max(sx / r.width, sy / r.height);
    vb = { w: r.width * kk, h: r.height * kk, x: bx + bw / 2 - (r.width * kk) / 2, y: by + bh / 2 - (r.height * kk) / 2 };
    setVB();
  }
  const toSvg = (e) => {
    const p = svg.createSVGPoint(); p.x = e.clientX; p.y = e.clientY;
    return p.matrixTransform(svg.getScreenCTM().inverse());
  };

  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    const p = toSvg(e), f = Math.exp(e.deltaY * 0.0015);
    vb = { x: p.x - (p.x - vb.x) * f, y: p.y - (p.y - vb.y) * f, w: vb.w * f, h: vb.h * f };
    setVB();
  }, { passive: false });

  // Review boxes: an edge or corner within a few screen px of the pointer can be dragged to resize.
  const setRect = (r, [x0, y0, x1, y1]) => { r.setAttribute('x', x0); r.setAttribute('y', y0); r.setAttribute('width', x1 - x0); r.setAttribute('height', y1 - y0); };
  const CURSOR = { l: 'ew-resize', r: 'ew-resize', t: 'ns-resize', b: 'ns-resize', lt: 'nwse-resize', rb: 'nwse-resize', rt: 'nesw-resize', lb: 'nesw-resize' };
  function edgeHit(p) {
    const tol = 6 * k();
    let best = null;
    for (const it of view?.review?.items || []) {
      const [x0, y0, x1, y1] = it.box;
      if (p.x < x0 - tol || p.x > x1 + tol || p.y < y0 - tol || p.y > y1 + tol) continue;
      const dist = { l: Math.abs(p.x - x0), r: Math.abs(p.x - x1), t: Math.abs(p.y - y0), b: Math.abs(p.y - y1) };
      const size = { l: x1 - x0, r: x1 - x0, t: y1 - y0, b: y1 - y0 };
      // on a box thinner than two tolerances, an edge only counts when the pointer is clearly on that side
      const grabs = (e) => dist[e] <= tol && (size[e] >= 2 * tol || dist[e] < size[e] / 4);
      const edges = [dist.l <= dist.r ? 'l' : 'r', dist.t <= dist.b ? 't' : 'b'].filter(grabs);
      if (!edges.length) continue;
      const near = Math.min(...edges.map((e) => dist[e]));
      if (!best || near < best.near) best = { id: it.id, edges, near, box: [...it.box] };
    }
    return best;
  }
  function resized(hit, p) {
    let [x0, y0, x1, y1] = hit.box;
    if (hit.edges.includes('l')) x0 = Math.min(p.x, x1 - 2);
    if (hit.edges.includes('r')) x1 = Math.max(p.x, x0 + 2);
    if (hit.edges.includes('t')) y0 = Math.min(p.y, y1 - 2);
    if (hit.edges.includes('b')) y1 = Math.max(p.y, y0 + 2);
    return [x0, y0, x1, y1];
  }

  let drag = null;
  svg.addEventListener('pointerdown', (e) => {
    const p = toSvg(e), hit = view?.review ? edgeHit(p) : null;
    const kind = hit ? 'resize' : mode === 'box' && view?.review ? 'rect' : 'pan';
    drag = { x: e.clientX, y: e.clientY, moved: 0, target: e.target, kind, hit, start: [p.x, p.y], box: null };
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener('pointermove', (e) => {
    if (drag && e.buttons) {
      const kk = k(), dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.moved += Math.abs(dx) + Math.abs(dy);
      drag.x = e.clientX; drag.y = e.clientY;
      if (drag.moved <= 4) return;
      const p = toSvg(e);
      if (drag.kind === 'resize') {
        drag.box = resized(drag.hit, p);
        const r = [...gReview.children].find((n) => n.dataset.id === drag.hit.id);
        if (r) setRect(r, drag.box);
      } else if (drag.kind === 'rect') {
        const [sx, sy] = drag.start;
        drag.box = [Math.min(sx, p.x), Math.min(sy, p.y), Math.max(sx, p.x), Math.max(sy, p.y)];
        setRect(boxPreview, drag.box);
        boxPreview.setAttribute('visibility', 'visible');
      } else { vb.x -= dx * kk; vb.y -= dy * kk; setVB(); svg.classList.add('panning'); }
      return;
    }
    if (view?.review) {
      const hit = edgeHit(toSvg(e));
      svg.classList.toggle('edge-hover', !!hit);
      svg.style.cursor = hit ? CURSOR[hit.edges.join('')] : '';
    }
    if (mode === 'draw' && drawStart) {
      const b = snap(drawStart, toSvg(e), e.altKey);
      Object.entries({ x1: drawStart[0], y1: drawStart[1], x2: b[0], y2: b[1] }).forEach(([a, v]) => preview.setAttribute(a, v));
      preview.setAttribute('visibility', 'visible');
    }
    const t = e.target.closest?.('[data-id]');
    handlers.hover(t ? { type: t.dataset.type, id: t.dataset.id } : null, e);
  });
  svg.addEventListener('pointerleave', (e) => handlers.hover(null, e));
  svg.addEventListener('pointerup', (e) => {
    svg.classList.remove('panning');
    const click = drag && drag.moved <= 4;
    const { target: downTarget, kind, hit, box } = drag || {};
    drag = null;
    if (kind === 'rect') boxPreview.setAttribute('visibility', 'hidden');
    if (!click) {
      if (kind === 'resize' && box) handlers.resize(hit.id, box.map(Math.round));
      if (kind === 'rect' && box && box[2] - box[0] >= 3 && box[3] - box[1] >= 3) handlers.drawBox(box.map(Math.round));
      return;
    }
    // a click on a box edge is still a click on that box
    if (kind === 'resize') { handlers.pick({ type: 'box', id: hit.id }); return; }
    if (mode === 'draw') {
      const p = toSvg(e);
      if (!drawStart) { drawStart = [p.x, p.y]; return; }
      const b = snap(drawStart, p, e.altKey);
      handlers.draw(drawStart.map(Math.round), b.map(Math.round));
      drawStart = null; preview.setAttribute('visibility', 'hidden');
      return;
    }
    // pointer capture retargets pointerup to the svg, so use the element pressed on
    const t = downTarget?.closest?.('[data-id]');
    handlers.pick(t ? { type: t.dataset.type, id: t.dataset.id } : null);
  });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') { drawStart = null; preview.setAttribute('visibility', 'hidden'); boxPreview.setAttribute('visibility', 'hidden'); } });
  const snap = (a, p, free) => (free ? [p.x, p.y] : Math.abs(p.x - a[0]) > Math.abs(p.y - a[1]) ? [p.x, a[1]] : [a[0], p.y]);

  function drawWalls() {
    gWalls.replaceChildren();
    // removed first so live walls sit on top
    const ordered = [...d.walls].sort((a, b) => (view.removed.has(b.id) ? 1 : 0) - (view.removed.has(a.id) ? 1 : 0));
    for (const w of ordered) {
      const st = wallState(w, view, selected);
      if (!st) continue;
      const cls = ['wall', st, st === 'pending' ? 'flagged' : ''];
      if (w.user) cls.push('user');
      const g = mk('g', { class: cls.join(' '), 'data-type': 'wall', 'data-id': w.id }, gWalls);
      const xy = { x1: w.a[0], y1: w.a[1], x2: w.b[0], y2: w.b[1] };
      mk('line', { ...xy, class: 'hit', 'vector-effect': 'non-scaling-stroke' }, g);
      mk('line', { ...xy, class: 'stroke', 'vector-effect': 'non-scaling-stroke' }, g);
    }
  }
  function drawRooms() {
    gRooms.replaceChildren();
    const ans = new Set(view.answer?.rooms || []);
    for (const r of d.rooms) {
      const on = selected?.type === 'room' && selected.id === r.id;
      mk('polygon', { points: r.polygon.map((p) => p.join(',')).join(' '), class: `room${on ? ' selected' : ''}${ans.has(r.id) ? ' answer' : ''}`,
        'data-type': 'room', 'data-id': r.id }, gRooms);
    }
  }
  function badge(parent, [mx, my], [nx, ny], dist, text, cls, data = {}) {
    const g = mk('g', { class: `badge ${cls}`, 'data-off': JSON.stringify([mx, my, nx, ny, dist]), ...data }, parent);
    mk('circle', { r: 11 }, g);
    mk('text', { 'text-anchor': 'middle', 'dominant-baseline': 'central' }, g).textContent = text;
    return g;
  }
  function drawMarks() {
    gMarks.replaceChildren();
    gEvidence.replaceChildren();
    const normal = (w) => {
      const mx = (w.a[0] + w.b[0]) / 2, my = (w.a[1] + w.b[1]) / 2;
      const horiz = Math.abs(w.a[1] - w.b[1]) < 1;
      const [iw, ih] = d.image.size;
      return [[mx, my], horiz ? [0, my < ih / 2 ? -1 : 1] : [mx < iw / 2 ? -1 : 1, 0]];
    };
    if (view.candidates?.length) {
      for (const c of view.candidates) {
        const w = d.walls.find((x) => x.id === c.id);
        const [m, n] = normal(w);
        badge(gMarks, m, n, 18, c.letter, c.id === view.ref ? 'cand on' : 'cand', { 'data-type': 'wall', 'data-id': w.id });
      }
    } else if (view.refActive) {
      const w = d.walls.find((x) => x.id === view.ref);
      if (w && !view.removed.has(w.id)) {
        const [[mx, my], [nx, ny]] = normal(w);
        const g = mk('g', { 'data-off': JSON.stringify([mx, my, nx, ny, 16]) }, gMarks);
        mk('text', { class: `ref-label${view.refConfirmed ? ' confirmed' : ''}`, 'text-anchor': 'middle', 'dominant-baseline': 'middle' }, g).textContent = view.refLabel;
      }
    }
    for (const b of view.answer?.boxes || []) {
      const [x0, y0, x1, y1] = b.box;
      mk('rect', { x: x0 - 3, y: y0 - 3, width: x1 - x0 + 6, height: y1 - y0 + 6, rx: 3, class: 'evidence' }, gEvidence);
      badge(gMarks, [x1 + 3, y0 - 3], [0.7, -0.7], 6, String(b.n), 'cite');
    }
  }
  function drawReview() {
    gReview.replaceChildren();
    for (const it of view?.review?.items || []) setRect(mk('rect', { class: `rbox ${it.state}`, 'data-type': 'box', 'data-id': it.id }, gReview), it.box);
    if (!view?.review) { svg.classList.remove('edge-hover'); svg.style.cursor = ''; }
  }
  const redraw = () => { drawRooms(); drawWalls(); drawReview(); drawMarks(); setVB(); };

  new ResizeObserver(() => fit()).observe(el);

  return {
    load(drawing, v) {
      d = drawing; view = v; selected = null;
      img.setAttribute('href', d.image.url);
      img.setAttribute('width', d.image.size[0]); img.setAttribute('height', d.image.size[1]);
      redraw(); fit();
    },
    update(v) { view = v; redraw(); },
    highlight(sel) { selected = sel; drawRooms(); drawWalls(); },
    focus(id) {
      const w = d.walls.find((x) => x.id === id);
      if (!w) return;
      const x0 = Math.min(w.a[0], w.b[0]), y0 = Math.min(w.a[1], w.b[1]);
      fit([x0, y0, Math.max(40, Math.abs(w.b[0] - w.a[0])), Math.max(40, Math.abs(w.b[1] - w.a[1]))]);
    },
    fitBox(box, pad = 0.35) { fit(box, pad); },
    fit: () => fit(),
    setMode(m) { mode = m; drawStart = null; preview.setAttribute('visibility', 'hidden'); boxPreview.setAttribute('visibility', 'hidden'); svg.classList.toggle('drawing', m === 'draw' || m === 'box'); },
    onPick(f) { handlers.pick = f; },
    onHover(f) { handlers.hover = f; },
    onDraw(f) { handlers.draw = f; },
    onDrawBox(f) { handlers.drawBox = f; },
    onResize(f) { handlers.resize = f; },
  };
}

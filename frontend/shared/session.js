// Shared app state: one drawing, its scale, wall decisions, selection, answer highlights and box review.
import { loadDrawing, loadBuildTrace } from './api.js';

export const wallPx = (w) => Math.hypot(w.b[0] - w.a[0], w.b[1] - w.a[1]);

export function polyAreaPx(p) {
  let a = 0;
  for (let i = 0; i < p.length; i++) {
    const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length];
    a += x1 * y2 - x2 * y1;
  }
  return Math.abs(a) / 2;
}

export function centroid(p) {
  let x = 0, y = 0, a = 0;
  for (let i = 0; i < p.length; i++) {
    const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length];
    const c = x1 * y2 - x2 * y1;
    a += c; x += (x1 + x2) * c; y += (y1 + y2) * c;
  }
  return a ? [x / (3 * a), y / (3 * a)] : p[0];
}

export function insidePoly([x, y], p) {
  let inside = false;
  for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
    const [xi, yi] = p[i], [xj, yj] = p[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export { loadLibrary } from './api.js';

const LETTERS = 'ABCDEF';
export const FLAG_BELOW = 0.85; // walls under this confidence are shown red for the user to check
export const REVIEW_BELOW = 0.8; // box-count answers under this confidence ask the user to check the boxes
export const BOX_CHECK_BELOW = 0.7; // counted boxes under this score are shown red until the user touches them

const NOUNS = { window: ['window', 'windows'], door: ['door', 'doors'], toilet_bowl: ['toilet', 'toilets'], sink: ['sink', 'sinks'],
  bathtub: ['bathtub', 'bathtubs'], shower: ['shower', 'showers'] };
export function countNoun(cls, n) {
  const name = String(cls).replaceAll('_', ' ');
  return (NOUNS[cls] || [name, `${name}s`])[n === 1 ? 0 : 1];
}
const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);

// A box-count answer the user should check: the backend sent its boxes and a confidence under REVIEW_BELOW.
export function needsReview(a) {
  return a?.status === 'answered' && Array.isArray(a.review?.boxes) && typeof a.confidence === 'number' && a.confidence < REVIEW_BELOW;
}

// Display state of one box under review: counted (keep), counted but doubtful (check) or not counted (off).
export function boxState(it) {
  if (!it.counted) return 'off';
  return !it.touched && !it.user && it.score < BOX_CHECK_BELOW ? 'check' : 'keep';
}

const isHoriz = (w) => Math.abs(w.a[1] - w.b[1]) < 1;
const longestHorizontal = (walls) => [...walls].filter(isHoriz).sort((a, b) => wallPx(b) - wallPx(a))[0] || walls[0];

export function formatValue(a) {
  if (a.value == null) return '';
  const v = Number(a.value), n = Number.isInteger(v) ? String(v) : v.toFixed(v >= 100 ? 0 : 2);
  return a.unit === 'm2' ? `${n} m²` : a.unit === 'm' ? `${n} m` : a.unit === 'm3' ? `${n} m³` : n;
}

export function createSession(opts = {}) {
  const listeners = new Set();
  const s = { d: null, build: [], scale: 1, height: 2.7, scaleConfirmed: false, decisions: new Map(), selected: null,
    added: 0, focus: 'scale', refId: null, answer: null, answerPayload: null, review: null, reviewShown: true };
  const emit = (type) => listeners.forEach((f) => f(type, api));
  const removedIds = () => new Set([...s.decisions].filter(([, k]) => !k).map(([id]) => id));

  const api = {
    state: s,
    opts,
    on(f) { listeners.add(f); return () => listeners.delete(f); },
    // Loads a drawing payload plus its build trace from the API.
    async open(id) {
      const [d, build] = await Promise.all([loadDrawing(id), loadBuildTrace(id)]);
      d.texts ??= []; d.symbols ??= []; d.openings ??= []; d.rooms ??= [];
      const below = opts.flagBelow ?? FLAG_BELOW;
      for (const w of d.walls) w.flag = w.confidence != null && w.confidence < below ? 'low_confidence' : null;
      Object.assign(s, { d, build, scale: d.scale.m_per_px, height: d.default_height_m ?? 2.7, scaleConfirmed: false,
        decisions: new Map(), selected: null, added: 0, focus: 'scale', refId: longestHorizontal(d.walls)?.id,
        answer: null, answerPayload: null, review: null });
      emit('load');
    },
    // Reference wall for scale. With opts.pickRef the user chooses among a few long, clear candidates.
    candidates() {
      if (!opts.pickRef || !s.d) return [];
      const rm = removedIds();
      const walls = s.d.walls.filter((w) => !rm.has(w.id) && !w.user).sort((a, b) => wallPx(b) - wallPx(a));
      const horiz = isHoriz;
      const pick = [], seen = [];
      for (const want of [true, false, true, false, true, false]) {
        const w = walls.find((x) => horiz(x) === want && !pick.includes(x)
          && !seen.some((p) => Math.abs(wallPx(p) - wallPx(x)) < 4 && horiz(p) === horiz(x)));
        if (w) { pick.push(w); seen.push(w); }
        if (pick.length === 4) break;
      }
      return pick.map((w, i) => ({ id: w.id, letter: LETTERS[i] }));
    },
    refWall() { return s.d.walls.find((w) => w.id === s.refId); },
    refLength() { return wallPx(api.refWall()) * s.scale; },
    setRef(id) { if (s.refId !== id) { s.refId = id; emit('scale'); } },
    setRefLength(m) {
      if (!(m > 0)) return;
      s.scale = m / wallPx(api.refWall());
      emit('scale');
    },
    confirmScale() { s.scaleConfirmed = true; emit('scale'); },
    // What the views emphasise: 'scale' (reference wall), 'walls' (wall check) or null.
    setFocus(f) { if (s.focus !== f) { s.focus = f; emit('walls'); } },
    setHeight(h) { if (h > 0.5 && h < 20) { s.height = h; emit('walls'); } },
    flagged() { return s.d.walls.filter((w) => w.flag); },
    pending() { return api.flagged().filter((w) => !s.decisions.has(w.id)); },
    decide(id, keep) { s.decisions.set(id, keep); emit('walls'); },
    // One click: wall -> not a wall; again: not a wall -> confirmed wall.
    toggleWall(id) { s.decisions.set(id, s.decisions.get(id) === false); emit('walls'); },
    keepRest() { api.pending().forEach((w) => s.decisions.set(w.id, true)); emit('walls'); },
    removed: removedIds,
    addWall(a, b) {
      s.added += 1;
      const w = { id: `u${s.added}`, a, b, flag: null, user: true };
      s.d.walls.push(w);
      s.decisions.set(w.id, true);
      emit('walls');
      return w;
    },
    select(sel) {
      const same = sel && s.selected && sel.type === s.selected.type && sel.id === s.selected.id;
      s.selected = same ? null : sel;
      emit('select');
    },
    // a = answer payload (spec) or null. Views get the derived marks (rooms, walls, boxes, 3D markers).
    setAnswer(a) { s.answerPayload = a; s.answer = answerView(a); emit('answer'); },
    citationBox(c) {
      if (c.box) return c.box;
      if (c.text_id) return s.d.texts.find((x) => x.id === c.text_id)?.box;
      if (c.symbol_id) return s.d.symbols.find((x) => x.id === c.symbol_id)?.box;
      if (c.opening_id) return s.d.openings.find((x) => x.id === c.opening_id)?.box;
      const poly = c.room_id && s.d.rooms.find((x) => x.id === c.room_id)?.polygon;
      if (poly) return [Math.min(...poly.map((p) => p[0])), Math.min(...poly.map((p) => p[1])), Math.max(...poly.map((p) => p[0])), Math.max(...poly.map((p) => p[1]))];
      return null;
    },
    // Box review for a low-confidence count: the model's boxes start pre-filled; the user counts or drops,
    // resizes and adds boxes, then finishes (recount + label record) or skips (model answer, unchecked).
    startReview(a, question = '') {
      const t = a.review.threshold ?? 0;
      s.review = { answer: a, question, cls: a.review.class, threshold: t, added: 0,
        items: a.review.boxes.map((b) => ({ id: String(b.id), box: [...b.box], orig: [...b.box], score: b.score,
          counted: b.score >= t, touched: false, user: false })) };
      emit('review');
    },
    showReview(on) { if (s.reviewShown !== on) { s.reviewShown = on; emit('review'); } },
    reviewStats() {
      const items = s.review?.items || [];
      return { count: items.filter((i) => i.counted).length, toCheck: items.filter((i) => boxState(i) === 'check').length };
    },
    // Click: a model box flips counted / not counted; a box the user drew is deleted.
    toggleBox(id) {
      const r = s.review, it = r?.items.find((i) => i.id === id);
      if (!it) return;
      if (it.user) r.items = r.items.filter((i) => i !== it);
      else { it.counted = !it.counted; it.touched = true; }
      emit('review');
    },
    // Resizing a box means it is a real object, so it counts.
    resizeBox(id, box) {
      const it = s.review?.items.find((i) => i.id === id), b = it && clampBox(box);
      if (!b) return;
      Object.assign(it, { box: b, counted: true, touched: true });
      emit('review');
    },
    addBox(box) {
      const r = s.review, b = r && clampBox(box);
      if (!b) return null;
      r.added += 1;
      const it = { id: `u${r.added}`, box: b, orig: null, score: null, counted: true, touched: true, user: true };
      r.items.push(it);
      emit('review');
      return it;
    },
    finishReview() {
      const r = s.review;
      if (!r) return null;
      const kept = r.items.filter((i) => i.counted), model = r.items.filter((i) => !i.user);
      const round = (b) => b.map((v) => Math.round(v));
      const changes = {
        removed: model.filter((i) => i.score >= r.threshold && !i.counted).length,
        added: kept.filter((i) => i.user || i.score < r.threshold).length,
        resized: model.filter((i) => round(i.box).some((v, k) => v !== Math.round(i.orig[k]))).length,
      };
      const n = kept.length, cites = n > 1 ? `<cite>1</cite>–<cite>${n}</cite>` : n ? '<cite>1</cite>' : '';
      const one = cap(countNoun(r.cls, 1));
      const answer = { ...r.answer, value: n, checked: true, focus: null,
        text: n ? `<b>${n}</b> ${countNoun(r.cls, n)}, checked by you ${cites}.` : `No ${countNoun(r.cls, 2)}, checked by you.`,
        citations: kept.map((i, k) => ({ n: k + 1, label: i.user ? `${one} · added by you` : `${one} · ${Math.round(i.score * 100)}%`, box: round(i.box) })) };
      // Objects follow the stoahackjunction annotation workbench schema so they can join its training data.
      const record = {
        schema: 'drawing-qa-review-v1',
        drawing: { id: s.d.id, title: s.d.title, dataset: s.d.source?.dataset, image_size: s.d.image.size },
        question: r.question, class: r.cls, user_count: n,
        model: { value: r.answer.value, confidence: r.answer.confidence, threshold: r.threshold,
          scores: Object.fromEntries(model.map((i) => [i.id, i.score])) },
        objects: r.items.map((i) => ({ id: i.id, class: r.cls, bbox_xyxy: round(i.box), subtype: '',
          status: i.counted ? 'accepted' : 'rejected', ...(i.user ? {} : { proposal_id: i.id }) })),
        reviewed_at: new Date().toISOString(),
      };
      s.review = null;
      emit('review');
      return { answer, record, changes };
    },
    skipReview() {
      const a = s.review && { ...s.review.answer, unchecked: true };
      s.review = null;
      emit('review');
      return a;
    },
    roomArea(r) { return polyAreaPx(r.polygon) * s.scale ** 2; },
    areaCheck() {
      const errs = s.d.rooms.filter((r) => r.printed_area_m2)
        .map((r) => Math.abs(api.roomArea(r) - r.printed_area_m2) / r.printed_area_m2).sort((a, b) => a - b);
      return errs.length ? { n: errs.length, median: errs[Math.floor(errs.length / 2)] } : null;
    },
    describe(sel) {
      if (!sel || !s.d) return '';
      if (sel.type === 'box') {
        const it = s.review?.items.find((i) => i.id === sel.id);
        if (!it) return '';
        if (it.user) return 'Added by you · click to remove';
        return `${cap(countNoun(s.review.cls, 1))} · ${Math.round(it.score * 100)}%${it.counted ? '' : ' · not counted'}`;
      }
      if (sel.type === 'room') {
        const r = s.d.rooms.find((x) => x.id === sel.id);
        return `${r.label || 'Room'} · ${api.roomArea(r).toFixed(1)} m²`;
      }
      const w = s.d.walls.find((x) => x.id === sel.id);
      const len = `${(wallPx(w) * s.scale).toFixed(2)} m`;
      if (opts.pickRef && s.focus === 'walls') return s.decisions.get(w.id) === false ? 'Not a wall · click to restore' : `Wall · ${len} · click to remove`;
      if (opts.pickRef && s.focus === 'scale') {
        const c = api.candidates().find((x) => x.id === w.id);
        if (c) return `${c.letter} · ${len} · click to use`;
      }
      return `Wall · ${len}`;
    },
    viewState() {
      const pend = api.pending().map((w) => w.id);
      const review = s.review && s.reviewShown ? { items: s.review.items.map((i) => ({ id: i.id, box: i.box, state: boxState(i) })) } : null;
      return { scale: s.scale, height: s.height, focus: s.focus, removed: removedIds(), decisions: s.decisions,
        pending: new Set(s.focus === 'walls' ? pend : []), ref: s.refId,
        refLabel: `${api.refLength().toFixed(2)} m`, refConfirmed: s.scaleConfirmed, refActive: s.focus === 'scale',
        candidates: s.focus === 'scale' ? api.candidates() : [], showRemoved: !!opts.pickRef && s.focus === 'walls',
        showKept: !!opts.pickRef && s.focus === 'walls', review, answer: review ? reviewMarks() : s.answer };
    },
  };
  function clampBox(box) {
    const [w, h] = s.d.image.size;
    const x0 = Math.max(0, Math.min(box[0], box[2])), x1 = Math.min(w, Math.max(box[0], box[2]));
    const y0 = Math.max(0, Math.min(box[1], box[3])), y1 = Math.min(h, Math.max(box[1], box[3]));
    return x1 - x0 >= 2 && y1 - y0 >= 2 ? [x0, y0, x1, y1] : null;
  }
  // While reviewing, the 3D view numbers the boxes that currently count; the plan draws the review layer.
  function reviewMarks() {
    const kept = s.review.items.filter((i) => i.counted);
    return { rooms: [], walls: [], boxes: [],
      markers: kept.map((i, k) => ({ px: [(i.box[0] + i.box[2]) / 2, (i.box[1] + i.box[3]) / 2], text: String(k + 1), cls: 'cite', y: s.height + 0.4 })) };
  }
  function answerView(a) {
    if (!a || a.status !== 'answered') return null;
    const hl = a.highlight || {};
    const boxes = [], markers = [];
    for (const c of a.citations || []) {
      const box = c.room_id && !c.box ? null : api.citationBox(c);
      if (box) boxes.push({ n: c.n, box });
    }
    for (const id of hl.symbols || []) {
      const b = s.d.symbols.find((x) => x.id === id)?.box;
      if (b && !boxes.some((x) => x.box === b)) boxes.push({ n: '', box: b });
    }
    const rooms = hl.rooms || [];
    const mid = (b) => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
    if (rooms.length === 1 && a.value != null && a.unit !== 'count') {
      const r = s.d.rooms.find((x) => x.id === rooms[0]);
      if (r) markers.push({ px: centroid(r.polygon), text: `${r.label || 'Room'} · ${formatValue(a)}`, cls: 'label', y: s.height + 0.5 });
    } else if (boxes.length) {
      boxes.forEach((b) => markers.push({ px: mid(b.box), text: String(b.n), cls: 'cite', y: s.height + 0.4 }));
    } else {
      rooms.forEach((id, i) => {
        const r = s.d.rooms.find((x) => x.id === id);
        if (r) markers.push({ px: centroid(r.polygon), text: String(i + 1), cls: 'cite', y: s.height + 0.4 });
      });
    }
    return { rooms, walls: hl.walls || [], boxes, markers };
  }

  return api;
}

// Keep a 2D plan and/or 3D viewer in sync with the session; optional floating tooltip.
export function bindViews(session, { plan, viewer, tooltip, onPick }) {
  session.on((type) => {
    const s = session.state;
    if (type === 'load') {
      plan?.load(s.d, session.viewState());
      viewer?.load(s.d, session.viewState());
    } else if (type === 'scale' || type === 'walls' || type === 'answer' || type === 'review') {
      plan?.update(session.viewState());
      viewer?.update(session.viewState());
    } else if (type === 'select') {
      plan?.highlight(s.selected);
      viewer?.highlight(s.selected);
    }
  });
  if (tooltip) {
    const show = (sel, e) => {
      if (!sel) { tooltip.hidden = true; return; }
      tooltip.textContent = session.describe(sel);
      tooltip.hidden = false;
      tooltip.style.left = `${e.clientX + 14}px`;
      tooltip.style.top = `${e.clientY + 14}px`;
    };
    plan?.onHover(show);
    viewer?.onHover(show);
  }
  const pick = onPick || ((sel) => session.select(sel));
  plan?.onPick(pick);
  viewer?.onPick(pick);
  plan?.onDraw((a, b) => session.addWall(a, b));
  plan?.onDrawBox?.((box) => session.addBox(box));
  plan?.onResize?.((id, box) => session.resizeBox(id, box));
}

// Visual state of one wall, shared by the 2D and 3D views. null = not drawn.
export function wallState(w, view, selected) {
  const removed = view.removed.has(w.id);
  if (removed) return view.showRemoved ? 'removed' : null;
  if (selected?.type === 'wall' && selected.id === w.id) return 'selected';
  if (view.refActive && w.id === view.ref) return 'ref';
  if (view.candidates?.some((c) => c.id === w.id)) return 'cand';
  if (view.pending.has(w.id)) return 'pending';
  if (view.showKept && (view.decisions?.get(w.id) === true)) return 'keep';
  if (view.answer?.walls?.includes(w.id)) return 'answer';
  return 'normal';
}

// The active sheet's working state: its drawing, scale, wall decisions, selection, answer highlights and the
// low-confidence box review. An external store: React subscribes through `subscribe` / `version`.
import { loadBuildTrace, loadDrawing } from './api';
import type { Answer, Box, Citation, Drawing, Pt, TraceEvent, Wall } from './types';
import {countNoun} from './count-language';
import {applyReviews} from './answer-presentation';
import {detectedOpenings, windowLayout, withDetected, type ModelWindow} from './window-model';
export {countNoun} from './count-language';

export const wallPx = (w: Pick<Wall, 'a' | 'b'>) => Math.hypot(w.b[0] - w.a[0], w.b[1] - w.a[1]);

export function polyAreaPx(p: Pt[]) {
  let a = 0;
  for (let i = 0; i < p.length; i++) {
    const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length];
    a += x1 * y2 - x2 * y1;
  }
  return Math.abs(a) / 2;
}

export function centroid(p: Pt[]): Pt {
  let x = 0, y = 0, a = 0;
  for (let i = 0; i < p.length; i++) {
    const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length];
    const c = x1 * y2 - x2 * y1;
    a += c; x += (x1 + x2) * c; y += (y1 + y2) * c;
  }
  return a ? [x / (3 * a), y / (3 * a)] : p[0];
}

const LETTERS = 'ABCDEF';
export const FLAG_BELOW = 0.85; // walls under this confidence are shown red for the user to check
export const REVIEW_BELOW = 0.8; // box-count answers under this confidence ask the user to check the boxes
export const BOX_CHECK_BELOW = 0.7; // counted boxes under this score are shown red until the user touches them

const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

/** A box-count answer the user should check: the backend sent its boxes and a confidence under REVIEW_BELOW,
 *  or picked this confident answer for a spot check (so confidence is measured where the model is sure too). */
export function needsReview(a: Answer | null | undefined): boolean {
  if(a?.job_id)return a.status==='needs_review' && !!a.review?.boxes.length;
  return a?.status === 'answered' && Array.isArray(a.review?.boxes) && typeof a.confidence === 'number'
    && (a.confidence < REVIEW_BELOW || a.review?.audit === true);
}

export interface ReviewItem { id: string; box: Box; orig: Box | null; score: number | null; counted: boolean; touched: boolean; user: boolean }
export type BoxState = 'keep' | 'check' | 'off';
/** Display state of one box under review: counted (keep), counted but doubtful (check) or not counted (off). */
export function boxState(it: ReviewItem): BoxState {
  if (!it.counted) return 'off';
  return !it.touched && !it.user && (it.score ?? 1) < BOX_CHECK_BELOW ? 'check' : 'keep';
}

const isHoriz = (w: Wall) => Math.abs(w.a[1] - w.b[1]) < 1;
const longestHorizontal = (walls: Wall[]) => [...walls].filter(isHoriz).sort((a, b) => wallPx(b) - wallPx(a))[0] ?? walls[0];

export function formatValue(a: Answer) {
  if (a.value == null) return '';
  const v = Number(a.value), n = Number.isInteger(v) ? String(v) : v.toFixed(v >= 100 ? 0 : 2);
  return a.unit === 'm2' ? `${n} m²` : a.unit === 'm' ? `${n} m` : a.unit === 'm3' ? `${n} m³` : n;
}

export type Focus = 'scale' | 'walls' | null;
export type Selection = { type: 'wall' | 'room' | 'box' | 'sheet'; id: string } | null;
export interface Marker { px: Pt; text: string; cls?: string; y?: number }
export interface AnswerMarks { rooms: string[]; walls: string[]; boxes: { n: number | string; box: Box }[]; markers: Marker[]; regions?: {polygon: Pt[]; holes: Pt[][]}[]; segments?: [Pt,Pt][] }
export interface Review { answer: Answer; question: string; cls: string; threshold: number; added: number; items: ReviewItem[]; saved?: ReviewRecord }
export interface ReviewRecord {
  schema: 'drawing-qa-review-v1';
  drawing: { id: string; title: string; dataset?: string; image_size: Pt };
  question: string; class: string; user_count: number;
  model: { value: Answer['value']; confidence?: number | null; threshold: number; scores: Record<string, number | null>;
    boxes: Record<string, number[]>; audit?: true };
  objects: { id: string; class: string; bbox_xyxy: number[]; subtype: string; status: 'accepted' | 'rejected'; proposal_id?: string }[];
  job_id?: string;
  sheet_id?: string;
  id?: number;
  revision_of?: number;
  reviewer?: string;
  server_received_at?: string;
  reviewed_at: string;
}

export interface SessionState {
  d: Drawing | null;
  build: TraceEvent[];
  scale: number;
  height: number;
  scaleConfirmed: boolean;
  decisions: Map<string, boolean>;
  selected: Selection;
  added: number;
  focus: Focus;
  refId: string | null;
  answer: AnswerMarks | null;
  answerPayload: Answer | null;
  review: Review | null;
  reviewShown: boolean;
  verifiedWindows: ModelWindow[];
}
export type SessionEvent = 'load' | 'scale' | 'walls' | 'select' | 'answer' | 'review';
export type Snapshot = Pick<SessionState, 'd' | 'build' | 'scale' | 'height' | 'scaleConfirmed' | 'decisions' | 'added' | 'refId' | 'verifiedWindows'>;

/** What the 2D and 3D views draw for the current state. */
export interface ViewState {
  scale: number; height: number; focus: Focus;
  removed: Set<string>; decisions: Map<string, boolean>; pending: Set<string>;
  ref: string | null; refLabel: string; refConfirmed: boolean; refActive: boolean;
  candidates: { id: string; letter: string }[];
  showRemoved: boolean; showKept: boolean;
  review: { items: { id: string; box: Box; state: BoxState }[] } | null;
  answer: AnswerMarks | null;
  windows: ModelWindow[];
}

export function createSession(opts: { pickRef?: boolean; flagBelow?: number } = {}) {
  const listeners = new Set<(type: SessionEvent) => void>();
  const s: SessionState = { d: null, build: [], scale: 1, height: 2.7, scaleConfirmed: false, decisions: new Map(), selected: null,
    added: 0, focus: 'scale', refId: null, answer: null, answerPayload: null, review: null, reviewShown: true, verifiedWindows:[] };
  let version = 0;
  const emit = (type: SessionEvent) => { version += 1; listeners.forEach((f) => f(type)); };
  const removedIds = () => new Set([...s.decisions].filter(([, k]) => !k).map(([id]) => id));
  const drawing = () => { if (!s.d) throw new Error('No drawing open'); return s.d; };
  // While windows are being reviewed, the review's boxes stand in for windows (detected doors stay).
  // Otherwise people's reviewed windows, plus the doors and windows Build detected.
  const modelWindows = (): ModelWindow[] => {
    const detected = s.d ? detectedOpenings(s.d, s.d.id) : [];
    return s.reviewShown && s.review?.cls === 'window'
      ? [...s.review.items.filter(i=>i.counted).map(i=>({id:i.id,sheet:s.d!.id,box:i.box,verified:false})), ...detected.filter(o=>o.kind==='door')]
      : withDetected(s.verifiedWindows, detected);
  };

  function clampBox(box: Box): Box | null {
    const [w, h] = drawing().image.size;
    const x0 = Math.max(0, Math.min(box[0], box[2])), x1 = Math.min(w, Math.max(box[0], box[2]));
    const y0 = Math.max(0, Math.min(box[1], box[3])), y1 = Math.min(h, Math.max(box[1], box[3]));
    return x1 - x0 >= 2 && y1 - y0 >= 2 ? [x0, y0, x1, y1] : null;
  }
  // While reviewing, the 3D view numbers the boxes that currently count; the plan draws the review layer.
  function reviewMarks(): AnswerMarks {
    const kept = s.review!.items.filter((i) => i.counted);
    return { rooms: [], walls: [], boxes: [],
      markers: kept.map((i, k) => ({ px: [(i.box[0] + i.box[2]) / 2, (i.box[1] + i.box[3]) / 2], text: String(k + 1), cls: 'cite', y: s.height + 0.4 })) };
  }
  function answerView(a: Answer | null): AnswerMarks | null {
    if (!a || !['answered','needs_review'].includes(a.status) || !s.d) return null;
    const d = s.d, hl = a.highlight ?? {};
    const boxes: AnswerMarks['boxes'] = [], markers: Marker[] = [];
    for (const c of a.citations ?? []) {
      if(c.sheet && c.sheet!==d.id)continue;
      const box = c.room_id && !c.box ? null : api.citationBox(c);
      if (box) boxes.push({ n: c.n, box });
    }
    for (const id of hl.symbols ?? []) {
      const b = d.symbols.find((x) => x.id === id)?.box;
      if (b && !boxes.some((x) => x.box === b)) boxes.push({ n: '', box: b });
    }
    const rooms = hl.rooms ?? [];
    const mid = (b: Box): Pt => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
    if (rooms.length === 1 && a.value != null && a.unit !== 'count') {
      const r = d.rooms.find((x) => x.id === rooms[0]);
      if (r) markers.push({ px: centroid(r.polygon), text: `${r.label || 'Room'} · ${formatValue(a)}`, cls: 'label', y: s.height + 0.5 });
    } else if (boxes.length) {
      boxes.forEach((b) => markers.push({ px: mid(b.box), text: String(b.n), cls: 'cite', y: s.height + 0.4 }));
    } else {
      rooms.forEach((id, i) => {
        const r = d.rooms.find((x) => x.id === id);
        if (r) markers.push({ px: centroid(r.polygon), text: String(i + 1), cls: 'cite', y: s.height + 0.4 });
      });
    }
    const cited=(a.citations ?? []).filter(c=>!c.sheet || c.sheet===d.id);
    return { rooms, walls: hl.walls ?? [], boxes, markers,
      regions:cited.filter(c=>c.polygon).map(c=>({polygon:c.polygon!,holes:c.holes ?? []})),
      segments:cited.filter(c=>c.endpoints).map(c=>c.endpoints!) };
  }

  const api = {
    state: s,
    subscribe(f: (type: SessionEvent) => void) { listeners.add(f); return () => { listeners.delete(f); }; },
    version: () => version,
    /** Loads a drawing payload plus its build trace from the API. */
    async open(id: string) {
      const [d, build] = await Promise.all([loadDrawing(id), loadBuildTrace(id)]);
      d.texts ??= []; d.symbols ??= []; d.openings ??= []; d.rooms ??= [];
      const below = opts.flagBelow ?? FLAG_BELOW;
      for (const w of d.walls) w.flag = w.confidence != null && w.confidence < below ? 'low_confidence' : null;
      // a scale that comes from the model (an IFC-rendered sheet) needs no confirming
      Object.assign(s, { d, build, scale: d.scale.m_per_px, height: d.default_height_m ?? 2.7, scaleConfirmed: d.scale.source === 'model',
        decisions: new Map(), selected: null, added: 0, focus: 'scale', refId: longestHorizontal(d.walls)?.id ?? null,
        answer: null, answerPayload: null, review: null, verifiedWindows:[] });
      emit('load');
    },
    /** Switching sheets in a project keeps each sheet's Build decisions. */
    snapshot(): Snapshot | null {
      return s.d && { d: s.d, build: s.build, scale: s.scale, height: s.height, scaleConfirmed: s.scaleConfirmed,
        decisions: new Map(s.decisions), added: s.added, refId: s.refId, verifiedWindows:s.verifiedWindows };
    },
    restore(snap: Snapshot) {
      Object.assign(s, snap, { decisions: new Map(snap.decisions), selected: null, focus: 'scale', answer: null, answerPayload: null, review: null });
      emit('load');
    },
    /** Re-announce the open drawing, e.g. to views that mounted after it loaded. */
    reload() { if (s.d) emit('load'); },
    /** Placing a sheet fixes its scale. */
    setScale(m: number, confirmed = false) { if (!(m > 0)) return; s.scale = m; if (confirmed) s.scaleConfirmed = true; emit('scale'); },
    /** Reference walls for scale: a few long, clear candidates the user picks from. */
    candidates(): { id: string; letter: string }[] {
      if (!opts.pickRef || !s.d) return [];
      const rm = removedIds();
      const walls = s.d.walls.filter((w) => !rm.has(w.id) && !w.user).sort((a, b) => wallPx(b) - wallPx(a));
      const pick: Wall[] = [];
      for (const want of [true, false, true, false, true, false]) {
        const w = walls.find((x) => isHoriz(x) === want && !pick.includes(x)
          && !pick.some((p) => Math.abs(wallPx(p) - wallPx(x)) < 4 && isHoriz(p) === isHoriz(x)));
        if (w) pick.push(w);
        if (pick.length === 4) break;
      }
      return pick.map((w, i) => ({ id: w.id, letter: LETTERS[i] }));
    },
    refWall: () => s.d?.walls.find((w) => w.id === s.refId),
    refLength() { const w = api.refWall(); return w ? wallPx(w) * s.scale : 0; },
    setRef(id: string) { if (s.refId !== id) { s.refId = id; emit('scale'); } },
    setRefLength(m: number) {
      const w = api.refWall();
      if (!(m > 0) || !w) return;
      s.scale = m / wallPx(w);
      emit('scale');
    },
    confirmScale() { s.scaleConfirmed = true; emit('scale'); },
    /** What the views emphasise: 'scale' (reference wall), 'walls' (wall check) or null. */
    setFocus(f: Focus) { if (s.focus !== f) { s.focus = f; emit('walls'); } },
    setHeight(h: number) { if (h > 0.5 && h < 20) { s.height = h; emit('walls'); } },
    flagged: () => s.d?.walls.filter((w) => w.flag) ?? [],
    pending: () => api.flagged().filter((w) => !s.decisions.has(w.id)),
    /** One click: wall → not a wall; again: not a wall → confirmed wall. */
    toggleWall(id: string) { s.decisions.set(id, s.decisions.get(id) === false); emit('walls'); },
    keepRest() { api.pending().forEach((w) => s.decisions.set(w.id, true)); emit('walls'); },
    removed: removedIds,
    addWall(a: Pt, b: Pt) {
      s.added += 1;
      const w: Wall = { id: `u${s.added}`, a, b, flag: null, user: true };
      drawing().walls.push(w);
      s.decisions.set(w.id, true);
      emit('walls');
      return w;
    },
    select(sel: Selection) {
      const same = sel && s.selected && sel.type === s.selected.type && sel.id === s.selected.id;
      s.selected = same ? null : sel;
      emit('select');
    },
    /** a = answer payload or null. Views get the derived marks (rooms, walls, boxes, 3D markers). */
    setAnswer(a: Answer | null) { s.answerPayload = a; s.answer = answerView(a); emit('answer'); },
    setVerifiedWindows(windows: ModelWindow[]) { s.verifiedWindows=windows;emit('review'); },
    windowSummary() {
      const layout=windowLayout(s.d?.walls ?? [],modelWindows(),removedIds());
      const detected=layout.placed.filter(w=>w.detected);
      // Reviewed and detected openings are counted apart: only people's labels are "verified".
      return {placed:layout.placed.filter(w=>!w.detected).length,unplaced:layout.unplaced.filter(w=>!w.detected).length,
        detectedWindows:detected.filter(w=>w.kind!=='door').length,detectedDoors:detected.filter(w=>w.kind==='door').length,
        preview:s.reviewShown && s.review?.cls==='window'};
    },
    citationBox(c: Citation): Box | null {
      const d = s.d;
      if (!d) return null;
      if (c.sheet && c.sheet!==d.id)return null;
      if (c.box) return c.box;
      if (c.text_id) return d.texts.find((x) => x.id === c.text_id)?.box ?? null;
      if (c.symbol_id) return d.symbols.find((x) => x.id === c.symbol_id)?.box ?? null;
      if (c.opening_id) return d.openings.find((x) => x.id === c.opening_id)?.box ?? null;
      const poly = c.room_id ? d.rooms.find((x) => x.id === c.room_id)?.polygon : null;
      if (poly) return [Math.min(...poly.map((p) => p[0])), Math.min(...poly.map((p) => p[1])), Math.max(...poly.map((p) => p[0])), Math.max(...poly.map((p) => p[1]))];
      return null;
    },
    /** Box review: the model's boxes start pre-filled; the user counts or drops, resizes and adds boxes. */
    startReview(a: Answer, question = '', saved?: ReviewRecord) {
      const t = a.review!.threshold ?? 0;
      s.review = { answer: a, question, cls: a.review!.class, threshold: t, added: 0,
        items: a.review!.boxes.map((b) => ({ id: String(b.id), box: [...b.box] as Box, orig: [...b.box] as Box, score: b.score,
          counted: b.accepted ?? (b.score ?? 0) >= t, touched: false, user: false })) };
      if (saved) {
        s.review.saved = saved;
        for (const it of s.review.items) {
          const o = saved.objects.find(o => o.proposal_id === it.id);
          if (o) Object.assign(it, {box:[...o.bbox_xyxy] as Box,counted:o.status === 'accepted',touched:true});
          else Object.assign(it,{counted:false,touched:true});
        }
        for (const o of saved.objects.filter(o => !o.proposal_id)) {
          s.review.items.push({id:o.id,box:[...o.bbox_xyxy] as Box,orig:null,score:null,counted:o.status === 'accepted',touched:true,user:true});
        }
      }
      emit('review');
    },
    showReview(on: boolean) { if (s.reviewShown !== on) { s.reviewShown = on; emit('review'); } },
    reviewStats() {
      const items = s.review?.items ?? [];
      return { count: items.filter((i) => i.counted).length, toCheck: items.filter((i) => boxState(i) === 'check').length };
    },
    /** Click: a model box flips counted / not counted; a box the user drew is deleted. */
    toggleBox(id: string) {
      const r = s.review, it = r?.items.find((i) => i.id === id);
      if (!r || !it) return;
      if (it.user) r.items = r.items.filter((i) => i !== it);
      else { it.counted = !it.counted; it.touched = true; }
      emit('review');
    },
    /** Resizing a box means it is a real object, so it counts. */
    resizeBox(id: string, box: Box) {
      const it = s.review?.items.find((i) => i.id === id), b = it && clampBox(box);
      if (!it || !b) return;
      Object.assign(it, { box: b, counted: true, touched: true });
      emit('review');
    },
    addBox(box: Box): ReviewItem | null {
      const r = s.review, b = r && clampBox(box);
      if (!r || !b) return null;
      do { r.added += 1; } while (r.items.some(i => i.id === `u${r.added}`));
      const it: ReviewItem = { id: `u${r.added}`, box: b, orig: null, score: null, counted: true, touched: true, user: true };
      r.items.push(it);
      emit('review');
      return it;
    },
    finishReview(keepOpen = false): { answer: Answer; record: ReviewRecord; changes: { removed: number; added: number; resized: number } } | null {
      const r = s.review, d = s.d;
      if (!r || !d) return null;
      const kept = r.items.filter((i) => i.counted), model = r.items.filter((i) => !i.user);
      const round = (b: Box) => b.map((v) => Math.round(v));
      const changes = {
        removed: model.filter((i) => (i.score ?? 0) >= r.threshold && !i.counted).length,
        added: kept.filter((i) => i.user || (i.score ?? 0) < r.threshold).length,
        resized: model.filter((i) => round(i.box).some((v, k) => v !== Math.round(i.orig![k]))).length,
      };
      const n = kept.length, cites = n > 1 ? `<cite>1</cite>–<cite>${n}</cite>` : n ? '<cite>1</cite>' : '';
      const one = cap(countNoun(r.cls, 1));
      const answer: Answer = { ...r.answer, value: n, checked: true, focus: null,
        text: n ? `I found <b>${n}</b> ${countNoun(r.cls, n)} ${cites}.` : `I found 0 ${countNoun(r.cls, 0)}.`,
        citations: kept.map((i, k) => ({ n: k + 1, label: `${one} · human-verified label`, box: round(i.box) as Box })) };
      // Objects follow the stoahackjunction annotation workbench schema so they can join its training data.
      const record: ReviewRecord = {
        schema: 'drawing-qa-review-v1',
        ...(r.answer.job_id?{job_id:r.answer.job_id,sheet_id:d.id}:{}),
        ...(r.saved?.id ? {revision_of:r.saved.id} : {}),
        drawing: { id: d.id, title: d.title, dataset: d.source?.dataset, image_size: d.image.size },
        question: r.question, class: r.cls, user_count: n,
        model: { value: r.answer.value, confidence: r.answer.confidence, threshold: r.threshold,
          scores: Object.fromEntries(model.map((i) => [i.id, i.score])),
          boxes: Object.fromEntries(model.map((i) => [i.id, round(i.orig ?? i.box)])),
          ...(r.answer.review?.audit ? { audit: true as const } : {}) },
        objects: r.items.map((i) => ({ id: i.id, class: r.cls, bbox_xyxy: round(i.box), subtype: '',
          status: i.counted ? 'accepted' as const : 'rejected' as const, ...(i.user ? {} : { proposal_id: i.id }) })),
        reviewed_at: new Date().toISOString(),
      };
      const shown: Answer=r.answer.job_id ? applyReviews(r.answer,[record]) : answer;
      if(!keepOpen)s.review = null;
      emit('review');
      return { answer:shown, record, changes };
    },
    skipReview(): Answer | null {
      const a = s.review && { ...s.review.answer, unchecked: true };
      s.review = null;
      emit('review');
      return a;
    },
    endReview() { s.review=null;emit('review'); },
    roomArea: (r: { polygon: Pt[] }) => polyAreaPx(r.polygon) * s.scale ** 2,
    areaCheck() {
      const errs = (s.d?.rooms ?? []).filter((r) => r.printed_area_m2)
        .map((r) => Math.abs(api.roomArea(r) - r.printed_area_m2!) / r.printed_area_m2!).sort((a, b) => a - b);
      return errs.length ? { n: errs.length, median: errs[Math.floor(errs.length / 2)] } : null;
    },
    describe(sel: Selection): string {
      const d = s.d;
      if (!sel || !d) return '';
      if (sel.type === 'box') {
        const it = s.review?.items.find((i) => i.id === sel.id);
        if (!it) return '';
        if (it.user) return 'Added by you · click to remove';
        return `${cap(countNoun(s.review!.cls, 1))}${it.score==null?'':` · score ${it.score.toFixed(2)}`}${it.counted ? '' : ' · not counted'}`;
      }
      if (sel.type === 'room') {
        const r = d.rooms.find((x) => x.id === sel.id);
        return r ? `${r.label || 'Room'} · ${api.roomArea(r).toFixed(1)} m²` : '';
      }
      if (sel.type === 'sheet') return 'Open this sheet';
      const w = d.walls.find((x) => x.id === sel.id);
      if (!w) return '';
      const len = `${(wallPx(w) * s.scale).toFixed(2)} m`;
      if (opts.pickRef && s.focus === 'walls') return s.decisions.get(w.id) === false ? 'Not a wall · click to restore' : `Wall · ${len} · click to remove`;
      if (opts.pickRef && s.focus === 'scale') {
        const c = api.candidates().find((x) => x.id === w.id);
        if (c) return `${c.letter} · ${len} · click to use`;
      }
      return `Wall · ${len}`;
    },
    viewState(): ViewState {
      const pend = api.pending().map((w) => w.id);
      const review = s.review && s.reviewShown ? { items: s.review.items.map((i) => ({ id: i.id, box: i.box, state: boxState(i) })) } : null;
      return { scale: s.scale, height: s.height, focus: s.focus, removed: removedIds(), decisions: s.decisions,
        pending: new Set(s.focus === 'walls' ? pend : []), ref: s.refId,
        refLabel: `${api.refLength().toFixed(2)} m`, refConfirmed: s.scaleConfirmed, refActive: s.focus === 'scale',
        candidates: s.focus === 'scale' ? api.candidates() : [], showRemoved: !!opts.pickRef && s.focus === 'walls',
        showKept: !!opts.pickRef && s.focus === 'walls', review, answer: review ? reviewMarks() : s.answer, windows:modelWindows() };
    },
  };
  return api;
}
export type Session = ReturnType<typeof createSession>;

export type WallLook = 'removed' | 'selected' | 'ref' | 'cand' | 'pending' | 'keep' | 'answer' | 'normal';
/** Visual state of one wall, shared by the 2D and 3D views. null = not drawn. */
export function wallState(w: Wall, view: ViewState, selected: Selection): WallLook | null {
  if (view.removed.has(w.id)) return view.showRemoved ? 'removed' : null;
  if (selected?.type === 'wall' && selected.id === w.id) return 'selected';
  if (view.refActive && w.id === view.ref) return 'ref';
  if (view.candidates.some((c) => c.id === w.id)) return 'cand';
  if (view.pending.has(w.id)) return 'pending';
  if (view.showKept && view.decisions.get(w.id) === true) return 'keep';
  if (view.answer?.walls?.includes(w.id)) return 'answer';
  return 'normal';
}

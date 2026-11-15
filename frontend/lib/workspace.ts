// The workspace for one building or drawing: which sheet is active, where each sheet sits, the ghosts, the
// Place step, the conversation. A zustand store for what React renders, plus the imperative 2D / 3D / trace
// views it drives. One per page, created by <Workspace>.
import { createStore } from 'zustand/vanilla';
import * as api from './api';
import {
  applyT, baseView, elevationFrame, footprint, ghostFor, ghostSlots, guessKind, isPlaceable, isPlaced, isPlan, kindOf,
  levelRole, levelsByZ, mergeOverlay, placementFor, scaleOf, singleSheetProject, solveSimilarity, transformOf, views,
  type Ghost, type Level, type Overlay, type Placement, type Project, type SheetView, type View,
} from './project';
import {
  createLocalProject, getLocalProject, loadOverlay, loadThread, nextLevel, saveOverlay, saveThread, touchRecent,
} from './persist';
import { createSession, needsReview, type ReviewRecord, type Selection, type Snapshot } from './session';
import {applyReviews, latestReviews, savedReview} from './answer-presentation';
import {reviewedWindows} from './window-model';
import { buildSteps } from './steps';
import { toast } from './toast';
import type { Plan } from './plan2d';
import type { Trace } from './trace';
import type { Answer, CountReview, Drawing, Pt, TraceEvent, LensStatus } from './types';
import type { CameraView, GhostShape, OtherSheet, SceneContext, Viewer } from './viewer3d';

export type Tab = 'build' | 'ask';
export type ViewMode = 'both' | 'one-plan' | 'one-model';
export interface Placing {
  sheet: Pt[];
  base: Pt[];
  step: 'sheet' | 'base' | 'check';
  prev: { patch?: Partial<View>; scale: number; confirmed: boolean };
  view?: CameraView;
}
export interface Entry {
  id: string;
  q: string;
  job_id?: string;
  request_id?: string;
  sheets?: string[];
  sheet: string;
  status: 'pending' | 'review' | 'done';
  stage?: string;
  answer?: Answer;
  modelAnswer?: Answer;
  records?: ReviewRecord[];
  events: TraceEvent[];
  at?: number;
  record?: ReviewRecord;
}

export interface WorkspaceState {
  status: 'loading' | 'ready' | 'error';
  error: string | null;
  project: Project | null;
  activeId: string | null;
  ghostSel: string | null;
  needsGhost: string | null;
  explode: boolean;
  isolate: boolean;
  hideGhosts: boolean;
  placing: Placing | null;
  tab: Tab;
  open: string | null;
  viewMode: ViewMode;
  drawing: boolean;
  thread: Entry[];
  busy: boolean;
  currentId: string | null;
  reviewingId: string | null;
  savingReview: boolean;
  traceOpen: boolean;
  traceStatus: string;
  traceBusy: boolean;
  suggestions: string[];
  askDot: boolean;
  selectedIds: string[];
  lensStatus: LensStatus | null;
}

const IDLE_TRACE = 'Ask a question to see how it’s answered';
/** The plan region of a sheet (its walls, else its plan box) as [x, y, w, h] in sheet px. */
function planBox(d: Drawing): [number, number, number, number] | null {
  const pts = d.walls.flatMap((w) => [w.a, w.b]);
  if (!pts.length) { const b = d.plans?.[0]?.box; return b ? [b[0], b[1], b[2] - b[0], b[3] - b[1]] : null; }
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)];
}
const DRAIN_QUESTION = 'Where does the main drain leave the building?';
const uid = () => Math.random().toString(36).slice(2, 10);

export function createWorkspace({ navigate }: { navigate: (url: string) => void }) {
  const session = createSession({ pickRef: true });
  const S = session.state;
  const store = createStore<WorkspaceState>(() => ({
    status: 'loading', error: null, project: null, activeId: null, ghostSel: null, needsGhost: null,
    explode: false, isolate: false, hideGhosts: false, placing: null, tab: 'build', open: null, viewMode: 'both',
    drawing: false, thread: [], busy: false, currentId: null, reviewingId: null, savingReview:false,
    traceOpen: true, traceStatus: IDLE_TRACE, traceBusy: false, suggestions: [], askDot: false, selectedIds: [], lensStatus: null,
  }));
  const get = store.getState, set = store.setState;

  // Not rendered by React: the manifest as served, this viewer's changes, loaded drawings, per-sheet Build state.
  let manifest: Project | null = null;
  let overlay: Overlay = {};
  const drawings = new Map<string, Drawing>();
  const snaps = new Map<string, Snapshot>();
  const sheetReviews = new Map<string, ReviewRecord[]>();
  const v3: { plan?: Plan; viewer?: Viewer; basePlan?: Plan; trace?: Trace } = {};
  let cancelAsk: (() => void) | null = null;
  let traceEntryId: string | null = null;
  let loadSeq = 0;

  /* ---------- Reading the project ---------- */
  const P = () => get().project;
  const viewOf = (id: string | null) => (P() && id ? views(P()!).find((v) => v.drawing_id === id && v.index === 0) ?? null : null);
  const levelOf = (id?: string | null) => P()?.levels.find((l) => l.id === id);
  const levelName = (id?: string | null) => levelOf(id)?.name ?? id ?? '';
  function levelY(id?: string | null) {
    const ls = levelsByZ(P()!), i = ls.findIndex((l) => l.id === id);
    return i < 0 ? 0 : ls[i].z_m + (get().explode ? i * 2.4 : 0);
  }
  const scaleFor = (id: string) => (id === get().activeId && S.d ? S.scale : snaps.get(id)?.scale ?? drawings.get(id)?.scale.m_per_px ?? 1);
  const tOf = (v: SheetView | null) => transformOf(v, scaleFor);
  const sheetLabel = (id: string | null) => {
    const d = id ? drawings.get(id) : undefined;
    return d ? `${d.sheet_number ? `${d.sheet_number} ` : ''}${d.title}` : id ?? '';
  };
  const isBase = () => !!P() && baseView(P()!)?.drawing_id === get().activeId;
  const merged = (): Project => {
    const m = mergeOverlay(manifest!, overlay);
    return { ...m, sheets: m.sheets.filter((s) => drawings.has(s.drawing_id)) };
  };
  const saveChanges = () => { saveOverlay(manifest!.id, overlay); set({ project: merged() }); };
  function patchView(id: string, patch: Partial<View>, { save = true } = {}) {
    const key = `${id}#0`;
    overlay = { ...overlay, patch: { ...overlay.patch, [key]: { ...(overlay.patch?.[key] ?? {}), ...patch } } };
    if (save) saveOverlay(manifest!.id, overlay);
    set({ project: merged() });
  }
  const defaultLevel = () => baseView(P()!)?.placement.level ?? levelsByZ(P()!).find((l) => levelRole(l) === 'storey')?.id ?? null;
  function footprintPoly() {
    const b = baseView(P()!);
    if (!b) return null;
    const d = b.drawing_id === get().activeId && S.d ? { ...S.d, walls: S.d.walls.filter((w) => !session.removed().has(w.id)) } : drawings.get(b.drawing_id);
    return footprint(d, tOf(b));
  }
  const ghosts = (): Ghost[] => (P() ? ghostSlots(P()!) : []);

  /** What the 3D view shows around the active sheet: where it sits, the other placed sheets, the ghosts. */
  function sceneFor(id: string): SceneContext {
    const { placing, isolate, ghostSel, needsGhost } = get();
    const project = P()!, v = viewOf(id);
    const placed = !!v && isPlaced(v);
    const activeY = placed && v.placement.type === 'plan' ? levelY(v.placement.level) : null;
    // sheets above this level fade so they don't hide it; an unplaced sheet uses the level it's meant for
    const fadeY = activeY ?? (v?.placement.type === 'plan' && v.placement.level ? levelY(v.placement.level) : levelY(defaultLevel()));
    const place: SceneContext['place'] = !placed ? { type: 'hidden' }
      : v.placement.type === 'plan' ? { type: 'plan', t: tOf(v)!, y: activeY!, opacity: placing?.step === 'check' ? 0.55 : 1 }
        : { type: 'elevation' };
    const onlyLevel = isolate && v?.placement.level ? v.placement.level : null;
    const others: OtherSheet[] = [];
    for (const o of views(project)) {
      if (o.index !== 0 || !isPlaced(o) || (o.drawing_id === id && o.placement.type === 'plan')) continue;
      if (onlyLevel && (o.placement.type !== 'plan' || o.placement.level !== onlyLevel)) continue;
      const d = drawings.get(o.drawing_id);
      if (!d) continue;
      if (o.placement.type === 'plan') {
        const y = levelY(o.placement.level);
        others.push({ id: o.drawing_id, drawing: d, place: { type: 'plan', t: tOf(o)!, y }, walls: o.kind === 'floor_plan',
          height: levelOf(o.placement.level)?.height_m ?? 3, faded: y > fadeY + 0.1, windows:reviewedWindows(sheetReviews.get(o.drawing_id) ?? [],o.drawing_id) });
      } else if (o.placement.type === 'elevation') {
        others.push({ id: o.drawing_id, drawing: d, place: { type: 'elevation', y: levelY(o.placement.level) },
          frame: elevationFrame(o.placement), active: o.drawing_id === id });
      }
    }
    const fp = footprintPoly();
    const ghostShapes: GhostShape[] = !fp || project.single ? [] : ghosts().filter((g) => !onlyLevel || g.level === onlyLevel).map((g) => {
      const y = levelY(g.level), hgt = levelOf(g.level)?.height_m ?? 3;
      const [y0, y1] = g.kind === 'roof' ? (g.onTop ? [y + hgt, y + hgt + 0.4] : [y, y + 0.4]) : g.kind === 'structure' ? [y - 0.3, y] : [y, y + hgt];
      return { id: g.id, kind: g.kind, polygon: fp, y0, y1, active: g.id === ghostSel || g.id === needsGhost };
    });
    return { place, walls: v?.kind === 'floor_plan', others, ghosts: ghostShapes };
  }
  function refreshScene(fit = false) {
    const id = get().activeId;
    if (P() && id && S.d) v3.viewer?.setScene(sceneFor(id), { fit });
  }

  /* ---------- Build steps and what the views emphasise ---------- */
  function placingHint() {
    const p = get().placing;
    return !p ? null : p.step === 'sheet' ? `Click point ${p.sheet.length + 1} on this sheet`
      : p.step === 'base' ? `Click point ${p.base.length + 1} on the base sheet` : 'Does it line up?';
  }
  function steps() {
    const b = P() ? baseView(P()!) : null;
    return buildSteps({ session, view: viewOf(get().activeId), isBase: isBase(), hasBase: !!b, baseLabel: sheetLabel(b?.drawing_id ?? null), levelName, placingHint: placingHint() });
  }
  function syncFocus() {
    const { tab, open } = get();
    const cur = tab === 'ask' ? null : open ?? steps().find((s) => s.state === 'you')?.id;
    session.setFocus(cur === 'confirm' ? 'scale' : cur === 'check' ? 'walls' : null);
  }

  /* ---------- Opening a building or a drawing ---------- */
  async function openProject(p: Project, { sheet }: { sheet?: string } = {}) {
    const seq = ++loadSeq;
    cancelAsk?.(); cancelAsk = null;
    manifest = p;
    overlay = loadOverlay(p.id) ?? {};
    drawings.clear(); snaps.clear();sheetReviews.clear();
    set({ status: 'loading', error: null, project: null, activeId: null, ghostSel: null, needsGhost: null, placing: null,
      busy: false, currentId: null, reviewingId: null, thread: [], suggestions: [] });
    const all = mergeOverlay(p, overlay);
    await Promise.all(all.sheets.map(async(s) => {
      const [drawing,records]=await Promise.all([api.loadDrawing(s.drawing_id).catch(()=>null),api.loadReviews(s.drawing_id).catch(()=>[])]);
      if(seq!==loadSeq)return;
      if(drawing)drawings.set(s.drawing_id,drawing);
      sheetReviews.set(s.drawing_id,records);
    }));
    if (seq !== loadSeq) return;
    const project = merged();
    if (!project.sheets.length) { set({ status: 'error', error: 'This building has no drawings left.' }); return; }
    const thread: Entry[] = loadThread(project.id).map((e) => ({ ...e, id: e.id ?? uid(), status: e.status ?? 'done' as const }));
    const remote=await api.loadJobs(project.id).catch(()=>[]);
    if(seq!==loadSeq)return;
    for(const job of remote){
      const found=thread.find(e=>e.job_id===job.id || e.request_id===job.request.request_id);
      const terminal=['completed','failed','cancelled','interrupted'].includes(job.status);
      const records=job.human_reviews ?? found?.records ?? [];
      const patch={job_id:job.id,request_id:job.request.request_id,modelAnswer:job.answer ?? undefined,records,
        record:records.at(-1) ?? found?.record,
        answer:job.answer ? applyReviews(job.answer,records) : found?.answer,status:terminal?'done' as const:'pending' as const};
      if(found)Object.assign(found,patch);
      else thread.push({id:job.id,q:job.request.question,sheet:job.request.drawing_id,sheets:job.request.selected_drawing_ids,events:[],at:Date.parse(job.created_at),...patch});
    }
    set({ project, thread, traceOpen: !!project.single, status: 'ready' });
    v3.viewer?.setKeepView(false);
    idleTrace();
    await selectSheet(sheet ?? baseView(project)?.drawing_id ?? project.sheets[0].drawing_id, { fit: true });
    if (seq !== loadSeq) return;
    // the layout settles after the first paint; frame the building again, then keep the user's camera
    requestAnimationFrame(() => requestAnimationFrame(() => v3.viewer?.fit()));
    v3.viewer?.setKeepView(true);
    void refreshLens();
    const pending=thread.find(e=>e.status==='pending' && e.job_id);
    if(pending)subscribeEntry(pending);
    const d = drawings.get(baseView(project)?.drawing_id ?? project.sheets[0].drawing_id);
    touchRecent({ id: project.single ? project.sheets[0].drawing_id : project.id, type: project.single ? 'drawing' : 'project', title: project.title, thumbnail: d?.image.url });
    // straight to Ask, unless the opening sheet still needs the user in Build (scale, walls, kind)
    setTab(thread.length || !steps().some((s) => s.state === 'you') ? 'ask' : 'build');
  }
  async function init(kind: 'building' | 'drawing', id: string) {
    try {
      if (kind === 'drawing') await openProject(singleSheetProject(await api.loadDrawing(id)));
      else if (id.startsWith('local-')) {
        const local = getLocalProject(id);
        if (!local) throw new Error('This building was started in another browser.');
        await openProject(local);
      } else await openProject(await api.loadProject(id));
    } catch (e) {
      const msg = (e as Error).message || '';
      set({ status: 'error', error: /unknown/i.test(msg) ? `We couldn’t find this ${kind}.` : msg || `This ${kind} could not be opened.` });
    }
  }

  async function selectSheet(id: string, { fit = false } = {}) {
    if (get().placing) cancelPlacing();
    if (get().reviewingId && id !== get().activeId) skipReview();
    set({ ghostSel: null });
    if (id === get().activeId) { refreshScene(fit); return; }
    const prev = get().activeId;
    if (prev && S.d) snaps.set(prev, session.snapshot()!);
    set({ activeId: id, open: null, selectedIds: [id] });
    const snap = snaps.get(id);
    if (snap) session.restore(snap); else await session.open(id);
    refreshScene(fit);
  }

  function selectGhost(id: string) {
    if (get().placing) cancelPlacing();
    const ghostSel = get().ghostSel === id ? null : id;
    set({ ghostSel, viewMode: ghostSel && get().viewMode === 'one-model' ? 'both' : get().viewMode });
    refreshScene();
    if (ghostSel) v3.viewer?.focusGhost(ghostSel);
  }

  /* ---------- Session events: what changes when the active sheet's Build state does ---------- */
  session.subscribe((type) => {
    if (type === 'load') {
      if(S.d)session.setVerifiedWindows(reviewedWindows(sheetReviews.get(S.d.id) ?? [],S.d.id));
      setDrawing(false);
      refreshSuggestions();
      const cur = currentEntry();
      if (get().tab === 'ask' && cur?.answer && (cur.sheets ?? [cur.sheet]).includes(get().activeId ?? '')) session.setAnswer(cur.answer);
      syncFocus();
    }
    if (type === 'scale' || (type === 'walls' && isBase())) refreshScene();
    if (type === 'walls' && S.focus === null && !session.pending().length && S.scaleConfirmed && get().tab !== 'ask') set({ askDot: true });
  });

  /* ---------- Build actions ---------- */
  function setOpen(id: string | null) {
    set({ open: id === 'check' && !S.scaleConfirmed ? 'confirm' : id });
    syncFocus();
  }
  function setKind(kind: string, level: string | null) {
    const v = viewOf(get().activeId)!;
    const next = placementFor({ kind }, level, isPlan(kind) && (!baseView(P()!) || isBase())).placement;
    const placement = (next.type === v.placement.type && next.type !== 'attached' ? { ...v.placement, level } : next) as Placement;
    patchView(v.drawing_id, { kind, kindSure: true, discipline: kindOf(kind).discipline ?? null, placement });
    set({ open: null });
    refreshScene();
    syncFocus();
  }
  function confirmScale(metres: number) {
    session.setRefLength(metres);
    session.confirmScale();
    setOpen(viewOf(get().activeId)?.kind === 'floor_plan' ? 'check' : null);
  }
  function finishWalls() { session.keepRest(); setDrawing(false); setOpen(null); }

  /* ---------- Place: two points on this sheet, the same two on the base sheet ---------- */
  function drawPoints() {
    const p = get().placing;
    v3.plan?.setPoints(p ? p.sheet.map((px, i) => ({ px, label: String(i + 1) })) : []);
    v3.basePlan?.setPoints(p ? p.base.map((px, i) => ({ px, label: String(i + 1) })) : []);
  }
  function startPlacing() {
    const b = baseView(P()!), id = get().activeId!;
    if (!b || b.drawing_id === id) { toast('Add a floor plan first: it becomes the base'); return; }
    setDrawing(false);
    set({ open: 'place', viewMode: 'both', placing: { sheet: [], base: [], step: 'sheet', prev: { patch: overlay.patch?.[`${id}#0`], scale: S.scale, confirmed: S.scaleConfirmed } } });
    const base = drawings.get(b.drawing_id)!;
    v3.basePlan?.load({ ...base, walls: [], rooms: [] }, session.viewState());
    // zoom both sheets to their plans: the points are easier to hit than on the whole sheet
    const baseBox = planBox(base), ownBox = S.d ? planBox(S.d) : null;
    if (baseBox) v3.basePlan?.fitBox(baseBox, 0.06);
    if (ownBox) v3.plan?.fitBox(ownBox, 0.06);
    v3.basePlan?.setMode('point');
    v3.plan?.setMode('point');
    drawPoints();
  }
  function addSheetPoint(p: Pt) {
    const cur = get().placing;
    if (cur?.step !== 'sheet') return;
    const sheet = [...cur.sheet, p];
    set({ placing: { ...cur, sheet, step: sheet.length === 2 ? 'base' : 'sheet' } });
    drawPoints();
  }
  function addBasePoint(p: Pt) {
    const cur = get().placing;
    if (cur?.step !== 'base') return;
    const base = [...cur.base, p];
    set({ placing: { ...cur, base } });
    if (base.length === 2) solvePlacing(); else drawPoints();
  }
  function solvePlacing() {
    const cur = get().placing!, v = viewOf(get().activeId)!, b = baseView(P()!)!;
    const q = cur.base.map((p) => applyT(tOf(b)!, p)) as [Pt, Pt];
    const ground = cur.sheet as [Pt, Pt];
    let placement: Placement;
    if (v.kind === 'elevation') placement = { type: 'elevation', level: v.placement.level ?? b.placement.level, base: q, sheet_ground: ground, by: 'user' };
    else {
      const t = solveSimilarity(ground, q);
      if (!t) { toast('Pick two different points'); cancelPlacing(); startPlacing(); return; }
      placement = { type: 'plan', level: v.placement.level ?? defaultLevel(), transform: t, by: 'user' };
    }
    patchView(v.drawing_id, { placement }, { save: false });
    set({ placing: { ...cur, step: 'check', view: v3.viewer?.saveView() } });
    v3.basePlan?.setMode('select');
    v3.plan?.setMode('select');
    if (placement.type === 'plan' && placement.transform) session.setScale(scaleOf(placement.transform));
    drawPoints();
    refreshScene();
    v3.viewer?.setMode('top');
  }
  function confirmPlacing() {
    const cur = get().placing, v = viewOf(get().activeId)!;
    saveOverlay(manifest!.id, overlay);
    set({ placing: null });
    if (v.placement.type === 'plan' && v.placement.transform) session.setScale(scaleOf(v.placement.transform), true);
    drawPoints();
    v3.plan?.fit();
    refreshScene();
    v3.viewer?.restoreView(cur?.view);
    setOpen('model');
    toast(`${sheetLabel(v.drawing_id)} is in the building`);
  }
  function cancelPlacing() {
    const cur = get().placing, id = get().activeId!;
    if (!cur) return;
    const key = `${id}#0`, patch = { ...overlay.patch };
    if (cur.prev.patch) patch[key] = cur.prev.patch; else delete patch[key];
    overlay = { ...overlay, patch };
    set({ placing: null, project: merged() });
    if (S.scale !== cur.prev.scale) session.setScale(cur.prev.scale);
    S.scaleConfirmed = cur.prev.confirmed;
    v3.basePlan?.setMode('select');
    v3.plan?.setMode('select');
    drawPoints();
    v3.plan?.fit();
    refreshScene();
    v3.viewer?.restoreView(cur.view);
  }
  function takeOut(id: string) {
    const v = viewOf(id);
    if (!v || v.placement.type === 'attached') return;
    const placement = (v.placement.type === 'plan' ? { ...v.placement, transform: null, by: null } : { ...v.placement, base: null, sheet_ground: null, by: null }) as Placement;
    patchView(id, { placement });
    refreshScene();
  }

  /* ---------- Sheets and levels ---------- */
  async function addSheet(file: File, ghost: Ghost | null) {
    toast('Reading the drawing…');
    try {
      const entries = await api.uploadDrawings(file);
      for (const entry of entries) {
      const d = await api.loadDrawing(entry.id);
      const kind = ghost?.want ?? guessKind(d.title, file.name).kind;
      const level = ghost?.level ?? (isPlaceable(kind) ? defaultLevel() : null);
      drawings.set(d.id, d);
      overlay = { ...overlay, added: [...(overlay.added ?? []), { drawing_id: d.id, views: [placementFor({ kind, kindSure: false }, level, isPlan(kind) && !baseView(P()!))] }] };
      saveChanges();
      await selectSheet(d.id);
      setTab('build');
      toast(`${d.title} added · check what it is`);
      }
    } catch (e) {
      toast((e as Error).message || 'This drawing could not be read');
    }
  }
  /** Takes a sheet out of this building (the drawing itself stays in your uploads). */
  async function removeSheet(id: string) {
    const project = P()!;
    if (project.sheets.length <= 1) { toast('A building needs at least one sheet'); return; }
    if (get().placing) cancelPlacing();
    overlay = { ...overlay, added: (overlay.added ?? []).filter((s) => s.drawing_id !== id) };
    if (manifest!.sheets.some((s) => s.drawing_id === id)) overlay = { ...overlay, removed: [...new Set([...(overlay.removed ?? []), id])] };
    snaps.delete(id);
    saveChanges();
    if (get().activeId === id) {
      set({ activeId: null });
      const next = baseView(P()!)?.drawing_id ?? P()!.sheets[0].drawing_id;
      await selectSheet(next);
    } else refreshScene();
    toast(`${sheetLabel(id)} removed from the building`);
  }
  function addLevel() {
    const lv: Level = nextLevel(P()!.levels);
    overlay = { ...overlay, levels: [...P()!.levels, lv] };
    saveChanges();
    refreshScene(true);
    toast(`${lv.name} added · its floor plan is missing`);
  }
  function toBuilding() {
    const v = viewOf(get().activeId);
    if (!S.d) return;
    const p = createLocalProject({ title: `${S.d.title} building`, drawing: S.d, kind: v?.kind ?? 'floor_plan', kindSure: !!v?.kindSure });
    navigate(`/building?id=${encodeURIComponent(p.id)}`);
  }

  /* ---------- Ask ---------- */
  const currentEntry = () => get().thread.find((e) => e.id === get().currentId) ?? null;
  const entryById = (id: string) => get().thread.find((e) => e.id === id) ?? null;
  function updateEntry(id: string, patch: Partial<Entry>) {
    set({ thread: get().thread.map((e) => (e.id === id ? { ...e, ...patch } : e)) });
  }
  const persistThread = () => saveThread(P()!.id, get().thread);

  async function idleTrace() {
    const events = await api.previewGraph();
    if (!get().currentId && !get().busy) { v3.trace?.replay(events);traceEntryId=null; set({ traceStatus: IDLE_TRACE, traceBusy: false }); }
  }
  async function refreshSuggestions() {
    const id = get().activeId;
    if (!id) return;
    const qs = await api.loadSuggestions(id);
    if (get().activeId !== id) return;
    const extra = ghosts().some((g) => g.kind === 'plumbing') ? [DRAIN_QUESTION] : [];
    set({ suggestions: [...qs.slice(0, 5), ...extra] });
  }
  /** The missing sheet an unanswered question needs: on the base (ground) level unless the question names one. */
  function ghostForEntry(e: Entry) {
    if (!e.answer || e.answer.job_id || e.answer.status === 'answered' || !P()) return null;
    return ghostFor(P()!, e.answer, e.q, baseView(P()!)?.placement.level ?? viewOf(e.sheet)?.placement.level);
  }

  async function refreshLens() { const lensStatus=await api.lensStatus().catch(()=>null);set({lensStatus}); }
  function handleEvent(entry: Entry, ev: TraceEvent) {
    if(ev.seq && entry.events.some(e=>e.seq===ev.seq))return;
    entry.events.push(ev);v3.trace?.apply(ev);
    if(ev.type==='job'){
      entry.job_id=ev.job_id;updateEntry(entry.id,{job_id:ev.job_id,stage:ev.status});persistThread();
    }
    if(ev.type==='connection'){
      updateEntry(entry.id,{stage:ev.message+' · Reload or reconnect to continue watching.'});
      set({traceStatus:'Connection interrupted · job retained',traceBusy:false});
    }
    if(ev.type==='status' && ev.state==='running'){
      updateEntry(entry.id,{stage:ev.summary ?? ev.id});set({traceStatus:ev.summary ?? ev.id});
    }
    if(ev.type==='answer'){
      cancelAsk=null;updateEntry(entry.id,{events:entry.events,modelAnswer:ev.answer});
      if(needsReview(ev.answer))void startReview(entry.id,ev.answer);else showAnswer(entry.id,ev.answer);
      void refreshLens();
    }
  }
  function subscribeEntry(entry: Entry) {
    cancelAsk?.();set({busy:true,traceBusy:true});
    v3.trace?.replay(entry.events);traceEntryId=entry.id;
    cancelAsk=api.reconnectJob(entry.job_id!,ev=>handleEvent(entry,ev),Math.max(0,...entry.events.map(e=>e.seq ?? 0)));
  }
  function ask(question: string) {
    const q=question.trim(), {busy,activeId,selectedIds}=get();
    if(!q || busy || !S.d || !activeId)return;
    const request_id=crypto.randomUUID();const sheets=selectedIds.length?selectedIds:[activeId];
    const anchor=sheets.includes(activeId)?activeId:sheets[0];
    const entry: Entry={id:request_id,request_id,q,sheet:anchor,sheets:[...sheets],status:'pending',stage:'Queued',events:[]};
    const prior_job_ids=get().thread.filter(e=>e.answer && e.job_id).map(e=>e.job_id!).slice(-6);
    set({busy:true,thread:[...get().thread,entry],traceOpen:true,needsGhost:null,traceStatus:'Queued',traceBusy:true});
    persistThread();if(get().tab!=='ask')setTab('ask');session.setAnswer(null);v3.trace?.reset();traceEntryId=entry.id;
    cancelAsk=api.ask({drawing_id:anchor,question:q,request_id,selected_drawing_ids:sheets,project_id:P()!.id,conversation_id:P()!.id,prior_job_ids,
      context:{m_per_px:S.scale,scale_confirmed:S.scaleConfirmed,height_m:S.height,removed_walls:[...session.removed()],added_walls:S.d.walls.filter(w=>w.user).map(({id,a,b})=>({id,a,b}))}},ev=>handleEvent(entry,ev));
  }
  function showAnswer(id: string, answer: Answer, preserveView = false) {
    updateEntry(id, { status: 'done', answer, at: Date.now() });
    persistThread();
    const e = entryById(id)!, cites = answer.citations?.length ?? 0, g = ghostForEntry(e);
    const abstained = answer.status !== 'answered' && answer.status !== 'needs_review';
    set({ busy: false, traceBusy: false, traceStatus: answer.status==='needs_review' ? 'Evidence needs review' : abstained ? (g ? `Needs the ${g.noun} · abstained` : 'No evidence found · abstained')
      : `Answered with ${cites} source${cites === 1 ? '' : 's'}` });
    if (abstained) {
      set({ currentId: null, needsGhost: P()!.single ? null : g?.id ?? null });
      session.setAnswer(null);
      refreshScene();
    } else applyAnswer(id,preserveView);
    const next=get().thread.find(e=>e.id!==id && e.status==='pending' && e.job_id);
    if(next)subscribeEntry(next);
  }
  async function applyAnswer(id: string, preserveView = false) {
    const e = entryById(id);
    if (!e?.answer) return;
    set({ currentId: id });
    if (!preserveView && e.sheet !== get().activeId) await selectSheet(e.sheet);
    const a = e.answer;
    session.setAnswer(a);
    if(preserveView)return;
    if (a.focus) {
      const [x0, y0, x1, y1] = a.focus;
      v3.plan?.fitBox([x0, y0, x1 - x0, y1 - y0], 0.9);
      v3.viewer?.focusBox(a.focus);
    } else { v3.plan?.fit(); v3.viewer?.fit(); }
  }
  /** Click on an earlier answer: show its evidence and its trace again. */
  async function entryTrace(e: Entry): Promise<TraceEvent[]> {
    if (!e.job_id || e.events.some(event=>event.type==='answer')) return e.events;
    const recorded=await api.loadJobTrace(e.job_id).catch(()=>null);
    if(!recorded)return e.events;
    const local=e.events.filter(event=>!event.seq && (event.type==='node' || event.type==='status') && ['you','ans'].includes(event.id));
    if(!local.length)for(const record of e.records ?? [])local.push(
      {type:'node',id:'you',name:`Review ${record.class.replaceAll('_',' ')}`,tool:'You'},
      {type:'node',id:'ans',name:'Answer',tool:'Cited',parents:['you']},
      {type:'status',id:'you',state:'done',summary:'Saved labels restored'},
      {type:'status',id:'ans',state:'done',summary:`${record.user_count} · human-verified`},
    );
    e.events=[...recorded,...local];updateEntry(e.id,{events:e.events});persistThread();
    return e.events;
  }
  async function revisit(id: string) {
    const e = entryById(id);
    if (!e?.answer || !['answered','needs_review'].includes(e.answer.status)) return;
    const events=await entryTrace(e);
    if (get().currentId !== id || e.sheet !== get().activeId) {
      await applyAnswer(id);
      v3.trace?.replay(events);
      traceEntryId=id;
      set({ traceStatus: 'Showing an earlier answer', traceBusy: false });
    }
  }
  async function focusCite(id: string, n: number) {
    const e = entryById(id), c = e?.answer?.citations?.find((x) => x.n === n);
    if (!e || !c) return;
    const sheet = c.sheet ?? e.sheet;
    if (sheet !== get().activeId) await selectSheet(sheet);
    session.setAnswer(e.answer!);
    const b = session.citationBox(c);
    if (!b) return;
    v3.plan?.fitBox([b[0], b[1], b[2] - b[0], b[3] - b[1]], c.room_id && !c.box ? 0.6 : 3);
    v3.viewer?.focusBox(b);
  }

  /* ---------- Low confidence: the user checks the model's boxes ---------- */
  function traceEvents(id: string, evs: TraceEvent[]) {
    const e = entryById(id)!;
    evs.forEach((ev) => v3.trace?.apply(ev));
    updateEntry(id, { events: [...e.events, ...evs] });
  }
  async function startReview(id: string, answer: Answer, group?: CountReview) {
    const chosen=group ?? answer.review;if(!chosen)return;
    if(get().reviewingId && get().reviewingId!==id)skipReview();
    const sheet=chosen.sheet ?? entryById(id)!.sheet;
    if(sheet!==get().activeId)await selectSheet(sheet);
    answer={...answer,review:chosen};
    const e = entryById(id)!;
    const events=await entryTrace(e);
    if(traceEntryId!==id){v3.trace?.replay(events);traceEntryId=id;}
    updateEntry(id, { status: 'review', answer });
    persistThread();
    set({ reviewingId: id, viewMode: get().viewMode === 'one-model' ? 'both' : get().viewMode });
    session.startReview(answer, e.q, savedReview(e.records ?? (e.record?[e.record]:[]),chosen));
    v3.plan?.fit(); v3.viewer?.fit();
    const pct = Math.round((answer.confidence ?? 0) * 100);
    // The frontend owns this step, like Build's "You" steps: it goes between Verifier and Answer.
    traceEvents(id, [
      { type: 'node', id: 'you', name: 'Check boxes', tool: 'You', parents: ['ver'] },
      { type: 'node', id: 'ans', name: 'Answer', tool: 'Cited', parents: ['you'] },
      { type: 'status', id: 'you', state: 'waiting', summary: answer.job_id ? 'Check the boxes' : `${pct}% sure · your turn` },
      { type: 'status', id: 'ans', state: 'pending', summary: 'Waiting for you' },
    ]);
    set({ busy: true, traceStatus: 'Check the boxes', traceBusy: false });
  }
  async function doneReview() {
    const id = get().reviewingId;
    if (!id || get().savingReview) return;
    set({savingReview:true});
    setDrawing(false);
    const draft=session.state.review;
    const res = session.finishReview(true);
    if (!res) { set({savingReview:false}); return; }
    const parts = ([['removed', res.changes.removed], ['added', res.changes.added], ['resized', res.changes.resized]] as const).filter(([, n]) => n).map(([k, n]) => `${n} ${k}`);
    let saved: ReviewRecord;
    try { const response=await api.submitReview(res.record);saved=response.record ?? {...res.record,id:response.id}; }
    catch (err) { session.state.review=draft;set({savingReview:false});session.showReview(false);session.showReview(true);toast(`${(err as Error).message || 'Labels could not be saved.'} Your edits are still here.`);return; }
    const e=entryById(id)!;
    const records=latestReviews([...(e.records ?? []),saved]);
    updateEntry(id, { record: saved, records });
    const sheet=saved.sheet_id ?? saved.drawing.id;
    sheetReviews.set(sheet,[...(sheetReviews.get(sheet) ?? []),saved]);
    session.setVerifiedWindows(reviewedWindows(sheetReviews.get(sheet)!,sheet));
    session.endReview();
    set({reviewingId:null,savingReview:false});
    traceEvents(id, [
      { type: 'status', id: 'you', state: 'done', summary: parts.join(' · ') || 'No changes' },
      { type: 'status', id: 'ans', state: 'done', summary: `${res.record.user_count} · human-verified` },
    ]);
    showAnswer(id, e.job_id ? applyReviews(e.modelAnswer ?? e.answer!,records) : res.answer,true);
  }
  function skipReview() {
    if(get().savingReview)return;
    const id = get().reviewingId;
    if (!id) return;
    set({ reviewingId: null });
    setDrawing(false);
    const draft = session.state.review;
    const answer = session.skipReview();
    if (!answer) return;
    traceEvents(id, [
      { type: 'status', id: 'you', state: 'skipped', summary: 'Skipped' },
      { type: 'status', id: 'ans', state: 'done', summary: draft?.saved ? 'Saved labels kept' : `${answer.value ?? 'Evidence'} · unchecked` },
    ]);
    showAnswer(id, draft?.saved ? {...answer,unchecked:false} : answer,true);
  }

  /* ---------- Tabs, tools, views ---------- */
  function setTab(tab: Tab) {
    if (tab === 'ask' && get().placing) cancelPlacing();
    set({ tab, ...(tab === 'ask' ? { askDot: false } : {}) });
    session.showReview(tab === 'ask');
    if (tab === 'ask') {
      setDrawing(false);
      const cur = currentEntry();
      if (cur?.answer && (cur.sheets ?? [cur.sheet]).includes(get().activeId ?? '')) session.setAnswer(cur.answer);
    } else session.setAnswer(null);
    syncFocus();
  }
  /** The pen draws walls while building, and boxes while the user checks a low-confidence answer. */
  function setDrawing(on: boolean) {
    const boxes = get().tab === 'ask' && !!S.review;
    set({ drawing: on });
    if (!get().placing) v3.plan?.setMode(on ? (boxes ? 'box' : 'draw') : 'select');
    if (on && !boxes && S.focus !== 'walls' && get().tab === 'build') setOpen('check');
  }
  function pick(sel: Selection) {
    if (!sel) { session.select(null); return; }
    if (sel.type === 'sheet') { selectSheet(sel.id); return; }
    if (sel.type === 'box') { session.toggleBox(sel.id); return; }
    if (sel.type === 'wall' && S.focus === 'scale') {
      if (session.candidates().some((c) => c.id === sel.id)) session.setRef(sel.id);
      return;
    }
    if (sel.type === 'wall' && S.focus === 'walls') { session.toggleWall(sel.id); return; }
    session.select(sel);
  }

  /** The views mount after the store exists (and again under React's strict mode); hand them the current state. */
  function attachViews(views: { plan: Plan; viewer: Viewer; basePlan: Plan }) {
    Object.assign(v3, views);
    views.plan.onPoint(addSheetPoint);
    views.basePlan.onPoint(addBasePoint);
    if (S.d) { session.reload(); refreshScene(true); drawPoints(); }
  }
  function detachViews() { delete v3.plan; delete v3.viewer; delete v3.basePlan; }
  function attachTrace(trace: Trace) {
    v3.trace = trace;
    const cur = currentEntry() ?? get().thread.find((e) => e.status !== 'done');
    if (cur) {trace.replay(cur.events);traceEntryId=cur.id;} else idleTrace();
  }
  function detachTrace() { delete v3.trace; }

  const actions = {
    init, selectSheet, selectGhost, setKind, setOpen, confirmScale, finishWalls,
    startPlacing, confirmPlacing, cancelPlacing, redoPlacing: () => { cancelPlacing(); startPlacing(); }, takeOut,
    addSheet, removeSheet, addLevel, toBuilding,
    ask, revisit, focusCite, doneReview, skipReview, refreshLens,
    reviewCount: (id: string, group: CountReview) => { if(get().busy)return;const a=entryById(id)?.answer;if(a)void startReview(id,a,group); },
    toggleScope: (id: string) => {
      const ids=get().selectedIds;if(ids.length===1 && ids.includes(id))return;
      if(ids.includes(id))set({selectedIds:ids.filter(x=>x!==id)});
      else if(ids.length<3)set({selectedIds:[...ids,id]});
    },
    reconnect: (id: string) => { const e=entryById(id);if(e?.job_id)subscribeEntry(e); },
    cancelJob: (id: string) => { const e=entryById(id);if(e?.job_id)void api.cancelJob(e.job_id).catch(()=>toast('Cancellation could not be sent. Reconnect and try again.')); },
    setTab, setDrawing, pick,
    setViewMode: (viewMode: ViewMode) => set({ viewMode }),
    toggleExplode: () => { set({ explode: !get().explode }); refreshScene(true); },
    toggleIsolate: () => { set({ isolate: !get().isolate }); refreshScene(); },
    toggleHideGhosts: () => set({ hideGhosts: !get().hideGhosts }),
    toggleTrace: () => set({ traceOpen: !get().traceOpen }),
    fit: () => { v3.plan?.fit(); v3.viewer?.fit(); },
    setHeight: (h: number) => session.setHeight(h),
    attachViews, detachViews, attachTrace, detachTrace,
    dispose: () => { cancelAsk?.(); loadSeq += 1; },
  };
  const baseId = () => (P() ? baseView(P()!)?.drawing_id ?? null : null);
  const read = { viewOf, levelName, sheetLabel, ghosts, steps, ghostForEntry, drawing: (id: string) => drawings.get(id), isBase, baseId };
  return { store, session, actions, read };
}
export type Workspace = ReturnType<typeof createWorkspace>;

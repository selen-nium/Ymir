// Projects: one building made of sheets. Each sheet's view has a kind and a placement in the building; parts
// the building should have but no sheet covers are ghosts. Pure functions (no DOM) so they can be unit-tested.
// Design: docs/superpowers/specs/2026-11-14-multi-sheet-project-design.md

// group decides Build steps and 3D: plan (flat at a level), upright (stands up), table / other (not in 3D)
export const KINDS = {
  floor_plan: { label: 'Floor plan', group: 'plan', discipline: 'architecture' },
  foundation_plan: { label: 'Foundation plan', group: 'plan', discipline: 'structure' },
  structural_plan: { label: 'Framing or slab plan', group: 'plan', discipline: 'structure' },
  roof_plan: { label: 'Roof plan', group: 'plan', discipline: 'architecture' },
  ceiling_plan: { label: 'Ceiling plan', group: 'plan', discipline: 'architecture' },
  site_plan: { label: 'Site plan', group: 'plan', discipline: 'site' },
  plumbing_plan: { label: 'Plumbing plan', group: 'plan', discipline: 'plumbing' },
  mep_plan: { label: 'Mechanical or electrical plan', group: 'plan', discipline: 'mep' },
  elevation: { label: 'Elevation', group: 'upright', discipline: 'architecture' },
  section: { label: 'Section', group: 'upright', discipline: 'architecture' },
  schedule: { label: 'Schedule', group: 'table' },
  legend: { label: 'Legend or symbols', group: 'table' },
  detail: { label: 'Detail', group: 'other' },
  perspective: { label: 'Perspective or photo', group: 'other' },
  notes: { label: 'Notes', group: 'other' },
  unknown: { label: 'Not sure', group: 'other' },
};
export const kindOf = (k) => KINDS[k] || KINDS.unknown;
export const isPlan = (k) => kindOf(k).group === 'plan';
// Sections stand on a cut line in phase 2; until then they're attached like details.
export const isPlaceable = (k) => isPlan(k) || k === 'elevation';

// First match wins, so specific words come before "plan".
const GUESS = [
  [/schedule|aikataulu|luettelo|liste\b|door\s*list/i, 'schedule'],
  [/legend|symbols|abbreviation|merkin/i, 'legend'],
  [/elevation|ansicht|julkisivu|fa[cç]ade/i, 'elevation'],
  [/section|schnitt|leikkaus/i, 'section'],
  [/detail/i, 'detail'],
  [/foundation|footing|fundament|perustus|\bpile/i, 'foundation_plan'],
  [/framing|\bslab|structur|rakenne|\bS-?\d{2,3}\b/i, 'structural_plan'],
  [/plumb|sanit|drain|sewer|\bLVI\b|\bP-?\d{1,3}\b/i, 'plumbing_plan'],
  [/mechanic|hvac|electric|\b[ME]-?\d{2,3}\b/i, 'mep_plan'],
  [/roof|\bdach|katto/i, 'roof_plan'],
  [/ceiling|\bRCP\b/i, 'ceiling_plan'],
  [/\bsite\b|lageplan|asemapiirros/i, 'site_plan'],
  [/perspective|render|photo|3d\s*view/i, 'perspective'],
  [/plan|grundriss|pohja|floor|geschoss|kerros|\bA-?\d{2,3}\b/i, 'floor_plan'],
];
// From a title or file name. sure=false means Build asks "What is this?" before anything else.
export function guessKind(...texts) {
  const t = texts.filter(Boolean).join(' ');
  for (const [re, kind] of GUESS) if (re.test(t)) return { kind, sure: kind !== 'floor_plan' || /plan|grundriss|pohja/i.test(t) };
  return { kind: 'floor_plan', sure: false };
}

/* ---------- Placement: a similarity [a, b, tx, ty] maps sheet px to project metres ---------- */
// x' = a·x − b·y + tx, y' = b·x + a·y + ty. Project y points the way image y does ("south").
export const applyT = ([a, b, tx, ty], [x, y]) => [a * x - b * y + tx, b * x + a * y + ty];
export const scaleOf = (t) => Math.hypot(t[0], t[1]);
export const angleOf = (t) => Math.atan2(t[1], t[0]);
export function invertT([a, b, tx, ty]) {
  const n = a * a + b * b;
  const ia = a / n, ib = -b / n;
  return [ia, ib, -(ia * tx - ib * ty), -(ib * tx + ia * ty)];
}
// Two point pairs fix scale, rotation and offset exactly: p (sheet px) → q (project metres).
export function solveSimilarity([p1, p2], [q1, q2]) {
  const dpx = p2[0] - p1[0], dpy = p2[1] - p1[1], dqx = q2[0] - q1[0], dqy = q2[1] - q1[1];
  const n = dpx * dpx + dpy * dpy;
  if (n < 1e-9) return null;
  const a = (dqx * dpx + dqy * dpy) / n, b = (dqy * dpx - dqx * dpy) / n;
  return [a, b, q1[0] - (a * p1[0] - b * p1[1]), q1[1] - (b * p1[0] + a * p1[1])];
}

// An elevation stands on its base line (project metres); sheet_ground are the same two points on the sheet (px).
// Returns how sheet (u, v) maps to project (x, y) along the base and height h above the level.
export function elevationFrame({ base, sheet_ground: g }) {
  const [B0, B1] = base, [G0, G1] = g;
  const bl = Math.hypot(B1[0] - B0[0], B1[1] - B0[1]), gl = Math.hypot(G1[0] - G0[0], G1[1] - G0[1]);
  if (bl < 1e-6 || gl < 1e-6) return null;
  const L = bl / gl, dir = [(B1[0] - B0[0]) / bl, (B1[1] - B0[1]) / bl];
  const gdir = [(G1[0] - G0[0]) / gl, (G1[1] - G0[1]) / gl], gup = [gdir[1], -gdir[0]];
  const map = ([u, v]) => {
    const du = u - G0[0], dv = v - G0[1];
    const s = (du * gdir[0] + dv * gdir[1]) * L, h = (du * gup[0] + dv * gup[1]) * L;
    return { x: B0[0] + dir[0] * s, y: B0[1] + dir[1] * s, h };
  };
  // the side the drawing is seen from: in 3D (X = x, Z = y) that is cross(dir, up)
  return { L, dir, normal: [-dir[1], dir[0]], map };
}

/* ---------- Footprint and ghosts ---------- */
export function hull(points) {
  const p = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [], upper = [];
  for (const q of p) { while (lower.length >= 2 && cross(lower.at(-2), lower.at(-1), q) <= 0) lower.pop(); lower.push(q); }
  for (const q of p.reverse()) { while (upper.length >= 2 && cross(upper.at(-2), upper.at(-1), q) <= 0) upper.pop(); upper.push(q); }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}
// Outline of a placed plan in project metres: its walls if it has any, else its plan region.
export function footprint(drawing, t) {
  if (!drawing || !t) return null;
  const pts = drawing.walls?.length ? drawing.walls.flatMap((w) => [w.a, w.b])
    : (() => { const [x0, y0, x1, y1] = drawing.plans?.[0]?.box || [0, 0, ...drawing.image.size]; return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]; })();
  const h = hull(pts.map((p) => applyT(t, p)));
  return h.length >= 3 ? h : null;
}

export function levelRole(lv) {
  if (lv.role) return lv.role;
  if (lv.id === 'F' || /foundation|perustus|fundament/i.test(lv.name)) return 'foundation';
  if (/roof|dach|katto/i.test(lv.name)) return 'roof';
  return 'storey';
}
export const levelsByZ = (project) => [...project.levels].sort((a, b) => a.z_m - b.z_m);
// A manifest's kinds are known (the model or an author set them); guesses carry kindSure: false.
export const views = (project) => project.sheets.flatMap((s) => s.views.map((v, i) => ({ kindSure: true, ...v, drawing_id: s.drawing_id, index: i })));

// The parts each level should have. kind: level (missing architecture plan), structure, plumbing, foundation, roof.
// Structure and plumbing ghosts are drawn in 3D only when picked, so a tall building doesn't fill with dashes.
export function ghostSlots(project) {
  const vs = views(project).filter((v) => isPlan(v.kind) && v.placement?.level);
  const has = (level, test) => vs.some((v) => v.placement.level === level && test(v));
  const out = [];
  const storeys = levelsByZ(project).filter((lv) => levelRole(lv) === 'storey');
  for (const lv of levelsByZ(project)) {
    const role = levelRole(lv);
    if (role === 'foundation' && !has(lv.id, (v) => v.kind === 'foundation_plan')) out.push({ id: 'foundation', kind: 'foundation', level: lv.id, label: 'Foundation plan', noun: 'foundation plan', want: 'foundation_plan' });
    if (role === 'roof' && !has(lv.id, (v) => v.kind === 'roof_plan')) out.push({ id: 'roof', kind: 'roof', level: lv.id, label: 'Roof plan', noun: 'roof plan', want: 'roof_plan' });
    if (role !== 'storey') continue;
    if (!has(lv.id, (v) => kindOf(v.kind).discipline === 'architecture')) out.push({ id: `arch:${lv.id}`, kind: 'level', level: lv.id, label: 'Floor plan', noun: 'floor plan', want: 'floor_plan' });
    if (!has(lv.id, (v) => kindOf(v.kind).discipline === 'structure')) out.push({ id: `struct:${lv.id}`, kind: 'structure', level: lv.id, label: 'Structure', noun: 'framing or slab plan', want: 'structural_plan' });
    if (!has(lv.id, (v) => ['plumbing', 'mep'].includes(kindOf(v.kind).discipline))) out.push({ id: `plumb:${lv.id}`, kind: 'plumbing', level: lv.id, label: 'Plumbing', noun: 'plumbing plan', want: 'plumbing_plan' });
  }
  // no roof level in the manifest: the roof ghost sits on the top storey
  if (!project.levels.some((lv) => levelRole(lv) === 'roof') && storeys.length && !vs.some((v) => v.kind === 'roof_plan')) {
    out.push({ id: 'roof', kind: 'roof', level: storeys.at(-1).id, label: 'Roof plan', noun: 'roof plan', want: 'roof_plan', onTop: true });
  }
  return out;
}

// Until the backend says what an unanswered question needed, guess from its words.
const NEEDS = [
  [/pipe|drain|plumb|sewer|water\s*(supply|line|main)|sanit|putki|viemä|vesijohto/i, 'plumbing'],
  [/footing|foundation|\bpiles?\b|perustu|fundament/i, 'foundation'],
  [/\bslab|beam|column|rebar|framing|joist|lintel|laatta|palkki/i, 'structure'],
  [/\broof|katto|\bdach/i, 'roof'],
  [/(second|2nd|third|3rd|upper|top|next)\s+(floor|storey|level)|\blevel\s+\d|upstairs|kerros/i, 'level'],
];
export function inferNeeds(question) {
  for (const [re, kind] of NEEDS) if (re.test(question || '')) return { kind };
  return null;
}
// The ghost an answer points at: backend `needs` first, else the guess; prefers the asked sheet's level.
export function ghostFor(project, answer, question, level) {
  const need = answer?.needs || inferNeeds(question);
  if (!need) return null;
  const ghosts = ghostSlots(project);
  const want = need.kind && KINDS[need.kind] ? { foundation_plan: 'foundation', structural_plan: 'structure', plumbing_plan: 'plumbing', mep_plan: 'plumbing', roof_plan: 'roof', floor_plan: 'level' }[need.kind] : need.kind;
  const lv = need.level || level;
  const all = ghosts.filter((g) => g.kind === want);
  return all.find((g) => g.level === lv) || all[0] || null;
}

/* ---------- Projects from the API, one-sheet projects, and the viewer's own changes ---------- */
export const BASE_LEVEL = 'L0';
// A library drawing on its own: one level, the drawing auto-placed from its scale.
export function singleSheetProject(d) {
  const g = d.kind ? { kind: d.kind, sure: true } : guessKind(d.title, d.source?.file);
  const sure = g.sure || !!d.kind || (d.source?.dataset && d.source.dataset !== 'Upload');
  return {
    id: `d:${d.id}`, title: d.title, single: true, source: d.source,
    levels: [{ id: 'F', name: 'Foundation', z_m: -0.6, height_m: 0.6 }, { id: BASE_LEVEL, name: 'Level 0', z_m: 0, height_m: d.default_height_m ?? 2.7 }],
    sheets: [{ drawing_id: d.id, views: [placementFor({ kind: g.kind, kindSure: sure }, BASE_LEVEL, true)] }],
  };
}
export function placementFor(view, level, base = false) {
  const kind = view.kind;
  const placement = !isPlaceable(kind) ? { type: 'attached' }
    : kind === 'elevation' ? { type: 'elevation', level, base: null, sheet_ground: null }
    : { type: 'plan', level, transform: null, by: base ? 'auto' : null };
  return { box: null, discipline: kindOf(kind).discipline || null, ...view, placement };
}
// The base sheet anchors the project: the first placed plan. An 'auto' base follows its sheet's scale.
export function baseView(project) {
  return views(project).find((v) => v.placement?.type === 'plan' && (v.placement.transform || v.placement.by === 'auto') && isPlan(v.kind)) || null;
}
export function transformOf(view, scaleFor) {
  const p = view?.placement;
  if (!p || p.type !== 'plan') return null;
  if (p.by === 'auto') return [scaleFor(view.drawing_id), 0, 0, 0];
  return p.transform || null;
}
export const isPlaced = (v) => v.placement?.type === 'plan' ? !!(v.placement.transform || v.placement.by === 'auto')
  : v.placement?.type === 'elevation' ? !!(v.placement.base && v.placement.sheet_ground) : false;

// The viewer's changes live on top of the manifest: added sheets and per-view patches.
export function mergeOverlay(project, overlay) {
  if (!overlay) return project;
  const sheets = [...project.sheets, ...(overlay.added || []).filter((s) => !project.sheets.some((x) => x.drawing_id === s.drawing_id))]
    .map((s) => ({ ...s, views: s.views.map((v, i) => ({ ...v, ...(overlay.patch?.[`${s.drawing_id}#${i}`] || {}) })) }));
  return { ...project, sheets, levels: overlay.levels || project.levels };
}
const KEY = (id) => `ymir.project.${id}`;
export function loadOverlay(id, storage = globalThis.localStorage) {
  try { return JSON.parse(storage?.getItem(KEY(id)) || 'null'); } catch { return null; }
}
export function saveOverlay(id, overlay, storage = globalThis.localStorage) {
  try { storage?.setItem(KEY(id), JSON.stringify(overlay)); } catch { /* private mode: changes last this visit */ }
}

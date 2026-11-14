// Projects: placement math, footprint, ghosts and the viewer's overlay.
// run (repo root): node --test frontend/tests/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  applyT, invertT, solveSimilarity, scaleOf, elevationFrame, hull, footprint, ghostSlots, inferNeeds, ghostFor,
  singleSheetProject, mergeOverlay, baseView, isPlaced, transformOf, guessKind, loadOverlay, saveOverlay, placementFor,
} from '../shared/project.js';

const ac20 = JSON.parse(readFileSync(new URL('../../data/samples/projects.json', import.meta.url)))[0];
const close = (a, b, eps = 1e-6) => a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < eps, `${a} ≈ ${b}`));

test('two point pairs give back a rotated, scaled, shifted sheet exactly', () => {
  const truth = [0.03 * Math.cos(0.4), 0.03 * Math.sin(0.4), 12.5, -3];
  const p = [[100, 80], [640, 410]];
  const t = solveSimilarity(p, p.map((x) => applyT(truth, x)));
  close(t, truth);
  close(applyT(invertT(t), applyT(t, [5, 7])), [5, 7]);
  assert.equal(solveSimilarity([[1, 1], [1, 1]], [[0, 0], [1, 1]]), null);
});

test('the south elevation stands on the south facade, seen from the south', () => {
  const el = ac20.sheets.find((s) => s.drawing_id === 'ac20-south').views[0].placement;
  const f = elevationFrame(el);
  assert.ok(Math.abs(f.L - 1 / 24) < 1e-4);
  const [g0] = el.sheet_ground;
  const p = f.map(g0);
  close([p.x, p.y, p.h], [...el.base[0], 0]);
  assert.ok(Math.abs(f.map([g0[0], g0[1] - 24]).h - 1) < 1e-3, 'one metre up the sheet is one metre up');
  close(f.normal, [0, 1]);
});

test('footprint is the hull of the walls in project metres', () => {
  assert.deepEqual(hull([[0, 0], [2, 0], [1, 1], [2, 2], [0, 2]]), [[0, 0], [2, 0], [2, 2], [0, 2]]);
  const d = { walls: [{ a: [0, 0], b: [100, 0] }, { a: [100, 0], b: [100, 50] }, { a: [0, 50], b: [100, 50] }], image: { size: [100, 50] } };
  close(footprint(d, [0.1, 0, 2, 3]).flat(), [2, 3, 12, 3, 12, 8, 2, 8]);
  const empty = { walls: [], plans: [{ box: [0, 0, 10, 10] }], image: { size: [10, 10] } };
  assert.equal(footprint(empty, [1, 0, 0, 0]).length, 4);
});

test('ghosts: what the AC20 sheets leave uncovered', () => {
  assert.deepEqual(ghostSlots(ac20).map((g) => g.id), ['foundation', 'struct:KG', 'plumb:KG', 'struct:EG', 'plumb:EG',
    'struct:OG1', 'plumb:OG1', 'arch:OG2', 'struct:OG2', 'plumb:OG2']);
});

test('an unanswered question points at the ghost it needs', () => {
  assert.deepEqual(inferNeeds('Where does the main drain leave the building?'), { kind: 'plumbing' });
  assert.equal(inferNeeds('How many windows are there?'), null);
  assert.equal(ghostFor(ac20, { status: 'abstained' }, 'Where does the main drain leave the building?', 'EG').id, 'plumb:EG');
  assert.equal(ghostFor(ac20, { needs: { kind: 'foundation_plan' } }, '', 'EG').id, 'foundation');
  assert.equal(ghostFor(ac20, { needs: { kind: 'level', level: 'OG2' } }, '', 'EG').id, 'arch:OG2');
});

test('a library drawing becomes a one-sheet project with an auto-placed base', () => {
  const p = singleSheetProject({ id: 'swiss', title: 'Grundriss 2. OG', source: { dataset: 'Swiss Dwellings' }, default_height_m: 2.7 });
  const v = baseView(p);
  assert.equal(v.drawing_id, 'swiss');
  assert.equal(v.kind, 'floor_plan');
  assert.equal(v.kindSure, true);
  assert.deepEqual(transformOf(v, () => 0.04), [0.04, 0, 0, 0]);
  assert.deepEqual(ghostSlots(p).map((g) => g.id), ['foundation', 'struct:L0', 'plumb:L0', 'roof']);
  const up = singleSheetProject({ id: 'u-1', title: 'IMG_2031', source: { dataset: 'Upload' } });
  assert.equal(up.sheets[0].views[0].kindSure, false);
});

test('the viewer places the level 1 plan; the overlay survives a reload', () => {
  const og1 = ac20.sheets.find((s) => s.drawing_id === 'ac20-og1').views[0];
  assert.equal(isPlaced(og1), false);
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  saveOverlay('ac20', { patch: { 'ac20-og1#0': { placement: { ...og1.placement, transform: [1 / 24, 0, 1, 2], by: 'user' } } } }, storage);
  const p = mergeOverlay(ac20, loadOverlay('ac20', storage));
  const v = p.sheets.find((s) => s.drawing_id === 'ac20-og1').views[0];
  assert.equal(isPlaced(v), true);
  assert.ok(Math.abs(scaleOf(v.placement.transform) - 1 / 24) < 1e-9);
  assert.equal(baseView(p).drawing_id, 'ac20-eg');
  assert.ok(!ghostSlots(p).some((g) => g.id === 'arch:OG1'));
  assert.equal(loadOverlay('x', { getItem: () => { throw new Error('blocked'); } }), null);
});

test('kinds are guessed from sheet titles and file names', () => {
  assert.equal(guessKind('Sheet - S-300 - FOUNDATION DETAILS').kind, 'detail');
  assert.equal(guessKind('Sheet A600 - 3196 Aalseth Lane - Schedules').kind, 'schedule');
  assert.equal(guessKind('The Downtowner - Sheet - S100 - STRUCTURE ONLY').kind, 'structural_plan');
  assert.equal(guessKind('A202 - BULDING ELEVATION - WEST').kind, 'elevation');
  assert.equal(guessKind('foundation plan.png').kind, 'foundation_plan');
  assert.deepEqual(guessKind('Grundriss 2. OG'), { kind: 'floor_plan', sure: true });
  assert.deepEqual(guessKind('Ändringsritning, 1921'), { kind: 'floor_plan', sure: false });
  assert.equal(placementFor({ kind: 'schedule' }, 'L0').placement.type, 'attached');
  assert.equal(placementFor({ kind: 'elevation' }, 'L0').placement.type, 'elevation');
});

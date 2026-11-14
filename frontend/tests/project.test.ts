// Projects: placement math, footprint, ghosts, kinds and the viewer's overlay.
import { readFileSync } from 'node:fs';
import { describe, expect, test } from 'vitest';
import {
  applyT, baseView, elevationFrame, footprint, ghostFor, ghostSlots, guessKind, hull, inferNeeds, invertT, isPlaced, mergeOverlay,
  placementFor, scaleOf, singleSheetProject, solveSimilarity, transformOf, type Project, type Similarity,
} from '@/lib/project';
import type { Pt } from '@/lib/types';

const ac20 = (JSON.parse(readFileSync(new URL('../../data/samples/projects.json', import.meta.url), 'utf8')) as Project[])[0];
const near = (a: number[], b: number[], digits = 6) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], digits));
const wall = (a: Pt, b: Pt) => ({ id: `${a}-${b}`, a, b });

describe('placement', () => {
  test('two point pairs give back a rotated, scaled, shifted sheet exactly', () => {
    const truth: Similarity = [0.03 * Math.cos(0.4), 0.03 * Math.sin(0.4), 12.5, -3];
    const p: [Pt, Pt] = [[100, 80], [640, 410]];
    const t = solveSimilarity(p, [applyT(truth, p[0]), applyT(truth, p[1])])!;
    near(t, truth);
    near(applyT(invertT(t), applyT(t, [5, 7])), [5, 7]);
    expect(solveSimilarity([[1, 1], [1, 1]], [[0, 0], [1, 1]])).toBeNull();
  });

  test('the south elevation stands on the south facade, seen from the south', () => {
    const el = ac20.sheets.find((s) => s.drawing_id === 'ac20-south')!.views[0].placement;
    if (el.type !== 'elevation') throw new Error('not an elevation');
    const f = elevationFrame(el)!;
    expect(f.L).toBeCloseTo(1 / 24, 4);
    const g0 = el.sheet_ground![0];
    const p = f.map(g0);
    near([p.x, p.y, p.h], [...el.base![0], 0]);
    expect(f.map([g0[0], g0[1] - 24]).h).toBeCloseTo(1, 3);
    near(f.normal, [0, 1]);
  });

  test('footprint is the hull of the walls in project metres', () => {
    expect(hull([[0, 0], [2, 0], [1, 1], [2, 2], [0, 2]])).toEqual([[0, 0], [2, 0], [2, 2], [0, 2]]);
    const d = { walls: [wall([0, 0], [100, 0]), wall([100, 0], [100, 50]), wall([0, 50], [100, 50])], image: { url: '', size: [100, 50] as Pt } };
    near(footprint(d, [0.1, 0, 2, 3])!.flat(), [2, 3, 12, 3, 12, 8, 2, 8]);
    expect(footprint({ walls: [], plans: [{ id: 'p', box: [0, 0, 10, 10] }], image: { url: '', size: [10, 10] } }, [1, 0, 0, 0])).toHaveLength(4);
  });
});

describe('ghosts', () => {
  test('what the AC20 sheets leave uncovered', () => {
    expect(ghostSlots(ac20).map((g) => g.id)).toEqual(['foundation', 'struct:KG', 'plumb:KG', 'struct:EG', 'plumb:EG',
      'struct:OG1', 'plumb:OG1', 'arch:OG2', 'struct:OG2', 'plumb:OG2']);
  });

  test('an unanswered question points at the ghost it needs', () => {
    expect(inferNeeds('Where does the main drain leave the building?')).toEqual({ kind: 'plumbing' });
    expect(inferNeeds('How many windows are there?')).toBeNull();
    expect(ghostFor(ac20, { needs: undefined }, 'Where does the main drain leave the building?', 'EG')?.id).toBe('plumb:EG');
    expect(ghostFor(ac20, { needs: { kind: 'foundation_plan' } }, '', 'EG')?.id).toBe('foundation');
    expect(ghostFor(ac20, { needs: { kind: 'level', level: 'OG2' } }, '', 'EG')?.id).toBe('arch:OG2');
  });
});

describe('projects', () => {
  test('a library drawing becomes a one-sheet project with an auto-placed base', () => {
    const p = singleSheetProject({ id: 'swiss', title: 'Grundriss 2. OG', source: { dataset: 'Swiss Dwellings' }, default_height_m: 2.7 });
    const v = baseView(p)!;
    expect(v.drawing_id).toBe('swiss');
    expect(v.kind).toBe('floor_plan');
    expect(v.kindSure).toBe(true);
    expect(transformOf(v, () => 0.04)).toEqual([0.04, 0, 0, 0]);
    expect(ghostSlots(p).map((g) => g.id)).toEqual(['foundation', 'struct:L0', 'plumb:L0', 'roof']);
    expect(singleSheetProject({ id: 'u-1', title: 'IMG_2031', source: { dataset: 'Upload' } }).sheets[0].views[0].kindSure).toBe(false);
  });

  test('the viewer places the level 1 plan, adds and removes sheets', () => {
    const og1 = ac20.sheets.find((s) => s.drawing_id === 'ac20-og1')!.views[0];
    expect(isPlaced(og1)).toBe(false);
    const placed = mergeOverlay(ac20, { patch: { 'ac20-og1#0': { placement: { type: 'plan', level: 'OG1', transform: [1 / 24, 0, 1, 2], by: 'user' } } } });
    const v = placed.sheets.find((s) => s.drawing_id === 'ac20-og1')!.views[0];
    expect(isPlaced(v)).toBe(true);
    if (v.placement.type === 'plan') expect(scaleOf(v.placement.transform!)).toBeCloseTo(1 / 24, 9);
    expect(baseView(placed)!.drawing_id).toBe('ac20-eg');
    expect(ghostSlots(placed).some((g) => g.id === 'arch:OG1')).toBe(false);

    const changed = mergeOverlay(ac20, { removed: ['ac20-dg'], added: [{ drawing_id: 'u-9', views: [placementFor({ kind: 'plumbing_plan' }, 'EG')] }] });
    expect(changed.sheets.map((s) => s.drawing_id)).not.toContain('ac20-dg');
    expect(ghostSlots(changed).map((g) => g.id)).toContain('roof');
    expect(ghostSlots(changed).map((g) => g.id)).not.toContain('plumb:EG');
  });

  test('kinds are guessed from sheet titles and file names', () => {
    expect(guessKind('Sheet - S-300 - FOUNDATION DETAILS').kind).toBe('detail');
    expect(guessKind('Sheet A600 - 3196 Aalseth Lane - Schedules').kind).toBe('schedule');
    expect(guessKind('The Downtowner - Sheet - S100 - STRUCTURE ONLY').kind).toBe('structural_plan');
    expect(guessKind('A202 - BULDING ELEVATION - WEST').kind).toBe('elevation');
    expect(guessKind('M-101A piping plan.png').kind).toBe('plumbing_plan');
    expect(guessKind('foundation plan.png').kind).toBe('foundation_plan');
    expect(guessKind('Grundriss 2. OG')).toEqual({ kind: 'floor_plan', sure: true });
    expect(guessKind('Ändringsritning, 1921')).toEqual({ kind: 'floor_plan', sure: false });
    expect(placementFor({ kind: 'schedule' }, 'L0').placement.type).toBe('attached');
    expect(placementFor({ kind: 'elevation' }, 'L0').placement.type).toBe('elevation');
  });
});

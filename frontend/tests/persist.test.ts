// What the browser remembers: project changes, threads, the viewer's own buildings, hidden samples, recent items.
import { describe, expect, test } from 'vitest';
import {
  MAX_THREAD, createLocalProject, getLocalProject, hiddenItems, loadOverlay, loadThread, localProjects, nextLevel, recent,
  removeLocalProject, saveOverlay, saveThread, setHidden, threadSummary, timeAgo, touchRecent, type KeyValueStorage,
} from '@/lib/persist';
import { baseView, ghostSlots, isPlaced } from '@/lib/project';
import type { Answer } from '@/lib/types';

const memory = (): KeyValueStorage => {
  const m = new Map<string, string>();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => { m.set(k, v); }, removeItem: (k) => { m.delete(k); } };
};
const blocked: KeyValueStorage = {
  getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); }, removeItem: () => { throw new Error('blocked'); },
};
const answer = (i: number): Answer => ({ status: i % 2 ? 'answered' : 'abstained', text: '' });

describe('persist', () => {
  test('a thread keeps the last answers with their sheet and trace', () => {
    const s = memory();
    const entries = Array.from({ length: MAX_THREAD + 5 }, (_, i) => ({ q: `q${i}`, sheet: 'ac20-eg', answer: answer(i), events: [], card: {} }));
    saveThread('ac20', entries, s);
    const t = loadThread('ac20', s);
    expect(t).toHaveLength(MAX_THREAD);
    expect(t[0].q).toBe('q5');
    expect('card' in t[0]).toBe(false);
    expect(threadSummary('ac20', s)).toMatchObject({ count: MAX_THREAD, last: `q${MAX_THREAD + 4}` });
    expect(threadSummary('nothing', s)).toBeNull();
    expect(loadThread('ac20', blocked)).toEqual([]);
    expect(saveThread('ac20', entries, blocked)).toBe(false);
  });

  test('a building started from a drawing has it as the auto-placed base', () => {
    const s = memory();
    const p = createLocalProject({ title: 'My house', drawing: { id: 'u-1', title: 'IMG', image: { url: 'x.png', size: [10, 10] } } }, s);
    expect(p.id).toMatch(/^local-/);
    expect(getLocalProject(p.id, s)!.title).toBe('My house');
    const v = baseView(p)!;
    expect(v.drawing_id).toBe('u-1');
    expect(isPlaced(v)).toBe(true);
    expect(v.kindSure).toBe(false);
    expect(ghostSlots(p).map((g) => g.id)).toEqual(['foundation', 'struct:L0', 'plumb:L0', 'roof']);
    expect(nextLevel(p.levels)).toEqual({ id: 'L1', name: 'Level 1', z_m: 3, height_m: 3 });
    saveOverlay(p.id, { removed: ['x'] }, s);
    expect(loadOverlay(p.id, s)).toEqual({ removed: ['x'] });
    removeLocalProject(p.id, s);
    expect(localProjects(s)).toEqual([]);
    expect(loadOverlay(p.id, s)).toBeNull();
  });

  test('hidden samples and recent items', () => {
    const s = memory();
    setHidden('p:ac20', true, s);
    setHidden('d:cubi', true, s);
    setHidden('p:ac20', false, s);
    expect(hiddenItems(s)).toEqual(['d:cubi']);
    touchRecent({ id: 'a', type: 'project' }, s);
    touchRecent({ id: 'b', type: 'drawing' }, s);
    touchRecent({ id: 'a', type: 'project' }, s);
    expect(recent(s).map((r) => r.id)).toEqual(['a', 'b']);
    expect(timeAgo(Date.now() - 3 * 3600e3)).toBe('3 h ago');
    expect(timeAgo(Date.now() - 26 * 3600e3)).toBe('yesterday');
  });
});

// What the browser remembers: threads, the viewer's own buildings, recent items.
// run (repo root): node --test frontend/tests/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadThread, saveThread, threadSummary, MAX_THREAD, createLocalProject, getLocalProject, localProjects,
  updateLocalProject, removeLocalProject, nextLevel, touchRecent, recent, timeAgo } from '../shared/store.js';
import { baseView, ghostSlots, isPlaced } from '../shared/project.js';

const memory = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v), removeItem: (k) => m.delete(k), m }; };
const blocked = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); }, removeItem: () => { throw new Error('blocked'); } };

test('a thread keeps the last answers with their sheet and trace', () => {
  const s = memory();
  const entries = Array.from({ length: MAX_THREAD + 5 }, (_, i) => ({ q: `q${i}`, sheet: 'ac20-eg', answer: { status: i % 2 ? 'answered' : 'abstained' }, events: [{ type: 'node' }], card: {} }));
  saveThread('ac20', entries, s);
  const t = loadThread('ac20', s);
  assert.equal(t.length, MAX_THREAD);
  assert.equal(t[0].q, 'q5');
  assert.equal('card' in t[0], false, 'DOM bits are not stored');
  const sum = threadSummary('ac20', s);
  assert.equal(sum.count, MAX_THREAD);
  assert.equal(sum.last, `q${MAX_THREAD + 4}`);
  assert.equal(threadSummary('nothing', s), null);
  assert.deepEqual(loadThread('ac20', blocked), []);
  assert.equal(saveThread('ac20', entries, blocked), false);
});

test('a building started from a drawing has it as the auto-placed base', () => {
  const s = memory();
  const p = createLocalProject({ title: 'My house', drawing: { id: 'u-1', title: 'IMG', image: { url: 'x.png' } } }, s);
  assert.match(p.id, /^local-/);
  assert.equal(getLocalProject(p.id, s).title, 'My house');
  const v = baseView(p);
  assert.equal(v.drawing_id, 'u-1');
  assert.equal(isPlaced(v), true);
  assert.equal(v.kindSure, false, 'Build asks what it is');
  assert.deepEqual(ghostSlots(p).map((g) => g.id), ['foundation', 'struct:L0', 'plumb:L0', 'roof']);
  const up = updateLocalProject(p.id, { levels: [...p.levels, nextLevel(p.levels)] }, s);
  assert.deepEqual(up.levels.at(-1), { id: 'L1', name: 'Level 1', z_m: 3, height_m: 3 });
  assert.ok(ghostSlots(up).some((g) => g.id === 'arch:L1'));
  removeLocalProject(p.id, s);
  assert.deepEqual(localProjects(s), []);
});

test('recent items are newest first and unique', () => {
  const s = memory();
  touchRecent({ id: 'a', type: 'project' }, s);
  touchRecent({ id: 'b', type: 'drawing' }, s);
  touchRecent({ id: 'a', type: 'project' }, s);
  assert.deepEqual(recent(s).map((r) => r.id), ['a', 'b']);
  assert.equal(timeAgo(Date.now() - 3 * 3600e3), '3 h ago');
  assert.equal(timeAgo(Date.now() - 26 * 3600e3), 'yesterday');
});

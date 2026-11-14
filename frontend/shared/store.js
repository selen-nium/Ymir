// What this browser remembers: conversations per project or drawing, buildings the viewer started, and what
// was opened recently (for the home page). localStorage only, so every read and write survives it being blocked.
import { placementFor, BASE_LEVEL } from './project.js';

const read = (storage, key, fallback) => {
  try { const v = storage?.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
};
const write = (storage, key, value) => {
  try { storage?.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
};
const LS = () => globalThis.localStorage;

/* ---------- Conversations: one thread per project ('ac20', 'local-…') or drawing ('d:cubi') ---------- */
export const MAX_THREAD = 30;
const threadKey = (pid) => `ymir.thread.${pid}`;
export function loadThread(pid, storage = LS()) {
  const t = read(storage, threadKey(pid), []);
  return Array.isArray(t) ? t : [];
}
// Keeps what re-showing an answer needs: the question, the sheet it was asked on, the answer and its trace.
export function saveThread(pid, entries, storage = LS()) {
  const kept = entries.slice(-MAX_THREAD).map(({ q, sheet, answer, events, at }) => ({ q, sheet, answer, events: events || [], at: at || Date.now() }));
  return write(storage, threadKey(pid), kept);
}
export function threadSummary(pid, storage = LS()) {
  const t = loadThread(pid, storage);
  if (!t.length) return null;
  const last = t.at(-1);
  return { count: t.length, last: last.q, at: last.at, answered: t.filter((e) => e.answer?.status === 'answered').length };
}

/* ---------- Buildings the viewer started from their own drawings ---------- */
const LOCAL = 'ymir.local.projects';
export const DEFAULT_LEVELS = [
  { id: 'F', name: 'Foundation', z_m: -0.6, height_m: 0.6 },
  { id: BASE_LEVEL, name: 'Ground floor', z_m: 0, height_m: 3.0 },
];
export function localProjects(storage = LS()) {
  const list = read(storage, LOCAL, []);
  return Array.isArray(list) ? list : [];
}
export const getLocalProject = (id, storage = LS()) => localProjects(storage).find((p) => p.id === id) || null;
// A new building with its first sheet as the base (placed from its own scale on the ground floor).
export function createLocalProject({ title, drawing, kind = 'floor_plan', kindSure = false }, storage = LS()) {
  const id = `local-${Math.random().toString(36).slice(2, 10)}`;
  const project = {
    id, title: title || `${drawing?.title || 'New'} building`, local: true, created: Date.now(),
    thumbnail: drawing?.image?.url || null, source: { dataset: 'Your drawings' },
    levels: DEFAULT_LEVELS.map((l) => ({ ...l })),
    sheets: drawing ? [{ drawing_id: drawing.id, views: [placementFor({ kind, kindSure }, BASE_LEVEL, true)] }] : [],
  };
  write(storage, LOCAL, [project, ...localProjects(storage)]);
  return project;
}
export function updateLocalProject(id, patch, storage = LS()) {
  const list = localProjects(storage).map((p) => (p.id === id ? { ...p, ...patch } : p));
  write(storage, LOCAL, list);
  return list.find((p) => p.id === id) || null;
}
export function removeLocalProject(id, storage = LS()) {
  write(storage, LOCAL, localProjects(storage).filter((p) => p.id !== id));
  try { storage?.removeItem(threadKey(id)); storage?.removeItem(`ymir.project.${id}`); } catch { /* blocked */ }
}
// The next storey above the top one, for "Add level".
export function nextLevel(levels) {
  const storeys = levels.filter((l) => l.id !== 'F');
  const top = storeys.reduce((a, b) => (b.z_m > a.z_m ? b : a), storeys[0] || { z_m: -3, height_m: 3 });
  const n = storeys.length;
  return { id: `L${n}`, name: `Level ${n}`, z_m: +(top.z_m + (top.height_m ?? 3)).toFixed(2), height_m: top.height_m ?? 3 };
}

/* ---------- Recently opened, newest first ---------- */
const RECENT = 'ymir.recent';
export function touchRecent(item, storage = LS()) {
  const list = read(storage, RECENT, []).filter((r) => r.id !== item.id);
  write(storage, RECENT, [{ ...item, at: Date.now() }, ...list].slice(0, 40));
}
export const recent = (storage = LS()) => { const r = read(storage, RECENT, []); return Array.isArray(r) ? r : []; };

export function timeAgo(at, now = Date.now()) {
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? 'yesterday' : `${d} days ago`;
}

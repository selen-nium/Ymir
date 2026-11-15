// What this browser remembers: conversations per project or drawing, buildings the viewer started, their changes
// to each project, and what was opened recently. localStorage only, so every read and write survives it being
// blocked (private windows, previews) and the app still works without it.
import { BASE_LEVEL, placementFor, type Level, type Overlay, type Project } from './project';
import type { Answer, Drawing, TraceEvent, RoutingMode } from './types';
import type {ReviewRecord} from './session';

export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const browserStorage = (): KeyValueStorage | undefined => (typeof window === 'undefined' ? undefined : window.localStorage);

// Pages re-read what's stored when it changes here or in another tab (useSyncExternalStore).
const listeners = new Set<() => void>();
let version = 0;
const changed = () => { version += 1; listeners.forEach((f) => f()); };
export function subscribePersist(f: () => void) {
  listeners.add(f);
  const onStorage = (e: StorageEvent) => { if (e.key?.startsWith('ymir.')) changed(); };
  window.addEventListener('storage', onStorage);
  return () => { listeners.delete(f); window.removeEventListener('storage', onStorage); };
}
export const persistVersion = () => version;
function read<T>(storage: KeyValueStorage | undefined, key: string, fallback: T): T {
  try {
    const v = storage?.getItem(key);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}
function write(storage: KeyValueStorage | undefined, key: string, value: unknown): boolean {
  try {
    if (!storage) return false;
    storage.setItem(key, JSON.stringify(value));
    changed();
    return true;
  } catch {
    return false;
  }
}
function remove(storage: KeyValueStorage | undefined, key: string) {
  try { storage?.removeItem(key); changed(); } catch { /* blocked */ }
}

/* ---------- The viewer's changes to a project (added sheets, kinds, placements, levels) ---------- */
const overlayKey = (pid: string) => `ymir.project.${pid}`;
export const loadOverlay = (pid: string, storage = browserStorage()) => read<Overlay | null>(storage, overlayKey(pid), null);
export const saveOverlay = (pid: string, overlay: Overlay, storage = browserStorage()) => write(storage, overlayKey(pid), overlay);

/* ---------- Conversations: one thread per project ('ac20', 'local-…') or drawing ('d:cubi') ---------- */
export const MAX_THREAD = 30;
export interface StoredEntry {
  routing_mode?: RoutingMode;
  q: string;
  sheet: string;
  answer?: Answer;
  modelAnswer?: Answer;
  records?: ReviewRecord[];
  record?: ReviewRecord;
  id?: string;
  job_id?: string;
  request_id?: string;
  sheets?: string[];
  status?: 'pending' | 'review' | 'done';
  events: TraceEvent[];
  at: number;
}
const conversationKey = (pid: string) => `ymir.conversation.${pid}`;
export function loadConversationId(pid: string, storage = browserStorage()): string {
  const id=read<unknown>(storage,conversationKey(pid),null);
  return typeof id==='string' && id.length>0 && id.length<=120 ? id : pid;
}
export const saveConversationId = (pid: string, id: string, storage = browserStorage()) => write(storage,conversationKey(pid),id);
const threadKey = (pid: string, storage: KeyValueStorage | undefined) => `ymir.thread.${loadConversationId(pid,storage)}`;
export function loadThread(pid: string, storage = browserStorage()): StoredEntry[] {
  const t = read<unknown>(storage, threadKey(pid,storage), []);
  return Array.isArray(t) ? (t as StoredEntry[]) : [];
}
/** Keeps what re-showing an answer needs: the question, the sheet it was asked on, the answer and its trace. */
export function saveThread(pid: string, entries: Partial<StoredEntry>[], storage = browserStorage()): boolean {
  const kept = entries.filter((e) => e.answer || e.job_id || e.request_id).slice(-MAX_THREAD)
    .map(({ q, sheet, sheets, routing_mode, answer, modelAnswer, records, record, events, at, id, job_id, request_id, status }) => ({ q, sheet, sheets, routing_mode, answer, modelAnswer, records, record, events: events ?? [], at: at ?? Date.now(), id, job_id, request_id, status }));
  return write(storage, threadKey(pid,storage), kept);
}
const routingKey = (pid: string, storage: KeyValueStorage | undefined) => `ymir.routing.${loadConversationId(pid,storage)}`;
export function loadRoutingMode(pid: string, storage = browserStorage()): RoutingMode | null {
  const value = read<unknown>(storage, routingKey(pid,storage), null);
  return value === 'smart' || value === 'frontier' ? value : null;
}
export const saveRoutingMode = (pid: string, mode: RoutingMode, storage = browserStorage()) => write(storage, routingKey(pid,storage), mode);
export function threadSummary(pid: string, storage = browserStorage()) {
  const t = loadThread(pid, storage);
  if (!t.length) return null;
  const last = t.at(-1)!;
  return { count: t.length, last: last.q, at: last.at, answered: t.filter((e) => e.answer?.status === 'answered').length };
}

/* ---------- Buildings the viewer started from their own drawings ---------- */
const LOCAL = 'ymir.local.projects';
export const DEFAULT_LEVELS: Level[] = [
  { id: 'F', name: 'Foundation', z_m: -0.6, height_m: 0.6 },
  { id: BASE_LEVEL, name: 'Ground floor', z_m: 0, height_m: 3.0 },
];
export function localProjects(storage = browserStorage()): Project[] {
  const list = read<unknown>(storage, LOCAL, []);
  return Array.isArray(list) ? (list as Project[]) : [];
}
export const getLocalProject = (id: string, storage = browserStorage()) => localProjects(storage).find((p) => p.id === id) ?? null;
/** A new building with its first sheet as the base (placed from its own scale on the ground floor). */
export function createLocalProject(
  { title, drawing, kind = 'floor_plan', kindSure = false }: { title?: string; drawing?: Pick<Drawing, 'id' | 'title' | 'image'>; kind?: string; kindSure?: boolean },
  storage = browserStorage(),
): Project {
  const id = `local-${Math.random().toString(36).slice(2, 10)}`;
  const project: Project = {
    id, title: title || `${drawing?.title ?? 'New'} building`, local: true, created: Date.now(),
    thumbnail: drawing?.image?.url ?? null, source: { dataset: 'Your drawings' },
    levels: DEFAULT_LEVELS.map((l) => ({ ...l })),
    sheets: drawing ? [{ drawing_id: drawing.id, views: [placementFor({ kind, kindSure }, BASE_LEVEL, true)] }] : [],
  };
  write(storage, LOCAL, [project, ...localProjects(storage)]);
  return project;
}
export function removeLocalProject(id: string, storage = browserStorage()) {
  write(storage, LOCAL, localProjects(storage).filter((p) => p.id !== id));
  remove(storage, routingKey(id,storage));
  remove(storage, threadKey(id,storage));
  remove(storage, conversationKey(id));
  remove(storage, overlayKey(id));
}
/** The next storey above the top one, for "Add level". */
export function nextLevel(levels: Level[]): Level {
  const storeys = levels.filter((l) => l.id !== 'F');
  const top = storeys.reduce((a, b) => (b.z_m > a.z_m ? b : a), storeys[0] ?? { id: '', name: '', z_m: -3, height_m: 3 });
  const n = storeys.length;
  return { id: `L${n}`, name: `Level ${n}`, z_m: +(top.z_m + (top.height_m ?? 3)).toFixed(2), height_m: top.height_m ?? 3 };
}

/* ---------- Samples the viewer hid from their home page ---------- */
const HIDDEN = 'ymir.hidden';
export function hiddenItems(storage = browserStorage()): string[] {
  const h = read<unknown>(storage, HIDDEN, []);
  return Array.isArray(h) ? (h as string[]) : [];
}
export function setHidden(key: string, hidden: boolean, storage = browserStorage()) {
  const rest = hiddenItems(storage).filter((k) => k !== key);
  write(storage, HIDDEN, hidden ? [...rest, key] : rest);
}
/** Forget what this browser kept about a drawing that was deleted (its conversation, its recent entry). */
export function forgetDrawing(id: string, storage = browserStorage()) {
  remove(storage, routingKey(`d:${id}`,storage));
  remove(storage, threadKey(`d:${id}`,storage));
  remove(storage, conversationKey(`d:${id}`));
  write(storage, RECENT, recent(storage).filter((r) => r.id !== id));
}

/* ---------- Recently opened, newest first ---------- */
const RECENT = 'ymir.recent';
export interface RecentItem { id: string; type: 'project' | 'drawing'; title?: string; thumbnail?: string; at?: number }
export function touchRecent(item: RecentItem, storage = browserStorage()) {
  const list = recent(storage).filter((r) => r.id !== item.id);
  write(storage, RECENT, [{ ...item, at: Date.now() }, ...list].slice(0, 40));
}
export function recent(storage = browserStorage()): RecentItem[] {
  const r = read<unknown>(storage, RECENT, []);
  return Array.isArray(r) ? (r as RecentItem[]) : [];
}

export function timeAgo(at: number, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? 'yesterday' : `${d} days ago`;
}

// Backend client. The agents run on the server (backend/ymir/qa) and stream trace events as NDJSON:
// {type:'node'|'status'|'answer'}; see backend/ymir/qa/stream.py.
// The browser calls /api/* on this site and next.config.ts forwards it to the API. Setting
// NEXT_PUBLIC_YMIR_API_BASE makes it call the API directly instead (the API then needs CORS for this origin).
import type { Drawing, LibraryEntry, ProjectEntry, TraceEvent } from './types';
import type { Project } from './project';

const BASE = (process.env.NEXT_PUBLIC_YMIR_API_BASE ?? '').replace(/\/+$/, '');
const api = (path: string) => `${BASE}/${path.replace(/^\/+/, '')}`;
/** API-relative URLs (thumbnails, images) as URLs the browser can load from any page. */
export const absolute = (url: string) => (/^(https?:)?\/\//.test(url) ? url : api(url));

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') msg = body.detail;
    } catch { /* keep the status text */ }
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}
const get = <T,>(path: string) => fetch(api(path)).then((r) => json<T>(r));

export const loadLibrary = () => get<LibraryEntry[]>('api/library').then((lib) => lib.map((e) => ({ ...e, thumbnail: absolute(e.thumbnail) })));
export const loadProjects = () =>
  get<ProjectEntry[]>('api/projects').then((ps) => ps.map((p) => ({ ...p, thumbnail: p.thumbnail && absolute(p.thumbnail) }))).catch(() => []);
export const loadProject = (id: string) => get<Project>(`api/projects/${encodeURIComponent(id)}`);
export const loadDrawing = (id: string) =>
  get<Drawing>(`api/drawings/${encodeURIComponent(id)}`).then((d) => ({ ...d, image: { ...d.image, url: absolute(d.image.url) } }));
export const loadBuildTrace = (id: string) => get<TraceEvent[]>(`api/drawings/${encodeURIComponent(id)}/trace`).catch(() => []);
export const previewGraph = () => get<TraceEvent[]>('api/agents').catch(() => []);
export const loadSuggestions = (drawingId: string) => get<string[]>(`api/drawings/${encodeURIComponent(drawingId)}/suggestions`).catch(() => []);

export interface AskBody {
  drawing_id: string;
  question: string;
  project_id?: string;
  sheet_id?: string;
  context: { m_per_px: number; scale_confirmed: boolean; height_m: number; removed_walls: string[]; added_walls: { id: string; a: [number, number]; b: [number, number] }[] };
}
/** Streams one answer's trace events. Returns a cancel function. */
export function ask(body: AskBody, onEvent: (ev: TraceEvent) => void): () => void {
  const ctl = new AbortController();
  (async () => {
    try {
      const res = await fetch(api('api/ask'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: ctl.signal });
      if (!res.ok || !res.body) await json(res);
      const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        for (let i = buf.indexOf('\n'); i >= 0; i = buf.indexOf('\n')) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 1);
          if (line) onEvent(JSON.parse(line) as TraceEvent);
        }
      }
      if (buf.trim()) onEvent(JSON.parse(buf) as TraceEvent);
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        onEvent({ type: 'answer', answer: { status: 'error', citations: [], text: 'The answering service is unavailable right now. Try again in a moment.' } });
      }
    }
  })();
  return () => ctl.abort();
}

export const submitReview = (record: unknown) =>
  fetch(api('api/reviews'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(record) }).then((r) => json<{ id: number }>(r));

/** Deletes an uploaded drawing on the server (samples can't be deleted). */
export async function deleteDrawing(id: string) {
  const res = await fetch(api(`api/drawings/${encodeURIComponent(id)}`), { method: 'DELETE' });
  if (!res.ok && res.status !== 404) await json(res);
}

export function uploadDrawing(file: File) {
  const form = new FormData();
  form.append('file', file);
  return fetch(api('api/drawings'), { method: 'POST', body: form }).then((r) => json<LibraryEntry>(r));
}

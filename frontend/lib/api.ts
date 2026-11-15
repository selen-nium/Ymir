// Backend client. Ask streams durable job, stage, node/status, progress and answer
// events as NDJSON; see backend/ymir/lens_jobs.py (also used by reconnect and replay).
// The browser calls /api/* on this site and next.config.ts forwards it to the API. Setting
// NEXT_PUBLIC_YMIR_API_BASE makes it call the API directly instead (the API then needs CORS for this origin).
import type { Drawing, LibraryEntry, ProjectEntry, TraceEvent, LensJob, LensStatus, RoutingMode } from './types';
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
  routing_mode: RoutingMode;
  drawing_id: string;
  request_id: string;
  selected_drawing_ids: string[];
  conversation_id: string;
  prior_job_ids: string[];
  question: string;
  project_id?: string;
  sheet_id?: string;
  context: { m_per_px: number; scale_confirmed: boolean; height_m: number; removed_walls: string[]; added_walls: { id: string; a: [number, number]; b: [number, number] }[] };
}
/** Disconnecting a subscriber never cancels a durable job. */
export function ask(body: AskBody, onEvent: (ev: TraceEvent) => void): () => void {
  return streamEvents('api/ask',onEvent,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
}
export function reconnectJob(id: string, onEvent: (ev: TraceEvent) => void, after = 0) {
  return streamEvents(`api/lens/jobs/${encodeURIComponent(id)}/events?after=${after}`,onEvent);
}
function streamEvents(path: string,onEvent: (ev: TraceEvent) => void,init: RequestInit = {}): () => void {
  const ctl = new AbortController();
  (async () => {
    try {
      const res=await fetch(api(path),{...init,signal:ctl.signal});
      if(!res.ok){
        let message=`Request failed (${res.status})`;try{await json(res);}catch(e){message=(e as Error).message;}
        onEvent({type:'answer',answer:{status:'error',text:message,citations:[]}});return;
      }
      if(!res.body)throw new Error('No event stream. Reload to reconnect.');
      const reader=res.body!.pipeThrough(new TextDecoderStream()).getReader();
      let buf='',answered=false;
      const emit=(line: string)=>{const ev=JSON.parse(line) as TraceEvent;if(ev.type==='answer')answered=true;onEvent(ev);};
      for (;;) {
        const {value,done}=await reader.read(); if(done) break;
        buf+=value;
        for(let i=buf.indexOf('\n');i>=0;i=buf.indexOf('\n')) {
          const line=buf.slice(0,i).trim();buf=buf.slice(i+1);
          if(line) emit(line);
        }
      }
      if(buf.trim())emit(buf);
      // The server always ends a job stream with its answer; anything else is a dropped connection.
      if(!answered)onEvent({type:'connection',message:'The connection closed before the answer arrived.'});
    } catch(e) {
      if((e as Error).name!=='AbortError')onEvent({type:'connection',message:(e as Error).message || 'Connection interrupted. Reload to reconnect to this job.'});
    }
  })();
  return () => ctl.abort();
}
export const lensStatus = () => get<LensStatus>('api/lens/status');
export const loadJobs = (project: string) => get<LensJob[]>(`api/lens/jobs?project_id=${encodeURIComponent(project)}&conversation_id=${encodeURIComponent(project)}`);
export const loadJobTrace = (id: string) => get<TraceEvent[]>(`api/lens/jobs/${encodeURIComponent(id)}/artifacts/events`);
export const loadReviews = (sheet: string) => get<import('./session').ReviewRecord[]>(`api/reviews?drawing_id=${encodeURIComponent(sheet)}`);
export const cancelJob = (id: string) => fetch(api(`api/lens/jobs/${encodeURIComponent(id)}/cancel`),{method:'POST'}).then(r=>json<LensJob>(r));

export const submitReview = (record: unknown) =>
  fetch(api('api/reviews'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(record) }).then((r) => json<{ id: number; record: import('./session').ReviewRecord }>(r));

/** Deletes an uploaded drawing on the server (samples can't be deleted). */
export async function deleteDrawing(id: string) {
  const res = await fetch(api(`api/drawings/${encodeURIComponent(id)}`), { method: 'DELETE' });
  if (!res.ok && res.status !== 404) await json(res);
}

export interface DocumentInventory { id: string; filename: string; format: string; page_count: number; pages: {number: number; rotation?: number; native_text?: boolean}[]; selectors: {id: string; label: string}[]; limitations: string[] }
export interface ImportSelection { pages?: number[]; options?: { element_ids?: string[]; storey_id?: string; dxf_unit?: string; svg_scale?: {p0: number[]; p1: number[]; length: number; unit: string; reading: string; reviewer: string} } }
const imports = new WeakMap<File,{doc: DocumentInventory; selection: ImportSelection}>();
export const selectImport = (file: File,doc: DocumentInventory,selection: ImportSelection) => imports.set(file,{doc,selection});
export function uploadDocument(file: File) {
  const form=new FormData();form.append('file',file);
  return fetch(api('api/documents'),{method:'POST',body:form}).then(r=>json<DocumentInventory>(r));
}
export async function uploadDrawings(file: File) {
  const selected=imports.get(file);
  const doc=selected?.doc ?? await uploadDocument(file);
  const prepared=await fetch(api(`api/documents/${doc.id}/sheets`),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(selected?.selection ?? {})}).then(r=>json<{sheets: LibraryEntry[]; limitations: string[]}>(r));
  if(!prepared.sheets.length)throw new Error(prepared.limitations.join(' · ') || 'No supported room geometry was found.');
  return prepared.sheets;
}
export async function uploadDrawing(file: File) { return (await uploadDrawings(file))[0]; }

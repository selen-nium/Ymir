// Backend client. The agents run on the server (backend/ymir/qa) and stream trace events as NDJSON:
// {type:'node'|'status'|'answer'}; see backend/ymir/qa/stream.py.
// Same origin locally; on Vercel, config.js sets YMIR_API_BASE to the Modal backend.
const BASE = String(globalThis.YMIR_API_BASE || '').replace(/\/+$/, '');
const api = (path) => (BASE ? `${BASE}/${path}` : path);
const absolute = (url) => (!BASE || /^(https?:)?\/\//.test(url) ? url : api(url));

async function json(res) {
  if (!res.ok) {
    let msg = res.statusText;
    try { const body = await res.json(); msg = typeof body.detail === 'string' ? body.detail : msg; } catch { /* keep status text */ }
    throw new Error(msg);
  }
  return res.json();
}

export const loadLibrary = () => fetch(api('api/library')).then(json).then((lib) => lib.map((e) => ({ ...e, thumbnail: absolute(e.thumbnail) })));
export const loadDrawing = (id) => fetch(api(`api/drawings/${encodeURIComponent(id)}`)).then(json)
  .then((d) => ({ ...d, image: { ...d.image, url: absolute(d.image.url) } }));
export const loadProjects = () => fetch(api('api/projects')).then(json).then((ps) => ps.map((p) => ({ ...p, thumbnail: p.thumbnail && absolute(p.thumbnail) }))).catch(() => []);
export const loadProject = (id) => fetch(api(`api/projects/${encodeURIComponent(id)}`)).then(json);
export const loadBuildTrace = (id) => fetch(api(`api/drawings/${encodeURIComponent(id)}/trace`)).then(json).catch(() => []);
export const previewGraph = () => fetch(api('api/agents')).then(json).catch(() => []);
export const suggestions = (session) => fetch(api(`api/drawings/${encodeURIComponent(session.state.d.id)}/suggestions`)).then(json).catch(() => []);

// Streams one answer. Sends the user's Build decisions so answers use their scale and walls; `extra` adds the
// project and sheet (project_id, sheet_id) for backends that answer across sheets. Returns a cancel function.
export function ask(session, question, onEvent, extra = {}) {
  const ctl = new AbortController();
  const s = session.state;
  const body = {
    drawing_id: s.d.id, question, ...extra,
    context: { m_per_px: s.scale, scale_confirmed: s.scaleConfirmed, height_m: s.height, removed_walls: [...session.removed()],
      added_walls: s.d.walls.filter((w) => w.user).map(({ id, a, b }) => ({ id, a, b })) },
  };
  (async () => {
    try {
      const res = await fetch(api('api/ask'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: ctl.signal });
      if (!res.ok || !res.body) await json(res);
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        for (let i = buf.indexOf('\n'); i >= 0; i = buf.indexOf('\n')) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 1);
          if (line) onEvent(JSON.parse(line));
        }
      }
      if (buf.trim()) onEvent(JSON.parse(buf));
    } catch (e) {
      if (e.name !== 'AbortError') onEvent({ type: 'answer', answer: { status: 'error', citations: [], text: 'The answering service is unavailable right now. Try again in a moment.' } });
    }
  })();
  return () => ctl.abort();
}

export const submitReview = (record) =>
  fetch(api('api/reviews'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(record) }).then(json);

export function uploadDrawing(file) {
  const form = new FormData();
  form.append('file', file);
  return fetch(api('api/drawings'), { method: 'POST', body: form }).then(json);
}

// Live agent/tool flowchart driven by trace-stream events (training-brief contract):
//   {type:'node', id, name, tool, parents}   adds a node (layout comes from the parent links)
//   {type:'status', id, state, summary, ms}  running | done | skipped | failed | waiting (on the user) | pending
// Re-sending a node with new parents re-links it (the frontend inserts its own "You" steps this way).
// Lens activity is shown in execution order; older static graphs retain parent links.
// Imperative, mounted by <TraceDock>.
import type { TraceEvent, TraceState } from './types';
import { createLensTraceSequence } from './lens-trace';

const NS = 'http://www.w3.org/2000/svg';
const NODE_H = 72, NODE_W = 172, NODE_GAP = 36, MIN_H = 224;
const STATE_CLASS: Partial<Record<TraceState, string>> = { running: 'run', done: 'done', skipped: 'skip', failed: 'fail', waiting: 'you' };
const EDGE_CLASS: Partial<Record<TraceState, string>> = { running: 'active', waiting: 'active', done: 'done', skipped: 'skip', failed: 'fail' };
type NodeEvent = Extract<TraceEvent, { type: 'node' }>;

export function createTrace(el: HTMLElement) {
  el.classList.add('trace-chart');
  let sequence=createLensTraceSequence();
  const viewport=el.parentElement ?? el;
  let follow=true,replaying=false;
  const onScroll=()=>{follow=viewport.scrollWidth-viewport.clientWidth-viewport.scrollLeft<32;};
  viewport.addEventListener('scroll',onScroll,{passive:true});
  const svg = document.createElementNS(NS, 'svg');
  el.appendChild(svg);
  let nodes = new Map<string, { def: NodeEvent; el: HTMLDivElement; state: TraceState | null }>();
  let edges = new Map<string, SVGPathElement>(); // "a>b" → path

  function layout() {
    const available=viewport.clientWidth;
    if (!available || !nodes.size) return;
    if(sequence.sequential){
      const width=Math.max(available,32+nodes.size*(NODE_W+NODE_GAP)-NODE_GAP),height=124;
      el.classList.add('sequential');el.style.width=`${width}px`;el.style.height=`${height}px`;
      svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
      let i=0;const pos=new Map<string,number>();
      for(const [id,n] of nodes){
        const x=16+i*(NODE_W+NODE_GAP);pos.set(id,x);n.el.dataset.step=String(++i);
        Object.assign(n.el.style,{left:`${x}px`,top:'26px',width:`${NODE_W}px`});
      }
      for(const [key,path] of edges){
        const [a,b]=key.split('>'),x1=pos.get(a),x2=pos.get(b);
        if(x1!=null && x2!=null)path.setAttribute('d',`M${x1+NODE_W},${26+NODE_H/2} H${x2}`);
      }
      if(follow && !replaying)viewport.scrollLeft=viewport.scrollWidth-viewport.clientWidth;
      return;
    }
    const depth = new Map<string, number>();
    const depthOf = (id: string, seen = new Set<string>()): number => {
      if (depth.has(id)) return depth.get(id)!;
      if (seen.has(id)) return 0;
      seen.add(id);
      const ps = (nodes.get(id)?.def.parents ?? []).filter((p) => nodes.has(p));
      const dd = ps.length ? 1 + Math.max(...ps.map((p) => depthOf(p, seen))) : 0;
      depth.set(id, dd);
      return dd;
    };
    const cols = new Map<number, string[]>();
    for (const id of nodes.keys()) {
      const dd = depthOf(id);
      if (!cols.has(dd)) cols.set(dd, []);
      cols.get(dd)!.push(id);
    }
    const maxD = Math.max(0, ...cols.keys());
    const width=Math.max(available,32+(maxD+1)*NODE_W+maxD*NODE_GAP);
    el.style.width=`${width}px`;
    const padX = 16, padY = 12;
    // grow when a column holds more nodes than the default height fits
    const height = Math.max(MIN_H, 2 * padY + Math.max(...[...cols.values()].map((ids) => ids.length)) * (NODE_H + 6));
    el.style.height = `${height}px`;
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    // Keep labels readable and scroll when the graph needs more space.
    const colStep = maxD ? (width - 2 * padX - NODE_W) / maxD : 0;
    const pos = new Map<string, { x: number; y: number }>();
    for (const [dd, ids] of cols) {
      const slot = (height - 2 * padY) / ids.length;
      ids.forEach((id, i) => {
        const x = padX + dd * colStep, y = padY + slot * (i + 0.5) - NODE_H / 2;
        pos.set(id, { x, y });
        Object.assign(nodes.get(id)!.el.style, { left: `${x}px`, top: `${y}px`, width: `${NODE_W}px` });
      });
    }
    for (const [key, path] of edges) {
      const [a, b] = key.split('>');
      const pa = pos.get(a), pb = pos.get(b);
      if (!pa || !pb) continue;
      const x1 = pa.x + NODE_W, y1 = pa.y + NODE_H / 2, x2 = pb.x, y2 = pb.y + NODE_H / 2, mx = (x1 + x2) / 2;
      path.setAttribute('d', `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
    }
  }
  const resizeObserver = new ResizeObserver(layout);
  resizeObserver.observe(viewport);

  function addEdges(id: string, parents: string[] = [], state: TraceState | null) {
    for (const p of parents) {
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('class', `tedge ${(state && EDGE_CLASS[state]) || 'idle'}`);
      svg.appendChild(path);
      edges.set(`${p}>${id}`, path);
    }
  }

  function applyNode(ev: TraceEvent) {
    if (ev.type === 'node') {
      const known = nodes.get(ev.id);
      if (known) {
        const old = known.def.parents ?? [], next = ev.parents ?? [];
        known.def={...known.def,...ev};
        known.el.querySelector('b')!.textContent=ev.name;known.el.querySelector('span')!.textContent=ev.tool ?? '';
        known.el.title=[ev.name,ev.tool,ev.detail].filter(Boolean).join(' · ');
        known.el.setAttribute('aria-label',known.el.title);
        if (old.join() === next.join()) return;
        for (const p of old) { edges.get(`${p}>${ev.id}`)?.remove(); edges.delete(`${p}>${ev.id}`); }
        known.def = { ...known.def, parents: next };
        addEdges(ev.id, next, known.state);
        layout();
        return;
      }
      const d = document.createElement('div');
      d.className = 'tnode idle';d.dataset.id=ev.id;
      d.title=[ev.name,ev.tool,ev.detail].filter(Boolean).join(' · ');
      d.tabIndex=0;d.setAttribute('role','group');d.setAttribute('aria-label',d.title);
      d.innerHTML = '<div class="tn-h"><i></i><b></b><span></span></div><div class="tn-o"></div>';
      d.querySelector('b')!.textContent = ev.name;
      d.querySelector('span')!.textContent = ev.tool ?? '';
      el.appendChild(d);
      nodes.set(ev.id, { def: ev, el: d, state: null });
      addEdges(ev.id, ev.parents, null);
      layout();
    } else if (ev.type === 'status') {
      const n = nodes.get(ev.id);
      if (!n) return;
      n.state = ev.state;
      n.el.className = `tnode ${STATE_CLASS[ev.state] ?? 'idle'}`;
      if (ev.summary !== undefined) {
        n.el.querySelector('.tn-o')!.textContent = ev.summary + (ev.ms ? ` · ${ev.ms} ms` : '');
        n.el.title=[n.def.name,n.def.tool,n.def.detail,ev.summary].filter(Boolean).join(' · ');
        n.el.setAttribute('aria-label',n.el.title);
      }
      for (const p of n.def.parents ?? []) edges.get(`${p}>${ev.id}`)?.setAttribute('class', `tedge ${EDGE_CLASS[ev.state] ?? 'idle'}`);
    }
  }

  function apply(ev: TraceEvent) {
    const wasSequential=sequence.sequential,projected=sequence.apply(ev);
    if(!wasSequential && sequence.sequential)clearGraph();
    projected.forEach(applyNode);
  }

  function clearGraph() {
    for (const n of nodes.values()) n.el.remove();
    for (const p of edges.values()) p.remove();
    nodes = new Map(); edges = new Map();
  }

  function reset() {
    clearGraph();sequence=createLensTraceSequence();follow=true;
    el.classList.remove('sequential');el.style.width='';el.style.height=`${MIN_H}px`;viewport.scrollLeft=0;
  }

  return {
    apply,
    reset,
    /** Draw a finished trace at once (e.g. when revisiting an earlier answer). */
    replay(events: TraceEvent[]) { reset();replaying=true;events.forEach(apply);replaying=false;layout(); },
    layout,
    name: (id: string) => nodes.get(id)?.def.name,
    dispose() { resizeObserver.disconnect();viewport.removeEventListener('scroll',onScroll);reset(); svg.remove(); el.classList.remove('trace-chart'); },
  };
}
export type Trace = ReturnType<typeof createTrace>;

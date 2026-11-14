'use client';
import { useEffect, useRef, useState } from 'react';
import { Icon } from '@/components/Icon';
import { isPlaceable, isPlaced } from '@/lib/project';
import { countNoun } from '@/lib/session';
import type { Entry } from '@/lib/workspace';
import { useSessionTick, useWorkspace, useWs } from './context';

/** The conversation about this building or drawing: questions, cited answers, the box review. */
export function AskPanel() {
  const ws = useWorkspace();
  const thread = useWs((s) => s.thread), busy = useWs((s) => s.busy), suggestions = useWs((s) => s.suggestions);
  const reviewing = useWs((s) => s.reviewingId), single = useWs((s) => !!s.project?.single);
  const [q, setQ] = useState('');
  const end = useRef<HTMLDivElement>(null);
  const lastStatus = thread.at(-1)?.status;
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [thread.length, lastStatus]);
  const send = (text: string) => { ws.actions.ask(text); setQ(''); };

  return (
    <div className="askp">
      <div className="thread">
        {!thread.length && (
          <div className="starter">
            <p>Ask about this {single ? 'drawing' : 'building'}. Answers point to where they came from in the {single ? 'drawing' : 'sheets'} and the 3D model.</p>
            {suggestions.length > 0 && <span className="starter-h">Try asking</span>}
            {suggestions.map((s) => <button key={s} className="starter-q" onClick={() => send(s)} disabled={busy}>{s}<Icon name="arrow" /></button>)}
          </div>
        )}
        {thread.map((e) => (
          <div key={e.id} style={{ display: 'contents' }}>
            <div className="msg q">{e.q}</div>
            {e.status === 'pending' ? <div className="msg a pending"><span className="spin" />{e.stage}</div>
              : e.status === 'review' ? <ReviewCard entry={e} />
                : <AnswerCard entry={e} />}
          </div>
        ))}
        <div ref={end} />
      </div>
      {thread.length > 0 && <div className="sugg">{suggestions.slice(0, 4).map((s) => <button key={s} onClick={() => send(s)} disabled={busy}>{s}</button>)}</div>}
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(q); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" aria-label="Your question"
          placeholder={reviewing ? 'Check the boxes first' : single ? 'Ask about this drawing' : 'Ask about this building'} />
        <button disabled={busy || !q.trim()} aria-label="Ask"><Icon name="up" /></button>
      </form>
    </div>
  );
}

/** Answer text from the backend: plain text with <b> and <cite>n</cite> only, turned into elements (never HTML). */
function AnswerText({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  const parts = text.split(/(<b>.*?<\/b>|<cite>\d+<\/cite>)/g);
  return (
    <>
      {parts.map((p, i) => {
        const b = p.match(/^<b>(.*)<\/b>$/), c = p.match(/^<cite>(\d+)<\/cite>$/);
        if (b) return <b key={i}><AnswerText text={b[1]} onCite={onCite} /></b>;
        if (c) return <CiteButton key={i} n={Number(c[1])} onCite={onCite} />;
        return p.replace(/<[^>]*>/g, '');
      })}
    </>
  );
}
function CiteButton({ n, onCite }: { n: number; onCite: (n: number) => void }) {
  return <button type="button" className="cite" aria-label={`Show source ${n}`} onClick={(e) => { e.stopPropagation(); onCite(n); }}>{n}</button>;
}

function AnswerCard({ entry }: { entry: Entry }) {
  const ws = useWorkspace();
  const current = useWs((s) => s.currentId === entry.id), project = useWs((s) => s.project);
  const a = entry.answer!;
  const abstained = a.status !== 'answered';
  const g = ws.read.ghostForEntry(entry);
  const cites = a.citations ?? [];
  const sheet = ws.read.viewOf(entry.sheet);
  const unplaced = !!sheet && isPlaceable(sheet.kind) && !isPlaced(sheet);
  const level = g ? ws.read.levelName(g.level).toLowerCase() : '';
  const cite = async (n: number) => { await ws.actions.revisit(entry.id); ws.actions.focusCite(entry.id, n); };
  const exportLabels = () => {
    if (!entry.record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(entry.record, null, 2)], { type: 'application/json' }));
    Object.assign(document.createElement('a'), { href: url, download: `labels-${entry.record.drawing.id}-${entry.record.class}.json` }).click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const open = () => ws.actions.revisit(entry.id);
  return (
    <div className={`msg a${abstained ? ' abstain' : ''}${current ? ' on' : ''}`}
      role={abstained ? undefined : 'button'} tabIndex={abstained ? undefined : 0} aria-label={abstained ? undefined : 'Show this answer’s evidence'}
      onClick={open} onKeyDown={(e) => { if (!abstained && (e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) { e.preventDefault(); open(); } }}>
      {!project?.single && <div className="scope">On {ws.read.sheetLabel(entry.sheet)}{unplaced ? ' · not placed in the building yet' : ''}</div>}
      {g ? (
        <div>{project?.single
          ? <>This needs the {g.noun}, which a single drawing doesn’t have.</>
          : <>This building has no {g.noun} for the {level} yet, so I can’t answer that from its sheets.</>}</div>
      ) : <div><AnswerText text={a.text} onCite={cite} /></div>}
      {a.checked && <div className="mark ok"><Icon name="check" />Checked by you{entry.record && <button onClick={(e) => { e.stopPropagation(); exportLabels(); }}>Export labels</button>}</div>}
      {a.unchecked && <div className="mark">Not checked · {Math.round((a.confidence ?? 0) * 100)}% sure</div>}
      {g && (project?.single
        ? <button className="needs" onClick={(e) => { e.stopPropagation(); ws.actions.toBuilding(); }}><Icon name="layers" />Start a building from this drawing</button>
        : <button className="needs" onClick={(e) => { e.stopPropagation(); ws.actions.selectGhost(g.id); }}><Icon name="up" />Add the {level} {g.noun}</button>)}
      {cites.length > 0 && (
        <div className="cites">
          {cites.slice(0, 5).map((c) => <div key={c.n}><CiteButton n={c.n} onCite={cite} />{c.label}</div>)}
          {cites.length > 5 && <div>+{cites.length - 5} more</div>}
        </div>
      )}
    </div>
  );
}

function ReviewCard({ entry }: { entry: Entry }) {
  const { session, actions } = useWorkspace();
  useSessionTick();
  const drawing = useWs((s) => s.drawing);
  const a = entry.answer!, st = session.reviewStats(), cls = a.review!.class;
  const sure = st.count - st.toCheck;
  return (
    <div className="msg a review">
      <div>Probably <b>{st.count}</b> {countNoun(cls, st.count)} · <span className="warn">{Math.round((a.confidence ?? 0) * 100)}% sure</span></div>
      <div className="act">
        <div className="legend"><span><i style={{ background: 'var(--answer)' }} />Sure</span><span><i style={{ background: 'var(--flag)' }} />Check</span><span><i style={{ background: 'var(--ghost)' }} />Not counted</span></div>
        <span className="chip">Click a box to count or drop it. Drag an edge to resize.</span>
        <span className="chip">{sure} sure{st.toCheck ? ` · ${st.toCheck} to check` : ''}</span>
        <div className="row nowrap">
          <button className={`btn sm${drawing ? ' on' : ''}`} onClick={() => actions.setDrawing(!drawing)}><Icon name="pen" />Draw</button>
          <span className="grow" />
          <button className="btn sm" onClick={actions.skipReview}>Skip</button>
          <button className="btn sm acc" onClick={actions.doneReview}>Done</button>
        </div>
      </div>
    </div>
  );
}

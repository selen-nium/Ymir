'use client';
import { useEffect, useRef, useState } from 'react';
import { Icon } from '@/components/Icon';
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
        {!thread.length && <div className="empty">Answers point to where they came from in the drawings and the building.</div>}
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
      <div className="sugg">{suggestions.map((s) => <button key={s} onClick={() => send(s)} disabled={busy}>{s}</button>)}</div>
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
        if (c) return <cite key={i} onClick={(e) => { e.stopPropagation(); onCite(Number(c[1])); }}>{c[1]}</cite>;
        return p.replace(/<[^>]*>/g, '');
      })}
    </>
  );
}

function AnswerCard({ entry }: { entry: Entry }) {
  const ws = useWorkspace();
  const current = useWs((s) => s.currentId === entry.id), project = useWs((s) => s.project);
  const a = entry.answer!;
  const abstained = a.status !== 'answered';
  const g = ws.read.ghostForEntry(entry);
  const cites = a.citations ?? [];
  const cite = async (n: number) => { await ws.actions.revisit(entry.id); ws.actions.focusCite(entry.id, n); };
  const exportLabels = () => {
    if (!entry.record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(entry.record, null, 2)], { type: 'application/json' }));
    Object.assign(document.createElement('a'), { href: url, download: `labels-${entry.record.drawing.id}-${entry.record.class}.json` }).click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <div className={`msg a${abstained ? ' abstain' : ''}${current ? ' on' : ''}`} onClick={() => ws.actions.revisit(entry.id)}>
      <div><AnswerText text={a.text} onCite={cite} /></div>
      {a.checked && <div className="mark ok"><Icon name="check" />Checked by you{entry.record && <button onClick={(e) => { e.stopPropagation(); exportLabels(); }}>Export labels</button>}</div>}
      {a.unchecked && <div className="mark">Not checked · {Math.round((a.confidence ?? 0) * 100)}% sure</div>}
      {g && (project?.single ? <>
        <div className="mark">This needs the {g.noun}. Start a building from this drawing to add it.</div>
        <button className="needs" onClick={(e) => { e.stopPropagation(); ws.actions.toBuilding(); }}><Icon name="layers" />Start a building</button>
      </> : <>
        <div className="mark">This needs the {g.noun} for {ws.read.levelName(g.level).toLowerCase()}, which this building doesn’t have yet.</div>
        <button className="needs" onClick={(e) => { e.stopPropagation(); ws.actions.selectGhost(g.id); }}><Icon name="up" />Add {g.noun}</button>
      </>)}
      {cites.length > 0 && (
        <div className="cites">
          {cites.slice(0, 5).map((c) => <div key={c.n}><cite onClick={(e) => { e.stopPropagation(); cite(c.n); }}>{c.n}</cite>{c.label}</div>)}
          {cites.length > 5 && <div>+{cites.length - 5} more</div>}
        </div>
      )}
      {!abstained && !project?.single && <div className="mark">From {ws.read.sheetLabel(entry.sheet)}</div>}
    </div>
  );
}

function ReviewCard({ entry }: { entry: Entry }) {
  const { session, actions } = useWorkspace();
  useSessionTick();
  const drawing = useWs((s) => s.drawing);
  const a = entry.answer!, st = session.reviewStats();
  return (
    <div className="msg a review">
      <div>Probably <b>{a.value}</b> {countNoun(a.review!.class, Number(a.value))} · <span className="warn">{Math.round((a.confidence ?? 0) * 100)}% sure</span></div>
      <div className="act">
        <div className="legend"><span><i style={{ background: 'var(--answer)' }} />Counted</span><span><i style={{ background: 'var(--flag)' }} />Check</span><span><i style={{ background: 'var(--ghost)' }} />Not counted</span></div>
        <span className="chip">Click a box to count or drop it. Drag an edge to resize.</span>
        <div className="row">
          <span className="chip">{st.count} counted{st.toCheck ? ` · ${st.toCheck} to check` : ''}</span><span className="grow" />
          <button className={`btn sm${drawing ? ' on' : ''}`} onClick={() => actions.setDrawing(!drawing)}><Icon name="pen" />Draw</button>
          <button className="btn sm" onClick={actions.skipReview}>Skip</button>
          <button className="btn sm acc" onClick={actions.doneReview}>Done</button>
        </div>
      </div>
    </div>
  );
}

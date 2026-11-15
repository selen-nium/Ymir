'use client';
import { useEffect, useRef, useState } from 'react';
import { Icon } from '@/components/Icon';
import { isPlaceable, isPlaced } from '@/lib/project';
import { countNoun } from '@/lib/session';
import { absolute } from '@/lib/api';
import {answerText, answerNotes, savedReview, shortDetail, productName} from '@/lib/answer-presentation';
import {modeReady, modeLabel} from '@/lib/routing';
import {elapsedLabel} from '@/lib/job-progress';
import type {RoutingMode} from '@/lib/types';
import type { Entry } from '@/lib/workspace';
import { useSessionTick, useWorkspace, useWs } from './context';

/** The conversation about this building or drawing: questions, cited answers, the box review. */
export function AskPanel() {
  const ws = useWorkspace();
  const thread = useWs((s) => s.thread), busy = useWs((s) => s.busy), suggestions = useWs((s) => s.suggestions);
  const reviewing = useWs((s) => s.reviewingId), single = useWs((s) => !!s.project?.single);
  const saving = useWs(s=>s.savingReview);
  const selected=useWs(s=>s.selectedIds), project=useWs(s=>s.project), lens=useWs(s=>s.lensStatus);
  const mode=useWs(s=>s.routingMode);
  const locked=busy || thread.some(e=>e.status==='pending');
  const ready=modeReady(lens,mode);
  const [q, setQ] = useState('');
  const end = useRef<HTMLDivElement>(null);
  const lastStatus = thread.at(-1)?.status;
  useEffect(()=>{const timer=setInterval(()=>{void ws.actions.refreshLens();},busy?3000:15000);return ()=>clearInterval(timer);},[busy,ws]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [thread.length, lastStatus]);
  const send = (text: string) => { ws.actions.ask(text); setQ(''); };

  return (
    <div className="askp">
      <div className="ask-toolbar"><button type="button" className="btn sm" disabled={!thread.length || thread.some(e=>e.status==='pending') || saving} onClick={ws.actions.clearChat} title="Start a fresh conversation. Drawings and saved labels stay."><Icon name="trash" />Clear chat</button></div>
      <div id="routing-readiness" className="lens-status" role="status" aria-live="polite">{!lens ? `Checking ${modeLabel(mode)}…` : ready ? `${modeLabel(mode)} ready` : `${modeLabel(mode)} unavailable · Try again when it is ready.`}</div>
      <fieldset className="ask-scope"><legend>Question scope · {selected.length}/3 sheets</legend>
        {project?.sheets.map(sheet=><label key={sheet.drawing_id}><input type="checkbox" checked={selected.includes(sheet.drawing_id)} disabled={busy || (selected.length===1 && selected.includes(sheet.drawing_id)) || (selected.length===3 && !selected.includes(sheet.drawing_id))} onChange={()=>ws.actions.toggleScope(sheet.drawing_id)}/>{ws.read.sheetLabel(sheet.drawing_id)}</label>)}
      </fieldset>
      <div className="thread">
        {!thread.length && (
          <div className="starter">
            <p>Ask about this {single ? 'drawing' : 'building'}. Answers point to where they came from in the {single ? 'drawing' : 'sheets'} and the 3D model.</p>
            {suggestions.length > 0 && <span className="starter-h">Try asking</span>}
            {suggestions.map((s) => <button key={s} className="starter-q" onClick={() => send(s)} disabled={locked || !ready}>{s}<Icon name="arrow" /></button>)}
          </div>
        )}
        {thread.map((e) => (
          <div key={e.id} style={{ display: 'contents' }}>
            <div className="msg q">{e.q}<small className="scope">{e.routing_mode && `${modeLabel(e.routing_mode)} · `}{(e.sheets ?? [e.sheet]).map(ws.read.sheetLabel).join(" · ")}</small></div>
            {e.status === 'pending' ? <PendingAnswer entry={e} />
              : e.status === 'review' ? saving ? <div className="msg a" role="status">Saving labels…</div> : <ReviewCard entry={e} />
                : <AnswerCard entry={e} />}
          </div>
        ))}
        <div ref={end} />
      </div>
      {thread.length > 0 && <div className="sugg">{suggestions.slice(0, 4).map((s) => <button key={s} onClick={() => send(s)} disabled={locked || !ready}>{s}</button>)}</div>}
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(q); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" aria-label="Your question"
          placeholder={reviewing ? 'Check the boxes first' : single ? 'Ask about this drawing' : 'Ask about this building'} />
        <select aria-label="Response mode" aria-describedby="routing-readiness" value={mode} disabled={locked}
          onChange={e=>ws.actions.setRoutingMode(e.target.value as RoutingMode)}>
          {(['smart','frontier'] as const).map(option=><option key={option} value={option} disabled={!modeReady(lens,option)}>{modeLabel(option)}{lens && !modeReady(lens,option)?' (unavailable)':''}</option>)}
        </select>
        <button disabled={locked || !ready || !q.trim()} aria-label="Ask"><Icon name="up" /></button>
      </form>
    </div>
  );
}

function PendingAnswer({entry}: {entry: Entry}) {
  const ws=useWorkspace();
  const [now,setNow]=useState(()=>Date.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return ()=>clearInterval(timer);},[]);
  return <div className="msg a pending">
    <div role="status" aria-live="polite"><span className="spin" />{productName(entry.stage ?? 'Connecting to this job…')}</div>
    {entry.at && <small className="pending-elapsed" aria-live="off">{elapsedLabel(entry.at,now)}</small>}
    {entry.job_id && <div className="row"><button className="btn sm" onClick={()=>ws.actions.reconnect(entry.id)}>Reconnect</button><button className="btn sm" onClick={()=>ws.actions.cancelJob(entry.id)}>Cancel job</button></div>}
  </div>;
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
  const notes = answerNotes(a);
  const records = entry.records ?? (entry.record ? [entry.record] : []);
  const abstained = a.status !== 'answered' && a.status !== 'needs_review';
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
      ) : <div><AnswerText text={answerText(a)} onCite={cite} /></div>}
      {a.status==='needs_review' && <div className="mark warn">Needs review</div>}
      {!!a.counts?.length && (a.counts.length>1 || !!a.rooms?.length || !!a.readings?.length || !!a.measurements?.length) && <div className="lens-results"><b>Object counts</b>{a.counts.map(c=><div key={`${c.sheet}:${c.object_class}`}>{c.count==null?`${countNoun(c.object_class,2)} still need checking`:`${c.count} ${countNoun(c.object_class,c.count)}`} · {ws.read.sheetLabel(c.sheet)}</div>)}</div>}
      {!!a.rooms?.length && <div className="lens-results"><b>Room areas</b>{a.rooms.map(r=><div key={`${r.sheet}:${r.room_id}`}>{r.label || 'Room'}: {r.area_m2==null?'Area unavailable':`${r.area_m2.toFixed(2)} m²${r.measurement?.estimate?' · estimate':''}`}{r.measurement?.reason && <small>{shortDetail(r.measurement.reason)}</small>}</div>)}</div>}
      {!!a.measurements?.length && <div className="lens-results"><b>Measured from the drawing</b>{a.measurements.map((m,i)=><div key={i}>{m.result.distance_m!=null?`${m.result.distance_m.toFixed(2)} m`:m.result.area_m2!=null?`${m.result.area_m2.toFixed(2)} m²`:shortDetail(m.result.reason ?? 'Measurement unavailable')}</div>)}</div>}
      {!!a.readings?.length && <div className="lens-results"><b>Read from the drawing</b><small>Model readings, including printed areas</small>{a.readings.map((r,i)=><div key={i}>{shortDetail(r.description,110)}: {String(r.value ?? 'unavailable')} {r.unit}</div>)}</div>}
      {!!notes.length && <div className="lens-results lens-notes"><b>Worth checking</b><ul>{notes.map(r=><li key={r}>{r}</li>)}</ul></div>}
      {(a.reviews ?? (a.review?[a.review]:[])).map(r=><button key={`${r.sheet}:${r.class}`} className="btn sm" disabled={ws.store.getState().busy} onClick={e=>{e.stopPropagation();ws.actions.reviewCount(entry.id,r);}}>{savedReview(records,r)?'Edit labels':'Review labels'} · {countNoun(r.class,2)}{(a.reviews?.length ?? 0)>1?` · ${ws.read.sheetLabel(r.sheet ?? entry.sheet)}`:''}</button>)}
      {a.job_id && <div className="lens-receipts"><a className="lens-receipt" href={absolute(`api/lens/jobs/${a.job_id}/artifacts/result`)} target="_blank" rel="noreferrer" onClick={e=>e.stopPropagation()}>Full run details</a><a className="lens-receipt" href={absolute(`api/lens/jobs/${a.job_id}/artifacts/manifest`)} target="_blank" rel="noreferrer" onClick={e=>e.stopPropagation()}>Source files</a></div>}
      {a.checked && <div className="mark ok"><Icon name="check" />Labels saved{entry.record && <button onClick={(e) => { e.stopPropagation(); exportLabels(); }}>Export labels</button>}</div>}
      {a.unchecked && <div className="mark">Not checked{a.confidence!=null?` · ${Math.round(a.confidence*100)}% sure`:''}</div>}
      {g && (project?.single
        ? <button className="needs" onClick={(e) => { e.stopPropagation(); ws.actions.toBuilding(); }}><Icon name="layers" />Start a building from this drawing</button>
        : <button className="needs" onClick={(e) => { e.stopPropagation(); ws.actions.selectGhost(g.id); }}><Icon name="up" />Add the {level} {g.noun}</button>)}
      {cites.length > 0 && (
        <div className="cites">
          {cites.slice(0,5).map((c) => <div key={c.n}><CiteButton n={c.n} onCite={cite} />{shortDetail(productName(c.label),150)}</div>)}
          {cites.length>5 && <details><summary>Show {cites.length-5} more sources</summary>{cites.slice(5).map(c=><div key={c.n}><CiteButton n={c.n} onCite={cite}/>{shortDetail(productName(c.label),150)}</div>)}</details>}
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
      <div><b>{st.count}</b> {countNoun(cls, st.count)} selected · <span className="warn">{a.job_id?'Check the boxes':`${Math.round((a.confidence ?? 0)*100)}% sure`}</span></div>
      <div className="act">
        <div className="legend"><span><i style={{ background: 'var(--answer)' }} />Selected</span><span><i style={{ background: 'var(--flag)' }} />Check</span><span><i style={{ background: 'var(--ghost)' }} />Not counted</span></div>
        <span className="chip">Click a box to count or drop it. Drag an edge to resize.</span>
        <span className="chip">{sure} selected{st.toCheck ? ` · ${st.toCheck} to check` : ''}</span>
        <div className="row nowrap">
          <button className={`btn sm${drawing ? ' on' : ''}`} onClick={() => actions.setDrawing(!drawing)}><Icon name="pen" />Draw</button>
          <span className="grow" />
          <button className="btn sm" onClick={actions.skipReview}>{session.state.review?.saved?'Cancel':'Skip'}</button>
          <button className="btn sm acc" onClick={actions.doneReview}>Save labels</button>
        </div>
      </div>
    </div>
  );
}

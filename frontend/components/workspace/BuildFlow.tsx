'use client';
import { useState } from 'react';
import { Icon } from '@/components/Icon';
import { KINDS, isPlaceable, isPlaced, levelsByZ, type KindGroup } from '@/lib/project';
import { wallPx } from '@/lib/session';
import type { StepId } from '@/lib/steps';
import { toast } from '@/lib/toast';
import { parseLength } from '@/lib/units';
import { useSessionTick, useWorkspace, useWs } from './context';

/** The active sheet's Build steps; the current one (or the one the user opened) shows its action. */
export function BuildFlow() {
  const ws = useWorkspace();
  useSessionTick();
  const open = useWs((s) => s.open);
  useWs((s) => s.placing); useWs((s) => s.project); useWs((s) => s.activeId);
  const steps = ws.read.steps();
  const current = open ?? steps.find((s) => s.state === 'you')?.id;
  return (
    <div className="flow">
      {steps.map((n) => (
        <div key={n.id} className={`node ${n.state}`}>
          <span className="st">{n.state === 'done' && <Icon name="check" />}</span>
          <div>
            <button className="hd" onClick={() => ws.actions.setOpen(open === n.id ? null : n.id)} aria-expanded={current === n.id}>
              <b>{n.name}</b><span className="tool">{n.tool}</span>
            </button>
            <div className="res">{n.res}</div>
            {current === n.id && <StepAction id={n.id} />}
          </div>
        </div>
      ))}
    </div>
  );
}

function StepAction({ id }: { id: StepId }) {
  const activeId = useWs((s) => s.activeId);
  switch (id) {
    case 'kind': return <KindAction key={activeId} />;
    case 'confirm': return <ScaleAction />;
    case 'check': return <CheckAction />;
    case 'place': return <PlaceAction />;
    case 'model': return <ModelAction />;
    default: return null;
  }
}

const GROUPS: [KindGroup, string][] = [['plan', 'Plans'], ['upright', 'Upright'], ['table', 'Tables'], ['other', 'Other']];
function KindAction() {
  const ws = useWorkspace();
  const project = useWs((s) => s.project)!, v = ws.read.viewOf(useWs((s) => s.activeId))!;
  const [kind, setKind] = useState(v.kind);
  const [level, setLevel] = useState(v.placement.level ?? ws.read.viewOf(ws.read.baseId())?.placement.level ?? project.levels.find((l) => l.id !== 'F')?.id ?? '');
  return (
    <div className="act">
      <span className="chip">{v.kindSure ? 'Change it if it’s wrong.' : 'Guessed from its title and file name. Change it if it’s wrong.'}</span>
      <select className="sel" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind of sheet">
        {GROUPS.map(([g, label]) => (
          <optgroup key={g} label={label}>
            {Object.entries(KINDS).filter(([, k]) => k.group === g).map(([key, k]) => <option key={key} value={key}>{k.label}</option>)}
          </optgroup>
        ))}
      </select>
      <div className="row">
        <span className="chip">Level</span>
        <select className="sel grow" value={level} disabled={!isPlaceable(kind)} onChange={(e) => setLevel(e.target.value)} aria-label="Level">
          {[...levelsByZ(project)].reverse().map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
        <button className="btn acc" onClick={() => ws.actions.setKind(kind, isPlaceable(kind) ? level : null)}>{v.kindSure ? 'Save' : 'Confirm'}</button>
      </div>
    </div>
  );
}

function ScaleAction() {
  const { session, actions } = useWorkspace();
  const S = session.state;
  const cands = session.candidates();
  const [text, setText] = useState<string | null>(null);
  if (!cands.length || !S.d) return <div className="act"><span className="chip">No walls to measure on this sheet. Placing it in the building sets its scale.</span></div>;
  const ref = cands.find((c) => c.id === S.refId) ?? cands[0];
  const chk = session.areaCheck();
  const shown = text ?? session.refLength().toFixed(2);
  const commit = () => { const m = parseLength(shown); if (m > 0) session.setRefLength(m); setText(null); };
  return (
    <div className="act">
      <div className="cands">
        {cands.map((c) => {
          const w = S.d!.walls.find((x) => x.id === c.id)!;
          return <button key={c.id} className={`cand${c.id === S.refId ? ' on' : ''}`} onClick={() => { setText(null); session.setRef(c.id); }}><b>{c.letter}</b>{(wallPx(w) * S.scale).toFixed(1)} m</button>;
        })}
      </div>
      <div className="row">
        <span className="chip">Wall {ref.letter}</span>
        <label className="num"><input value={shown} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit(); }} aria-label={`Length of wall ${ref.letter}`} /><span>m</span></label>
        <button className="btn acc" onClick={() => { actions.confirmScale(parseLength(shown)); setText(null); }}>{S.scaleConfirmed ? 'Update' : 'Confirm'}</button>
      </div>
      <span className="chip">Metres, or feet and inches (12&apos;-6&quot;).</span>
      {chk && <span className={`chip${chk.median < 0.1 ? ' good' : ''}`}>Room areas ±{Math.round(chk.median * 100)}% vs sheet</span>}
    </div>
  );
}

function CheckAction() {
  const { session, actions } = useWorkspace();
  const drawing = useWs((s) => s.drawing);
  const flagged = session.flagged(), done = flagged.filter((w) => session.state.decisions.has(w.id)).length, rm = session.removed().size;
  return (
    <div className="act">
      <div className="legend"><span><i style={{ background: 'var(--flag)' }} />Check</span><span><i style={{ background: 'var(--ok)' }} />Wall</span><span><i style={{ background: 'var(--ghost)' }} />Not a wall</span></div>
      <span className="chip">Click a wall to remove it, again to keep it.</span>
      <div className="row">
        <span className="chip">{done}/{flagged.length} checked{rm ? ` · ${rm} removed` : ''}</span><span className="grow" />
        <button className={`btn sm${drawing ? ' on' : ''}`} onClick={() => actions.setDrawing(!drawing)}><Icon name="pen" />Draw</button>
        <button className="btn sm primary" onClick={actions.finishWalls}>Done</button>
      </div>
    </div>
  );
}

function PlaceAction() {
  const ws = useWorkspace();
  const placing = useWs((s) => s.placing), v = ws.read.viewOf(useWs((s) => s.activeId))!;
  if (!placing) {
    const on = isPlaced(v);
    return (
      <div className="act">
        <span className="chip">{v.kind === 'elevation'
          ? 'Click the two ends of the ground line on this elevation, then the two matching building corners on the base plan.'
          : 'Click two points on this sheet, then the same two on the base plan. Building corners or grid crossings work best.'}</span>
        <div className="row">
          <span className="grow" />
          {on ? <button className="btn sm" onClick={() => ws.actions.takeOut(v.drawing_id)}>Take out</button>
            : <button className="btn sm" onClick={() => ws.actions.setOpen('model')}>Place later</button>}
          <button className="btn sm acc" onClick={ws.actions.startPlacing}>{on ? 'Place again' : 'Start'}</button>
        </div>
      </div>
    );
  }
  const dots = (pts: unknown[]) => [0, 1].map((i) => <span key={i} className={`pt${pts[i] ? ' on' : ''}`}>{i + 1}</span>);
  const tip = v.kind === 'elevation' ? 'The ends of the ground line, then the matching building corners.' : 'Building corners or grid crossings work best.';
  const hint = placing.step === 'sheet' ? `Click point ${placing.sheet.length + 1} on this sheet (left). ${tip}`
    : placing.step === 'base' ? `Now click the same point ${placing.base.length + 1} on the base sheet (right).` : 'Seen from above, at half opacity over the building. Does it line up?';
  return (
    <div className="act">
      <div className="row"><span className="pts">This sheet {dots(placing.sheet)}</span><span className="pts" style={{ marginLeft: 12 }}>Base {dots(placing.base)}</span></div>
      <span className="chip">{hint}</span>
      <div className="row">
        <span className="grow" />
        <button className="btn sm" onClick={ws.actions.cancelPlacing}>Cancel</button>
        <button className="btn sm" onClick={ws.actions.redoPlacing}>Start over</button>
        {placing.step === 'check' && <button className="btn sm acc" onClick={ws.actions.confirmPlacing}>Looks right</button>}
      </div>
    </div>
  );
}

function ModelAction() {
  const { actions } = useWorkspace();
  return (
    <div className="act">
      <div className="row">
        {['IFC', 'OBJ', 'JSON'].map((x) => <button key={x} className="btn sm" onClick={() => toast(`${x} export connects to the parser next`)}>{x}</button>)}
        <span className="grow" />
        <button className="btn sm primary" onClick={() => actions.setTab('ask')}>Ask<Icon name="arrow" /></button>
      </div>
    </div>
  );
}

'use client';
import { useEffect, useRef } from 'react';
import { Icon } from '@/components/Icon';
import { isPlaced, kindOf } from '@/lib/project';
import { createPlan } from '@/lib/plan2d';
import { createViewer } from '@/lib/viewer3d';
import type { Selection } from '@/lib/session';
import { FilePick } from './FilePick';
import { useSessionTick, useWorkspace, useWs } from './context';

/** The 2D sheet and the 3D building, kept in sync with the active sheet's Build state. */
export function Views() {
  const ws = useWorkspace();
  const planEl = useRef<HTMLDivElement>(null), modelEl = useRef<HTMLDivElement>(null), baseEl = useRef<HTMLDivElement>(null), tipEl = useRef<HTMLDivElement>(null);
  const viewMode = useWs((s) => s.viewMode);

  useEffect(() => {
    const { session, actions } = ws;
    const plan = createPlan(planEl.current!), viewer = createViewer(modelEl.current!), basePlan = createPlan(baseEl.current!);
    const unsubscribe = session.subscribe((type) => {
      const s = session.state;
      if (!s.d) return;
      if (type === 'load') { plan.load(s.d, session.viewState()); viewer.load(s.d, session.viewState()); }
      else if (type === 'select') { plan.highlight(s.selected); viewer.highlight(s.selected); }
      else { plan.update(session.viewState()); viewer.update(session.viewState()); }
    });
    const tip = tipEl.current!;
    const hover = (sel: Selection, e: PointerEvent) => {
      const text = sel ? session.describe(sel) : '';
      tip.hidden = !text;
      if (!text) return;
      tip.textContent = text;
      tip.style.left = `${e.clientX + 14}px`;
      tip.style.top = `${e.clientY + 14}px`;
    };
    plan.onHover(hover);
    viewer.onHover(hover);
    plan.onPick(actions.pick);
    viewer.onPick(actions.pick);
    plan.onDraw((a, b) => session.addWall(a, b));
    plan.onDrawBox((box) => session.addBox(box));
    plan.onResize((id, box) => session.resizeBox(id, box));
    actions.attachViews({ plan, viewer, basePlan });
    return () => {
      unsubscribe();
      actions.detachViews();
      plan.dispose(); viewer.dispose(); basePlan.dispose();
    };
  }, [ws]);

  return (
    <div className={`views ${viewMode === 'both' ? '' : viewMode}`}>
      <div className="v plan">
        <div className="host" ref={planEl} />
        <GhostCard />
        <Note2D />
      </div>
      <div className="v model">
        <div className="host" ref={modelEl} />
        <BaseSheet hostRef={baseEl} />
        <Note3D />
      </div>
      <div className="tip" ref={tipEl} hidden />
    </div>
  );
}

/** The base sheet, shown in the 3D panel while the active sheet's two points are matched to it. */
function BaseSheet({ hostRef }: { hostRef: React.RefObject<HTMLDivElement | null> }) {
  const placing = useWs((s) => s.placing);
  return <div className="base2d" ref={hostRef} hidden={!placing || placing.step === 'check'} />;
}

function GhostCard() {
  const ws = useWorkspace();
  const ghostSel = useWs((s) => s.ghostSel);
  useWs((s) => s.project);
  const g = ghostSel ? ws.read.ghosts().find((x) => x.id === ghostSel) : null;
  if (!g) return null;
  const why = {
    foundation: 'Questions about footings need it. The dashed footings in 3D are guessed from the walls above.',
    level: 'Questions about this floor need it. The dashed box in 3D marks where it would go.',
    roof: 'Questions about the roof need it. The dashed plate in 3D marks where it would go.',
    plumbing: 'Questions about pipes, drains and fixtures need it. The dashed outline in 3D marks the level.',
    structure: 'Questions about slabs, beams and columns need it. The dashed plate in 3D marks the level.',
  }[g.kind];
  return (
    <div className="ghostcard">
      <div>
        <b>No {g.noun} for {ws.read.levelName(g.level).toLowerCase()} yet</b>{why}<br />
        <FilePick className="btn sm acc" onFile={(f) => ws.actions.addSheet(f, g)}><Icon name="up" />Add {g.noun}</FilePick>
      </div>
    </div>
  );
}

function Note2D() {
  const placing = useWs((s) => s.placing);
  const ws=useWorkspace();useSessionTick();
  const src=ws.session.state.d?.source;
  if(!placing && src?.image_registration?.resized){const reg=src.image_registration;return <div className="vnote">Working image {reg.sheet_size.map(n=>n.toLocaleString()).join(' × ')} · full-resolution original preserved</div>;}
  if(!placing && src?.limitations?.length)return <div className="vnote"><details><summary>{src.rendering_scope ? 'Generated room-footprint preview' : 'Source document'}{src.source_page?` · page ${src.source_page}`:''}</summary>{src.limitations.map((text,i)=><p key={i}>{text}</p>)}</details></div>;
  if (placing?.step !== 'sheet') return null;
  return <div className="vnote place"><b>This sheet</b> · click point {placing.sheet.length + 1}: a building corner or grid crossing</div>;
}

function Note3D() {
  const ws = useWorkspace();
  const placing = useWs((s) => s.placing), activeId = useWs((s) => s.activeId), project = useWs((s) => s.project), status = useWs((s) => s.status);
  useSessionTick();
  if (status === 'loading') return <div className="vnote"><span className="spin" />Loading the {project?.single === false ? 'building' : 'model'}…</div>;
  if (!project || !activeId) return null;
  const v = ws.read.viewOf(activeId), baseId = ws.read.baseId(), baseLabel = ws.read.sheetLabel(baseId);
  let text: React.ReactNode = null, place = false;
  if (placing?.step === 'base') { text = <><b>Base · {baseLabel}</b> · click point {placing.base.length + 1}, the same spot as on this sheet</>; place = true; }
  else if (placing?.step === 'sheet') { text = <><b>Base · {baseLabel}</b> · first the two points on this sheet</>; place = true; }
  else if (placing?.step === 'check') { text = 'Seen from above: does the new sheet line up?'; place = true; }
  else if (!baseId && !project.single) text = 'Add a floor plan to start the building';
  else if (v?.placement.type === 'attached') text = `${kindOf(v.kind).label} sheets are linked to the building, not placed in 3D`;
  else if (v && !isPlaced(v)) text = 'Not placed yet · use Place in the sheet list or in Build';
  else if(v?.kind==='floor_plan') {
    const windows=ws.session.windowSummary(), plural=(n:number,word:string)=>`${n} ${word}${n===1?'':'s'}`;
    const parts=[windows.placed ? plural(windows.placed,windows.preview?'window':'verified window') : '',
      windows.detectedWindows ? plural(windows.detectedWindows,'detected window') : '',
      windows.detectedDoors ? plural(windows.detectedDoors,'detected door') : ''].filter(Boolean);
    if(parts.length || windows.unplaced)text=<>
      {windows.preview?'Preview · ':''}{parts.join(' · ')}{parts.length ? ' · height estimated' : ''}
      {windows.unplaced>0 && <><br />{windows.unplaced} label{windows.unplaced===1?' needs':'s need'} a wall</>}
    </>;
  }
  return text ? <div className={`vnote${place ? ' place' : ''}`}>{text}</div> : null;
}

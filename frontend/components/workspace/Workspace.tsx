'use client';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useStore } from 'zustand';
import { Icon } from '@/components/Icon';
import { TopBar } from '@/components/TopBar';
import { kindOf } from '@/lib/project';
import { createWorkspace, type Workspace as WorkspaceApi } from '@/lib/workspace';
import { AskPanel } from './AskPanel';
import { BuildFlow } from './BuildFlow';
import { FilePick } from './FilePick';
import { SheetRail } from './SheetRail';
import { TraceDock } from './TraceDock';
import { Views } from './Views';
import { WorkspaceContext, useSessionTick, useWorkspace, useWs } from './context';
import './views.css';
import './workspace.css';

type Kind = 'building' | 'drawing';

function SourceFileType({ file }: { file?: string }) {
  const format = file?.match(/\.(pdf|ifc|svg|dxf|png|jpe?g|webp)$/i)?.[1].toUpperCase();
  if (!format) return null;
  return <span className="source-file-type" title={`Original file: ${file}`}><span className="sr-only">File type: </span>{format}</span>;
}

/** A building (several sheets assembled in 3D) or a single drawing, with Build and Ask on the right. */
export function Workspace({ kind }: { kind: Kind }) {
  const router = useRouter();
  const id = useSearchParams().get('id');
  const [ws] = useState<WorkspaceApi>(() => createWorkspace({ navigate: (url) => router.push(url) }));
  useEffect(() => { if (id) ws.actions.init(kind, id); }, [ws, kind, id]);
  useEffect(() => () => ws.actions.dispose(), [ws]);
  const title = useStore(ws.store, (s) => s.project?.title);
  useEffect(() => { if (title) document.title = `${title} · Ymir`; }, [title]);

  return (
    <WorkspaceContext.Provider value={ws}>
      <div className={`ws ${kind === 'building' ? 'project' : 'drawing'}`}>
        <WorkspaceTopBar kind={kind} />
        {kind === 'building' && <SheetRail />}
        <main>
          <ViewsBar kind={kind} />
          <Views />
          <TraceDock />
        </main>
        <Panel kind={kind} />
        <NotFound kind={kind} hasId={!!id} />
      </div>
    </WorkspaceContext.Provider>
  );
}

function WorkspaceTopBar({ kind }: { kind: Kind }) {
  const ws = useWorkspace();
  const title = useWs((s) => s.project?.title ?? ''), ready = useWs((s) => s.status === 'ready');
  return (
    <TopBar crumbs={<><Link href="/home">Home</Link><span className="sep">/</span><span className="kind-tag">{kind === 'building' ? 'Building' : 'Drawing'}</span><b>{title}</b></>}>
      {kind === 'building' ? <>
        <button className="tb-btn" onClick={ws.actions.addLevel} disabled={!ready} title="Add a storey on top"><Icon name="plus" /><span className="lbl">Add level</span></button>
        <FilePick className="tb-btn primary" disabled={!ready} title="Add a sheet to this building" onFile={(f) => ws.actions.addSheet(f, null)}><Icon name="up" /><span className="lbl">Add sheet</span></FilePick>
      </> : (
        <button className="tb-btn primary" onClick={ws.actions.toBuilding} disabled={!ready} title="Start a building with this drawing as its ground floor"><Icon name="layers" /><span className="lbl">Start a building from this</span></button>
      )}
    </TopBar>
  );
}

function ViewsBar({ kind }: { kind: Kind }) {
  const ws = useWorkspace();
  useSessionTick();
  const viewMode = useWs((s) => s.viewMode), explode = useWs((s) => s.explode), isolate = useWs((s) => s.isolate), drawing = useWs((s) => s.drawing);
  const activeId = useWs((s) => s.activeId);
  const isFloorPlan = useWs((s) => ws.read.viewOf(s.activeId)?.kind === 'floor_plan');
  const modes = [['one-plan', 'Drawing', 'plan'], ['both', 'Both', null], ['one-model', '3D', 'cube']] as const;
  return (
    <div className="bar">
      <div className="seg" role="group" aria-label="Views">
        {modes.map(([m, label, icon]) => (
          <button key={m} className={viewMode === m ? 'on' : ''} onClick={() => ws.actions.setViewMode(m)}>{icon && <Icon name={icon} />}{label}</button>
        ))}
      </div>
      <div className="spacer" />
      <label className="hgt">Height <input key={activeId} defaultValue={ws.session.state.height} onChange={(e) => ws.actions.setHeight(parseFloat(e.target.value))} aria-label="Wall height in metres" /> m</label>
      {kind === 'building' && <>
        <button className={`iconbtn${isolate ? ' on' : ''}`} onClick={ws.actions.toggleIsolate} title="Show only this sheet’s level" aria-pressed={isolate}><Icon name="focus" /></button>
        <button className={`iconbtn${explode ? ' on' : ''}`} onClick={ws.actions.toggleExplode} title="Spread levels apart" aria-pressed={explode}><Icon name="layers" /></button>
      </>}
      {isFloorPlan && <button className={`iconbtn${drawing ? ' on' : ''}`} onClick={() => ws.actions.setDrawing(!drawing)} title="Draw a missing wall" aria-pressed={drawing}><Icon name="pen" /></button>}
      <button className="iconbtn" onClick={ws.actions.fit} title="Fit"><Icon name="fit" /></button>
    </div>
  );
}

function Panel({ kind }: { kind: Kind }) {
  const ws = useWorkspace();
  const tab = useWs((s) => s.tab), askDot = useWs((s) => s.askDot);
  return (
    <aside className="panel">
      {kind === 'drawing' ? <DrawingCard /> : <SheetHead />}
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'build'} className={tab === 'build' ? 'on' : ''} onClick={() => ws.actions.setTab('build')}><Icon name="cube" />Build</button>
        <button role="tab" aria-selected={tab === 'ask'} className={tab === 'ask' ? 'on' : ''} onClick={() => ws.actions.setTab('ask')}><Icon name="chat" />Ask{askDot && <span className="dot" />}</button>
      </div>
      {tab === 'build' ? <BuildFlow /> : <AskPanel />}
    </aside>
  );
}

function DrawingCard() {
  const { session } = useWorkspace();
  useSessionTick();
  const S = session.state, d = S.d;
  if (!d) return <div className="src"><div>Opening…</div></div>;
  const scale = `${(S.scale * 100).toFixed(2)} cm/px · ${S.scaleConfirmed ? 'confirmed' : d.scale.source === 'model' ? 'from model' : 'estimate'}`;
  return (
    <>
      <div className="src">
        {/* eslint-disable-next-line @next/next/no-img-element -- drawings come from the API, any size */}
        <img src={d.image.url} alt="" />
        <div><span className="src-title" title={d.title}>{d.title}</span><small>{[d.source?.dataset, d.source?.kind].filter(Boolean).join(' · ')}</small></div>
        <SourceFileType file={d.source?.file} />
      </div>
      <div className="dmeta">{[`${d.image.size[0]}×${d.image.size[1]} px`, scale, `${d.walls.length} walls`, `${d.rooms.length} rooms`].map((t) => <span key={t}>{t}</span>)}</div>
    </>
  );
}

function SheetHead() {
  const ws = useWorkspace();
  useSessionTick();
  const activeId = useWs((s) => s.activeId), ghostSel = useWs((s) => s.ghostSel);
  useWs((s) => s.project);
  const v = ws.read.viewOf(activeId);
  const g = ghostSel ? ws.read.ghosts().find((x) => x.id === ghostSel) : null;
  if (g) {
    return (
      <div className="sheethead ghost">
        <small>Missing</small>
        <b>{g.noun.charAt(0).toUpperCase() + g.noun.slice(1)}</b>
        <span>{ws.read.levelName(g.level)} · dashed in 3D</span>
      </div>
    );
  }
  if (!v) return <div className="sheethead"><small>Opening…</small></div>;
  return (
    <div className="sheethead">
      <div className="sheethead-top"><small>Working on</small><SourceFileType file={activeId ? ws.read.drawing(activeId)?.source.file : undefined} /></div>
      <b>{ws.read.sheetLabel(activeId)}</b>
      <span>{kindOf(v.kind).label}{v.placement.level ? ` · ${ws.read.levelName(v.placement.level)}` : ''}</span>
    </div>
  );
}

/** A building or drawing that doesn't exist (or no id at all): say so plainly and offer a way on. */
function NotFound({ kind, hasId }: { kind: Kind; hasId: boolean }) {
  const status = useWs((s) => s.status), error = useWs((s) => s.error);
  if (hasId && status !== 'error') return null;
  return (
    <div className="ws-status" role="alert">
      <div>
        <h1>{hasId ? error : `No ${kind} chosen`}</h1>
        <p>It may have been removed, or started in another browser. The sample building is always here.</p>
        <div className="row" style={{ justifyContent: 'center' }}>
          <Link className="tb-btn primary" href="/building?id=ac20">Open the sample building</Link>
          <Link className="tb-btn" href="/home">Back to home</Link>
        </div>
      </div>
    </div>
  );
}

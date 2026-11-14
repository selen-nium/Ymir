'use client';
import { useEffect, useState } from 'react';
import { Icon } from '@/components/Icon';
import { ghostSlots, isPlaceable, isPlaced, kindOf, levelsByZ, views, type Ghost, type SheetView } from '@/lib/project';
import { FilePick } from './FilePick';
import { useWorkspace, useWs } from './context';

/** A building's sheets by level, with the parts no sheet covers (ghosts). */
export function SheetRail() {
  const ws = useWorkspace();
  const project = useWs((s) => s.project), activeId = useWs((s) => s.activeId), ghostSel = useWs((s) => s.ghostSel), hideGhosts = useWs((s) => s.hideGhosts);
  if (!project) return <nav className="rail" />;

  const vs = views(project).filter((v) => v.index === 0);
  const allGhosts = ghostSlots(project), ghosts = hideGhosts ? [] : allGhosts;
  const expected = ghostSlots({ ...project, sheets: [] }).length;
  const cover = expected ? 1 - allGhosts.length / expected : 1;
  const base = ws.read.drawing(ws.read.baseId() ?? project.sheets[0].drawing_id);
  const placed = vs.filter(isPlaced).length;
  const levelOf = (id?: string | null) => project.levels.find((l) => l.id === id);
  const sheetRows = (list: SheetView[]) => list.map((v) => <SheetRow key={v.drawing_id} v={v} on={v.drawing_id === activeId && !ghostSel} />);
  const ghostRow = (g: Ghost) => (
    <div key={g.id} className={`trow ghost${g.id === ghostSel ? ' on' : ''}`}>
      <button className="pick" onClick={() => ws.actions.selectGhost(g.id)}><span className="dot ghost" /><span className="lab">{g.label}</span></button>
      <small>missing</small>
    </div>
  );
  const chip = (g: Ghost) => (
    <button key={g.id} className={`chip-g${g.id === ghostSel ? ' on' : ''}`} title={`No ${g.noun} for this level`} onClick={() => ws.actions.selectGhost(g.id)}>+ {g.label}</button>
  );
  const top = ghosts.find((g) => g.onTop);
  const loose = vs.filter((v) => v.placement.type !== 'attached' && !levelOf(v.placement.level));
  const attached = vs.filter((v) => v.placement.type === 'attached');

  return (
    <nav className="rail" aria-label="Sheets">
      <div className="src">
        {/* eslint-disable-next-line @next/next/no-img-element -- drawings come from the API, any size */}
        {base && <img src={base.image.url} alt="" />}
        <div>{project.title}<small>{project.sheets.length} sheet{project.sheets.length === 1 ? '' : 's'} · {placed} in 3D · {project.levels.length} levels</small></div>
      </div>
      <div className="cover" title="Share of the building's expected plans that have a sheet">
        <div className="meter"><i style={{ width: `${Math.round(cover * 100)}%` }} /></div>
        <small>{Math.round(cover * 100)}% of the building has a build plan · {allGhosts.length} missing</small>
      </div>
      <div className="rail-h"><span>Sheets</span><button onClick={ws.actions.toggleHideGhosts}>{hideGhosts ? 'Show missing' : 'Hide missing'}</button></div>
      <div className="tree">
        {top && <><div className="lvl-h"><span>Roof</span></div>{ghostRow(top)}</>}
        {[...levelsByZ(project)].reverse().map((lv) => {
          const here = vs.filter((v) => v.placement.type !== 'attached' && v.placement.level === lv.id);
          const gs = ghosts.filter((g) => g.level === lv.id && !g.onTop);
          const small = gs.filter((g) => g.kind === 'structure' || g.kind === 'plumbing');
          return (
            <div key={lv.id}>
              <div className="lvl-h"><span>{lv.name}</span>{small.map(chip)}</div>
              {sheetRows(here)}
              {gs.filter((g) => !small.includes(g)).map(ghostRow)}
            </div>
          );
        })}
        {loose.length > 0 && <><div className="lvl-h"><span>To place</span></div>{sheetRows(loose)}</>}
        {attached.length > 0 && <><div className="lvl-h"><span>Not in 3D</span></div>{sheetRows(attached)}</>}
        <FilePick className="addrow" onFile={(f) => ws.actions.addSheet(f, null)}><Icon name="plus" />Add a sheet to this building</FilePick>
      </div>
    </nav>
  );
}

function SheetRow({ v, on }: { v: SheetView; on: boolean }) {
  const ws = useWorkspace();
  const [menu, setMenu] = useState(false);
  const status = v.placement.type === 'attached' ? 'attached' : isPlaced(v) ? 'placed' : 'pending';
  const label = ws.read.sheetLabel(v.drawing_id);
  const canPlace = isPlaceable(v.kind) && ws.read.baseId() !== v.drawing_id;
  const place = async () => {
    setMenu(false);
    await ws.actions.selectSheet(v.drawing_id);
    ws.actions.setTab('build');
    ws.actions.startPlacing();
  };
  const remove = () => {
    setMenu(false);
    if (confirm(`Remove “${label}” from this building? The drawing itself stays in your uploads.`)) ws.actions.removeSheet(v.drawing_id);
  };
  // the menu closes on a click elsewhere or Escape
  useEffect(() => {
    if (!menu) return;
    const close = (e: Event) => { if (e instanceof KeyboardEvent ? e.key === 'Escape' : true) setMenu(false); };
    window.addEventListener('pointerdown', close);
    window.addEventListener('keydown', close);
    return () => { window.removeEventListener('pointerdown', close); window.removeEventListener('keydown', close); };
  }, [menu]);
  return (
    <div className={`trow${on ? ' on' : ''}${menu ? ' menu-open' : ''}`}>
      <button className="pick" title={label} onClick={() => ws.actions.selectSheet(v.drawing_id)}>
        <span className={`dot ${status}`} /><span className="lab">{label}</span>
      </button>
      {status === 'pending' && canPlace
        ? <button className="placebtn" onClick={place}>Place</button>
        : <small>{kindOf(v.kind).label}</small>}
      <span className="more" onPointerDown={(e) => e.stopPropagation()}>
        <button aria-label={`More for ${label}`} aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)}>⋯</button>
        {menu && (
          <span className="menu" role="menu">
            {canPlace && <button role="menuitem" onClick={place}><Icon name="target" />{status === 'placed' ? 'Place again' : 'Place in building'}</button>}
            <button role="menuitem" className="rm" onClick={remove}><Icon name="trash" />Remove from building</button>
          </span>
        )}
      </span>
    </div>
  );
}

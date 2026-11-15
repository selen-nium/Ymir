'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Icon, type IconName } from '@/components/Icon';
import { TopBar } from '@/components/TopBar';
import { FilePick } from '@/components/workspace/FilePick';
import { deleteDrawing, loadDrawing, loadLibrary, loadProjects, uploadDrawing } from '@/lib/api';
import {
  createLocalProject, forgetDrawing, hiddenItems, localProjects, persistVersion, recent, removeLocalProject, setHidden, subscribePersist,
  threadSummary, timeAgo,
} from '@/lib/persist';
import { guessKind } from '@/lib/project';
import { toast } from '@/lib/toast';
import type { LibraryEntry, ProjectEntry } from '@/lib/types';
import s from './home.module.css';

interface CardData {
  key: string;
  href: string;
  img?: string | null;
  title: string;
  meta: string;
  badge: 'Upload' | 'Yours' | 'Sample' | 'Hidden';
  mine: boolean;
  threadId: string;
  action: { icon: IconName; label: string; confirm?: string; run: () => void | Promise<void> };
}
interface Section { cards: CardData[]; hidden: number }

/** Home: uploads and buildings (yours first, then the samples), with their conversations. Personal data lives in this browser. */
export function HomeView() {
  const router = useRouter();
  const [lib, setLib] = useState<LibraryEntry[] | null>(null);
  const [samples, setSamples] = useState<ProjectEntry[]>([]);
  const [showHidden, setShowHidden] = useState<{ uploads: boolean; buildings: boolean }>({ uploads: false, buildings: false });
  const [q, setQ] = useState('');
  const [uploading, setUploading] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);

  // What this browser keeps (your buildings, hidden samples, conversations); empty while rendering on the server.
  const stored = useSyncExternalStore(subscribePersist, persistVersion, () => -1);
  const { locals, hidden, opened } = useMemo(() => (stored < 0
    ? { locals: [], hidden: [], opened: new Map<string, number>() }
    : { locals: localProjects(), hidden: hiddenItems(), opened: new Map(recent().map((r) => [r.id, r.at ?? 0])) }), [stored]);
  useEffect(() => {
    loadLibrary().then(setLib, () => setLib([]));
    loadProjects().then(setSamples);
  }, []);

  const upload = async (file: File) => {
    setUploading(true);
    toast('Reading the drawing…');
    try {
      const entry = await uploadDrawing(file);
      router.push(`/drawing?id=${encodeURIComponent(entry.id)}`);
    } catch (e) {
      toast((e as Error).message || 'This drawing could not be read');
      setUploading(false);
    }
  };

  const { uploads, buildings } = useMemo(() => {
    const match = (...t: (string | undefined)[]) => !q || t.join(' ').toLowerCase().includes(q.toLowerCase());
    const active = (threadId: string, id: string) => Math.max(threadSummary(threadId)?.at ?? 0, opened.get(id) ?? 0);
    const isHidden = (key: string) => hidden.includes(key);
    const hideAction = (key: string, title: string): CardData['action'] => isHidden(key)
      ? { icon: 'eye', label: `Show “${title}” again`, run: () => setHidden(key, false) }
      : { icon: 'eyeOff', label: `Hide “${title}”`, run: () => { setHidden(key, true); toast(`${title} hidden`, { label: 'Undo', run: () => setHidden(key, false) }); } };
    // yours first (most recently used), then the samples (most recently used); hidden samples only on request
    const order = (a: CardData & { at: number }, b: CardData & { at: number }) => Number(b.mine) - Number(a.mine) || b.at - a.at;
    const section = (all: (CardData & { at: number })[], show: boolean): Section => ({
      cards: all.filter((c) => show || c.badge !== 'Hidden').sort(order),
      hidden: all.filter((c) => c.badge === 'Hidden').length,
    });

    const drawings = (lib ?? []).filter((d) => match(d.title, d.source.dataset, d.source.kind)).map((d) => {
      const mine = d.source.dataset === 'Upload', key = `d:${d.id}`;
      return {
        key: d.id, href: `/drawing?id=${encodeURIComponent(d.id)}`, img: d.thumbnail, title: d.title, mine, threadId: key, at: active(key, d.id),
        meta: mine ? d.source.kind : `${d.source.dataset} · ${d.source.kind}`,
        badge: mine ? 'Upload' as const : isHidden(key) ? 'Hidden' as const : 'Sample' as const,
        action: mine
          ? { icon: 'trash' as IconName, label: `Delete “${d.title}”`, confirm: `Delete “${d.title}”? The drawing is removed from the server for everyone, and buildings using it lose that sheet. Its conversation in this browser is cleared.`,
            run: async () => { await deleteDrawing(d.id); forgetDrawing(d.id); setLib((l) => (l ?? []).filter((x) => x.id !== d.id)); toast(`${d.title} deleted`); } }
          : hideAction(key, d.title),
      };
    });
    const builds = [
      ...locals.filter((p) => match(p.title)).map((p) => ({
        key: p.id, href: `/building?id=${encodeURIComponent(p.id)}`, img: p.thumbnail, title: p.title, badge: 'Yours' as const, mine: true, threadId: p.id,
        meta: p.created ? `Started ${timeAgo(p.created)}` : 'Yours', at: active(p.id, p.id) || (p.created ?? 0),
        action: { icon: 'trash' as IconName, label: `Remove “${p.title}”`, confirm: `Remove the building “${p.title}” and its conversation from this browser? Its drawings stay in your uploads.`,
          run: () => { removeLocalProject(p.id); toast(`${p.title} removed`); } },
      })),
      ...samples.filter((p) => match(p.title, p.source?.dataset)).map((p) => ({
        key: p.id, href: `/building?id=${encodeURIComponent(p.id)}`, img: p.thumbnail, title: p.title, mine: false, threadId: p.id,
        badge: isHidden(`p:${p.id}`) ? 'Hidden' as const : 'Sample' as const, meta: `${p.sheets} sheets · ${p.source?.dataset ?? 'Sample'}`, at: active(p.id, p.id),
        action: hideAction(`p:${p.id}`, p.title),
      })),
    ];
    return { uploads: section(drawings, showHidden.uploads), buildings: section(builds, showHidden.buildings) };
    // `stored` re-runs this when a conversation changes (threadSummary reads the browser store)
  }, [lib, samples, locals, hidden, opened, q, showHidden, stored]); // eslint-disable-line react-hooks/exhaustive-deps

  const heading = (title: string, sec: Section, which: 'uploads' | 'buildings') => (
    <div className={s.sh}>
      <h2>{title}</h2>
      {sec.hidden > 0 && (
        <button className={s.link} onClick={() => setShowHidden((v) => ({ ...v, [which]: !v[which] }))}>
          {showHidden[which] ? 'Hide hidden samples' : `Show ${sec.hidden} hidden`}
        </button>
      )}
    </div>
  );
  const noMatch = <p className={s.empty}>No matches for “{q}”.</p>;

  return (
    <>
      <TopBar crumbs={<b>Home</b>}>
        <button className="tb-btn" onClick={() => dialog.current?.showModal()}><Icon name="layers" /><span className="lbl">New building</span></button>
        <FilePick className="tb-btn primary" title="Open a single drawing" onFile={upload} disabled={uploading}><Icon name="up" />{uploading ? 'Reading…' : 'Upload a drawing'}</FilePick>
      </TopBar>
      <div className={s.wrap}>
        <h1 className="sr-only">Home</h1>
        <div className={s.tools}>
          <label className={s.search}><Icon name="search" /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" autoComplete="off" aria-label="Search uploads and buildings" /></label>
        </div>

        <section>
          {heading('Uploads', uploads, 'uploads')}
          <div className={s.grid}>
            {lib === null ? <p className={s.empty}>Loading…</p> : <>
              {uploads.cards.map((c) => <Card key={c.key} c={c} />)}
              {q ? !uploads.cards.length && noMatch
                : <FilePick className={s.new} onFile={upload} disabled={uploading}><Icon name="up" /><b>Upload a drawing</b><span>A floor plan, a foundation or piping plan, an elevation…</span></FilePick>}
            </>}
          </div>
        </section>

        <section>
          {heading('Buildings', buildings, 'buildings')}
          <div className={s.grid}>
            {buildings.cards.map((c) => <Card key={c.key} c={c} />)}
            {q ? !buildings.cards.length && noMatch
              : <button className={s.new} onClick={() => dialog.current?.showModal()}><Icon name="plus" /><b>New building</b><span>Start from your ground floor plan</span></button>}
          </div>
        </section>
        <details className={s.sources}>
          <summary>Sample sources and credits</summary>
          {[...(lib ?? []).filter(d=>d.source.credit),...samples].map(item=><p key={item.id}><b>{item.title}</b> · {item.source.credit}{item.source.licence?` · ${item.source.licence}`:''}</p>)}
        </details>
      </div>
      <NewBuildingDialog ref={dialog} onCreated={(id) => router.push(`/building?id=${encodeURIComponent(id)}`)} />
    </>
  );
}

function Card({ c }: { c: CardData }) {
  const t = threadSummary(c.threadId);
  return (
    <div className={`${s.card}${c.badge === 'Hidden' ? ` ${s.hiddenCard}` : ''}`}>
      <Link href={c.href} className={s.cardLink}>
        {/* eslint-disable-next-line @next/next/no-img-element -- thumbnails come from the API, any size */}
        <div className={s.thumb}>{c.img && <img src={c.img} alt="" loading="lazy" />}</div>
        <span className={`${s.badge}${c.mine ? ` ${s.mine}` : ''}`}>{c.badge}</span>
        <div className={s.body}>
          <b>{c.title}</b>
          <span className={s.meta}>{c.meta}</span>
          {t ? <div className={s.convo}><Icon name="chat" /><span>{t.count} question{t.count === 1 ? '' : 's'} · {timeAgo(t.at)}<q>{t.last}</q></span></div>
            : <div className={`${s.convo} ${s.none}`}><Icon name="chat" /><span>No questions yet</span></div>}
        </div>
      </Link>
      <button className={s.act} title={c.action.label} aria-label={c.action.label}
        onClick={async () => { if (!c.action.confirm || confirm(c.action.confirm)) { try { await c.action.run(); } catch (e) { toast((e as Error).message); } } }}>
        <Icon name={c.action.icon} />
      </button>
    </div>
  );
}

function NewBuildingDialog({ ref, onCreated }: { ref: React.RefObject<HTMLDialogElement | null>; onCreated: (id: string) => void }) {
  const [name, setName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const create = async () => {
    if (!name.trim()) { setErr('Give the building a name.'); return; }
    if (!file) { setErr('Choose its first sheet.'); return; }
    setBusy(true);
    try {
      const entry = await uploadDrawing(file);
      const d = await loadDrawing(entry.id);
      const g = guessKind(d.title, file.name);
      const p = createLocalProject({ title: name.trim(), drawing: d, kind: g.sure ? g.kind : 'floor_plan', kindSure: false });
      onCreated(p.id);
    } catch (e) {
      setErr((e as Error).message || 'This drawing could not be read.');
      setBusy(false);
    }
  };
  return (
    <dialog ref={ref} className={s.dialog} onClose={() => { setErr(''); setBusy(false); }} aria-labelledby="new-building-title">
      <form method="dialog" onSubmit={(e) => { e.preventDefault(); create(); }}>
        <h3 id="new-building-title">New building</h3>
        <p>Start with one sheet, usually the ground floor plan. Add foundation, plumbing and other sheets after.</p>
        <label>Name<input value={name} onChange={(e) => { setName(e.target.value); setErr(''); }} placeholder="Lakeside apartments" autoComplete="off" /></label>
        <div className={s.field}>
          <span>First sheet</span>
          <FilePick className={`${s.drop}${file ? ` ${s.has}` : ''}`} onFile={(f) => { setFile(f); setErr(''); }}>{file ? file.name : 'Choose a PDF, image, IFC or SVG'}</FilePick>
        </div>
        <span className={s.err} role="alert">{err}</span>
        <div className={s.row}>
          <button type="button" className="tb-btn" onClick={() => ref.current?.close()}>Cancel</button>
          <button type="submit" className="tb-btn acc" disabled={busy}>{busy ? 'Reading the drawing…' : 'Create building'}</button>
        </div>
      </form>
    </dialog>
  );
}

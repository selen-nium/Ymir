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
  badge: string;
  mine: boolean;
  threadId: string;
  action: { icon: IconName; label: string; confirm?: string; run: () => void | Promise<void> };
}

/** Home: your uploads, buildings and their conversations. Everything personal lives in this browser. */
export function HomeView() {
  const router = useRouter();
  const [lib, setLib] = useState<LibraryEntry[] | null>(null);
  const [samples, setSamples] = useState<ProjectEntry[]>([]);
  const [showHidden, setShowHidden] = useState(false);
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

  const { uploads, buildings, sampleDrawings, hiddenCount } = useMemo(() => {
    const match = (...t: (string | undefined)[]) => !q || t.join(' ').toLowerCase().includes(q.toLowerCase());
    const active = (threadId: string, id: string) => Math.max(threadSummary(threadId)?.at ?? 0, opened.get(id) ?? 0);
    const isHidden = (key: string) => hidden.includes(key);
    const hide = (key: string, title: string): CardData['action'] => ({ icon: 'eyeOff', label: `Hide “${title}”`, run: () => { setHidden(key, true); toast(`${title} hidden`); } });
    const unhide = (key: string, title: string): CardData['action'] => ({ icon: 'eyeOff', label: `Show “${title}” again`, run: () => setHidden(key, false) });

    const ups: CardData[] = (lib ?? []).filter((d) => d.source.dataset === 'Upload' && match(d.title, d.source.kind))
      .sort((a, b) => active(`d:${b.id}`, b.id) - active(`d:${a.id}`, a.id))
      .map((d) => ({
        key: d.id, href: `/drawing?id=${encodeURIComponent(d.id)}`, img: d.thumbnail, title: d.title, meta: d.source.kind, badge: 'Upload', mine: true, threadId: `d:${d.id}`,
        action: { icon: 'trash', label: `Delete “${d.title}”`, confirm: `Delete “${d.title}”? The drawing is removed from the server for everyone, and buildings using it lose that sheet. Its conversation in this browser is cleared.`,
          run: async () => { await deleteDrawing(d.id); forgetDrawing(d.id); setLib((l) => (l ?? []).filter((x) => x.id !== d.id)); toast(`${d.title} deleted`); } },
      }));
    const bs: CardData[] = [
      ...locals.filter((p) => match(p.title)).map((p) => ({
        key: p.id, href: `/building?id=${encodeURIComponent(p.id)}`, img: p.thumbnail, title: p.title, badge: 'Yours', mine: true, threadId: p.id,
        meta: p.created ? `Started ${timeAgo(p.created)}` : 'Yours', at: active(p.id, p.id) || (p.created ?? 0),
        action: { icon: 'trash' as IconName, label: `Remove “${p.title}”`, confirm: `Remove the building “${p.title}” and its conversation from this browser? Its drawings stay in your uploads.`,
          run: () => { removeLocalProject(p.id); toast(`${p.title} removed`); } },
      })),
      ...samples.filter((p) => match(p.title, p.source?.dataset) && (showHidden || !isHidden(`p:${p.id}`))).map((p) => ({
        key: p.id, href: `/building?id=${encodeURIComponent(p.id)}`, img: p.thumbnail, title: p.title, badge: isHidden(`p:${p.id}`) ? 'Hidden' : 'Sample', mine: false, threadId: p.id,
        meta: `${p.sheets} sheets · ${p.source?.dataset ?? 'Sample'}`, at: active(p.id, p.id),
        action: isHidden(`p:${p.id}`) ? unhide(`p:${p.id}`, p.title) : hide(`p:${p.id}`, p.title),
      })),
    ].sort((a, b) => b.at - a.at);
    const smp: CardData[] = (lib ?? []).filter((d) => d.source.dataset !== 'Upload' && match(d.title, d.source.dataset, d.source.kind) && (showHidden || !isHidden(`d:${d.id}`)))
      .sort((a, b) => active(`d:${b.id}`, b.id) - active(`d:${a.id}`, a.id))
      .map((d) => ({
        key: d.id, href: `/drawing?id=${encodeURIComponent(d.id)}`, img: d.thumbnail, title: d.title, meta: `${d.source.dataset} · ${d.source.kind}`,
        badge: isHidden(`d:${d.id}`) ? 'Hidden' : 'Sample', mine: false, threadId: `d:${d.id}`,
        action: isHidden(`d:${d.id}`) ? unhide(`d:${d.id}`, d.title) : hide(`d:${d.id}`, d.title),
      }));
    const hiddenCount = hidden.filter((k) => (k.startsWith('p:') && samples.some((p) => `p:${p.id}` === k)) || (k.startsWith('d:') && (lib ?? []).some((d) => `d:${d.id}` === k))).length;
    return { uploads: ups, buildings: bs, sampleDrawings: smp, hiddenCount };
    // `stored` re-runs this when a conversation changes (threadSummary reads the browser store)
  }, [lib, samples, locals, hidden, opened, q, showHidden, stored]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <TopBar crumbs={<b>Home</b>}>
        <button className="tb-btn" onClick={() => dialog.current?.showModal()}><Icon name="layers" /><span className="lbl">New building</span></button>
        <FilePick className="tb-btn primary" title="Open a single drawing" onFile={upload}><Icon name="up" />{uploading ? 'Reading…' : 'Upload a drawing'}</FilePick>
      </TopBar>
      <div className={s.wrap}>
        <h1 className="sr-only">Home</h1>
        <div className={s.tools}>
          <label className={s.search}><Icon name="search" /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" autoComplete="off" aria-label="Search" /></label>
          {hiddenCount > 0 && <button className={s.link} onClick={() => setShowHidden((v) => !v)}>{showHidden ? 'Hide hidden samples' : `Show ${hiddenCount} hidden`}</button>}
        </div>

        <section>
          <h2>Uploads</h2>
          <div className={s.grid}>
            {lib === null ? <p className={s.empty}>Loading…</p>
              : uploads.length ? uploads.map((c) => <Card key={c.key} c={c} />)
                : <FilePick className={s.new} onFile={upload}><Icon name="up" /><b>Upload a drawing</b><span>A floor plan, a foundation or piping plan, an elevation…</span></FilePick>}
          </div>
        </section>

        <section>
          <h2>Buildings</h2>
          <div className={s.grid}>
            {buildings.map((c) => <Card key={c.key} c={c} />)}
            <button className={s.new} onClick={() => dialog.current?.showModal()}><Icon name="plus" /><b>New building</b><span>Start from your ground floor plan</span></button>
          </div>
        </section>

        {sampleDrawings.length > 0 && (
          <section>
            <h2>Samples</h2>
            <div className={s.grid}>{sampleDrawings.map((c) => <Card key={c.key} c={c} />)}</div>
          </section>
        )}
      </div>
      <NewBuildingDialog ref={dialog} onCreated={(id) => router.push(`/building?id=${encodeURIComponent(id)}`)} />
    </>
  );
}

function Card({ c }: { c: CardData }) {
  const t = threadSummary(c.threadId);
  return (
    <div className={s.card}>
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
    <dialog ref={ref} className={s.dialog} onClose={() => { setErr(''); setBusy(false); }}>
      <form method="dialog" onSubmit={(e) => { e.preventDefault(); create(); }}>
        <h3>New building</h3>
        <p>Start with one sheet, usually the ground floor plan. Add foundation, plumbing and other sheets after.</p>
        <label>Name<input value={name} onChange={(e) => { setName(e.target.value); setErr(''); }} placeholder="Lakeside apartments" autoComplete="off" /></label>
        <label>First sheet
          <FilePick className={`${s.drop}${file ? ` ${s.has}` : ''}`} onFile={(f) => { setFile(f); setErr(''); }}>{file ? file.name : 'Choose a drawing (PNG, JPEG or WebP)'}</FilePick>
        </label>
        <span className={s.err} role="alert">{err}</span>
        <div className={s.row}>
          <button type="button" className="tb-btn" onClick={() => ref.current?.close()}>Cancel</button>
          <button type="submit" className="tb-btn acc" disabled={busy}>{busy ? 'Reading the drawing…' : 'Create building'}</button>
        </div>
      </form>
    </dialog>
  );
}

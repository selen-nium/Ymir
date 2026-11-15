import s from './landing.module.css';

const box = (x: number, y: number, w: number, h: number, title: string, sub: string, tone?: 'acc' | 'blue' | 'green') => (
  <g key={`${title}${x}${y}`}>
    <rect className={`${s.dgBox}${tone ? ` ${s[`dg_${tone}`]}` : ''}`} x={x} y={y} width={w} height={h} rx={10} />
    <text className={s.dgT} x={x + 14} y={y + h / 2 - 3}>{title}</text>
    <text className={s.dgS} x={x + 14} y={y + h / 2 + 14}>{sub}</text>
  </g>
);
const TOOLS = [
  ['VISTA', 'source images · crops'],
  ['D-FINE-L / Kimi', 'Modal detector · Verda fallback'],
  ['Native PDF text', 'positioned text · when available'],
  ['Room outlines', 'native IFC/SVG · Raster2Seq on Modal'],
  ['Measurements', 'source calibration'],
];

/** Import/Build and Lens use preserved sources, with separate model and QA outputs. */
export function ArchitectureDiagram() {
  return (
    <svg viewBox="0 0 1160 710" role="img" aria-labelledby="architecture-title architecture-description">
      <title id="architecture-title">Ymir architecture</title>
      <desc id="architecture-description">The planned Vercel frontend calls a FastAPI backend on Modal. Import preserves originals and canonical sheets. Build uses WallNet or OpenCV to find walls for the model. Ask snapshots one to three sheets in a durable, single-worker queue. DeepSeek calls VISTA, D-FINE-L for trained object classes, Kimi for other classes, native PDF text, native or Raster2Seq room geometry, and calibrated measurement tools. The host validates proposal IDs and polygons, computes counts and measurements, and streams evidence or a needs_review result. SQLite retains jobs, events, reviews and the budget. Shapely supports polygon checks and area calculations.</desc>
      <defs>
        <marker id="architecture-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#b9b2a4" /></marker>
        <marker id="architecture-flow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#2563eb" /></marker>
      </defs>
      <rect className={s.dgZone} x={10} y={20} width={216} height={644} rx={14} />
      <text className={s.dgZl} x={28} y={44}>NEXT.JS · VERCEL</text>
      <rect className={s.dgZone} x={256} y={20} width={894} height={644} rx={14} />
      <text className={s.dgZl} x={274} y={44}>API · FASTAPI ON MODAL</text>
      {box(28, 70, 180, 64, 'Ymir app', 'Home · building · drawing', 'acc')}
      {box(28, 168, 180, 56, '2D sheet', 'source pixels · citations')}
      {box(28, 244, 180, 56, '3D view', 'placed sheets · evidence')}
      {box(28, 320, 180, 56, 'Trace & job controls', 'progress · cancel · reconnect')}
      {box(28, 406, 180, 64, 'Browser state', 'buildings · threads · placement')}

      <text className={s.dgZl} x={276} y={74}>IMPORT & BUILD</text>
      {box(276, 86, 220, 64, 'Import sources', 'PDF pages · images · IFC/SVG')}
      {box(526, 86, 260, 64, 'Build model', 'WallNet / OpenCV · wall geometry')}
      {box(826, 86, 300, 64, 'Drawing store', 'originals · hashes · canonical sheets')}
      <path className={s.dgLine} d="M496 118h30M786 118h40" markerEnd="url(#architecture-arrow)" />
      <path className={s.dgLine} d="M976 150v30H386v44" markerEnd="url(#architecture-arrow)" />

      <text className={s.dgZl} x={276} y={210}>ASK · YMIR</text>
      {box(276, 224, 220, 64, 'Durable job queue', '1–3 sheets · source snapshot')}
      {box(526, 224, 260, 64, 'DeepSeek on Verda', 'six prior turns · selected sources', 'blue')}
      {box(276, 330, 220, 64, 'SQLite', 'jobs · events · reviews · budget')}
      <text className={s.dgS} x={290} y={420}>One inference worker</text>
      <text className={s.dgS} x={290} y={440}>Reservations before paid calls</text>
      <text className={s.dgS} x={290} y={460}>Interrupted jobs stay stopped</text>
      <path className={s.dgLine} d="M496 256h30M386 288v42" markerEnd="url(#architecture-arrow)" />
      {TOOLS.map(([title, detail], i) => box(826, 224 + i * 62, 300, 52, title, detail))}
      <path className={s.dgLine} d="M786 256h20M806 250v248M806 250h20M806 312h20M806 374h20M806 436h20M806 498h20" />
      <text className={s.dgS} x={548} y={350}>Tool evidence</text>
      <text className={s.dgS} x={548} y={370}>and answer JSON</text>
      <path className={s.dgLine} d="M656 288v282" markerEnd="url(#architecture-arrow)" />
      {box(526, 570, 260, 64, 'Host checks & calculations', 'proposal IDs · counts · polygon checks', 'blue')}
      {box(826, 570, 300, 64, 'Answer & review', 'citations · needs_review · human records', 'green')}
      <path className={s.dgLine} d="M786 602h40" markerEnd="url(#architecture-arrow)" />
      <path className={s.dgLine} d="M1126 250h12v300H656v20M1126 312h12M1126 374h12M1126 436h12M1126 498h12" markerEnd="url(#architecture-arrow)" />

      <path className={s.dgFlow} d="M208 94C242 94 242 118 276 118" markerEnd="url(#architecture-flow)" />
      <path className={s.dgFlow} d="M208 120C240 120 240 256 276 256" markerEnd="url(#architecture-flow)" />
      <path className={s.dgFlow} d="M976 634v18H240V348h-32" markerEnd="url(#architecture-flow)" />
      <text className={s.dgS} x={534} y={690}>NDJSON · progress and answers · reconnect by job ID</text>
    </svg>
  );
}

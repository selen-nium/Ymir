import s from './landing.module.css';

const box = (x: number, y: number, w: number, h: number, title: string, sub: string, tone?: 'acc' | 'blue' | 'green') => (
  <g key={`${title}${x}${y}`}>
    <rect className={`${s.dgBox}${tone ? ` ${s[`dg_${tone}`]}` : ''}`} x={x} y={y} width={w} height={h} rx={10} />
    <text className={s.dgT} x={x + 14} y={y + h / 2 - 3}>{title}</text>
    <text className={s.dgS} x={x + 14} y={y + h / 2 + 14}>{sub}</text>
  </g>
);
const TOOLS = [
  ['VISTA', 'source crops · pixel reads'],
  ['D-FINE-L / Argon', '14 trained classes / other classes'],
  ['Native PDF', 'positioned text · registered vectors'],
  ['Room outlines', 'IFC / SVG / DXF · Raster2Seq'],
  ['Measurements', 'source calibration · areas, distances'],
];

/** Preserved sources feed Build and Ask; tools return evidence to the bounded loop. */
export function ArchitectureDiagram() {
  return (
    <svg viewBox="0 0 1160 860" role="img" aria-labelledby="architecture-title architecture-description">
      <title id="architecture-title">Ymir architecture</title>
      <desc id="architecture-description">The Next.js frontend on Vercel calls FastAPI on Modal. Import preserves original PDF, image, IFC, SVG and DXF sources and prepares selected sheets. Build uses a working copy and the Stoa wall converter for the 3D model. Ask snapshots one to three sheets in a durable single-worker queue. The host binds eligible one-page counts to the compact counter; other questions use the general harness. Independently, Smart selects DeepSeek and Frontier selects Sol on OpenRouter. The model requests available tools and receives their evidence in a bounded loop. D-FINE-L runs on Modal GPU for fourteen trained classes; Argon on OpenRouter handles other classes. VISTA reads source pixels. Native PDF text and vectors, native room geometry, Raster2Seq on Modal GPU and calibrated measurements are available according to the harness and source. Host checks validate proposal IDs and polygons and derive counts and measurements. The answer includes citations and any review groups. Optional box review happens in the browser and saves a separate revision. SQLite retains jobs, events and receipts; source artifacts and saved labels persist on the Modal volume. Progress and answers stream over NDJSON and reconnect by job ID. Old receipts are replayed without rewriting them.</desc>
      <defs>
        <marker id="architecture-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#b9b2a4" /></marker>
        <marker id="architecture-flow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#2563eb" /></marker>
      </defs>
      <rect className={s.dgZone} x={10} y={20} width={216} height={780} rx={14} />
      <text className={s.dgZl} x={28} y={44}>NEXT.JS · VERCEL</text>
      <rect className={s.dgZone} x={256} y={20} width={894} height={780} rx={14} />
      <text className={s.dgZl} x={274} y={44}>FASTAPI · MODAL CPU · DURABLE DATA VOLUME</text>
      {box(28, 70, 180, 64, 'Ymir app', 'question · 1–3 selected sheets', 'acc')}
      {box(28, 168, 180, 56, '2D sheet', 'source pixels · citations')}
      {box(28, 244, 180, 56, '3D view', 'placed sheets · evidence')}
      {box(28, 320, 180, 56, 'Trace & job controls', 'progress · cancel · reconnect')}
      {box(28, 406, 180, 64, 'Browser state', 'threads · placement · modes')}
      {box(28, 560, 180, 64, 'Check boxes · optional', 'keep · resize · remove · draw', 'acc')}

      <text className={s.dgZl} x={276} y={74}>IMPORT & BUILD</text>
      {box(276, 86, 220, 64, 'Import sources', 'PDF · images · IFC / SVG / DXF')}
      {box(526, 86, 260, 64, 'Preserved sources & sheets', 'originals · hashes · selected geometry')}
      {box(826, 86, 300, 64, 'Build model', 'working copy · cleanup · Stoa wall converter')}
      <path className={s.dgLine} d="M496 118h30M786 118h40" markerEnd="url(#architecture-arrow)" />
      <path className={s.dgLine} d="M656 150v30H386v44" markerEnd="url(#architecture-arrow)" />

      <text className={s.dgZl} x={276} y={210}>ASK · RECORDED EXECUTION</text>
      {box(276, 224, 220, 64, 'Queue & snapshot', 'copy · hash · manifest · single worker')}
      {box(276, 330, 220, 64, 'Host routing', 'compact counter / general harness')}
      <path className={s.dgLine} d="M386 288v42M496 362h30" markerEnd="url(#architecture-arrow)" />
      <text className={s.dgS} x={290} y={420}>Bound one-page counts → compact</text>
      <text className={s.dgS} x={290} y={440}>Other questions → general harness</text>
      {box(526, 330, 260, 64, 'Smart / Frontier', 'DeepSeek / Sol · OpenRouter', 'blue')}
      <text className={s.dgS} x={540} y={418}>Model choice is separate from routing</text>
      <text className={s.dgS} x={540} y={438}>Bounded responses and tool calls</text>
      <text className={s.dgS} x={540} y={470}>Compact: pixels, detection, PDF text</text>
      <text className={s.dgS} x={540} y={490}>General: tools allowed by the source</text>
      <text className={s.dgZl} x={826} y={210}>TOOLS · AS REQUESTED</text>
      {TOOLS.map(([title, detail], i) => box(826, 224 + i * 62, 300, 52, title, detail))}
      {/* Requests go to available tools; their evidence returns to the model loop. */}
      <path className={s.dgLine} d="M786 362h20V250M806 250v248" />
      {[250,312,374,436,498].map(y=><path key={y} className={s.dgLine} d={`M806 ${y}h20`} markerEnd="url(#architecture-arrow)" />)}
      <path className={s.dgLine} d="M1126 250h12v302H794V310H740v20" markerEnd="url(#architecture-arrow)" />
      <path className={s.dgLine} d="M1126 312h12M1126 374h12M1126 436h12M1126 498h12" />
      <text className={s.dgS} x={842} y={546}>Tool evidence returns to the loop</text>
      <text className={s.dgS} x={826} y={574}>D-FINE-L + Raster2Seq: Modal GPU · Argon: OpenRouter</text>

      {box(276, 500, 220, 64, 'SQLite · durable receipts', 'jobs · events · answers · billing')}
      <text className={s.dgS} x={290} y={590}>Interrupted jobs stay stopped</text>
      <text className={s.dgS} x={290} y={610}>Historical receipts stay unchanged</text>
      <path className={s.dgLine} d="M276 362h-12v170h12" markerEnd="url(#architecture-arrow)" />
      <path className={s.dgLine} d="M526 380h-14v252h14" markerEnd="url(#architecture-arrow)" />
      <text className={s.dgS} x={526} y={582}>Final proposal</text>
      {box(526, 600, 260, 64, 'Host checks & calculations', 'IDs · polygons · counts · measurements', 'blue')}
      {box(826, 600, 300, 64, 'Answer', 'citations · review groups · evidence status', 'green')}
      <path className={s.dgLine} d="M786 632h40" markerEnd="url(#architecture-arrow)" />
      <text className={s.dgS} x={540} y={688}>Checks + answer commit together; answer last</text>
      {box(826, 710, 300, 64, 'Review result', 'saved label revision · original answer retained', 'green')}
      <path className={s.dgLine} d="M976 664v34H240V592h-32" markerEnd="url(#architecture-arrow)" />
      <path className={s.dgLine} d="M118 624v118H826" markerEnd="url(#architecture-arrow)" />
      <text className={s.dgS} x={290} y={732}>Optional human review → saved labels on the data volume</text>

      <path className={s.dgFlow} d="M208 94C242 94 242 118 276 118" markerEnd="url(#architecture-flow)" />
      <path className={s.dgFlow} d="M208 120C240 120 240 256 276 256" markerEnd="url(#architecture-flow)" />
      <path className={s.dgFlow} d="M1126 632h18v186H232V348h-24" markerEnd="url(#architecture-flow)" />
      <text className={s.dgS} x={426} y={846}>NDJSON · live events, artifact replay and reconnect use the same job log</text>
    </svg>
  );
}

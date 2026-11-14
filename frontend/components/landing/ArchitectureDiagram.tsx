import s from './landing.module.css';

const box = (x: number, y: number, w: number, h: number, title: string, sub: string, tone?: 'acc' | 'blue' | 'green') => (
  <g key={`${title}${x}${y}`}>
    <rect className={`${s.dgBox}${tone ? ` ${s[`dg_${tone}`]}` : ''}`} x={x} y={y} width={w} height={h} rx={10} />
    <text className={s.dgT} x={x + 14} y={y + h / 2 - (sub ? 3 : -4)}>{title}</text>
    {sub && <text className={s.dgS} x={x + 14} y={y + h / 2 + 14}>{sub}</text>}
  </g>
);
const tool = (y: number, title: string, sub: string) => (
  <g key={title}>
    <rect className={s.dgBox} x={410} y={y} width={130} height={38} rx={9} />
    <text className={s.dgT} x={422} y={y + 24} fontSize={12}>{title} <tspan className={s.dgS}>{sub}</tspan></text>
  </g>
);

/** Browser → API → build pipeline and question-answering agents → stores. */
export function ArchitectureDiagram() {
  return (
    <svg viewBox="0 0 1000 430" role="img" aria-label="Architecture: the Next.js app on Vercel calls a FastAPI service on Modal, which runs the build pipeline and the question-answering agents over the drawing store">
      <defs>
        <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#b9b2a4" /></marker>
        <marker id="arb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#2563eb" /></marker>
      </defs>
      <rect className={s.dgZone} x={10} y={20} width={200} height={390} rx={14} /><text className={s.dgZl} x={24} y={40}>NEXT.JS · VERCEL</text>
      <rect className={s.dgZone} x={240} y={20} width={750} height={390} rx={14} /><text className={s.dgZl} x={254} y={40}>API · FASTAPI ON MODAL</text>
      {box(28, 60, 164, 70, 'Ymir app', 'Home · building · drawing', 'acc')}
      {box(28, 146, 164, 54, '3D model', 'three.js · sheets · ghosts')}
      {box(28, 212, 164, 54, '2D sheet', 'SVG · citations · placing')}
      {box(28, 278, 164, 54, 'Live trace', 'NDJSON stream')}
      {box(28, 344, 164, 50, 'Browser store', 'threads · your buildings')}
      <text className={s.dgZl} x={262} y={72}>BUILD PIPELINE · ON UPLOAD</text>
      {box(262, 82, 120, 52, 'Find plan', 'OpenCV')}
      {box(398, 82, 120, 52, 'Walls', 'WallNet · PyTorch')}
      {box(534, 82, 120, 52, 'Text, scale', 'RapidOCR')}
      {box(670, 82, 120, 52, 'Rooms', 'Shapely')}
      <path className={s.dgLine} d="M382 108h16M518 108h16M654 108h16" markerEnd="url(#ar)" />
      <text className={s.dgZl} x={262} y={176}>QUESTION ANSWERING · PER QUESTION</text>
      {box(262, 186, 110, 60, 'Planner', 'routes the question', 'blue')}
      {tool(186, 'Detector', 'YOLO11')}
      {tool(230, 'Reader', 'OCR index')}
      {tool(274, 'Geometry', 'Shapely')}
      {tool(318, 'Model', 'IFC query')}
      {tool(362, 'Vision', 'Claude')}
      {box(580, 186, 110, 60, 'Verifier', 'cross-checks', 'blue')}
      {box(730, 186, 120, 60, 'Cited answer', 'or abstain · needs', 'green')}
      <path className={s.dgLine} d="M372 216h18v-11h20M390 216v33h20M390 216v77h20M390 216v121h20M390 216v165h20" />
      <path className={s.dgLine} d="M540 205h40M540 249h20v-33h20" markerEnd="url(#ar)" />
      <path className={s.dgLine} d="M690 216h40" markerEnd="url(#ar)" />
      {box(870, 82, 104, 62, 'Drawings', 'samples · uploads')}
      {box(870, 300, 104, 62, 'Reviews', 'SQLite → labels')}
      <path className={s.dgLine} d="M790 108h80" markerEnd="url(#ar)" />
      <path className={s.dgLine} d="M922 144v42h-72" markerEnd="url(#ar)" />
      <path className={s.dgFlow} d="M192 95 C230 95 230 108 262 108" markerEnd="url(#arb)" />
      <path className={s.dgFlow} d="M192 110 C226 110 226 216 262 216" markerEnd="url(#arb)" />
      <path className={s.dgFlow} d="M790 186 V152 H232 V298 Q232 305 225 305 H194" markerEnd="url(#arb)" />
      <path className={s.dgLine} d="M192 369 C520 420 760 380 870 335" markerEnd="url(#ar)" />
      <text className={s.dgS} x={200} y={132} fontSize={10.5}>upload</text>
      <text className={s.dgS} x={204} y={176} fontSize={10.5}>ask</text>
      <text className={s.dgS} x={600} y={147} fontSize={10.5} fill="#2563eb">trace + answer, streamed back</text>
      <text className={s.dgS} x={560} y={414} fontSize={10.5}>your box corrections</text>
    </svg>
  );
}

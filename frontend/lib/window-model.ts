// Reviewed plan boxes supply horizontal placement. Vertical dimensions are schematic.
import type {Box, Pt, Wall} from './types';
import type {ReviewRecord} from './session';

export interface ModelWindow { id: string; sheet: string; box: Box; verified: boolean }
export interface WallWindow extends ModelWindow { wallId: string; start: number; end: number }
export interface WindowLayout { placed: WallWindow[]; unplaced: ModelWindow[] }

export function reviewedWindows(records: ReviewRecord[], sheet: string): ModelWindow[] {
  const review = records.filter(r => (r.sheet_id ?? r.drawing.id) === sheet && r.class === 'window')
    .reduce<ReviewRecord | undefined>((latest,r) => !latest || (r.id ?? 0) >= (latest.id ?? 0) ? r : latest, undefined);
  return (review?.objects ?? []).filter(o => o.status === 'accepted').map(o => ({
    id:o.proposal_id ?? o.id, sheet, box:[...o.bbox_xyxy] as Box, verified:true,
  }));
}

/** Match the labelled opening to an intersecting/aligned wall, never a distant wall. */
export function windowLayout(walls: Wall[], windows: ModelWindow[], removed = new Set<string>()): WindowLayout {
  const placed: WallWindow[] = [], unplaced: ModelWindow[] = [];
  for (const window of windows) {
    const [x0,y0,x1,y1] = window.box;
    if (![x0,y0,x1,y1].every(Number.isFinite) || x1<=x0 || y1<=y0) { unplaced.push(window); continue; }
    const center: Pt = [(x0+x1)/2,(y0+y1)/2], bw=x1-x0, bh=y1-y0;
    const candidates = walls.filter(w => !removed.has(w.id)).flatMap(w => {
      const dx=w.b[0]-w.a[0],dy=w.b[1]-w.a[1],length=Math.hypot(dx,dy);
      if(length<2)return [];
      const ux=dx/length,uy=dy/length;
      // Elongated boxes should follow the wall, not a perpendicular room partition.
      if(Math.max(bw,bh)/Math.min(bw,bh)>1.8 && Math.abs(bw>bh?ux:uy)<.72)return [];
      const distance=Math.abs((center[0]-w.a[0])*uy-(center[1]-w.a[1])*ux);
      const across=Math.abs(uy)*bw+Math.abs(ux)*bh;
      if(distance>across/2+Math.max(2,(w.thickness_px ?? 4)/2))return [];
      const along=(center[0]-w.a[0])*ux+(center[1]-w.a[1])*uy;
      const span=Math.abs(ux)*bw+Math.abs(uy)*bh;
      const start=Math.max(0,along-span/2),end=Math.min(length,along+span/2);
      if(end-start<Math.max(2,span*.55))return [];
      return [{wallId:w.id,start,end,score:distance/Math.max(1,across)}];
    }).sort((a,b)=>a.score-b.score);
    if(candidates.length)placed.push({...window,...candidates[0]});else unplaced.push(window);
  }
  return {placed,unplaced};
}

/** Overlapping horizontal openings share one hole; each reviewed label keeps its own identity. */
export function openingSpans(spans: [number,number][]): [number,number][] {
  const merged: [number,number][]=[];
  for(const [a,b] of spans.filter(([a,b])=>b-a>.0001).sort((a,b)=>a[0]-b[0])) {
    const last=merged.at(-1);
    if(last && a<=last[1]+.0001)last[1]=Math.max(last[1],b);else merged.push([a,b]);
  }
  return merged;
}

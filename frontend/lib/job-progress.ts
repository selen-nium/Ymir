import {requestStatus} from './answer-presentation';
import type {TraceEvent} from './types';

/** Human-readable execution state from recorded receipts, never a time estimate. */
export function progressLabel(event: TraceEvent): string | null {
  if(event.type==='job')return ({queued:'Queued for a worker…',running:'Reading selected drawings…',cancelling:'Cancelling after the current operation…',completed:'Preparing the answer…',failed:'Request stopped',cancelled:'Cancelled',interrupted:'Request interrupted'} as Record<string,string>)[event.status] ?? null;
  if(event.type==='stage')return event.stage==='checks'?'Checking the answer against the drawing…':null;
  if(event.type!=='progress')return null;
  const a=event.activity;
  if(a.operation==='response'){
    if(a.phase==='failed')return requestStatus(typeof a.reason==='string'?a.reason:'Reviewing the available evidence…');
    if(a.phase==='completed')return 'Checking the selected evidence…';
    return a.final?'Checking the answer…':Number(a.response_index)>0?'Reviewing drawing evidence…':'Reading selected drawings…';
  }
  if(a.operation!=='tool')return null;
  if(a.phase!=='started')return 'Reviewing tool results…';
  const tools: Record<string,string>={inspect:'Inspecting drawing detail…',read_pixels:'Reading drawing pixels…',read_pdf_text:'Reading PDF text…',read_pdf_vectors:'Inspecting PDF geometry…',native_room_candidates:'Checking native room outlines…',predict_room_polygons:'Finding room outlines…',calibrate:'Checking the drawing scale…',measure_room:'Measuring room area…',measure_distance:'Measuring distance…',coverage_plan:'Planning the drawing inspection…',execution_plan:'Planning the evidence checks…'};
  if(a.tool==='detect_objects'){
    const route=a.routing as Record<string,unknown> | undefined;
    const kind=route?.requested_class ?? route?.canonical_class;
    return typeof kind==='string'?`Finding ${kind.replaceAll('_',' ')}…`:'Finding objects…';
  }
  return tools[String(a.tool)] ?? 'Checking drawing evidence…';
}

export function elapsedLabel(at: number, now = Date.now()): string {
  const seconds=Math.max(0,Math.floor((now-at)/1000));
  return seconds<60?`${seconds}s elapsed`:`${Math.floor(seconds/60)}m ${seconds%60}s elapsed`;
}

// The Build steps for the active sheet, chosen by its kind: the backend's build trace, then the steps that
// need the user. Pure, so the panel draws them and the workspace store derives focus from the same list.
import { isPlaceable, isPlaced, kindOf, type SheetView } from './project';
import type { Session } from './session';

export type StepState = 'done' | 'you' | 'wait' | 'skip' | 'fail';
export type StepId = 'kind' | 'confirm' | 'check' | 'place' | 'model' | 'attached' | `b-${string}`;
export interface Step { id: StepId; name: string; tool: string; state: StepState; res: string }

export interface StepInput {
  session: Session;
  view: SheetView | null;
  isBase: boolean;
  hasBase: boolean;
  baseLabel: string;
  levelName: (id?: string | null) => string;
  placingHint: string | null;
}

const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);

export function buildSteps({ session, view: v, isBase, hasBase, baseLabel, levelName, placingHint }: StepInput): Step[] {
  const S = session.state, d = S.d;
  if (!d) return [];
  // Steps the backend reported in its build trace (node + status events), drawn as they arrived.
  const backend = new Map<string, Step>();
  for (const e of S.build) {
    if (e.type === 'node') {
      // Frozen samples keep their original traces; retired steps do not appear in today's Build panel.
      if(e.id==='vision' || /claude/i.test(e.name+' '+(e.tool ?? '')) || (e.id==='text' && /rapidocr/i.test(e.tool ?? '')))continue;
      const tool=/rapidocr/i.test(e.tool ?? '') ? (d.scale.source==='sheet_note'?'Source metadata':'Estimate') : e.tool ?? '';
      backend.set(e.id, { id: `b-${e.id}`, name: e.name, tool, state: 'wait', res: '' });
    }
    else if (e.type === 'status' && backend.has(e.id)) {
      const st = backend.get(e.id)!;
      st.state = ({ done: 'done', skipped: 'skip', failed: 'fail' } as const)[e.state as 'done'] ?? 'wait';
      st.res = (e.summary ?? '') + (e.ms ? ` · ${fmtMs(e.ms)}` : '');
    }
  }
  const kind = v?.kind ?? 'floor_plan', k = kindOf(kind);
  const list: Step[] = [...backend.values()];
  list.push({ id: 'kind', name: 'What is this', tool: 'You', state: v?.kindSure ? 'done' : 'you',
    res: `${k.label}${isPlaceable(kind) && v?.placement.level ? ` · ${levelName(v.placement.level)}` : ''}` });
  if (!v?.kindSure) return list;

  if (k.group === 'plan') {
    const cands = session.candidates(), ref = cands.find((c) => c.id === S.refId), model = d.scale.source === 'model';
    list.push({ id: 'confirm', name: 'Confirm scale', tool: model ? 'Model' : 'You', state: S.scaleConfirmed ? 'done' : cands.length ? 'you' : 'skip',
      res: model ? `From the model · ${d.scale.text ?? ''}`
        : S.scaleConfirmed ? (ref ? `Wall ${ref.letter} = ${session.refLength().toFixed(2)} m` : `${(S.scale * 100).toFixed(2)} cm per px`)
          : cands.length ? 'Pick a wall you know' : 'No walls to measure · placing sets it' });
    if (kind === 'floor_plan') {
      const pend = session.pending(), kept = d.walls.length - session.removed().size;
      list.push({ id: 'check', name: 'Check walls', tool: 'You', state: pend.length ? 'you' : 'done', res: pend.length ? `${pend.length} look doubtful` : `${kept} walls kept` });
    }
  }
  if (isPlaceable(kind) && !isBase) {
    const on = isPlaced(v), by = v.placement.type === 'attached' ? null : v.placement.by;
    list.push({ id: 'place', name: 'Place in building', tool: on && by === 'model' ? 'Model' : 'You',
      state: placingHint ? 'you' : on ? 'done' : 'you',
      res: placingHint ?? (on ? `${levelName(v.placement.level)} · ${by === 'model' ? 'lined up by the model' : 'placed by you'}`
        : hasBase ? `Match 2 points with ${baseLabel}` : 'Needs a floor plan first') });
  }
  if (isPlaceable(kind)) {
    const ready = isPlaced(v) && !session.pending().length;
    list.push({ id: 'model', name: '3D model', tool: 'three.js', state: ready ? 'done' : 'wait', res: ready ? 'In the building' : 'Updates live' });
  } else {
    list.push({ id: 'attached', name: 'Not in 3D', tool: 'Linked', state: 'skip', res: k.group === 'table' ? 'Tables are linked to the building, not placed' : 'Linked to the building, not placed' });
  }
  return list;
}

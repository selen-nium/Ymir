// Presentation only: immutable run results and saved review revisions remain separate.
import type {Answer, Citation, CountReview} from './types';
import type {ReviewRecord} from './session';
import {countNoun, countSentence} from './count-language';

const key = (sheet: string | undefined, cls: string) => `${sheet}:${cls}`;
export function latestReviews(records: ReviewRecord[]) {
  const groups = new Map<string, ReviewRecord>();
  for (const r of records) groups.set(key(r.sheet_id ?? r.drawing.id, r.class), r);
  return [...groups.values()];
}
export function savedReview(records: ReviewRecord[], group: CountReview) {
  return latestReviews(records).find(r => key(r.sheet_id ?? r.drawing.id, r.class) === key(group.sheet, group.class));
}
function belongs(c: Citation, group: CountReview) {
  if (c.sheet !== group.sheet) return false;
  if (c.object_class) return c.object_class === group.class;
  return group.boxes.some(b => b.id === (c.proposal_id ?? c.evidence_id)
    || (!c.polygon && !c.room_id && !c.text_id && c.box?.every((v,i) => v === b.box[i])))
    || c.label === `Human reviewed ${group.class}`;
}
export function applyReviews(machine: Answer, records: ReviewRecord[]): Answer {
  const latest = latestReviews(records);
  if (!latest.length) return machine;
  const groups = machine.reviews ?? (machine.review ? [machine.review] : []);
  const countOnly = !machine.rooms?.length && !machine.readings?.length && !machine.measurements?.length;
  let citations = [...(machine.citations ?? [])];
  const counts = [...(machine.counts ?? [])];
  for (const r of latest) {
    const sheet = r.sheet_id ?? r.drawing.id;
    const group = groups.find(g => key(g.sheet,g.class) === key(sheet,r.class));
    if (!group) continue;
    citations = citations.filter(c => !belongs(c,group));
    const noun = countNoun(r.class,1);
    citations.push(...r.objects.filter(o => o.status === 'accepted').map(o => ({
      n: o.proposal_id ? (machine.citations?.find(c => c.sheet === sheet && (c.proposal_id ?? c.evidence_id) === o.proposal_id)?.n ?? 0) : 0,
      label: `${noun.charAt(0).toUpperCase()+noun.slice(1)} · human-verified label`,
      sheet, page_id: group.page_id, object_class: r.class, proposal_id: o.proposal_id,
      box: [...o.bbox_xyxy] as Citation['box'], score: null, provenance: 'human_review', review_id: r.id,
    })));
    const index = counts.findIndex(c => key(c.sheet,c.object_class) === key(sheet,r.class));
    const count = {sheet, object_class:r.class, count:r.user_count, provenance:'human_review'};
    if (index < 0) counts.push(count); else counts[index] = {...counts[index],...count};
  }
  // Preserve source numbers used in model text. New labels get unused numbers.
  let next = Math.max(0,...(machine.citations ?? []).map(c => c.n),...citations.map(c => c.n));
  citations = citations.map((c,i) => countOnly ? {...c,n:i+1} : c.n ? c : {...c,n:++next});
  const complete = groups.length > 0 && groups.every(g => !!savedReview(latest,g));
  return {...machine, counts, citations, checked:true, review:undefined,
    value:counts.length === 1 ? counts[0].count : machine.value,
    text:counts.length === 1 && countOnly ? countSentence(counts[0].object_class,counts[0].count) : machine.text,
    status:machine.status === 'needs_review' && countOnly && complete ? 'answered' : machine.status,
    human_reviews:latest.map(r => ({sheet:r.sheet_id ?? r.drawing.id,class:r.class,count:r.user_count})),
  };
}

export const productName = (text: string) => text.replace(/Stoa Lens|\bLens\b/g,'Ymir');
function clean(text: string) {
  return productName(text).replace(/\s*\([^)]*(?:proposal[_-]|view-|page-|candidate|\bpx\b|pixels)[^)]*\)/gi,'')
    .replace(/\b(?:proposal[_-]|view-|page-|r2s-)[\w.-]+/g,'')
    .replace(/source-bound calibration/g,'confirmed scale').replace(/source-bound footprint/g,'room outline')
    .replace(/source-bound/g,'drawing').replace(/host-derived/g,'calculated').replace(/\s+/g,' ').trim();
}
export function shortDetail(text: string, limit = 170) {
  const t = clean(text);
  if (t.length <= limit) return t;
  const first = t.match(/^.{20,}?[.!?](?:\s|$)/)?.[0]?.trim();
  if (first && first.length <= limit) return first;
  const chunk = t.slice(0,limit-1).replace(/\s+\S*$/,'');
  return `${chunk}…`;
}
export function answerText(a: Answer) {
  if (a.job_id && a.counts?.length === 1 && !a.rooms?.length && !a.readings?.length && !a.measurements?.length)
    return countSentence(a.counts[0].object_class,a.counts[0].count);
  if (a.job_id && /^(Answer grounded|Here’s what I found|Evidence needs review|Some details need checking)/.test(a.text)) {
    const finding = a.reasons?.find(r => /^Answer:/i.test(r));
    if (finding) return shortDetail(finding.replace(/^Answer:\s*/i,''),240);
  }
  return productName(a.text);
}
/** Readable caveats; proposal-by-proposal audit prose is in the full run details. */
export function answerNotes(a: Answer): string[] {
  const notes = new Set<string>();
  const verified = (a.human_reviews ?? []).map(r => key(r.sheet,r.class));
  const verifiedCountOnly = a.status === 'answered' && !!a.checked && !a.rooms?.length && !a.readings?.length && !a.measurements?.length;
  if (a.status === 'needs_review' && a.reviews?.some(g => !verified.includes(key(g.sheet,g.class))))
    notes.add('Check the boxes to finish the count.');
  for (const raw of a.reasons ?? []) {
    const r = raw.toLowerCase();
    if (verifiedCountOnly && /count|detect|proposal|incomplete.*evidence/.test(r)) continue;
    if (/^(answer:|accepted[: ]|rejected[: ]|window count\s*=)/i.test(raw)) continue;
    if (/no (source.bound )?calibration|calibration.*(?:missing|unavailable)|no (?:dimension|scale)|without.*calibration|uncalibrated/.test(r)) {
      if (a.rooms?.length || a.measurements?.length) notes.add('A confirmed scale is needed for measurements in metres.');
    }
    if (/room (?:uses|names?).*inferred/.test(r)) notes.add('Room names are inferred from the drawing.');
    if (/labels?.*(?:not legible|unreadable|illegible)/.test(r)) notes.add('The printed labels are unclear.');
    if (/(?:native|pdf) text.*(?:unavailable|missing)|no native.*text/.test(r)) notes.add('This sheet has no readable PDF text. Text readings rely on the image.');
    if (/relative comparison|ranking.*footprint/.test(r)) notes.add('Room sizes were compared on the drawing; metric areas are unavailable.');
    if (/quantiz|raster.*(?:estimat|approximat)|polygon.*approximat/.test(r)) notes.add('Room outlines are approximate.');
    if (/(?:clipped|cut off).*(?:unresolved|incomplete)|(?:incomplete|unresolved).*(?:clipped|cut off)/.test(r)) notes.add('Some objects are cut off at the image edge.');
    if (/budget.*(?:exhaust|limit|insufficient)|insufficient.*budget/.test(r)) notes.add('The budget ran out before this check finished.');
    if (/timed? ?out|timeout|tool.*fail|tool.*unavailable/.test(r)) notes.add('A tool could not finish. Check the affected evidence.');
    if (/scope|limited to|provided page|single.*page|accepted.*proposal|detector|proposal[_-]|view-|page-|r2s-|polygon quantiz|zoom|magnif|pixel|\bpx\b/.test(r)) continue;
    if (/calibrat|native.*text|room (?:uses|labels?)|relative comparison|budget|tool.*fail/.test(r)) continue;
    // Retain short, plain caveats. Long audit explanations remain available via the receipt.
    const plain = shortDetail(raw);
    if (plain && plain.length <= 170 && !plain.endsWith('…')) notes.add(plain);
  }
  return [...notes];
}

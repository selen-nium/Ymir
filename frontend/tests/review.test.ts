// Low-confidence box review: answer contract, review state and the label record.
// The answer fixture has the shape backend/ymir/qa/routes.py SymbolCount returns (checked there by pytest).
import { describe, expect, test } from 'vitest';
import { createSession, needsReview } from '@/lib/session';
import type { Answer, Box, Drawing } from '@/lib/types';

// Field set the stoahackjunction annotation workbench accepts for an object (validate_objects).
const STOA_OBJECT_KEYS = new Set(['id', 'class', 'bbox_xyxy', 'subtype', 'status', 'proposal_id']);

function cubiSession() {
  const session = createSession({ pickRef: true });
  session.state.d = { id: 'cubi', title: 'Apartment plan', source: { dataset: 'CubiCasa5k' }, image: { url: '', size: [687, 455] },
    scale: { m_per_px: 0.03, source: 'estimate' }, texts: [], rooms: [], walls: [], symbols: [], openings: [] } satisfies Drawing;
  return session;
}
const BOXES: [string, Box, number][] = [['w1', [54, 21, 186, 37], 0.93], ['w2', [647, 250, 667, 382], 0.88], ['w3', [650, 40, 667, 150], 0.55],
  ['w4', [551, 40, 577, 182], 0.47], ['w5', [312, 250, 492, 292], 0.44], ['w6', [19, 276, 35, 314], 0.34]];
function windowsAnswer(): Answer {
  const boxes = BOXES.map(([id, box, score]) => ({ id, box: [...box] as Box, score }));
  const counted = boxes.filter((b) => b.score >= 0.42);
  return { status: 'answered', value: counted.length, unit: 'count', confidence: 0.58,
    review: { class: 'window', threshold: 0.42, boxes },
    text: `<b>${counted.length}</b> windows found by the detector.`,
    citations: counted.map((b, i) => ({ n: i + 1, label: `Window · ${Math.round(b.score * 100)}%`, box: b.box })),
    highlight: { rooms: [], walls: [], openings: [], symbols: [] }, focus: null };
}

describe('box review', () => {
  test('needsReview only for box answers under the confidence cutoff', () => {
    const review = { class: 'window', threshold: 0.4, boxes: [] };
    expect(needsReview({ status: 'answered', text: '', confidence: 0.58, review })).toBe(true);
    expect(needsReview({ status: 'answered', text: '', confidence: 0.91, review })).toBe(false);
    expect(needsReview({ status: 'answered', text: '', confidence: 0.3 })).toBe(false);
    expect(needsReview({ status: 'answered', text: '', review })).toBe(false);
    expect(needsReview({ status: 'abstained', text: '', confidence: 0.2, review })).toBe(false);
    expect(needsReview(null)).toBe(false);
    expect(needsReview(windowsAnswer())).toBe(true);
  });

  test('a confident answer the backend picked for a spot check is reviewed too', () => {
    const review = { class: 'door', threshold: 0.4, boxes: [], audit: true };
    expect(needsReview({ status: 'answered', text: '', confidence: 0.91, review })).toBe(true);
    expect(needsReview({ status: 'answered', text: '', confidence: 0.91, review: { ...review, audit: false } })).toBe(false);
  });

  test('the record says whether it was a spot check', () => {
    const session = cubiSession();
    session.startReview({ ...windowsAnswer(), confidence: 0.91, review: { ...windowsAnswer().review!, audit: true } });
    expect(session.finishReview()!.record.model.audit).toBe(true);
    session.startReview(windowsAnswer());
    expect('audit' in session.finishReview()!.record.model).toBe(false);
  });

  test('drop false boxes, count a near miss, resize, add and remove, then finish', () => {
    const session = cubiSession();
    session.startReview(windowsAnswer(), 'How many windows are there?');
    expect(session.reviewStats()).toEqual({ count: 5, toCheck: 3 });

    session.toggleBox('w4'); // wardrobe: not a window
    session.toggleBox('w5'); // kitchen counter: not a window
    session.toggleBox('w6'); // small window under the threshold: count it
    session.resizeBox('w3', [650, 40, 667, 235]); // window cut short by the model
    const extra = session.addBox([300, 300, 280, 310])!; // corners in any order
    expect(extra.box).toEqual([280, 300, 300, 310]);
    session.toggleBox(extra.id); // a drawn box is removed on click
    expect(session.state.review!.items.some((i) => i.id === extra.id)).toBe(false);
    expect(session.reviewStats()).toEqual({ count: 4, toCheck: 0 });

    const { answer, record, changes } = session.finishReview()!;
    expect(session.state.review).toBeNull();
    expect(answer.value).toBe(4);
    expect(answer.checked).toBe(true);
    expect(answer.citations).toHaveLength(4);
    expect(changes).toEqual({ removed: 2, added: 1, resized: 1 });

    expect(record.schema).toBe('drawing-qa-review-v1');
    expect(record.question).toBe('How many windows are there?');
    expect(record.user_count).toBe(4);
    expect(record.model).toEqual({ value: 5, confidence: 0.58, threshold: 0.42, scores: { w1: 0.93, w2: 0.88, w3: 0.55, w4: 0.47, w5: 0.44, w6: 0.34 },
      // the model's own boxes, before the user resized any, so the backend can tell a nudge from a different object
      boxes: Object.fromEntries(BOXES.map(([id, box]) => [id, box])) });
    expect(record.drawing).toEqual({ id: 'cubi', title: 'Apartment plan', dataset: 'CubiCasa5k', image_size: [687, 455] });
    expect(Object.fromEntries(record.objects.map((o) => [o.id, o.status]))).toEqual(
      { w1: 'accepted', w2: 'accepted', w3: 'accepted', w4: 'rejected', w5: 'rejected', w6: 'accepted' });
    for (const o of record.objects) {
      expect(Object.keys(o).every((k) => STOA_OBJECT_KEYS.has(k))).toBe(true);
      expect(o.class).toBe('window');
      expect(o.bbox_xyxy.every(Number.isInteger)).toBe(true);
    }
    expect(record.objects.find((o) => o.id === 'w3')!.bbox_xyxy).toEqual([650, 40, 667, 235]);
  });

  test('a user-drawn box is labelled without a proposal id; boxes are clamped to the image', () => {
    const session = cubiSession();
    session.startReview(windowsAnswer());
    expect(session.addBox([10, 10, 11, 11])).toBeNull(); // too small
    const b = session.addBox([680, 440, 720, 470])!; // runs off the image
    expect(b.box).toEqual([680, 440, 687, 455]);
    session.resizeBox('w6', [19, 276, 35, 314]); // resizing a near miss counts it
    expect(session.state.review!.items.find((i) => i.id === 'w6')!.counted).toBe(true);
    const { record, changes } = session.finishReview()!;
    const drawn = record.objects.find((o) => o.id === b.id)!;
    expect(drawn.status).toBe('accepted');
    expect('proposal_id' in drawn).toBe(false);
    expect(changes.added).toBe(2);
  });

  test('skip keeps the model answer, marked unchecked', () => {
    const session = cubiSession();
    session.startReview(windowsAnswer());
    session.toggleBox('w4');
    const a = session.skipReview()!;
    expect(a.value).toBe(5);
    expect(a.unchecked).toBe(true);
    expect(session.state.review).toBeNull();
  });
});

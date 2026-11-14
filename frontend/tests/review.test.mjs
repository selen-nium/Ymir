// Low-confidence box review: answer contract, review state and the label record.
// run (repo root): node --test frontend/tests/*.test.mjs
// The answer fixture has the shape backend/ymir/qa/routes.py SymbolCount returns (checked there by pytest).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createSession, needsReview } from '../shared/session.js';

// Field set the stoahackjunction annotation workbench accepts for an object (validate_objects).
const STOA_OBJECT_KEYS = new Set(['id', 'class', 'bbox_xyxy', 'subtype', 'status', 'proposal_id']);

function cubiSession() {
  const session = createSession({ pickRef: true });
  session.state.d = { id: 'cubi', title: 'Apartment plan', source: { dataset: 'CubiCasa5k' }, image: { size: [687, 455] },
    texts: [], rooms: [], walls: [], symbols: [], openings: [] };
  return session;
}
const BOXES = [['w1', [54, 21, 186, 37], 0.93], ['w2', [647, 250, 667, 382], 0.88], ['w3', [650, 40, 667, 150], 0.55],
  ['w4', [551, 40, 577, 182], 0.47], ['w5', [312, 250, 492, 292], 0.44], ['w6', [19, 276, 35, 314], 0.34]];
function windowsAnswer() {
  const boxes = BOXES.map(([id, box, score]) => ({ id, box: [...box], score }));
  const counted = boxes.filter((b) => b.score >= 0.42);
  return { status: 'answered', value: counted.length, unit: 'count', confidence: 0.58,
    review: { class: 'window', threshold: 0.42, boxes },
    text: `<b>${counted.length}</b> windows found by the detector.`,
    citations: counted.map((b, i) => ({ n: i + 1, label: `Window · ${Math.round(b.score * 100)}%`, box: b.box })),
    highlight: { rooms: [], walls: [], openings: [], symbols: [] }, focus: null };
}
const answerFor = async () => windowsAnswer();

test('needsReview only for box answers under the confidence cutoff', () => {
  const boxes = { class: 'window', threshold: 0.4, boxes: [] };
  assert.equal(needsReview({ status: 'answered', confidence: 0.58, review: boxes }), true);
  assert.equal(needsReview({ status: 'answered', confidence: 0.91, review: boxes }), false);
  assert.equal(needsReview({ status: 'answered', confidence: 0.3 }), false);
  assert.equal(needsReview({ status: 'answered', review: boxes }), false);
  assert.equal(needsReview({ status: 'abstained', confidence: 0.2, review: boxes }), false);
  assert.equal(needsReview(null), false);
});

test('the backend windows answer asks for review', () => {
  assert.equal(needsReview(windowsAnswer()), true);
});

test('review: drop false boxes, count a near miss, resize, add and remove, then finish', async () => {
  const session = cubiSession();
  const a = await answerFor(session, 'How many windows are there?');
  session.startReview(a, 'How many windows are there?');
  assert.deepEqual(session.reviewStats(), { count: 5, toCheck: 3 });

  session.toggleBox('w4');                       // wardrobe: not a window
  session.toggleBox('w5');                       // kitchen counter: not a window
  session.toggleBox('w6');                       // small window under the threshold: count it
  session.resizeBox('w3', [650, 40, 667, 235]);  // window cut short by the model
  const extra = session.addBox([300, 300, 280, 310]);   // corners in any order
  assert.deepEqual(extra.box, [280, 300, 300, 310]);
  session.toggleBox(extra.id);                   // a drawn box is removed on click
  assert.equal(session.state.review.items.some((i) => i.id === extra.id), false);
  assert.deepEqual(session.reviewStats(), { count: 4, toCheck: 0 });

  const { answer, record, changes } = session.finishReview();
  assert.equal(session.state.review, null);
  assert.equal(answer.value, 4);
  assert.equal(answer.checked, true);
  assert.equal(answer.citations.length, 4);
  assert.deepEqual(changes, { removed: 2, added: 1, resized: 1 });

  assert.equal(record.schema, 'drawing-qa-review-v1');
  assert.equal(record.question, 'How many windows are there?');
  assert.equal(record.user_count, 4);
  assert.deepEqual(record.model, { value: 5, confidence: 0.58, threshold: 0.42,
    scores: { w1: 0.93, w2: 0.88, w3: 0.55, w4: 0.47, w5: 0.44, w6: 0.34 } });
  assert.deepEqual(record.drawing, { id: 'cubi', title: 'Apartment plan', dataset: 'CubiCasa5k', image_size: [687, 455] });
  const status = Object.fromEntries(record.objects.map((o) => [o.id, o.status]));
  assert.deepEqual(status, { w1: 'accepted', w2: 'accepted', w3: 'accepted', w4: 'rejected', w5: 'rejected', w6: 'accepted' });
  for (const o of record.objects) {
    assert.ok(Object.keys(o).every((k) => STOA_OBJECT_KEYS.has(k)), `unexpected field in ${o.id}`);
    assert.equal(o.class, 'window');
    assert.ok(o.bbox_xyxy.every(Number.isInteger));
  }
  assert.deepEqual(record.objects.find((o) => o.id === 'w3').bbox_xyxy, [650, 40, 667, 235]);
});

test('a user-drawn box is labelled without a proposal id; boxes are clamped to the image', async () => {
  const session = cubiSession();
  session.startReview(await answerFor(session, 'How many windows are there?'));
  assert.equal(session.addBox([10, 10, 11, 11]), null);             // too small
  const b = session.addBox([680, 440, 720, 470]);                   // runs off the image
  assert.deepEqual(b.box, [680, 440, 687, 455]);
  session.resizeBox('w6', [19, 276, 35, 314]);                     // resizing a near miss counts it
  assert.equal(session.state.review.items.find((i) => i.id === 'w6').counted, true);
  const { record, changes } = session.finishReview();
  const drawn = record.objects.find((o) => o.id === b.id);
  assert.equal(drawn.status, 'accepted');
  assert.equal('proposal_id' in drawn, false);
  assert.equal(changes.added, 2);
});

test('skip keeps the model answer, marked unchecked', async () => {
  const session = cubiSession();
  session.startReview(await answerFor(session, 'How many windows are there?'));
  session.toggleBox('w4');
  const a = session.skipReview();
  assert.equal(a.value, 5);
  assert.equal(a.unchecked, true);
  assert.equal(session.state.review, null);
});

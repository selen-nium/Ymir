"""Phase 3: the DeepSeek arms of PLAN §6 (DeepSeek V4.1 Flash on Verda, src/stoa_iterative/verda.py).

  B  PNG-only comparator, DeepSeek with native tool calls (PLAN §3.1-3.2): it sees the sheet with the current rooms,
     the flagged regions and crops; it calls tools (view_region, segment_region, grow_room_from_seed, snap_room,
     split_room_by_walls, rule_repair, read_text, room_stats) and ends with submit_edits.
  C  Same information, no tool choice: the host pre-runs the tools on every flag and shows the candidates; DeepSeek
     returns prompted JSON with the same edit operations.
  E  Oracle comparator (ceiling): flags are the true errors from the reference (missed, false, >2% area off),
     then as B. Acceptance uses the true-error count.

Host control (PLAN §3.1): at most 3 calls per sheet per iteration, at most 2 tool calls per response, typed edit
operations only, every operation validated (valid polygon, minimum size, inside the plan, no OCR room number left
uncovered); heal resolves overlaps. Dismissals ("this flag is not an error") and deletions are judgments: their
regions are not flagged again. An iteration is kept only if the disagreement score D falls (monotone acceptance);
otherwise the graph is reverted and the sheet stops. Every call goes through the Verda ledger and cache.

python -m stoa_iterative.vlm_arms <dataset> <split> <n_max> <arm> <generator>[,<generator>...]
"""
import base64
import copy
import io
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from stoa_semseg import cpu_arms
from stoa_semseg.common import CLASSES, pages
from stoa_semseg.evaluate import eval_page, read_predictions

from . import comparator as C
from . import repair, text, trace, verda
from .graph import from_prediction_row, heal, to_prediction_row
from .phase1 import OTHER, false_rooms, thresholds

K_FLAGS = 6
MAX_CALLS = 3
MAX_TOOLS = 2
WORKERS = int(os.environ.get('WORKERS', '6'))


# ---------------------------------------------------------------- rendering

def _data_url(im, q=80):
    buf = io.BytesIO()
    im.convert('RGB').save(buf, format='JPEG', quality=q)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def draw(P, graph, bbox=None, cands=None, flags=None, size=768, pad=40):
    """The PNG (crop) with rooms in blue (labelled by id), candidates in green, flags in red. bbox in page px."""
    im = Image.fromarray(P.rgb).convert('RGB')
    H, W = P.rgb.shape[:2]
    ox, oy = P.off
    if bbox is not None:
        x0, y0 = max(0, int(bbox[0] - ox - pad)), max(0, int(bbox[1] - oy - pad))
        x1, y1 = min(W, int(bbox[2] - ox + pad)), min(H, int(bbox[3] - oy + pad))
        if x1 - x0 < 48 or y1 - y0 < 48:
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            x0, y0, x1, y1 = max(0, cx - 60), max(0, cy - 60), min(W, cx + 60), min(H, cy + 60)
    else:
        x0, y0, x1, y1 = 0, 0, W, H
    s = size / max(x1 - x0, y1 - y0)
    im = im.crop((x0, y0, x1, y1)).resize((max(1, int((x1 - x0) * s)), max(1, int((y1 - y0) * s))))
    d = ImageDraw.Draw(im, 'RGBA')
    T = lambda x, y: ((x - ox - x0) * s, (y - oy - y0) * s)
    lw = 2 if bbox is not None else 1
    for r in graph['rooms']:
        g = Polygon(r['polygon'])
        if bbox is not None and not g.intersects(box(bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)):
            continue
        d.polygon([T(*p) for p in r['polygon']], outline=(30, 90, 220, 255), fill=(30, 90, 220, 30))
        c = g.representative_point()
        d.text(T(c.x, c.y), r['id'], fill=(20, 60, 180, 255))
    for cid, poly in (cands or {}).items():
        for part in getattr(poly, 'geoms', [poly]):
            d.line([T(*p) for p in part.exterior.coords], fill=(10, 160, 60, 255), width=lw + 1)
            c = part.representative_point()
            d.text(T(c.x, c.y + 10), cid, fill=(0, 120, 40, 255))
    for fid, f in (flags or {}).items():
        b = f['bbox']
        d.rectangle([T(b[0], b[1]), T(b[2], b[3])], outline=(220, 30, 30, 255), width=lw + 1)
        d.text(T(b[0], b[1] - 12), fid, fill=(200, 20, 20, 255))
    return _data_url(im)


# ---------------------------------------------------------------- flags

def _iou_box(a, b):
    A, B = box(*a), box(*b)
    i = A.intersection(B).area
    return i / max(1e-9, A.area + B.area - i)


def pngonly_flags(P, g, orow, othr, dismissed):
    fl = [f for f in C.compare(P, g, orow, othr, checks=trace.LOOP_CHECKS)
          if not any(_iou_box(f['bbox'], d) > 0.3 for d in dismissed)]
    return fl


def oracle_flags(page, g, nc, dismissed):
    """Arm E: true errors from the reference (ceiling only)."""
    scored = eval_page(page, to_prediction_row(g), 0.0, nc)
    out = []
    preds = {r['id']: Polygon(r['polygon']).buffer(0) for r in g['rooms']}
    for x in scored['rooms']:
        ref = Polygon(page['rooms'][x['ri']]['polygon']).buffer(0)
        if x['matched_iou'] is None:
            c = ref.representative_point()
            out.append({'check': 'O-miss', 'why': 'a reference room has no predicted room here', 'bbox': list(C._bbox(ref)),
                        'room': None, 'seed': [c.x, c.y]})
        elif x['ape'] is not None and x['ape'] > 0.02:
            rid = max(preds, key=lambda k: preds[k].intersection(ref).area) if preds else None
            sign = 'too large' if rid and preds[rid].area > ref.area else 'too small'
            out.append({'check': 'O-area', 'why': f'room area is {100 * x["ape"]:.0f}% off ({sign})', 'bbox': list(C._bbox(ref)), 'room': rid})
    for fr in false_rooms(page, g):
        rid = max(preds, key=lambda k: preds[k].intersection(fr).area) if preds else None
        out.append({'check': 'O-false', 'why': 'this predicted room is not a room in the reference', 'bbox': list(C._bbox(fr)), 'room': rid})
    return [f for f in out if not any(_iou_box(f['bbox'], d) > 0.3 for d in dismissed)]


def D_score(flags):
    return sum(C.WEIGHTS.get(f['check'], 1.0) for f in flags)


# ---------------------------------------------------------------- tools

class Tools:
    def __init__(self, P, graph, gen_row, orow, othr, thr):
        self.P, self.g, self.gen_row, self.orow, self.othr, self.thr = P, graph, gen_row, orow, othr, thr
        self.cands = {}   # id -> shapely polygon (or MultiPolygon for splits)
        self.rule = {}    # id -> flag (rule candidate re-derived at apply time)
        self.k = 0
        self.images = []  # images to show after tool results
        self.k2 = (P.page.get('m_per_px') or 0) ** 2

    def _cid(self, prefix):
        self.k += 1
        return f'{prefix}{self.k}'

    def _area(self, poly):
        return round(poly.area * self.k2, 2) if self.k2 else round(poly.area)

    def _desc(self, cid, poly):
        unit = 'm2' if self.k2 else 'px2'
        rooms = [(r['id'], Polygon(r['polygon']).buffer(0)) for r in self.g['rooms']]
        ov = sorted(((round(poly.intersection(q).area / max(1, poly.area), 2), rid) for rid, q in rooms if q.intersects(poly)), reverse=True)[:3]
        return {'candidate': cid, f'area_{unit}': self._area(poly), 'overlaps_existing': [{'room': r, 'share': v} for v, r in ov if v > 0.02]}

    def view_region(self, bbox):
        self.images.append(('view of ' + str([round(v) for v in bbox]), draw(self.P, self.g, bbox, flags=None)))
        return {'ok': True, 'note': 'image follows'}

    def segment_region(self, bbox):
        out, cands = [], {}
        B = box(*bbox)
        for src, row, thr in (('generator', self.gen_row, self.thr), ('other_segmenter', self.orow, self.othr)):
            for inst in (row or {}).get('instances', []):
                if inst['score'] < thr or not inst['polygons'] or len(inst['polygons'][0]) < 3:
                    continue
                p = C.largest(Polygon(inst['polygons'][0]).buffer(0))
                if p is None or p.intersection(B).area < 0.3 * min(p.area, B.area):
                    continue
                cid = self._cid('S')
                self.cands[cid] = p
                cands[cid] = p
                out.append({**self._desc(cid, p), 'source': src, 'score': round(inst['score'], 2)})
        out = out[:6]
        if cands:
            self.images.append(('segment_region candidates', draw(self.P, self.g, bbox, cands={k: cands[k] for k in [o['candidate'] for o in out]})))
        return {'candidates': out}

    def grow_room_from_seed(self, x, y):
        out, cands = [], {}
        for r in (14, 6):
            reg = repair._region_at_radius(self.P, (x, y), r)
            if reg is None or reg.area < repair.MIN_ROOM[self.P.dataset] / 3:
                continue
            if any(reg.symmetric_difference(c).area < 0.05 * reg.area for c in cands.values()):
                continue
            cid = self._cid('G')
            self.cands[cid] = cands[cid] = reg
            out.append({**self._desc(cid, reg), 'door_bridging_px': r})
        if cands:
            b = unary_union(list(cands.values())).bounds
            self.images.append(('grow_room_from_seed candidates', draw(self.P, self.g, b, cands=cands)))
        return {'candidates': out} if out else {'candidates': [], 'note': 'no enclosed region at this point'}

    def _room(self, room_id):
        return next((r for r in self.g['rooms'] if r['id'] == room_id), None)

    def snap_room(self, room_id):
        r = self._room(room_id)
        if r is None:
            return {'error': f'no room {room_id}'}
        parts = repair.snap_room(self.P, r)
        if not parts or parts[0] is None:
            return {'candidates': [], 'note': 'snap failed'}
        cid = self._cid('N')
        self.cands[cid] = parts[0]
        self.images.append(('snap_room candidate', draw(self.P, self.g, parts[0].bounds, cands={cid: parts[0]})))
        return {'candidates': [{**self._desc(cid, parts[0]), 'old_area': self._area(Polygon(r['polygon']))}]}

    def split_room_by_walls(self, room_id):
        r = self._room(room_id)
        if r is None:
            return {'error': f'no room {room_id}'}
        parts = repair.split_by_walls(self.P, Polygon(r['polygon'], r.get('holes') or []), repair.MIN_ROOM[self.P.dataset] / 3)
        if not parts:
            return {'candidates': [], 'note': 'no wall inside this room splits it'}
        cid = self._cid('W')
        mp = unary_union(parts)
        self.cands[cid] = mp
        self.images.append(('split candidate', draw(self.P, self.g, mp.bounds, cands={cid: mp})))
        return {'candidates': [{'candidate': cid, 'parts': len(parts), 'part_areas': [self._area(p) for p in parts]}]}

    def rule_repair(self, flag):
        cands = repair.candidates_for(self.P, self.g, flag, self.orow, self.othr)
        if not cands:
            return {'candidates': [], 'note': 'the fixed rule has no repair for this flag'}
        cand, label = cands[0]
        cid = self._cid('R')
        self.rule[cid] = flag
        before = {r['id']: Polygon(r['polygon']).buffer(0) for r in self.g['rooms']}
        new = [Polygon(r['polygon']).buffer(0) for r in cand['rooms'] if r['id'] not in before]
        if new:
            self.images.append((f'rule {label} result', draw(self.P, self.g, unary_union(new).bounds, cands={cid: unary_union(new)})))
        return {'candidates': [{'candidate': cid, 'rule': label, 'rooms_before': len(self.g['rooms']), 'rooms_after': len(cand['rooms'])}]}

    def read_text(self, bbox):
        B = box(*bbox)
        return {'tokens': [{'text': t['text'], 'kind': t['kind'], 'bbox': [round(v) for v in t['bbox']]}
                           for t in self.P.tokens if B.intersects(box(*t['bbox']))][:40]}

    def room_stats(self, room_id):
        r = self._room(room_id)
        if r is None:
            return {'error': f'no room {room_id}'}
        g = Polygon(r['polygon']).buffer(0)
        labels = [t['text'] for t in self.P.tokens if g.contains(Point((t['bbox'][0] + t['bbox'][2]) / 2, (t['bbox'][1] + t['bbox'][3]) / 2))]
        nb = [q['id'] for q in self.g['rooms'] if q['id'] != room_id and Polygon(q['polygon']).buffer(3).intersects(g)]
        return {'room': room_id, 'area': self._area(g), 'unit': 'm2' if self.k2 else 'px2', 'labels_inside': labels[:12],
                'class': CLASSES[self.P.dataset][r['type']] if 0 <= (r.get('type') or -1) < len(CLASSES[self.P.dataset]) else None,
                'neighbours': nb[:12], 'source': r.get('source')}


TOOL_SPECS = [
    ('view_region', 'Zoomed crop of the drawing with the current rooms drawn (blue, labelled by id).', {'bbox': 'bbox'}),
    ('segment_region', 'Room instances from the two segmenters inside a region, as candidate polygons (drawn green).', {'bbox': 'bbox'}),
    ('grow_room_from_seed', 'Flood the free space from a point until walls stop it (doors bridged). Returns candidate polygons.', {'x': 'number', 'y': 'number'}),
    ('snap_room', 'Refit one room outline to the drawn walls. Returns a candidate.', {'room_id': 'string'}),
    ('split_room_by_walls', 'Split one room along wall ink inside it. Returns a multi-part candidate.', {'room_id': 'string'}),
    ('rule_repair', 'The fixed rule-based repair for one flag (what the no-VLM loop would do). Returns a candidate.', {'flag_id': 'string'}),
    ('read_text', 'OCR tokens (room numbers, names, areas) inside a region.', {'bbox': 'bbox'}),
    ('room_stats', 'Area, labels inside, class, neighbours of one room.', {'room_id': 'string'}),
]
OPS_SCHEMA = {'type': 'array', 'items': {'type': 'object', 'properties': {
    'op': {'type': 'string', 'enum': ['add_room', 'replace_room', 'delete_room', 'merge_rooms', 'split_room', 'apply_rule']},
    'candidate': {'type': 'string'}, 'room_id': {'type': 'string'}, 'room_ids': {'type': 'array', 'items': {'type': 'string'}},
    'reason': {'type': 'string'}}, 'required': ['op']}}
DISMISS_SCHEMA = {'type': 'array', 'items': {'type': 'object', 'properties': {'flag_id': {'type': 'string'}, 'reason': {'type': 'string'}}, 'required': ['flag_id']}}


def tool_defs():
    out = []
    for name, desc, params in TOOL_SPECS:
        props = {k: ({'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4, 'description': '[x0, y0, x1, y1] page pixels'}
                     if v == 'bbox' else {'type': v}) for k, v in params.items()}
        out.append({'type': 'function', 'function': {'name': name, 'description': desc,
                                                     'parameters': {'type': 'object', 'properties': props, 'required': list(params)}}})
    out.append({'type': 'function', 'function': {'name': 'submit_edits', 'description': 'Finish this iteration: the edit operations to apply and the flags you judge to be false alarms.',
                                                 'parameters': {'type': 'object', 'properties': {'ops': OPS_SCHEMA, 'dismiss': DISMISS_SCHEMA}, 'required': ['ops', 'dismiss']}}})
    return out


SYSTEM = """You review a structured room graph extracted from an architectural floor plan PNG and fix it.
Rooms are closed polygons in page pixel coordinates. A comparator flagged regions where the graph and the drawing may disagree (F1, F2, ...).
For each flag decide: repair it, or dismiss it as a false alarm (shafts, voids, stair openings, courtyards, balconies outside the plan, furniture, hatching and text are not missed rooms; a room correctly bounded by thin walls is not an error).
You never write coordinates for geometry: tools produce candidate polygons (ids like S3, G5, N2, W4, R1) and you choose among them.
Edit operations: add_room(candidate), replace_room(room_id, candidate), delete_room(room_id) for a predicted room that is not a real room, merge_rooms(room_ids) for one room split in pieces, split_room(room_id, candidate from split_room_by_walls), apply_rule(candidate from rule_repair).
Only add or keep spaces that a floor-plan room schedule would list as rooms. Be conservative: an edit that is not clearly right is worse than no edit.
At most 2 tool calls per response. You have at most 3 responses in this iteration; end with submit_edits."""

SYSTEM_C = """You review a structured room graph extracted from an architectural floor plan PNG and fix it.
A comparator flagged regions (F1, F2, ...). For each flag the host already ran the repair tools; their candidate polygons are drawn green with ids.
For each flag decide: apply one candidate, delete a predicted room that is not a real room, merge rooms, or dismiss the flag as a false alarm (shafts, voids, stair openings, courtyards, furniture, hatching and text are not missed rooms).
Only add or keep spaces that a floor-plan room schedule would list as rooms. Be conservative: an edit that is not clearly right is worse than no edit.
Return only JSON: {"ops": [{"op": "add_room|replace_room|delete_room|merge_rooms|split_room|apply_rule", "candidate": "...", "room_id": "...", "room_ids": [...], "reason": "..."}], "dismiss": [{"flag_id": "F1", "reason": "..."}]}"""


def flag_brief(P, g, fid, f, tools):
    rooms = [r for r in g['rooms'] if Polygon(r['polygon']).intersects(box(*f['bbox']))][:8]
    k2 = tools.k2
    return {'flag_id': fid, 'check': f['check'], 'why': f['why'], 'bbox': [round(v) for v in f['bbox']], 'room': f.get('room'),
            'seed': [round(v) for v in f['seed']] if f.get('seed') else None,
            'rooms_here': [{'id': r['id'], 'area': round(Polygon(r['polygon']).area * k2, 2) if k2 else round(Polygon(r['polygon']).area)} for r in rooms]}


def first_message(P, g, F, tools, unit):
    content = [{'type': 'text', 'text': f'Sheet {P.page["id"]}. Areas in {unit}. Whole plan with rooms (blue) and flags (red):'},
               {'type': 'image_url', 'image_url': {'url': draw(P, g, flags=F, size=1024)}},
               {'type': 'text', 'text': 'Flags:\n' + json.dumps([flag_brief(P, g, fid, f, tools) for fid, f in F.items()])}]
    for fid, f in F.items():
        content += [{'type': 'text', 'text': f'Crop around {fid}:'}, {'type': 'image_url', 'image_url': {'url': draw(P, g, f['bbox'], flags={fid: f}, size=512)}}]
    return content


# ---------------------------------------------------------------- applying edits

def apply_ops(P, g, ops, tools, F):
    """Validate and apply typed operations in order. Returns (graph, applied labels, rejected, judged regions)."""
    applied, rejected, judged = [], [], []
    mn = repair.MIN_ROOM[P.dataset] / 3
    plan = box(*[P.off[0], P.off[1], P.off[0] + P.rgb.shape[1], P.off[1] + P.rgb.shape[0]])
    ids = lambda gr: {r['id'] for r in gr['rooms']}
    for op in ops or []:
        kind = op.get('op')
        try:
            before = covered = repair.covered_numbers(P, g)
            new = None
            if kind in ('add_room', 'replace_room', 'split_room'):
                cand = tools.cands.get(op.get('candidate'))
                if cand is None:
                    raise ValueError('unknown candidate')
                parts = [p for p in getattr(cand, 'geoms', [cand]) if p.is_valid and p.area >= mn and plan.contains(p.centroid)]
                if not parts:
                    raise ValueError('candidate invalid, too small or outside the plan')
                if kind == 'add_room':
                    new = g
                    for p in parts:
                        new = repair._add(new, p, 'vlm-' + op.get('candidate', '')[:1], 'vlm-add')
                else:
                    if op.get('room_id') not in ids(g):
                        raise ValueError('unknown room')
                    new = repair._replace(g, op['room_id'], parts, 'vlm', 'vlm-' + kind)
            elif kind == 'delete_room':
                r = next((x for x in g['rooms'] if x['id'] == op.get('room_id')), None)
                if r is None:
                    raise ValueError('unknown room')
                new = copy.deepcopy(g)
                new['rooms'] = [x for x in new['rooms'] if x['id'] != r['id']]
                new = heal(new)
                judged.append(list(C._bbox(Polygon(r['polygon']).buffer(0))))
            elif kind == 'merge_rooms':
                rs = [x for x in g['rooms'] if x['id'] in set(op.get('room_ids') or [])]
                if len(rs) < 2:
                    raise ValueError('merge needs two known rooms')
                u = C.largest(unary_union([Polygon(x['polygon']).buffer(1.5) for x in rs]).buffer(-1.5))
                new = copy.deepcopy(g)
                new['rooms'] = [x for x in new['rooms'] if x['id'] not in {r['id'] for r in rs[1:]}]
                new = repair._replace(new, rs[0]['id'], [u], 'vlm', 'vlm-merge')
            elif kind == 'apply_rule':
                f = tools.rule.get(op.get('candidate'))
                if f is None:
                    raise ValueError('unknown rule candidate')
                cs = repair.candidates_for(P, g, f, tools.orow, tools.othr)
                if not cs:
                    raise ValueError('rule no longer applies')
                new = cs[0][0]
            else:
                raise ValueError('unknown op')
            if kind != 'delete_room' and repair.covered_numbers(P, new) < before:
                raise ValueError('would leave a printed room number outside every room')
            if kind == 'delete_room' and repair.covered_numbers(P, new) < before:
                raise ValueError('room holds a printed room number')
            g = new
            applied.append(f'{kind}')
            tools.g = g
        except Exception as e:
            rejected.append({'op': op, 'why': str(e)[:120]})
    return g, applied, rejected, judged


def _parse_json(s):
    import re
    m = re.search(r'\{.*\}', s or '', re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- one iteration

def vlm_iteration(arm, P, page, g, gen_row, orow, othr, thr, dismissed, nc, tag):
    unit = 'm2' if P.page.get('m_per_px') else 'pixels²'
    flags = oracle_flags(page, g, nc, []) if arm == 'E' else pngonly_flags(P, g, orow, othr, dismissed)
    D0 = D_score(flags)
    stats = {'flags': len(flags), 'calls': 0, 'tool_calls': 0, 'ops_proposed': 0, 'ops_applied': 0, 'ops_rejected': 0,
             'dismissed': 0, 'errors': 0, 'accepted': False}
    if not flags:
        return g, [], stats, dismissed, flags
    order = sorted(flags, key=lambda f: -C.WEIGHTS.get(f['check'], 1.0))[:K_FLAGS]
    F = {f'F{i + 1}': f for i, f in enumerate(order)}
    tools = Tools(P, g, gen_row, orow, othr, thr)
    result = None
    if arm == 'C':
        content = [{'type': 'text', 'text': f'Sheet {P.page["id"]}. Areas in {unit}. Whole plan with rooms (blue) and flags (red):'},
                   {'type': 'image_url', 'image_url': {'url': draw(P, g, flags=F, size=1024)}}]
        for fid, f in F.items():
            before = set(tools.cands)
            res = {'flag': flag_brief(P, g, fid, f, tools)}
            res['rule_repair'] = tools.rule_repair(f)
            if f.get('seed'):
                res['grow_room_from_seed'] = tools.grow_room_from_seed(*f['seed'])
            else:
                c = box(*f['bbox']).centroid
                res['grow_room_from_seed'] = tools.grow_room_from_seed(c.x, c.y)
            res['segment_region'] = tools.segment_region(f['bbox'])
            if f.get('room') and tools._room(f['room']):
                res['snap_room'] = tools.snap_room(f['room'])
                if f['check'] in ('C2', 'C4'):
                    res['split_room_by_walls'] = tools.split_room_by_walls(f['room'])
            tools.images = []
            new = {k: tools.cands[k] for k in tools.cands if k not in before}
            content += [{'type': 'text', 'text': json.dumps(res)},
                        {'type': 'image_url', 'image_url': {'url': draw(P, g, f['bbox'], cands=new, flags={fid: f}, size=512)}}]
        body = {'messages': [{'role': 'system', 'content': SYSTEM_C}, {'role': 'user', 'content': content}],
                'temperature': 0, 'max_tokens': 1500, 'response_format': {'type': 'json_object'}}
        try:
            resp = verda.chat(body, f'{tag}:C')
        except verda.CapReached:
            stats['errors'] += 1
            return g, [], {**stats, 'cap': True}, dismissed, flags
        stats['calls'] += 1
        stats['errors'] += int('error' in resp)
        result = _parse_json(verda.text_of(resp))
    else:
        sysmsg = SYSTEM + ('\nIn this run every flag is a confirmed error taken from a reference annotation (O-miss: a room is missing here; '
                           'O-false: this predicted room is not a room; O-area: this room outline is too large or too small). Repair them; do not dismiss.'
                           if arm == 'E' else '')
        msgs = [{'role': 'system', 'content': sysmsg}, {'role': 'user', 'content': first_message(P, g, F, tools, unit)}]
        for call in range(MAX_CALLS):
            body = {'messages': msgs, 'tools': tool_defs(), 'temperature': 0, 'max_tokens': 1500}
            if call == MAX_CALLS - 1:
                body['tool_choice'] = {'type': 'function', 'function': {'name': 'submit_edits'}}
            try:
                resp = verda.chat(body, f'{tag}:{arm}:{call}')
            except verda.CapReached:
                stats['errors'] += 1
                return g, [], {**stats, 'cap': True}, dismissed, flags
            stats['calls'] += 1
            if 'error' in resp:
                stats['errors'] += 1
                break
            m = resp['choices'][0]['message']
            tcs = m.get('tool_calls') or []
            if not tcs:
                result = _parse_json(m.get('content'))
                break
            msgs.append({'role': 'assistant', 'content': m.get('content') or '', 'tool_calls': tcs})
            done = False
            for i, tc in enumerate(tcs):
                name = tc['function']['name']
                try:
                    args = json.loads(tc['function'].get('arguments') or '{}')
                except json.JSONDecodeError:
                    args = {}
                if name == 'submit_edits':
                    result, done = args, True
                    out = {'ok': True}
                elif i >= MAX_TOOLS:
                    out = {'error': f'at most {MAX_TOOLS} tool calls per response'}
                else:
                    stats['tool_calls'] += 1
                    try:
                        if name == 'rule_repair':
                            fl = F.get(args.get('flag_id'))
                            out = tools.rule_repair(fl) if fl else {'error': 'unknown flag'}
                        else:
                            out = getattr(tools, name)(**args)
                    except Exception as e:
                        out = {'error': f'{type(e).__name__}: {e}'[:200]}
                msgs.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': json.dumps(out)})
            if done:
                break
            if tools.images:
                msgs.append({'role': 'user', 'content': sum(([{'type': 'text', 'text': t}, {'type': 'image_url', 'image_url': {'url': u}}]
                                                              for t, u in tools.images[-4:]), [])})
                tools.images = []
    result = result or {}
    ops = result.get('ops') or []
    dis = [F[d['flag_id']]['bbox'] for d in (result.get('dismiss') or []) if isinstance(d, dict) and d.get('flag_id') in F]
    stats['ops_proposed'] = len(ops)
    g_new, applied, rejected, judged = apply_ops(P, g, ops, tools, F)
    stats['ops_applied'], stats['ops_rejected'] = len(applied), len(rejected)
    new_dismissed = dismissed + [list(b) for b in dis] + judged
    stats['dismissed'] = len(dis)
    if arm == 'E':  # oracle flags are confirmed errors: dismissals never count, only fewer true errors does
        new_dismissed = dismissed
        flags1 = oracle_flags(page, g_new, nc, [])
    else:
        flags1 = pngonly_flags(P, g_new, orow, othr, new_dismissed)
    D1 = D_score(flags1)
    labels = [f'{arm}-{a}' for a in applied] + [f'{arm}-dismiss:{F[d["flag_id"]]["check"]}' for d in (result.get('dismiss') or [])
                                                if isinstance(d, dict) and d.get('flag_id') in F]
    if D1 < D0:
        stats['accepted'] = True
        g_new = copy.deepcopy(g_new)
        g_new['iteration'] = g['iteration'] + 1
        g_new['disagreement'] = D1
        g_new['log'] = g['log'] + [{'event': 'iteration', 'arm': arm, 'iteration': g['iteration'] + 1, 'flags': len(flags),
                                    'applied': labels, 'rejected': rejected, 'stats': stats}]
        return g_new, labels, stats, new_dismissed, flags
    return g, [], {**stats, 'reverted_ops': len(applied)}, dismissed, flags


def _job(args):
    dataset, split, page, gen, thr, other, othr, n_max, arm = args
    rows = read_predictions(cpu_arms.pred_path(gen, dataset, split))
    orow = read_predictions(cpu_arms.pred_path(other, dataset, split)).get(page['id'])
    gen_row = rows.get(page['id'])
    toks = text.load(dataset, page['id']) if text.path(dataset, page['id']).exists() else []
    P = C.Page(page, toks, dataset, C.CONFIG[dataset]['closing_r'])
    g = from_prediction_row(page, gen_row, dataset, thr, gen)
    nc = len(CLASSES[dataset])
    iters, snaps, dismissed, stopped = [], [], [], False
    for k in range(n_max + 1):
        rec, snap, _ = trace.record(P, page, g, orow, othr, toks, nc, k)
        snaps.append(snap)
        if k == n_max or stopped:
            rec['applied'], rec['vlm'] = [], None
            iters.append(rec)
            continue
        g2, labels, stats, dismissed2, _ = vlm_iteration(arm, P, page, g, gen_row, orow, othr, thr, dismissed, nc,
                                                         f'arm{arm}:{dataset}:{split}:{gen}:{page["id"]}:{k}')
        rec['applied'], rec['vlm'] = labels, stats
        iters.append(rec)
        snaps[-1]['applied'] = labels
        if stats.get('accepted'):
            g, dismissed = g2, dismissed2
        else:
            stopped = True  # monotone acceptance: an iteration that does not lower D stops this sheet
    iters[0]['text_only'] = trace.text_only_counts(page, toks)
    return page['id'], iters, snaps


def run(dataset, split, n_max, arm, gen):
    th = thresholds(dataset)
    other = OTHER.get(gen, 'eomt-finetuned')
    P = pages(dataset, split)
    jobs = [(dataset, split, p, gen, th[gen], other, th[other], n_max, arm) for p in P]
    spent0 = verda.spent()
    t = time.time()
    with Pool(WORKERS) as pool:
        out = pool.map(_job, jobs, chunksize=1)
    vlm = {}
    for _, it, _ in out:
        for r in it:
            for k2, v in (r.get('vlm') or {}).items():
                if isinstance(v, (int, float, bool)):
                    vlm.setdefault(r['n'], {}).setdefault(k2, 0)
                    vlm[r['n']][k2] += v
    extra = {'arm': arm, 'model': verda.MODEL, 'endpoint': verda.BASE, 'vlm_per_iteration': vlm,
             'usd': round(verda.spent() - spent0, 4), 'seconds': round(time.time() - t)}
    return trace.aggregate(dataset, split, f'arm{arm}-{gen}', other, P, out, n_max, extra=extra)


if __name__ == '__main__':
    ds, split, n, arm = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    for gen in sys.argv[5].split(','):
        run(ds, split, n, arm, gen)

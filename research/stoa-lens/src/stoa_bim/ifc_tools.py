"""Query tools over a generated IFC, for answering Junction questions with or without DeepSeek.

Everything is read from the written IFC with IfcOpenShell (spaces from their extruded profiles, elements by class,
fixtures by PredefinedType / ObjectType). model.json is used only for the pixel frame (metres <-> PNG pixels), so
regions can be given and shown in PNG pixel coordinates.

IfcIndex(ifc_path, model_json_path, png_path) exposes:
  summary()                           counts per element type, spaces, total space area, scale used
  list_spaces(name_contains, number_prefix, region)
  count_elements(element, region)     door | window | toilet | washbasin | bathtub | shower | stair | space
  sum_area(space_numbers)             total area of the named spaces
  view_region(region, overlay)        PNG crop with the IFC elements drawn on it (data URL)
  digest()                            the pre-digested text given to arm 2
"""
import base64
import io
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon, box

KIND = {'TOILETPAN': 'toilet', 'WASHHANDBASIN': 'washbasin', 'BATH': 'bathtub', 'SHOWER': 'shower', 'SINK': 'sink'}
ELEMENTS = ('door', 'window', 'toilet', 'washbasin', 'bathtub', 'shower', 'sink', 'stair', 'space')
OUTDOOR = re.compile(r'balk|balc|loggia|terras|terrazz|sitzplatz|veranda|patio|jardin|garten|giardino', re.I)
NOT_ROOM = re.compile(r'schacht|gaine|vide|void|luftraum|cavedio|vano tecnico|shaft', re.I)


def _profile(rep_items):
    """Footprint polygon (metres) of an extruded-area solid, with voids."""
    for it in rep_items:
        if it.is_a('IfcExtrudedAreaSolid'):
            a = it.SweptArea
            outer = [p.Coordinates[:2] for p in a.OuterCurve.Points]
            holes = [[p.Coordinates[:2] for p in h.Points] for h in (a.InnerCurves or [])] if a.is_a('IfcArbitraryProfileDefWithVoids') else []
            try:
                return Polygon(outer, holes).buffer(0)
            except Exception:
                return None
    return None


class IfcIndex:
    def __init__(self, ifc_path, model_json, png_path):
        import ifcopenshell
        f = ifcopenshell.open(str(ifc_path))
        frame = json.loads(Path(model_json).read_text())['frame']
        self.k = frame['m_per_px']
        self.scale_source = frame.get('scale_source', '')
        self.png_path = png_path
        self.crop = frame.get('crop')
        self.items = []  # {type, id, name, kind, poly (px)}
        for e in f.by_type('IfcProduct'):
            cls = e.is_a()
            if not e.Representation:
                continue
            body = [r for r in e.Representation.Representations if r.RepresentationIdentifier == 'Body']
            g = None
            for r in body:
                g = g or _profile(r.Items)
                if g is not None:
                    break
            if g is None or g.is_empty:
                continue
            t = None
            if cls == 'IfcDoor':
                t = 'door'
            elif cls == 'IfcWindow':
                t = 'window'
            elif cls == 'IfcSanitaryTerminal':
                t = KIND.get(str(e.PredefinedType), (e.ObjectType or 'fixture').lower())
            elif cls == 'IfcStair':
                t = 'stair'
            elif cls == 'IfcSpace':
                t = 'space'
            if t is None:
                continue
            gp = self._to_px(g)
            rec = {'type': t, 'id': e.GlobalId, 'poly_m': g, 'poly': gp}
            if t == 'space':
                num = e.Name or ''
                rec['number'] = None if re.fullmatch(r'S\d+', num) else num
                rec['name'] = e.LongName or ''
                rec['area_m2'] = round(g.area, 2)
            self.items.append(rec)

    def _to_px(self, g):
        from shapely.affinity import affine_transform
        return affine_transform(g, [1 / self.k, 0, 0, -1 / self.k, 0, 0])

    # ---- tools -------------------------------------------------------------------------------------------------
    def _in(self, rec, region):
        if not region:
            return True
        c = rec['poly'].representative_point()
        return box(*region).contains(c)

    def spaces(self):
        return [r for r in self.items if r['type'] == 'space']

    def summary(self):
        cnt = {t: sum(1 for r in self.items if r['type'] == t) for t in ELEMENTS}
        sp = self.spaces()
        return {'counts': cnt, 'spaces_numbered': sum(1 for s in sp if s['number']),
                'total_space_area_m2': round(sum(s['area_m2'] for s in sp), 2),
                'scale': f"{self.k:.5f} m per PNG pixel ({self.scale_source})",
                'png_plan_crop_px': self.crop,
                'note': 'Generated automatically from the drawing; elements can be missing or extra. Areas are net IfcSpace footprints.'}

    def _space_row(self, s):
        b = s['poly'].bounds
        return {'number': s['number'], 'name': s['name'], 'area_m2': s['area_m2'],
                'bbox_px': [int(b[0]), int(b[1]), int(b[2]), int(b[3])]}

    def list_spaces(self, name_contains=None, number_prefix=None, region=None, limit=150):
        out = []
        for s in self.spaces():
            if name_contains and name_contains.lower() not in s['name'].lower():
                continue
            if number_prefix and not (s['number'] or '').startswith(number_prefix):
                continue
            if not self._in(s, region):
                continue
            out.append(self._space_row(s))
        out.sort(key=lambda r: (r['number'] is None, r['number'] or '', r['bbox_px'][1]))
        return {'n': len(out), 'spaces': out[:limit], 'truncated': len(out) > limit}

    def count_elements(self, element, region=None):
        if element not in ELEMENTS:
            return {'error': f'element must be one of {list(ELEMENTS)}'}
        R = [r for r in self.items if r['type'] == element and self._in(r, region)]
        return {'element': element, 'count': len(R),
                'centres_px': [[int(r['poly'].centroid.x), int(r['poly'].centroid.y)] for r in R][:200]}

    def sum_area(self, space_numbers):
        by = {}
        for s in self.spaces():
            if s['number']:
                by.setdefault(s['number'], []).append(s)
        found, missing, total = [], [], 0.0
        for n in dict.fromkeys(space_numbers or []):
            if n in by:
                a = sum(s['area_m2'] for s in by[n])
                found.append({'number': n, 'area_m2': round(a, 2)})
                total += a
            else:
                missing.append(n)
        return {'total_m2': round(total, 2), 'found': found, 'missing': missing}

    def view_region(self, region=None, overlay='all', size=900):
        im = Image.open(self.png_path).convert('RGB')
        x0, y0, x1, y1 = [int(v) for v in (region or self.crop or (0, 0, im.width, im.height))]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(im.width, x1), min(im.height, y1)
        d = ImageDraw.Draw(im, 'RGBA')
        col = {'door': (220, 30, 30, 255), 'window': (20, 90, 230, 255), 'stair': (130, 60, 170, 255), 'space': (0, 150, 160, 255)}
        for r in self.items:
            t = r['type']
            if overlay not in ('all', t) and not (overlay == 'fixtures' and t in KIND.values()):
                continue
            c = col.get(t, (20, 150, 60, 255))
            polys = getattr(r['poly'], 'geoms', [r['poly']])
            for p in polys:
                d.line(list(p.exterior.coords), fill=c, width=1 if t == 'space' else 3)
            if t == 'space' and r['number']:
                pt = r['poly'].representative_point()
                d.text((pt.x - 10, pt.y - 6), r['number'], fill=c)
            elif t in KIND.values():
                pt = r['poly'].centroid
                d.text((pt.x - 4, pt.y - 6), t[0].upper(), fill=c)
        crop = im.crop((x0, y0, x1, y1))
        s = size / max(crop.width, crop.height)
        if s < 1:
            crop = crop.resize((int(crop.width * s), int(crop.height * s)), Image.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, format='JPEG', quality=80)
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()

    def digest(self):
        s = self.summary()
        lines = ['IFC model generated automatically from this drawing (may contain errors).',
                 f"Scale: {s['scale']}.",
                 'Element counts: ' + ', '.join(f'{k} {v}' for k, v in s['counts'].items()) + '.',
                 f"Total IfcSpace area: {s['total_space_area_m2']} m2.",
                 'Spaces (number | name | net area m2 | bbox in PNG pixels x0,y0,x1,y1):']
        for r in self.list_spaces(limit=400)['spaces']:
            lines.append(f"{r['number'] or '-'} | {r['name'] or '-'} | {r['area_m2']} | {','.join(map(str, r['bbox_px']))}")
        return '\n'.join(lines)

    # ---- the rule answer (no language model) -----------------------------------------------------------------
    def rule_answer(self, family, question):
        sp = self.spaces()
        norm = lambda t: re.sub(r'^\W+|\W+$', '', (t or '').strip().lower())
        if family == 'room':
            m = re.search(r'room (\S+?) \(', question)
            hit = [x for x in sp if m and x['number'] == m.group(1)]
            return round(sum(x['area_m2'] for x in hit), 2) if hit else None
        if family in ('label', 'count_rooms_label'):
            m = re.search(r"labelled '([^']+)'", question)
            hit = [x for x in sp if m and norm(x['name']) == norm(m.group(1))]
            if family == 'count_rooms_label':
                return len(hit)
            return round(sum(x['area_m2'] for x in hit), 2) if hit else None
        if family == 'floor':
            hit = [x for x in sp if x['number'] and not OUTDOOR.search(x['name']) and not NOT_ROOM.search(x['name'])]
            return round(sum(x['area_m2'] for x in hit), 2) if hit else None
        cls_of = lambda x: x['name'].split(':', 1)[0].strip() if ':' in x['name'] else ''
        labs_of = lambda x: set(x['name'].split(':', 1)[1].split()) if ':' in x['name'] else set()
        if family == 'count_bedrooms':
            return sum(1 for x in sp if cls_of(x) == 'bedroom')
        if family == 'count_kitchens':
            return sum(1 for x in sp if cls_of(x) == 'kitchen')
        from stoa_iterative.qa import LABELS_CUBICASA
        if family in LABELS_CUBICASA:
            return sum(1 for x in sp if labs_of(x) & LABELS_CUBICASA[family])
        if family == 'count_sinks':
            return sum(1 for r in self.items if r['type'] in ('sink', 'washbasin'))
        if family == 'count_showers_bathtubs':
            return sum(1 for r in self.items if r['type'] in ('shower', 'bathtub'))
        fam = {'count_doors': 'door', 'count_windows': 'window', 'count_toilets': 'toilet', 'count_washbasins': 'washbasin',
               'count_bathtubs': 'bathtub', 'count_showers': 'shower', 'count_sinks': 'sink'}.get(family)
        if fam:
            return sum(1 for r in self.items if r['type'] == fam)
        return None  # apartment area / apartment count: the IFC has no apartment grouping


TOOLS = [
    {'type': 'function', 'function': {'name': 'ifc_summary', 'description': 'Counts of every element type in the IFC model, number of spaces, total space area and the scale used.',
                                      'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'list_spaces', 'description': 'List IfcSpaces (rooms) with number, name, net area in m2 and bounding box in PNG pixels. All filters optional.',
                                      'parameters': {'type': 'object', 'properties': {
                                          'name_contains': {'type': 'string'}, 'number_prefix': {'type': 'string'},
                                          'region': {'type': 'array', 'items': {'type': 'number'}, 'description': '[x0,y0,x1,y1] in PNG pixels'}}}}},
    {'type': 'function', 'function': {'name': 'count_elements', 'description': 'Count IFC elements of one type, optionally inside a region of the PNG; returns their centres in PNG pixels.',
                                      'parameters': {'type': 'object', 'properties': {
                                          'element': {'type': 'string', 'enum': list(ELEMENTS)},
                                          'region': {'type': 'array', 'items': {'type': 'number'}, 'description': '[x0,y0,x1,y1] in PNG pixels'}},
                                          'required': ['element']}}},
    {'type': 'function', 'function': {'name': 'sum_area', 'description': 'Sum the net areas (m2) of the spaces with these room numbers.',
                                      'parameters': {'type': 'object', 'properties': {'space_numbers': {'type': 'array', 'items': {'type': 'string'}}},
                                                     'required': ['space_numbers']}}},
    {'type': 'function', 'function': {'name': 'view_region', 'description': 'Show a crop of the drawing with the IFC elements drawn over it (doors red, windows blue, spaces teal with their numbers, fixtures green with a letter), to check the model against the drawing.',
                                      'parameters': {'type': 'object', 'properties': {
                                          'region': {'type': 'array', 'items': {'type': 'number'}, 'description': '[x0,y0,x1,y1] in PNG pixels; omit for the whole plan'},
                                          'overlay': {'type': 'string', 'enum': ['all', 'door', 'window', 'space', 'fixtures', 'stair']}}}}},
    {'type': 'function', 'function': {'name': 'answer', 'description': 'Give the final answer.',
                                      'parameters': {'type': 'object', 'properties': {
                                          'status': {'type': 'string', 'enum': ['answered', 'needs_review']},
                                          'value': {'type': ['number', 'null']}, 'unit': {'type': 'string'}},
                                          'required': ['status', 'value']}}},
]


def call_tool(ix, name, args):
    """Returns (json result, image data URL or None)."""
    if name == 'ifc_summary':
        return ix.summary(), None
    if name == 'list_spaces':
        return ix.list_spaces(args.get('name_contains'), args.get('number_prefix'), args.get('region')), None
    if name == 'count_elements':
        return ix.count_elements(args.get('element'), args.get('region')), None
    if name == 'sum_area':
        return ix.sum_area(args.get('space_numbers')), None
    if name == 'view_region':
        return {'shown': True}, ix.view_region(args.get('region'), args.get('overlay') or 'all')
    return {'error': f'unknown tool {name}'}, None

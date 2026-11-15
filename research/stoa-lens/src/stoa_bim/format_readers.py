"""Read each generated file format back into the same query index as ifc_tools.IfcIndex, using only that file.

  DXF : ezdxf. Closed polylines on DOOR / WINDOW / STAIR / FIXTURE_<KIND> / ROOM_OUTLINE layers; room names and
        numbers from ROOM_NAME / ROOM_NUMBER text inside each outline. Units: metres.
  SVG : xml. <g class="door|window|fixture|stair">, <polygon class="space" data-number data-name>; areas from the
        pixel polygons and the root's data-m-per-px.
  PDF : PyMuPDF. Paths grouped by optional-content layer (Doors, Windows, Fixtures: <kind>, Stairs, Rooms); room labels
        from the page text inside each room; areas from the "scale 1:n" printed on the sheet (points -> metres).

Each index supports summary / list_spaces / count_elements / sum_area / digest / rule_answer like IfcIndex. Pixel
boxes (for list_spaces and view_region) use the PNG frame from model.json for DXF and SVG; the PDF has its own page
coordinates, so its boxes are in PDF points and it has no view_region.
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from shapely.geometry import Point, Polygon

from .ifc_tools import IfcIndex

NUM = re.compile(r'^\d+(?:[.\-/]\d+)*[a-z]?$', re.I)


def assign(rooms, texts, tol):
    """Each text to the room that contains it, else to the nearest room within tol; first text per room wins."""
    out = {}
    for p, t in texts:
        if not t:
            continue
        inside = [i for i, g in enumerate(rooms) if g.contains(p)]
        if inside:
            i = min(inside, key=lambda j: rooms[j].area)
        else:
            dist = [(g.distance(p), i) for i, g in enumerate(rooms)]
            if not dist or min(dist)[0] > tol:
                continue
            i = min(dist)[1]
        out.setdefault(i, t)
    return out


class FileIndex(IfcIndex):
    fmt = 'file'
    box_unit = 'PNG pixels'

    def _init_common(self, model_json, png_path):
        frame = json.loads(Path(model_json).read_text())['frame']
        self.k = frame['m_per_px']
        self.png_path = png_path
        self.crop = frame.get('crop')
        self.items = []

    def _space(self, poly_px, area_m2, number, name):
        return {'type': 'space', 'id': f'S{len(self.items)}', 'poly': poly_px, 'number': number or None, 'name': name or '', 'area_m2': round(area_m2, 2)}

    def summary(self):
        s = super().summary()
        s['scale'] = self.scale_source
        s['note'] = f'Read from the {self.fmt.upper()} file only. Generated automatically from the drawing; elements can be missing or extra.'
        return s

    def digest(self):
        s = self.summary()
        lines = [f'{self.fmt.upper()} file generated automatically from this drawing (may contain errors).',
                 f"Scale: {s['scale']}.",
                 'Element counts: ' + ', '.join(f'{k} {v}' for k, v in s['counts'].items()) + '.',
                 f"Total room area: {s['total_space_area_m2']} m2.",
                 f'Rooms (number | name | net area m2 | bbox in {self.box_unit} x0,y0,x1,y1):']
        for r in self.list_spaces(limit=400)['spaces']:
            lines.append(f"{r['number'] or '-'} | {r['name'] or '-'} | {r['area_m2']} | {','.join(map(str, r['bbox_px']))}")
        return '\n'.join(lines)


class DxfIndex(FileIndex):
    fmt = 'dxf'

    def __init__(self, dxf_path, model_json, png_path):
        import ezdxf
        self._init_common(model_json, png_path)
        self.scale_source = 'DXF units are metres'
        doc = ezdxf.readfile(str(dxf_path))
        msp = doc.modelspace()
        texts = {'ROOM_NAME': [], 'ROOM_NUMBER': []}
        for t in msp.query('TEXT'):
            if t.dxf.layer in texts:
                p = t.dxf.insert
                texts[t.dxf.layer].append((Point(p[0], p[1]), t.dxf.text))
        rooms = []
        for e in msp.query('LWPOLYLINE'):
            if not e.closed:
                continue
            lay = e.dxf.layer.upper()
            pts = [(p[0], p[1]) for p in e.get_points('xy')]
            if len(pts) < 3:
                continue
            g = Polygon(pts).buffer(0)
            t = {'DOOR': 'door', 'WINDOW': 'window', 'STAIR': 'stair'}.get(lay)
            if lay.startswith('FIXTURE_') and lay != 'FIXTURE_SYMBOL':
                t = lay[len('FIXTURE_'):].lower()
            if lay == 'ROOM_OUTLINE':
                rooms.append(g)
            elif t:
                self.items.append({'type': t, 'id': f'E{len(self.items)}', 'poly': self._to_px(g)})
        names, nums = assign(rooms, texts['ROOM_NAME'], 0.5), assign(rooms, texts['ROOM_NUMBER'], 0.5)
        for i, g in enumerate(rooms):
            self.items.append(self._space(self._to_px(g), g.area, nums.get(i), names.get(i, '')))


class SvgIndex(FileIndex):
    fmt = 'svg'

    def __init__(self, svg_path, model_json, png_path):
        self._init_common(model_json, png_path)
        root = ET.parse(str(svg_path)).getroot()
        ns = '{http://www.w3.org/2000/svg}'
        k = float(root.get('data-m-per-px') or 0) or None
        self.scale_source = f'data-m-per-px={k}' if k else 'no scale in the SVG'
        P = lambda s: Polygon([tuple(map(float, xy.split(','))) for xy in s.split()]).buffer(0)
        for el in root.iter():
            tag = el.tag.replace(ns, '')
            cls = el.get('class')
            if tag == 'polygon' and cls == 'space':
                g = P(el.get('points'))
                self.items.append(self._space(g, g.area * k * k if k else 0.0, el.get('data-number'), el.get('data-name')))
            elif tag == 'g' and cls in ('door', 'window', 'stair', 'fixture'):
                t = el.get('data-kind') if cls == 'fixture' else cls
                from shapely.geometry import MultiPoint
                xy = [tuple(map(float, v.split(','))) for c in el if c.get('points') for v in c.get('points').split()]
                hull = MultiPoint(xy).convex_hull if xy else Point(0, 0)
                self.items.append({'type': t or 'fixture', 'id': f'E{len(self.items)}', 'poly': hull if hull.geom_type == 'Polygon' else hull.buffer(1)})


class PdfIndex(FileIndex):
    fmt = 'pdf'
    box_unit = 'PDF points'

    def __init__(self, pdf_path, model_json, png_path):
        import pymupdf
        self._init_common(model_json, png_path)
        self.crop = None
        d = pymupdf.open(str(pdf_path))
        pg = d[0]
        txt = pg.get_text()
        m = re.search(r'scale 1:(\d+)', txt)
        n = int(m.group(1)) if m else None
        self.pt_per_m = 72 / 25.4 * 1000 / n if n else None
        self.scale_source = f'printed scale 1:{n}' if n else 'no printed scale'
        self.k = 1.0  # boxes stay in PDF points
        rooms = []
        for dr in pg.get_drawings():
            lay = dr.get('layer') or ''
            pts = []
            for it in dr['items']:
                if it[0] == 'l':
                    pts += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
                elif it[0] == 're':
                    r = it[1]
                    pts += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                elif it[0] == 'qu':
                    q = it[1]
                    pts += [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
                elif it[0] == 'c':
                    pts += [(it[1].x, it[1].y), (it[4].x, it[4].y)]
            if len(pts) < 3:
                continue
            g = Polygon(pts).buffer(0) if lay in ('Rooms',) else Polygon(pts).convex_hull
            t = {'Doors': 'door', 'Windows': 'window', 'Stairs': 'stair'}.get(lay)
            if lay.startswith('Fixtures: '):
                t = lay[len('Fixtures: '):]
            if lay == 'Rooms':
                rooms.append(g)
            elif t:
                self.items.append({'type': t, 'id': f'E{len(self.items)}', 'poly': g})
        labels = []
        for b in pg.get_text('dict')['blocks']:
            for ln in b.get('lines', []):
                for sp in ln['spans']:
                    o = sp['origin']
                    labels.append((Point(o[0] + 1, o[1] - 1), sp['text'].strip()))
        got = assign(rooms, labels, 3.0)
        for i, g in enumerate(rooms):
            lab = got.get(i, '')
            parts = lab.split()
            num = parts[-1] if parts and NUM.match(parts[-1]) else None
            name = ' '.join(parts[:-1] if num else parts)
            area = g.area / self.pt_per_m ** 2 if self.pt_per_m else 0.0
            self.items.append(self._space(g, area, num, name))

    def _to_px(self, g):
        return g

    def view_region(self, *a, **k):
        raise ValueError('the PDF has no link to PNG pixels')


def open_index(fmt, d, png_path):
    d = Path(d)
    if fmt == 'ifc':
        return IfcIndex(d / 'model.ifc', d / 'model.json', png_path)
    cls = {'dxf': DxfIndex, 'svg': SvgIndex, 'pdf': PdfIndex}[fmt]
    return cls(d / f'model.{fmt}', d / 'model.json', png_path)

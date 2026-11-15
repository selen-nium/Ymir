"""CubiCasa PNG -> model -> IFC / DXF / SVG (no reference used), for the question-answering comparison.

  spaces   : RF-DETR-Seg-L fine-tuned room instances (stoa_semseg prediction file, threshold chosen on validation);
             IfcSpace LongName = "<class>: <OCR labels inside the room>", e.g. "bath: KH"
  openings : counting-trials RF-DETR-L fine-tuned on CubiCasa (boxes on these same PNGs), thresholds chosen on validation
  fixtures : same detector: toilet -> TOILETPAN, sink -> SINK, bathtub -> BATH, shower -> SHOWER, sauna bench -> furnishing
  walls    : the classical wall mask (generate.wall_mask)
  scale    : CubiCasa sheets print no scale; a nominal 0.02 m per pixel is assumed and recorded (counts need no scale)

python -m stoa_bim.cubicasa_model validation
"""
import json
import sys
from pathlib import Path

from shapely.geometry import Polygon, box

from stoa_iterative import qa as QA
from stoa_iterative import text
from stoa_iterative.graph import from_prediction_row
from stoa_iterative.phase1 import thresholds
from stoa_semseg import cpu_arms
from stoa_semseg.common import CLASSES, pages
from stoa_semseg.evaluate import read_predictions

from . import generate as G
from . import model as M
from . import writers as Wr

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/png-to-bim/cubicasa'
DET = ROOT / 'data/counting-trials/predictions/rfdetr-finetuned/cubicasa/{split}.json'
REPORT = ROOT / 'data/counting-trials/report.json'
DET_CLASSES = ['door', 'window', 'toilet', 'sink', 'bathtub', 'shower', 'sauna_bench']
IFC_TYPE = {'toilet': 'TOILETPAN', 'sink': 'SINK', 'bathtub': 'BATH', 'shower': 'SHOWER'}
NOMINAL_M_PER_PX = 0.02


def build(page, room_row, room_thr, dets, det_thr, toks):
    page = {**page, 'm_per_px': NOMINAL_M_PER_PX}
    m = M.new_model(page, 'CubiCasa: RF-DETR-Seg rooms + RF-DETR openings/fixtures + classical walls')
    m['frame']['scale_source'] = f'assumed {NOMINAL_M_PER_PX} m/px (CubiCasa prints no scale)'
    try:
        gray, sat = G._gray(page)
        walls, _ = G.wall_mask(page, gray, sat, toks)
        for i, p in enumerate(G.polygons(walls)):
            m['walls'].append({'id': f'W{i}', 'polygon': M.px_to_m(m, list(p.exterior.coords)[:-1]),
                               'holes': [M.px_to_m(m, list(h.coords)[:-1]) for h in p.interiors], 'z0': 0.0, 'z1': M.DEFAULTS['storey_height']})
    except Exception as e:
        m['assumptions']['walls_error'] = str(e)[:200]
    graph = from_prediction_row(page, room_row, 'cubicasa', room_thr, 'rfdetr-seg-finetuned')
    rooms = [(r, Polygon(r['polygon']).buffer(0)) for r in graph['rooms']]
    labels = {i: [] for i in range(len(rooms))}
    for t in toks:
        parts = QA.label_parts(t)
        if not parts:
            continue
        c = box(*t['bbox']).centroid
        k = next((i for i, (_, g) in enumerate(rooms) if g.contains(c)), None)
        if k is not None:
            labels[k] += sorted(parts)
    for i, (r, g) in enumerate(rooms):
        if g.is_empty or g.geom_type != 'Polygon':
            g = max(getattr(g, 'geoms', [g]), key=lambda x: x.area)
        cls = CLASSES['cubicasa'][r.get('type', r.get('class', 8))]
        m['spaces'].append({'id': f'S{i}', 'polygon': M.px_to_m(m, list(g.exterior.coords)[:-1]), 'holes': [],
                            'number': None, 'name': f"{cls}: {' '.join(labels[i])}".strip(': ').strip()})
    nd = nw = nf = 0
    for c, s, *b in dets:
        name = DET_CLASSES[c]
        if s < det_thr[name]:
            continue
        pts = M.px_to_m(m, [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])])
        if name == 'door':
            m['doors'].append({'id': f'D{nd}', 'polygon': pts, 'z0': 0.0, 'z1': M.DEFAULTS['door_head'], 'source': f'detector {s:.2f}'})
            nd += 1
        elif name == 'window':
            m['windows'].append({'id': f'N{nw}', 'polygon': pts, 'z0': M.DEFAULTS['window_sill'], 'z1': M.DEFAULTS['window_head'], 'source': f'detector {s:.2f}'})
            nw += 1
        else:
            m['fixtures'].append({'id': f'F{nf}', 'kind': name, 'ifc_type': IFC_TYPE.get(name), 'polygon': pts,
                                  'z1': 0.45 if name == 'sauna_bench' else 0.9, 'source': f'detector {s:.2f}'})
            nf += 1
    return m


def main(split):
    rows = read_predictions(cpu_arms.pred_path('rfdetr-seg-finetuned', 'cubicasa', split))
    rthr = thresholds('cubicasa')['rfdetr-seg-finetuned']
    dets = json.loads(Path(str(DET).format(split=split)).read_text())['pages']
    dthr = json.loads(REPORT.read_text())['results']['rfdetr-finetuned/cubicasa']['thresholds']
    for p in pages('cubicasa', split):
        if not p.get('count_questions'):
            continue
        d = OUT / split / p['id'].replace('/', '__')
        if (d / 'model.ifc').exists():
            continue
        toks = text.load('cubicasa', p['id']) if text.path('cubicasa', p['id']).exists() else []
        m = build(p, rows.get(p['id']), rthr, dets.get(p['id'], []), dthr, toks)
        d.mkdir(parents=True, exist_ok=True)
        (d / 'model.json').write_text(json.dumps(m))
        Wr.to_ifc(m, d / 'model.ifc')
        Wr.to_dxf(m, d / 'model.dxf')
        Wr.to_svg(m, d / 'model.svg')
        print(p['id'], {k: len(m[k]) for k in ('walls', 'doors', 'windows', 'fixtures', 'spaces')}, flush=True)


if __name__ == '__main__':
    main(sys.argv[1])

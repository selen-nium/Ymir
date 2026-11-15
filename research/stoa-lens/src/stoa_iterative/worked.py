"""Assets for the worked-examples viewer on iterative-structuring.html (validation sheets, every repair arm, n = 0..4).

For each example and arm (D rules; B DeepSeek tools; C DeepSeek prompted JSON; E DeepSeek with oracle flags) the
per-iteration room snapshots saved by the trace are drawn and scored here:
  rooms     : the room graph (L1) at that iteration, simplified to 0.7 px, in view coordinates
  vs ref    : each room matched to the reference at IoU >= 0.5 (greedy); missed and extra rooms; area within 2%
  dxf       : the graph written by exports.to_dxf, read back with ezdxf and drawn
  ifc3d     : the graph written as IFC (one IfcSpace per room, 2.8 m) and tessellated to glTF
Writes experiments/iterative-structuring/worked/{data.json, <ex>/png.jpg, <ex>/<hash>_dxf.jpg, <ex>/<hash>.gltf.json}.
python -m stoa_iterative.worked
"""
import hashlib
import json
import tempfile
from pathlib import Path

from PIL import Image
from shapely.geometry import Polygon

from stoa_semseg.common import image_path, pages

from . import exports as X
from .graph import new_graph

ROOT = Path(__file__).resolve().parents[2]
TRACE = ROOT / 'data/iterative-structuring/trace/pages'
OUT = ROOT / 'experiments/iterative-structuring/worked'
MAXW = 900
ARMS = [('D', 'Rules (arm D)', ''), ('B', 'DeepSeek tools (arm B)', 'armB-'), ('C', 'DeepSeek JSON (arm C)', 'armC-'),
        ('E', 'DeepSeek, oracle flags (arm E)', 'armE-')]
EXAMPLES = [
    ('swiss', 'eomt-finetuned-zoom-z2-iou0.8', 'swiss/site745_bldg1634_floor7253', 'Rules add 3 errors; DeepSeek holds; the oracle reaches 0'),
    ('swiss', 'eomt-finetuned-zoom-z2-iou0.8', 'swiss/site540_bldg1240_floor51681', 'Only the oracle flags find the errors (8 to 3)'),
    ('swiss', 'eomt-finetuned-zoom-z2-iou0.8', 'swiss/site171_bldg350_floor1442', 'A busy sheet (74 reference rooms): the JSON arm ends best, F1 82 to 90.5'),
    ('swiss', 'raster2seq-base', 'swiss/site122_bldg9_floor554', 'A weak start (Raster2Seq): rules add 39 rooms but 13 more errors; DeepSeek adds fewer, better rooms'),
    ('cubicasa', 'rfdetr-seg-finetuned', 'cubicasa/colorful/13804', 'Scan: rules add false rooms (8 to 35 errors); DeepSeek tools dismiss them'),
    ('cubicasa', 'rfdetr-seg-finetuned', 'cubicasa/colorful/14418', 'Scan: the oracle fixes 2 errors; the other arms hold'),
]
GEN_NAME = {'eomt-finetuned-zoom-z2-iou0.8': 'EoMT-L + zoom', 'raster2seq-base': 'Raster2Seq', 'rfdetr-seg-finetuned': 'RF-DETR-Seg-L'}


def match(pred, ref):
    """Greedy IoU >= 0.5 matching. Returns (pairs [(pi, ri, iou, ape)], unmatched pred, unmatched ref)."""
    cand = []
    for i, p in enumerate(pred):
        for j, r in enumerate(ref):
            if p.intersects(r):
                u = p.union(r).area
                iou = p.intersection(r).area / u if u else 0
                if iou >= 0.5:
                    cand.append((iou, i, j))
    used_p, used_r, pairs = set(), set(), []
    for iou, i, j in sorted(cand, reverse=True):
        if i in used_p or j in used_r:
            continue
        used_p.add(i); used_r.add(j)
        pairs.append((i, j, round(iou, 3), round(abs(pred[i].area - ref[j].area) / ref[j].area, 4)))
    return pairs, [i for i in range(len(pred)) if i not in used_p], [j for j in range(len(ref)) if j not in used_r]


def dxf_image(path, crop_m, size):
    import ezdxf
    import io
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    doc = ezdxf.readfile(str(path))
    fig = plt.figure(figsize=(size[0] / 100, size[1] / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(doc.modelspace(), finalize=True)
    x0, y0, x1, y1 = crop_m
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect('equal', adjustable='datalim'); ax.axis('off')
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor='white')
    plt.close(fig)
    return Image.open(buf).convert('RGB')


def glb_state(page, rooms, dst):
    from stoa_bim import model as M
    from stoa_bim import writers as Wr
    from stoa_bim.iter_assets import glb_to_gltf
    pg = {**page, 'm_per_px': page.get('m_per_px') or 0.02}
    m = M.new_model(pg, 'room graph (L1)')
    for r in rooms:
        m['spaces'].append({'id': r['id'], 'polygon': M.px_to_m(m, r['polygon']), 'holes': [M.px_to_m(m, h) for h in r.get('holes') or []],
                            'number': None, 'name': None})
    with tempfile.TemporaryDirectory() as td:
        Wr.to_ifc(m, Path(td) / 'm.ifc')
        Wr.ifc_to_glb(Path(td) / 'm.ifc', Path(td) / 'm.glb', include_spaces=True)
        dst.write_text(json.dumps(glb_to_gltf(Path(td) / 'm.glb')))


def build():
    data = {'examples': []}
    for ds, gen, pid, blurb in EXAMPLES:
        page = next(p for p in pages(ds, 'validation') if p['id'] == pid)
        x0, y0, x1, y1 = page['crop']
        s = MAXW / max(x1 - x0, y1 - y0)
        size = (int((x1 - x0) * s), int((y1 - y0) * s))
        name = pid.replace('/', '__')
        od = OUT / name
        od.mkdir(parents=True, exist_ok=True)
        Image.open(image_path(page)).convert('RGB').crop((x0, y0, x1, y1)).resize(size, Image.LANCZOS).save(od / 'png.jpg', quality=78)
        V = lambda pts: [[round((x - x0) * s, 1), round((y - y0) * s, 1)] for x, y in pts]
        refs = [Polygon(r['polygon']).buffer(0) for r in page['rooms']]
        ref_v = [V(list((g if g.geom_type == 'Polygon' else max(g.geoms, key=lambda q: q.area)).exterior.coords)) for g in refs]
        states, arms = {}, []
        for key, label, pre in ARMS:
            tf = TRACE / f'{ds}-validation-{pre}{gen}' / f'{name}.json'
            if not tf.exists():
                continue
            tr = json.loads(tf.read_text())
            its = []
            for snap, rec in zip(tr['snapshots'], tr['iterations']):
                rooms = snap['rooms']
                h = hashlib.sha1(json.dumps([r['polygon'] for r in rooms]).encode()).hexdigest()[:10]
                if h not in states:
                    geo = [Polygon(r['polygon'], r.get('holes') or []).buffer(0) for r in rooms]
                    pairs, extra, missed = match(geo, refs)
                    simp = []
                    for g in geo:
                        g2 = g if g.geom_type == 'Polygon' else max(g.geoms, key=lambda q: q.area)
                        simp.append(V(list(g2.simplify(0.7).exterior.coords)))
                    ok2 = sum(1 for _, _, _, ape in pairs if ape <= 0.02)
                    tp = len(pairs)
                    P = tp / len(geo) if geo else 0
                    R = tp / len(refs) if refs else 0
                    states[h] = {'rooms': simp, 'matched': {str(i): [j, iou, ape] for i, j, iou, ape in pairs}, 'extra': extra, 'missed': missed,
                                 'metrics': {'rooms': len(geo), 'ref': len(refs), 'P': round(100 * P, 1), 'R': round(100 * R, 1),
                                             'F1': round(200 * P * R / (P + R), 1) if P + R else 0.0, 'within2': round(100 * ok2 / len(refs), 1) if refs else None}}
                    g = {**new_graph(page, ds), 'rooms': rooms}
                    k = page.get('m_per_px') or 1.0
                    with tempfile.TemporaryDirectory() as td:
                        X.to_dxf(g, Path(td) / 'g.dxf')
                        dxf_image(Path(td) / 'g.dxf', (x0 * k, -y1 * k, x1 * k, -y0 * k), size).save(od / f'{h}_dxf.jpg', quality=78)
                    glb_state(page, rooms, od / f'{h}.gltf.json')
                fl = [{'check': f['check'], 'why': f['why'], 'box': [round((f['bbox'][0] - x0) * s, 1), round((f['bbox'][1] - y0) * s, 1),
                                                                      round((f['bbox'][2] - x0) * s, 1), round((f['bbox'][3] - y0) * s, 1)]}
                      for f in snap.get('flags', [])[:60]]
                its.append({'n': rec['n'], 'hash': h, 'flags': fl, 'applied': rec.get('applied', []), 'errors': rec['errors'],
                            'area_q': rec.get('area_all'), 'vlm': rec.get('vlm')})
            arms.append({'key': key, 'label': label, 'iterations': its})
        data['examples'].append({'name': name, 'id': pid, 'dataset': 'Swiss' if ds == 'swiss' else 'CubiCasa', 'generator': GEN_NAME[gen],
                                 'blurb': blurb, 'size': size, 'ref': ref_v, 'arms': arms, 'states': states,
                                 'scale': 'true sheet scale' if page.get('m_per_px') else 'no scale (pixels)'})
        print(name, len(states), 'states', flush=True)
    (OUT / 'data.json').write_text(json.dumps(data, separators=(',', ':')))


if __name__ == '__main__':
    build()

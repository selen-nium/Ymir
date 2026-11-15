"""Assets for the iteration viewer: per example and iteration, the PNG, flags, and a rendering of every output file.

Writes experiments/png-to-bim/iterations/ (published next to the page): <ex>/png.jpg, <ex>/n<k>_{flags,ifc,svg,dxf,pdf}.jpg,
<ex>/m<hash>.glb (deduplicated across iterations), and data.json. Every rendering is made from the written file:
  ifc  : the IFC read back with IfcOpenShell and cut at 1.2 m (section.py)
  svg  : the SVG file as written (inlined into data.json, viewBox set to the plan)
  dxf  : the DXF read back with ezdxf and drawn by ezdxf's drawing add-on
  pdf  : page 1 of the PDF rasterised by PyMuPDF (the file itself is vector)
python -m stoa_bim.iter_assets <arm> <out_dir>
"""
import hashlib
import io
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from stoa_semseg.common import image_path, pages

from . import compare as C
from . import ifcref as R
from . import section as S

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / 'data/png-to-bim/examples'
MAXW = 900


def page_of(name):
    if name.startswith('resbim_'):
        n = int(name.split('_')[1])
        page, _ = R.page_for(n)
        return page, page['image_abs']
    pid = name.replace('__', '/')
    p = [x for x in pages('swiss', 'validation') if x['id'] == pid][0]
    return p, image_path(p)


def save(im, path, q=72):
    im.convert('RGB').save(path, format='JPEG', quality=q)


def dxf_image(dxf, crop_m, size):
    import ezdxf
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    doc = ezdxf.readfile(str(dxf))
    fig = plt.figure(figsize=(size[0] / 100, size[1] / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor('white')
    Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(doc.modelspace(), finalize=True)
    x0, y0, x1, y1 = crop_m
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect('equal')
    ax.axis('off')
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor='white')
    plt.close(fig)
    return Image.open(buf)


def pdf_image(pdf, width):
    import pymupdf
    d = pymupdf.open(str(pdf))
    pg = d[0]
    z = width / pg.rect.width
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z))
    return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)


def glb_to_gltf(path):
    import base64
    import struct
    b = Path(path).read_bytes()
    o, js, binc = 12, None, b''
    while o < len(b):
        cl, ct = struct.unpack('<II', b[o:o + 8])
        c = b[o + 8:o + 8 + cl]
        o += 8 + cl
        if ct == 0x4E4F534A:
            js = json.loads(c)
        elif ct == 0x004E4942:
            binc = c
    if js.get('buffers'):
        js['buffers'][0]['uri'] = 'data:application/octet-stream;base64,' + base64.b64encode(binc).decode()
    return js


def build(arm, out):
    out = Path(out)
    data = {'arm': arm, 'examples': []}
    for exdir in sorted((EX / arm).iterdir()):
        if not (exdir / 'curve.json').exists():
            continue
        name = exdir.name
        page, img = page_of(name)
        x0, y0, x1, y1 = page['crop']
        s = MAXW / max(x1 - x0, y1 - y0)
        size = (int((x1 - x0) * s), int((y1 - y0) * s))
        od = out / name
        od.mkdir(parents=True, exist_ok=True)
        rgb = Image.open(img).convert('RGB')
        save(rgb.crop((x0, y0, x1, y1)).resize(size, Image.LANCZOS), od / 'png.jpg')
        curve = json.loads((exdir / 'curve.json').read_text())
        iters = []
        seen = {}
        for rec in curve:
            n = rec['n']
            d = exdir / f'iter_{n}'
            m = json.loads((d / 'model.json').read_text())
            h = hashlib.sha1((d / 'model.json').read_bytes()).hexdigest()[:10]
            # flags: the written IFC read back and compared with the PNG
            ink, _ = S.rasterize(S.section(d / 'model.ifc'), m['frame'])
            png = C.png_ink(page, img)
            cov, flags, un, ph = C.compare(png, ink)
            if h not in seen:
                vis = np.asarray(rgb).copy()
                base = (vis * 0.45 + 255 * 0.55).astype(np.uint8)
                base[un] = (220, 50, 40)
                base[ph] = (20, 150, 70)
                save(Image.fromarray(base[y0:y1, x0:x1]).resize(size, Image.LANCZOS), od / f'{h}_flags.jpg')
                plan = np.full(png.shape + (3,), 255, np.uint8)
                plan[ink.astype(bool)] = (25, 30, 35)
                save(Image.fromarray(plan[y0:y1, x0:x1]).resize(size, Image.LANCZOS), od / f'{h}_ifc.jpg')
                k = m['frame']['m_per_px']
                crop_m = (x0 * k, -y1 * k, x1 * k, -y0 * k)
                save(dxf_image(d / 'model.dxf', crop_m, size), od / f'{h}_dxf.jpg')
                save(pdf_image(d / 'model.pdf', MAXW), od / f'{h}_pdf.jpg')
                (od / f'{h}.gltf.json').write_text(json.dumps(glb_to_gltf(d / 'model.glb')))  # .glb is not a served artifact type
                svg = (d / 'model.svg').read_text().replace('<svg ', '<svg preserveAspectRatio="xMidYMid meet" ', 1)
                svg = svg.replace(f'viewBox="0 0 {m["frame"]["width"]} {m["frame"]["height"]}"', f'viewBox="{x0} {y0} {x1 - x0} {y1 - y0}"')
                seen[h] = svg
            fl = [{'check': f['check'], 'why': f['why'], 'px': f['px'],
                   'box': [round((f['bbox'][0] - x0) * s, 1), round((f['bbox'][1] - y0) * s, 1), round((f['bbox'][2] - x0) * s, 1), round((f['bbox'][3] - y0) * s, 1)]}
                  for f in flags[:60]]
            iters.append({'n': n, 'hash': h, 'metrics': {k2: rec[k2] for k2 in ('explained', 'phantom', 'walls_iou', 'doors', 'windows', 'elements')},
                          'applied': rec.get('applied_to_reach_next', []), 'flags': fl, 'n_flags': len(flags),
                          'files_kb': {e: round((d / f'model.{e}').stat().st_size / 1024) for e in ('ifc', 'dxf', 'svg', 'pdf', 'glb')}})
        data['examples'].append({'name': name, 'dataset': 'ResBIM' if name.startswith('resbim') else 'Swiss', 'size': size,
                                 'iterations': iters, 'svgs': seen})
        print(name, 'done', flush=True)
    (out / 'data.json').write_text(json.dumps(data))
    return out


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2])

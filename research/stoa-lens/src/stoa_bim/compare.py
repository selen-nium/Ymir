"""PNG vs the IFC's plan view: where the drawing and the file disagree.

PNG ink: dark, unsaturated pixels inside the plan crop (the red dash-dot apartment outlines and coloured fills are
not building elements), with OCR text boxes removed (text is L2, not geometry).
File ink: section.rasterize of the IFC.
  explained = share of PNG ink within TOL px of file ink   (low  -> the file misses things that are drawn)
  phantom   = share of file ink farther than TOL px from PNG ink (high -> the file has things that are not drawn)
Unexplained and phantom components are returned as flagged regions (K1, K2).
"""
import cv2
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

TOL = 3.0  # px (about 5 cm at 1:100, 150 DPI)


def png_ink(page, image_path, tokens=()):
    im = np.asarray(Image.open(image_path).convert('RGB')).astype(np.int16)
    gray = im.mean(2)
    sat = im.max(2) - im.min(2)
    ink = (gray < 215) & (sat < 40)
    x0, y0, x1, y1 = page['crop']
    m = np.zeros(ink.shape, bool)
    m[y0:y1, x0:x1] = True
    ink &= m
    for t in tokens:
        a, b, c, d = [int(round(v)) for v in t['bbox']]
        ink[max(0, b - 2):d + 2, max(0, a - 2):c + 2] = False
    return ink


def compare(png, file_ink, tol=TOL, min_px=40):
    file_ink = file_ink.astype(bool)
    d_file = ndi.distance_transform_edt(~file_ink) if file_ink.any() else np.full(png.shape, 1e9)
    d_png = ndi.distance_transform_edt(~png) if png.any() else np.full(png.shape, 1e9)
    unexpl = png & (d_file > tol)
    phantom = file_ink & (d_png > tol)
    res = {'png_ink_px': int(png.sum()), 'file_ink_px': int(file_ink.sum()),
           'explained': round(100 * (1 - unexpl.sum() / max(1, png.sum())), 2),
           'phantom': round(100 * phantom.sum() / max(1, file_ink.sum()), 2)}
    flags = []
    for kind, mask in (('K1', unexpl), ('K2', phantom)):
        lab, n = ndi.label(cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8)))
        for i, sl in enumerate(ndi.find_objects(lab), 1):
            size = int((mask[sl] & (lab[sl] == i)).sum())
            if size >= min_px:
                flags.append({'check': kind, 'px': size, 'bbox': [sl[1].start, sl[0].start, sl[1].stop, sl[0].stop],
                              'why': ('drawn ink the file does not explain' if kind == 'K1' else 'file geometry with no ink in the drawing') + f' ({size} px)'})
    flags.sort(key=lambda f: -f['px'])
    return res, flags, unexpl, phantom

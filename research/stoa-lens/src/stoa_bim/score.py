"""Per-element scores of a generated model against the reference model (both in the same metre frame)."""
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .model import poly


def match(pred, ref, min_cover=0.5):
    """Greedy one-to-one match: overlap / smaller area >= min_cover (reference door and window rectangles are
    thinner than the wall band a detector returns). Returns tp, n_pred, n_ref."""
    A = [Polygon(x['polygon']).buffer(0) for x in pred]
    B = [Polygon(x['polygon']).buffer(0) for x in ref]
    pairs = sorted(((a.intersection(b).area / max(1e-12, min(a.area, b.area)), i, j)
                    for i, a in enumerate(A) for j, b in enumerate(B) if a.intersects(b)), reverse=True)
    ua, ub, tp = set(), set(), 0
    for c, i, j in pairs:
        if c < min_cover:
            break
        if i in ua or j in ub:
            continue
        ua.add(i)
        ub.add(j)
        tp += 1
    return tp, len(A), len(B)


def prf(tp, n_pred, n_ref):
    p = 100 * tp / n_pred if n_pred else 0.0
    r = 100 * tp / n_ref if n_ref else 0.0
    return {'tp': tp, 'pred': n_pred, 'ref': n_ref, 'precision': round(p, 1), 'recall': round(r, 1),
            'f1': round(2 * p * r / (p + r), 1) if p + r else 0.0}


def walls(pred, ref):
    A = unary_union([poly(w) for w in pred['walls']]).buffer(0)
    B = unary_union([poly(w) for w in ref['walls']]).buffer(0)
    inter = A.intersection(B).area
    return {'iou': round(100 * inter / max(1e-9, A.union(B).area), 1), 'precision': round(100 * inter / max(1e-9, A.area), 1),
            'recall': round(100 * inter / max(1e-9, B.area), 1), 'area_pred_m2': round(A.area, 2), 'area_ref_m2': round(B.area, 2)}


def score(pred, ref):
    return {'walls': walls(pred, ref), 'doors': prf(*match(pred['doors'], ref['doors'])),
            'windows': prf(*match(pred['windows'], ref['windows'])),
            'counts': {'doors': [len(pred['doors']), len(ref['doors'])], 'windows': [len(pred['windows']), len(ref['windows'])]}}

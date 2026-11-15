"""Read the drawing's scale from its printed scale bar, using OCR words.

A scale bar is a row of numbers that grow evenly with distance along the bar and carry a length unit
("0 1 2 5 m", "0' 20' 40' 80'", "0 10m 20m"). Grid labels (1 2 3 ...) and dimension strings are rows of numbers
too, so a row counts only if it has a unit and its numbers fit a straight line against their positions.
Returns metres per image pixel, or None. Nothing is guessed from the paper size or a "1:100" note alone:
an image has no reliable DPI.
"""
from __future__ import annotations

import re

import numpy as np

UNITS = {"m": 1.0, "'": 0.3048, "′": 0.3048, "ft": 0.3048, "cm": 0.01, "mm": 0.001}
NUMBER = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(mm|cm|m|ft|'|′)?$")


def read(words: list[dict]) -> dict | None:
    """words: [{box: [x0, y0, x1, y1], text}] in image pixels. Best scale-bar reading, or None."""
    items, unit_words = [], []
    for w in words:
        text = w["text"].strip().replace("’", "'")
        x0, y0, x1, y1 = w["box"]
        if text.lower() in UNITS:
            unit_words.append((text.lower(), (x0 + x1) / 2, (y0 + y1) / 2))
            continue
        m = NUMBER.match(text.lower())
        if m:
            items.append({"value": float(m.group(1).replace(",", ".")), "unit": m.group(2), "x": (x0 + x1) / 2, "y": (y0 + y1) / 2,
                          "h": y1 - y0, "text": text})
    best = None
    for seed in items:
        row = sorted((i for i in items if abs(i["y"] - seed["y"]) <= 0.6 * seed["h"] and 0.6 <= i["h"] / seed["h"] <= 1.6),
                     key=lambda i: i["x"])
        if len(row) < 3:
            continue
        values, xs = np.array([i["value"] for i in row]), np.array([i["x"] for i in row])
        if np.any(np.diff(values) <= 0) or xs[-1] - xs[0] < 4 * seed["h"]:
            continue
        units = {i["unit"] for i in row if i["unit"]}
        if not units:  # a separate unit word just past either end of the row ("0 1 2 5 m")
            near = [u for u, ux, uy in unit_words if abs(uy - seed["y"]) <= 2.5 * seed["h"] and xs[0] - 6 * seed["h"] <= ux <= xs[-1] + 6 * seed["h"]]
            units = set(near)
        if len(units) != 1:
            continue
        slope, intercept = np.polyfit(values, xs, 1)  # pixels per unit
        residual = np.abs(xs - (slope * values + intercept)).max()
        if slope <= 0 or residual > max(0.03 * (xs[-1] - xs[0]), 0.5 * seed["h"]):
            continue
        unit = units.pop()
        m_per_px = UNITS[unit] / slope
        if not 0.002 <= m_per_px <= 0.5:
            continue
        cand = {"m_per_px": float(m_per_px), "labels": [i["text"] for i in row], "unit": unit, "points": len(row),
                "fit_residual_px": round(float(residual), 1)}
        if best is None or (cand["points"], -cand["fit_residual_px"]) > (best["points"], -best["fit_residual_px"]):
            best = cand
    return best

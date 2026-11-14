"""Question routes. The planner picks the first route whose `match` returns a plan summary; the route then
runs its tools and returns the answer plus one summary per tool for the trace.

Add a question type by writing a Route and listing it in ROUTES (order = priority).
Answer payload (frontend contract): {status: 'answered'|'abstained', value, unit, text, citations, highlight, focus}
plus, for box counts, {confidence, review: {class, threshold, boxes: [{id, box, score}]}}.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .. import models
from .context import Context, NUM, cites, ctr, fmt, poly_box, printed_area, union_box

REVIEW_BELOW = 0.8  # same cutoff as frontend/shared/session.js: under it the user is asked to check the boxes


@dataclass
class Step:
    summary: str
    ms: int | None = None


@dataclass
class Outcome:
    answer: dict
    steps: dict[str, Step] = field(default_factory=dict)  # tool id → summary
    verify: Step = field(default_factory=lambda: Step("Single source"))
    final: str = ""


class Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.ms = max(1, round((time.perf_counter() - self.t) * 1000))


def answered(**o):
    o.setdefault("highlight", {})
    o["highlight"] = {"rooms": [], "walls": [], "openings": [], "symbols": [], **o["highlight"]}
    return {"status": "answered", **o}


def noun(cls: str, n: int) -> str:
    names = {"window": ("window", "windows"), "door": ("door", "doors"), "toilet_bowl": ("toilet", "toilets"),
             "sink": ("sink", "sinks"), "bathtub": ("bathtub", "bathtubs"), "shower": ("shower", "showers")}
    one, many = names.get(cls, (cls.replace("_", " "), cls.replace("_", " ") + "s"))
    return one if n == 1 else many


ROOM_TYPES = [
    (re.compile(r"k[uü]che|kitchen", re.I), re.compile(r"k[uü]che", re.I), "kitchen"),
    (re.compile(r"zimmer|bedroom|\broom\b(?!s)", re.I), re.compile(r"zimme", re.I), "room (Zimmer)"),
    (re.compile(r"korridor|corridor|hall", re.I), re.compile(r"ko[rm]+idor", re.I), "corridor"),
    (re.compile(r"reduit|storage", re.I), re.compile(r"reduit", re.I), "storage room"),
    (re.compile(r"treppen|stair", re.I), re.compile(r"treppen", re.I), "stairwell"),
]
SYMBOLS = [
    (re.compile(r"windows?|fenster|ikkuna", re.I), "window"),
    (re.compile(r"doors?|t[üu]ren?\b|ovi", re.I), "door"),
    (re.compile(r"toilets?|wc seats?", re.I), "toilet_bowl"),
    (re.compile(r"sinks?|wash ?basins?", re.I), "sink"),
]
COUNT = re.compile(r"how many|number of|count")


def room_type(ctx: Context):
    return next(((label, name) for ask, label, name in ROOM_TYPES if ask.search(ctx.question)), None)


class Route:
    tools: tuple[str, ...] = ()

    def match(self, ctx: Context) -> str | None:
        raise NotImplementedError

    def run(self, ctx: Context) -> Outcome:
        raise NotImplementedError


class SymbolCount(Route):
    """Count of a drawn symbol (windows, doors, fixtures) from a registered detector."""
    tools = ("det",)

    def match(self, ctx):
        if not COUNT.search(ctx.lower):
            return None
        self.cls = next((cls for pattern, cls in SYMBOLS if pattern.search(ctx.question)), None)
        self.detector = self.cls and models.detector_for(ctx.drawing, self.cls)
        return f"Count {noun(self.cls, 2)} → detector" if self.detector else None

    def run(self, ctx):
        with Timer() as t:
            result = self.detector.detect(ctx.image_path, ctx.drawing, [self.cls])
        counted = result.counted(self.cls)
        n, conf, threshold = len(counted), result.confidence_for(self.cls), result.thresholds.get(self.cls, 0.5)
        one = noun(self.cls, 1).capitalize()
        boxes = [d for d in result.detections if d.cls == self.cls]
        answer = answered(
            value=n, unit="count", confidence=conf,
            review={"class": self.cls, "threshold": threshold,
                    "boxes": [{"id": d.id, "box": list(d.box), "score": d.score} for d in boxes]},
            text=f"<b>{n}</b> {noun(self.cls, n)} found by the detector {cites(n)}." if n
            else f"No {noun(self.cls, 2)} found by the detector.",
            citations=[{"n": i + 1, "label": f"{one} · {round(d.score * 100)}%", "box": list(d.box)} for i, d in enumerate(counted)],
            focus=None,
        )
        return Outcome(answer, {"det": Step(f"{n} boxes ≥ {threshold}", t.ms)},
                       Step(f"Confidence {round(conf * 100)}% {'⚠' if conf < REVIEW_BELOW else '✓'}"), str(n))


class RoomArea(Route):
    """Area of a numbered room: the printed area first, the measured polygon as a cross-check."""
    tools = ("ocr", "geo", "bim")

    def match(self, ctx):
        m = re.search(r"\b(\d\.\d{2})\b", ctx.question)
        if not (m and re.search(r"area|big|size|m²|m2|large", ctx.lower) and ctx.texts):
            return None
        self.n = m.group(1)
        self.nt = next((t for t in ctx.texts if t["text"] == self.n), None)
        return f"Find room {self.n} → area" if self.nt else None

    def run(self, ctx):
        n, nt = self.n, self.nt
        with Timer() as t_ocr:
            at = ctx.printed_below(nt["box"])
        with Timer() as t_geo:
            room = ctx.room_at(ctr(nt["box"]))
            measured = ctx.room_area(room) if room else None
        printed = printed_area(at["text"]) if at else None
        if printed is None and measured is None:
            return Abstain().run(ctx)
        inner = bool(room) and room.get("polygon_basis") == "inner_faces"
        diff = abs(measured - printed) / printed if printed and measured else None
        name = (room or {}).get("label") or f"Room {n}"
        value = printed if printed is not None else measured
        where = "inside the drawn walls" if inner else "to wall centrelines"
        if printed is not None:
            text = f"<b>{fmt(printed)} m²</b>. Printed on the sheet for {name} <cite>1</cite>"
            text += f"; the model measures {fmt(measured)} m² {where} <cite>2</cite>." if measured is not None else "."
        else:
            text = f"About <b>{fmt(measured)} m²</b>, measured on the model <cite>2</cite>. No printed area found."
        answer = answered(
            value=round(value, 2), unit="m2", text=text,
            citations=[{"n": 1, "label": f"Text “{(at or nt)['text']}”", "text_id": (at or nt)["id"]}]
            + ([{"n": 2, "label": f"{name} polygon", "room_id": room["id"]}] if room else []),
            highlight={"rooms": [room["id"]] if room else []},
            focus=poly_box(room["polygon"]) if room else nt["box"],
        )
        return Outcome(answer, {
            "ocr": Step(f"“{n}” + “{at['text']}”" if at else f"“{n}” found", t_ocr.ms),
            "geo": Step(f"Polygon {fmt(measured)} m²" if measured is not None else "No closed polygon", t_geo.ms),
            "bim": Step(f"IfcSpace {room['id']}" if room else "No space", 1),
        }, Step(f"Δ {round(diff * 100)}% ({'printed vs drawn' if inner else 'net vs centreline'}) ✓" if diff is not None
                else "Single source"), f"{fmt(value)} m²")


class RoomTypeCount(Route):
    """Count of a room type from its printed labels; each label is cited."""
    tools = ("ocr", "geo")

    def match(self, ctx):
        self.type = room_type(ctx)
        return f"Count “{self.type[1]}” labels" if COUNT.search(ctx.lower) and self.type and ctx.texts else None

    def run(self, ctx):
        label, name = self.type
        with Timer() as t_ocr:
            hits = [t for t in ctx.texts if label.search(t["text"])]
        with Timer() as t_geo:
            rooms = list(dict.fromkeys(r["id"] for t in hits if (r := ctx.room_at(ctr(t["box"])))))
        n = len(hits)
        answer = answered(
            value=n, unit="count", text=f"<b>{n}</b> {name}{'' if n == 1 else 's'}, one label each {cites(n)}.",
            citations=[{"n": i + 1, "label": f"Text “{t['text']}”", "text_id": t["id"]} for i, t in enumerate(hits)],
            highlight={"rooms": rooms}, focus=union_box([t["box"] for t in hits]) if hits else None,
        )
        verify = "Labels ↔ rooms match ✓" if len(rooms) == n else f"{n - len(rooms)} label(s) in merged rooms"
        return Outcome(answer, {"ocr": Step(f"{n} matching labels", t_ocr.ms), "geo": Step(f"{len(rooms)} enclosing rooms", t_geo.ms)},
                       Step(verify), str(n))


class LargestRoom(Route):
    """Largest or smallest room: printed areas where available, else measured polygons."""
    tools = ("ocr", "geo", "bim")

    def match(self, ctx):
        return "Rank rooms by area" if re.search(r"largest|biggest|smallest", ctx.lower) else None

    def run(self, ctx):
        small = "smallest" in ctx.lower
        kind = room_type(ctx)
        pool = []
        with Timer() as t_ocr:
            for nt in ctx.texts:
                if NUM.match(nt["text"]) and (at := ctx.printed_below(nt["box"])):
                    room = ctx.room_at(ctr(nt["box"]))
                    if room and (not kind or kind[0].search(room.get("label") or "")):
                        pool.append({"nt": nt, "at": at, "v": printed_area(at["text"]), "room": room})
        if pool:
            best = sorted(pool, key=lambda x: x["v"], reverse=not small)[0]
            by_measure = sorted(pool, key=lambda x: ctx.room_area(x["room"]), reverse=not small)[0]
            name = best["room"].get("label") or best["nt"]["text"]
            answer = answered(
                value=best["v"], unit="m2",
                text=f"<b>{name}</b>, {fmt(best['v'])} m² <cite>1</cite>. Compared {len(pool)} rooms with printed areas <cite>2</cite>.",
                citations=[{"n": 1, "label": f"Text “{best['at']['text']}”", "text_id": best["at"]["id"]},
                           {"n": 2, "label": f"{name} polygon", "room_id": best["room"]["id"]}],
                highlight={"rooms": [best["room"]["id"]]}, focus=poly_box(best["room"]["polygon"]),
            )
            agree = by_measure is best
            return Outcome(answer, {
                "ocr": Step(f"{len(pool)} printed areas", t_ocr.ms),
                "geo": Step(f"Measured {fmt(ctx.room_area(best['room']))} m²", 1),
                "bim": Step(f"IfcSpace {best['room']['id']}", 1),
            }, Step("Printed rank = measured rank ✓" if agree else "Printed and measured ranks differ ⚠"), name)
        with Timer() as t_geo:
            rooms = sorted((r for r in ctx.rooms if ctx.room_area(r) > 2), key=ctx.room_area, reverse=not small)
        if not rooms:
            return Abstain().run(ctx)
        best = rooms[0]
        v = ctx.room_area(best)
        name = best.get("label")
        measured = ("Measured on the model's geometry." if ctx.drawing["scale"].get("source") == "model"
                    else "No printed areas on this sheet, so it depends on the scale you confirmed.")
        lead = f"<b>{name}</b> is the {'smallest' if small else 'largest'} room" if name else f"The highlighted room is the {'smallest' if small else 'largest'}"
        answer = answered(
            value=round(v, 2), unit="m2",
            text=f"{lead}, about <b>{fmt(v)} m²</b> <cite>1</cite>. {measured}",
            citations=[{"n": 1, "label": f"{name or 'Room'} polygon", "room_id": best["id"]}],
            highlight={"rooms": [best["id"]]}, focus=poly_box(best["polygon"]),
        )
        return Outcome(answer, {"ocr": Step("No area labels", t_ocr.ms), "geo": Step(f"{len(rooms)} rooms ranked", t_geo.ms),
                                "bim": Step(f"IfcSpace {best['id']}", 1)},
                       Step("Scale confirmed ✓" if ctx.scale_confirmed else "Scale unconfirmed ⚠"), f"{fmt(v)} m²")


class RoomCount(Route):
    tools = ("geo", "bim")

    def match(self, ctx):
        return "Count closed rooms" if "how many" in ctx.lower and "room" in ctx.lower else None

    def run(self, ctx):
        with Timer() as t:
            rooms = [r for r in ctx.rooms if ctx.room_area(r) > 2]
        n = len(rooms)
        answer = answered(value=n, unit="count", text=f"<b>{n}</b> enclosed rooms in the model <cite>1</cite>.",
                          citations=[{"n": 1, "label": f"{n} closed polygons"}],
                          highlight={"rooms": [r["id"] for r in rooms]}, focus=None)
        return Outcome(answer, {"geo": Step(f"{n} polygons > 2 m²", t.ms), "bim": Step(f"{n} IfcSpace", 1)},
                       Step("Counts agree ✓"), str(n))


class WallLength(Route):
    tools = ("geo", "bim")

    def match(self, ctx):
        return "Sum wall lengths" if "wall" in ctx.lower and re.search(r"long|length|total", ctx.lower) else None

    def run(self, ctx):
        with Timer() as t:
            walls = ctx.walls()
            total = sum(ctx.wall_length(w) for w in walls)
        answer = answered(value=round(total, 2), unit="m",
                          text=f"<b>{fmt(total)} m</b> of wall centreline across {len(walls)} walls <cite>1</cite>.",
                          citations=[{"n": 1, "label": f"{len(walls)} walls in the model"}],
                          highlight={"walls": [w["id"] for w in walls]}, focus=None)
        return Outcome(answer, {"geo": Step(f"{len(walls)} walls", t.ms), "bim": Step(f"{len(walls)} IfcWall", 1)},
                       Step("Scale confirmed ✓" if ctx.scale_confirmed else "Scale unconfirmed ⚠"), f"{fmt(total)} m")


class Abstain(Route):
    """Nothing found that supports an answer: say so rather than guess."""

    def match(self, ctx):
        return "No matching tool"

    def run(self, ctx):
        return Outcome({"status": "abstained", "citations": [],
                        "text": "I couldn’t find evidence for that on this sheet, so I won’t guess."},
                       {}, Step("No evidence → abstain"), "Abstained")


ROUTES = [SymbolCount, RoomArea, RoomTypeCount, LargestRoom, RoomCount, WallLength, Abstain]


def plan(ctx: Context) -> tuple[Route, str]:
    for cls in ROUTES:
        route = cls()
        summary = route.match(ctx)
        if summary:
            return route, summary
    raise AssertionError("Abstain always matches")


def suggestions(drawing: dict) -> list[str]:
    out = []
    for cls in ("window", "door"):
        if models.detector_for(drawing, cls):
            out.append(f"How many {noun(cls, 2)} are there?")
    rooms = drawing.get("rooms", [])
    labelled = [r for r in rooms if r.get("printed_area_m2") and re.search(r"\d\.\d{2}", r.get("label") or "")]
    if labelled:
        out.append(f"What is the area of {sorted(labelled, key=lambda r: r['label'])[0]['label']}?")
    if any(r.get("type") == "kitchen" for r in rooms):
        out.append("How many kitchens are there?")
    out.append("Which is the largest room?")
    if not drawing.get("texts"):
        out += ["How many rooms are there?", "How long are the walls in total?"]
    return out

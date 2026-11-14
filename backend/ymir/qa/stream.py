"""Answer a question as a stream of trace events (the contract frontend/shared/trace.js draws):

    {type:'node', id, name, tool, parents}      a step appears
    {type:'status', id, state, summary, ms}     running | done | skipped | failed
    {type:'answer', answer}                     last event
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator

from .. import models, settings
from .context import Context
from .routes import plan

log = logging.getLogger("ymir.qa")

TOOLS = ("det", "ocr", "geo", "bim", "vis")


def graph() -> list[dict]:
    """The agent graph: planner, tools, verifier. The Detector shows the first registered model's name."""
    det = models.detectors()[0].name if models.detectors() else "Detector"
    nodes = [
        ("q", "Question", "You", []),
        ("plan", "Planner", "Router", ["q"]),
        ("det", "Detector", det, ["plan"]),
        ("ocr", "Reader", "OCR index", ["plan"]),
        ("geo", "Geometry", "Shapely", ["plan"]),
        ("bim", "Model", "IFC query", ["plan"]),
        ("vis", "Vision", "Claude", ["plan"]),
        ("ver", "Verifier", "Cross-check", list(TOOLS)),
        ("ans", "Answer", "Cited", ["ver"]),
    ]
    return [{"type": "node", "id": i, "name": n, "tool": t, "parents": p} for i, n, t, p in nodes]


def status(node_id, state, summary=None, ms=None):
    ev = {"type": "status", "id": node_id, "state": state}
    if summary is not None:
        ev["summary"] = summary
    if ms is not None:
        ev["ms"] = ms
    return ev


def ask(ctx: Context, pace: float | None = None) -> Iterator[dict]:
    pace = settings.TRACE_PACE if pace is None else pace
    wait = (lambda k=1.0: time.sleep(pace * k)) if pace else (lambda k=1.0: None)
    nodes = {n["id"]: n for n in graph()}
    short = ctx.question if len(ctx.question) <= 38 else ctx.question[:36] + "…"

    yield nodes["q"]
    yield status("q", "running", f"“{short}”")
    wait()
    yield status("q", "done", f"“{short}”")
    yield nodes["plan"]
    yield status("plan", "running", "Planning…")
    t = time.perf_counter()
    route, summary = plan(ctx)
    wait(2)
    yield status("plan", "done", summary, max(1, round((time.perf_counter() - t) * 1000)))
    for tool in TOOLS:
        yield nodes[tool]
        yield status(tool, "running", "Working…") if tool in route.tools else status(tool, "skipped", "Not needed")
    try:
        outcome = route.run(ctx)
    except Exception:
        log.exception("Answering failed for %r on %s", ctx.question, ctx.drawing.get("id"))
        for tool in route.tools:
            yield status(tool, "failed", "Error")
        yield nodes["ver"]
        yield status("ver", "skipped", "Not reached")
        yield nodes["ans"]
        yield status("ans", "failed", "Error")
        yield {"type": "answer", "answer": {"status": "error", "citations": [],
                                            "text": "Something went wrong while answering this. Try another question."}}
        return
    for tool in route.tools:
        wait(1.5)
        step = outcome.steps.get(tool)
        yield status(tool, "done", step.summary, step.ms) if step else status(tool, "skipped", "Not used")
    wait()
    yield nodes["ver"]
    yield status("ver", "running", "Checking…")
    wait(2)
    yield status("ver", "done", outcome.verify.summary, outcome.verify.ms)
    yield nodes["ans"]
    yield status("ans", "running", "…")
    wait()
    yield status("ans", "done", outcome.final)
    yield {"type": "answer", "answer": outcome.answer}

"""Ymir API: drawings, projects, streamed answers and review labels.

    PYTHONPATH=backend uvicorn ymir.app:app --reload --port 8000

The Next.js frontend (frontend/) forwards /api/* here. Deployed, the API runs on Modal (deploy/modal_app.py)
and the frontend on Vercel.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import calibration, models, qa, settings
from .drawings import DrawingStore
from .reviews import Review, ReviewStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ymir")

app = FastAPI(title="Ymir", version="0.1.0")
store = DrawingStore()
reviews = ReviewStore()

if settings.CORS_ORIGINS or settings.CORS_ORIGIN_REGEX:
    app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_origin_regex=settings.CORS_ORIGIN_REGEX,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])


def drawing_or_404(drawing_id: str) -> dict:
    try:
        return store.raw(drawing_id)
    except KeyError:
        raise HTTPException(404, "Unknown drawing")


class Wall(BaseModel):
    id: str = Field(max_length=64)
    a: list[float] = Field(min_length=2, max_length=2)
    b: list[float] = Field(min_length=2, max_length=2)


class AskContext(BaseModel):
    """The user's Build decisions, so answers use the scale they confirmed and the walls they kept."""
    m_per_px: float | None = Field(default=None, gt=0)
    scale_confirmed: bool = False
    height_m: float = Field(default=2.7, gt=0, lt=50)
    removed_walls: list[str] = Field(default_factory=list, max_length=5000)
    added_walls: list[Wall] = Field(default_factory=list, max_length=5000)


class AskRequest(BaseModel):
    drawing_id: str
    question: str = Field(min_length=1, max_length=2000)
    context: AskContext = Field(default_factory=AskContext)


@app.get("/api/health")
def health():
    return {"status": "ok", "models": [d.name for d in models.detectors()]}


@app.get("/api/library")
def library():
    return store.library()


@app.get("/api/drawings/{drawing_id}")
def drawing(drawing_id: str):
    drawing_or_404(drawing_id)
    return store.payload(drawing_id)


@app.get("/api/drawings/{drawing_id}/trace")
def build_trace(drawing_id: str):
    drawing_or_404(drawing_id)
    return store.trace(drawing_id)


@app.get("/api/drawings/{drawing_id}/image")
def drawing_image(drawing_id: str):
    drawing_or_404(drawing_id)
    return FileResponse(store.image_path(drawing_id))


@app.get("/api/drawings/{drawing_id}/suggestions")
def suggestions(drawing_id: str):
    return qa.suggestions(drawing_or_404(drawing_id))


@app.get("/api/agents")
def agents():
    return qa.graph()


@app.get("/api/projects")
def projects():
    return store.projects()


@app.get("/api/projects/{project_id}")
def project(project_id: str):
    try:
        return store.project(project_id)
    except KeyError:
        raise HTTPException(404, "Unknown project")


@app.post("/api/ask")
def ask(req: AskRequest):
    d = drawing_or_404(req.drawing_id)
    c = req.context
    ctx = qa.Context(d, req.question.strip(), image_path=store.image_path(req.drawing_id), m_per_px=c.m_per_px,
                     scale_confirmed=c.scale_confirmed, height_m=c.height_m, removed_walls=set(c.removed_walls),
                     added_walls=[w.model_dump() for w in c.added_walls], reviews=reviews)
    lines = (json.dumps(ev, ensure_ascii=False) + "\n" for ev in qa.ask(ctx))
    return StreamingResponse(lines, media_type="application/x-ndjson")


@app.post("/api/drawings")
def upload(file: UploadFile = File(...)):
    """Build a new drawing from an uploaded plan image (PNG, JPEG or WebP)."""
    from .build import build_drawing
    raw = file.file.read(int(settings.MAX_UPLOAD_MB * 1024 * 1024) + 1)
    if len(raw) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Drawings up to {settings.MAX_UPLOAD_MB:g} MB")
    name = Path(file.filename or "Uploaded drawing").stem[:80] or "Uploaded drawing"
    try:
        built = build_drawing(raw, drawing_id="upload", title=name, dataset="Upload", kind="Uploaded image",
                              file=file.filename or "")
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception:
        log.exception("Building %s failed", file.filename)
        raise HTTPException(422, "This drawing could not be read. Try a PNG or JPEG of one floor plan.")
    drawing_id = store.save(built.payload, built.trace, built.image, built.ext)
    return next(e for e in store.library() if e["id"] == drawing_id)


@app.delete("/api/drawings/{drawing_id}", status_code=204)
def delete_drawing(drawing_id: str):
    """Remove an uploaded drawing. Its reviews stay in the database as training labels."""
    try:
        store.delete(drawing_id)
    except KeyError:
        raise HTTPException(404, "Unknown drawing")
    except PermissionError:
        raise HTTPException(403, "Sample drawings can't be deleted")
    return Response(status_code=204)


@app.post("/api/reviews")
def add_review(review: Review):
    d = drawing_or_404(str(review.drawing.get("id", "")))
    # The server says which detector was checked (the client can't), so reviews calibrate the right model.
    det = models.detector_for(d, review.class_)
    review.model.pop("detector", None)
    if det:
        review.model["detector"] = calibration.detector_info(det)
    return {"id": reviews.add(review)}


@app.get("/api/reviews")
def list_reviews(drawing_id: str | None = Query(default=None)):
    return reviews.list(drawing_id)


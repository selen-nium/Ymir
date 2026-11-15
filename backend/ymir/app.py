"""Ymir API: drawings, projects, streamed answers and review labels.

    PYTHONPATH=backend uvicorn ymir.app:app --reload --port 8000

The Next.js frontend (frontend/) forwards /api/* here. Deployed, the API runs on Modal (deploy/modal_app.py)
and the frontend on Vercel.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import calibration, models, qa, settings
from .drawings import DrawingStore
from .reviews import Review, ReviewStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ymir")

@asynccontextmanager
async def lifespan(app):
    # Hosting may start the job worker later, once this container is the only writer (deploy/modal_app.py).
    if getattr(app.state, "start_jobs", True):
        await asyncio.to_thread(jobs.start)
    yield
    await asyncio.to_thread(jobs.stop)
    for hook in getattr(app.state, "after_shutdown", []):
        await asyncio.to_thread(hook)

app = FastAPI(title="Ymir", version="0.2.0", lifespan=lifespan)
store = DrawingStore()
reviews = ReviewStore()
from .documents import DocumentStore, SheetSelection
from .lens_jobs import JobStore, RouteUnavailable
from .lens_readiness import readiness
documents = DocumentStore(store)
jobs = JobStore(store, documents)

@app.middleware("http")
async def gate_writes(request, call_next):
    """Hosting can hold writes, e.g. while a redeployed container waits for the previous one to stop.
    `app.state.write_gate` returns the reason writes must wait, or None. Added before CORS, so the
    browser can read the 503."""
    gate = getattr(request.app.state, "write_gate", None)
    if gate and request.method in ("POST", "DELETE"):
        reason = gate()
        if reason:
            return JSONResponse({"detail": reason}, status_code=503, headers={"Retry-After": "30"})
    return await call_next(request)


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
    model_config = {"extra": "forbid"}
    routing_mode: Literal["smart", "frontier"] = "smart"
    drawing_id: str
    request_id: str = Field(min_length=1,max_length=120)
    selected_drawing_ids: list[str] = Field(min_length=1,max_length=3)
    project_id: str = Field(min_length=1,max_length=120)
    conversation_id: str = Field(min_length=1,max_length=120)
    prior_job_ids: list[str] = Field(default_factory=list,max_length=30)
    question: str = Field(min_length=1, max_length=2000)
    context: AskContext = Field(default_factory=AskContext)


@app.get("/api/health")
def health():
    return {"status": "ok", "models": ["Ymir"], "lens": readiness(jobs.budget)}


@app.head("/api/library", include_in_schema=False)
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
    return [{'type':'node','id':'lens','name':'Ymir','tool':'Ymir','parents':[]},{'type':'status','id':'lens','state':'pending','summary':'Select sources and ask a question'}]


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
    for ident in req.selected_drawing_ids:drawing_or_404(ident)
    if req.drawing_id not in req.selected_drawing_ids:
        raise HTTPException(422, 'Active drawing must be in the selected scope')
    if not req.question.strip():raise HTTPException(422, 'A nonempty question is required')
    try:
        ident=jobs.submit(req.model_dump())
    except ValueError as e:raise HTTPException(409, str(e))
    except RouteUnavailable as e:raise HTTPException(503, str(e))
    return StreamingResponse(jobs.stream(ident),media_type='application/x-ndjson',headers={'X-Lens-Job':ident,'Cache-Control':'no-store','X-Accel-Buffering':'no'})


@app.get('/api/lens/status')
def lens_status():return readiness(jobs.budget)


@app.get('/api/lens/jobs')
def lens_jobs(project_id: str, conversation_id: str | None = None):
    return [dict(job, human_reviews=reviews.for_job(job['id'])) for job in jobs.list(project_id,conversation_id)]


@app.get('/api/lens/jobs/{ident}')
def lens_job(ident: str):
    try:return dict(jobs.lookup(ident), human_reviews=reviews.for_job(ident))
    except KeyError:raise HTTPException(404,'Unknown Ymir job')


@app.get('/api/lens/jobs/{ident}/events')
def lens_events(ident: str, after: int = Query(default=0,ge=0)):
    try:jobs.lookup(ident)
    except KeyError:raise HTTPException(404,'Unknown Ymir job')
    return StreamingResponse(jobs.stream(ident,after),media_type='application/x-ndjson',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})


@app.post('/api/lens/jobs/{ident}/cancel')
def lens_cancel(ident: str):
    try:return jobs.cancel(ident)
    except KeyError:raise HTTPException(404,'Unknown Ymir job')


@app.get('/api/lens/jobs/{ident}/artifacts')
def lens_artifacts(ident: str):
    try:return jobs.artifact_inventory(ident)
    except KeyError:raise HTTPException(404,'Unknown Ymir job')


@app.get('/api/lens/jobs/{ident}/artifacts/{name}')
def lens_artifact(ident: str, name: str):
    try:
        artifact=jobs.artifact(ident,name)
        return FileResponse(artifact) if isinstance(artifact,Path) else artifact
    except KeyError:raise HTTPException(404,'Unknown allowlisted artifact')


@app.post('/api/documents')
def upload_document(file: UploadFile = File(...), project_identity: str | None = Form(default=None)):
    raw=file.file.read(int(settings.MAX_UPLOAD_MB*1024*1024)+1)
    if len(raw)>settings.MAX_UPLOAD_MB*1024*1024:raise HTTPException(413,f'File exceeds the {settings.MAX_UPLOAD_MB:g} MB upload limit. Use a smaller file.')
    try:return documents.upload(raw,file.filename or 'drawing.png',project_identity)
    except ValueError as e:raise HTTPException(422,str(e))


@app.get('/api/documents/{ident}')
def document(ident: str):
    try:return documents.get(ident)
    except KeyError:raise HTTPException(404,'Unknown document')


@app.get('/api/documents/{ident}/original')
def original_document(ident: str):
    try:return FileResponse(documents.original(ident),filename=documents.get(ident)['filename'])
    except KeyError:raise HTTPException(404,'Unknown document')


@app.post('/api/documents/{ident}/sheets')
def prepare_sheets(ident: str, selection: SheetSelection):
    try:return documents.prepare(ident,selection)
    except KeyError:raise HTTPException(404,'Unknown document')
    except ValueError as e:raise HTTPException(422,str(e))


@app.post("/api/drawings")
def upload(file: UploadFile = File(...)):
    """Build a new drawing from an uploaded plan image (PNG, JPEG or WebP)."""
    uploaded=upload_document(file,project_identity=None)
    prepared=documents.prepare(uploaded['id'],SheetSelection())
    if not prepared['sheets']:raise HTTPException(422,'No supported sheets in this document')
    return prepared['sheets'][0]


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
    if review.job_id:
        try:job=jobs.lookup(review.job_id)
        except KeyError:raise HTTPException(404,'Unknown Ymir job')
        if job['status']!='completed':raise HTTPException(409,'Review requires a completed Ymir job')
        review_group=next((r for r in (job['answer'] or {}).get('reviews',[]) if r['sheet']==review.sheet_id and r['class']==review.class_),None)
        if not review_group or review.sheet_id!=d['id']:raise HTTPException(422,'Review sheet/class is outside this job')
        ids=[o.id for o in review.objects];proposal_ids=[o.proposal_id for o in review.objects if o.proposal_id]
        if len(set(ids))!=len(ids) or len(set(proposal_ids))!=len(proposal_ids):raise HTTPException(422,'Review identities must be unique')
        if any(o.class_!=review.class_ for o in review.objects):raise HTTPException(422,'Review objects must match the reviewed class')
        proposals={b['id']:b for b in review_group['boxes']}
        if any(o.proposal_id and o.proposal_id not in proposals for o in review.objects):raise HTTPException(422,'Unknown originating proposal')
        if review.user_count!=sum(o.status=='accepted' for o in review.objects):raise HTTPException(422,'Count does not match accepted objects')
        w,h=d['image']['size']
        if any(o.bbox_xyxy[2]>w or o.bbox_xyxy[3]>h for o in review.objects):raise HTTPException(422,'Review box outside source image')
        review.model={'value':review_group['count'],'boxes':{k:v['box'] for k,v in proposals.items()},'scores':{k:v['score'] for k,v in proposals.items()},'proposal_attribution':{k:{f:v.get(f) for f in ('model','confidence_kind')} for k,v in proposals.items()},'detector':{'name':'Ymir','version':'job:'+review.job_id},'attribution':'server','job_id':review.job_id,'sheet_id':review.sheet_id,'page_id':review_group['page_id']}
        review.drawing={'id':d['id'],'title':d['title'],'image_size':d['image']['size'],'dataset':d.get('source',{}).get('dataset')}
        try:ident=reviews.add(review)
        except ValueError as exc:raise HTTPException(409,str(exc))
        return {'id':ident,'record':reviews.get(ident)}
    det = models.detector_for(d, review.class_)
    review.model.pop("detector", None)
    if det:
        review.model["detector"] = calibration.detector_info(det)
    ident=reviews.add(review)
    return {"id": ident, "record": reviews.get(ident)}


@app.get("/api/reviews")
def list_reviews(drawing_id: str | None = Query(default=None)):
    return reviews.list(drawing_id)


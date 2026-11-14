"""Ymir API on Modal.

    git lfs pull                                   # the real weights, not LFS pointers
    YMIR_CORS_ORIGINS=https://ymir.vercel.app modal deploy deploy/modal_app.py

The API is served at https://<workspace>--ymir-web.modal.run (printed by `modal deploy`); set it as
YMIR_API_BASE on the Vercel project. Uploads and the review database live on the "ymir-data" volume.
"""
from __future__ import annotations

import os
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
WRITES = {"/api/reviews", "/api/drawings"}

if modal.is_local():
    weights = ROOT / "weights" / "wallnet.pt"
    if not weights.is_file() or weights.stat().st_size < 1_000_000:
        raise SystemExit("weights/wallnet.pt is missing or an LFS pointer: run `git lfs pull` before deploying.")

app = modal.App("ymir")
data = modal.Volume.from_name("ymir-data", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    # CPU-only torch keeps the image small; switch to the default index (and add gpu= below) for GPU models.
    .uv_pip_install("torch==2.15.0", "torchvision==0.29.1", index_url="https://download.pytorch.org/whl/cpu")
    .uv_pip_install(requirements=[str(ROOT / "requirements.txt")])
    .env({
        "PYTHONPATH": "/root/backend",
        "YMIR_DATA_DIR": "/data",
        "YMIR_WEIGHTS_DIR": "/root/weights",
        # read from your shell at deploy time
        "YMIR_CORS_ORIGINS": os.environ.get("YMIR_CORS_ORIGINS", ""),
        "YMIR_CORS_ORIGIN_REGEX": os.environ.get("YMIR_CORS_ORIGIN_REGEX", ""),
        "YMIR_MODELS": os.environ.get("YMIR_MODELS", "ymir.models.mock:MockDetector"),
    })
    .add_local_dir(ROOT / "weights", "/root/weights")
    .add_local_dir(ROOT / "data" / "samples", "/root/data/samples")
    .add_local_dir(ROOT / "backend", "/root/backend", ignore=["**/__pycache__"])
)


@app.function(
    image=image,
    volumes={"/data": data},
    cpu=2.0,
    memory=4096,            # torch + WallNet + OCR + detectors
    timeout=600,
    scaledown_window=300,   # stay warm 5 min after the last request; set min_containers=1 on demo day
    max_containers=1,       # one writer for the SQLite review database on the volume
)
@modal.concurrent(max_inputs=16)
@modal.asgi_app()
def web():
    from ymir.app import app as api

    @api.middleware("http")
    async def persist_writes(request, call_next):
        response = await call_next(request)
        if request.method == "POST" and request.url.path in WRITES and response.status_code < 400:
            await data.commit.aio()  # make new uploads and reviews durable right away
        return response

    return api

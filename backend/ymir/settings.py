"""Paths and switches, all overridable with environment variables (see README)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = ROOT / "data" / "samples"

# Writable state: uploaded drawings and the review database. Point at a persistent volume when deployed.
DATA_DIR = Path(os.environ.get("YMIR_DATA_DIR", ROOT / "var"))
UPLOADS_DIR = DATA_DIR / "drawings"
DB_PATH = Path(os.environ.get("YMIR_DB", DATA_DIR / "ymir.db"))

# Model weights (WallNet and any team detector weights).
WEIGHTS_DIR = Path(os.environ.get("YMIR_WEIGHTS_DIR", ROOT / "weights"))

# Comma-separated "module:factory" detectors to load; the first one supporting a class answers for it.
MODELS = os.environ.get("YMIR_MODELS", "ymir.models.mock:MockDetector")

# Seconds between trace stages so the agent diagram is readable; 0 streams as fast as the tools run.
TRACE_PACE = float(os.environ.get("YMIR_TRACE_PACE", "0.25"))

# Upload limit for new drawings.
MAX_UPLOAD_MB = float(os.environ.get("YMIR_MAX_UPLOAD_MB", "15"))

# Frontends on another origin (e.g. Vercel) allowed to call the API: exact origins, comma-separated,
# and/or a regex for preview deployments, e.g. https://ymir-[a-z0-9-]+\.vercel\.app
CORS_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("YMIR_CORS_ORIGINS", "").split(",") if o.strip()]
CORS_ORIGIN_REGEX = os.environ.get("YMIR_CORS_ORIGIN_REGEX") or None

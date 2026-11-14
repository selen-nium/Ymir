import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Settings are read at import: stream without pauses and write uploads/reviews to a throwaway folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ["YMIR_TRACE_PACE"] = "0"
os.environ["YMIR_DATA_DIR"] = tempfile.mkdtemp(prefix="ymir-test-")
os.environ["YMIR_AUDIT_RATE"] = "0"  # no random spot checks unless a test asks for them
os.environ.pop("YMIR_MODELS", None)
os.environ.pop("YMIR_DB", None)


@pytest.fixture(autouse=True)
def reviews(tmp_path, monkeypatch):
    """A review database per test: a saved review changes later answers (calibration, reuse of a person's check)."""
    from ymir.reviews import ReviewStore
    store = ReviewStore(tmp_path / "reviews.db")
    monkeypatch.setattr(importlib.import_module("ymir.app"), "reviews", store)
    return store

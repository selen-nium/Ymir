import os
import sys
import tempfile
from pathlib import Path

# Settings are read at import: stream without pauses and write uploads/reviews to a throwaway folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ["YMIR_TRACE_PACE"] = "0"
os.environ["YMIR_DATA_DIR"] = tempfile.mkdtemp(prefix="ymir-test-")
os.environ.pop("YMIR_MODELS", None)
os.environ.pop("YMIR_DB", None)

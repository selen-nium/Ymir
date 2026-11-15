import os
from pathlib import Path

ROOT = Path(os.environ.get('YMIR_STOA_ROOT',Path(__file__).resolve().parents[2])).resolve()
DATA = ROOT / "data"
RUNS = DATA / "runs"
REPORTS = ROOT / "reports"
ALLOWLIST = (
    "elorian/glimpse",
    "deepseek/deepseek-v4.1-flash-1017",
    "z-ai/glm-5.3-flash",
    "minimax/minimax-m3.1-flash",
)
CHEAP_MODELS = ALLOWLIST[1:]
CAP_USD = 5.0
KEY_FILE = Path.home() / ".config" / "stoa" / "openrouter.key"

def check_model(model: str) -> str:
    if model not in ALLOWLIST:
        raise ValueError(f"Model is outside the approved allowlist: {model}")
    return model

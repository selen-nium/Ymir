from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOME = ROOT / 'data/junction-verda-baseline-v1'
OUT = ROOT / 'reports/junction-verda-baseline-v1'
OLD = ROOT / 'data/junction-specialist'
SOURCE = ROOT / 'data/junction-dataset'
SEED = 'junction-verda-high-400-v1'
MODEL = 'deepseek-4-1-flash-1017'
CONFIG = {'model': MODEL, 'reasoning_effort': 'high', 'workers': 4,
          'max_calls': 8, 'max_tokens': 16384, 'deadline_s': 600,
          'shell_timeout_s': 45, 'overview_pixels': 1600, 'cap_usd': 20,
          'context_tokens': 1048576, 'max_tool_text_bytes': 24000,
          'max_image_bytes': 8_000_000}
SYSTEM = '''Answer the question from this drawing using the overview and original files.
You may use bash and read_file in an offline container. Originals are in /source;
/scratch is writable. Python, Pillow, NumPy, PyMuPDF, ezdxf and Tesseract are available.
Use read_file to view an image you create, including crops or rendered native files.
Return only final JSON with status, value, unit. Use status="answered", a nonnegative
integer value, and unit="count"; if unsupported use status="needs_review", value=null,
unit="count". Count totals do not establish instance-level grounding.'''


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def encoded(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode()


def digest(obj):
    return hashlib.sha256(encoded(obj)).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def write(path, obj, *, frozen=False):
    path = Path(path)
    data = encoded(obj)
    if path.exists() and frozen:
        if path.read_bytes() != data:
            raise ValueError(f'Refusing to change frozen artifact: {path}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_bytes(data)
    temp.replace(path)


def category(question, swiss=False):
    from stoa_junction.baseline_report import family
    label = family(question)
    return {'sinks': 'washbasins', 'baths': 'bathtubs'}.get(label, label) if swiss else label


def ordered(key):
    return hashlib.sha256((SEED + '|' + key).encode()).hexdigest()


def active_amendment(home=HOME):
    pointer = home / 'active-amendment.json'
    if pointer.exists():
        item = load(pointer)
        name = item['manifest']
        if Path(name).name != name or sha(home / name) != item['sha256']:
            raise ValueError('Active amendment pointer changed or escapes study')
        return load(home / name)
    path = home / 'pacing-amendment.json'
    return load(path) if path.exists() else None

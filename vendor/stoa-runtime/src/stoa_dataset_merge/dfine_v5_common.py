"""Pinned official D-FINE source and v5 data contracts; no remote actions."""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

SOURCE_COMMIT = '956d1709314c2c6a4df6f34de232054578a7449f'
DATASET_SHA = '9f85722ddbef0b880a13637e272b2583b4cd72a462a7c1c3d473ca3a96b5b16f'
WEIGHTS_SHA = 'f1d76b20c06610e03fa833ac9524337635e5e74204f22f4f8721174f19303d78'
OVERLAY_FILES = ('dfine_v5_common.py','dfine_v5_data.py','dfine_v5_loss.py','dfine_v5_runtime.py',
    'v2_balanced_sampling.py','v2_floorplan_augmentations.py','expanded_audit.py',
    'expanded_medium_evaluation.py','expanded_artifact_limits.py','v2_training_curves.py',
    'v2_fast_training_loader.py','expanded_export.py')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def overlay_digest():
    return hashlib.sha256(json.dumps({n:digest(Path(__file__).with_name(n)) for n in OVERLAY_FILES},sort_keys=True).encode()).hexdigest()


def source_files(root):
    root = Path(root)
    return {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '.git' not in p.parts and (p.suffix in ('.py', '.yml', '.txt') or p.name == 'LICENSE')}


def load_upstream(root):
    root = Path(root).resolve()
    if (root / '.git').exists():
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        if head != SOURCE_COMMIT:
            raise ValueError('D-FINE source commit differs from the pinned official release')
    existing = sys.modules.get('src')
    if existing is not None and Path(existing.__file__).resolve() != root / 'src/__init__.py':
        raise ValueError('Another package named src is already imported; use an isolated D-FINE process')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module('src')


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)

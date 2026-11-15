"""Bound persisted output so complete artifacts fit the reserved download."""
from __future__ import annotations

from pathlib import Path

# One GiB remains for an interrupted checkpoint and one for metadata beyond the
# 18 GiB guarded run; the downloader's independent absolute cap is 20 GiB.
RUN_ARTIFACT_LIMIT = 18 * 2**30
CHECKPOINT_FILE_LIMIT = 2**30


def used_bytes(root: Path) -> int:
    root = Path(root)
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Run outputs must not contain symlinks')
        if path.is_file():
            total += path.stat().st_size
    return total


def require_capacity(root: Path, additional_bytes: int, *, limit: int = RUN_ARTIFACT_LIMIT) -> None:
    if additional_bytes < 0 or used_bytes(root) + additional_bytes > limit:
        raise RuntimeError('Run artifact limit reached; preserve outputs and download before further work')


def verify_checkpoint(path: Path, *, limit: int = CHECKPOINT_FILE_LIMIT) -> None:
    if not path.is_file() or path.stat().st_size > limit:
        raise RuntimeError('Full checkpoint exceeds the recorded one GiB size bound')

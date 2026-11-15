"""Bounded child-process cleanup with explicit incomplete-cleanup receipts."""
from __future__ import annotations

import os
import signal
import subprocess


def _text(value):
    if value is None:
        return ''
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value)


def _combine(first, second):
    first, second = _text(first), _text(second)
    if first in second:
        return second
    if second in first:
        return first
    return first + second


def terminate_and_collect(process, prior=None, *, timeout=3):
    """Kill isolated group and collect output for at most three seconds.

    A stuck process can outlive this call. Never wait indefinitely for pipe EOF.
    """
    timeout = min(3.0, max(0.0, timeout))
    initial_out = _text(getattr(prior, 'output', None))
    initial_err = _text(getattr(prior, 'stderr', None))
    kill_error = None
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        kill_error = str(exc)
    try:
        out, err = process.communicate(timeout=timeout)
        return {'cleanup_confirmed': process.poll() is not None and not kill_error,
                'stdout': _combine(initial_out, out), 'stderr': _combine(initial_err, err),
                'cleanup_error': kill_error}
    except subprocess.TimeoutExpired as exc:
        return {'cleanup_confirmed': False, 'stdout': _combine(initial_out, exc.output),
                'stderr': _combine(initial_err, exc.stderr),
                'cleanup_error': kill_error or 'Process did not exit and close pipes within cleanup deadline'}

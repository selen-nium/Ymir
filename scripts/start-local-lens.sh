#!/usr/bin/env bash
# Local backend only; no cloud registration or deployment.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/backend:$PWD/vendor/stoa-runtime/src${PYTHONPATH:+:$PYTHONPATH}"
export YMIR_STOA_ROOT="${YMIR_STOA_ROOT:-$(dirname "$PWD")/stoa}"
exec .venv/bin/python -m uvicorn ymir.app:app --host 127.0.0.1 --port "${YMIR_PORT:-8011}"

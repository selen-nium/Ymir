"""Verda Inference client for DeepSeek V4.1 Flash (OpenAI-compatible), with a spending ledger and a response cache.

Endpoint (docs.verda.com/inference): base https://inference.verda.com/v1, model deepseek-4-1-flash-1017,
image input supported, tools supported, non-streaming requests time out after 60 s.
Price (verda.com/models/deepseek-v4-1-flash, 2026-11-15): $0.15 / 1M input, $0.006 cached, $0.60 / 1M output.

The key is read from DEEPSEEK_4_1_FLASH_API_KEY in .env and never logged. Every call reserves a conservative cost
before dispatch; the run stops before the ledger would pass VERDA_MAX_USD (default 10). Responses are cached by
request hash, so replays cost nothing. Failures are logged and never retried automatically.
"""
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = 'https://inference.verda.com/v1'
MODEL = 'deepseek-4-1-flash-1017'
PRICE_IN, PRICE_CACHED, PRICE_OUT = 0.15, 0.006, 0.60  # USD per 1M tokens
HOME = ROOT / 'data/iterative-structuring/verda'
LEDGER = HOME / 'ledger.jsonl'
CAP = float(os.environ.get('VERDA_MAX_USD', '10'))
RPM = float(os.environ.get('VERDA_RPM', '55'))  # the account's limit is about 60 requests per minute (measured)


def _pace():
    """Cross-process pacing: reserve the next send slot under a file lock, then sleep until it."""
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / '.pace', 'a+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            nxt = float(f.read().strip() or 0)
        except ValueError:
            nxt = 0.0
        slot = max(time.time(), nxt)
        f.seek(0)
        f.truncate()
        f.write(str(slot + 60.0 / RPM))
        fcntl.flock(f, fcntl.LOCK_UN)
    time.sleep(max(0.0, slot - time.time()))


class CapReached(RuntimeError):
    pass


def _key():
    for line in (ROOT / '.env').read_text().splitlines():
        if line.startswith('DEEPSEEK_4_1_FLASH_API_KEY='):
            return line.split('=', 1)[1].strip().strip('"\'')
    raise KeyError('DEEPSEEK_4_1_FLASH_API_KEY not in .env')


def cost(usage):
    u = usage or {}
    cached = (u.get('prompt_tokens_details') or {}).get('cached_tokens', 0) or 0
    return ((u.get('prompt_tokens', 0) - cached) * PRICE_IN + cached * PRICE_CACHED + u.get('completion_tokens', 0) * PRICE_OUT) / 1e6


def spent():
    if not LEDGER.exists():
        return 0.0
    return sum(json.loads(l).get('usd', 0) for l in LEDGER.read_text().splitlines() if l.strip())


def chat(body, tag, reserve_in=12000, reserve_out=1200, timeout=60):
    """One chat completion. Returns the response dict (cached if seen before). Raises CapReached."""
    body = {'model': MODEL, **body}
    h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    cache = HOME / 'cache' / f'{h}.json'
    if cache.exists():
        return json.loads(cache.read_text())
    HOME.mkdir(parents=True, exist_ok=True)
    reserve = (reserve_in * PRICE_IN + reserve_out * PRICE_OUT) / 1e6
    lock = open(HOME / '.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        if spent() + reserve > CAP:
            raise CapReached(f'Verda cap {CAP} USD reached ({spent():.4f} spent)')
        with open(LEDGER, 'a') as f:  # reservation first, settled below
            f.write(json.dumps({'id': h, 'tag': tag, 'state': 'reserved', 'usd': reserve, 'at': time.time()}) + '\n')
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
    import requests
    t = time.time()
    for attempt in range(10):  # only HTTP 429 (rejected before processing, not billed) is resent
        _pace()
        try:
            r = requests.post(BASE + '/chat/completions', json=body, timeout=timeout,
                              headers={'Authorization': f'Bearer {_key()}', 'Content-Type': 'application/json'})
            resp = r.json() if r.headers.get('content-type', '').startswith('application/json') else {'error': f'HTTP {r.status_code}'}
            if r.status_code == 429:
                wait = float((resp.get('error') or {}).get('retry_after_s', 2) if isinstance(resp.get('error'), dict) else 2)
                time.sleep(wait + 0.5 * attempt)
                resp = {'error': 'HTTP 429: rate limited after 10 attempts'}
                continue
            if r.status_code != 200:
                resp = {'error': f"HTTP {r.status_code}: {str(resp.get('error', resp))[:300]}"}
        except Exception as e:
            resp = {'error': f'{type(e).__name__}: {e}'[:300]}
        break
    resp['_seconds'] = round(time.time() - t, 2)
    usd = cost(resp.get('usage')) if 'error' not in resp else 0.0
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:  # settle: cancel the reservation and book the actual cost (timeouts keep the reservation: billing unknown)
        settle = -reserve + usd if not resp.get('error', '').startswith(('ReadTimeout', 'Timeout')) else 0.0
        with open(LEDGER, 'a') as f:
            f.write(json.dumps({'id': h, 'tag': tag, 'state': 'error' if 'error' in resp else 'settled', 'usd': settle,
                                'actual_usd': usd, 'usage': resp.get('usage'), 'error': resp.get('error'), 'at': time.time()}) + '\n')
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    if 'error' not in resp:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(resp))
    return resp


def text_of(resp):
    if 'error' in resp:
        return None
    return ((resp.get('choices') or [{}])[0].get('message') or {}).get('content')

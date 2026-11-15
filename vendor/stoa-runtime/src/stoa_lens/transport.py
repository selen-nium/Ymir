"""Deadline-bound, once-only OpenRouter transport for production Lens jobs.

Adapted from Stoa gemini_cv.py; provenance records the reviewed source hashes.
No experiment imports, process patches, provider fallback, or inference retries.
"""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from stoa_vista_specialists.specialists.detectors import PROMPT, SpecialistResponseError, _extract_object
from .budget import BudgetError
from .model_config import ARGON, DEEPSEEK, SOL, MAX_TOKENS, PROVIDERS, provider_home, resolve, validate
from .pacing import Pacer
from .streaming import stream_sdk

BASE = 'https://openrouter.ai/api/v1'
MAX_PRICE_AGE_SECONDS = 86400


def _env(root=None):
    return dict(os.environ)


def _write(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)


def _secrets(env):
    return sorted({value for key, value in env.items() if isinstance(value, str) and len(value) >= 8
                   and any(part in key.upper() for part in ('KEY', 'SECRET', 'TOKEN'))
                   and '://' not in value}, key=len, reverse=True)


def _redact(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, list): return [_redact(x, secrets) for x in value]
    if isinstance(value, dict): return {k: _redact(v, secrets) for k, v in value.items()}
    return value


def validate_catalog(record, model=None):
    try:
        stamp = datetime.fromisoformat(record['observed_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None: raise ValueError('Catalog timestamp must be timezone-aware')
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if not -60 <= age <= MAX_PRICE_AGE_SECONDS: raise ValueError('OpenRouter catalog is stale')
        meta, row = record['metadata'], record['selected']
        actual = meta['id']
        if actual not in PROVIDERS or (model is not None and model != actual):
            raise ValueError('Unapproved model catalog')
        if 'image' not in meta['architecture']['input_modalities']:
            raise ValueError('Model image capability unavailable')
        if row['tag'] != PROVIDERS[actual] or row.get('service_tier') is not None or row.get('status', 0) != 0:
            raise ValueError('Pinned standard endpoint unavailable')
        required = {'reasoning_effort', 'response_format', 'max_tokens'}
        if actual in (DEEPSEEK, SOL): required |= {'tools', 'tool_choice'}
        if not required <= set(row['supported_parameters']): raise ValueError('Required parameters unavailable')
        if int(row['max_completion_tokens']) < MAX_TOKENS or int(row['context_length']) <= MAX_TOKENS:
            raise ValueError('Model context/output allowance insufficient')
        for rates in [row['pricing'], *row['pricing'].get('overrides', [])]:
            for field in ('prompt', 'completion', 'image', 'request'):
                rate = float(rates.get(field, row['pricing'].get(field, 0)))
                if not math.isfinite(rate) or rate < 0 or (field in ('prompt', 'completion') and rate == 0):
                    raise ValueError('Invalid OpenRouter pricing')
    except (KeyError, TypeError, OverflowError, AttributeError) as exc:
        raise ValueError('Incomplete OpenRouter catalog') from exc
    return record


def _validate_pricing(data, models=None):
    for model in models or PROVIDERS:
        validate_catalog(data['models'][model], model)
    return data


def refresh_catalog(model, deadline=None):
    import requests
    if model not in PROVIDERS: raise ValueError('Unapproved model')
    remaining = 20 if deadline is None else min(20, deadline - time.monotonic())
    if remaining <= 0: raise TimeoutError('Deadline before catalog lookup')
    response = requests.get(BASE + '/models/' + model + '/endpoints', timeout=remaining, allow_redirects=False)
    if not response.ok: raise RuntimeError('OpenRouter catalog HTTP ' + str(response.status_code))
    meta = response.json()['data']
    selected = next((r for r in meta['endpoints'] if r.get('tag') == PROVIDERS[model]
                     and r.get('status', 0) == 0 and r.get('service_tier') is None), None)
    if selected is None: raise ValueError('Pinned OpenRouter endpoint unavailable')
    return validate_catalog({'observed_at': datetime.now(timezone.utc).isoformat(),
                             'metadata': meta, 'selected': selected,
                             'source': BASE + '/models/' + model + '/endpoints'}, model)


def catalog_path(home, model):
    if model not in PROVIDERS: raise ValueError('Unapproved model')
    return Path(home) / (model.replace('/', '--') + '.json')


def refresh_pricing(root=None, provider_home=None, deadline=None):
    """Read-only public metadata calls; publish each route independently."""
    home = Path(provider_home) if provider_home else globals()['provider_home']()
    home.mkdir(parents=True, exist_ok=True)
    models, errors = {}, {}
    for model in PROVIDERS:
        try:
            record = refresh_catalog(model, deadline)
            fd, name = tempfile.mkstemp(prefix='catalog-', suffix='.tmp', dir=home)
            try:
                with os.fdopen(fd, 'w') as handle:
                    json.dump(record, handle, indent=2, allow_nan=False); handle.flush(); os.fsync(handle.fileno())
                os.replace(name, catalog_path(home, model))
            finally:
                if os.path.exists(name): os.unlink(name)
            models[model] = record
        except (OSError, ValueError, RuntimeError) as exc:
            errors[model] = type(exc).__name__
    return {'models': models, 'errors': errors}


def reservation_amount(record):
    row = validate_catalog(record)['selected']; price = row['pricing']; rates = [price, *price.get('overrides', [])]
    prompt = max(float(r.get('prompt', price['prompt'])) for r in rates)
    output = max(float(r.get('completion', price['completion'])) for r in rates)
    # Full input context conservatively covers all image tokenization. Each image
    # and request fee is covered separately by the dispatch-specific calculation.
    return row, prompt, output, rates


def _partial_receipt(path):
    generation, usage = None, None
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            try: chunk = json.loads(line)
            except ValueError: continue
            if isinstance(chunk, dict):
                generation = generation or chunk.get('id')
                if isinstance(chunk.get('usage'), dict): usage = chunk['usage']
    return generation, usage


def _cost(usage):
    cost = usage.get('cost') if isinstance(usage, dict) else None
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0):
        raise ValueError('Invalid OpenRouter billing cost')
    return cost


def _filter_headers(headers):
    return {str(k): str(v) for k, v in (headers or {}).items()
            if str(k).lower().startswith(('x-ratelimit', 'retry-after', 'x-request-id'))}


def _admission(body, model):
    def strip(value):
        if isinstance(value, dict):
            if value.get('type') == 'image_url': return {'type': 'image_url'}
            return {k: strip(v) for k, v in value.items()}
        if isinstance(value, list): return [strip(v) for v in value]
        return value
    images = sum(sum(isinstance(b, dict) and b.get('type') == 'image_url' for b in m['content'])
                 for m in body['messages'] if isinstance(m.get('content'), list))
    return MAX_TOKENS + len(json.dumps(strip(body), ensure_ascii=False).encode()) + 256 * len(body['messages']) + images * (16384 if model == DEEPSEEK else 65536)


class OpenRouterTransport:
    def __init__(self, root, directory, budget, *, configuration=None, catalogs=None,
                 provider_home=None, requester=None, pacer=None, credential=None):
        self.configuration = validate(configuration if configuration is not None else resolve())
        self.model = self.configuration['orchestrator']['model']
        self.root = Path(root); self.directory = Path(directory).resolve(); self.directory.mkdir(parents=True, exist_ok=True)
        self.case_id = self.directory.name; self.budget = budget
        self.provider_home = Path(provider_home) if provider_home else globals()['provider_home']()
        self.catalogs = catalogs; self.requester = requester; self.credential = credential
        self.pacing = pacer or Pacer(self.provider_home / 'pacing.sqlite')
        self.requests = 0; self.receipts = []

    def _catalog(self, model, deadline):
        if self.catalogs is not None: return validate_catalog(self.catalogs[model], model)
        path = catalog_path(self.provider_home, model)
        try: return validate_catalog(json.loads(path.read_text()), model)
        except (OSError, ValueError):
            # Refresh only this model; an unavailable Frontier cannot block Smart.
            record = refresh_catalog(model, deadline)
            self.provider_home.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='catalog-', dir=self.provider_home)
            try:
                with os.fdopen(fd, 'w') as handle: json.dump(record, handle, allow_nan=False)
                os.replace(name, path)
            finally:
                if os.path.exists(name): os.unlink(name)
            return record

    def _reserve(self, model, amount, key, deadline):
        while True:
            try: return self.budget.reserve(model, self.case_id, amount, key=key)
            except BudgetError as exc:
                if str(exc) != 'Budget ceiling exhausted or reservations held': raise
                summary = self.budget.summary()
                active = [c for c in summary['charges'] if c['status'] == 'reserved']
                unknown = sum(c['reserved_usd'] for c in summary['charges'] if c['status'] == 'unknown')
                if not active or summary['cap_usd'] - summary['actual_usd'] - unknown + 1e-9 < amount: raise
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise TimeoutError('Deadline waiting for active reservations') from exc
                time.sleep(min(.1, remaining))

    def _call(self, model, messages, tools, deadline, ident, final=False, crop_sha256=None):
        if model not in (self.model, ARGON): raise ValueError('Model outside recorded route')
        if time.monotonic() >= deadline: raise TimeoutError('Deadline before OpenRouter call')
        secret = self.credential if self.credential is not None else os.environ.get('OPENROUTER_API_KEY')
        if not isinstance(secret, str) or not secret.strip(): raise ValueError('OpenRouter credential unavailable')
        record = self._catalog(model, deadline)
        body = {'model': model, 'reasoning_effort': 'high', 'max_tokens': MAX_TOKENS,
                'stream': True, 'stream_options': {'include_usage': True},
                'provider': {'only': [PROVIDERS[model]], 'allow_fallbacks': False, 'require_parameters': True}, 'messages': messages}
        if tools: body.update(tools=tools, tool_choice='none' if final else 'auto')
        if not tools and (final or model == ARGON): body['response_format'] = {'type': 'json_object'}
        path = self.directory / 'api' / ident; path.mkdir(parents=True, exist_ok=False)
        row, prompt, output, rates = reservation_amount(record)
        images = sum(sum(isinstance(b, dict) and b.get('type') == 'image_url' for b in m['content'])
                     for m in messages if isinstance(m.get('content'), list))
        price = row['pricing']
        extra = max(images * float(r.get('image', price.get('image', 0))) + float(r.get('request', price.get('request', 0))) for r in rates)
        amount = 1.15 * (int(row['context_length']) * prompt + MAX_TOKENS * output + extra) + .001
        key = 'lens-openrouter-' + hashlib.sha256(str(path).encode()).hexdigest()
        reservation = self._reserve(model, amount, key, deadline)
        sent = settled = False; ticket = None; response = None; headers = {}
        secrets = _secrets(os.environ) + [secret]
        try:
            _write(path / 'request.json', _redact(body, secrets))
            _write(path / 'reservation.json', {'id': reservation, 'amount_usd': amount, 'catalog': record, 'crop_sha256': crop_sha256})
            self.pacing.credential_scope = hashlib.sha256(secret.encode()).hexdigest()
            ticket = self.pacing.acquire(model, 8, 1000000, _admission(body, model), deadline)
            validate_catalog(record, model)
            if time.monotonic() >= deadline: raise TimeoutError('Deadline before OpenRouter dispatch')
            if self.requester is not None:
                sent = True; response = self.requester(body, deadline, path)
                if not isinstance(response, dict): raise ValueError('Invalid transport response')
                headers = _filter_headers(response.pop('_headers', {}))
            else:
                with tempfile.TemporaryDirectory(prefix='lens-openrouter-stream-') as temp:
                    chunks, head = Path(temp) / 'chunks.jsonl', Path(temp) / 'headers.json'
                    try:
                        sent = True; response = asyncio.run(stream_sdk(body, secret, deadline, chunks, head))
                    finally:
                        for source in (chunks, head):
                            if source.exists(): (path / source.name).write_text(_redact(source.read_text(), secrets))
                        if head.exists(): headers = _filter_headers(json.loads(head.read_text()))
            self.pacing.observe(model, headers)
            response = _redact(response, secrets); _write(path / 'response.json', response)
            usage = response.get('usage') or {}; cost = _cost(usage)
            generation = response.get('id') or _partial_receipt(path / 'chunks.jsonl')[0]
            try: self.budget.settle(reservation, cost, generation_id=generation, usage=usage)
            except BudgetError:
                settled = True
                raise
            settled = True
            receipt = {'model': model, 'actual_model': response.get('model') or model,
                       'provider': 'OpenRouter', 'endpoint': PROVIDERS[model], 'actual_provider': response.get('provider'),
                       'reasoning': 'high', 'max_tokens': MAX_TOKENS, 'usage': usage, 'cost_usd': cost,
                       'reservation_id': reservation, 'reservation_usd': amount, 'generation_id': generation,
                       'finish_reason': response.get('finish_reason')}
            _write(path / 'billing.json', receipt)
            self.receipts.append(receipt)
            if response.get('model') and response['model'] != model: raise ValueError('Provider returned an unexpected model')
            if response.get('finish_reason') in ('length', 'error', 'content_filter'): raise ValueError('Provider response incomplete')
            message = response.get('message')
            if not isinstance(message, dict) or message.get('role') != 'assistant': raise ValueError('Malformed assistant response')
            return message, receipt
        except BaseException as exc:
            status = getattr(exc, 'status_code', None); http = getattr(exc, 'response', None)
            if http is not None and hasattr(http, 'headers'): headers = _filter_headers(dict(http.headers))
            if status is not None: self.pacing.observe(model, headers, status)
            if not settled:
                generation, usage = _partial_receipt(path / 'chunks.jsonl')
                # Verified partial billing may arrive before a stream breaks. Missing
                # or invalid billing holds the reservation, including provider failures.
                try: cost = _cost(usage)
                except ValueError: cost = None
                try: self.budget.settle(reservation, 0 if not sent or status == 429 else cost, generation_id=generation, usage=usage)
                except BudgetError: settled = True; raise
            _write(path / 'failure.json', _redact({'error_type': type(exc).__name__, 'http_status': status,
                   'api_dispatch_started': sent, 'headers': headers,
                   'reservation_state': next(c['status'] for c in self.budget.summary()['charges'] if c['id'] == reservation)}, secrets))
            raise
        finally:
            if ticket is not None: self.pacing.finish(ticket)

    def complete(self, messages, tools, deadline, response_index, final=False):
        if type(response_index) is not int or response_index < 0: raise ValueError('Invalid response index')
        message, receipt = self._call(self.model, messages, tools, deadline, f'response-{response_index:02d}', final)
        self.last_response = receipt
        return message

    def argon(self):
        return Argon(self)


class Argon:
    """Atomic crop analysis: validated proposals only, never orchestration."""
    model = ARGON
    def __init__(self, transport): self.transport = transport; self.requests = 0
    def predict(self, image_path, reason, timeout):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Invalid Argon timeout')
        deadline = time.monotonic() + timeout; self.requests += 1; crop = Path(image_path).read_bytes()
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': PROMPT + reason},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(crop).decode()}}]}]
        message, receipt = self.transport._call(ARGON, messages, None, deadline, f'argon-{self.requests:02d}',
                                               crop_sha256=hashlib.sha256(crop).hexdigest())
        receipt = dict(receipt, text=message.get('content'), message=message)
        try: return _extract_object(receipt['text']), receipt
        except Exception as exc: raise SpecialistResponseError(type(exc).__name__, receipt) from exc
    def close(self): pass

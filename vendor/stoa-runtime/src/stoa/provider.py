"""The only OpenRouter transport. No model fallback list or automatic paid retries."""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .budget import Budget
from .config import KEY_FILE, RUNS, check_model

BASE = 'https://openrouter.ai/api/v1'

class ProviderError(RuntimeError):
    pass

def api_key():
    value = os.environ.get('OPENROUTER_API_KEY')
    if not value and KEY_FILE.is_file():
        value = KEY_FILE.read_text().strip()
    if not value:
        raise ProviderError('Configure OPENROUTER_API_KEY or the owner-only external key file.')
    return value

class OpenRouter:
    def __init__(self, budget: Budget):
        self.budget = budget
        self.catalog = {}
        self.http = requests.Session()

    def metadata(self, model):
        check_model(model)
        if model not in self.catalog:
            response = self.http.get(f'{BASE}/models/{model}/endpoints', timeout=30)
            response.raise_for_status()
            meta = response.json()['data']
            endpoints = [e for e in meta['endpoints'] if e.get('status', 0) == 0 and 'tools' in e.get('supported_parameters', [])]
            if not endpoints:
                raise ProviderError(f'No tool-capable provider for {model}')
            # Pin one provider and record its exact price; a different provider is a separate run.
            endpoints.sort(key=lambda e: float(e['pricing']['prompt']) + float(e['pricing']['completion']))
            if model == 'z-ai/glm-5.3-flash':
                # The catalog's cheaper Relace route returned an unbilled 404 on this account.
                endpoints.sort(key=lambda e: 0 if e.get('tag') == 'wafer/us' else 1)
            self.catalog[model] = (meta, endpoints[0])
            RUNS.mkdir(parents=True, exist_ok=True)
            (RUNS / ('provider-' + model.replace('/', '_') + '.json')).write_text(json.dumps({'fetched_at': datetime.now(timezone.utc).isoformat(), 'model': model, 'metadata': meta, 'selected': endpoints[0]}, indent=2))
        return self.catalog[model]

    def complete(self, model, messages, case_id, tools=None, max_tokens=8192, tool_choice='auto'):
        meta, endpoint = self.metadata(model)
        credential = api_key()  # Resolve locally before reserving any inference spend.
        price = endpoint['pricing']
        overrides = [price, *price.get('overrides', [])]
        prompt_rate = max(float(p.get('prompt', price['prompt'])) for p in overrides)
        output_rate = max(float(p.get('completion', price['completion'])) for p in overrides)
        context = endpoint.get('context_length') or meta.get('context_length')
        if not context:
            raise ProviderError('Cannot reserve safely without a provider context bound.')
        images = sum(1 for m in messages if isinstance(m.get('content'), list) for c in m['content'] if c.get('type') == 'image_url')
        reserve = 1.15 * (context*prompt_rate + max_tokens*output_rate + float(price.get('request', 0)) + images*float(price.get('image', 0))) + .001
        ident = self.budget.reserve(model, case_id, reserve)
        provider_tag = endpoint.get('tag') or endpoint['provider_name'].lower()
        body = {'model': model, 'messages': messages, 'max_tokens': max_tokens,
                'provider': {'only': [provider_tag], 'allow_fallbacks': False, 'require_parameters': True}}
        if getattr(self,'service_tier',None):
            body['service_tier']=self.service_tier
        if tools:
            body.update(tools=tools, tool_choice=tool_choice)
        start = time.monotonic()
        try:
            response = self.http.post(f'{BASE}/chat/completions', headers={'Authorization': 'Bearer '+credential, 'Content-Type': 'application/json'}, json=body, timeout=(15, 180))
        except requests.RequestException:
            self.budget.settle(ident, None)
            raise ProviderError('Transport failed; full reservation retained because billing is unknown.') from None
        try:
            data = response.json()
        except ValueError:
            self.budget.settle(ident, None)
            raise ProviderError('Invalid provider response; reservation retained.') from None
        usage = data.get('usage') or {}
        cost = usage.get('cost')
        if cost is None and response.status_code in (400, 401, 402, 403, 404, 422, 429) and not data.get('id'):
            cost = 0.0
        if cost is None and data.get('id'):
            # Read-only billing reconciliation; this does not issue another inference request.
            try:
                stats = self.http.get(f'{BASE}/generation', params={'id': data['id']}, headers={'Authorization': 'Bearer '+credential}, timeout=20)
                if stats.ok:
                    cost = stats.json()['data'].get('total_cost')
            except (requests.RequestException, KeyError, ValueError):
                pass
        self.budget.settle(ident, float(cost) if cost is not None else None, data.get('id'), usage)
        if not response.ok or data.get('error'):
            error = data.get('error', {})
            message = str(error.get('message', 'Provider request rejected'))[:350] if isinstance(error, dict) else 'Provider request rejected'
            message = re.sub(r'sk-or-v1-[A-Za-z0-9]+','[REDACTED]',message)
            # Never include headers, request bodies, credentials, or image bytes in errors.
            raise ProviderError(f'HTTP {response.status_code}: {message}')
        if not data.get('choices'):
            raise ProviderError('Provider returned no choices')
        return {'message': data['choices'][0]['message'], 'usage': usage, 'cost_usd': cost,
                'latency_s': time.monotonic()-start, 'provider': endpoint['provider_name'],
                'generation_id': data.get('id'), 'service_tier':data.get('service_tier'), 'finish_reason': data['choices'][0].get('finish_reason')}

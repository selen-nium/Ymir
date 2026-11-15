"""OpenAI SDK streaming with full reasoning/tool deltas and no automatic retries."""
import asyncio
import base64
import hashlib
import io
import json
import math
import time

from .accounting import cost
from .common import CONFIG, MODEL, digest, encoded, write


def merge_delta(target, delta):
    for key, value in delta.items():
        if value is None:
            continue
        if isinstance(value, str):
            target[key] = target.get(key, '') + value
        elif isinstance(value, dict):
            merge_delta(target.setdefault(key, {}), value)
        elif isinstance(value, list):
            values = target.setdefault(key, [])
            for item in value:
                if not isinstance(item, dict) or 'index' not in item:
                    values.append(item)
                    continue
                index = item['index']
                if type(index) is not int or not 0 <= index < 64:
                    raise ValueError('Invalid stream fragment index')
                while len(values) <= index:
                    values.append({})
                merge_delta(values[index], {k: v for k, v in item.items() if k != 'index'})
        else:
            target[key] = value


class Accumulator:
    def __init__(self):
        self.message = {'role': 'assistant'}
        self.usage, self.finish_reason = None, None

    def add(self, chunk):
        if chunk.get('usage') is not None:
            self.usage = chunk['usage']
        for choice in chunk.get('choices', []):
            if choice.get('index', 0) != 0:
                raise ValueError('Multiple streamed choices')
            delta = dict(choice.get('delta') or {})
            if delta.pop('role', 'assistant') != 'assistant':
                raise ValueError('Unexpected streamed role')
            merge_delta(self.message, delta)
            if choice.get('finish_reason'):
                self.finish_reason = choice['finish_reason']

    def result(self):
        self.message.setdefault('content', None)
        return {'message': self.message, 'usage': self.usage, 'finish_reason': self.finish_reason}


def usage_cost(provider, usage):
    if not isinstance(usage, dict):
        return None
    p, c = usage.get('prompt_tokens'), usage.get('completion_tokens')
    if type(p) is not int or type(c) is not int or min(p, c) < 0:
        return None
    return cost(provider, p, c)


def request(messages, tools=None, final=False, *, final_json_mode=False):
    body = {'model': MODEL, 'reasoning_effort': 'high', 'max_tokens': CONFIG['max_tokens'],
            'stream': True, 'stream_options': {'include_usage': True}, 'messages': messages}
    if tools:
        body['tools'] = tools
        body['tool_choice'] = 'none' if final else 'auto'
    if final and final_json_mode:
        body['response_format'] = {'type': 'json_object'}
    return body


def image_block(path):
    return {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()}}


def lossless_image_block(block):
    """Reduce PNG wire bytes, proving identical decoded RGBA pixels and dimensions."""
    from PIL import Image
    url = block['image_url']['url']
    prefix, payload = url.split(',', 1)
    original = base64.b64decode(payload)
    receipt = {'original_sha256': hashlib.sha256(original).hexdigest(), 'original_bytes': len(original)}
    if not prefix.startswith('data:image/png;'):
        return block, receipt | {'changed': False}
    with Image.open(io.BytesIO(original)) as image:
        pixels = image.convert('RGBA').tobytes()
        receipt.update(size=list(image.size), rgba_pixels_sha256=hashlib.sha256(pixels).hexdigest())
        if image.getexif().get(274, 1) != 1:
            return block, receipt | {'changed': False}
        candidate = image.copy()
        if candidate.mode == 'RGB':
            red, green, blue = candidate.split()
            if red.tobytes() == green.tobytes() == blue.tobytes():
                candidate = red
        buffer = io.BytesIO()
        candidate.save(buffer, format='PNG', optimize=True, compress_level=9)
        optimized = buffer.getvalue()
        if len(optimized) >= len(original):
            optimized = original
        with Image.open(io.BytesIO(optimized)) as decoded:
            if decoded.size != image.size or decoded.convert('RGBA').tobytes() != pixels:
                raise ValueError('Lossless transport pixel verification failed')
    result = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(optimized).decode()}}
    return result, receipt | {'changed': optimized != original, 'delivered_bytes': len(optimized),
                              'delivered_sha256': hashlib.sha256(optimized).hexdigest()}


def input_bound(body, provider=None):
    estimate = model_input_bound(body, provider)
    if provider and provider.get('pacing_wire_bytes_per_token'):
        # The gateway's TPM admission estimator can count encoded image payloads,
        # independently of billed vision tokens. This is only a pacing estimate;
        # every dollar reservation still covers the entire model context.
        estimate = max(estimate, math.ceil(len(encoded(body)) / provider['pacing_wire_bytes_per_token']))
    return estimate


def model_input_bound(body, provider=None):
    # UTF-8 bytes upper-bound ordinary BPE text. For images, reserve the entire
    # model context rather than assuming an undocumented image token formula.
    # Billing always uses the full context bound independently of pacing.
    if any('image_url' in str(m.get('content', '')) for m in body['messages']):
        if not provider or not provider.get('image_tokens_upper_bound'):
            return CONFIG['context_tokens'] - CONFIG['max_tokens']
        def stripped(obj):
            if isinstance(obj, dict):
                if obj.get('type') == 'image_url':
                    return {'type': 'image_url'}
                return {k: stripped(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [stripped(v) for v in obj]
            return obj
        images = sum(sum(isinstance(b, dict) and b.get('type') == 'image_url' for b in m['content'])
                     for m in body['messages'] if isinstance(m.get('content'), list))
        return min(CONFIG['context_tokens'] - CONFIG['max_tokens'],
                   len(encoded(stripped(body))) + 256 * len(body['messages']) + images * provider['image_tokens_upper_bound'])
    return min(CONFIG['context_tokens'] - CONFIG['max_tokens'], len(encoded(body)) + 256 * len(body['messages']))


async def stream_sdk(body, key, base_url, deadline, chunks_path, headers_path):
    from openai import AsyncOpenAI
    accumulator = Accumulator()
    remaining = deadline - time.monotonic()
    async with AsyncOpenAI(api_key=key, base_url=base_url, max_retries=0, timeout=max(.01, remaining)) as client:
        async with asyncio.timeout(remaining):
            raw = await client.chat.completions.with_raw_response.create(**body)
            write(headers_path, {k: v for k, v in raw.headers.items() if k.lower().startswith(('x-ratelimit', 'retry-after', 'x-request-id'))}, frozen=True)
            stream = raw.parse()
            try:
                with chunks_path.open('x') as log:
                    async for chunk in stream:
                        obj = chunk.model_dump(mode='json', exclude_none=True)
                        log.write(json.dumps(obj, ensure_ascii=False, allow_nan=False) + '\n')
                        log.flush()
                        accumulator.add(obj)
            finally:
                await stream.close()
    return accumulator.result()


def observe_headers(directory, provider, ledger, tokens):
    path = directory / 'headers.json'
    if not provider.get('pacing_use_provider_headers') or not path.exists():
        return
    headers = __import__('json').loads(path.read_text())
    remaining = headers.get('x-ratelimit-remaining-tokens')
    reset = headers.get('x-ratelimit-reset-tokens', '').removesuffix('s')
    if remaining is not None and reset:
        if float(remaining) < tokens:
            ledger.defer(max(0, float(reset)) + 1)


def call(body, *, key, provider, ledger, call_key, directory, deadline):
    directory.mkdir(parents=True, exist_ok=True)
    write(directory / 'request.json', body, frozen=True)
    # The endpoint cannot bill more input tokens than its complete context, even
    # if image tokenization or cached token reporting changes.
    reservation = cost(provider, CONFIG['context_tokens'], body['max_tokens'])
    pacing_id = ledger.pace(provider['rpm'], provider['tpm'], input_bound(body, provider) + body['max_tokens'], deadline)
    ledger.reserve(call_key, reservation)
    write(directory / 'reservation.json', {'key': call_key, 'request_sha256': digest(body),
                                         'reserved_usd': str(reservation)}, frozen=True)
    try:
        response = asyncio.run(stream_sdk(body, key, provider['base_url'], deadline,
                                         directory / 'chunks.jsonl', directory / 'headers.json'))
        write(directory / 'response.json', response, frozen=True)
        usage = response['usage']
        actual_tokens = None
        if usage_cost(provider, usage) is not None:
            actual_tokens = usage['prompt_tokens'] + usage['completion_tokens']
        if provider.get('pacing_refund_returned_usage') and actual_tokens is not None:
            ledger.settle_pacing(pacing_id, actual_tokens)
        observe_headers(directory, provider, ledger, actual_tokens or input_bound(body, provider) + body['max_tokens'])
        ledger.settle(call_key, usage_cost(provider, response['usage']))
        return response
    except BaseException as exc:
        # Preserve any received usage even if the stream/transport terminates.
        accumulator = Accumulator()
        chunks = directory / 'chunks.jsonl'
        if chunks.exists():
            for line in chunks.read_text().splitlines():
                accumulator.add(json.loads(line))
        body = getattr(exc, 'body', None)
        if body is not None:
            body = json.loads(json.dumps(body, default=str).replace(key, '[REDACTED]'))
        write(directory / 'failure.json', {'error_type': type(exc).__name__,
                                         'http_status': getattr(exc, 'status_code', None),
                                         'provider_error': body,
                                         'partial_response': accumulator.result()}, frozen=True)
        ledger.settle(call_key, usage_cost(provider, accumulator.usage))
        raise

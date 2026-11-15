"""Once-only SDK streaming, adapted from Stoa's reviewed stream accumulator.

Keep opaque reasoning signatures and encrypted blocks in assistant continuations.
Identity fields repeat in some providers' deltas; only payload fragments concatenate.
"""
import asyncio
import copy
import json
import time

_IDENTITIES = {'id', 'type', 'format', 'signature'}


class EmptyAssistantResponse(ValueError):
    """No assistant payload arrived; a bounded conversation turn can recover."""
    allow_tools = True


def require_assistant_payload(message):
    content = message.get('content') if isinstance(message, dict) else None
    if (not isinstance(message, dict) or
        not (isinstance(content, str) and content.strip()) and
        not message.get('tool_calls') and not message.get('refusal')):
        raise EmptyAssistantResponse('Provider stream returned no assistant text or tool calls')


def merge_delta(target, delta):
    for key, value in delta.items():
        if value is None:
            continue
        if isinstance(value, str):
            if key in _IDENTITIES:
                if key in target and target[key] != value:
                    raise ValueError('Conflicting stream identity: ' + key)
                target[key] = value
            else:
                target[key] = target.get(key, '') + value
        elif isinstance(value, dict):
            merge_delta(target.setdefault(key, {}), value)
        elif isinstance(value, list):
            values = target.setdefault(key, [])
            for item in value:
                if not isinstance(item, dict) or 'index' not in item:
                    values.append(copy.deepcopy(item))
                    continue
                index = item['index']
                if type(index) is not int or not 0 <= index < 64:
                    raise ValueError('Invalid stream fragment index')
                while len(values) <= index:
                    values.append({})
                # Tool indexes are wire positions; reasoning indexes are part of
                # the continuation contract and must also be sent back.
                part = dict(item)
                if key == 'tool_calls':
                    part.pop('index')
                merge_delta(values[index], part)
        else:
            target[key] = value


class Accumulator:
    def __init__(self):
        self.message = {'role': 'assistant'}
        self.usage = self.finish_reason = self.generation_id = self.model = self.provider = None

    def add(self, chunk):
        if chunk.get('error'):
            raise RuntimeError('Provider stream error')
        for key, attr in (('id', 'generation_id'), ('model', 'model'), ('provider', 'provider')):
            if chunk.get(key):
                old = getattr(self, attr)
                if old is not None and old != chunk[key]:
                    raise ValueError('Stream identity changed: ' + key)
                setattr(self, attr, chunk[key])
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
        return {'id': self.generation_id, 'model': self.model, 'provider': self.provider,
                'message': self.message, 'usage': self.usage, 'finish_reason': self.finish_reason}


async def stream_sdk(body, key, deadline, chunks_path, headers_path):
    from openai import AsyncOpenAI
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Deadline before stream')
    accumulator = Accumulator()
    sdk_body = {k: v for k, v in body.items() if k != 'provider'}
    sdk_body['extra_body'] = {'provider': body['provider']}
    async with AsyncOpenAI(api_key=key, base_url='https://openrouter.ai/api/v1',
                           max_retries=0, timeout=remaining) as client:
        async with asyncio.timeout(remaining):
            raw = await client.chat.completions.with_raw_response.create(**sdk_body)
            with headers_path.open('x') as handle:
                json.dump({k: v for k, v in raw.headers.items()
                           if k.lower().startswith(('x-ratelimit', 'retry-after', 'x-request-id'))}, handle)
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
    result = accumulator.result()
    require_assistant_payload(result['message'])
    return result

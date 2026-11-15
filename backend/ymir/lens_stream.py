"""Negotiate live SSE with the browser while retaining legacy NDJSON replay."""
import json
from fastapi.responses import StreamingResponse


async def sse(events):
    async for line in events:
        if not line.strip():
            yield ': keep-alive\n\n'
            continue
        event=json.loads(line)
        ident=f"id: {event['seq']}\n" if 'seq' in event else ''
        yield ident+'data: '+line.rstrip('\r\n')+'\n\n'


def event_response(events, accept='', headers=None):
    # Modal buffers ordinary response bodies. SSE is its documented real-time
    # transport; retain NDJSON for existing API clients and archived scripts.
    live='text/event-stream' in accept.lower()
    return StreamingResponse(sse(events) if live else events,
        media_type='text/event-stream' if live else 'application/x-ndjson',
        headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no',
                 'X-Content-Type-Options':'nosniff','Vary':'Accept',**(headers or {})})

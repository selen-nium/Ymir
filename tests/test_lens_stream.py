import asyncio
import json

from ymir.lens_stream import event_response, sse


def test_sse_emits_each_event_before_completion_and_keeps_heartbeats():
    async def verify():
        release=asyncio.Event()
        async def events():
            yield '{"type":"job","seq":1}\n'
            await release.wait()
            yield '\n'
            yield '{"type":"answer","seq":2}\n'
        output=sse(events())
        assert await anext(output)=='id: 1\ndata: {"type":"job","seq":1}\n\n'
        release.set()
        assert await anext(output)==': keep-alive\n\n'
        assert await anext(output)=='id: 2\ndata: {"type":"answer","seq":2}\n\n'
    asyncio.run(verify())


def test_negotiated_transport_keeps_legacy_ndjson_and_disables_transform():
    async def events():
        yield '{"type":"job","seq":1}\n'
    old=event_response(events())
    live=event_response(events(),'text/event-stream')
    assert old.media_type=='application/x-ndjson'
    assert live.media_type=='text/event-stream'
    assert 'no-transform' in live.headers['cache-control']
    assert live.headers['x-accel-buffering']=='no'

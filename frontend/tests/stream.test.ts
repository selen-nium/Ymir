import { afterEach, expect, test, vi } from 'vitest';
import { reconnectJob } from '@/lib/api';
import type { TraceEvent } from '@/lib/types';

function respond(lines: string[]) {
  const body = new ReadableStream<Uint8Array>({
    start(c) { for (const l of lines) c.enqueue(new TextEncoder().encode(l)); c.close(); },
  });
  vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })));
}
async function collect() {
  const events: TraceEvent[] = [];
  reconnectJob('job-1', (ev) => events.push(ev));
  await vi.waitFor(() => expect(events.some((e) => e.type === 'answer' || e.type === 'connection')).toBe(true));
  await new Promise((r) => setTimeout(r, 0));
  return events;
}
afterEach(() => vi.unstubAllGlobals());

test('a stream that closes before the answer reports a dropped connection', async () => {
  respond(['{"type":"job","status":"running","seq":1,"job_id":"job-1"}\n']);
  const events = await collect();
  expect(events.map((e) => e.type)).toEqual(['job', 'connection']);
});

test('a stream that ends with the answer reports nothing more', async () => {
  respond(['{"type":"job","status":"completed","seq":1}\n', '{"type":"answer","seq":2,"answer":{"status":"answered","text":"ok","citations":[]}}']);
  const events = await collect();
  expect(events.map((e) => e.type)).toEqual(['job', 'answer']);
});

test('SSE progress is delivered before the answer while the connection stays open', async () => {
  const events: TraceEvent[]=[];
  let stream!: ReadableStreamDefaultController<Uint8Array>;
  const body=new ReadableStream<Uint8Array>({start(controller){stream=controller;}});
  const fetcher=vi.fn<typeof fetch>().mockResolvedValue(new Response(body,{headers:{'Content-Type':'text/event-stream'}}));
  vi.stubGlobal('fetch',fetcher);
  const stop=reconnectJob('live',event=>events.push(event));
  const write=(value:string)=>stream.enqueue(new TextEncoder().encode(value));
  write(': keep-alive\r\n\r\nid: 1\r\ndata: {"type":"job","status":"running","job_id":"live","seq":1}\r\n\r\n');
  await vi.waitFor(()=>expect(events).toHaveLength(1));
  expect(events[0]).toMatchObject({type:'job',status:'running'});
  expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get('Accept')).toBe('text/event-stream');
  write('id: 2\ndata: {"type":"progress","activity":{"operation":"tool","phase":"started",');
  write('"tool":"predict_room_polygons"},"seq":2}\n\n');
  await vi.waitFor(()=>expect(events).toHaveLength(2));
  expect(events.some(e=>e.type==='answer')).toBe(false);
  write('data: {"type":"answer",\ndata: "answer":{"status":"answered","text":"done"}}\n\n');
  stream.close();
  await vi.waitFor(()=>expect(events).toHaveLength(3));
  expect(events.at(-1)).toMatchObject({type:'answer'});stop();
});

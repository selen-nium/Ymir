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

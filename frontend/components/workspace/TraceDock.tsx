'use client';
import { useEffect, useRef } from 'react';
import { Icon } from '@/components/Icon';
import { createTrace } from '@/lib/trace';
import { useWorkspace, useWs } from './context';

/** How the last question was answered: the agents and tools as a live flowchart. */
export function TraceDock() {
  const ws = useWorkspace();
  const chartEl = useRef<HTMLDivElement>(null);
  const open = useWs((s) => s.traceOpen), status = useWs((s) => s.traceStatus), busy = useWs((s) => s.traceBusy);

  useEffect(() => {
    const trace = createTrace(chartEl.current!);
    ws.actions.attachTrace(trace);
    return () => { ws.actions.detachTrace(); trace.dispose(); };
  }, [ws]);

  return (
    <section className={`trace${open ? '' : ' closed'}`}>
      <header onClick={ws.actions.toggleTrace}>
        <b>Trace</b>
        <span className="status">{busy && <span className="spin" />}{status}</span>
        <button aria-label={open ? 'Collapse the trace' : 'Expand the trace'}><Icon name="chev" /></button>
      </header>
      <div className="chartwrap"><div ref={chartEl} /></div>
    </section>
  );
}

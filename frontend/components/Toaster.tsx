'use client';
import { dismissToast, useToast } from '@/lib/toast';

export function Toaster() {
  const { msg, shown, action } = useToast();
  return (
    <div className={`toast${shown ? ' show' : ''}`} role="status" aria-live="polite">
      {msg}
      {action && shown && <button onClick={() => { action.run(); dismissToast(); }}>{action.label}</button>}
    </div>
  );
}

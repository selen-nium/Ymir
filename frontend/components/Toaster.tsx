'use client';
import { useToast } from '@/lib/toast';

export function Toaster() {
  const { msg, shown } = useToast();
  return <div className={`toast${shown ? ' show' : ''}`} role="status" aria-live="polite">{msg}</div>;
}

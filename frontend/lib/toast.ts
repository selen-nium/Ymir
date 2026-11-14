// One short message at a time, shown by <Toaster /> in the root layout.
import { create } from 'zustand';

interface ToastState { msg: string; shown: boolean; seq: number }
export const useToast = create<ToastState>(() => ({ msg: '', shown: false, seq: 0 }));

let timer: ReturnType<typeof setTimeout> | undefined;
export function toast(msg: string) {
  useToast.setState((s) => ({ msg, shown: true, seq: s.seq + 1 }));
  clearTimeout(timer);
  timer = setTimeout(() => useToast.setState({ shown: false }), 2400);
}

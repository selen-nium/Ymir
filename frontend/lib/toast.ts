// One short message at a time, shown by <Toaster /> in the root layout, with an optional action (Undo).
import { create } from 'zustand';

export interface ToastAction { label: string; run: () => void }
interface ToastState { msg: string; shown: boolean; action: ToastAction | null }
export const useToast = create<ToastState>(() => ({ msg: '', shown: false, action: null }));

let timer: ReturnType<typeof setTimeout> | undefined;
export function toast(msg: string, action: ToastAction | null = null) {
  useToast.setState({ msg, shown: true, action });
  clearTimeout(timer);
  timer = setTimeout(() => useToast.setState({ shown: false }), action ? 5000 : 2400);
}
export const dismissToast = () => { clearTimeout(timer); useToast.setState({ shown: false }); };

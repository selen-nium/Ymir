'use client';
import { createContext, useContext, useSyncExternalStore } from 'react';
import { useStore } from 'zustand';
import type { Workspace, WorkspaceState } from '@/lib/workspace';

export const WorkspaceContext = createContext<Workspace | null>(null);

export function useWorkspace(): Workspace {
  const ws = useContext(WorkspaceContext);
  if (!ws) throw new Error('useWorkspace outside <Workspace>');
  return ws;
}
/** A slice of the workspace store; re-renders when it changes. */
export function useWs<T>(selector: (s: WorkspaceState) => T): T {
  return useStore(useWorkspace().store, selector);
}
/** Re-renders on every change to the active sheet's Build state (scale, walls, review, answer). */
export function useSessionTick(): number {
  const { session } = useWorkspace();
  return useSyncExternalStore(session.subscribe, session.version, session.version);
}

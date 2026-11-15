import type {LensStatus, RoutingMode} from './types';

export const modeLabel = (mode: RoutingMode) => mode === 'frontier' ? 'Frontier' : 'Smart';
export function modeReady(status: LensStatus | null, mode: RoutingMode): boolean {
  return status?.modes?.[mode]?.ready ?? (mode === 'smart' && status?.ready === true);
}
export function orchestratorLabel(model: string): string {
  return ({'deepseek/deepseek-v4.1-flash-1017':'DeepSeek','openai/gpt-6.1-sol':'Sol'} as Record<string,string>)[model] ?? model;
}

import type { LiveEvent } from '$lib/types';

export interface LiveState { events: LiveEvent[]; seen: Set<string>; progress: number; stage: string; }
export const initialLiveState = (): LiveState => ({ events: [], seen: new Set(), progress: 0, stage: 'idle' });
export function reduceLiveEvent(state: LiveState, event: LiveEvent): LiveState {
  if (state.seen.has(event.eventId)) return state;
  const seen = new Set(state.seen); seen.add(event.eventId);
  return { events: [...state.events, event], seen, progress: Math.max(state.progress, event.progress), stage: event.stage };
}

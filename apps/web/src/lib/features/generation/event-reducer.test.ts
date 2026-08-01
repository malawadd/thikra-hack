import { describe, expect, it } from 'vitest';
import { initialLiveState, reduceLiveEvent } from './event-reducer';

const event = { eventId: 'event-1', runId: 'run-1', type: 'generation.started', timestamp: new Date(0).toISOString(), stage: 'video', progress: 0.5, message: 'Started', data: {} };
describe('live event reducer', () => {
  it('deduplicates reconnect frames and never regresses progress', () => {
    const first = reduceLiveEvent(initialLiveState(), event);
    const duplicate = reduceLiveEvent(first, event);
    const later = reduceLiveEvent(duplicate, { ...event, eventId: 'event-2', progress: 0.3, stage: 'voice' });
    expect(duplicate.events).toHaveLength(1);
    expect(later.events).toHaveLength(2);
    expect(later.progress).toBe(0.5);
    expect(later.stage).toBe('voice');
  });
});

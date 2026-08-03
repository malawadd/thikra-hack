import type { SequenceClip, StudioRender } from './types';

export const snapFrame = (milliseconds: number, fps = 30) =>
  Math.max(0, Math.round(milliseconds / (1000 / fps)) * (1000 / fps));

export function splitClip(clip: SequenceClip, playheadMs: number, nextId: string): [SequenceClip, SequenceClip] | null {
  const offset = playheadMs - clip.start_ms;
  if (offset <= 0 || offset >= clip.duration_ms) return null;
  return [
    { ...structuredClone(clip), duration_ms: offset },
    {
      ...structuredClone(clip), id: nextId, start_ms: playheadMs,
      duration_ms: clip.duration_ms - offset, source_in_ms: clip.source_in_ms + offset,
    },
  ];
}

export function deleteClips(clips: SequenceClip[], selectedIds: ReadonlySet<string>, ripple: boolean): SequenceClip[] {
  if (!ripple) return clips.filter((clip) => !selectedIds.has(clip.id));
  const removed = clips.filter((clip) => selectedIds.has(clip.id)).sort((a, b) => a.start_ms - b.start_ms);
  let result = clips.filter((clip) => !selectedIds.has(clip.id));
  for (const deleted of removed) {
    result = result.map((clip) => clip.track_id === deleted.track_id && clip.start_ms > deleted.start_ms
      ? { ...clip, start_ms: Math.max(deleted.start_ms, clip.start_ms - deleted.duration_ms) }
      : clip);
  }
  return result;
}

export function reduceRenderEvent(render: StudioRender, event: {status?: StudioRender['status'];progress?:number;type?:string;message?:string}): StudioRender {
  return {
    ...render,
    status: event.status ?? render.status,
    progress: Math.max(render.progress, event.progress ?? 0),
    error: event.type === 'render.failed' ? event.message : render.error,
  };
}

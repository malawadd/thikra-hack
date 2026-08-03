import type { ClipKind, SequenceClip, SequenceDocument, SequenceTrack, StudioRender, TrackKind } from './types';

export const isCompositingTrack = (track: SequenceTrack) => track.kind !== 'audio';

export function displayedTracks(tracks: SequenceTrack[]): SequenceTrack[] {
  const compositing = tracks.filter(isCompositingTrack).sort((a, b) => b.order - a.order);
  const audio = tracks.filter((track) => track.kind === 'audio').sort((a, b) => a.order - b.order);
  return [...compositing, ...audio];
}

export function compatibleTrackKind(kind: ClipKind): TrackKind {
  if (kind === 'image' || kind === 'video') return 'visual';
  return kind;
}

export const trackAcceptsClip = (track: SequenceTrack, clip: SequenceClip) =>
  !track.locked && track.kind === compatibleTrackKind(clip.kind);

export function normalizeTrackOrders(tracks: SequenceTrack[], backToFrontIds?: string[]): SequenceTrack[] {
  const ids = backToFrontIds ?? [...tracks].sort((a, b) => a.order - b.order).map((track) => track.id);
  const order = new Map(ids.map((id, index) => [id, index]));
  return tracks.map((track) => ({ ...track, order: order.get(track.id) ?? track.order }));
}

export function clipsCollide(
  candidate: SequenceClip,
  clips: SequenceClip[],
  ignoredIds: ReadonlySet<string> = new Set(),
): boolean {
  return clips.some((other) => {
    if (other.id === candidate.id || ignoredIds.has(other.id) || other.track_id !== candidate.track_id) return false;
    const overlap = Math.min(candidate.start_ms + candidate.duration_ms, other.start_ms + other.duration_ms)
      - Math.max(candidate.start_ms, other.start_ms);
    if (overlap <= 0) return false;
    return overlap > Math.max(candidate.transition_duration_ms, other.transition_duration_ms);
  });
}

export function upgradeSequenceDocument(document: SequenceDocument): SequenceDocument {
  const upgraded = structuredClone(document);
  if (upgraded.schema_version === 1) {
    upgraded.schema_version = 2;
    upgraded.clips = upgraded.clips.map((clip) => clip.kind === 'text' || clip.kind === 'caption'
      ? {
          ...clip,
          transform: {
            ...clip.transform,
            position_x: clip.text?.position_x ?? .5,
            position_y: clip.text?.position_y ?? .82,
          },
        }
      : clip);
  }
  return upgraded;
}

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

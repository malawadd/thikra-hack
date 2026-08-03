import { describe, expect, it } from 'vitest';
import type { SequenceClip, StudioRender } from './types';
import { deleteClips, reduceRenderEvent, snapFrame, splitClip } from './timeline';

const clip = (id: string, start_ms: number, duration_ms: number): SequenceClip => ({
  id, track_id:'v1', kind:'video', name:id, asset_id:'asset', start_ms, duration_ms,
  source_in_ms:500, transition_in:'cut', transition_out:'cut', transition_duration_ms:0,
  transform:{fit:'fill',position_x:.5,position_y:.5,scale:1,rotation:0,opacity:1,fade_in_ms:0,fade_out_ms:0,ken_burns:false},
  audio:{gain_db:0,muted:false,fade_in_ms:0,fade_out_ms:0,role:'source'},
});

describe('timeline editing', () => {
  it('snaps to frames and splits source ranges without regenerating media', () => {
    expect(snapFrame(1010)).toBeCloseTo(1000);
    const result = splitClip(clip('a', 1000, 4000), 2500, 'b')!;
    expect(result.map((item)=>item.duration_ms)).toEqual([1500,2500]);
    expect(result[1].source_in_ms).toBe(2000);
    expect(result[1].asset_id).toBe('asset');
  });

  it('ripple delete closes only the deleted track gap', () => {
    const audio = {...clip('audio',5000,1000),track_id:'a1',kind:'audio' as const};
    const result = deleteClips([clip('a',0,1000),clip('b',1000,1000),audio],new Set(['a']),true);
    expect(result.find((item)=>item.id==='b')?.start_ms).toBe(0);
    expect(result.find((item)=>item.id==='audio')?.start_ms).toBe(5000);
  });

  it('reduces reconnect-safe render events monotonically', () => {
    const render = {id:'r',sequence_id:'s',revision_id:'v',preset:'landscape_720',status:'RUNNING',progress:60,cancel_requested:false} as StudioRender;
    expect(reduceRenderEvent(render,{status:'RUNNING',progress:40}).progress).toBe(60);
    expect(reduceRenderEvent(render,{status:'FAILED',type:'render.failed',message:'encoder stopped'}).error).toBe('encoder stopped');
  });
});

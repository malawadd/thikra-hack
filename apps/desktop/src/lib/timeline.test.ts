import { describe, expect, it } from 'vitest';
import type { SequenceClip, SequenceDocument, SequenceTrack, StudioRender } from './types';
import { clipsCollide, deleteClips, displayedTracks, normalizeTrackOrders, reduceRenderEvent, snapFrame, splitClip, trackAcceptsClip, upgradeSequenceDocument } from './timeline';

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

  it('shows the frontmost compositing track first and keeps audio below it', () => {
    const tracks = [
      {id:'video',name:'Video',kind:'visual',order:0},
      {id:'title',name:'Title',kind:'text',order:2},
      {id:'audio',name:'Audio',kind:'audio',order:1},
    ] as SequenceTrack[];
    expect(displayedTracks(tracks).map((track)=>track.id)).toEqual(['title','video','audio']);
    expect(normalizeTrackOrders(tracks,['video','audio','title']).map((track)=>track.order)).toEqual([0,2,1]);
  });

  it('accepts image and video on visual layers and detects unsupported collisions', () => {
    const visual = {id:'v2',name:'Overlay',kind:'visual',order:1,locked:false} as SequenceTrack;
    const candidate = {...clip('b',500,1000),track_id:'v2'};
    expect(trackAcceptsClip(visual,candidate)).toBe(true);
    expect(clipsCollide(candidate,[{...clip('a',0,1000),track_id:'v2'}])).toBe(true);
    expect(clipsCollide({...candidate,start_ms:1000},[{...clip('a',0,1000),track_id:'v2'}])).toBe(false);
  });

  it('normalizes legacy text geometry without changing the input revision', () => {
    const title = {...clip('title',0,1000),kind:'text' as const,asset_id:undefined,text:{content:'Title',font_family:'Noto Sans' as const,font_size:56,font_weight:600 as const,color:'#ffffff',background:'#00000000',align:'center' as const,position_x:.2,position_y:.3}};
    const legacy = {schema_version:1,preset:'landscape_720',background:'#000000',tracks:[{id:'v1',name:'Titles',kind:'text',order:0,locked:false,hidden:false,muted:false}],clips:[title],duck_music_under_narration:true,captions_stale:false} as SequenceDocument;
    const upgraded = upgradeSequenceDocument(legacy);
    expect(upgraded.schema_version).toBe(2);
    expect(upgraded.clips[0].transform.position_x).toBe(.2);
    expect(legacy.clips[0].transform.position_x).toBe(.5);
  });
});

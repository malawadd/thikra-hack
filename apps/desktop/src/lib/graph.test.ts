import { describe, expect, it } from 'vitest';
import type { Edge, Node } from '@xyflow/svelte';
import { operationClosure, validateConnection } from './graph';

describe('operationClosure', () => {
  it('includes proposal dependencies exactly once', () => {
    const operations = [{ id: 'brief', depends_on: [] }, { id: 'variants', depends_on: ['brief'] }];
    expect(operationClosure(['variants'], operations).sort()).toEqual(['brief', 'variants']);
  });
});

describe('validateConnection', () => {
  const nodes = [
    { id: 'brief', position: { x: 0, y: 0 }, data: { type: 'creative_brief' } },
    { id: 'image', position: { x: 0, y: 0 }, data: { type: 'image_generation' } },
    { id: 'select', position: { x: 0, y: 0 }, data: { type: 'asset_selector' } },
  ] as Node[];

  it('accepts matching ports and rejects incompatible ports', () => {
    expect(validateConnection({ source: 'brief', sourceHandle: 'text', target: 'image', targetHandle: 'prompt' }, nodes, [])).toBe(true);
    expect(validateConnection({ source: 'brief', sourceHandle: 'text', target: 'select', targetHandle: 'variants' }, nodes, [])).toBe(false);
  });

  it('rejects a connection that closes a cycle', () => {
    const directors = [{ id: 'look-a', position: { x: 0, y: 0 }, data: { type: 'look_director' } }, { id: 'look-b', position: { x: 0, y: 0 }, data: { type: 'look_director' } }] as Node[];
    const edges = [{ id: 'one', source: 'look-a', sourceHandle: 'style', target: 'look-b', targetHandle: 'brief' }] as Edge[];
    expect(validateConnection({ source: 'look-b', sourceHandle: 'style', target: 'look-a', targetHandle: 'brief' }, directors, edges)).toBe(false);
  });
});

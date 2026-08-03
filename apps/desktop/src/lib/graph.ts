import type { Edge, Node } from '@xyflow/svelte';
import type { NodeStatus, WorkflowEdge, WorkflowGraph, WorkflowNode } from './types';

export const PORTS: Record<string, { inputs: string[]; outputs: string[] }> = {
  creative_brief: { inputs: [], outputs: ['text'] }, reference_asset: { inputs: [], outputs: ['image'] },
  look_director: { inputs: ['brief', 'reference'], outputs: ['style'] }, image_generation: { inputs: ['prompt'], outputs: ['variants'] },
  asset_selector: { inputs: ['variants'], outputs: ['asset'] }, video_generation: { inputs: ['image', 'prompt'], outputs: ['variants'] },
  narration: { inputs: ['text'], outputs: ['audio'] }, music: { inputs: ['prompt'], outputs: ['audio'] },
  composition: { inputs: ['visual', 'voice', 'music'], outputs: ['media'] }, verification: { inputs: ['media'], outputs: ['report'] },
  export: { inputs: ['media', 'report'], outputs: ['delivery'] }, note: { inputs: [], outputs: [] }, group: { inputs: [], outputs: [] }
};

const OUTPUT_TYPES: Record<string, Record<string, string>> = {
  creative_brief: { text: 'text' }, reference_asset: { image: 'image' }, look_director: { style: 'text' },
  image_generation: { variants: 'variant_set' }, asset_selector: { asset: 'asset' }, video_generation: { variants: 'variant_set' },
  narration: { audio: 'audio' }, music: { audio: 'audio' }, composition: { media: 'final_media' }, verification: { report: 'report' }, export: { delivery: 'final_media' }
};
const INPUT_TYPES: Record<string, Record<string, string[]>> = {
  look_director: { brief: ['text'], reference: ['image'] }, image_generation: { prompt: ['text'] }, asset_selector: { variants: ['variant_set'] },
  video_generation: { image: ['image', 'asset'], prompt: ['text'] }, narration: { text: ['text'] }, music: { prompt: ['text'] },
  composition: { visual: ['image', 'asset', 'variant_set'], voice: ['audio'], music: ['audio'] }, verification: { media: ['final_media'] }, export: { media: ['final_media'], report: ['report'] }
};

export function validateConnection(connection: { source: string | null; target: string | null; sourceHandle?: string | null; targetHandle?: string | null }, nodes: Node[], edges: Edge[]): boolean {
  if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle || connection.source === connection.target) return false;
  const source = nodes.find((node) => node.id === connection.source); const target = nodes.find((node) => node.id === connection.target);
  if (!source || !target) return false;
  const sourceType = String(source.data.type); const targetType = String(target.data.type);
  const outputType = OUTPUT_TYPES[sourceType]?.[connection.sourceHandle];
  if (!outputType || !INPUT_TYPES[targetType]?.[connection.targetHandle]?.includes(outputType)) return false;
  const adjacency = new Map<string, string[]>();
  for (const edge of edges) adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target]);
  const pending = [connection.target]; const seen = new Set<string>();
  while (pending.length) { const current = pending.pop()!; if (current === connection.source) return false; if (!seen.has(current)) { seen.add(current); pending.push(...(adjacency.get(current) ?? [])); } }
  return !edges.some((edge) => edge.source === connection.source && edge.target === connection.target && edge.sourceHandle === connection.sourceHandle && edge.targetHandle === connection.targetHandle);
}

const fallbackPosition = (index: number) => ({ x: 70 + (index % 4) * 270, y: 70 + Math.floor(index / 4) * 190 });

export function toFlowNodes(graph: WorkflowGraph, layout: Record<string, { x: number; y: number }>, statuses: Record<string, NodeStatus> = {}): Node[] {
  return graph.nodes.map((node, index) => ({
    id: node.id, type: 'studio', position: layout[node.id] ?? fallbackPosition(index),
    data: { ...node, inputs: PORTS[node.type]?.inputs ?? [], outputs: PORTS[node.type]?.outputs ?? [], status: statuses[node.id] ?? 'IDLE' }
  }));
}

export function toFlowEdges(edges: WorkflowEdge[]): Edge[] {
  return edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: edge.source_port, targetHandle: edge.target_port, animated: false }));
}

export function fromFlow(nodes: Node[], edges: Edge[]): WorkflowGraph {
  return {
    schema_version: 1,
    nodes: nodes.map((node) => { const data = node.data as unknown as WorkflowNode; return { id: node.id, type: data.type, label: data.label, config: data.config ?? {} }; }),
    edges: edges.map((edge) => ({ id: edge.id, source: edge.source, source_port: edge.sourceHandle ?? 'text', target: edge.target, target_port: edge.targetHandle ?? 'prompt' }))
  };
}

export function operationClosure(ids: string[], operations: { id: string; depends_on: string[] }[]): string[] {
  const result = new Set(ids); const byId = new Map(operations.map((item) => [item.id, item])); const pending = [...ids];
  while (pending.length) for (const dep of byId.get(pending.pop()!)?.depends_on ?? []) if (!result.has(dep)) { result.add(dep); pending.push(dep); }
  return [...result];
}

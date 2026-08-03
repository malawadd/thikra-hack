"""Typed DAG validation and deterministic graph operations."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from copy import deepcopy

from app.studio.schemas import ProposalOperation, WorkflowGraph, WorkflowNode

OUTPUTS = {
    "creative_brief": {"text"},
    "reference_asset": {"image"},
    "look_director": {"style"},
    "image_generation": {"variants"},
    "asset_selector": {"asset"},
    "video_generation": {"variants"},
    "narration": {"audio"},
    "music": {"audio"},
    "composition": {"media"},
    "verification": {"report"},
    "export": {"delivery"},
    "note": set(),
    "group": set(),
}
INPUTS = {
    "look_director": {"brief": {"text"}, "reference": {"image"}},
    "image_generation": {"prompt": {"text", "style"}},
    "asset_selector": {"variants": {"variants"}},
    "video_generation": {"image": {"image", "asset"}, "prompt": {"text", "style"}},
    "narration": {"text": {"text"}},
    "music": {"prompt": {"text", "style"}},
    "composition": {
        "visual": {"image", "asset", "variants"},
        "voice": {"audio"},
        "music": {"audio"},
    },
    "verification": {"media": {"media"}},
    "export": {"media": {"media"}, "report": {"report"}},
}
REQUIRED_INPUTS = {
    "image_generation": {"prompt"},
    "asset_selector": {"variants"},
    "video_generation": {"image"},
    "narration": {"text"},
    "composition": {"visual"},
    "verification": {"media"},
    "export": {"media"},
}


def canonical_graph(graph: WorkflowGraph) -> str:
    return json.dumps(graph.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def graph_hash(graph: WorkflowGraph) -> str:
    return hashlib.sha256(canonical_graph(graph).encode()).hexdigest()


def validate_graph(graph: WorkflowGraph) -> list[str]:
    errors: list[str] = []
    nodes = {node.id: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes):
        errors.append("Node ids must be unique")
    edge_ids = {edge.id for edge in graph.edges}
    if len(edge_ids) != len(graph.edges):
        errors.append("Edge ids must be unique")
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in nodes}
    connected_inputs: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        source, target = nodes.get(edge.source), nodes.get(edge.target)
        if source is None or target is None:
            errors.append(f"Edge {edge.id} references a missing node")
            continue
        if edge.source_port not in OUTPUTS[source.type]:
            errors.append(f"{source.type} has no output port '{edge.source_port}'")
        accepted = INPUTS.get(target.type, {}).get(edge.target_port)
        if accepted is None:
            errors.append(f"{target.type} has no input port '{edge.target_port}'")
        elif edge.source_port not in accepted:
            errors.append(
                f"Port type mismatch: {edge.source_port} cannot connect to {target.type}.{edge.target_port}"
            )
        adjacency[edge.source].append(edge.target)
        indegree[edge.target] += 1
        connected_inputs[edge.target].add(edge.target_port)
    for node in graph.nodes:
        missing = REQUIRED_INPUTS.get(node.type, set()) - connected_inputs[node.id]
        if missing:
            errors.append(f"{node.label} is missing required input(s): {', '.join(sorted(missing))}")
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for child in adjacency[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(nodes):
        errors.append("Workflow graph must be acyclic")
    return errors


def topological_nodes(graph: WorkflowGraph) -> list[WorkflowNode]:
    errors = validate_graph(graph)
    if errors:
        raise ValueError("; ".join(errors))
    nodes = {node.id: node for node in graph.nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in nodes}
    for edge in graph.edges:
        adjacency[edge.source].append(edge.target)
        indegree[edge.target] += 1
    queue = deque(node_id for node_id in nodes if indegree[node_id] == 0)
    result: list[WorkflowNode] = []
    while queue:
        current = queue.popleft()
        result.append(nodes[current])
        for child in adjacency[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    return result


def apply_operations(graph: WorkflowGraph, operations: list[ProposalOperation]) -> WorkflowGraph:
    document = deepcopy(graph.model_dump(mode="json"))
    nodes = {node["id"]: node for node in document["nodes"]}
    edges = {edge["id"]: edge for edge in document["edges"]}
    for operation in operations:
        if operation.type == "add_node" and operation.node:
            proposed = operation.node.model_dump(mode="json")
            proposed["config"] = operation.node.config.model_dump(exclude_none=True)
            nodes[operation.node.id] = proposed
        elif operation.type == "update_node" and operation.node_id in nodes:
            patch = operation.config_patch.model_dump(exclude_none=True) if operation.config_patch else {}
            nodes[operation.node_id]["config"].update(patch)
        elif operation.type == "remove_node" and operation.node_id:
            nodes.pop(operation.node_id, None)
            edges = {
                key: value
                for key, value in edges.items()
                if value["source"] != operation.node_id and value["target"] != operation.node_id
            }
        elif operation.type == "connect" and operation.edge:
            edges[operation.edge.id] = operation.edge.model_dump(mode="json")
        elif operation.type == "disconnect" and operation.edge:
            edges.pop(operation.edge.id, None)
    updated = WorkflowGraph.model_validate(
        {"schema_version": 1, "nodes": list(nodes.values()), "edges": list(edges.values())}
    )
    errors = validate_graph(updated)
    if errors:
        raise ValueError("; ".join(errors))
    return updated


def default_graph() -> WorkflowGraph:
    return WorkflowGraph.model_validate(
        {
            "schema_version": 1,
            "nodes": [
                {"id": "brief", "type": "creative_brief", "label": "Creative brief", "config": {"text": "Describe the story you want to create"}},
                {"id": "look", "type": "look_director", "label": "Look director", "config": {}},
                {"id": "image", "type": "image_generation", "label": "Image variants", "config": {"vendor": "replicate", "model": "black-forest-labs/flux-schnell", "variants": 3}},
                {"id": "select", "type": "asset_selector", "label": "Choose the look", "config": {"selected_index": 0}},
                {"id": "video", "type": "video_generation", "label": "Animate", "config": {"vendor": "openai", "model": "sora-2", "variants": 1, "duration_sec": 4}},
                {"id": "compose", "type": "composition", "label": "Final composition", "config": {}},
                {"id": "verify", "type": "verification", "label": "Verify", "config": {}},
                {"id": "export", "type": "export", "label": "Export", "config": {"format": "mp4"}},
            ],
            "edges": [
                {"id": "e1", "source": "brief", "source_port": "text", "target": "look", "target_port": "brief"},
                {"id": "e2", "source": "look", "source_port": "style", "target": "image", "target_port": "prompt"},
                {"id": "e3", "source": "image", "source_port": "variants", "target": "select", "target_port": "variants"},
                {"id": "e4", "source": "select", "source_port": "asset", "target": "video", "target_port": "image"},
                {"id": "e5", "source": "video", "source_port": "variants", "target": "compose", "target_port": "visual"},
                {"id": "e6", "source": "compose", "source_port": "media", "target": "verify", "target_port": "media"},
                {"id": "e7", "source": "compose", "source_port": "media", "target": "export", "target_port": "media"},
                {"id": "e8", "source": "verify", "source_port": "report", "target": "export", "target_port": "report"},
            ],
        }
    )

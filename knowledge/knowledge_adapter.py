#!/usr/bin/env python3
"""Retrieve EvoLab's curated sources and small, auditable knowledge graph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_PATH = Path(__file__).with_name("sources.json")
DEFAULT_GRAPH_PATH = Path(__file__).with_name("evolution_graph.json")
DEFAULT_CARD_PATH = (
    Path(__file__).resolve().parents[1] / "skills" / "evolution" / "knowledge_cards.json"
)
EVIDENCE_CLASSES = {
    "known_mechanism",
    "teaching_simplification",
    "scenario_extrapolation",
}
REQUIRED_SOURCE_FIELDS = {
    "source_id",
    "title",
    "url",
    "supports",
    "boundary",
    "transition_ids",
    "pressure_ids",
    "knowledge_card_ids",
}


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_empty_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_non_empty_string(item) for item in value)
    )


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_non_empty_string(item) for item in value)


def load_catalog(source_path: str | Path = DEFAULT_SOURCE_PATH) -> dict[str, Any]:
    """Load and validate the curated catalog, failing instead of guessing."""

    path = Path(source_path)
    with path.open("r", encoding="utf-8") as handle:
        catalog = json.load(handle)

    if not isinstance(catalog, dict) or not isinstance(catalog.get("sources"), list):
        raise ValueError("sources.json must contain a top-level sources array")

    source_ids: set[str] = set()
    for index, source in enumerate(catalog["sources"]):
        if not isinstance(source, dict):
            raise ValueError(f"sources[{index}] must be an object")

        missing = REQUIRED_SOURCE_FIELDS.difference(source)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"sources[{index}] is missing required fields: {names}")

        for field in ("source_id", "title", "url", "supports", "boundary"):
            if not _non_empty_string(source[field]):
                raise ValueError(f"sources[{index}].{field} must be a non-empty string")

        for field in ("transition_ids", "pressure_ids", "knowledge_card_ids"):
            if not _string_list(source[field]):
                raise ValueError(
                    f"sources[{index}].{field} must be a string array"
                )

        source_id = source["source_id"]
        if source_id in source_ids:
            raise ValueError(f"duplicate source_id: {source_id}")
        source_ids.add(source_id)

    return catalog


def lookup_sources(
    *,
    knowledge_card_id: str | None = None,
    transition_id: str | None = None,
    pressure_id: str | None = None,
    source_path: str | Path = DEFAULT_SOURCE_PATH,
) -> dict[str, Any]:
    """Return sources for exactly one catalog key.

    An unknown key is a successful lookup with an explicit empty result. It is
    never expanded with generated or inferred citations.
    """

    supplied = sum(
        value is not None
        for value in (knowledge_card_id, transition_id, pressure_id)
    )
    if supplied != 1:
        raise ValueError(
            "provide exactly one of knowledge_card_id, transition_id or pressure_id"
        )

    if knowledge_card_id is not None:
        query_type = "knowledge_card_id"
        query_value = knowledge_card_id.strip().upper()
        source_field = "knowledge_card_ids"
    elif transition_id is not None:
        query_type = "transition_id"
        query_value = (transition_id or "").strip().upper()
        source_field = "transition_ids"
    else:
        query_type = "pressure_id"
        query_value = (pressure_id or "").strip().upper()
        source_field = "pressure_ids"

    catalog = load_catalog(source_path)
    matches = [
        source for source in catalog["sources"] if query_value in source[source_field]
    ]

    result: dict[str, Any] = {
        "status": "ok" if matches else "empty",
        "query": {query_type: query_value},
        "count": len(matches),
        "sources": matches,
    }
    if not matches:
        result["message"] = "无可用来源"
    return result


def _validate_evidence_object(item: dict[str, Any], label: str) -> None:
    required = {
        "evidence_class",
        "prerequisites",
        "mechanisms",
        "tradeoffs",
        "source_ids",
        "boundary",
    }
    missing = required.difference(item)
    if missing:
        raise ValueError(f"{label} is missing required fields: {', '.join(sorted(missing))}")
    if item["evidence_class"] not in EVIDENCE_CLASSES:
        raise ValueError(f"{label}.evidence_class is invalid")
    for field in ("prerequisites", "mechanisms", "tradeoffs", "source_ids"):
        if not _string_list(item[field]):
            raise ValueError(f"{label}.{field} must be a string array")
    if not _non_empty_string(item["boundary"]):
        raise ValueError(f"{label}.boundary must be a non-empty string")


def load_graph(
    graph_path: str | Path = DEFAULT_GRAPH_PATH,
    source_path: str | Path = DEFAULT_SOURCE_PATH,
) -> dict[str, Any]:
    """Load and cross-check graph nodes and edges against the source whitelist."""

    path = Path(graph_path)
    with path.open("r", encoding="utf-8") as handle:
        graph = json.load(handle)
    if not isinstance(graph, dict):
        raise ValueError("evolution_graph.json root must be an object")
    for field in ("nodes", "edges"):
        if not isinstance(graph.get(field), list):
            raise ValueError(f"evolution_graph.json must contain a {field} array")

    known_source_ids = {
        source["source_id"] for source in load_catalog(source_path)["sources"]
    }
    node_ids: set[str] = set()
    for index, node in enumerate(graph["nodes"]):
        if not isinstance(node, dict) or not _non_empty_string(node.get("node_id")):
            raise ValueError(f"nodes[{index}].node_id must be a non-empty string")
        node_id = node["node_id"]
        if node_id in node_ids:
            raise ValueError(f"duplicate node_id: {node_id}")
        node_ids.add(node_id)
        _validate_evidence_object(node, f"nodes[{index}]")
        unknown_sources = set(node["source_ids"]).difference(known_source_ids)
        if unknown_sources:
            raise ValueError(f"nodes[{index}] references unknown sources: {sorted(unknown_sources)}")

    edge_ids: set[str] = set()
    for index, edge in enumerate(graph["edges"]):
        if not isinstance(edge, dict) or not _non_empty_string(edge.get("edge_id")):
            raise ValueError(f"edges[{index}].edge_id must be a non-empty string")
        edge_id = edge["edge_id"]
        if edge_id in edge_ids:
            raise ValueError(f"duplicate edge_id: {edge_id}")
        edge_ids.add(edge_id)
        for endpoint in ("from_node_id", "to_node_id"):
            if edge.get(endpoint) not in node_ids:
                raise ValueError(f"edges[{index}].{endpoint} references an unknown node")
        _validate_evidence_object(edge, f"edges[{index}]")
        unknown_sources = set(edge["source_ids"]).difference(known_source_ids)
        if unknown_sources:
            raise ValueError(f"edges[{index}] references unknown sources: {sorted(unknown_sources)}")
    return graph


def load_interactive_cards(
    card_path: str | Path = DEFAULT_CARD_PATH,
    source_path: str | Path = DEFAULT_SOURCE_PATH,
) -> list[dict[str, Any]]:
    """Load cards used by the interactive experience without changing legacy manifests."""

    path = Path(card_path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    cards = payload.get("interactive_cards")
    if not isinstance(cards, list):
        raise ValueError("knowledge_cards.json must contain an interactive_cards array")
    known_source_ids = {
        source["source_id"] for source in load_catalog(source_path)["sources"]
    }
    card_ids: set[str] = set()
    for index, card in enumerate(cards):
        if not isinstance(card, dict) or not _non_empty_string(card.get("knowledge_card_id")):
            raise ValueError(
                f"interactive_cards[{index}].knowledge_card_id must be a non-empty string"
            )
        card_id = card["knowledge_card_id"]
        if card_id in card_ids:
            raise ValueError(f"duplicate knowledge_card_id: {card_id}")
        card_ids.add(card_id)
        _validate_evidence_object(card, f"interactive_cards[{index}]")
        unknown_sources = set(card["source_ids"]).difference(known_source_ids)
        if unknown_sources:
            raise ValueError(
                f"interactive_cards[{index}] references unknown sources: {sorted(unknown_sources)}"
            )
    return cards


def lookup_knowledge(
    *,
    transition_id: str | None = None,
    pressure_id: str | None = None,
    knowledge_card_id: str | None = None,
    graph_path: str | Path = DEFAULT_GRAPH_PATH,
    card_path: str | Path = DEFAULT_CARD_PATH,
    source_path: str | Path = DEFAULT_SOURCE_PATH,
) -> dict[str, Any]:
    """Resolve a transition, pressure or card to graph facts and curated sources.

    A missing graph match is returned as ``no_match``. No citation or explanation
    is generated to fill the gap.
    """

    supplied = sum(
        value is not None
        for value in (transition_id, pressure_id, knowledge_card_id)
    )
    if supplied != 1:
        raise ValueError(
            "provide exactly one of transition_id, pressure_id or knowledge_card_id"
        )

    if transition_id is not None:
        query_type = "transition_id"
        query_value = transition_id.strip().upper()
    elif pressure_id is not None:
        query_type = "pressure_id"
        query_value = pressure_id.strip().upper()
    else:
        query_type = "knowledge_card_id"
        query_value = (knowledge_card_id or "").strip().upper()

    graph = load_graph(graph_path, source_path)
    cards = load_interactive_cards(card_path, source_path)
    if query_type == "transition_id":
        edges = [edge for edge in graph["edges"] if edge.get("transition_id") == query_value]
        nodes_by_id = {node["node_id"]: node for node in graph["nodes"]}
        node_ids = {
            endpoint
            for edge in edges
            for endpoint in (edge["from_node_id"], edge["to_node_id"])
        }
        nodes = [nodes_by_id[node_id] for node_id in sorted(node_ids)]
        matched_cards = [card for card in cards if query_value in card.get("transition_ids", [])]
    elif query_type == "pressure_id":
        nodes = [node for node in graph["nodes"] if node.get("pressure_id") == query_value]
        edges = [edge for edge in graph["edges"] if query_value in edge.get("pressure_ids", [])]
        matched_cards = [card for card in cards if query_value in card.get("pressure_ids", [])]
    else:
        matched_cards = [card for card in cards if card["knowledge_card_id"] == query_value]
        edges = [
            edge
            for edge in graph["edges"]
            if edge.get("knowledge_card_id") == query_value
        ]
        node_ids = {
            endpoint
            for edge in edges
            for endpoint in (edge["from_node_id"], edge["to_node_id"])
        }
        nodes = [node for node in graph["nodes"] if node["node_id"] in node_ids]

    if not (nodes or edges or matched_cards):
        return {
            "status": "no_match",
            "query": {query_type: query_value},
            "nodes": [],
            "edges": [],
            "knowledge_cards": [],
            "sources": [],
            "message": "知识库没有匹配这一步；只能展示受约束的推演理由，不能附会成已知事实。",
        }

    source_ids = {
        source_id
        for item in [*nodes, *edges, *matched_cards]
        for source_id in item.get("source_ids", [])
    }
    catalog = load_catalog(source_path)
    sources = [
        source for source in catalog["sources"] if source["source_id"] in source_ids
    ]
    return {
        "status": "ok",
        "query": {query_type: query_value},
        "nodes": nodes,
        "edges": edges,
        "knowledge_cards": matched_cards,
        "sources": sources,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retrieve manually curated EvoLab source cards as JSON."
    )
    query = parser.add_mutually_exclusive_group(required=True)
    query.add_argument("--knowledge-card-id", help="For example: ENDOSYMBIOSIS")
    query.add_argument("--transition-id", help="For example: M02")
    query.add_argument("--pressure-id", help="For example: GLOBAL_WARMING")
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Return graph nodes, knowledge cards and sources; unknown IDs are no_match.",
    )
    parser.add_argument(
        "--sources",
        default=str(DEFAULT_SOURCE_PATH),
        help="Catalog path; defaults to knowledge/sources.json.",
    )
    parser.add_argument(
        "--graph",
        default=str(DEFAULT_GRAPH_PATH),
        help="Knowledge graph path; defaults to knowledge/evolution_graph.json.",
    )
    parser.add_argument(
        "--cards",
        default=str(DEFAULT_CARD_PATH),
        help="Knowledge cards path; defaults to skills/evolution/knowledge_cards.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.explain:
            result = lookup_knowledge(
                knowledge_card_id=args.knowledge_card_id,
                transition_id=args.transition_id,
                pressure_id=args.pressure_id,
                graph_path=args.graph,
                card_path=args.cards,
                source_path=args.sources,
            )
        else:
            result = lookup_sources(
                knowledge_card_id=args.knowledge_card_id,
                transition_id=args.transition_id,
                pressure_id=args.pressure_id,
                source_path=args.sources,
            )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        error = {"status": "error", "message": str(exc), "sources": []}
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

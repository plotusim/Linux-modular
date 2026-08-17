"""JSON-lines interchange between LLVM extraction and whole-program analysis."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from .errors import GraphValidationError, SchemaVersionError
from .model import CURRENT_SCHEMA_VERSION, ReferenceGraph
from .pointer_analysis import POINTER_SCHEMA_VERSION, PointerProgram


FACT_SCHEMA_VERSION = 1


@dataclass
class TranslationUnitFacts:
    translation_unit: str
    graph: ReferenceGraph
    pointer_program: PointerProgram
    metadata: Dict[str, Any]

    def to_json_lines(self) -> str:
        header = {
            "record": "translation_unit",
            "schema_version": FACT_SCHEMA_VERSION,
            "translation_unit": self.translation_unit,
            "metadata": self.metadata,
        }
        records = [header]
        graph_raw = self.graph.to_dict()
        records.extend(
            {"record": "node", **node}
            for node in graph_raw["nodes"]
        )
        records.extend(
            {"record": "edge", **edge}
            for edge in graph_raw["edges"]
        )
        pointer_raw = self.pointer_program.to_dict()
        for key in ("addresses", "copies", "loads", "stores", "geps"):
            singular = {
                "addresses": "address",
                "copies": "copy",
                "loads": "load",
                "stores": "store",
                "geps": "gep",
            }[key]
            records.extend(
                {"record": singular, **constraint}
                for constraint in pointer_raw[key]
            )
        records.extend(
            {"record": "summary", **summary}
            for summary in pointer_raw["summaries"]
        )
        records.extend(
            {"record": "call", **call}
            for call in pointer_raw["calls"]
        )
        return "\n".join(
            json.dumps(record, sort_keys=True, ensure_ascii=False)
            for record in records
        ) + "\n"

    @classmethod
    def from_json_lines(cls, content: str) -> "TranslationUnitFacts":
        records = []
        for line_number, line in enumerate(content.splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise GraphValidationError(
                    f"invalid fact JSON at line {line_number}: {error}"
                ) from error
            if not isinstance(record, Mapping):
                raise GraphValidationError(
                    f"fact line {line_number} must contain an object"
                )
            records.append(dict(record))
        if not records or records[0].get("record") != "translation_unit":
            raise GraphValidationError(
                "fact stream must begin with a translation_unit record"
            )
        header = records[0]
        if header.get("schema_version") != FACT_SCHEMA_VERSION:
            raise SchemaVersionError(
                "unsupported fact schema_version="
                f"{header.get('schema_version')!r}; "
                f"expected {FACT_SCHEMA_VERSION}"
            )
        translation_unit = header.get("translation_unit")
        metadata = header.get("metadata", {})
        if not isinstance(translation_unit, str) or not translation_unit:
            raise GraphValidationError(
                "translation_unit record requires a non-empty path"
            )
        if not isinstance(metadata, Mapping):
            raise GraphValidationError("fact metadata must be an object")

        node_records = []
        edge_records = []
        pointer_records: Dict[str, list[Dict[str, Any]]] = {
            "addresses": [],
            "copies": [],
            "loads": [],
            "stores": [],
            "geps": [],
            "summaries": [],
            "calls": [],
        }
        record_to_pointer_key = {
            "address": "addresses",
            "copy": "copies",
            "load": "loads",
            "store": "stores",
            "gep": "geps",
            "summary": "summaries",
            "call": "calls",
        }
        for record in records[1:]:
            kind = record.pop("record", None)
            if kind == "translation_unit":
                raise GraphValidationError(
                    "fact stream contains multiple translation_unit records"
                )
            if kind == "node":
                node_records.append(record)
            elif kind == "edge":
                edge_records.append(record)
            elif kind in record_to_pointer_key:
                pointer_records[record_to_pointer_key[kind]].append(record)
            else:
                raise GraphValidationError(f"unknown fact record {kind!r}")

        graph = ReferenceGraph.from_dict(
            {
                "schema_version": CURRENT_SCHEMA_VERSION,
                "metadata": {
                    **dict(metadata),
                    "translation_units": [translation_unit],
                },
                "nodes": node_records,
                "edges": edge_records,
            }
        )
        pointer_program = PointerProgram.from_dict(
            {
                "schema_version": POINTER_SCHEMA_VERSION,
                "metadata": {
                    **dict(metadata),
                    "translation_units": [translation_unit],
                },
                **pointer_records,
            }
        )
        return cls(
            translation_unit=translation_unit,
            graph=graph,
            pointer_program=pointer_program,
            metadata=dict(metadata),
        )


def load_translation_unit_facts(path: str | Path) -> TranslationUnitFacts:
    fact_path = Path(path)
    try:
        content = fact_path.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphValidationError(
            f"cannot read LLVM fact stream {fact_path}: {error}"
        ) from error
    return TranslationUnitFacts.from_json_lines(content)


def merge_translation_unit_facts(
    facts: Iterable[TranslationUnitFacts],
) -> tuple[ReferenceGraph, PointerProgram]:
    merged_graph = ReferenceGraph()
    merged_program = PointerProgram()
    translation_units = []
    pending_edges = []
    for unit in sorted(facts, key=lambda item: item.translation_unit):
        translation_units.append(unit.translation_unit)
        for node in unit.graph.nodes.values():
            merged_graph.merge_node(node)
        pending_edges.extend(unit.graph.edges)
        merged_program.merge(unit.pointer_program)
    for edge in sorted(set(pending_edges), key=lambda item: item.key):
        merged_graph.add_edge(edge)
    merged_graph.metadata["translation_units"] = translation_units
    merged_program.metadata["translation_units"] = translation_units
    return merged_graph, merged_program


def load_and_merge_fact_files(
    paths: Sequence[str | Path],
) -> tuple[ReferenceGraph, PointerProgram]:
    return merge_translation_unit_facts(
        load_translation_unit_facts(path) for path in paths
    )

"""Versioned, linkage-aware reference graph model.

The legacy pipeline used a function's source-level name as the DOT node key.
That silently merged unrelated translation-unit-local Linux functions such as
``probe`` or ``show``.  This model makes identity and edge provenance explicit
and validates a graph before it can be consumed by the module planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Set, Tuple

from .errors import GraphValidationError, SchemaVersionError


CURRENT_SCHEMA_VERSION = 1


class Linkage(str, Enum):
    EXTERNAL = "external"
    INTERNAL = "internal"


class EntityKind(str, Enum):
    FUNCTION = "function"
    GLOBAL = "global"
    ASSEMBLY = "assembly"
    LINKER_SECTION = "linker_section"
    UNRESOLVED = "unresolved"


class EdgeKind(str, Enum):
    DIRECT_CALL = "direct_call"
    INDIRECT_CALL = "indirect_call"
    ADDRESS_TAKEN = "address_taken"
    CALLBACK_FIELD = "callback_field"
    FUNCTION_ARGUMENT = "function_argument"
    FUNCTION_RETURN = "function_return"
    GLOBAL_READ = "global_read"
    GLOBAL_WRITE = "global_write"
    GLOBAL_INITIALIZER = "global_initializer"
    ALIAS = "alias"
    ASSEMBLY = "assembly"
    LINKER_SECTION = "linker_section"
    UNRESOLVED_CALL = "unresolved_call"


class ExecutionContext(str, Enum):
    PROCESS = "process"
    ATOMIC = "atomic"
    IRQ = "irq"
    NMI = "nmi"
    EARLY_BOOT = "early_boot"
    UNKNOWN = "unknown"


def _normalize_translation_unit(path: str) -> str:
    normalized = str(PurePosixPath(path))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized == ".":
        raise GraphValidationError(
            "internal symbols require a non-empty translation_unit"
        )
    if normalized.startswith("../") or normalized == "..":
        raise GraphValidationError(
            f"translation_unit must be workspace-relative, got {path!r}"
        )
    if normalized.startswith("/"):
        raise GraphValidationError(
            f"translation_unit must be workspace-relative, got {path!r}"
        )
    return normalized


@dataclass(frozen=True, order=True)
class FunctionIdentity:
    """Stable identity for a function across graph-production stages."""

    symbol: str
    linkage: Linkage
    translation_unit: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise GraphValidationError("function symbol cannot be empty")
        if any(character.isspace() for character in self.symbol):
            raise GraphValidationError(
                f"function symbol cannot contain whitespace: {self.symbol!r}"
            )
        if self.linkage is Linkage.INTERNAL:
            if self.translation_unit is None:
                raise GraphValidationError(
                    f"internal function {self.symbol!r} requires translation_unit"
                )
            object.__setattr__(
                self,
                "translation_unit",
                _normalize_translation_unit(self.translation_unit),
            )
        elif self.translation_unit is not None:
            # External declarations and definitions must converge on one ID.
            object.__setattr__(self, "translation_unit", None)

    @property
    def canonical(self) -> str:
        if self.linkage is Linkage.EXTERNAL:
            return f"fn:external:{self.symbol}"
        assert self.translation_unit is not None
        return f"fn:internal:{self.translation_unit}:{self.symbol}"

    @property
    def short_hash(self) -> str:
        return hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, order=True)
class GlobalIdentity:
    """Stable identity for a global variable across translation units."""

    symbol: str
    linkage: Linkage
    translation_unit: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise GraphValidationError("global symbol cannot be empty")
        if any(character.isspace() for character in self.symbol):
            raise GraphValidationError(
                f"global symbol cannot contain whitespace: {self.symbol!r}"
            )
        if self.linkage is Linkage.INTERNAL:
            if self.translation_unit is None:
                raise GraphValidationError(
                    f"internal global {self.symbol!r} requires translation_unit"
                )
            object.__setattr__(
                self,
                "translation_unit",
                _normalize_translation_unit(self.translation_unit),
            )
        elif self.translation_unit is not None:
            object.__setattr__(self, "translation_unit", None)

    @property
    def canonical(self) -> str:
        if self.linkage is Linkage.EXTERNAL:
            return f"global:external:{self.symbol}"
        assert self.translation_unit is not None
        return f"global:internal:{self.translation_unit}:{self.symbol}"


@dataclass(frozen=True)
class ReferenceNode:
    """A function or data entity participating in the reference graph."""

    id: str
    kind: EntityKind
    symbol: str
    linkage: Optional[Linkage] = None
    translation_unit: Optional[str] = None
    source_path: Optional[str] = None
    section: Optional[str] = None
    size_bytes: Optional[int] = None
    attributes: frozenset[str] = field(default_factory=frozenset)
    contexts: frozenset[ExecutionContext] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.id:
            raise GraphValidationError("node id cannot be empty")
        if not self.symbol:
            raise GraphValidationError(f"node {self.id!r} has an empty symbol")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise GraphValidationError(
                f"node {self.id!r} has negative size_bytes={self.size_bytes}"
            )
        if self.kind is EntityKind.FUNCTION:
            if self.linkage is None:
                raise GraphValidationError(
                    f"function node {self.id!r} is missing linkage"
                )
            identity = FunctionIdentity(
                symbol=self.symbol,
                linkage=self.linkage,
                translation_unit=self.translation_unit,
            )
            if self.id != identity.canonical:
                raise GraphValidationError(
                    f"function node id {self.id!r} does not match canonical "
                    f"identity {identity.canonical!r}"
                )
        elif self.kind is EntityKind.GLOBAL and self.linkage is not None:
            identity = GlobalIdentity(
                symbol=self.symbol,
                linkage=self.linkage,
                translation_unit=self.translation_unit,
            )
            if self.id != identity.canonical:
                raise GraphValidationError(
                    f"global node id {self.id!r} does not match canonical "
                    f"identity {identity.canonical!r}"
                )

    @classmethod
    def function(
        cls,
        symbol: str,
        linkage: Linkage,
        *,
        translation_unit: Optional[str] = None,
        source_path: Optional[str] = None,
        section: Optional[str] = None,
        size_bytes: Optional[int] = None,
        attributes: Iterable[str] = (),
        contexts: Iterable[ExecutionContext] = (),
    ) -> "ReferenceNode":
        identity = FunctionIdentity(symbol, linkage, translation_unit)
        return cls(
            id=identity.canonical,
            kind=EntityKind.FUNCTION,
            symbol=symbol,
            linkage=linkage,
            translation_unit=identity.translation_unit,
            source_path=source_path,
            section=section,
            size_bytes=size_bytes,
            attributes=frozenset(attributes),
            contexts=frozenset(contexts),
        )

    @classmethod
    def entity(
        cls,
        kind: EntityKind,
        symbol: str,
        *,
        owner: Optional[str] = None,
        source_path: Optional[str] = None,
        section: Optional[str] = None,
        size_bytes: Optional[int] = None,
        attributes: Iterable[str] = (),
    ) -> "ReferenceNode":
        if kind is EntityKind.FUNCTION:
            raise GraphValidationError("use ReferenceNode.function for functions")
        owner_component = owner or "kernel"
        node_id = f"{kind.value}:{owner_component}:{symbol}"
        return cls(
            id=node_id,
            kind=kind,
            symbol=symbol,
            source_path=source_path,
            section=section,
            size_bytes=size_bytes,
            attributes=frozenset(attributes),
        )

    @classmethod
    def global_variable(
        cls,
        symbol: str,
        linkage: Linkage,
        *,
        translation_unit: Optional[str] = None,
        source_path: Optional[str] = None,
        section: Optional[str] = None,
        size_bytes: Optional[int] = None,
        attributes: Iterable[str] = (),
    ) -> "ReferenceNode":
        identity = GlobalIdentity(symbol, linkage, translation_unit)
        return cls(
            id=identity.canonical,
            kind=EntityKind.GLOBAL,
            symbol=symbol,
            linkage=linkage,
            translation_unit=identity.translation_unit,
            source_path=source_path,
            section=section,
            size_bytes=size_bytes,
            attributes=frozenset(attributes),
        )


@dataclass(frozen=True, order=True)
class ReferenceEdge:
    """A directed, provenance-carrying reference between two entities."""

    source: str
    target: str
    kind: EdgeKind
    location: Optional[str] = None
    field_path: Optional[str] = None
    contexts: frozenset[ExecutionContext] = field(default_factory=frozenset)
    evidence: frozenset[str] = field(default_factory=frozenset)

    @property
    def key(self) -> Tuple[Any, ...]:
        return (
            self.source,
            self.target,
            self.kind.value,
            self.location or "",
            self.field_path or "",
            tuple(sorted(context.value for context in self.contexts)),
            tuple(sorted(self.evidence)),
        )


class ReferenceGraph:
    """Validated graph with deterministic JSON serialization."""

    def __init__(self, *, metadata: Optional[Mapping[str, Any]] = None) -> None:
        self._nodes: Dict[str, ReferenceNode] = {}
        self._edges: Set[ReferenceEdge] = set()
        self._outgoing_edges: Dict[str, Set[ReferenceEdge]] = {}
        self._incoming_edges: Dict[str, Set[ReferenceEdge]] = {}
        self._edge_snapshot: Optional[frozenset[ReferenceEdge]] = None
        self.metadata: Dict[str, Any] = dict(metadata or {})

    @property
    def nodes(self) -> Mapping[str, ReferenceNode]:
        return self._nodes

    @property
    def edges(self) -> frozenset[ReferenceEdge]:
        if self._edge_snapshot is None:
            self._edge_snapshot = frozenset(self._edges)
        return self._edge_snapshot

    def add_node(self, node: ReferenceNode) -> None:
        previous = self._nodes.get(node.id)
        if previous is not None and previous != node:
            raise GraphValidationError(
                f"conflicting definitions for node {node.id!r}: "
                f"{previous!r} versus {node!r}"
            )
        self._nodes[node.id] = node

    def merge_node(self, node: ReferenceNode) -> None:
        """Merge compatible declaration/definition facts for one entity."""

        previous = self._nodes.get(node.id)
        if previous is None:
            self._nodes[node.id] = node
            return
        identity_fields = (
            "kind",
            "symbol",
            "linkage",
            "translation_unit",
        )
        for field_name in identity_fields:
            if getattr(previous, field_name) != getattr(node, field_name):
                raise GraphValidationError(
                    f"conflicting {field_name} for node {node.id!r}"
                )
        self._nodes[node.id] = ReferenceNode(
            id=node.id,
            kind=node.kind,
            symbol=node.symbol,
            linkage=node.linkage,
            translation_unit=node.translation_unit,
            source_path=_merge_optional_fact(
                node.id,
                "source_path",
                previous.source_path,
                node.source_path,
            ),
            section=_merge_optional_fact(
                node.id,
                "section",
                previous.section,
                node.section,
            ),
            size_bytes=_merge_optional_fact(
                node.id,
                "size_bytes",
                previous.size_bytes,
                node.size_bytes,
            ),
            attributes=previous.attributes.union(node.attributes),
            contexts=previous.contexts.union(node.contexts),
        )

    def add_edge(self, edge: ReferenceEdge) -> None:
        missing = [
            endpoint
            for endpoint in (edge.source, edge.target)
            if endpoint not in self._nodes
        ]
        if missing:
            raise GraphValidationError(
                f"edge {edge.kind.value} references missing node(s): "
                + ", ".join(repr(item) for item in missing)
            )
        if edge in self._edges:
            return
        self._edges.add(edge)
        self._outgoing_edges.setdefault(edge.source, set()).add(edge)
        self._incoming_edges.setdefault(edge.target, set()).add(edge)
        self._edge_snapshot = None

    def outgoing(
        self,
        node_id: str,
        *,
        kinds: Optional[Set[EdgeKind]] = None,
    ) -> Iterator[ReferenceEdge]:
        self._require_node(node_id)
        for edge in sorted(
            self._outgoing_edges.get(node_id, ()),
            key=lambda item: item.key,
        ):
            if kinds is None or edge.kind in kinds:
                yield edge

    def incoming(
        self,
        node_id: str,
        *,
        kinds: Optional[Set[EdgeKind]] = None,
    ) -> Iterator[ReferenceEdge]:
        self._require_node(node_id)
        for edge in sorted(
            self._incoming_edges.get(node_id, ()),
            key=lambda item: item.key,
        ):
            if kinds is None or edge.kind in kinds:
                yield edge

    def _require_node(self, node_id: str) -> None:
        if node_id not in self._nodes:
            raise GraphValidationError(f"unknown node id {node_id!r}")

    def validate(self) -> None:
        for node_id, node in self._nodes.items():
            if node.id != node_id:
                raise GraphValidationError(
                    f"node mapping key {node_id!r} differs from node id {node.id!r}"
                )
            # Re-run dataclass validation after any future deserializer changes.
            if node.kind is EntityKind.FUNCTION:
                FunctionIdentity(
                    node.symbol,
                    node.linkage or Linkage.EXTERNAL,
                    node.translation_unit,
                )
        for edge in self._edges:
            if edge.source not in self._nodes or edge.target not in self._nodes:
                raise GraphValidationError(
                    f"edge {edge.key!r} has an endpoint absent from the graph"
                )

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "metadata": _json_safe_mapping(self.metadata),
            "nodes": [
                _node_to_dict(node)
                for node in sorted(self._nodes.values(), key=lambda item: item.id)
            ],
            "edges": [
                _edge_to_dict(edge)
                for edge in sorted(self._edges, key=lambda item: item.key)
            ],
        }

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ReferenceGraph":
        version = raw.get("schema_version")
        if version != CURRENT_SCHEMA_VERSION:
            raise SchemaVersionError(
                f"unsupported graph schema_version={version!r}; "
                f"expected {CURRENT_SCHEMA_VERSION}"
            )
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise GraphValidationError("graph metadata must be an object")
        graph = cls(metadata=metadata)
        raw_nodes = raw.get("nodes")
        raw_edges = raw.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise GraphValidationError("graph nodes and edges must be arrays")
        for raw_node in raw_nodes:
            graph.add_node(_node_from_dict(raw_node))
        for raw_edge in raw_edges:
            graph.add_edge(_edge_from_dict(raw_edge))
        graph.validate()
        return graph

    @classmethod
    def from_json(cls, content: str) -> "ReferenceGraph":
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as error:
            raise GraphValidationError(f"invalid graph JSON: {error}") from error
        if not isinstance(raw, Mapping):
            raise GraphValidationError("graph JSON root must be an object")
        return cls.from_dict(raw)


def _json_safe_mapping(value: Mapping[str, Any]) -> Dict[str, Any]:
    # A serialization round trip validates that metadata is reproducible JSON.
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise GraphValidationError(f"metadata is not JSON serializable: {error}") from error
    if not isinstance(decoded, dict):
        raise GraphValidationError("metadata must serialize to an object")
    return decoded


def _node_to_dict(node: ReferenceNode) -> Dict[str, Any]:
    return {
        "id": node.id,
        "kind": node.kind.value,
        "symbol": node.symbol,
        "linkage": node.linkage.value if node.linkage is not None else None,
        "translation_unit": node.translation_unit,
        "source_path": node.source_path,
        "section": node.section,
        "size_bytes": node.size_bytes,
        "attributes": sorted(node.attributes),
        "contexts": sorted(context.value for context in node.contexts),
    }


def _edge_to_dict(edge: ReferenceEdge) -> Dict[str, Any]:
    return {
        "source": edge.source,
        "target": edge.target,
        "kind": edge.kind.value,
        "location": edge.location,
        "field_path": edge.field_path,
        "contexts": sorted(context.value for context in edge.contexts),
        "evidence": sorted(edge.evidence),
    }


def _node_from_dict(raw: Any) -> ReferenceNode:
    if not isinstance(raw, Mapping):
        raise GraphValidationError("each graph node must be an object")
    try:
        linkage_raw = raw.get("linkage")
        return ReferenceNode(
            id=str(raw["id"]),
            kind=EntityKind(raw["kind"]),
            symbol=str(raw["symbol"]),
            linkage=Linkage(linkage_raw) if linkage_raw is not None else None,
            translation_unit=_optional_string(raw.get("translation_unit")),
            source_path=_optional_string(raw.get("source_path")),
            section=_optional_string(raw.get("section")),
            size_bytes=_optional_integer(raw.get("size_bytes")),
            attributes=frozenset(_string_list(raw.get("attributes", []))),
            contexts=frozenset(
                ExecutionContext(item)
                for item in _string_list(raw.get("contexts", []))
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GraphValidationError(f"invalid node object {raw!r}: {error}") from error


def _edge_from_dict(raw: Any) -> ReferenceEdge:
    if not isinstance(raw, Mapping):
        raise GraphValidationError("each graph edge must be an object")
    try:
        return ReferenceEdge(
            source=str(raw["source"]),
            target=str(raw["target"]),
            kind=EdgeKind(raw["kind"]),
            location=_optional_string(raw.get("location")),
            field_path=_optional_string(raw.get("field_path")),
            contexts=frozenset(
                ExecutionContext(item)
                for item in _string_list(raw.get("contexts", []))
            ),
            evidence=frozenset(_string_list(raw.get("evidence", []))),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GraphValidationError(f"invalid edge object {raw!r}: {error}") from error


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise GraphValidationError(f"expected an array of strings, got {raw!r}")
    return list(raw)


def _optional_string(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise GraphValidationError(f"expected a string or null, got {raw!r}")
    return raw


def _optional_integer(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise GraphValidationError(f"expected an integer or null, got {raw!r}")
    return raw


def _merge_optional_fact(
    node_id: str,
    field_name: str,
    first: Any,
    second: Any,
) -> Any:
    if first is None:
        return second
    if second is None or first == second:
        return first
    raise GraphValidationError(
        f"conflicting {field_name} for node {node_id!r}: "
        f"{first!r} versus {second!r}"
    )

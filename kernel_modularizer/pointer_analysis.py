"""Whole-program, inclusion-based function pointer constraint solver.

LLVM extraction is intentionally separated from solving.  A compiler pass can
emit per-translation-unit constraints in parallel; this module merges them and
iterates to a fixed point across the complete configured kernel.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field, replace
from functools import lru_cache
import json
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple

from .errors import GraphValidationError, SchemaVersionError
from .model import (
    EdgeKind,
    EntityKind,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNode,
)

try:
    from pyroaring import BitMap as _RoaringBitMap
except ImportError:  # Optional full-kernel acceleration.
    _RoaringBitMap = None


POINTER_SCHEMA_VERSION = 1


def memory_object_id(owner: str, name: str) -> str:
    if not owner or not name:
        raise GraphValidationError("memory object owner and name cannot be empty")
    return f"obj:{owner}:{name}"


@dataclass(frozen=True, order=True)
class AddressConstraint:
    pointer: str
    target: str


@dataclass(frozen=True, order=True)
class CopyConstraint:
    destination: str
    source: str


@dataclass(frozen=True, order=True)
class LoadConstraint:
    destination: str
    pointer: str


@dataclass(frozen=True, order=True)
class StoreConstraint:
    pointer: str
    source: str


@dataclass(frozen=True, order=True)
class GepConstraint:
    destination: str
    base: str
    field_path: str


@dataclass(frozen=True)
class FunctionPointerSummary:
    function: str
    parameters: Tuple[Optional[str], ...] = ()
    result: Optional[str] = None
    signature: Optional[str] = None


@dataclass(frozen=True)
class CallConstraint:
    id: str
    caller: str
    actuals: Tuple[Optional[str], ...] = ()
    result: Optional[str] = None
    direct_target: Optional[str] = None
    callee_pointer: Optional[str] = None
    encoded_function_base: Optional[str] = None
    location: Optional[str] = None
    signature: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.id:
            raise GraphValidationError("call constraint id cannot be empty")
        if not self.caller:
            raise GraphValidationError(f"call {self.id!r} has no caller")
        direct = self.direct_target is not None
        indirect = self.callee_pointer is not None
        if direct == indirect:
            raise GraphValidationError(
                f"call {self.id!r} must have exactly one of direct_target "
                "or callee_pointer"
            )
        if self.encoded_function_base is not None and not indirect:
            raise GraphValidationError(
                f"call {self.id!r} has an encoded function base but is "
                "not indirect"
            )

    @property
    def is_indirect(self) -> bool:
        return self.callee_pointer is not None


@dataclass
class PointerProgram:
    """Mergeable points-to constraints extracted from LLVM modules."""

    addresses: Set[AddressConstraint] = field(default_factory=set)
    copies: Set[CopyConstraint] = field(default_factory=set)
    loads: Set[LoadConstraint] = field(default_factory=set)
    stores: Set[StoreConstraint] = field(default_factory=set)
    geps: Set[GepConstraint] = field(default_factory=set)
    summaries: Dict[str, FunctionPointerSummary] = field(default_factory=dict)
    calls: Dict[str, CallConstraint] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_summary(self, summary: FunctionPointerSummary) -> None:
        previous = self.summaries.get(summary.function)
        if previous is not None and previous != summary:
            raise GraphValidationError(
                f"conflicting pointer summaries for {summary.function!r}"
            )
        self.summaries[summary.function] = summary

    def add_call(self, call: CallConstraint) -> None:
        previous = self.calls.get(call.id)
        if previous is not None and previous != call:
            raise GraphValidationError(
                f"conflicting call constraints for {call.id!r}"
            )
        self.calls[call.id] = call

    def merge(self, other: "PointerProgram") -> None:
        self.addresses.update(other.addresses)
        self.copies.update(other.copies)
        self.loads.update(other.loads)
        self.stores.update(other.stores)
        self.geps.update(other.geps)
        for summary in other.summaries.values():
            self.add_summary(summary)
        for call in other.calls.values():
            self.add_call(call)
        translation_units = set(self.metadata.get("translation_units", []))
        translation_units.update(other.metadata.get("translation_units", []))
        if translation_units:
            self.metadata["translation_units"] = sorted(translation_units)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": POINTER_SCHEMA_VERSION,
            "metadata": _json_object(self.metadata),
            "addresses": [
                {"pointer": item.pointer, "target": item.target}
                for item in sorted(self.addresses)
            ],
            "copies": [
                {"destination": item.destination, "source": item.source}
                for item in sorted(self.copies)
            ],
            "loads": [
                {"destination": item.destination, "pointer": item.pointer}
                for item in sorted(self.loads)
            ],
            "stores": [
                {"pointer": item.pointer, "source": item.source}
                for item in sorted(self.stores)
            ],
            "geps": [
                {
                    "destination": item.destination,
                    "base": item.base,
                    "field_path": item.field_path,
                }
                for item in sorted(self.geps)
            ],
            "summaries": [
                {
                    "function": item.function,
                    "parameters": list(item.parameters),
                    "result": item.result,
                    "signature": item.signature,
                }
                for item in sorted(
                    self.summaries.values(),
                    key=lambda summary: summary.function,
                )
            ],
            "calls": [
                {
                    "id": item.id,
                    "caller": item.caller,
                    "actuals": list(item.actuals),
                    "result": item.result,
                    "direct_target": item.direct_target,
                    "callee_pointer": item.callee_pointer,
                    "encoded_function_base": item.encoded_function_base,
                    "location": item.location,
                    "signature": item.signature,
                }
                for item in sorted(self.calls.values(), key=lambda call: call.id)
            ],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PointerProgram":
        version = raw.get("schema_version")
        if version != POINTER_SCHEMA_VERSION:
            raise SchemaVersionError(
                f"unsupported pointer schema_version={version!r}; "
                f"expected {POINTER_SCHEMA_VERSION}"
            )
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise GraphValidationError("pointer metadata must be an object")
        program = cls(metadata=dict(metadata))
        try:
            for item in _object_list(raw, "addresses"):
                program.addresses.add(
                    AddressConstraint(
                        _required_string(item, "pointer"),
                        _required_string(item, "target"),
                    )
                )
            for item in _object_list(raw, "copies"):
                program.copies.add(
                    CopyConstraint(
                        _required_string(item, "destination"),
                        _required_string(item, "source"),
                    )
                )
            for item in _object_list(raw, "loads"):
                program.loads.add(
                    LoadConstraint(
                        _required_string(item, "destination"),
                        _required_string(item, "pointer"),
                    )
                )
            for item in _object_list(raw, "stores"):
                program.stores.add(
                    StoreConstraint(
                        _required_string(item, "pointer"),
                        _required_string(item, "source"),
                    )
                )
            for item in _object_list(raw, "geps"):
                program.geps.add(
                    GepConstraint(
                        _required_string(item, "destination"),
                        _required_string(item, "base"),
                        _required_string(item, "field_path"),
                    )
                )
            for item in _object_list(raw, "summaries"):
                parameters = _optional_string_list(item.get("parameters", []))
                program.add_summary(
                    FunctionPointerSummary(
                        _required_string(item, "function"),
                        tuple(parameters),
                        _optional_string(item.get("result")),
                        _optional_string(item.get("signature")),
                    )
                )
            for item in _object_list(raw, "calls"):
                actuals = _optional_string_list(item.get("actuals", []))
                program.add_call(
                    CallConstraint(
                        id=_required_string(item, "id"),
                        caller=_required_string(item, "caller"),
                        actuals=tuple(actuals),
                        result=_optional_string(item.get("result")),
                        direct_target=_optional_string(item.get("direct_target")),
                        callee_pointer=_optional_string(
                            item.get("callee_pointer")
                        ),
                        encoded_function_base=_optional_string(
                            item.get("encoded_function_base")
                        ),
                        location=_optional_string(item.get("location")),
                        signature=_optional_string(item.get("signature")),
                    )
                )
        except (TypeError, ValueError) as error:
            raise GraphValidationError(
                f"invalid pointer constraint object: {error}"
            ) from error
        return program

    @classmethod
    def from_json(cls, content: str) -> "PointerProgram":
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as error:
            raise GraphValidationError(
                f"invalid pointer-program JSON: {error}"
            ) from error
        if not isinstance(raw, Mapping):
            raise GraphValidationError("pointer-program root must be an object")
        return cls.from_dict(raw)


@dataclass(frozen=True)
class PointerAnalysisResult:
    points_to: Mapping[str, frozenset[str]]
    call_targets: Mapping[str, frozenset[str]]
    unresolved_calls: frozenset[str]
    iterations: int
    points_to_variables: int = 0
    points_to_relations: int = 0
    max_points_to_set: int = 0
    solver: str = "python-set-worklist-v1"
    saturated_variables: int = 0
    saturated_calls: int = 0
    points_to_limit: Optional[int] = None
    saturation_reasons: Mapping[str, int] = field(default_factory=dict)
    saturation_samples: Tuple[Tuple[str, str], ...] = ()
    global_memory_trigger: Optional[str] = None
    saturated_memory_families: Tuple[str, ...] = ()
    saturated_call_ids: frozenset[str] = frozenset()
    field_resolved_calls: int = 0
    field_unresolved_calls: int = 0
    field_points_to_variables: int = 0
    field_points_to_relations: int = 0
    field_slice_resolved_calls: int = 0
    field_slice_visited_nodes: int = 0
    field_slice_budget_calls: int = 0
    field_fallback_calls: int = 0

    def add_call_edges(self, graph: ReferenceGraph, program: PointerProgram) -> None:
        """Add resolved and unresolved call edges to ``graph``."""

        (
            unresolved_field_paths,
            unresolved_parameters,
            unresolved_objects,
        ) = _infer_indirect_call_provenance(
            program, self.unresolved_calls
        )
        for edge in _infer_static_function_argument_edges(program):
            if (
                edge.source in graph.nodes
                and edge.target in graph.nodes
            ):
                graph.add_edge(edge)
        for function_id, summary in sorted(program.summaries.items()):
            if summary.signature is None or function_id not in graph.nodes:
                continue
            node = graph.nodes[function_id]
            graph.merge_node(
                replace(
                    node,
                    attributes=node.attributes.union(
                        {f"abi_signature={summary.signature}"}
                    ),
                )
            )

        for call_id, call in sorted(program.calls.items()):
            if call.caller not in graph.nodes:
                raise GraphValidationError(
                    f"call {call_id!r} caller {call.caller!r} is absent "
                    "from the reference graph"
                )
            targets = self.call_targets.get(call_id, frozenset())
            for target in sorted(targets):
                if target not in graph.nodes:
                    raise GraphValidationError(
                        f"call {call_id!r} target {target!r} is absent "
                        "from the reference graph"
                    )
                graph.add_edge(
                    ReferenceEdge(
                        source=call.caller,
                        target=target,
                        kind=(
                            EdgeKind.INDIRECT_CALL
                            if call.is_indirect
                            else EdgeKind.DIRECT_CALL
                        ),
                        location=call.location,
                        evidence=frozenset({"fixed-point-points-to"}),
                    )
                )
            if call_id in self.unresolved_calls:
                attributes = {"indirect_call"}
                if call.signature is not None:
                    attributes.add(f"abi_signature={call.signature}")
                if call.encoded_function_base is not None:
                    attributes.add(
                        "encoded_function_base="
                        f"{call.encoded_function_base}"
                    )
                attributes.update(
                    f"callback_field={field_path}"
                    for field_path in unresolved_field_paths.get(
                        call_id, ()
                    )
                )
                attributes.update(
                    f"callback_parameter={parameter}"
                    for parameter in unresolved_parameters.get(
                        call_id, ()
                    )
                )
                attributes.update(
                    f"callback_object={memory_object}"
                    for memory_object in unresolved_objects.get(
                        call_id, ()
                    )
                )
                unresolved = ReferenceNode.entity(
                    EntityKind.UNRESOLVED,
                    call_id,
                    owner=call.caller,
                    attributes=attributes,
                )
                graph.add_node(unresolved)
                graph.add_edge(
                    ReferenceEdge(
                        source=call.caller,
                        target=unresolved.id,
                        kind=EdgeKind.UNRESOLVED_CALL,
                        location=call.location,
                        evidence=frozenset({"fixed-point-points-to"}),
                    )
                )


def _infer_indirect_call_provenance(
    program: PointerProgram,
    call_ids: Iterable[str],
    *,
    node_limit: int = 4096,
) -> Tuple[
    Mapping[str, frozenset[str]],
    Mapping[str, frozenset[str]],
    Mapping[str, frozenset[str]],
]:
    """Recover static field, formal-parameter, and object provenance.

    LLVM commonly represents ``ops->callback(...)`` as a GEP, a pointer
    load, and an indirect call, with casts/phis in between.  Preserve those
    field paths on the unresolved node so later closed-world planning can
    distinguish an absent external-module provider from an arbitrary
    built-in function target.  Exceeding the bounded reverse walk yields no
    field evidence and therefore retains the broader conservative fallback.
    """

    reverse_copies: Dict[str, Set[str]] = defaultdict(set)
    for constraint in program.copies:
        reverse_copies[constraint.destination].add(constraint.source)
    for call in program.calls.values():
        if call.direct_target is None:
            continue
        summary = program.summaries.get(call.direct_target)
        if summary is None:
            continue
        for actual, formal in zip(call.actuals, summary.parameters):
            if actual is not None and formal is not None:
                reverse_copies[formal].add(actual)
        if call.result is not None and summary.result is not None:
            reverse_copies[call.result].add(summary.result)

    gep_paths: Dict[str, Set[str]] = defaultdict(set)
    for constraint in program.geps:
        gep_paths[constraint.destination].add(constraint.field_path)
    load_pointers: Dict[str, Set[str]] = defaultdict(set)
    for constraint in program.loads:
        load_pointers[constraint.destination].add(constraint.pointer)
    stored_values: Dict[str, Set[str]] = defaultdict(set)
    for constraint in program.stores:
        stored_values[constraint.pointer].add(constraint.source)
    memory_objects: Dict[str, Set[str]] = defaultdict(set)
    for constraint in program.addresses:
        if _is_memory_object(constraint.target):
            memory_objects[constraint.pointer].add(constraint.target)
    formal_parameters: Dict[str, Set[str]] = defaultdict(set)
    for function_id, summary in program.summaries.items():
        for index, parameter in enumerate(summary.parameters):
            if parameter is not None:
                formal_parameters[parameter].add(
                    f"{function_id}:{index}"
                )

    def provenance_reaching_pointer(
        pointer: str,
    ) -> Optional[Tuple[Set[str], Set[str], Set[str]]]:
        paths: Set[str] = set()
        objects: Set[str] = set()
        values: Set[str] = set()
        pending = [pointer]
        visited: Set[str] = set()
        while pending:
            variable = pending.pop()
            if variable in visited:
                continue
            visited.add(variable)
            if len(visited) > node_limit:
                return None
            paths.update(gep_paths.get(variable, ()))
            objects.update(memory_objects.get(variable, ()))
            values.update(stored_values.get(variable, ()))
            pending.extend(reverse_copies.get(variable, ()))
        return paths, objects, values

    result_fields: Dict[str, frozenset[str]] = {}
    result_parameters: Dict[str, frozenset[str]] = {}
    result_objects: Dict[str, frozenset[str]] = {}
    for call_id in sorted(set(call_ids)):
        call = program.calls.get(call_id)
        if call is None or not call.is_indirect:
            continue
        pending = [call.callee_pointer or ""]
        visited: Set[str] = set()
        paths: Set[str] = set()
        parameters: Set[str] = set()
        objects: Set[str] = set()
        complete = True
        while pending:
            variable = pending.pop()
            if variable in visited:
                continue
            visited.add(variable)
            if len(visited) > node_limit:
                complete = False
                break
            parameters.update(formal_parameters.get(variable, ()))
            for pointer in load_pointers.get(variable, ()):
                pointer_provenance = provenance_reaching_pointer(
                    pointer
                )
                if pointer_provenance is None:
                    complete = False
                    break
                (
                    pointer_paths,
                    pointer_objects,
                    pointer_values,
                ) = pointer_provenance
                paths.update(pointer_paths)
                objects.update(pointer_objects)
                pending.extend(pointer_values)
            if not complete:
                break
            pending.extend(reverse_copies.get(variable, ()))
        if not complete:
            continue
        if paths:
            result_fields[call_id] = frozenset(paths)
        if parameters:
            result_parameters[call_id] = frozenset(parameters)
        if objects:
            result_objects[call_id] = frozenset(objects)
    return result_fields, result_parameters, result_objects


def _infer_static_function_argument_edges(
    program: PointerProgram,
    *,
    node_limit: int = 4096,
) -> Tuple[ReferenceEdge, ...]:
    """Record direct function addresses passed into callback formals."""

    reverse_copies: Dict[str, Set[str]] = defaultdict(set)
    for constraint in program.copies:
        reverse_copies[constraint.destination].add(constraint.source)
    function_addresses: Dict[str, Set[str]] = defaultdict(set)
    for constraint in program.addresses:
        if _is_function(constraint.target):
            function_addresses[constraint.pointer].add(
                constraint.target
            )

    def static_targets(variable: str) -> Optional[Set[str]]:
        targets: Set[str] = set()
        pending = [variable]
        visited: Set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            if len(visited) > node_limit:
                return None
            targets.update(function_addresses.get(current, ()))
            pending.extend(reverse_copies.get(current, ()))
        return targets

    edges: Set[ReferenceEdge] = set()
    for call in program.calls.values():
        if call.direct_target is None:
            continue
        for index, actual in enumerate(call.actuals):
            if actual is None:
                continue
            targets = static_targets(actual)
            if targets is None:
                continue
            parameter = f"{call.direct_target}:{index}"
            for target in targets:
                edges.add(
                    ReferenceEdge(
                        source=call.caller,
                        target=target,
                        kind=EdgeKind.FUNCTION_ARGUMENT,
                        location=call.location,
                        field_path=parameter,
                        evidence=frozenset(
                            {"llvm-static-argument-flow"}
                        ),
                    )
                )
    return tuple(sorted(edges))


def solve_pointer_constraints(
    program: PointerProgram,
    *,
    retain_points_to: bool = True,
    backend: str = "auto",
    max_points_to_set: Optional[int] = None,
) -> PointerAnalysisResult:
    """Solve inclusion constraints with an exact delta worklist."""

    if backend not in {"auto", "python", "roaring", "hybrid"}:
        raise GraphValidationError(
            f"unknown pointer-analysis backend {backend!r}"
        )
    if max_points_to_set is not None and max_points_to_set < 1:
        raise GraphValidationError(
            "max_points_to_set must be positive or None"
        )
    selected = (
        "hybrid"
        if backend == "auto" and _RoaringBitMap is not None
        else backend
    )
    if selected == "auto":
        selected = "python"
    if selected in {"roaring", "hybrid"}:
        if _RoaringBitMap is None:
            raise GraphValidationError(
                "the roaring pointer backend requires the optional "
                "'pyroaring' package"
            )
        base = _solve_pointer_constraints_roaring(
            program,
            retain_points_to=retain_points_to,
            max_points_to_set=max_points_to_set,
        )
        if selected == "hybrid":
            field = _solve_field_collapsed_functions(
                program,
                max_points_to_set=max_points_to_set,
            )
            return _merge_hybrid_results(base, field, program)
        return base
    if max_points_to_set is not None and (
        len(program.addresses)
        + len(program.loads)
        + len(program.stores)
        + len(program.geps)
        > 500_000
    ):
        raise GraphValidationError(
            "budgeted full-kernel pointer analysis requires the optional "
            "Roaring backend; install the 'full-kernel' extra"
        )
    return _solve_pointer_constraints_python(
        program,
        retain_points_to=retain_points_to,
    )


def _solve_pointer_constraints_python(
    program: PointerProgram,
    *,
    retain_points_to: bool,
) -> PointerAnalysisResult:
    """Reference worklist using Python sets; intended for smaller graphs."""

    points_to: Dict[str, Set[str]] = {}
    pending: Dict[str, list[str]] = {}
    worklist: deque[str] = deque()
    queued: Set[str] = set()

    def values(variable: str) -> Set[str]:
        return points_to.setdefault(variable, set())

    def add_points(variable: str, targets: Iterable[str]) -> bool:
        destination = values(variable)
        additions = []
        for target in targets:
            if target in destination:
                continue
            destination.add(target)
            additions.append(target)
        if not additions:
            return False
        pending.setdefault(variable, []).extend(additions)
        if variable not in queued:
            queued.add(variable)
            worklist.append(variable)
        return True

    call_targets: Dict[str, Set[str]] = {
        call_id: set() for call_id in program.calls
    }
    copy_edges: Dict[str, Set[str]] = defaultdict(set)
    gep_edges: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
    load_watchers: Dict[str, Set[str]] = defaultdict(set)
    store_watchers: Dict[str, Set[str]] = defaultdict(set)
    indirect_watchers: Dict[str, Set[str]] = defaultdict(set)
    activated_targets: Dict[str, Set[str]] = defaultdict(set)

    def add_copy_edge(source: str, destination: str) -> None:
        if destination in copy_edges[source]:
            return
        copy_edges[source].add(destination)
        add_points(destination, values(source))

    def activate_call_target(call_id: str, target: str) -> None:
        if target in activated_targets[call_id]:
            return
        call = program.calls[call_id]
        summary = program.summaries.get(target)
        if (
            call.is_indirect
            and summary is not None
            and not abi_signatures_compatible(
                call.signature, summary.signature
            )
        ):
            return
        activated_targets[call_id].add(target)
        call_targets[call_id].add(target)
        if summary is None:
            return
        for actual, formal in zip(call.actuals, summary.parameters):
            if actual is not None and formal is not None:
                add_copy_edge(actual, formal)
        if call.result is not None and summary.result is not None:
            add_copy_edge(summary.result, call.result)

    for constraint in sorted(program.copies):
        add_copy_edge(constraint.source, constraint.destination)
    for constraint in sorted(program.geps):
        gep_edges[constraint.base].add(
            (constraint.destination, constraint.field_path)
        )
    for constraint in sorted(program.loads):
        load_watchers[constraint.pointer].add(constraint.destination)
    for constraint in sorted(program.stores):
        store_watchers[constraint.pointer].add(constraint.source)
    for call_id, call in sorted(program.calls.items()):
        if call.direct_target is not None:
            activate_call_target(call_id, call.direct_target)
        else:
            indirect_watchers[call.callee_pointer or ""].add(call_id)
    for constraint in sorted(program.addresses):
        add_points(constraint.pointer, (constraint.target,))

    iterations = 0
    while worklist:
        iterations += 1
        variable = worklist.popleft()
        queued.remove(variable)
        additions = pending.pop(variable)
        for destination in sorted(copy_edges.get(variable, ())):
            add_points(destination, additions)
        for destination, field_path in sorted(
            gep_edges.get(variable, ())
        ):
            add_points(
                destination,
                (
                    _field_object(target, field_path)
                    for target in additions
                    if _is_memory_object(target)
                ),
            )
        memory_targets = tuple(
            target for target in additions if _is_memory_object(target)
        )
        if memory_targets:
            for destination in sorted(load_watchers.get(variable, ())):
                for target in memory_targets:
                    add_copy_edge(_memory_cell(target), destination)
            for source in sorted(store_watchers.get(variable, ())):
                for target in memory_targets:
                    add_copy_edge(source, _memory_cell(target))
        function_targets = tuple(
            target for target in additions if _is_function(target)
        )
        if function_targets:
            for call_id in sorted(indirect_watchers.get(variable, ())):
                for target in sorted(function_targets):
                    activate_call_target(call_id, target)

    unresolved: Set[str] = set()
    for call_id, call in program.calls.items():
        if not call.is_indirect:
            continue
        raw_targets = values(call.callee_pointer or "")
        function_targets = call_targets[call_id]
        if not function_targets or any(
            not _is_function(target) for target in raw_targets
        ):
            unresolved.add(call_id)

    points_to_variables = len(points_to)
    points_to_relations = sum(len(targets) for targets in points_to.values())
    largest_points_to_set = max(
        (len(targets) for targets in points_to.values()),
        default=0,
    )
    retained_points_to = (
        {
            variable: frozenset(targets)
            for variable, targets in sorted(points_to.items())
        }
        if retain_points_to
        else {}
    )
    return PointerAnalysisResult(
        points_to=retained_points_to,
        call_targets={
            call_id: frozenset(targets)
            for call_id, targets in sorted(call_targets.items())
        },
        unresolved_calls=frozenset(unresolved),
        iterations=max(1, iterations),
        points_to_variables=points_to_variables,
        points_to_relations=points_to_relations,
        max_points_to_set=largest_points_to_set,
        solver="python-set-worklist-v1",
    )


def _solve_pointer_constraints_roaring(
    program: PointerProgram,
    *,
    retain_points_to: bool,
    max_points_to_set: Optional[int],
) -> PointerAnalysisResult:
    """Exact worklist with interned targets and compressed Roaring sets."""

    assert _RoaringBitMap is not None
    target_ids: Dict[str, int] = {}
    target_names: list[str] = []

    def intern_target(target: str) -> int:
        existing = target_ids.get(target)
        if existing is not None:
            return existing
        identifier = len(target_names)
        if identifier >= 2**32:
            raise GraphValidationError(
                "Roaring target-id space exceeded 32 bits"
            )
        target_ids[target] = identifier
        target_names.append(target)
        return identifier

    points_to: Dict[str, Any] = {}
    pending: Dict[str, Any] = {}
    worklist: deque[str] = deque()
    queued: Set[str] = set()
    saturation_pending: Set[str] = set()
    saturated_variables: Set[str] = set()
    saturated_calls: Set[str] = set()
    global_memory_saturated = False
    global_memory_trigger: Optional[str] = None
    saturation_reasons: Counter[str] = Counter()
    saturation_samples: list[Tuple[str, str]] = []
    variable_memory_families: Dict[str, Set[str]] = defaultdict(set)
    tainted_memory_families: Set[str] = set()
    load_destinations_by_family: Dict[str, Set[str]] = defaultdict(set)

    def target_memory_families(targets: Iterable[int]) -> Set[str]:
        return {
            family
            for target_id in targets
            if (
                family := _memory_field_family(
                    target_names[target_id]
                )
            )
            is not None
        }

    def enqueue(variable: str) -> None:
        if variable not in queued:
            queued.add(variable)
            worklist.append(variable)

    def values(variable: str):
        bitmap = points_to.get(variable)
        if bitmap is None:
            bitmap = _RoaringBitMap()
            points_to[variable] = bitmap
        return bitmap

    def mark_saturated(
        variable: str,
        reason: str,
        *,
        additional_targets: Iterable[int] = (),
        forced_families: Iterable[str] = (),
    ) -> bool:
        families = set(forced_families)
        current = points_to.get(variable)
        if current is not None:
            families.update(target_memory_families(current))
        families.update(target_memory_families(additional_targets))
        if variable in saturated_variables:
            additions = families.difference(
                variable_memory_families[variable]
            )
            if additions:
                variable_memory_families[variable].update(additions)
                saturation_pending.add(variable)
                enqueue(variable)
            return False
        saturated_variables.add(variable)
        variable_memory_families[variable].update(families)
        saturation_reasons[reason] += 1
        if len(saturation_samples) < 100:
            saturation_samples.append((variable, reason))
        points_to[variable] = _RoaringBitMap()
        pending.pop(variable, None)
        saturation_pending.add(variable)
        enqueue(variable)
        return True

    def add_points(variable: str, targets: Iterable[int]) -> bool:
        candidates = (
            targets.copy()
            if isinstance(targets, _RoaringBitMap)
            else _RoaringBitMap(targets)
        )
        if variable in saturated_variables:
            mark_saturated(
                variable,
                "additional-targets-after-saturation",
                additional_targets=candidates,
            )
            return False
        destination = values(variable)
        candidates -= destination
        if not candidates:
            return False
        if (
            max_points_to_set is not None
            and len(destination) + len(candidates) > max_points_to_set
        ):
            return mark_saturated(
                variable,
                "points-to-limit",
                additional_targets=candidates,
            )
        destination |= candidates
        existing = pending.get(variable)
        if existing is None:
            pending[variable] = candidates
        else:
            existing |= candidates
        enqueue(variable)
        return True

    call_targets: Dict[str, Any] = {
        call_id: _RoaringBitMap() for call_id in program.calls
    }
    copy_edges: Dict[str, Set[str]] = defaultdict(set)
    gep_edges: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
    load_watchers: Dict[str, Set[str]] = defaultdict(set)
    store_watchers: Dict[str, Set[str]] = defaultdict(set)
    indirect_watchers: Dict[str, Set[str]] = defaultdict(set)
    activated_targets: Dict[str, Any] = {}

    def add_copy_edge(source: str, destination: str) -> None:
        if destination in copy_edges[source]:
            return
        copy_edges[source].add(destination)
        if source in saturated_variables:
            mark_saturated(
                destination,
                "copy-from-saturated",
                forced_families=variable_memory_families[source],
            )
            return
        add_points(destination, values(source))

    def activate_call_target(call_id: str, target_id: int) -> None:
        activated = activated_targets.get(call_id)
        if activated is None:
            activated = _RoaringBitMap()
            activated_targets[call_id] = activated
        if target_id in activated:
            return
        target = target_names[target_id]
        call = program.calls[call_id]
        summary = program.summaries.get(target)
        if (
            call.is_indirect
            and summary is not None
            and not abi_signatures_compatible(
                call.signature, summary.signature
            )
        ):
            return
        activated.add(target_id)
        call_targets[call_id].add(target_id)
        if summary is None:
            return
        for actual, formal in zip(call.actuals, summary.parameters):
            if actual is not None and formal is not None:
                add_copy_edge(actual, formal)
        if call.result is not None and summary.result is not None:
            add_copy_edge(summary.result, call.result)

    for constraint in sorted(program.copies):
        add_copy_edge(constraint.source, constraint.destination)
    for constraint in sorted(program.geps):
        gep_edges[constraint.base].add(
            (constraint.destination, constraint.field_path)
        )
    for constraint in sorted(program.loads):
        load_watchers[constraint.pointer].add(constraint.destination)
    for constraint in sorted(program.stores):
        store_watchers[constraint.pointer].add(constraint.source)
    for call_id, call in sorted(program.calls.items()):
        if call.direct_target is not None:
            activate_call_target(
                call_id,
                intern_target(call.direct_target),
            )
        else:
            indirect_watchers[call.callee_pointer or ""].add(call_id)

    all_load_destinations = tuple(
        sorted(
            {
                destination
                for destinations in load_watchers.values()
                for destination in destinations
            }
        )
    )

    def saturate_unknown_memory(
        trigger: str,
        families: Iterable[str],
    ) -> None:
        nonlocal global_memory_saturated, global_memory_trigger
        selected_families = set(families)
        if selected_families:
            new_families = selected_families.difference(
                tainted_memory_families
            )
            tainted_memory_families.update(new_families)
            for family in new_families:
                for destination in load_destinations_by_family.get(
                    family, ()
                ):
                    mark_saturated(
                        destination,
                        "unknown-memory-family-load",
                    )
            return
        if global_memory_saturated:
            return
        global_memory_saturated = True
        global_memory_trigger = trigger
        for destination in all_load_destinations:
            mark_saturated(destination, "unknown-memory-load")

    for constraint in sorted(program.addresses):
        add_points(
            constraint.pointer,
            (intern_target(constraint.target),),
        )

    iterations = 0
    while worklist:
        iterations += 1
        variable = worklist.popleft()
        queued.remove(variable)
        if variable in saturation_pending:
            saturation_pending.remove(variable)
            pending.pop(variable, None)
            for destination in copy_edges.get(variable, ()):
                mark_saturated(
                    destination,
                    "copy-from-saturated",
                    forced_families=variable_memory_families[variable],
                )
            for destination, field_path in gep_edges.get(variable, ()):
                mark_saturated(
                    destination,
                    "gep-from-saturated",
                    forced_families=(field_path,),
                )
            for destination in load_watchers.get(variable, ()):
                mark_saturated(destination, "load-from-saturated-pointer")
            if store_watchers.get(variable):
                saturate_unknown_memory(
                    variable,
                    variable_memory_families[variable],
                )
            for call_id in indirect_watchers.get(variable, ()):
                saturated_calls.add(call_id)
                call_targets[call_id] = _RoaringBitMap()
            continue
        additions = pending.pop(variable)
        for destination in sorted(copy_edges.get(variable, ())):
            add_points(destination, additions)
        for destination, field_path in sorted(
            gep_edges.get(variable, ())
        ):
            add_points(
                destination,
                (
                    intern_target(
                        _field_object(target_names[target_id], field_path)
                    )
                    for target_id in additions
                    if _is_memory_object(target_names[target_id])
                ),
            )
        memory_targets = tuple(
            target_id
            for target_id in additions
            if _is_memory_object(target_names[target_id])
        )
        if memory_targets:
            for destination in sorted(load_watchers.get(variable, ())):
                for target_id in memory_targets:
                    family = _memory_field_family(
                        target_names[target_id]
                    )
                    if family is not None:
                        load_destinations_by_family[family].add(
                            destination
                        )
                        if (
                            global_memory_saturated
                            or family in tainted_memory_families
                        ):
                            mark_saturated(
                                destination,
                                "unknown-memory-family-load",
                            )
                    add_copy_edge(
                        _memory_cell(target_names[target_id]),
                        destination,
                    )
            for source in sorted(store_watchers.get(variable, ())):
                for target_id in memory_targets:
                    add_copy_edge(
                        source,
                        _memory_cell(target_names[target_id]),
                    )
        function_targets = tuple(
            target_id
            for target_id in additions
            if _is_function(target_names[target_id])
        )
        if function_targets:
            for call_id in sorted(indirect_watchers.get(variable, ())):
                for target_id in function_targets:
                    activate_call_target(call_id, target_id)

    unresolved: Set[str] = set()
    for call_id, call in program.calls.items():
        if not call.is_indirect:
            continue
        raw_targets = values(call.callee_pointer or "")
        function_targets = call_targets[call_id]
        if not function_targets or any(
            not _is_function(target_names[target_id])
            for target_id in raw_targets
        ):
            unresolved.add(call_id)
    unresolved.update(saturated_calls)

    points_to_variables = len(points_to)
    points_to_relations = sum(len(targets) for targets in points_to.values())
    largest_points_to_set = max(
        (len(targets) for targets in points_to.values()),
        default=0,
    )
    retained_points_to = (
        {
            variable: frozenset(
                target_names[target_id] for target_id in targets
            )
            for variable, targets in sorted(points_to.items())
        }
        if retain_points_to
        else {}
    )
    return PointerAnalysisResult(
        points_to=retained_points_to,
        call_targets={
            call_id: frozenset(
                target_names[target_id] for target_id in targets
            )
            for call_id, targets in sorted(call_targets.items())
        },
        unresolved_calls=frozenset(unresolved),
        iterations=max(1, iterations),
        points_to_variables=points_to_variables,
        points_to_relations=points_to_relations,
        max_points_to_set=largest_points_to_set,
        solver=(
            "roaring-worklist-v1-bounded"
            if max_points_to_set is not None
            else "roaring-worklist-v1"
        ),
        saturated_variables=len(saturated_variables),
        saturated_calls=len(saturated_calls),
        points_to_limit=max_points_to_set,
        saturation_reasons=dict(sorted(saturation_reasons.items())),
        saturation_samples=tuple(saturation_samples),
        global_memory_trigger=global_memory_trigger,
        saturated_memory_families=tuple(
            sorted(tainted_memory_families)
        ),
        saturated_call_ids=frozenset(saturated_calls),
    )


@dataclass(frozen=True)
class _FieldAnalysisResult:
    call_targets: Mapping[str, frozenset[str]]
    resolved_calls: frozenset[str]
    unresolved_calls: frozenset[str]
    saturated_calls: frozenset[str]
    points_to_variables: int
    points_to_relations: int
    iterations: int
    slice_resolved_calls: int = 0
    slice_visited_nodes: int = 0
    slice_budget_calls: int = 0


def _solve_field_collapsed_functions(
    program: PointerProgram,
    *,
    max_points_to_set: Optional[int],
) -> _FieldAnalysisResult:
    """Resolve callbacks in a compact type-and-field memory abstraction.

    Only function targets are propagated.  Every GEP with the same typed
    field path shares one abstract memory cell, while non-GEP roots retain
    their exact allocation/global object identity.  This intentionally
    over-approximates targets across instances of one ops structure without
    allowing unrelated data arrays to poison every callback load.
    """

    assert _RoaringBitMap is not None
    limit = max_points_to_set
    variable_ids: Dict[str, int] = {}
    next_variable_id = 0

    def variable_id(name: str) -> int:
        nonlocal next_variable_id
        existing = variable_ids.get(name)
        if existing is not None:
            return existing
        identifier = next_variable_id
        next_variable_id += 1
        variable_ids[name] = identifier
        return identifier

    function_ids: Dict[str, int] = {}
    function_names: list[str] = []
    function_signatures: list[Optional[str]] = []
    function_signature_group_ids: list[int] = []
    function_signature_group_names: list[Optional[str]] = []
    function_signature_groups: Dict[Optional[str], int] = {}

    def function_id(name: str) -> int:
        existing = function_ids.get(name)
        if existing is not None:
            return existing
        identifier = len(function_names)
        function_ids[name] = identifier
        function_names.append(name)
        summary = program.summaries.get(name)
        signature = summary.signature if summary is not None else None
        function_signatures.append(signature)
        signature_group = function_signature_groups.get(signature)
        if signature_group is None:
            signature_group = len(function_signature_group_names)
            function_signature_groups[signature] = signature_group
            function_signature_group_names.append(signature)
        function_signature_group_ids.append(signature_group)
        return identifier

    copy_edges: Dict[int, Set[int]] = defaultdict(set)
    reverse_copy_edges: Dict[int, Set[int]] = defaultdict(set)
    values: Dict[int, Any] = {}
    pending: Dict[int, Any] = {}
    worklist: deque[int] = deque()
    queued: Set[int] = set()
    saturated: Set[int] = set()
    demand_ready = False
    demands: Dict[int, Any] = {}
    call_signature_names: list[Optional[str]] = []
    compatible_groups_by_call_signature: Dict[int, frozenset[int]] = {}
    compatible_groups_by_demands: Dict[
        tuple[int, ...], frozenset[int]
    ] = {}

    def groups_compatible_with_call(
        call_signature_id: int,
    ) -> frozenset[int]:
        cached = compatible_groups_by_call_signature.get(
            call_signature_id
        )
        if cached is not None:
            return cached
        call_signature = call_signature_names[call_signature_id]
        compatible = frozenset(
            group_id
            for group_id, target_signature in enumerate(
                function_signature_group_names
            )
            if abi_signatures_compatible(
                call_signature, target_signature
            )
        )
        compatible_groups_by_call_signature[
            call_signature_id
        ] = compatible
        return compatible

    def groups_compatible_with_demands(
        demand_ids,
    ) -> frozenset[int]:
        key = tuple(demand_ids)
        cached = compatible_groups_by_demands.get(key)
        if cached is not None:
            return cached
        compatible: Set[int] = set()
        for call_signature_id in key:
            compatible.update(
                groups_compatible_with_call(call_signature_id)
            )
        result = frozenset(compatible)
        compatible_groups_by_demands[key] = result
        return result

    def enqueue(variable: int) -> None:
        if variable not in queued:
            queued.add(variable)
            worklist.append(variable)

    def value_set(variable: int):
        bitmap = values.get(variable)
        if bitmap is None:
            bitmap = _RoaringBitMap()
            values[variable] = bitmap
        return bitmap

    def mark_saturated(variable: int) -> None:
        if variable in saturated:
            return
        saturated.add(variable)
        values[variable] = _RoaringBitMap()
        pending.pop(variable, None)
        enqueue(variable)

    def add_values(variable: int, additions) -> None:
        if variable in saturated:
            return
        candidates = (
            additions.copy()
            if isinstance(additions, _RoaringBitMap)
            else _RoaringBitMap(additions)
        )
        if candidates and demand_ready:
            variable_demands = demands.get(variable)
            if variable_demands is None:
                return
            compatible_groups = groups_compatible_with_demands(
                variable_demands
            )
            candidates = _RoaringBitMap([
                target_id
                for target_id in candidates
                if function_signature_group_ids[target_id]
                in compatible_groups
            ])
        destination = value_set(variable)
        candidates -= destination
        if not candidates:
            return
        # One shared source can legitimately feed many incompatible callback
        # signatures.  Give each demanded ABI domain its own bounded slice;
        # narrower destinations still filter first and retain the original
        # per-signature limit.  This is equivalent to signature-partitioned
        # saturation without allocating a bitmap dictionary per variable.
        effective_limit = limit
        if limit is not None and demand_ready:
            effective_limit *= max(
                1, len(demands.get(variable, ()))
            )
        if (
            effective_limit is not None
            and len(destination) + len(candidates) > effective_limit
        ):
            mark_saturated(variable)
            return
        destination |= candidates
        delta = pending.get(variable)
        if delta is None:
            pending[variable] = candidates
        else:
            delta |= candidates
        enqueue(variable)

    def add_copy_edge(source: int, destination: int) -> None:
        if destination in copy_edges[source]:
            return
        copy_edges[source].add(destination)
        reverse_copy_edges[destination].add(source)
        if demand_ready:
            destination_demands = demands.get(destination)
            if destination_demands is not None and add_demands(
                source, destination_demands
            ):
                drain_demands()
        if source in saturated:
            mark_saturated(destination)
        else:
            add_values(destination, value_set(source))

    static_copy_names: list[tuple[str, str]] = [
        (constraint.source, constraint.destination)
        for constraint in program.copies
    ]
    for call in program.calls.values():
        if call.direct_target is None:
            continue
        summary = program.summaries.get(call.direct_target)
        if summary is None:
            continue
        static_copy_names.extend(
            (actual, formal)
            for actual, formal in zip(call.actuals, summary.parameters)
            if actual is not None and formal is not None
        )
        if call.result is not None and summary.result is not None:
            static_copy_names.append((summary.result, call.result))
    for source, destination in static_copy_names:
        add_copy_edge(variable_id(source), variable_id(destination))

    # Compact location propagation.  Most variables carry one location;
    # represent that case as an int and allocate a set only at joins.
    location_ids: Dict[tuple[str, str], int] = {}

    def location_id(kind: str, value: str) -> int:
        key = (kind, value)
        existing = location_ids.get(key)
        if existing is not None:
            return existing
        identifier = len(location_ids)
        location_ids[key] = identifier
        return identifier

    locations: Dict[int, int | Set[int]] = {}

    def iter_locations(value: int | Set[int]) -> Iterable[int]:
        if isinstance(value, int):
            return (value,)
        return value

    def add_locations(variable: int, additions: Iterable[int]) -> bool:
        additions_set = set(additions)
        if not additions_set:
            return False
        current = locations.get(variable)
        if current is None:
            if len(additions_set) == 1:
                locations[variable] = next(iter(additions_set))
            else:
                locations[variable] = additions_set
            return True
        current_set = (
            {current} if isinstance(current, int) else current
        )
        new_values = additions_set.difference(current_set)
        if not new_values:
            return False
        merged = current_set.union(new_values)
        locations[variable] = (
            next(iter(merged)) if len(merged) == 1 else merged
        )
        return True

    location_worklist: deque[int] = deque()
    location_queued: Set[int] = set()

    def enqueue_location(variable: int) -> None:
        if variable not in location_queued:
            location_queued.add(variable)
            location_worklist.append(variable)

    for address in program.addresses:
        if not _is_memory_object(address.target):
            continue
        variable = variable_id(address.pointer)
        if add_locations(
            variable,
            (location_id("object", address.target),),
        ):
            enqueue_location(variable)
    for gep in program.geps:
        variable = variable_id(gep.destination)
        if add_locations(
            variable,
            (location_id("field", gep.field_path),),
        ):
            enqueue_location(variable)
    location_copy_edges: Dict[int, Set[int]] = defaultdict(set)
    for source, destination in static_copy_names:
        location_copy_edges[variable_id(source)].add(
            variable_id(destination)
        )
    while location_worklist:
        source = location_worklist.popleft()
        location_queued.remove(source)
        source_locations = locations[source]
        for destination in location_copy_edges.get(source, ()):
            if add_locations(
                destination, iter_locations(source_locations)
            ):
                enqueue_location(destination)

    memory_variables: Dict[int, int] = {}

    def memory_variable(location: int) -> int:
        existing = memory_variables.get(location)
        if existing is not None:
            return existing
        identifier = next_variable_id_for_memory()
        memory_variables[location] = identifier
        return identifier

    def next_variable_id_for_memory() -> int:
        nonlocal next_variable_id
        identifier = next_variable_id
        next_variable_id += 1
        return identifier

    for store in program.stores:
        pointer = variable_id(store.pointer)
        pointer_locations = locations.get(pointer)
        if pointer_locations is None:
            continue
        source = variable_id(store.source)
        for location in iter_locations(pointer_locations):
            add_copy_edge(source, memory_variable(location))
    for load in program.loads:
        pointer = variable_id(load.pointer)
        pointer_locations = locations.get(pointer)
        if pointer_locations is None:
            continue
        destination = variable_id(load.destination)
        for location in iter_locations(pointer_locations):
            add_copy_edge(memory_variable(location), destination)

    call_ids = sorted(
        call_id
        for call_id, call in program.calls.items()
        if call.is_indirect
    )
    call_indexes = {
        call_id: index for index, call_id in enumerate(call_ids)
    }
    call_watchers: Dict[int, list[int]] = defaultdict(list)
    for call_id in call_ids:
        call = program.calls[call_id]
        call_watchers[variable_id(call.callee_pointer or "")].append(
            call_indexes[call_id]
        )

    signature_ids: Dict[Optional[str], int] = {}

    def call_signature_id(signature: Optional[str]) -> int:
        existing = signature_ids.get(signature)
        if existing is not None:
            return existing
        identifier = len(call_signature_names)
        signature_ids[signature] = identifier
        call_signature_names.append(signature)
        return identifier

    function_seeds: Dict[int, Any] = {}
    for address in program.addresses:
        if not _is_function(address.target):
            continue
        variable = variable_id(address.pointer)
        target_id = function_id(address.target)
        seeds = function_seeds.get(variable)
        if seeds is None:
            seeds = _RoaringBitMap()
            function_seeds[variable] = seeds
        seeds.add(target_id)

    demand_pending: Dict[int, Any] = {}
    demand_worklist: deque[int] = deque()
    demand_queued: Set[int] = set()

    def enqueue_demand(variable: int) -> None:
        if variable not in demand_queued:
            demand_queued.add(variable)
            demand_worklist.append(variable)

    def add_demands(variable: int, additions) -> bool:
        candidates = (
            additions.copy()
            if isinstance(additions, _RoaringBitMap)
            else _RoaringBitMap(additions)
        )
        current = demands.get(variable)
        if current is None:
            current = _RoaringBitMap()
            demands[variable] = current
        candidates -= current
        if not candidates:
            return False
        current |= candidates
        pending = demand_pending.get(variable)
        if pending is None:
            demand_pending[variable] = candidates
        else:
            pending |= candidates
        enqueue_demand(variable)
        return True

    def drain_demands() -> None:
        while demand_worklist:
            variable = demand_worklist.popleft()
            demand_queued.remove(variable)
            additions = demand_pending.pop(variable)
            for predecessor in reverse_copy_edges.get(variable, ()):
                add_demands(predecessor, additions)
            seeds = function_seeds.get(variable)
            if seeds is not None:
                add_values(variable, seeds)
            for predecessor in reverse_copy_edges.get(variable, ()):
                predecessor_values = values.get(predecessor)
                if predecessor_values is not None:
                    add_values(variable, predecessor_values)

    demand_ready = True
    for call_id in call_ids:
        call = program.calls[call_id]
        add_demands(
            variable_id(call.callee_pointer or ""),
            (call_signature_id(call.signature),),
        )
    drain_demands()

    call_targets = [_RoaringBitMap() for _ in call_ids]
    activated = [_RoaringBitMap() for _ in call_ids]

    def activate(call_index: int, target_id: int) -> None:
        if target_id in activated[call_index]:
            return
        target = function_names[target_id]
        summary = program.summaries.get(target)
        call = program.calls[call_ids[call_index]]
        if (
            summary is not None
            and not abi_signatures_compatible(
                call.signature, summary.signature
            )
        ):
            return
        activated[call_index].add(target_id)
        call_targets[call_index].add(target_id)
        if summary is None:
            return
        for actual, formal in zip(call.actuals, summary.parameters):
            if actual is not None and formal is not None:
                add_copy_edge(variable_id(actual), variable_id(formal))
        if call.result is not None and summary.result is not None:
            add_copy_edge(
                variable_id(summary.result),
                variable_id(call.result),
            )

    saturated_calls: Set[str] = set()
    iterations = 0
    while worklist:
        iterations += 1
        source = worklist.popleft()
        queued.remove(source)
        if source in saturated:
            for destination in copy_edges.get(source, ()):
                mark_saturated(destination)
            for call_index in call_watchers.get(source, ()):
                saturated_calls.add(call_ids[call_index])
                call_targets[call_index] = _RoaringBitMap()
            continue
        delta = pending.pop(source)
        for destination in copy_edges.get(source, ()):
            add_values(destination, delta)
        for call_index in call_watchers.get(source, ()):
            for target_id in delta:
                activate(call_index, target_id)

    # A saturated forward value set does not make the constraint graph
    # unknowable.  Query the remaining call sites backwards through the
    # already-built inclusion graph.  A result is accepted only when the
    # complete reverse slice fits both the node and target budgets.
    slice_resolved: Set[str] = set()
    slice_visited_nodes = 0
    slice_budget_calls: Set[str] = set()
    slice_node_limit = max(65_536, (limit or 4096) * 64)

    def reverse_slice(call_id: str) -> tuple[Any, bool, int]:
        call = program.calls[call_id]
        start = variable_id(call.callee_pointer or "")
        queue = deque((start,))
        visited = {start}
        targets = _RoaringBitMap()
        while queue:
            variable = queue.popleft()
            seeds = function_seeds.get(variable)
            if seeds is not None:
                targets |= _RoaringBitMap(
                    target_id
                    for target_id in seeds
                    if abi_signatures_compatible(
                        call.signature,
                        function_signatures[target_id],
                    )
                )
                if limit is not None and len(targets) > limit:
                    return _RoaringBitMap(), False, len(visited)
            for predecessor in reverse_copy_edges.get(variable, ()):
                if predecessor in visited:
                    continue
                visited.add(predecessor)
                if len(visited) > slice_node_limit:
                    return _RoaringBitMap(), False, len(visited)
                queue.append(predecessor)
        return targets, True, len(visited)

    remaining = {
        call_id
        for call_id in call_ids
        if (
            variable_id(
                program.calls[call_id].callee_pointer or ""
            )
            in saturated
            or not call_targets[call_indexes[call_id]]
        )
    }
    for _ in range(4):
        recovered_this_round: Set[str] = set()
        for call_id in sorted(remaining):
            targets, complete, visited_count = reverse_slice(call_id)
            slice_visited_nodes += visited_count
            if not complete:
                slice_budget_calls.add(call_id)
                continue
            if not targets:
                continue
            call_index = call_indexes[call_id]
            for target_id in targets:
                activate(call_index, target_id)
            recovered_this_round.add(call_id)
        if not recovered_this_round:
            break
        slice_resolved.update(recovered_this_round)
        remaining.difference_update(recovered_this_round)

    result_targets = {}
    resolved_calls = set()
    unresolved_calls = set()
    for call_id in call_ids:
        index = call_indexes[call_id]
        callee = variable_id(
            program.calls[call_id].callee_pointer or ""
        )
        targets = frozenset(
            function_names[target_id]
            for target_id in call_targets[index]
        )
        result_targets[call_id] = targets
        if (
            call_id in slice_resolved
            or (callee not in saturated and targets)
        ):
            resolved_calls.add(call_id)
        else:
            unresolved_calls.add(call_id)
    return _FieldAnalysisResult(
        call_targets=result_targets,
        resolved_calls=frozenset(resolved_calls),
        unresolved_calls=frozenset(unresolved_calls),
        saturated_calls=frozenset(saturated_calls),
        points_to_variables=len(values),
        points_to_relations=sum(len(item) for item in values.values()),
        iterations=max(1, iterations + slice_visited_nodes),
        slice_resolved_calls=len(slice_resolved),
        slice_visited_nodes=slice_visited_nodes,
        slice_budget_calls=len(slice_budget_calls),
    )


def _merge_hybrid_results(
    base: PointerAnalysisResult,
    field: _FieldAnalysisResult,
    program: PointerProgram,
) -> PointerAnalysisResult:
    targets = {}
    for call_id in program.calls:
        base_targets = base.call_targets.get(call_id, frozenset())
        # The object-sensitive solver is more precise and is already
        # conservative whenever it declares a call complete.  The
        # type/layout-collapsed solver exists only to recover calls whose
        # exact object flow saturated or was unavailable; unioning its broad
        # targets into an already-complete call needlessly expands the kernel
        # dependency closure.
        if call_id not in base.unresolved_calls and base_targets:
            targets[call_id] = base_targets
        else:
            targets[call_id] = frozenset(
                set(base_targets)
                | set(field.call_targets.get(call_id, ()))
            )
    unresolved = frozenset(
        call_id
        for call_id in base.unresolved_calls
        if call_id in field.unresolved_calls
    )
    field_fallback_calls = len(
        base.unresolved_calls.intersection(field.resolved_calls)
    )
    effective_saturated = base.saturated_call_ids.difference(
        field.resolved_calls
    )
    return PointerAnalysisResult(
        points_to=base.points_to,
        call_targets=dict(sorted(targets.items())),
        unresolved_calls=unresolved,
        iterations=base.iterations + field.iterations,
        points_to_variables=base.points_to_variables,
        points_to_relations=base.points_to_relations,
        max_points_to_set=base.max_points_to_set,
        solver=(
            "roaring-worklist-v2-hybrid-bounded"
            if base.points_to_limit is not None
            else "roaring-worklist-v2-hybrid"
        ),
        saturated_variables=base.saturated_variables,
        saturated_calls=len(effective_saturated),
        points_to_limit=base.points_to_limit,
        saturation_reasons=base.saturation_reasons,
        saturation_samples=base.saturation_samples,
        global_memory_trigger=base.global_memory_trigger,
        saturated_memory_families=base.saturated_memory_families,
        saturated_call_ids=frozenset(effective_saturated),
        field_resolved_calls=len(field.resolved_calls),
        field_unresolved_calls=len(field.unresolved_calls),
        field_points_to_variables=field.points_to_variables,
        field_points_to_relations=field.points_to_relations,
        field_slice_resolved_calls=field.slice_resolved_calls,
        field_slice_visited_nodes=field.slice_visited_nodes,
        field_slice_budget_calls=field.slice_budget_calls,
        field_fallback_calls=field_fallback_calls,
    )


def _is_function(target: str) -> bool:
    return target.startswith("fn:")


@lru_cache(maxsize=None)
def _parse_abi_signature(
    signature: str,
) -> Optional[Tuple[str, str, Tuple[str, ...], bool]]:
    try:
        fields = dict(
            component.split("=", 1)
            for component in signature.split(";")
        )
        arguments = tuple(
            item for item in fields["args"].split(",") if item
        )
        return (
            fields["cc"],
            fields["ret"],
            arguments,
            fields["vararg"] == "1",
        )
    except (KeyError, ValueError):
        return None


def abi_signatures_compatible(
    call_signature: Optional[str],
    target_signature: Optional[str],
) -> bool:
    """Return whether a target is compatible with an indirect-call ABI.

    Missing or unrecognized metadata remains conservative and accepts the
    target.  The LLVM pass deliberately records broad ABI classes (for
    example, pointers and machine-word integers are both ``gpr``), so this
    rejects only targets that cannot share the call site's convention,
    return register class, arity, or fixed argument register classes.
    """

    if call_signature is None or target_signature is None:
        return True
    call = _parse_abi_signature(call_signature)
    target = _parse_abi_signature(target_signature)
    if call is None or target is None:
        return True
    call_cc, call_return, call_arguments, _ = call
    target_cc, target_return, target_arguments, target_vararg = target
    if call_cc != target_cc or call_return != target_return:
        return False
    if target_vararg:
        if len(call_arguments) < len(target_arguments):
            return False
    elif len(call_arguments) != len(target_arguments):
        return False
    return all(
        call_argument == target_argument
        for call_argument, target_argument in zip(
            call_arguments, target_arguments
        )
    )


def _is_memory_object(target: str) -> bool:
    return target.startswith("obj:") or target.startswith("objfield:")


def _memory_cell(target: str) -> str:
    return f"mem:{target}"


def _field_object(target: str, field_path: str) -> str:
    return f"objfield:{target}:{field_path}"


def _memory_field_family(target: str) -> Optional[str]:
    if target.startswith("objfield:"):
        return target.rsplit(":", 1)[-1]
    if target.startswith("obj:"):
        return "<root>"
    return None


def _json_object(value: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise GraphValidationError(
            f"pointer metadata is not JSON serializable: {error}"
        ) from error
    return decoded


def _object_list(raw: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise GraphValidationError(f"{key} must be an array of objects")
    return list(value)


def _required_string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise GraphValidationError(f"{key} must be a non-empty string")
    return value


def _optional_string(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise GraphValidationError("expected a non-empty string or null")
    return raw


def _optional_string_list(raw: Any) -> list[Optional[str]]:
    if not isinstance(raw, list) or not all(
        item is None or (isinstance(item, str) and item) for item in raw
    ):
        raise GraphValidationError(
            "expected an array containing strings or null values"
        )
    return list(raw)

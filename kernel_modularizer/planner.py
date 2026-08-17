"""Conservative, explainable function-level module boundary planner."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatchcase
import hashlib
from typing import Dict, Iterable, Iterator, Mapping, Optional, Sequence, Set, Tuple

from .errors import GraphValidationError
from .model import (
    EdgeKind,
    EntityKind,
    ExecutionContext,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNode,
)
from .pointer_analysis import abi_signatures_compatible


class Disposition(str, Enum):
    CORE = "core"
    INTERFACE = "interface"
    MODULE = "module"
    DEAD = "dead"
    UNKNOWN = "unknown"


DEFAULT_HARD_ATTRIBUTES = frozenset(
    {
        "__init",
        "__head",
        "__always_inline",
        "inline_hint",
        "early_boot",
        "entry",
        "interrupt",
        "linker_registered",
        "naked",
        "noinstr",
        "notrace",
        "notrace_critical",
        "used",
        "module_loader_dependency",
    }
)

DEFAULT_RESIDENT_ATTRIBUTES = frozenset(
    {
        "exported",
        "permanent_api",
        "syscall_entry",
        "trace_entry",
    }
)

DEFAULT_NON_LOADABLE_CONTEXTS = frozenset(
    {
        ExecutionContext.ATOMIC,
        ExecutionContext.IRQ,
        ExecutionContext.NMI,
        ExecutionContext.EARLY_BOOT,
    }
)

DEFAULT_DEPENDENCY_EDGES = frozenset(
    {
        EdgeKind.DIRECT_CALL,
        EdgeKind.INDIRECT_CALL,
        EdgeKind.ADDRESS_TAKEN,
        EdgeKind.CALLBACK_FIELD,
        EdgeKind.FUNCTION_ARGUMENT,
        EdgeKind.FUNCTION_RETURN,
        EdgeKind.GLOBAL_READ,
        EdgeKind.GLOBAL_WRITE,
        EdgeKind.GLOBAL_INITIALIZER,
        EdgeKind.ALIAS,
        EdgeKind.ASSEMBLY,
        EdgeKind.LINKER_SECTION,
        EdgeKind.UNRESOLVED_CALL,
    }
)

CALL_EDGES = frozenset(
    {
        EdgeKind.DIRECT_CALL,
        EdgeKind.INDIRECT_CALL,
        EdgeKind.CALLBACK_FIELD,
        EdgeKind.FUNCTION_ARGUMENT,
        EdgeKind.FUNCTION_RETURN,
        EdgeKind.ALIAS,
        EdgeKind.ASSEMBLY,
    }
)

_OBSERVED_GENERIC_REASON = "observed in boot trace"
_OBSERVED_PRE_LOADER_REASON = (
    "observed before module loader ready"
)
_OBSERVED_POST_LOADER_REASON = (
    "observed after module loader ready"
)
_OBSERVED_UNKNOWN_PHASE_REASON = (
    "observed without module-loader phase evidence"
)
_OBSERVATION_REASONS = frozenset(
    {
        _OBSERVED_GENERIC_REASON,
        _OBSERVED_PRE_LOADER_REASON,
        _OBSERVED_POST_LOADER_REASON,
        _OBSERVED_UNKNOWN_PHASE_REASON,
    }
)


@dataclass(frozen=True)
class PlannerPolicy:
    """Safety and optimization inputs for one kernel/configuration."""

    boot_roots: frozenset[str] = field(default_factory=frozenset)
    observed_boot_functions: frozenset[str] = field(default_factory=frozenset)
    resident_roots: frozenset[str] = field(default_factory=frozenset)
    deferred_roots: frozenset[str] = field(default_factory=frozenset)
    load_safe_roots: frozenset[str] = field(default_factory=frozenset)
    module_loader_roots: frozenset[str] = field(default_factory=frozenset)
    hard_attributes: frozenset[str] = DEFAULT_HARD_ATTRIBUTES
    resident_attributes: frozenset[str] = DEFAULT_RESIDENT_ATTRIBUTES
    non_loadable_contexts: frozenset[ExecutionContext] = (
        DEFAULT_NON_LOADABLE_CONTEXTS
    )
    dependency_edges: frozenset[EdgeKind] = DEFAULT_DEPENDENCY_EDGES
    allow_dead_code_removal: bool = False
    phase_aware_boot: bool = False
    trace_guarded_indirect_boot: bool = False
    trace_covers_initcalls: bool = False
    field_scoped_unresolved_targets: bool = False
    syscall_entries_load_safe: bool = False
    structural_syscall_entry_prefixes: Tuple[str, ...] = ()
    enforce_loader_ready_phase: bool = False
    loader_ready_observed: bool = False
    identify_preload_candidates: bool = False
    pre_loader_observed_functions: frozenset[str] = field(
        default_factory=frozenset
    )
    post_loader_observed_functions: frozenset[str] = field(
        default_factory=frozenset
    )
    unknown_phase_observed_functions: frozenset[str] = field(
        default_factory=frozenset
    )
    encoded_function_domains: Tuple[
        Tuple[str, Tuple[str, ...]], ...
    ] = ()
    pack_load_safe_candidates_by_source: bool = False
    aggregate_savings_groups: bool = False


@dataclass(frozen=True)
class FunctionDecision:
    node_id: str
    disposition: Disposition
    reasons: Tuple[str, ...]
    candidate_id: Optional[str] = None


@dataclass(frozen=True)
class ModuleCandidate:
    id: str
    functions: Tuple[str, ...]
    owned_globals: Tuple[str, ...]
    interfaces: Tuple[str, ...]
    incoming_edges: Tuple[ReferenceEdge, ...]
    outgoing_edges: Tuple[ReferenceEdge, ...]
    known_size_bytes: int
    unknown_size_functions: int


@dataclass(frozen=True)
class ModulePlan:
    decisions: Mapping[str, FunctionDecision]
    candidates: Tuple[ModuleCandidate, ...]
    hard_core_nodes: frozenset[str]
    unresolved_callers: frozenset[str]

    def decision_for(self, node_id: str) -> FunctionDecision:
        try:
            return self.decisions[node_id]
        except KeyError as error:
            raise GraphValidationError(
                f"no function decision exists for {node_id!r}"
            ) from error


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, first: str, second: str) -> None:
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root == second_root:
            return
        # A lexical tie-break keeps results reproducible.
        if first_root > second_root:
            first_root, second_root = second_root, first_root
        self.parent[second_root] = first_root

    def groups(self) -> Tuple[Tuple[str, ...], ...]:
        result: Dict[str, list[str]] = {}
        for item in sorted(self.parent):
            result.setdefault(self.find(item), []).append(item)
        return tuple(
            sorted(
                (tuple(members) for members in result.values()),
                key=lambda members: members,
            )
        )


def plan_modules(
    graph: ReferenceGraph,
    policy: Optional[PlannerPolicy] = None,
) -> ModulePlan:
    """Generate a conservative plan for every function in ``graph``."""

    graph.validate()
    selected_policy = policy or PlannerPolicy()
    _validate_policy_nodes(graph, selected_policy)

    function_ids = {
        node.id
        for node in graph.nodes.values()
        if node.kind is EntityKind.FUNCTION
    }
    hard_seeds, hard_seed_reasons = _hard_core_seeds(graph, selected_policy)
    if (
        selected_policy.phase_aware_boot
        and selected_policy.trace_covers_initcalls
    ):
        filtered_reasons = {
            node_id: {
                reason
                for reason in reasons
                if not _is_trace_covered_init_reason(reason)
            }
            for node_id, reasons in hard_seed_reasons.items()
        }
        hard_seed_reasons = {
            node_id: reasons
            for node_id, reasons in filtered_reasons.items()
            if reasons
        }
        hard_seeds = set(hard_seed_reasons)
    closure_seeds = set(hard_seeds)
    if selected_policy.phase_aware_boot:
        closure_seeds = {
            node_id
            for node_id in hard_seeds
            if not _can_defer_observed_dependencies(
                graph.nodes[node_id],
                hard_seed_reasons[node_id],
                selected_policy,
            )
        }
    if (
        selected_policy.phase_aware_boot
        and selected_policy.trace_guarded_indirect_boot
    ):
        observed_only_seeds = {
            node_id
            for node_id in closure_seeds
            if hard_seed_reasons[node_id]
            and hard_seed_reasons[node_id].issubset(
                _OBSERVATION_REASONS
            )
        }
        static_seeds = closure_seeds.difference(observed_only_seeds)
        hard_core = _forward_closure(
            graph,
            static_seeds,
            selected_policy.dependency_edges,
        )
        hard_core.update(
            _trace_guarded_forward_closure(
                graph,
                observed_only_seeds,
                selected_policy.dependency_edges,
                selected_policy.observed_boot_functions,
            )
        )
    else:
        hard_core = _forward_closure(
            graph,
            closure_seeds,
            selected_policy.dependency_edges,
        )
    # Phase-aware process roots remain resident themselves. Their unobserved
    # targets become lazy-boundary candidates and require separate context
    # proof before reporting can mark them READY.
    hard_core.update(hard_seeds)

    resident_reasons: Dict[str, Set[str]] = {}
    resident = set(selected_policy.resident_roots)
    for node in graph.nodes.values():
        matching = node.attributes.intersection(selected_policy.resident_attributes)
        if matching:
            resident.add(node.id)
            resident_reasons.setdefault(node.id, set()).update(
                f"resident attribute {attribute}" for attribute in sorted(matching)
            )
        if _has_structural_syscall_entry_prefix(
            node, selected_policy
        ):
            resident.add(node.id)
            resident_reasons.setdefault(node.id, set()).add(
                "structurally proven syscall dispatch entry"
            )
    for node_id in selected_policy.resident_roots:
        resident_reasons.setdefault(node_id, set()).add("explicit resident root")
    resident.difference_update(hard_core)
    core_nodes = hard_core.union(resident)

    unresolved_callers = {
        edge.source
        for edge in graph.edges
        if edge.kind is EdgeKind.UNRESOLVED_CALL
        and graph.nodes[edge.source].kind is EntityKind.FUNCTION
    }
    unknown_functions: Set[str] = set()
    unknown_reasons: Dict[str, Set[str]] = {}
    for function_id in unresolved_callers:
        if function_id not in core_nodes:
            unknown_functions.add(function_id)
            unknown_reasons.setdefault(function_id, set()).add(
                "contains unresolved indirect call"
            )
    for node_id in function_ids.difference(core_nodes):
        node = graph.nodes[node_id]
        if ExecutionContext.UNKNOWN in node.contexts:
            unknown_functions.add(node_id)
            unknown_reasons.setdefault(node_id, set()).add(
                "execution context is explicitly unknown"
            )
        if (
            "declaration" in node.attributes
            and "definition" not in node.attributes
        ):
            unknown_functions.add(node_id)
            unknown_reasons.setdefault(node_id, set()).add(
                "no extractable function definition is present"
            )
        if "definition" in node.attributes and not node.source_path:
            unknown_functions.add(node_id)
            unknown_reasons.setdefault(node_id, set()).add(
                "definition has no extractable source path"
            )

    if unresolved_callers:
        possible_target_edges = {
            EdgeKind.ADDRESS_TAKEN,
            EdgeKind.CALLBACK_FIELD,
            EdgeKind.FUNCTION_ARGUMENT,
            EdgeKind.FUNCTION_RETURN,
            EdgeKind.GLOBAL_INITIALIZER,
            EdgeKind.ALIAS,
        }
        target_edges = [
            edge
            for edge in graph.edges
            if edge.kind in possible_target_edges
            and graph.nodes[edge.target].kind is EntityKind.FUNCTION
        ]
        all_possible_targets = {
            edge.target for edge in target_edges
        }
        targets_by_scope: Dict[str, Set[str]] = defaultdict(set)
        targets_by_object: Dict[str, Set[str]] = defaultdict(set)
        for edge in target_edges:
            if edge.field_path is not None:
                targets_by_scope[edge.field_path].add(edge.target)
            if graph.nodes[edge.source].kind is EntityKind.GLOBAL:
                targets_by_object[f"obj:{edge.source}"].add(
                    edge.target
                )
        targets_by_encoded_base: Dict[str, Set[str]] = {}
        for encoded_base, global_patterns in (
            selected_policy.encoded_function_domains
        ):
            dispatch_globals = {
                node.id
                for node in graph.nodes.values()
                if (
                    node.kind is EntityKind.GLOBAL
                    and any(
                        fnmatchcase(node.symbol, pattern)
                        for pattern in global_patterns
                    )
                )
            }
            targets_by_encoded_base[encoded_base] = {
                edge.target
                for edge in target_edges
                if (
                    edge.kind is EdgeKind.GLOBAL_INITIALIZER
                    and edge.source in dispatch_globals
                )
            }
            # An encoded offset of zero aliases the anchor itself.  Keep it
            # in the conservative target domain even when the kernel's
            # verifier normally rejects that value.
            targets_by_encoded_base[encoded_base].add(encoded_base)
        possible_targets: Set[str] = set()
        for edge in graph.edges:
            if edge.kind is not EdgeKind.UNRESOLVED_CALL:
                continue
            unresolved_node = graph.nodes[edge.target]
            signatures = _node_abi_signatures(unresolved_node)
            fields = _node_callback_fields(unresolved_node)
            parameters = _node_callback_parameters(unresolved_node)
            objects = _node_callback_objects(unresolved_node)
            encoded_bases = _node_encoded_function_bases(
                unresolved_node
            )
            if encoded_bases and all(
                base in targets_by_encoded_base
                for base in encoded_bases
            ):
                domain = {
                    target
                    for base in encoded_bases
                    for target in targets_by_encoded_base[base]
                }
            elif (
                selected_policy.field_scoped_unresolved_targets
                and (fields or parameters or objects)
            ):
                domain = {
                    target
                    for scope in set(fields).union(parameters)
                    for target in targets_by_scope.get(scope, ())
                }
                domain.update(
                    target
                    for memory_object in objects
                    for target in targets_by_object.get(
                        memory_object, ()
                    )
                )
            else:
                domain = all_possible_targets
            possible_targets.update(
                node_id
                for node_id in domain
                if (
                    not signatures
                    or any(
                        abi_signatures_compatible(
                            call_signature, target_signature
                        )
                        for call_signature in signatures
                        for target_signature in (
                            _node_abi_signatures(
                                graph.nodes[node_id]
                            )
                            or (None,)
                        )
                    )
                )
            )
        uncertain_closure = _forward_closure(
            graph,
            possible_targets,
            selected_policy.dependency_edges,
        )
        for node_id in sorted(uncertain_closure.intersection(function_ids)):
            if node_id in core_nodes:
                continue
            unknown_functions.add(node_id)
            unknown_reasons.setdefault(node_id, set()).add(
                "potential target or dependency of an unresolved "
                "indirect call"
            )

    _mark_mutable_global_boundary_conflicts(
        graph,
        core_nodes=core_nodes,
        unknown_functions=unknown_functions,
        unknown_reasons=unknown_reasons,
    )
    _mark_unlinkable_module_dependencies(
        graph,
        core_nodes=core_nodes,
        unknown_functions=unknown_functions,
        unknown_reasons=unknown_reasons,
    )

    movable = function_ids.difference(core_nodes).difference(unknown_functions)
    units = _build_candidate_units(graph, movable)
    if selected_policy.pack_load_safe_candidates_by_source:
        units = _pack_load_safe_candidate_units_by_source(
            graph,
            units,
            core_nodes=core_nodes,
            policy=selected_policy,
        )
    owned_globals_by_unit = _owned_globals_by_candidate_unit(
        graph, units, movable
    )
    candidates: list[ModuleCandidate] = []
    decisions: Dict[str, FunctionDecision] = {}

    for function_id in sorted(function_ids.intersection(hard_core)):
        reasons = _core_reasons(
            graph,
            function_id,
            hard_seeds=hard_seeds,
            hard_seed_reasons=hard_seed_reasons,
        )
        decisions[function_id] = FunctionDecision(
            function_id,
            Disposition.CORE,
            tuple(sorted(reasons)),
        )
    for function_id in sorted(function_ids.intersection(resident)):
        decisions[function_id] = FunctionDecision(
            function_id,
            Disposition.CORE,
            tuple(sorted(resident_reasons.get(function_id, {"resident root"}))),
        )
    for function_id in sorted(unknown_functions):
        decisions[function_id] = FunctionDecision(
            function_id,
            Disposition.UNKNOWN,
            tuple(sorted(unknown_reasons[function_id])),
        )

    for unit in units:
        candidate = _make_candidate(
            graph,
            unit,
            core_nodes=core_nodes,
            owned_globals=owned_globals_by_unit.get(unit, ()),
        )
        candidates.append(candidate)
        is_dead = _unit_is_dead(
            graph,
            unit,
            candidate,
            selected_policy,
        )
        for function_id in unit:
            if is_dead:
                disposition = Disposition.DEAD
                reasons = ("closed-world unit has no incoming reference",)
            elif function_id in candidate.interfaces:
                disposition = Disposition.INTERFACE
                reasons = ("called from resident code across module boundary",)
            else:
                disposition = Disposition.MODULE
                reasons = ("belongs to movable dependency unit",)
            decisions[function_id] = FunctionDecision(
                function_id,
                disposition,
                reasons,
                candidate.id,
            )

    if set(decisions) != function_ids:
        missing = sorted(function_ids.difference(decisions))
        raise GraphValidationError(
            "planner failed to classify function(s): " + ", ".join(missing)
        )

    return ModulePlan(
        decisions=dict(sorted(decisions.items())),
        candidates=tuple(sorted(candidates, key=lambda item: item.id)),
        hard_core_nodes=frozenset(hard_core),
        unresolved_callers=frozenset(unresolved_callers),
    )


def _node_abi_signatures(
    node: ReferenceNode,
) -> Tuple[str, ...]:
    prefix = "abi_signature="
    return tuple(
        sorted(
            attribute[len(prefix):]
            for attribute in node.attributes
            if attribute.startswith(prefix)
        )
    )


def _node_callback_fields(node: ReferenceNode) -> Tuple[str, ...]:
    prefix = "callback_field="
    return tuple(
        sorted(
            attribute[len(prefix):]
            for attribute in node.attributes
            if attribute.startswith(prefix)
        )
    )


def _node_callback_parameters(node: ReferenceNode) -> frozenset[str]:
    prefix = "callback_parameter="
    return frozenset(
        attribute[len(prefix):]
        for attribute in node.attributes
        if attribute.startswith(prefix)
    )


def _node_callback_objects(node: ReferenceNode) -> frozenset[str]:
    prefix = "callback_object="
    return frozenset(
        attribute[len(prefix):]
        for attribute in node.attributes
        if attribute.startswith(prefix)
    )


def _node_encoded_function_bases(
    node: ReferenceNode,
) -> frozenset[str]:
    prefix = "encoded_function_base="
    return frozenset(
        attribute[len(prefix):]
        for attribute in node.attributes
        if attribute.startswith(prefix)
    )


def _validate_policy_nodes(graph: ReferenceGraph, policy: PlannerPolicy) -> None:
    configured = (
        policy.boot_roots
        | policy.observed_boot_functions
        | policy.resident_roots
        | policy.deferred_roots
        | policy.load_safe_roots
        | policy.module_loader_roots
        | policy.pre_loader_observed_functions
        | policy.post_loader_observed_functions
        | policy.unknown_phase_observed_functions
    )
    missing = sorted(configured.difference(graph.nodes))
    if missing:
        raise GraphValidationError(
            "planner policy references unknown node(s): " + ", ".join(missing)
        )
    if (
        policy.syscall_entries_load_safe
        and not policy.module_loader_roots
    ):
        raise GraphValidationError(
            "syscall_entries_load_safe requires explicit "
            "module_loader_roots"
        )
    if (
        policy.structural_syscall_entry_prefixes
        and not policy.syscall_entries_load_safe
    ):
        raise GraphValidationError(
            "structural_syscall_entry_prefixes requires "
            "syscall_entries_load_safe"
        )
    invalid_syscall_prefixes = sorted(
        prefix
        for prefix in policy.structural_syscall_entry_prefixes
        if (
            not prefix.startswith("__")
            or not prefix.endswith("_")
            or not all(
                character.isalnum() or character == "_"
                for character in prefix
            )
        )
    )
    if invalid_syscall_prefixes:
        raise GraphValidationError(
            "invalid structural syscall entry prefix(es): "
            + ", ".join(invalid_syscall_prefixes)
        )
    if (
        policy.enforce_loader_ready_phase
        and not policy.module_loader_roots
    ):
        raise GraphValidationError(
            "enforce_loader_ready_phase requires explicit "
            "module_loader_roots"
        )
    if (
        policy.enforce_loader_ready_phase
        and not policy.phase_aware_boot
    ):
        raise GraphValidationError(
            "enforce_loader_ready_phase requires phase_aware_boot"
        )
    if (
        policy.identify_preload_candidates
        and not policy.enforce_loader_ready_phase
    ):
        raise GraphValidationError(
            "identify_preload_candidates requires "
            "enforce_loader_ready_phase"
        )
    if (
        policy.enforce_loader_ready_phase
        and not policy.loader_ready_observed
    ):
        raise GraphValidationError(
            "enforce_loader_ready_phase requires observations from "
            "traces containing the module-loader-ready marker"
        )
    phase_observed = (
        policy.pre_loader_observed_functions
        | policy.post_loader_observed_functions
        | policy.unknown_phase_observed_functions
    )
    missing_observed_phase = sorted(
        phase_observed.difference(policy.observed_boot_functions)
    )
    if missing_observed_phase:
        raise GraphValidationError(
            "loader-phase evidence references functions that are not "
            "observed_boot_functions: "
            + ", ".join(missing_observed_phase)
        )
    if policy.enforce_loader_ready_phase:
        unphased_observed = sorted(
            policy.observed_boot_functions.difference(
                phase_observed
            )
        )
        if unphased_observed:
            raise GraphValidationError(
                "loader-phase enforcement requires phase evidence for "
                "every observed boot function: "
                + ", ".join(unphased_observed)
            )
        contradictory_unknown = sorted(
            policy.unknown_phase_observed_functions.intersection(
                policy.pre_loader_observed_functions
                | policy.post_loader_observed_functions
            )
        )
        if contradictory_unknown:
            raise GraphValidationError(
                "unknown module-loader phase evidence overlaps a known "
                "phase for: "
                + ", ".join(contradictory_unknown)
            )
    for encoded_base, global_patterns in (
        policy.encoded_function_domains
    ):
        node = graph.nodes.get(encoded_base)
        if node is None or node.kind is not EntityKind.FUNCTION:
            raise GraphValidationError(
                "encoded function domain references an unknown "
                f"function base: {encoded_base}"
            )
        if not global_patterns:
            raise GraphValidationError(
                f"encoded function domain {encoded_base} has no "
                "global patterns"
            )
        if not any(
            candidate.kind is EntityKind.GLOBAL
            and any(
                fnmatchcase(candidate.symbol, pattern)
                for pattern in global_patterns
            )
            for candidate in graph.nodes.values()
        ):
            raise GraphValidationError(
                f"encoded function domain {encoded_base} patterns "
                "match no global nodes"
            )


def _hard_core_seeds(
    graph: ReferenceGraph,
    policy: PlannerPolicy,
) -> Tuple[Set[str], Dict[str, Set[str]]]:
    seeds: Set[str] = set()
    reasons: Dict[str, Set[str]] = {}

    def add(node_id: str, reason: str) -> None:
        seeds.add(node_id)
        reasons.setdefault(node_id, set()).add(reason)

    for node_id in policy.boot_roots:
        add(node_id, "explicit boot root")
    for node_id in policy.observed_boot_functions:
        if not policy.enforce_loader_ready_phase:
            add(node_id, _OBSERVED_GENERIC_REASON)
        elif node_id in policy.pre_loader_observed_functions:
            add(node_id, _OBSERVED_PRE_LOADER_REASON)
        elif node_id in policy.unknown_phase_observed_functions:
            add(node_id, _OBSERVED_UNKNOWN_PHASE_REASON)
        elif node_id in policy.post_loader_observed_functions:
            add(node_id, _OBSERVED_POST_LOADER_REASON)
        else:
            add(node_id, _OBSERVED_GENERIC_REASON)
    for node_id in policy.module_loader_roots:
        add(node_id, "module loader bootstrap root")

    for node in graph.nodes.values():
        matching_attributes = node.attributes.intersection(policy.hard_attributes)
        for attribute in sorted(matching_attributes):
            add(node.id, f"hard attribute {attribute}")
        matching_contexts = node.contexts.intersection(policy.non_loadable_contexts)
        for context in sorted(matching_contexts, key=lambda item: item.value):
            add(node.id, f"non-loadable context {context.value}")
        if node.section is not None and _is_hard_section(node.section):
            add(node.id, f"non-loadable section {node.section}")
        is_resident = (
            node.id in policy.resident_roots
            or bool(node.attributes.intersection(policy.resident_attributes))
            or _has_structural_syscall_entry_prefix(node, policy)
        )
        if is_resident and not _is_demand_load_safe(node, policy):
            add(node.id, "resident entry lacks a proven process-context loader")

    # A linker registry may contain a pointer to an immutable callback table
    # instead of a direct function relocation (for example,
    # .x86_cpu_dev.init -> amd_cpu_dev -> init_amd).  Recover the function
    # roots through initializer edges even when a phase-aware policy
    # intentionally excludes general data-flow edges from its closure.
    registered_globals = sorted(
        node.id
        for node in graph.nodes.values()
        if node.kind is EntityKind.GLOBAL
        and "linker_registered" in node.attributes
        and "linker_registered" in policy.hard_attributes
    )
    for registry_id in registered_globals:
        pending = [registry_id]
        visited: Set[str] = set()
        while pending:
            owner_id = pending.pop()
            if owner_id in visited:
                continue
            visited.add(owner_id)
            for edge in graph.outgoing(
                owner_id,
                kinds={EdgeKind.GLOBAL_INITIALIZER},
            ):
                target = graph.nodes[edge.target]
                if target.kind is EntityKind.FUNCTION:
                    add(
                        target.id,
                        f"linker-registered callback via {registry_id}",
                    )
                elif target.kind is EntityKind.GLOBAL:
                    pending.append(target.id)
    return seeds, reasons


def _is_demand_load_safe(
    node: ReferenceNode, policy: PlannerPolicy
) -> bool:
    if node.kind is not EntityKind.FUNCTION:
        return False
    if policy.enforce_loader_ready_phase:
        if (
            not policy.loader_ready_observed
            or node.id in policy.pre_loader_observed_functions
            or node.id in policy.unknown_phase_observed_functions
        ):
            return False
        has_phase_or_structural_proof = (
            node.id in policy.post_loader_observed_functions
            or "load_safe" in node.attributes
            or node.id in policy.load_safe_roots
            or (
                policy.syscall_entries_load_safe
                and _is_syscall_entry(node, policy)
            )
        )
        if not has_phase_or_structural_proof:
            return False
    if "load_safe" in node.attributes or node.id in policy.load_safe_roots:
        return True
    if (
        policy.syscall_entries_load_safe
        and _is_syscall_entry(node, policy)
    ):
        return True
    return bool(node.contexts) and node.contexts.issubset(
        {ExecutionContext.PROCESS}
    )


def _is_syscall_entry(
    node: ReferenceNode,
    policy: PlannerPolicy,
) -> bool:
    """Recognize table-proven and explicitly configured ABI wrappers.

    Some architectures generate compatibility syscall tables outside the
    LLVM translation unit that defines the wrapper.  In that case the table
    relocation cannot contribute ``syscall_entry`` to the merged IR facts.
    A policy may name the architecture's reserved wrapper prefixes to recover
    that structural proof without treating internal ``__do_sys_*`` helpers as
    dispatch entries.
    """

    return (
        "syscall_entry" in node.attributes
        or _has_structural_syscall_entry_prefix(node, policy)
    )


def _has_structural_syscall_entry_prefix(
    node: ReferenceNode,
    policy: PlannerPolicy,
) -> bool:
    return (
        node.kind is EntityKind.FUNCTION
        and any(
            node.symbol.startswith(prefix)
            for prefix in policy.structural_syscall_entry_prefixes
        )
    )


def is_demand_load_safe(
    node: ReferenceNode, policy: PlannerPolicy
) -> bool:
    return _is_demand_load_safe(node, policy)


def _can_defer_observed_dependencies(
    node: ReferenceNode,
    reasons: Set[str],
    policy: PlannerPolicy,
) -> bool:
    non_observation_reasons = reasons.difference(
        _OBSERVATION_REASONS
    )
    if (
        _is_observed_process_only(node, policy)
        and not non_observation_reasons
    ):
        return True
    if not (
        policy.enforce_loader_ready_phase
        and policy.identify_preload_candidates
        and node.id in policy.post_loader_observed_functions
        and node.id not in policy.pre_loader_observed_functions
        and node.id not in policy.unknown_phase_observed_functions
    ):
        return False
    # A post-loader IRQ/NMI/atomic caller cannot invoke request_module(), but
    # its dependency can still be reported as PRELOAD if the only additional
    # hard-root reasons are its non-sleeping execution contexts. The report
    # keeps this class out of READY until preload and registration lifetime
    # have independent proof.
    return bool(non_observation_reasons) and all(
        reason.startswith("non-loadable context ")
        for reason in non_observation_reasons
    )


def _is_observed_process_only(
    node: ReferenceNode, policy: PlannerPolicy
) -> bool:
    prefix = "observed_context="
    contexts = {
        attribute[len(prefix):]
        for attribute in node.attributes
        if attribute.startswith(prefix)
    }
    if policy.enforce_loader_ready_phase:
        if (
            node.id not in policy.post_loader_observed_functions
            or node.id in policy.pre_loader_observed_functions
            or node.id in policy.unknown_phase_observed_functions
        ):
            return False
    return contexts == {ExecutionContext.PROCESS.value}


def _is_hard_section(section: str) -> bool:
    if ".initcall" in section:
        return True
    return section.startswith(
        (
            ".init",
            ".exit",
            ".con_initcall",
            ".head",
            ".entry",
            ".irqentry",
            ".softirqentry",
            ".noinstr",
            ".kprobes",
            ".cpuidle",
            ".spinlock",
            ".lock",
            ".hyp",
            ".idmap",
            ".hibernate",
        )
    )


def _is_trace_covered_init_reason(reason: str) -> bool:
    if reason == "hard attribute __init":
        return True
    prefix = "non-loadable section "
    if not reason.startswith(prefix):
        return False
    section = reason[len(prefix):]
    return (
        section.startswith(".init")
        or section.startswith(".con_initcall")
        or ".initcall" in section
    )


def _forward_closure(
    graph: ReferenceGraph,
    roots: Iterable[str],
    edge_kinds: frozenset[EdgeKind],
) -> Set[str]:
    reached = set(roots)
    work = list(sorted(reached, reverse=True))
    while work:
        current = work.pop()
        for edge in graph.outgoing(current, kinds=set(edge_kinds)):
            if edge.target not in reached:
                reached.add(edge.target)
                work.append(edge.target)
    return reached


def _trace_guarded_forward_closure(
    graph: ReferenceGraph,
    roots: Iterable[str],
    edge_kinds: frozenset[EdgeKind],
    observed_functions: frozenset[str],
) -> Set[str]:
    """Close trace-only roots without inventing unobserved boot callbacks.

    Function tracing records a callback target when it actually executes.
    For roots whose only hard evidence is that trace, an indirect edge to an
    unobserved target is a possible post-boundary dependency, not evidence
    that the target was needed during the measured startup interval.  Static
    hard roots use the ordinary closure and never pass through this filter.
    """

    reached = set(roots)
    work = list(sorted(reached, reverse=True))
    while work:
        current = work.pop()
        for edge in graph.outgoing(current, kinds=set(edge_kinds)):
            if (
                edge.kind is EdgeKind.INDIRECT_CALL
                and edge.target not in observed_functions
            ):
                continue
            if edge.target not in reached:
                reached.add(edge.target)
                work.append(edge.target)
    return reached


def _core_reasons(
    graph: ReferenceGraph,
    function_id: str,
    *,
    hard_seeds: Set[str],
    hard_seed_reasons: Mapping[str, Set[str]],
) -> Set[str]:
    if function_id in hard_seeds:
        return set(hard_seed_reasons[function_id])
    # A compact explanation is intentional; detailed paths belong in reports.
    incoming_core = [
        edge
        for edge in graph.incoming(function_id)
        if edge.source in hard_seeds
    ]
    if incoming_core:
        return {
            f"reachable from hard root via {edge.kind.value}"
            for edge in incoming_core
        }
    return {"reachable from boot-critical dependency closure"}


def _mutable_global_users(
    graph: ReferenceGraph,
    global_node: ReferenceNode,
) -> Set[str]:
    users: Set[str] = set()
    for edge in graph.incoming(global_node.id):
        if graph.nodes[edge.source].kind is EntityKind.FUNCTION:
            users.add(edge.source)
    for edge in graph.outgoing(global_node.id):
        if graph.nodes[edge.target].kind is EntityKind.FUNCTION:
            users.add(edge.target)
    return users


def _is_mutable_global(graph: ReferenceGraph, node: ReferenceNode) -> bool:
    if node.kind is not EntityKind.GLOBAL:
        return False
    if _is_non_source_owned_global(node):
        return False
    if "immutable" in node.attributes or "shared_api" in node.attributes:
        return False
    if "mutable" in node.attributes or "internal" in node.attributes:
        return True
    return any(
        edge.kind is EdgeKind.GLOBAL_WRITE
        for edge in graph.incoming(node.id)
    )


def _mark_mutable_global_boundary_conflicts(
    graph: ReferenceGraph,
    *,
    core_nodes: Set[str],
    unknown_functions: Set[str],
    unknown_reasons: Dict[str, Set[str]],
) -> None:
    for node in graph.nodes.values():
        if not _is_mutable_global(graph, node):
            continue
        users = _mutable_global_users(graph, node)
        core_users = users.intersection(core_nodes)
        movable_users = users.difference(core_nodes)
        if core_users and movable_users:
            for function_id in movable_users:
                unknown_functions.add(function_id)
                unknown_reasons.setdefault(function_id, set()).add(
                    f"shares mutable global {node.symbol} with resident code"
                )


def _is_module_visible(node: ReferenceNode) -> bool:
    # Inline definitions are compile-time dependencies, not runtime symbols
    # that MODPOST must resolve from vmlinux. The generated module gets the
    # body through the original includes/source extraction; the backend
    # remains responsible for rejecting a body it cannot reproduce.
    return bool(
        node.attributes.intersection(
            {
                "exported",
                "shared_api",
                "__always_inline",
                "inline_hint",
            }
        )
    )


def _is_non_source_owned_global(node: ReferenceNode) -> bool:
    """Return metadata/literals recreated by compilation, not extraction."""

    if node.kind is not EntityKind.GLOBAL:
        return False
    if node.attributes.intersection(
        {
            "compiler_generated",
            "compiler_metadata",
            "retention_metadata",
        }
    ):
        return True
    # Keep compatibility with facts produced by older pass versions.
    if node.symbol in {"llvm.used", "llvm.compiler.used"}:
        return True
    if node.symbol == ".str" or node.symbol.startswith(".str."):
        return True
    return node.section in {"llvm.metadata", ".discard.addressable"}


def _mark_unlinkable_module_dependencies(
    graph: ReferenceGraph,
    *,
    core_nodes: Set[str],
    unknown_functions: Set[str],
    unknown_reasons: Dict[str, Set[str]],
) -> None:
    """Reject candidates that could not pass the kernel modpost link check."""

    function_ids = {
        node.id
        for node in graph.nodes.values()
        if node.kind is EntityKind.FUNCTION
    }
    while True:
        movable = function_ids.difference(core_nodes).difference(
            unknown_functions
        )
        newly_unknown: Dict[str, Set[str]] = {}
        for source in sorted(movable):
            for edge in graph.outgoing(
                source, kinds=set(DEFAULT_DEPENDENCY_EDGES)
            ):
                target = graph.nodes[edge.target]
                if target.kind is EntityKind.FUNCTION:
                    if target.id in movable or _is_module_visible(target):
                        continue
                    newly_unknown.setdefault(source, set()).add(
                        f"depends on non-exported resident function "
                        f"{target.symbol}"
                    )
                elif target.kind is EntityKind.GLOBAL:
                    if _is_module_visible(target):
                        continue
                    users = _mutable_global_users(graph, target)
                    if users and users.issubset(movable):
                        continue
                    newly_unknown.setdefault(source, set()).add(
                        f"depends on non-exported resident global "
                        f"{target.symbol}"
                    )
        if not newly_unknown:
            return
        for function_id, reasons in newly_unknown.items():
            unknown_functions.add(function_id)
            unknown_reasons.setdefault(function_id, set()).update(reasons)


def _build_candidate_units(
    graph: ReferenceGraph,
    movable: Set[str],
) -> Tuple[Tuple[str, ...], ...]:
    union_find = _UnionFind(movable)

    # A generated module must be link-closed. Keep every movable call/callback
    # dependency in one unit; otherwise one generated module would reference a
    # private symbol extracted into another generated module.
    adjacency = {
        function_id: {
            edge.target
            for edge in graph.outgoing(function_id, kinds=set(CALL_EDGES))
            if edge.target in movable
        }
        for function_id in movable
    }
    for source, targets in adjacency.items():
        for target in targets:
            union_find.union(source, target)

    # Preserve the explicit cycle invariant if link-closed grouping becomes a
    # configurable optimization in a later schema.
    for component in _strongly_connected_components(adjacency):
        for other in component[1:]:
            union_find.union(component[0], other)

    # Private state, mutable or immutable, is owned by one module unit.
    for node in graph.nodes.values():
        if node.kind is not EntityKind.GLOBAL or _is_module_visible(node):
            continue
        users = sorted(_mutable_global_users(graph, node).intersection(movable))
        for other in users[1:]:
            union_find.union(users[0], other)

    return union_find.groups()


def _pack_load_safe_candidate_units_by_source(
    graph: ReferenceGraph,
    units: Tuple[Tuple[str, ...], ...],
    *,
    core_nodes: Set[str],
    policy: PlannerPolicy,
) -> Tuple[Tuple[str, ...], ...]:
    """Co-locate independent, proven-load-safe units from one source file.

    Link closure intentionally creates the smallest safe units.  A generated
    module may contain several independent entry points, however, and their
    aggregate resident saving can justify one module even when no individual
    entry crosses the size gate.  Only units whose every resident interface
    has an executable, load-safe incoming edge are eligible for this optional
    packaging optimization.
    """

    units_by_source: Dict[str, list[Tuple[str, ...]]] = defaultdict(list)
    unpacked: list[Tuple[str, ...]] = []
    execution_kinds = {
        EdgeKind.DIRECT_CALL,
        EdgeKind.INDIRECT_CALL,
        EdgeKind.ASSEMBLY,
    }
    for unit in units:
        source_paths = {
            graph.nodes[function_id].source_path
            for function_id in unit
        }
        candidate = _make_candidate(
            graph,
            unit,
            core_nodes=core_nodes,
        )
        if (
            len(source_paths) != 1
            or None in source_paths
            or candidate.unknown_size_functions
            or not candidate.interfaces
            or any(
                "__init" in graph.nodes[function_id].attributes
                or (
                    graph.nodes[function_id].section is not None
                    and graph.nodes[function_id].section.startswith(
                        ".init"
                    )
                )
                for function_id in unit
            )
        ):
            unpacked.append(unit)
            continue
        safe_interfaces = True
        for interface in candidate.interfaces:
            execution_edges = [
                edge
                for edge in candidate.incoming_edges
                if (
                    edge.target == interface
                    and edge.kind in execution_kinds
                    and graph.nodes[edge.source].kind
                    is EntityKind.FUNCTION
                )
            ]
            if (
                not execution_edges
                or any(
                    not _is_demand_load_safe(
                        graph.nodes[edge.source],
                        policy,
                    )
                    for edge in execution_edges
                )
            ):
                safe_interfaces = False
                break
        if not safe_interfaces:
            unpacked.append(unit)
            continue
        source_path = next(iter(source_paths))
        assert source_path is not None
        units_by_source[source_path].append(unit)

    packed = list(unpacked)
    for source_path in sorted(units_by_source):
        source_units = units_by_source[source_path]
        packed.append(
            tuple(
                sorted(
                    function_id
                    for unit in source_units
                    for function_id in unit
                )
            )
        )
    return tuple(sorted(packed))


def _strongly_connected_components(
    adjacency: Mapping[str, Set[str]],
) -> Tuple[Tuple[str, ...], ...]:
    index = 0
    indices: Dict[str, int] = {}
    lowlinks: Dict[str, int] = {}
    stack: list[str] = []
    on_stack: Set[str] = set()
    result: list[Tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in sorted(adjacency.get(node, set())):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])

        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            result.append(tuple(sorted(component)))

    for node in sorted(adjacency):
        if node not in indices:
            visit(node)
    return tuple(sorted(result))


def _make_candidate(
    graph: ReferenceGraph,
    functions: Tuple[str, ...],
    *,
    core_nodes: Set[str],
    owned_globals: Iterable[str] = (),
) -> ModuleCandidate:
    function_set = set(functions)
    selected_globals = tuple(sorted(owned_globals))
    member_nodes = function_set.union(selected_globals)

    incoming = tuple(
        sorted(
            {
                edge
                for member in member_nodes
                for edge in graph.incoming(member)
                if edge.target in member_nodes and edge.source not in member_nodes
            },
            key=lambda item: item.key,
        )
    )
    outgoing = tuple(
        sorted(
            {
                edge
                for member in member_nodes
                for edge in graph.outgoing(member)
                if edge.source in member_nodes and edge.target not in member_nodes
            },
            key=lambda item: item.key,
        )
    )
    interfaces = tuple(
        sorted(
            {
                edge.target
                for edge in incoming
                if edge.target in function_set and edge.source in core_nodes
            }
        )
    )
    digest_input = "\n".join(functions).encode("utf-8")
    candidate_id = "candidate:" + hashlib.sha256(digest_input).hexdigest()[:16]
    sizes = [graph.nodes[item].size_bytes for item in functions]
    return ModuleCandidate(
        id=candidate_id,
        functions=functions,
        owned_globals=selected_globals,
        interfaces=interfaces,
        incoming_edges=incoming,
        outgoing_edges=outgoing,
        known_size_bytes=sum(size for size in sizes if size is not None),
        unknown_size_functions=sum(size is None for size in sizes),
    )


def _owned_globals_by_candidate_unit(
    graph: ReferenceGraph,
    units: Tuple[Tuple[str, ...], ...],
    movable: Set[str],
) -> Dict[Tuple[str, ...], Tuple[str, ...]]:
    unit_by_function = {
        function_id: unit
        for unit in units
        for function_id in unit
    }
    owned: Dict[Tuple[str, ...], list[str]] = {}
    for node in graph.nodes.values():
        if (
            node.kind is not EntityKind.GLOBAL
            or _is_module_visible(node)
            or _is_non_source_owned_global(node)
        ):
            continue
        users = _mutable_global_users(graph, node).intersection(movable)
        if not users:
            continue
        user_units = {
            unit_by_function[function_id]
            for function_id in users
        }
        if len(user_units) != 1:
            continue
        unit = next(iter(user_units))
        if users.issubset(unit):
            owned.setdefault(unit, []).append(node.id)
    return {
        unit: tuple(sorted(global_ids))
        for unit, global_ids in owned.items()
    }


def _unit_is_dead(
    graph: ReferenceGraph,
    unit: Tuple[str, ...],
    candidate: ModuleCandidate,
    policy: PlannerPolicy,
) -> bool:
    if not policy.allow_dead_code_removal:
        return False
    if set(unit).intersection(policy.deferred_roots):
        return False
    if candidate.incoming_edges:
        return False
    for function_id in unit:
        node = graph.nodes[function_id]
        if node.attributes.intersection({"address_taken", "externally_discoverable"}):
            return False
    return True

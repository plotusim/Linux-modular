"""Load and resolve versioned planner policy files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .errors import GraphValidationError, SchemaVersionError
from .model import EdgeKind, ExecutionContext, ReferenceGraph
from .planner import PlannerPolicy


POLICY_SCHEMA_VERSION = 1


def load_planner_policy(
    path: str | Path,
    graph: ReferenceGraph,
) -> PlannerPolicy:
    policy_path = Path(path)
    try:
        content = policy_path.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphValidationError(
            f"cannot read planner policy {policy_path}: {error}"
        ) from error
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as error:
        raise GraphValidationError(
            f"invalid planner policy JSON: {error}"
        ) from error
    if not isinstance(raw, Mapping):
        raise GraphValidationError("planner policy root must be an object")
    return planner_policy_from_dict(raw, graph)


def planner_policy_from_dict(
    raw: Mapping[str, Any],
    graph: ReferenceGraph,
) -> PlannerPolicy:
    version = raw.get("schema_version")
    if version != POLICY_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"unsupported policy schema_version={version!r}; "
            f"expected {POLICY_SCHEMA_VERSION}"
        )
    defaults = PlannerPolicy()
    boot_roots = _resolve_selectors(raw.get("boot_roots", []), graph)
    observed = _resolve_selectors(
        raw.get("observed_boot_functions", []),
        graph,
    )
    resident = _resolve_selectors(raw.get("resident_roots", []), graph)
    deferred = _resolve_selectors(raw.get("deferred_roots", []), graph)
    load_safe = _resolve_selectors(
        raw.get("load_safe_roots", []), graph
    )
    module_loader_roots = _resolve_selectors(
        raw.get("module_loader_roots", []), graph
    )
    pre_loader_observed = _resolve_selectors(
        raw.get("pre_loader_observed_functions", []), graph
    )
    post_loader_observed = _resolve_selectors(
        raw.get("post_loader_observed_functions", []), graph
    )
    unknown_phase_observed = _resolve_selectors(
        raw.get("unknown_phase_observed_functions", []), graph
    )
    hard_attributes = frozenset(
        _string_list(raw.get("hard_attributes", sorted(defaults.hard_attributes)))
    )
    resident_attributes = frozenset(
        _string_list(
            raw.get(
                "resident_attributes",
                sorted(defaults.resident_attributes),
            )
        )
    )
    try:
        contexts = frozenset(
            ExecutionContext(value)
            for value in _string_list(
                raw.get(
                    "non_loadable_contexts",
                    sorted(
                        context.value
                        for context in defaults.non_loadable_contexts
                    ),
                )
            )
        )
        dependency_edges = frozenset(
            EdgeKind(value)
            for value in _string_list(
                raw.get(
                    "dependency_edges",
                    sorted(edge.value for edge in defaults.dependency_edges),
                )
            )
        )
    except ValueError as error:
        raise GraphValidationError(
            f"planner policy contains an unknown enum value: {error}"
        ) from error
    allow_dead = raw.get("allow_dead_code_removal", False)
    if not isinstance(allow_dead, bool):
        raise GraphValidationError(
            "allow_dead_code_removal must be true or false"
        )
    phase_aware_boot = raw.get("phase_aware_boot", False)
    if not isinstance(phase_aware_boot, bool):
        raise GraphValidationError(
            "phase_aware_boot must be true or false"
        )
    trace_guarded_indirect_boot = raw.get(
        "trace_guarded_indirect_boot", False
    )
    if not isinstance(trace_guarded_indirect_boot, bool):
        raise GraphValidationError(
            "trace_guarded_indirect_boot must be true or false"
        )
    trace_covers_initcalls = raw.get(
        "trace_covers_initcalls", False
    )
    if not isinstance(trace_covers_initcalls, bool):
        raise GraphValidationError(
            "trace_covers_initcalls must be true or false"
        )
    field_scoped_unresolved_targets = raw.get(
        "field_scoped_unresolved_targets", False
    )
    if not isinstance(field_scoped_unresolved_targets, bool):
        raise GraphValidationError(
            "field_scoped_unresolved_targets must be true or false"
        )
    syscall_entries_load_safe = raw.get(
        "syscall_entries_load_safe", False
    )
    if not isinstance(syscall_entries_load_safe, bool):
        raise GraphValidationError(
            "syscall_entries_load_safe must be true or false"
        )
    structural_syscall_entry_prefixes = tuple(
        _string_list(
            raw.get("structural_syscall_entry_prefixes", [])
        )
    )
    enforce_loader_ready_phase = raw.get(
        "enforce_loader_ready_phase", False
    )
    if not isinstance(enforce_loader_ready_phase, bool):
        raise GraphValidationError(
            "enforce_loader_ready_phase must be true or false"
        )
    loader_ready_observed = raw.get(
        "loader_ready_observed", False
    )
    if not isinstance(loader_ready_observed, bool):
        raise GraphValidationError(
            "loader_ready_observed must be true or false"
        )
    identify_preload_candidates = raw.get(
        "identify_preload_candidates", False
    )
    if not isinstance(identify_preload_candidates, bool):
        raise GraphValidationError(
            "identify_preload_candidates must be true or false"
        )
    encoded_function_domains = _encoded_function_domains(
        raw.get("encoded_function_domains", {}),
        graph,
    )
    pack_load_safe_candidates_by_source = raw.get(
        "pack_load_safe_candidates_by_source",
        False,
    )
    if not isinstance(pack_load_safe_candidates_by_source, bool):
        raise GraphValidationError(
            "pack_load_safe_candidates_by_source must be true or false"
        )
    aggregate_savings_groups = raw.get(
        "aggregate_savings_groups",
        False,
    )
    if not isinstance(aggregate_savings_groups, bool):
        raise GraphValidationError(
            "aggregate_savings_groups must be true or false"
        )
    return PlannerPolicy(
        boot_roots=boot_roots,
        observed_boot_functions=observed,
        resident_roots=resident,
        deferred_roots=deferred,
        load_safe_roots=load_safe,
        module_loader_roots=module_loader_roots,
        hard_attributes=hard_attributes,
        resident_attributes=resident_attributes,
        non_loadable_contexts=contexts,
        dependency_edges=dependency_edges,
        allow_dead_code_removal=allow_dead,
        phase_aware_boot=phase_aware_boot,
        trace_guarded_indirect_boot=trace_guarded_indirect_boot,
        trace_covers_initcalls=trace_covers_initcalls,
        field_scoped_unresolved_targets=(
            field_scoped_unresolved_targets
        ),
        syscall_entries_load_safe=syscall_entries_load_safe,
        structural_syscall_entry_prefixes=(
            structural_syscall_entry_prefixes
        ),
        enforce_loader_ready_phase=enforce_loader_ready_phase,
        loader_ready_observed=loader_ready_observed,
        identify_preload_candidates=identify_preload_candidates,
        pre_loader_observed_functions=pre_loader_observed,
        post_loader_observed_functions=post_loader_observed,
        unknown_phase_observed_functions=unknown_phase_observed,
        encoded_function_domains=encoded_function_domains,
        pack_load_safe_candidates_by_source=(
            pack_load_safe_candidates_by_source
        ),
        aggregate_savings_groups=aggregate_savings_groups,
    )


def planner_policy_to_dict(policy: PlannerPolicy) -> dict[str, Any]:
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "boot_roots": sorted(policy.boot_roots),
        "observed_boot_functions": sorted(policy.observed_boot_functions),
        "resident_roots": sorted(policy.resident_roots),
        "deferred_roots": sorted(policy.deferred_roots),
        "load_safe_roots": sorted(policy.load_safe_roots),
        "module_loader_roots": sorted(policy.module_loader_roots),
        "hard_attributes": sorted(policy.hard_attributes),
        "resident_attributes": sorted(policy.resident_attributes),
        "non_loadable_contexts": sorted(
            context.value for context in policy.non_loadable_contexts
        ),
        "dependency_edges": sorted(
            edge.value for edge in policy.dependency_edges
        ),
        "allow_dead_code_removal": policy.allow_dead_code_removal,
        "phase_aware_boot": policy.phase_aware_boot,
        "trace_guarded_indirect_boot": (
            policy.trace_guarded_indirect_boot
        ),
        "trace_covers_initcalls": policy.trace_covers_initcalls,
        "field_scoped_unresolved_targets": (
            policy.field_scoped_unresolved_targets
        ),
        "syscall_entries_load_safe": (
            policy.syscall_entries_load_safe
        ),
        "structural_syscall_entry_prefixes": list(
            policy.structural_syscall_entry_prefixes
        ),
        "enforce_loader_ready_phase": (
            policy.enforce_loader_ready_phase
        ),
        "loader_ready_observed": policy.loader_ready_observed,
        "identify_preload_candidates": (
            policy.identify_preload_candidates
        ),
        "pre_loader_observed_functions": sorted(
            policy.pre_loader_observed_functions
        ),
        "post_loader_observed_functions": sorted(
            policy.post_loader_observed_functions
        ),
        "unknown_phase_observed_functions": sorted(
            policy.unknown_phase_observed_functions
        ),
        "encoded_function_domains": {
            base: list(patterns)
            for base, patterns in policy.encoded_function_domains
        },
        "pack_load_safe_candidates_by_source": (
            policy.pack_load_safe_candidates_by_source
        ),
        "aggregate_savings_groups": (
            policy.aggregate_savings_groups
        ),
    }


def _resolve_selectors(
    raw: Any,
    graph: ReferenceGraph,
) -> frozenset[str]:
    selectors = _string_list(raw)
    resolved = set()
    for selector in selectors:
        if selector in graph.nodes:
            resolved.add(selector)
            continue
        matches = sorted(
            node.id
            for node in graph.nodes.values()
            if node.symbol == selector
        )
        if not matches:
            raise GraphValidationError(
                f"policy selector {selector!r} matches no graph node"
            )
        if len(matches) > 1:
            raise GraphValidationError(
                f"policy selector {selector!r} is ambiguous; use one of: "
                + ", ".join(matches)
            )
        resolved.add(matches[0])
    return frozenset(resolved)


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and item for item in raw
    ):
        raise GraphValidationError("expected an array of non-empty strings")
    return list(raw)


def _encoded_function_domains(
    raw: Any,
    graph: ReferenceGraph,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(raw, Mapping):
        raise GraphValidationError(
            "encoded_function_domains must be an object"
        )
    patterns_by_base: dict[str, set[str]] = {}
    for selector, raw_patterns in raw.items():
        if not isinstance(selector, str) or not selector:
            raise GraphValidationError(
                "encoded_function_domains keys must be non-empty strings"
            )
        patterns = tuple(sorted(set(_string_list(raw_patterns))))
        resolved = _resolve_selectors([selector], graph)
        base = next(iter(resolved))
        patterns_by_base.setdefault(base, set()).update(patterns)
    return tuple(
        (base, tuple(sorted(patterns)))
        for base, patterns in sorted(patterns_by_base.items())
    )

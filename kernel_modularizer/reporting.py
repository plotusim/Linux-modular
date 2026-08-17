"""Deterministic JSON and Markdown reports for module plans."""

from __future__ import annotations

from collections import Counter
import hashlib
from typing import Any, Dict, Iterable, Mapping

from .errors import GraphValidationError
from .model import EdgeKind, EntityKind, ReferenceEdge, ReferenceGraph
from .planner import (
    Disposition,
    ModuleCandidate,
    ModulePlan,
    PlannerPolicy,
    is_demand_load_safe,
)
from .policy import planner_policy_to_dict


REPORT_SCHEMA_VERSION = 1
# Linux 5.10 object measurements bound a single generated boundary at 608
# bytes. The shared noinline loader and one operations-table export reduce a
# seven-interface signal.c boundary from 1,703 to 995 bytes. 640 + 96 *
# (n - 1) estimates 1,216 bytes at n=7, retaining 221 bytes of measured
# margin; a three-interface stat.c boundary also stays below its 832-byte
# estimate.
DEFAULT_INTERFACE_STUB_BYTES = 640
DEFAULT_ADDITIONAL_INTERFACE_STUB_BYTES = 96
_BELOW_MINIMUM_REASON = (
    "estimated resident savings are below the configured minimum"
)
LOADER_PHASE_CLASSIFICATIONS = (
    "EARLY_CORE",
    "PRELOAD",
    "LAZY_READY",
    "UNKNOWN_PHASE",
)


def module_plan_to_dict(
    graph: ReferenceGraph,
    policy: PlannerPolicy,
    plan: ModulePlan,
    *,
    interface_stub_bytes: int = DEFAULT_INTERFACE_STUB_BYTES,
    additional_interface_stub_bytes: int = (
        DEFAULT_ADDITIONAL_INTERFACE_STUB_BYTES
    ),
    minimum_estimated_savings_bytes: int = 4096,
) -> Dict[str, Any]:
    if (
        interface_stub_bytes < 0
        or additional_interface_stub_bytes < 0
        or minimum_estimated_savings_bytes < 0
    ):
        raise GraphValidationError("size estimates must be non-negative")
    counts = Counter(
        decision.disposition.value for decision in plan.decisions.values()
    )
    candidates = [
        _candidate_to_dict(
            graph,
            candidate,
            policy=policy,
            interface_stub_bytes=interface_stub_bytes,
            additional_interface_stub_bytes=(
                additional_interface_stub_bytes
            ),
            minimum_estimated_savings_bytes=(
                minimum_estimated_savings_bytes
            ),
        )
        for candidate in plan.candidates
    ]
    aggregate_savings_groups = _apply_aggregate_savings_groups(
        candidates,
        enabled=policy.aggregate_savings_groups,
        minimum_estimated_savings_bytes=(
            minimum_estimated_savings_bytes
        ),
    )
    candidate_viability = {
        "positive_net": sum(
            candidate["estimated_net_bytes"] > 0
            for candidate in candidates
        ),
        "with_resident_interface": sum(
            bool(candidate["interfaces"]) for candidate in candidates
        ),
        "with_executable_interface": sum(
            candidate["executable_interface_edges"] > 0
            for candidate in candidates
        ),
        "with_proven_load_safe_interface": sum(
            candidate["load_safe_interface_edges"] > 0
            for candidate in candidates
        ),
        "meeting_minimum_savings": sum(
            not candidate["unknown_size_functions"]
            and candidate["estimated_net_bytes"]
            >= minimum_estimated_savings_bytes
            for candidate in candidates
        ),
        "meeting_savings_with_proven_load_safe_interface": sum(
            not candidate["unknown_size_functions"]
            and candidate["estimated_net_bytes"]
            >= minimum_estimated_savings_bytes
            and candidate["load_safe_interface_edges"] > 0
            for candidate in candidates
        ),
        "ready": sum(
            candidate["readiness"] == "READY"
            for candidate in candidates
        ),
        "ready_via_aggregate_savings": sum(
            candidate.get("readiness_basis")
            == "aggregate_savings_group"
            for candidate in candidates
        ),
    }
    loader_classifications = Counter(
        candidate["loader_phase_classification"]
        for candidate in candidates
    )
    if policy.enforce_loader_ready_phase:
        loader_classification_counts = {
            classification: loader_classifications.get(
                classification, 0
            )
            for classification in LOADER_PHASE_CLASSIFICATIONS
        }
    else:
        loader_classification_counts = {
            "NOT_ENFORCED": loader_classifications.get(
                "NOT_ENFORCED", 0
            )
        }
    pre_loader_only = (
        policy.pre_loader_observed_functions
        - policy.post_loader_observed_functions
        - policy.unknown_phase_observed_functions
    )
    post_loader_only = (
        policy.post_loader_observed_functions
        - policy.pre_loader_observed_functions
        - policy.unknown_phase_observed_functions
    )
    pre_and_post_loader = (
        policy.pre_loader_observed_functions
        & policy.post_loader_observed_functions
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "summary": {
            "functions": len(plan.decisions),
            "candidates": len(plan.candidates),
            "known_candidate_bytes": sum(
                candidate.known_size_bytes for candidate in plan.candidates
            ),
            "estimated_net_bytes": sum(
                candidate["estimated_net_bytes"]
                for candidate in candidates
            ),
            "unresolved_callers": len(plan.unresolved_callers),
            "interface_stub_bytes": interface_stub_bytes,
            "additional_interface_stub_bytes": (
                additional_interface_stub_bytes
            ),
            "minimum_estimated_savings_bytes": (
                minimum_estimated_savings_bytes
            ),
            "aggregate_savings": {
                "enabled": policy.aggregate_savings_groups,
                "groups": aggregate_savings_groups,
            },
            "candidate_readiness": dict(
                sorted(
                    Counter(
                        candidate["readiness"]
                        for candidate in candidates
                    ).items()
                )
            ),
            "candidate_viability": candidate_viability,
            "loader_phase": {
                "enforced": policy.enforce_loader_ready_phase,
                "loader_ready_observed": (
                    policy.loader_ready_observed
                ),
                "pre_loader_observed_functions": len(
                    policy.pre_loader_observed_functions
                ),
                "post_loader_observed_functions": len(
                    policy.post_loader_observed_functions
                ),
                "pre_loader_only_observed_functions": len(
                    pre_loader_only
                ),
                "post_loader_only_observed_functions": len(
                    post_loader_only
                ),
                "pre_and_post_loader_observed_functions": len(
                    pre_and_post_loader
                ),
                "unknown_phase_observed_functions": len(
                    policy.unknown_phase_observed_functions
                ),
                "candidate_classifications": (
                    loader_classification_counts
                ),
            },
            "dispositions": dict(sorted(counts.items())),
        },
        "graph_metadata": graph.metadata,
        "policy": planner_policy_to_dict(policy),
        "decisions": [
            {
                "id": decision.node_id,
                "symbol": graph.nodes[decision.node_id].symbol,
                "source_path": graph.nodes[decision.node_id].source_path,
                "disposition": decision.disposition.value,
                "candidate_id": decision.candidate_id,
                "reasons": list(decision.reasons),
            }
            for decision in plan.decisions.values()
        ],
        "candidates": candidates,
    }


def render_markdown_report(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    dispositions = summary["dispositions"]
    viability = summary["candidate_viability"]
    loader_phase = summary["loader_phase"]
    aggregate_savings = summary["aggregate_savings"]
    lines = [
        "# Linux Function Modularization Plan",
        "",
        "## Summary",
        "",
        f"- Functions classified: {summary['functions']}",
        f"- Module candidates: {summary['candidates']}",
        f"- Known movable bytes: {summary['known_candidate_bytes']}",
        f"- Estimated net bytes after resident stubs: "
        f"{summary['estimated_net_bytes']}",
        f"- Resident stub estimate: "
        f"{summary['interface_stub_bytes']} bytes for the first interface, "
        f"{summary['additional_interface_stub_bytes']} bytes for each "
        "additional interface",
        f"- Candidates with an executable lazy interface: "
        f"{viability['with_executable_interface']}",
        f"- Candidates meeting savings and proven load-safety gates: "
        f"{viability['meeting_savings_with_proven_load_safe_interface']}",
        f"- READY via aggregate savings groups: "
        f"{viability['ready_via_aggregate_savings']}",
        f"- Aggregate savings groups: "
        f"{len(aggregate_savings['groups'])}",
        f"- Unresolved indirect-call owners: {summary['unresolved_callers']}",
        f"- Loader-ready phase enforced: {loader_phase['enforced']}",
        f"- Loader-ready marker observed: "
        f"{loader_phase['loader_ready_observed']}",
        f"- Pre-loader observed functions: "
        f"{loader_phase['pre_loader_observed_functions']}",
        f"- Post-loader observed functions: "
        f"{loader_phase['post_loader_observed_functions']}",
        f"- Pre-loader only observed functions: "
        f"{loader_phase['pre_loader_only_observed_functions']}",
        f"- Post-loader only observed functions: "
        f"{loader_phase['post_loader_only_observed_functions']}",
        f"- Observed in both loader phases: "
        f"{loader_phase['pre_and_post_loader_observed_functions']}",
        f"- Unknown-phase observed functions: "
        f"{loader_phase['unknown_phase_observed_functions']}",
        *(
            f"- {classification} candidates: {count}"
            for classification, count in loader_phase[
                "candidate_classifications"
            ].items()
        ),
        f"- CORE: {dispositions.get(Disposition.CORE.value, 0)}",
        f"- INTERFACE: {dispositions.get(Disposition.INTERFACE.value, 0)}",
        f"- MODULE: {dispositions.get(Disposition.MODULE.value, 0)}",
        f"- DEAD: {dispositions.get(Disposition.DEAD.value, 0)}",
        f"- UNKNOWN: {dispositions.get(Disposition.UNKNOWN.value, 0)}",
        "",
        "## Candidates",
        "",
        "| Candidate | Functions | Interfaces | Known bytes | Net estimate | "
        "Loader class | Readiness | Basis |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    for candidate in report["candidates"]:
        lines.append(
            f"| `{candidate['id']}` | {len(candidate['functions'])} | "
            f"{len(candidate['interfaces'])} | "
            f"{candidate['known_size_bytes']} | "
            f"{candidate['estimated_net_bytes']} | "
            f"{candidate['loader_phase_classification']} | "
            f"{candidate['readiness']} | "
            f"{candidate['readiness_basis'] or '—'} |"
        )
    if not report["candidates"]:
        lines.append("| _none_ | 0 | 0 | 0 | 0 | — | — | — |")

    unknown = [
        decision
        for decision in report["decisions"]
        if decision["disposition"] == Disposition.UNKNOWN.value
    ]
    lines.extend(["", "## Blocked or Unknown Functions", ""])
    if not unknown:
        lines.append("No functions are blocked by unresolved safety evidence.")
    else:
        for decision in unknown:
            reasons = "; ".join(decision["reasons"])
            lines.append(
                f"- `{decision['id']}`"
                f" ({decision['source_path'] or 'source unknown'}): {reasons}"
            )

    lines.extend(["", "## Candidate Details", ""])
    for candidate in report["candidates"]:
        lines.extend(
            [
                f"### `{candidate['id']}`",
                "",
                f"- Readiness: {candidate['readiness']}",
                f"- Readiness basis: "
                f"{candidate['readiness_basis'] or 'none'}",
                f"- Aggregate savings group: "
                f"{candidate['aggregate_savings_group'] or 'none'}",
                f"- Loader phase: "
                f"{candidate['loader_phase_classification']}",
                f"- Known size: {candidate['known_size_bytes']} bytes",
                f"- Functions without size data: "
                f"{candidate['unknown_size_functions']}",
                f"- Incoming boundary edges: "
                f"{len(candidate['incoming_edges'])}",
                f"- Outgoing boundary edges: "
                f"{len(candidate['outgoing_edges'])}",
                "",
                "Functions:",
                "",
            ]
        )
        lines.extend(f"- `{item}`" for item in candidate["functions"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _candidate_to_dict(
    graph: ReferenceGraph,
    candidate: ModuleCandidate,
    *,
    policy: PlannerPolicy,
    interface_stub_bytes: int,
    additional_interface_stub_bytes: int,
    minimum_estimated_savings_bytes: int,
) -> Dict[str, Any]:
    interface_count = len(candidate.interfaces)
    estimated_stub_bytes = (
        interface_stub_bytes
        + additional_interface_stub_bytes * (interface_count - 1)
        if interface_count
        else 0
    )
    estimated_net_bytes = (
        candidate.known_size_bytes - estimated_stub_bytes
    )
    evidence_reasons = []
    blocked_reasons = []
    execution_edges = []
    load_safe_execution_edges = []
    if candidate.unknown_size_functions:
        evidence_reasons.append("missing function size data")
    if not candidate.functions:
        blocked_reasons.append("candidate has no functions")
    deferred_init_functions = {
        node_id
        for node_id in candidate.functions
        if (
            "__init" in graph.nodes[node_id].attributes
            or (
                graph.nodes[node_id].section is not None
                and graph.nodes[node_id].section.startswith(".init")
            )
        )
        and "deferred_initcall_safe"
        not in graph.nodes[node_id].attributes
    }
    if deferred_init_functions:
        evidence_reasons.append(
            "deferred built-in init code lacks a reviewed initcall "
            "registration rewrite"
        )
    if not candidate.interfaces:
        blocked_reasons.append(
            "candidate has no resident lazy-load interface"
        )
    else:
        execution_edges = [
            edge
            for edge in candidate.incoming_edges
            if (
                edge.target in candidate.interfaces
                and edge.kind
                in {
                    EdgeKind.DIRECT_CALL,
                    EdgeKind.INDIRECT_CALL,
                    EdgeKind.ASSEMBLY,
                }
                and graph.nodes[edge.source].kind
                is EntityKind.FUNCTION
            )
        ]
        if not execution_edges:
            evidence_reasons.append(
                "lazy interface has no executable incoming call edge"
            )
        load_safe_execution_edges = [
            edge
            for edge in execution_edges
            if is_demand_load_safe(
                graph.nodes[edge.source], policy
            )
        ]
        unsafe_sources = {
            edge.source
            for edge in execution_edges
            if not is_demand_load_safe(
                graph.nodes[edge.source], policy
            )
        }
        if unsafe_sources:
            evidence_reasons.append(
                "lazy interface caller context is not proven load-safe"
            )
    loader_phase_classification = _loader_phase_classification(
        candidate,
        execution_edges,
        load_safe_execution_edges,
        policy,
    )
    if loader_phase_classification == "EARLY_CORE":
        blocked_reasons.append(
            "candidate is reachable before the module loader is ready"
        )
    elif loader_phase_classification == "PRELOAD":
        evidence_reasons.append(
            "post-loader non-sleeping interface requires explicit preload "
            "and registration-lifetime proof"
        )
    elif loader_phase_classification == "UNKNOWN_PHASE":
        evidence_reasons.append(
            "module-loader phase is not proven for the lazy interface"
        )
    if (
        not candidate.unknown_size_functions
        and estimated_net_bytes < minimum_estimated_savings_bytes
    ):
        blocked_reasons.append(_BELOW_MINIMUM_REASON)
    if blocked_reasons:
        readiness = "BLOCKED"
    elif evidence_reasons:
        readiness = "NEEDS_EVIDENCE"
    else:
        readiness = "READY"
    readiness_reasons = blocked_reasons + evidence_reasons
    return {
        "id": candidate.id,
        "functions": list(candidate.functions),
        "owned_globals": list(candidate.owned_globals),
        "interfaces": list(candidate.interfaces),
        "executable_interface_edges": len(execution_edges),
        "load_safe_interface_edges": len(load_safe_execution_edges),
        "loader_phase_classification": (
            loader_phase_classification
        ),
        "known_size_bytes": candidate.known_size_bytes,
        "unknown_size_functions": candidate.unknown_size_functions,
        "estimated_stub_bytes": estimated_stub_bytes,
        "first_interface_stub_bytes": interface_stub_bytes,
        "additional_interface_stub_bytes": (
            additional_interface_stub_bytes
        ),
        "estimated_net_bytes": estimated_net_bytes,
        "minimum_estimated_savings_bytes": (
            minimum_estimated_savings_bytes
        ),
        "individual_minimum_met": (
            not candidate.unknown_size_functions
            and estimated_net_bytes
            >= minimum_estimated_savings_bytes
        ),
        "readiness_basis": (
            "individual"
            if readiness == "READY"
            else None
        ),
        "aggregate_savings_group": None,
        "readiness": readiness,
        "readiness_reasons": readiness_reasons,
        "incoming_edges": [
            _edge_to_dict(graph, edge) for edge in candidate.incoming_edges
        ],
        "outgoing_edges": [
            _edge_to_dict(graph, edge) for edge in candidate.outgoing_edges
        ],
    }


def _apply_aggregate_savings_groups(
    candidates: list[Dict[str, Any]],
    *,
    enabled: bool,
    minimum_estimated_savings_bytes: int,
) -> list[Dict[str, Any]]:
    """Promote independently safe positive candidates as one release group.

    The permanent-memory objective applies to the deployed kernel as a whole.
    Several independently loadable modules may therefore satisfy the minimum
    together even if no small member does so alone.  Only candidates whose
    sole blocker is the byte threshold participate; phase, context, boundary,
    and evidence failures are never hidden by aggregation.
    """

    if not enabled or minimum_estimated_savings_bytes <= 0:
        return []
    eligible = [
        candidate
        for candidate in candidates
        if (
            candidate["readiness"] == "BLOCKED"
            and candidate["readiness_reasons"]
            == [_BELOW_MINIMUM_REASON]
            and candidate["estimated_net_bytes"] > 0
            and candidate["loader_phase_classification"]
            in {"LAZY_READY", "NOT_ENFORCED"}
        )
    ]
    estimated_net_bytes = sum(
        candidate["estimated_net_bytes"]
        for candidate in eligible
    )
    if (
        len(eligible) < 2
        or estimated_net_bytes < minimum_estimated_savings_bytes
    ):
        return []
    member_ids = tuple(
        sorted(candidate["id"] for candidate in eligible)
    )
    digest = hashlib.sha256(
        "\n".join(member_ids).encode("utf-8")
    ).hexdigest()[:16]
    group_id = f"aggregate:{digest}"
    group = {
        "id": group_id,
        "candidate_ids": list(member_ids),
        "candidates": len(member_ids),
        "functions": sum(
            len(candidate["functions"])
            for candidate in eligible
        ),
        "estimated_net_bytes": estimated_net_bytes,
        "minimum_estimated_savings_bytes": (
            minimum_estimated_savings_bytes
        ),
    }
    for candidate in eligible:
        candidate["readiness"] = "READY"
        candidate["readiness_reasons"] = []
        candidate["readiness_basis"] = "aggregate_savings_group"
        candidate["aggregate_savings_group"] = group_id
    return [group]


def _loader_phase_classification(
    candidate: ModuleCandidate,
    execution_edges: Iterable[ReferenceEdge],
    load_safe_execution_edges: Iterable[ReferenceEdge],
    policy: PlannerPolicy,
) -> str:
    if not policy.enforce_loader_ready_phase:
        return "NOT_ENFORCED"
    execution = tuple(execution_edges)
    safe = tuple(load_safe_execution_edges)
    related = set(candidate.functions)
    related.update(edge.source for edge in execution)
    if related.intersection(policy.pre_loader_observed_functions):
        return "EARLY_CORE"
    if related.intersection(
        policy.unknown_phase_observed_functions
    ):
        return "UNKNOWN_PHASE"
    if not execution:
        return "UNKNOWN_PHASE"
    if len(safe) == len(execution):
        return "LAZY_READY"
    safe_edges = set(safe)
    unsafe_sources = {
        edge.source for edge in execution if edge not in safe_edges
    }
    if (
        unsafe_sources
        and unsafe_sources.issubset(
            policy.post_loader_observed_functions
        )
    ):
        return "PRELOAD"
    return "UNKNOWN_PHASE"


def _edge_to_dict(
    graph: ReferenceGraph,
    edge: ReferenceEdge,
) -> Dict[str, Any]:
    return {
        "source": edge.source,
        "source_symbol": graph.nodes[edge.source].symbol,
        "target": edge.target,
        "target_symbol": graph.nodes[edge.target].symbol,
        "kind": edge.kind.value,
        "location": edge.location,
        "field_path": edge.field_path,
        "contexts": sorted(context.value for context in edge.contexts),
        "evidence": sorted(edge.evidence),
    }

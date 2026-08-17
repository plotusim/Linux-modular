"""Empirical startup-phase accounting against the final linked kernel.

This report deliberately does not reuse planner dispositions.  It answers a
different question: which final ``vmlinux`` function symbols were observed
before the module-loader-ready marker, only afterwards, or not at all in the
supplied traces.  Backend load-safety and source-extraction constraints remain
separate from this startup classification.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from .errors import GraphValidationError
from .full_kernel_stats import LinkedFunctionSymbol
from .model import EntityKind, ReferenceGraph
from .observations import BootObservations, BootPhase


BOOT_PHASE_STATS_SCHEMA_VERSION = 1
BOOT_PHASE_CATEGORIES = (
    "BOOT_HOT",
    "POST_BOOT_ONLY",
    "BOOT_COLD",
    "UNKNOWN",
)
_CONSERVATIVE_CATEGORY_ORDER = {
    "BOOT_HOT": 0,
    "UNKNOWN": 1,
    "POST_BOOT_ONLY": 2,
    "BOOT_COLD": 3,
}


def summarize_boot_phase(
    graph: ReferenceGraph,
    observations: BootObservations,
    linked_symbols: Iterable[LinkedFunctionSymbol],
) -> dict[str, Any]:
    """Reconcile loader-phase observations with final ELF functions.

    ``BOOT_COLD`` is an empirical upper-bound category: the function was not
    observed in any supplied trace.  It is not, by itself, proof that moving
    the function is safe or that other startup workloads will not execute it.
    """

    graph.validate()
    if not observations.loader_ready_observed:
        raise GraphValidationError(
            "boot-phase accounting requires a loader-ready marker in every "
            "trace"
        )
    _validate_observation_nodes(graph, observations)

    category_by_node = {
        node.id: _node_boot_category(node.id, observations)
        for node in graph.nodes.values()
        if node.kind is EntityKind.FUNCTION
    }
    graph_machine_nodes: dict[
        tuple[str, int], list[tuple[str, str, str | None]]
    ] = defaultdict(list)
    for node_id, category in category_by_node.items():
        node = graph.nodes[node_id]
        if node.size_bytes is None or node.size_bytes <= 0:
            continue
        graph_machine_nodes[(node.symbol, node.size_bytes)].append(
            (node_id, category, node.source_path)
        )

    all_linked = tuple(linked_symbols)
    positive_linked = tuple(
        symbol for symbol in all_linked if symbol.size_bytes > 0
    )
    linked_by_key: Counter[tuple[str, int]] = Counter(
        (symbol.symbol, symbol.size_bytes) for symbol in positive_linked
    )

    linked_counts: Counter[str] = Counter()
    linked_bytes: Counter[str] = Counter()
    source_counts: dict[str, Counter[str]] = {
        category: Counter() for category in BOOT_PHASE_CATEGORIES
    }
    source_bytes: dict[str, Counter[str]] = {
        category: Counter() for category in BOOT_PHASE_CATEGORIES
    }
    source_unit_counts: dict[str, Counter[str]] = defaultdict(Counter)
    source_unit_bytes: dict[str, Counter[str]] = defaultdict(Counter)
    matched_node_ids: set[str] = set()
    unmatched_linked_count = 0
    unmatched_linked_bytes = 0
    matched_count = 0
    matched_bytes = 0

    for key in sorted(set(linked_by_key) | set(graph_machine_nodes)):
        _symbol, size = key
        linked_count = linked_by_key.get(key, 0)
        graph_nodes = sorted(
            graph_machine_nodes.get(key, ()),
            key=lambda item: (
                _CONSERVATIVE_CATEGORY_ORDER[item[1]],
                item[0],
            ),
        )
        selected = graph_nodes[:linked_count]
        for node_id, category, source_path in selected:
            matched_node_ids.add(node_id)
            linked_counts[category] += 1
            linked_bytes[category] += size
            source_root = _source_root(source_path)
            source_counts[category][source_root] += 1
            source_bytes[category][source_root] += size
            if source_path:
                source_unit_counts[source_path][category] += 1
                source_unit_bytes[source_path][category] += size
        exact = len(selected)
        matched_count += exact
        matched_bytes += exact * size
        missing = linked_count - exact
        if missing:
            linked_counts["UNKNOWN"] += missing
            linked_bytes["UNKNOWN"] += missing * size
            source_counts["UNKNOWN"]["<unmatched>"] += missing
            source_bytes["UNKNOWN"]["<unmatched>"] += missing * size
            unmatched_linked_count += missing
            unmatched_linked_bytes += missing * size

    linked_total_count = len(positive_linked)
    linked_total_bytes = sum(
        symbol.size_bytes for symbol in positive_linked
    )
    graph_machine_count = sum(
        len(nodes) for nodes in graph_machine_nodes.values()
    )
    graph_machine_bytes = sum(
        size * len(nodes)
        for (_symbol, size), nodes in graph_machine_nodes.items()
    )
    defer_functions = (
        linked_counts["POST_BOOT_ONLY"] + linked_counts["BOOT_COLD"]
    )
    defer_bytes = (
        linked_bytes["POST_BOOT_ONLY"] + linked_bytes["BOOT_COLD"]
    )
    source_unit_report = _source_unit_report(
        source_unit_counts, source_unit_bytes
    )

    return {
        "schema_version": BOOT_PHASE_STATS_SCHEMA_VERSION,
        "stage": "boot-phase-function-accounting",
        "methodology": {
            "primary_denominator": (
                "positive-size defined function/text symbol rows in final "
                "vmlinux"
            ),
            "exact_match_key": ["symbol", "size_bytes"],
            "planner_dispositions_used": False,
            "category_mapping": {
                "observed before loader-ready": "BOOT_HOT",
                "observed both before and after loader-ready": "BOOT_HOT",
                "observed only after loader-ready": "POST_BOOT_ONLY",
                "not observed in supplied traces": "BOOT_COLD",
                "unknown observation phase": "UNKNOWN",
                "unmatched final symbol": "UNKNOWN",
            },
            "boot_cold_semantics": (
                "empirical startup-defer upper bound, not module-safety proof"
            ),
        },
        "summary": {
            "linked_function_symbols": linked_total_count,
            "linked_function_bytes": linked_total_bytes,
            "graph_to_linked_exact_matches": matched_count,
            "graph_to_linked_coverage_percent": _percent(
                matched_count, linked_total_count
            ),
            "categories": _category_report(
                linked_counts,
                linked_bytes,
                count_total=linked_total_count,
                byte_total=linked_total_bytes,
            ),
            "startup_defer_upper_bound_functions": defer_functions,
            "startup_defer_upper_bound_ratio_percent": _percent(
                defer_functions, linked_total_count
            ),
            "startup_defer_upper_bound_bytes": defer_bytes,
            "startup_defer_upper_bound_byte_ratio_percent": _percent(
                defer_bytes, linked_total_bytes
            ),
        },
        "observation_coverage": {
            "traces": len(observations.trace_sha256),
            "loader_ready_marker": observations.loader_ready_marker,
            "loader_ready_timestamps_seconds": list(
                observations.loader_ready_timestamps
            ),
            "observed_graph_functions": len(
                observations.observed_node_ids
            ),
            "pre_loader_graph_functions": len(
                observations.pre_loader_node_ids
            ),
            "post_loader_graph_functions": len(
                observations.post_loader_node_ids
            ),
            "unknown_phase_graph_functions": len(
                observations.unknown_phase_node_ids
            ),
            "observed_graph_functions_exactly_matched_to_vmlinux": len(
                matched_node_ids & observations.observed_node_ids
            ),
        },
        "linked_coverage": {
            "graph_positive_size_functions": graph_machine_count,
            "graph_positive_size_bytes": graph_machine_bytes,
            "exact_matches": matched_count,
            "exact_match_bytes": matched_bytes,
            "unmatched_linked_functions_classified_unknown": (
                unmatched_linked_count
            ),
            "unmatched_linked_bytes_classified_unknown": (
                unmatched_linked_bytes
            ),
            "unlinked_graph_functions_excluded": (
                graph_machine_count - matched_count
            ),
            "unlinked_graph_bytes_excluded": (
                graph_machine_bytes - matched_bytes
            ),
        },
        "source_roots": {
            category: {
                root: {
                    "functions": source_counts[category][root],
                    "bytes": source_bytes[category][root],
                }
                for root in sorted(source_counts[category])
            }
            for category in BOOT_PHASE_CATEGORIES
        },
        "source_unit_accounting": source_unit_report,
    }


def render_boot_phase_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    coverage = report["linked_coverage"]
    observations = report["observation_coverage"]
    lines = [
        "# Final-vmlinux startup phase accounting",
        "",
        "## Result",
        "",
        (
            f"The final `vmlinux` contains "
            f"{summary['linked_function_symbols']:,} positive-size function "
            f"symbols ({summary['linked_function_bytes']:,} bytes)."
        ),
        "",
        "| Category | Functions | Ratio | Bytes | Byte ratio |",
        "|---|---:|---:|---:|---:|",
    ]
    for category in BOOT_PHASE_CATEGORIES:
        value = summary["categories"][category]
        lines.append(
            f"| {category} | {value['functions']:,} | "
            f"{value['function_ratio_percent']:.6f}% | "
            f"{value['bytes']:,} | {value['byte_ratio_percent']:.6f}% |"
        )
    lines.extend(
        [
            "",
            "## Startup-defer upper bound",
            "",
            (
                f"- Functions: "
                f"{summary['startup_defer_upper_bound_functions']:,} "
                f"({summary['startup_defer_upper_bound_ratio_percent']:.6f}%)"
            ),
            (
                f"- Baseline function bytes: "
                f"{summary['startup_defer_upper_bound_bytes']:,} "
                f"({summary['startup_defer_upper_bound_byte_ratio_percent']:.6f}%)"
            ),
            "",
            "## Evidence and coverage",
            "",
            (
                f"- Loader-ready traces: {observations['traces']:,}; "
                f"marker: `{observations['loader_ready_marker']}`"
            ),
            (
                f"- Pre-loader graph functions observed: "
                f"{observations['pre_loader_graph_functions']:,}"
            ),
            (
                f"- Exact IR-to-ELF matches: {coverage['exact_matches']:,}/"
                f"{summary['linked_function_symbols']:,} "
                f"({summary['graph_to_linked_coverage_percent']:.6f}%)"
            ),
            (
                f"- Unmatched final functions classified UNKNOWN: "
                f"{coverage['unmatched_linked_functions_classified_unknown']:,}"
            ),
            (
                f"- Entirely startup-defer source units: "
                f"{report['source_unit_accounting']['classifications']['DEFER_ONLY']['units']:,} "
                f"units, "
                f"{report['source_unit_accounting']['classifications']['DEFER_ONLY']['bytes']:,} "
                f"function bytes"
            ),
            "",
            "`BOOT_COLD` means only that a function was not observed in the "
            "supplied startup traces. The startup-defer value is an empirical "
            "upper bound, independent of planner residency and backend "
            "load-safety decisions; it is not automatic authorization to move "
            "every function.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _validate_observation_nodes(
    graph: ReferenceGraph,
    observations: BootObservations,
) -> None:
    for node_id in observations.observed_node_ids:
        node = graph.nodes.get(node_id)
        if node is None or node.kind is not EntityKind.FUNCTION:
            raise GraphValidationError(
                f"observation references unknown function {node_id!r}"
            )
        if not observations.node_phases.get(node_id):
            raise GraphValidationError(
                f"observation for {node_id!r} has no loader phase"
            )


def _node_boot_category(
    node_id: str,
    observations: BootObservations,
) -> str:
    phases = observations.node_phases.get(node_id, frozenset())
    if BootPhase.PRE_LOADER in phases:
        return "BOOT_HOT"
    if BootPhase.UNKNOWN in phases:
        return "UNKNOWN"
    if BootPhase.POST_LOADER in phases:
        return "POST_BOOT_ONLY"
    return "BOOT_COLD"


def _source_root(source_path: str | None) -> str:
    if not source_path:
        return "<unknown>"
    return source_path.split("/", 1)[0]


def _category_report(
    counts: Mapping[str, int],
    byte_counts: Mapping[str, int],
    *,
    count_total: int,
    byte_total: int,
) -> dict[str, dict[str, int | float]]:
    return {
        category: {
            "functions": counts.get(category, 0),
            "function_ratio_percent": _percent(
                counts.get(category, 0), count_total
            ),
            "bytes": byte_counts.get(category, 0),
            "byte_ratio_percent": _percent(
                byte_counts.get(category, 0), byte_total
            ),
        }
        for category in BOOT_PHASE_CATEGORIES
    }


def _source_unit_report(
    unit_counts: Mapping[str, Counter[str]],
    unit_bytes: Mapping[str, Counter[str]],
) -> dict[str, Any]:
    classifications: dict[str, Counter[str]] = {
        "DEFER_ONLY": Counter(),
        "MIXED_BOOT": Counter(),
        "BOOT_HOT_ONLY": Counter(),
        "UNKNOWN": Counter(),
    }
    defer_only_units = []
    for source_path in sorted(unit_counts):
        counts = unit_counts[source_path]
        byte_counts = unit_bytes[source_path]
        defer_functions = (
            counts["POST_BOOT_ONLY"] + counts["BOOT_COLD"]
        )
        defer_bytes = (
            byte_counts["POST_BOOT_ONLY"] + byte_counts["BOOT_COLD"]
        )
        if counts["UNKNOWN"]:
            classification = "UNKNOWN"
        elif counts["BOOT_HOT"] and defer_functions:
            classification = "MIXED_BOOT"
        elif counts["BOOT_HOT"]:
            classification = "BOOT_HOT_ONLY"
        else:
            classification = "DEFER_ONLY"
        summary = classifications[classification]
        summary["units"] += 1
        summary["functions"] += sum(counts.values())
        summary["bytes"] += sum(byte_counts.values())
        if classification == "DEFER_ONLY":
            defer_only_units.append(
                {
                    "source_path": source_path,
                    "functions": defer_functions,
                    "bytes": defer_bytes,
                    "post_boot_only_functions": counts[
                        "POST_BOOT_ONLY"
                    ],
                    "boot_cold_functions": counts["BOOT_COLD"],
                }
            )
    defer_only_units.sort(
        key=lambda value: (
            -value["bytes"],
            -value["functions"],
            value["source_path"],
        )
    )
    return {
        "source_units": len(unit_counts),
        "classifications": {
            classification: {
                "units": values["units"],
                "functions": values["functions"],
                "bytes": values["bytes"],
            }
            for classification, values in classifications.items()
        },
        "defer_only_units": defer_only_units,
    }


def _percent(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 6)

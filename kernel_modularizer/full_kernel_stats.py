"""Conservative accounting against the final linked kernel image."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping

from .errors import GraphValidationError, SchemaVersionError
from .model import EntityKind, ReferenceGraph


FULL_KERNEL_STATS_SCHEMA_VERSION = 1
SOURCE_CLOSURE_STATS_SCHEMA_VERSION = 1
SUCCESS_VALIDATION_SCHEMA_VERSION = 1
CATEGORIES = ("CORE", "READY", "BLOCKED", "UNKNOWN")
_FUNCTION_SYMBOL_TYPES = frozenset("TtWwIi")
_SUCCESS_GATES = (
    "source_extraction_passed",
    "kernel_build_passed",
    "qemu_boot_passed",
    "modprobe_passed",
    "unload_passed",
    "size_gate_passed",
    "startup_ab_passed",
)
_CONSERVATIVE_CATEGORY_ORDER = {
    "CORE": 0,
    "UNKNOWN": 1,
    "BLOCKED": 2,
    "READY": 3,
}


@dataclass(frozen=True)
class LinkedFunctionSymbol:
    symbol: str
    symbol_type: str
    size_bytes: int


def load_plan_report(path: str | Path) -> dict[str, Any]:
    report = _load_json_object(path, "module plan")
    if report.get("schema_version") != 1:
        raise SchemaVersionError(
            "unsupported module plan schema_version "
            f"{report.get('schema_version')!r}"
        )
    return report


def read_linked_function_symbols(
    vmlinux: str | Path,
    *,
    nm: str | Path = "nm",
) -> tuple[LinkedFunctionSymbol, ...]:
    image = Path(vmlinux)
    if not image.is_file():
        raise GraphValidationError(f"vmlinux does not exist: {image}")
    try:
        completed = subprocess.run(
            [
                str(nm),
                "-S",
                "--defined-only",
                "--format=posix",
                str(image),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = (
            error.stderr.strip()
            if isinstance(error, subprocess.CalledProcessError)
            and error.stderr
            else str(error)
        )
        raise GraphValidationError(
            f"cannot read final vmlinux symbols from {image}: {detail}"
        ) from error

    symbols = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if (
            len(fields) < 4
            or fields[1] not in _FUNCTION_SYMBOL_TYPES
        ):
            continue
        try:
            size = int(fields[3], 16)
        except ValueError:
            continue
        symbols.append(
            LinkedFunctionSymbol(
                symbol=fields[0],
                symbol_type=fields[1],
                size_bytes=size,
            )
        )
    return tuple(symbols)


def load_successful_candidates(
    path: str | Path,
) -> frozenset[str]:
    return frozenset(load_successful_validations(path))


def load_successful_validations(
    path: str | Path,
) -> dict[str, tuple[str, ...]]:
    artifact = _load_json_object(path, "successful-candidate validation")
    if artifact.get("schema_version") != SUCCESS_VALIDATION_SCHEMA_VERSION:
        raise SchemaVersionError(
            "unsupported successful-candidate validation schema_version "
            f"{artifact.get('schema_version')!r}"
        )
    validations = artifact.get("validations")
    if not isinstance(validations, list):
        raise GraphValidationError(
            "successful-candidate validation.validations must be an array"
        )
    successful = {}
    seen = set()
    for index, value in enumerate(validations):
        if not isinstance(value, Mapping):
            raise GraphValidationError(
                f"validations[{index}] must be an object"
            )
        candidate_id = value.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise GraphValidationError(
                f"validations[{index}].candidate_id must be a string"
            )
        if candidate_id in seen:
            raise GraphValidationError(
                f"duplicate validation for candidate {candidate_id!r}"
            )
        seen.add(candidate_id)
        invalid_gates = [
            gate for gate in _SUCCESS_GATES if value.get(gate) is not True
        ]
        moved_function_ids = value.get("moved_function_ids", [])
        if not isinstance(moved_function_ids, list) or not all(
            isinstance(item, str) and item
            for item in moved_function_ids
        ):
            raise GraphValidationError(
                f"validations[{index}].moved_function_ids must be a "
                "string array"
            )
        if len(set(moved_function_ids)) != len(moved_function_ids):
            raise GraphValidationError(
                f"validations[{index}].moved_function_ids contains "
                "duplicates"
            )
        if invalid_gates:
            continue
        successful[candidate_id] = tuple(moved_function_ids)
    return successful


def summarize_full_kernel(
    graph: ReferenceGraph,
    plan_report: Mapping[str, Any],
    linked_symbols: Iterable[LinkedFunctionSymbol],
    *,
    successful_candidates: Iterable[str] = (),
    successful_function_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build disjoint IR and final-ELF function accounting.

    The primary denominator is positive-size defined function/text symbol rows
    in the final ``vmlinux``. Exact graph matches require both the linker symbol
    name and byte size to agree. Any row without an exact match is UNKNOWN.
    """

    graph.validate()
    decisions, candidates = _validate_plan(graph, plan_report)
    successful = frozenset(successful_candidates)
    validated_function_ids = frozenset(successful_function_ids)
    unknown_successes = sorted(successful.difference(candidates))
    if unknown_successes:
        raise GraphValidationError(
            "successful validation references unknown candidate(s): "
            + ", ".join(unknown_successes)
        )
    non_ready_successes = sorted(
        candidate_id
        for candidate_id in successful
        if candidates[candidate_id]["readiness"] != "READY"
    )
    if non_ready_successes:
        raise GraphValidationError(
            "only READY candidates can count as successfully modularized: "
            + ", ".join(non_ready_successes)
        )
    graph_function_ids = {
        node.id
        for node in graph.nodes.values()
        if node.kind is EntityKind.FUNCTION
    }
    invalid_successful_functions = sorted(
        validated_function_ids.difference(graph_function_ids)
    )
    if invalid_successful_functions:
        raise GraphValidationError(
            "successful validation references unknown function(s): "
            + ", ".join(invalid_successful_functions)
        )
    if validated_function_ids and not successful:
        raise GraphValidationError(
            "successful function closure requires a successful candidate"
        )

    category_by_node = {
        node_id: _decision_category(
            decision, candidates
        )
        for node_id, decision in decisions.items()
    }
    ir_counts: Counter[str] = Counter(category_by_node.values())
    ir_known_bytes: Counter[str] = Counter()
    graph_machine_nodes: dict[
        tuple[str, int], list[tuple[str, str]]
    ] = defaultdict(list)
    for node_id, category in category_by_node.items():
        node = graph.nodes[node_id]
        if node.size_bytes is None:
            continue
        if node.size_bytes <= 0:
            continue
        ir_known_bytes[category] += node.size_bytes
        graph_machine_nodes[(node.symbol, node.size_bytes)].append(
            (node_id, category)
        )

    all_linked = tuple(linked_symbols)
    positive_linked = tuple(
        symbol for symbol in all_linked if symbol.size_bytes > 0
    )
    zero_size_linked = sum(
        symbol.size_bytes == 0 for symbol in all_linked
    )
    linked_by_key: Counter[tuple[str, int]] = Counter(
        (symbol.symbol, symbol.size_bytes) for symbol in positive_linked
    )

    linked_counts: Counter[str] = Counter()
    linked_bytes: Counter[str] = Counter()
    matched_node_ids = set()
    unmatched_linked_count = 0
    unmatched_linked_bytes = 0
    unmatched_linked_examples = []
    matched_count = 0
    matched_bytes = 0
    all_keys = sorted(set(linked_by_key) | set(graph_machine_nodes))
    selected_by_key = _select_exact_graph_nodes(
        graph_machine_nodes,
        linked_by_key,
    )
    for key in all_keys:
        symbol, size = key
        linked_count = linked_by_key.get(key, 0)
        selected = selected_by_key.get(key, ())
        for node_id, category in selected:
            matched_node_ids.add(node_id)
            linked_counts[category] += 1
            linked_bytes[category] += size
        exact = len(selected)
        matched_count += exact
        matched_bytes += exact * size
        missing = linked_count - exact
        if missing:
            linked_counts["UNKNOWN"] += missing
            linked_bytes["UNKNOWN"] += missing * size
            unmatched_linked_count += missing
            unmatched_linked_bytes += missing * size
            if len(unmatched_linked_examples) < 100:
                unmatched_linked_examples.append(
                    {
                        "symbol": symbol,
                        "size_bytes": size,
                        "rows": missing,
                    }
                )

    graph_machine_count = sum(len(nodes) for nodes in graph_machine_nodes.values())
    graph_machine_bytes = sum(
        size * len(nodes)
        for (_symbol, size), nodes in graph_machine_nodes.items()
    )
    unlinked_graph_nodes = graph_machine_count - matched_count
    unlinked_graph_bytes = graph_machine_bytes - matched_bytes
    linked_total_count = len(positive_linked)
    linked_total_bytes = sum(
        symbol.size_bytes for symbol in positive_linked
    )

    successful_node_ids = {
        node_id
        for candidate_id in successful
        for node_id in candidates[candidate_id]["functions"]
    }
    return _finish_full_kernel_summary(locals())


def summarize_source_closures(
    graph: ReferenceGraph,
    plan_report: Mapping[str, Any],
    linked_symbols: Iterable[LinkedFunctionSymbol],
    source_closures: Iterable[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Reconcile moved source definitions with a baseline final image.

    Callback-table rewriting can move functions which the conservative
    planner originally classified as CORE.  Such closures therefore cannot
    be represented honestly as another planner category.  This report keeps
    the planner partition unchanged and accounts the moved closure as an
    orthogonal set.  A function counts only when its graph node wins the same
    conservative ``(symbol, size_bytes)`` match used by
    :func:`summarize_full_kernel`.
    """

    graph.validate()
    all_linked = tuple(linked_symbols)
    positive_linked = tuple(
        symbol for symbol in all_linked if symbol.size_bytes > 0
    )
    linked_by_key: Counter[tuple[str, int]] = Counter(
        (symbol.symbol, symbol.size_bytes) for symbol in positive_linked
    )
    matched_node_ids = match_final_linked_function_ids(
        graph,
        plan_report,
        positive_linked,
    )
    function_nodes: dict[tuple[str, str], list[str]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind is not EntityKind.FUNCTION or not node.source_path:
            continue
        function_nodes[(node.source_path, node.symbol)].append(node.id)

    closure_reports = []
    all_moved_ids = set()
    total_moved_globals = 0
    for label, closure in source_closures:
        if not isinstance(label, str) or not label:
            raise GraphValidationError(
                "source closure label must be a non-empty string"
            )
        if not isinstance(closure, Mapping):
            raise GraphValidationError(
                f"source closure {label} must be an object"
            )
        if closure.get("schema_version") != 1:
            raise SchemaVersionError(
                f"unsupported source closure schema_version in {label}: "
                f"{closure.get('schema_version')!r}"
            )
        candidate_id = closure.get("candidate_id")
        translation_units = closure.get("translation_units")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise GraphValidationError(
                f"source closure {label} has an invalid candidate_id"
            )
        if not isinstance(translation_units, list):
            raise GraphValidationError(
                f"source closure {label}.translation_units must be an array"
            )

        moved_rows = []
        moved_globals = 0
        closure_ids = set()
        for index, unit in enumerate(translation_units):
            if not isinstance(unit, Mapping):
                raise GraphValidationError(
                    f"source closure {label}.translation_units[{index}] "
                    "must be an object"
                )
            source_path = unit.get("translation_unit")
            moved = unit.get("moved")
            if not isinstance(source_path, str) or not source_path:
                raise GraphValidationError(
                    f"source closure {label}.translation_units[{index}] "
                    "has an invalid translation_unit"
                )
            if not isinstance(moved, Mapping):
                raise GraphValidationError(
                    f"source closure {label}.translation_units[{index}]."
                    "moved must be an object"
                )
            functions = moved.get("functions")
            globals_ = moved.get("globals")
            if not isinstance(functions, list) or not all(
                isinstance(item, str) and item for item in functions
            ):
                raise GraphValidationError(
                    f"source closure {label}.translation_units[{index}]."
                    "moved.functions must be a string array"
                )
            if not isinstance(globals_, list) or not all(
                isinstance(item, str) and item for item in globals_
            ):
                raise GraphValidationError(
                    f"source closure {label}.translation_units[{index}]."
                    "moved.globals must be a string array"
                )
            if len(set(functions)) != len(functions):
                raise GraphValidationError(
                    f"source closure {label}.translation_units[{index}]."
                    "moved.functions contains duplicates"
                )
            moved_globals += len(globals_)
            for symbol in functions:
                ids = function_nodes.get((source_path, symbol), ())
                if len(ids) != 1:
                    raise GraphValidationError(
                        f"source closure {label} function "
                        f"{source_path}:{symbol} maps to {len(ids)} graph "
                        "nodes"
                    )
                node_id = ids[0]
                if node_id in closure_ids:
                    raise GraphValidationError(
                        f"source closure {label} repeats graph function "
                        f"{node_id}"
                    )
                closure_ids.add(node_id)
                node = graph.nodes[node_id]
                matched = node_id in matched_node_ids
                row = {
                    "node_id": node_id,
                    "name": symbol,
                    "source_path": source_path,
                    "size_bytes": node.size_bytes,
                }
                if not matched:
                    if node.size_bytes is None or node.size_bytes <= 0:
                        reason = "no_positive_graph_size"
                    elif linked_by_key.get(
                        (node.symbol, node.size_bytes), 0
                    ):
                        reason = "duplicate_exact_match_displaced"
                    else:
                        reason = "no_exact_final_elf_row"
                    row["reason"] = reason
                moved_rows.append((matched, row))

        repeated = sorted(closure_ids.intersection(all_moved_ids))
        if repeated:
            raise GraphValidationError(
                "source closure artifacts overlap moved functions: "
                + ", ".join(repeated)
            )
        all_moved_ids.update(closure_ids)
        total_moved_globals += moved_globals
        matched_rows = sorted(
            (row for matched, row in moved_rows if matched),
            key=lambda item: (item["source_path"], item["name"]),
        )
        unmatched_rows = sorted(
            (row for matched, row in moved_rows if not matched),
            key=lambda item: (item["source_path"], item["name"]),
        )
        matched_bytes = sum(
            int(row["size_bytes"]) for row in matched_rows
        )
        closure_reports.append(
            {
                "label": label,
                "candidate_id": candidate_id,
                "moved_source_function_definitions": len(moved_rows),
                "moved_source_global_definitions": moved_globals,
                "matched_final_elf_functions": len(matched_rows),
                "matched_baseline_bytes": matched_bytes,
                "matched_functions": matched_rows,
                "unmatched_functions": unmatched_rows,
            }
        )

    matched_total = sum(
        item["matched_final_elf_functions"] for item in closure_reports
    )
    byte_total = sum(
        symbol.size_bytes for symbol in positive_linked
    )
    matched_bytes_total = sum(
        item["matched_baseline_bytes"] for item in closure_reports
    )
    return {
        "schema_version": SOURCE_CLOSURE_STATS_SCHEMA_VERSION,
        "stage": "source-closure-final-elf-accounting",
        "methodology": {
            "primary_denominator": (
                "positive-size defined function/text symbol rows in baseline "
                "vmlinux"
            ),
            "exact_match_key": ["symbol", "size_bytes"],
            "duplicate_policy": (
                "prefer CORE, UNKNOWN, BLOCKED, then READY graph nodes"
            ),
            "relationship_to_planner_partition": "orthogonal validated set",
        },
        "baseline": {
            "function_symbols": len(positive_linked),
            "function_bytes": byte_total,
        },
        "closures": closure_reports,
        "summary": {
            "source_closures": len(closure_reports),
            "moved_source_function_definitions": len(all_moved_ids),
            "moved_source_global_definitions": total_moved_globals,
            "matched_final_elf_functions": matched_total,
            "matched_final_elf_function_ratio_percent": _percent(
                matched_total, len(positive_linked)
            ),
            "matched_baseline_bytes": matched_bytes_total,
            "matched_baseline_byte_ratio_percent": _percent(
                matched_bytes_total, byte_total
            ),
        },
    }


def match_final_linked_function_ids(
    graph: ReferenceGraph,
    plan_report: Mapping[str, Any],
    linked_symbols: Iterable[LinkedFunctionSymbol],
) -> frozenset[str]:
    """Return graph nodes selected by conservative final-ELF matching."""

    graph.validate()
    decisions, candidates = _validate_plan(graph, plan_report)
    category_by_node = {
        node_id: _decision_category(decision, candidates)
        for node_id, decision in decisions.items()
    }
    graph_machine_nodes: dict[
        tuple[str, int], list[tuple[str, str]]
    ] = defaultdict(list)
    for node_id, category in category_by_node.items():
        node = graph.nodes[node_id]
        if node.size_bytes is None or node.size_bytes <= 0:
            continue
        graph_machine_nodes[(node.symbol, node.size_bytes)].append(
            (node_id, category)
        )
    linked_by_key: Counter[tuple[str, int]] = Counter(
        (symbol.symbol, symbol.size_bytes)
        for symbol in linked_symbols
        if symbol.size_bytes > 0
    )
    selected_by_key = _select_exact_graph_nodes(
        graph_machine_nodes,
        linked_by_key,
    )
    return frozenset(
        node_id
        for selected in selected_by_key.values()
        for node_id, _category in selected
    )


def render_source_closure_markdown(report: Mapping[str, Any]) -> str:
    """Render compact, reviewable source-closure ELF accounting."""

    summary = report["summary"]
    baseline = report["baseline"]
    lines = [
        "# Source-closure final-ELF accounting",
        "",
        (
            f"The baseline `vmlinux` contains "
            f"{baseline['function_symbols']:,} positive-size function "
            f"symbols ({baseline['function_bytes']:,} bytes)."
        ),
        "",
        "| Closure | Moved C functions | Final ELF functions | Bytes |",
        "|---|---:|---:|---:|",
    ]
    for closure in report["closures"]:
        lines.append(
            f"| `{closure['candidate_id']}` | "
            f"{closure['moved_source_function_definitions']:,} | "
            f"{closure['matched_final_elf_functions']:,} | "
            f"{closure['matched_baseline_bytes']:,} |"
        )
    lines.extend(
        [
            "",
            (
                f"Total: {summary['moved_source_function_definitions']:,} "
                f"moved C function definitions, "
                f"{summary['matched_final_elf_functions']:,} final ELF "
                f"functions "
                f"({summary['matched_final_elf_function_ratio_percent']:.6f}%), "
                f"and {summary['matched_baseline_bytes']:,} baseline bytes "
                f"({summary['matched_baseline_byte_ratio_percent']:.6f}%)."
            ),
            "",
            (
                "This set is orthogonal to CORE/READY/BLOCKED/UNKNOWN: "
                "callback-table rewriting can safely promote functions that "
                "the conservative planner originally kept in CORE."
            ),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _finish_full_kernel_summary(context: Mapping[str, Any]) -> dict[str, Any]:
    """Finish the report after the exact-linkage scan.

    Keeping this tail separate lets source-closure accounting reuse the exact
    matcher without making the main reconciliation loop less readable.
    """

    graph = context["graph"]
    candidates = context["candidates"]
    successful = context["successful"]
    validated_function_ids = context["validated_function_ids"]
    matched_node_ids = context["matched_node_ids"]
    successful_node_ids = context["successful_node_ids"]
    linked_counts = context["linked_counts"]
    linked_bytes = context["linked_bytes"]
    linked_total_count = context["linked_total_count"]
    linked_total_bytes = context["linked_total_bytes"]
    ir_counts = context["ir_counts"]
    ir_known_bytes = context["ir_known_bytes"]
    category_by_node = context["category_by_node"]
    graph_machine_count = context["graph_machine_count"]
    graph_machine_bytes = context["graph_machine_bytes"]
    matched_count = context["matched_count"]
    matched_bytes = context["matched_bytes"]
    zero_size_linked = context["zero_size_linked"]
    unmatched_linked_count = context["unmatched_linked_count"]
    unmatched_linked_bytes = context["unmatched_linked_bytes"]
    unlinked_graph_nodes = context["unlinked_graph_nodes"]
    unlinked_graph_bytes = context["unlinked_graph_bytes"]
    unmatched_linked_examples = context["unmatched_linked_examples"]

    successful_node_ids.update(validated_function_ids)
    successful_linked_node_ids = matched_node_ids.intersection(
        successful_node_ids
    )
    successful_linked_functions = len(successful_linked_node_ids)
    successful_linked_bytes = sum(
        graph.nodes[node_id].size_bytes or 0
        for node_id in successful_linked_node_ids
    )
    ready_candidates = sum(
        value["readiness"] == "READY"
        for value in candidates.values()
    )
    if successful:
        success_status = "validated"
    elif ready_candidates:
        success_status = "ready_candidates_not_yet_validated"
    else:
        success_status = "no_ready_candidates"

    linked_category_report = _category_report(
        linked_counts,
        linked_bytes,
        count_total=linked_total_count,
        byte_total=linked_total_bytes,
    )
    ir_category_report = _category_report(
        ir_counts,
        ir_known_bytes,
        count_total=len(category_by_node),
        byte_total=sum(ir_known_bytes.values()),
    )
    candidate_readiness = Counter(
        value["readiness"] for value in candidates.values()
    )
    candidate_functions = Counter()
    for value in candidates.values():
        candidate_functions[
            _readiness_category(value["readiness"])
        ] += len(value["functions"])

    return {
        "schema_version": FULL_KERNEL_STATS_SCHEMA_VERSION,
        "stage": "full-kernel-function-accounting",
        "methodology": {
            "primary_denominator": (
                "positive-size defined function/text symbol rows in final "
                "vmlinux"
            ),
            "exact_match_key": ["symbol", "size_bytes"],
            "category_mapping": {
                "core disposition": "CORE",
                "READY candidate": "READY",
                "BLOCKED candidate": "BLOCKED",
                "NEEDS_EVIDENCE candidate": "UNKNOWN",
                "unknown disposition": "UNKNOWN",
                "unmatched final symbol": "UNKNOWN",
            },
            "successful_function_gate": list(_SUCCESS_GATES),
        },
        "summary": {
            "ir_function_nodes": len(category_by_node),
            "linked_function_symbols": linked_total_count,
            "linked_function_bytes": linked_total_bytes,
            "graph_to_linked_exact_matches": matched_count,
            "graph_to_linked_coverage_percent": _percent(
                matched_count, linked_total_count
            ),
            "categories": linked_category_report,
            "successful_modularized_functions": (
                successful_linked_functions
            ),
            "successful_modularized_ratio_percent": _percent(
                successful_linked_functions, linked_total_count
            ),
            "successful_modularized_bytes": successful_linked_bytes,
            "successful_modularized_byte_ratio_percent": _percent(
                successful_linked_bytes, linked_total_bytes
            ),
        },
        "linked_accounting": {
            "function_symbols": linked_total_count,
            "function_bytes": linked_total_bytes,
            "zero_size_text_symbols_excluded": zero_size_linked,
            "categories": linked_category_report,
        },
        "ir_accounting": {
            "function_nodes": len(category_by_node),
            "known_machine_code_bytes": sum(ir_known_bytes.values()),
            "categories": ir_category_report,
        },
        "coverage": {
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
            "unlinked_graph_functions_excluded": unlinked_graph_nodes,
            "unlinked_graph_bytes_excluded": unlinked_graph_bytes,
            "unmatched_linked_examples": unmatched_linked_examples,
        },
        "candidates": {
            "total": len(candidates),
            "readiness": dict(sorted(candidate_readiness.items())),
            "functions_by_final_category": {
                category: candidate_functions.get(category, 0)
                for category in CATEGORIES
            },
        },
        "successful_modularization": {
            "status": success_status,
            "validated_candidate_ids": sorted(successful),
            "validated_function_ids": sorted(successful_node_ids),
            "functions": successful_linked_functions,
            "ratio_percent": _percent(
                successful_linked_functions, linked_total_count
            ),
            "bytes": successful_linked_bytes,
            "byte_ratio_percent": _percent(
                successful_linked_bytes, linked_total_bytes
            ),
        },
    }


def _select_exact_graph_nodes(
    graph_machine_nodes: Mapping[
        tuple[str, int], Iterable[tuple[str, str]]
    ],
    linked_by_key: Mapping[tuple[str, int], int],
) -> dict[tuple[str, int], tuple[tuple[str, str], ...]]:
    """Apply the shared conservative policy for duplicate ELF matches."""

    selected = {}
    for key in sorted(set(linked_by_key) | set(graph_machine_nodes)):
        graph_nodes = sorted(
            graph_machine_nodes.get(key, ()),
            key=lambda item: (
                _CONSERVATIVE_CATEGORY_ORDER[item[1]],
                item[0],
            ),
        )
        selected[key] = tuple(graph_nodes[: linked_by_key.get(key, 0)])
    return selected


def render_full_kernel_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    linked = report["linked_accounting"]
    coverage = report["coverage"]
    success = report["successful_modularization"]
    lines = [
        "# Full-kernel function modularization accounting",
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
    for category in CATEGORIES:
        value = linked["categories"][category]
        lines.append(
            f"| {category} | {value['functions']:,} | "
            f"{value['function_ratio_percent']:.6f}% | "
            f"{value['bytes']:,} | {value['byte_ratio_percent']:.6f}% |"
        )
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            (
                f"- Exact IR-to-ELF matches: "
                f"{coverage['exact_matches']:,}/"
                f"{summary['linked_function_symbols']:,} "
                f"({summary['graph_to_linked_coverage_percent']:.6f}%)"
            ),
            (
                f"- Unmatched final functions conservatively classified "
                f"UNKNOWN: "
                f"{coverage['unmatched_linked_functions_classified_unknown']:,}"
            ),
            (
                f"- Zero-size text aliases excluded from the denominator: "
                f"{linked['zero_size_text_symbols_excluded']:,}"
            ),
            "",
            "## Successfully modularized",
            "",
            (
                f"- Status: `{success['status']}`"
            ),
            (
                f"- Functions passing extraction, build, boot, modprobe and "
                f"unload gates: {success['functions']:,} "
                f"({success['ratio_percent']:.6f}%)"
            ),
            (
                f"- Matched baseline bytes represented by those functions: "
                f"{success['bytes']:,} "
                f"({success['byte_ratio_percent']:.6f}%)"
            ),
            "",
            "The primary denominator is the positive-size defined "
            "function/text symbol rows in the final linked image. An ELF "
            "function that cannot be matched to the IR graph by both symbol "
            "and size is counted as UNKNOWN.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _validate_plan(
    graph: ReferenceGraph,
    report: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    if report.get("schema_version") != 1:
        raise SchemaVersionError(
            "unsupported module plan schema_version "
            f"{report.get('schema_version')!r}"
        )
    raw_decisions = report.get("decisions")
    raw_candidates = report.get("candidates")
    if not isinstance(raw_decisions, list) or not isinstance(
        raw_candidates, list
    ):
        raise GraphValidationError(
            "module plan decisions/candidates must be arrays"
        )
    decisions = {}
    for index, decision in enumerate(raw_decisions):
        if not isinstance(decision, Mapping):
            raise GraphValidationError(
                f"decisions[{index}] must be an object"
            )
        node_id = decision.get("id")
        if not isinstance(node_id, str) or node_id in decisions:
            raise GraphValidationError(
                f"decisions[{index}] has an invalid or duplicate id"
            )
        decisions[node_id] = decision
    function_ids = {
        node.id
        for node in graph.nodes.values()
        if node.kind is EntityKind.FUNCTION
    }
    missing = sorted(function_ids.difference(decisions))
    extra = sorted(set(decisions).difference(function_ids))
    if missing or extra:
        raise GraphValidationError(
            "module plan function coverage differs from graph "
            f"(missing={len(missing)}, extra={len(extra)})"
        )

    candidates = {}
    for index, candidate in enumerate(raw_candidates):
        if not isinstance(candidate, Mapping):
            raise GraphValidationError(
                f"candidates[{index}] must be an object"
            )
        candidate_id = candidate.get("id")
        readiness = candidate.get("readiness")
        functions = candidate.get("functions")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or candidate_id in candidates
        ):
            raise GraphValidationError(
                f"candidates[{index}] has an invalid or duplicate id"
            )
        if readiness not in {"READY", "BLOCKED", "NEEDS_EVIDENCE"}:
            raise GraphValidationError(
                f"candidate {candidate_id} has invalid readiness "
                f"{readiness!r}"
            )
        if not isinstance(functions, list) or not all(
            isinstance(item, str) for item in functions
        ):
            raise GraphValidationError(
                f"candidate {candidate_id}.functions must be a string array"
            )
        candidates[candidate_id] = candidate
    return decisions, candidates


def _decision_category(
    decision: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
) -> str:
    disposition = decision.get("disposition")
    if disposition == "core":
        return "CORE"
    if disposition == "unknown":
        return "UNKNOWN"
    if disposition not in {"interface", "module", "dead"}:
        raise GraphValidationError(
            f"invalid function disposition {disposition!r}"
        )
    candidate_id = decision.get("candidate_id")
    candidate = candidates.get(candidate_id)
    if candidate is None:
        raise GraphValidationError(
            f"function decision references missing candidate {candidate_id!r}"
        )
    return _readiness_category(candidate["readiness"])


def _readiness_category(readiness: str) -> str:
    if readiness == "READY":
        return "READY"
    if readiness == "BLOCKED":
        return "BLOCKED"
    if readiness == "NEEDS_EVIDENCE":
        return "UNKNOWN"
    raise GraphValidationError(f"invalid candidate readiness {readiness!r}")


def _category_report(
    counts: Mapping[str, int],
    sizes: Mapping[str, int],
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
            "bytes": sizes.get(category, 0),
            "byte_ratio_percent": _percent(
                sizes.get(category, 0), byte_total
            ),
        }
        for category in CATEGORIES
    }


def _percent(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(numerator * 100.0 / denominator, 6)


def _load_json_object(
    path: str | Path,
    context: str,
) -> dict[str, Any]:
    artifact = Path(path)
    try:
        value = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphValidationError(
            f"cannot read {context} {artifact}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise GraphValidationError(f"{context} must be an object")
    return value

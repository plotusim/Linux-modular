#!/usr/bin/env python3
"""Merge LLVM fact streams, solve pointers, and emit a validated graph."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kernel_modularizer.facts import load_and_merge_fact_files  # noqa: E402
from kernel_modularizer.io import (  # noqa: E402
    write_json_atomic,
    write_reference_graph,
)
from kernel_modularizer.pointer_analysis import (  # noqa: E402
    solve_pointer_constraints,
)


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Solve whole-program Linux function-pointer constraints.",
    )
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--graph-output", required=True, type=Path)
    parser.add_argument("--points-to-output", required=True, type=Path)
    parser.add_argument(
        "--include-points-to",
        action="store_true",
        help=(
            "serialize every points-to set; omitted by default because a "
            "full kernel can require tens of gigabytes"
        ),
    )
    parser.add_argument(
        "--pointer-backend",
        choices=("auto", "python", "roaring", "hybrid"),
        default="auto",
        help=(
            "points-to set backend; auto uses compressed Roaring sets plus "
            "the field/ABI-sensitive fallback when the optional pyroaring "
            "package is installed"
        ),
    )
    parser.add_argument(
        "--max-points-to-set",
        type=int,
        default=4096,
        help=(
            "saturate a variable above this many targets and conservatively "
            "mark affected indirect calls unresolved; 0 requests an exact "
            "unbounded solve"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    if args.max_points_to_set < 0:
        raise SystemExit("--max-points-to-set must be non-negative")
    points_to_limit = (
        None if args.max_points_to_set == 0 else args.max_points_to_set
    )
    fact_files = sorted(args.facts.rglob("*.facts.jsonl"))
    if not fact_files:
        raise SystemExit(f"no fact streams found under {args.facts}")
    merge_started = time.monotonic()
    print(
        f"merge_start fact_streams={len(fact_files)}",
        file=sys.stderr,
        flush=True,
    )
    graph, program = load_and_merge_fact_files(fact_files)
    print(
        "merge_complete "
        f"seconds={time.monotonic() - merge_started:.3f} "
        f"nodes={len(graph.nodes)} edges={len(graph.edges)} "
        f"addresses={len(program.addresses)} "
        f"copies={len(program.copies)} loads={len(program.loads)} "
        f"stores={len(program.stores)} geps={len(program.geps)} "
        f"summaries={len(program.summaries)} calls={len(program.calls)}",
        file=sys.stderr,
        flush=True,
    )
    solve_started = time.monotonic()
    result = solve_pointer_constraints(
        program,
        retain_points_to=args.include_points_to,
        backend=args.pointer_backend,
        max_points_to_set=points_to_limit,
    )
    print(
        "solve_complete "
        f"seconds={time.monotonic() - solve_started:.3f} "
        f"points_to_variables={result.points_to_variables} "
        f"points_to_relations={result.points_to_relations} "
        f"max_points_to_set={result.max_points_to_set} "
        f"saturated_variables={result.saturated_variables} "
        f"saturated_calls={result.saturated_calls} "
        f"saturation_reasons={dict(result.saturation_reasons)} "
        f"global_memory_trigger={result.global_memory_trigger} "
        f"memory_families={len(result.saturated_memory_families)} "
        f"field_resolved={result.field_resolved_calls} "
        f"field_unresolved={result.field_unresolved_calls} "
        f"slice_resolved={result.field_slice_resolved_calls} "
        f"slice_nodes={result.field_slice_visited_nodes} "
        f"slice_budget_calls={result.field_slice_budget_calls} "
        f"field_fallback={result.field_fallback_calls} "
        f"unresolved={len(result.unresolved_calls)} "
        f"worklist_steps={result.iterations} solver={result.solver}",
        file=sys.stderr,
        flush=True,
    )
    result.add_call_edges(graph, program)
    graph.metadata.update(
        {
            "pointer_iterations": result.iterations,
            "pointer_solver": result.solver,
            "points_to_variables": result.points_to_variables,
            "points_to_relations": result.points_to_relations,
            "max_points_to_set": result.max_points_to_set,
            "points_to_limit": result.points_to_limit,
            "saturated_variables": result.saturated_variables,
            "saturated_calls": result.saturated_calls,
            "field_resolved_calls": result.field_resolved_calls,
            "field_unresolved_calls": result.field_unresolved_calls,
            "field_points_to_variables": (
                result.field_points_to_variables
            ),
            "field_points_to_relations": (
                result.field_points_to_relations
            ),
            "field_slice_resolved_calls": (
                result.field_slice_resolved_calls
            ),
            "field_slice_visited_nodes": (
                result.field_slice_visited_nodes
            ),
            "field_slice_budget_calls": (
                result.field_slice_budget_calls
            ),
            "field_fallback_calls": result.field_fallback_calls,
            "saturation_reasons": dict(result.saturation_reasons),
            "saturation_samples": [
                {"variable": variable, "reason": reason}
                for variable, reason in result.saturation_samples
            ],
            "global_memory_trigger": result.global_memory_trigger,
            "saturated_memory_families": list(
                result.saturated_memory_families
            ),
            "pointer_analysis_complete": (
                result.saturated_variables == 0
            ),
            "unresolved_calls": len(result.unresolved_calls),
            "fact_streams": len(fact_files),
        }
    )
    write_reference_graph(args.graph_output, graph)
    write_json_atomic(
        args.points_to_output,
        {
            "schema_version": 1,
            "solver": result.solver,
            "worklist_steps": result.iterations,
            "points_to_included": args.include_points_to,
            "points_to_variables": result.points_to_variables,
            "points_to_relations": result.points_to_relations,
            "max_points_to_set": result.max_points_to_set,
            "points_to_limit": result.points_to_limit,
            "saturated_variables": result.saturated_variables,
            "saturated_calls": result.saturated_calls,
            "field_resolved_calls": result.field_resolved_calls,
            "field_unresolved_calls": result.field_unresolved_calls,
            "field_points_to_variables": (
                result.field_points_to_variables
            ),
            "field_points_to_relations": (
                result.field_points_to_relations
            ),
            "field_slice_resolved_calls": (
                result.field_slice_resolved_calls
            ),
            "field_slice_visited_nodes": (
                result.field_slice_visited_nodes
            ),
            "field_slice_budget_calls": (
                result.field_slice_budget_calls
            ),
            "field_fallback_calls": result.field_fallback_calls,
            "saturation_reasons": dict(result.saturation_reasons),
            "saturation_samples": [
                {"variable": variable, "reason": reason}
                for variable, reason in result.saturation_samples
            ],
            "global_memory_trigger": result.global_memory_trigger,
            "saturated_memory_families": list(
                result.saturated_memory_families
            ),
            "analysis_complete": result.saturated_variables == 0,
            "unresolved_calls": sorted(result.unresolved_calls),
            "call_targets": {
                call_id: sorted(targets)
                for call_id, targets in result.call_targets.items()
            },
            "points_to": {
                variable: sorted(targets)
                for variable, targets in result.points_to.items()
            },
        },
    )
    print(
        f"graph nodes={len(graph.nodes)} edges={len(graph.edges)} "
        f"unresolved={len(result.unresolved_calls)} "
        f"iterations={result.iterations}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

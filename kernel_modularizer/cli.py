"""Command-line interface for graph validation and inspection."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
from typing import Optional, Sequence

from .backend import generate_module_bundle, load_backend_policy
from .boot_metrics import (
    BootResourcePolicy,
    compare_boot_resources,
)
from .boot_phase_stats import (
    render_boot_phase_markdown,
    summarize_boot_phase,
)
from .callback_tables import (
    analyze_callback_tables,
    load_callback_plan,
    prepare_callback_tables,
    render_callback_table_markdown,
)
from .candidate import prepare_candidate
from .embedded_bitcode import extract_embedded_bitcode_tree
from .errors import GraphValidationError
from .extraction import load_source_extraction
from .full_kernel_stats import (
    load_plan_report,
    load_successful_validations,
    read_linked_function_symbols,
    render_full_kernel_markdown,
    render_source_closure_markdown,
    summarize_full_kernel,
    summarize_source_closures,
)
from .io import (
    load_reference_graph,
    write_json_atomic,
    write_reference_graph,
    write_text_atomic,
)
from .model import EdgeKind
from .manifest import create_stage_manifest
from .integration import apply_bundle, rollback_transaction
from .linker_layout import (
    LinkerAlignmentSymbols,
    compare_linker_alignment,
    render_linker_alignment_markdown,
)
from .observations import (
    DEFAULT_LOADER_READY_MARKER,
    load_boot_observations,
    parse_ftrace_files,
)
from .planner import PlannerPolicy, plan_modules
from .policy import load_planner_policy
from .portfolio import prepare_callback_portfolio
from .qemu import load_qemu_scenario, run_qemu_scenario
from .qemu_benchmark import (
    run_qemu_ab_benchmark,
    run_qemu_benchmark,
)
from .reporting import (
    DEFAULT_ADDITIONAL_INTERFACE_STUB_BYTES,
    DEFAULT_INTERFACE_STUB_BYTES,
    module_plan_to_dict,
    render_markdown_report,
)
from .size_validation import evaluate_size_gate
from .symbol_sizes import enrich_graph_symbol_sizes


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kernel-modularizer",
        description=(
            "Analyze, generate and validate function-level Linux modules."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate-graph",
        help="validate a versioned reference graph and print a summary",
    )
    validate.add_argument("graph", help="path to reference-graph JSON")
    validate.add_argument(
        "--json",
        action="store_true",
        help="emit the validation summary as JSON",
    )
    plan = subparsers.add_parser(
        "plan",
        help="create an explainable function modularization plan",
    )
    plan.add_argument("graph", help="path to reference-graph JSON")
    plan.add_argument(
        "--policy",
        help="versioned planner-policy JSON; safe defaults are used if omitted",
    )
    plan.add_argument("--json-output", required=True)
    plan.add_argument("--markdown-output", required=True)
    plan.add_argument(
        "--fail-on-unknown",
        action="store_true",
        help="return a failure status if any function remains UNKNOWN",
    )
    plan.add_argument(
        "--manifest-output",
        help="optional content-addressed stage manifest",
    )
    plan.add_argument(
        "--observations",
        help="boot observations JSON produced by observe-boot",
    )
    plan.add_argument(
        "--interface-stub-bytes",
        type=int,
        default=DEFAULT_INTERFACE_STUB_BYTES,
        help=(
            "conservative resident cost estimate for the first lazy "
            "interface "
            f"(default: {DEFAULT_INTERFACE_STUB_BYTES})"
        ),
    )
    plan.add_argument(
        "--additional-interface-stub-bytes",
        type=int,
        default=DEFAULT_ADDITIONAL_INTERFACE_STUB_BYTES,
        help=(
            "amortized resident cost estimate for each interface after "
            f"the first (default: "
            f"{DEFAULT_ADDITIONAL_INTERFACE_STUB_BYTES})"
        ),
    )
    plan.add_argument(
        "--minimum-estimated-savings",
        type=int,
        default=4096,
        help="block candidates below this estimated resident-byte saving",
    )
    callback_rank = subparsers.add_parser(
        "rank-callback-tables",
        help=(
            "rank resident operations tables whose cold implementations "
            "can move behind lazy wrappers"
        ),
    )
    callback_rank.add_argument("graph")
    callback_rank.add_argument("plan")
    callback_rank.add_argument("--json-output", required=True)
    callback_rank.add_argument("--markdown-output", required=True)
    callback_rank.add_argument(
        "--minimum-release-savings",
        type=int,
        default=4096,
        help=(
            "estimated resident-byte threshold used to recommend "
            "complementary source-group portfolios (default: 4096)"
        ),
    )
    callback_portfolio = subparsers.add_parser(
        "prepare-callback-portfolio",
        help=(
            "batch-select callback source groups and generate isolated "
            "module bundles"
        ),
    )
    callback_portfolio.add_argument("graph")
    callback_portfolio.add_argument("plan")
    callback_portfolio.add_argument("--source-extractor", required=True)
    callback_portfolio.add_argument("--compile-database", required=True)
    callback_portfolio.add_argument("--kernel-root", required=True)
    callback_portfolio.add_argument("--output-directory", required=True)
    callback_portfolio.add_argument(
        "--minimum-estimated-net-bytes",
        type=int,
        default=0,
        help=(
            "include known-size source groups at or above this direct "
            "estimate; may be negative for aggressive exploration "
            "(default: 0)"
        ),
    )
    callback_portfolio.add_argument(
        "--exclude-unknown-size",
        action="store_false",
        dest="include_unknown_size",
        help=(
            "skip groups whose graph estimate is unknown; exploratory "
            "mode includes them by default"
        ),
    )
    callback_portfolio.set_defaults(include_unknown_size=True)
    callback_portfolio.add_argument(
        "--max-source-groups",
        type=int,
        default=0,
        help="maximum ranked groups to prepare; zero means all",
    )
    callback_portfolio.add_argument(
        "--only-source",
        action="append",
        default=[],
        help=(
            "prepare only this kernel-relative source group; repeat as "
            "needed"
        ),
    )
    callback_portfolio.add_argument(
        "--exclude-source",
        action="append",
        default=[],
        help="exclude this kernel-relative source group; repeat as needed",
    )
    callback_portfolio.add_argument(
        "--module-prefix", default="deferred_auto_cb"
    )
    callback_portfolio.add_argument(
        "--alias-prefix", default="lmautocb"
    )
    callback_portfolio.add_argument(
        "--integrate",
        action="store_true",
        help="transactionally apply every successfully generated bundle",
    )
    callback_portfolio.add_argument(
        "--integration-dry-run",
        action="store_true",
        help="verify integration without changing the kernel tree",
    )
    callback_portfolio.add_argument(
        "--no-resume",
        action="store_false",
        dest="resume",
        help="regenerate groups instead of reusing hash-verified results",
    )
    callback_portfolio.set_defaults(resume=True)
    callback_portfolio.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop after recording the first candidate-local failure",
    )
    callback_portfolio.add_argument(
        "--allow-modified-sources",
        action="store_true",
        help=(
            "allow a stale graph to operate on sources already rewritten "
            "by linux-modular; disabled by default"
        ),
    )
    callback_portfolio.add_argument(
        "--json-output",
        help="portfolio JSON path (default: OUTPUT/portfolio.json)",
    )
    callback_portfolio.add_argument(
        "--markdown-output",
        help="portfolio Markdown path (default: OUTPUT/portfolio.md)",
    )
    observe = subparsers.add_parser(
        "observe-boot",
        help="map ftrace function records to linkage-aware graph nodes",
    )
    observe.add_argument("graph")
    observe.add_argument("trace", nargs="+")
    observe.add_argument("--output", required=True)
    observe.add_argument(
        "--loader-ready-marker",
        default=DEFAULT_LOADER_READY_MARKER,
        help="trace_marker payload that proves demand loading is usable",
    )
    observe.add_argument(
        "--require-loader-ready-marker",
        action="store_true",
        help="reject every input trace that lacks the loader-ready marker",
    )
    generate = subparsers.add_parser(
        "generate-module",
        help="generate a checked source/Kbuild/Kconfig module bundle",
    )
    generate.add_argument(
        "extraction", help="SourceExtractor schema-v1 JSON"
    )
    generate.add_argument(
        "--policy", required=True, help="backend policy schema-v1 JSON"
    )
    generate.add_argument(
        "--output-directory", required=True, help="new or existing bundle root"
    )
    generate.add_argument(
        "--manifest-output",
        help="optional content-addressed stage manifest outside the bundle",
    )
    apply_command = subparsers.add_parser(
        "apply-bundle",
        help="transactionally integrate a generated bundle into a kernel tree",
    )
    apply_command.add_argument("bundle_directory")
    apply_command.add_argument("--kernel-root", required=True)
    apply_command.add_argument(
        "--dry-run",
        action="store_true",
        help="verify every input and report changes without writing",
    )
    rollback = subparsers.add_parser(
        "rollback-bundle",
        help="restore a previously applied bundle transaction",
    )
    rollback.add_argument("--kernel-root", required=True)
    rollback.add_argument("--transaction-id", required=True)
    qemu = subparsers.add_parser(
        "qemu-validate",
        help="run serial-console boot/autoload/unload regression steps",
    )
    qemu.add_argument("scenario")
    qemu.add_argument("--log-output", required=True)
    qemu.add_argument("--json-output", required=True)
    qemu_benchmark = subparsers.add_parser(
        "qemu-benchmark",
        help="repeat a QEMU scenario and capture startup resource samples",
    )
    qemu_benchmark.add_argument("scenario")
    qemu_benchmark.add_argument("--repeats", type=int, default=3)
    qemu_benchmark.add_argument("--log-directory", required=True)
    qemu_benchmark.add_argument("--json-output", required=True)
    qemu_ab = subparsers.add_parser(
        "qemu-ab-benchmark",
        help="run balanced adjacent baseline/modular QEMU startup pairs",
    )
    qemu_ab.add_argument("baseline_scenario")
    qemu_ab.add_argument("modular_scenario")
    qemu_ab.add_argument("--pairs", type=int, default=4)
    qemu_ab.add_argument("--log-directory", required=True)
    qemu_ab.add_argument(
        "--max-permanent-kernel-regression-kb",
        type=float,
        default=0,
    )
    qemu_ab.add_argument(
        "--max-ready-time-regression-seconds",
        type=float,
        default=0.25,
    )
    qemu_ab.add_argument(
        "--max-mem-available-regression-kb",
        type=float,
        default=128,
    )
    qemu_ab.add_argument(
        "--max-slab-regression-kb",
        type=float,
        default=64,
    )
    qemu_ab.add_argument("--json-output", required=True)
    boot_compare = subparsers.add_parser(
        "compare-boot",
        help="compare repeated baseline/modular startup resource logs",
    )
    boot_compare.add_argument(
        "--baseline-log", action="append", required=True
    )
    boot_compare.add_argument(
        "--modular-log", action="append", required=True
    )
    boot_compare.add_argument(
        "--max-permanent-kernel-regression-kb",
        type=float,
        default=0,
    )
    boot_compare.add_argument(
        "--max-ready-time-regression-seconds",
        type=float,
        default=0.25,
    )
    boot_compare.add_argument(
        "--max-mem-available-regression-kb",
        type=float,
        default=128,
    )
    boot_compare.add_argument(
        "--max-slab-regression-kb",
        type=float,
        default=64,
    )
    boot_compare.add_argument("--output", required=True)
    prepare = subparsers.add_parser(
        "prepare-candidate",
        help="extract one plan candidate and generate its module bundle",
    )
    prepare.add_argument("graph")
    prepare.add_argument("plan")
    prepare.add_argument("candidate_id")
    prepare.add_argument("--source-extractor", required=True)
    prepare.add_argument("--compile-database", required=True)
    prepare.add_argument("--kernel-root", required=True)
    prepare.add_argument("--module-name", required=True)
    prepare.add_argument("--output-directory", required=True)
    prepare.add_argument(
        "--failure-expressions",
        help="optional JSON object keyed by interface id or symbol",
    )
    prepare.add_argument(
        "--duplicate-function",
        action="append",
        default=[],
        help=(
            "audited static helper to copy into the module; repeat and use "
            "a graph node id when a symbol is ambiguous"
        ),
    )
    prepare.add_argument(
        "--resident-export",
        action="append",
        default=[],
        help=(
            "audited source function to keep resident and export; repeat "
            "and use a graph node id when a symbol is ambiguous"
        ),
    )
    prepare.add_argument(
        "--external-resident-export",
        action="append",
        default=[],
        help=(
            "audited existing resident symbol whose GPL export is added at "
            "the candidate interface source; repeat as needed"
        ),
    )
    prepare.add_argument(
        "--pack-resident-dependencies",
        action="store_true",
        help=(
            "expose audited source-local and external resident dependencies "
            "through one typed table instead of one ksymtab export per symbol"
        ),
    )
    prepare.add_argument(
        "--resident-inline-proxy",
        action="append",
        default=[],
        help=(
            "header-defined inline function whose resident address is "
            "passed through the packed dependency table; repeat as needed"
        ),
    )
    prepare.add_argument(
        "--initialize-packed-dependencies-at-load",
        action="store_true",
        help=(
            "export one resident fill function and initialize the packed "
            "dependency table in module memory during module_init"
        ),
    )
    prepare.add_argument(
        "--bind-packed-dependencies-on-first-use",
        action="store_true",
        help=(
            "keep the packed table in module memory and bind it through "
            "the operations table before the first deferred call"
        ),
    )
    prepare.add_argument(
        "--direct-resident-dependency",
        action="append",
        default=[],
        help=(
            "dependency that must remain a direct export because a macro "
            "or inline expansion hides at least one module reference"
        ),
    )
    prepare.add_argument(
        "--module-defines",
        help=(
            "optional JSON object of audited object/function-like macro "
            "declarators to single-line constant expressions"
        ),
    )
    prepare.add_argument(
        "--resident-ro-after-init-global",
        action="append",
        default=[],
        help=(
            "audited initialized const resident global to place in the "
            "kernel's post-init read-only region; repeat as needed"
        ),
    )
    prepare.add_argument(
        "--autoload-alias",
        help=(
            "optional compact modalias used by request_module; the "
            "generated module advertises the matching MODULE_ALIAS"
        ),
    )
    prepare.add_argument(
        "--expand-private-source-closure",
        action="store_true",
        help=(
            "recursively move same-translation-unit AST dependencies and "
            "use LLVM incoming references to retain shared frontier entities"
        ),
    )
    prepare.add_argument(
        "--merge-candidate",
        action="append",
        default=[],
        help=(
            "merge another READY, or LAZY_READY size-only "
            "NEEDS_EVIDENCE, candidate into the same generated module; "
            "repeat for a composite boundary"
        ),
    )
    prepare.add_argument(
        "--exclude-interface",
        action="append",
        default=[],
        help=(
            "keep one audited candidate interface resident while preparing "
            "the remaining lazy-load entries; repeat and use a graph node "
            "id when a symbol is ambiguous"
        ),
    )
    prepare.add_argument(
        "--promote-callback-table",
        action="append",
        default=[],
        help=(
            "reviewed resident function-pointer table whose same-source "
            "initializer callbacks become additional lazy interfaces; "
            "repeat and use --exclude-interface for unsafe fields"
        ),
    )
    prepare_table = subparsers.add_parser(
        "prepare-callback-table",
        help=(
            "generate a module directly from READY resident callback "
            "tables without requiring a syscall candidate"
        ),
    )
    prepare_table.add_argument("graph")
    prepare_table.add_argument("plan")
    prepare_table.add_argument(
        "table",
        nargs="+",
        help=(
            "table symbol or linkage-aware global id; multiple tables are "
            "packed into one module"
        ),
    )
    prepare_table.add_argument("--source-extractor", required=True)
    prepare_table.add_argument("--compile-database", required=True)
    prepare_table.add_argument("--kernel-root", required=True)
    prepare_table.add_argument("--module-name", required=True)
    prepare_table.add_argument("--output-directory", required=True)
    prepare_table.add_argument("--failure-expressions")
    prepare_table.add_argument(
        "--duplicate-function", action="append", default=[]
    )
    prepare_table.add_argument(
        "--resident-export", action="append", default=[]
    )
    prepare_table.add_argument(
        "--external-resident-export", action="append", default=[]
    )
    prepare_table.add_argument(
        "--resident-inline-proxy", action="append", default=[]
    )
    prepare_table.add_argument(
        "--pack-resident-dependencies", action="store_true"
    )
    prepare_table.add_argument(
        "--initialize-packed-dependencies-at-load", action="store_true"
    )
    prepare_table.add_argument(
        "--bind-packed-dependencies-on-first-use", action="store_true"
    )
    prepare_table.add_argument(
        "--direct-resident-dependency", action="append", default=[]
    )
    prepare_table.add_argument("--module-defines")
    prepare_table.add_argument(
        "--resident-ro-after-init-global", action="append", default=[]
    )
    prepare_table.add_argument("--autoload-alias")
    prepare_table.add_argument(
        "--no-expand-private-source-closure",
        action="store_false",
        dest="expand_private_source_closure",
        help=(
            "disable the default AST/LLVM private dependency closure "
            "(normally only useful for diagnostics)"
        ),
    )
    prepare_table.set_defaults(expand_private_source_closure=True)
    prepare_table.add_argument(
        "--exclude-interface", action="append", default=[]
    )
    linker_layout = subparsers.add_parser(
        "analyze-linker-alignment",
        help="explain when aligned linker islands absorb text savings",
    )
    linker_layout.add_argument("baseline_vmlinux")
    linker_layout.add_argument("resident_vmlinux")
    linker_layout.add_argument("--nm", default="nm")
    linker_layout.add_argument(
        "--alignment-bytes",
        type=int,
        default=2 * 1024 * 1024,
    )
    linker_layout.add_argument(
        "--payload-end-symbol",
        default="__kprobes_text_end",
    )
    linker_layout.add_argument(
        "--aligned-start-symbol",
        default="__entry_text_start",
    )
    linker_layout.add_argument(
        "--aligned-end-symbol",
        default="__entry_text_end",
    )
    linker_layout.add_argument(
        "--next-aligned-start-symbol",
        default="__softirqentry_text_start",
    )
    linker_layout.add_argument("--json-output", required=True)
    linker_layout.add_argument("--markdown-output", required=True)
    size_gate = subparsers.add_parser(
        "validate-size",
        help="gate on permanent allocatable ELF bytes removed from residents",
    )
    size_gate.add_argument(
        "--baseline-object",
        action="append",
        required=True,
        help="baseline built-in ELF object; repeat for multiple objects",
    )
    size_gate.add_argument(
        "--resident-object",
        action="append",
        required=True,
        help="post-extraction resident ELF object; repeat as needed",
    )
    size_gate.add_argument(
        "--module-object",
        action="append",
        default=[],
        help="generated .o or .ko for loaded-footprint reporting",
    )
    size_gate.add_argument(
        "--baseline-image",
        action="append",
        default=[],
        help="optional baseline image/file metric and image-gate input",
    )
    size_gate.add_argument(
        "--resident-image",
        action="append",
        default=[],
        help="optional modular image/file metric and image-gate input",
    )
    size_gate.add_argument(
        "--baseline-linked-kernel",
        help=(
            "optional baseline vmlinux; enables a zero-regression gate on "
            "Linux linker-boundary resident bytes"
        ),
    )
    size_gate.add_argument(
        "--resident-linked-kernel",
        help="modular vmlinux paired with --baseline-linked-kernel",
    )
    size_gate.add_argument(
        "--minimum-resident-savings",
        type=int,
        default=4096,
        help="minimum permanent resident bytes that must be removed",
    )
    size_gate.add_argument(
        "--maximum-image-regression",
        type=int,
        help="optional maximum compressed/image byte growth",
    )
    size_gate.add_argument(
        "--maximum-linked-kernel-regression",
        type=int,
        help=(
            "maximum final vmlinux permanent-byte growth; defaults to zero "
            "when a linked-kernel pair is supplied"
        ),
    )
    size_gate.add_argument(
        "--nm",
        default="nm",
        help="nm-compatible tool used for linked-kernel boundary symbols",
    )
    size_gate.add_argument("--output", required=True)
    bitcode = subparsers.add_parser(
        "extract-bitcode",
        help="collect embedded/sidecar bitcode from a Clang kernel build",
    )
    bitcode.add_argument("--object-root", required=True)
    bitcode.add_argument("--output-directory", required=True)
    bitcode.add_argument("--manifest-output", required=True)
    bitcode.add_argument("--jobs", type=int)
    bitcode.add_argument("--overwrite", action="store_true")
    bitcode.add_argument(
        "--link-input",
        action="append",
        default=[],
        help=(
            "restrict extraction to an actual linker .o/.a input; repeat "
            "for every KBUILD_VMLINUX_OBJS entry"
        ),
    )
    bitcode.add_argument(
        "--ar",
        default="ar",
        help="archive tool used to enumerate thin Kbuild archives",
    )
    sizes = subparsers.add_parser(
        "enrich-sizes",
        help="fill graph node sizes from matching configured-kernel objects",
    )
    sizes.add_argument("graph")
    sizes.add_argument("--facts-manifest", required=True)
    sizes.add_argument("--object-root", required=True)
    sizes.add_argument("--nm", default="nm")
    sizes.add_argument(
        "--objdump",
        default="objdump",
        help=(
            "objdump used to recover linker callback registrations from "
            "ELF relocation sections"
        ),
    )
    sizes.add_argument("--graph-output", required=True)
    sizes.add_argument("--report-output", required=True)
    full_stats = subparsers.add_parser(
        "summarize-full-kernel",
        help=(
            "reconcile the module plan with final vmlinux function symbols"
        ),
    )
    full_stats.add_argument("graph")
    full_stats.add_argument("--plan", required=True)
    full_stats.add_argument("--vmlinux", required=True)
    full_stats.add_argument("--nm", default="nm")
    full_stats.add_argument(
        "--successful-validations",
        action="append",
        default=[],
        help=(
            "optional schema-v1 candidate validation artifact; repeat to "
            "combine disjoint validated stages"
        ),
    )
    full_stats.add_argument("--json-output", required=True)
    full_stats.add_argument("--markdown-output", required=True)
    closure_stats = subparsers.add_parser(
        "summarize-source-closures",
        help=(
            "reconcile extracted source closures with baseline vmlinux "
            "function symbols"
        ),
    )
    closure_stats.add_argument("graph")
    closure_stats.add_argument("--plan", required=True)
    closure_stats.add_argument("--vmlinux", required=True)
    closure_stats.add_argument("--nm", default="nm")
    closure_stats.add_argument(
        "--source-closure",
        action="append",
        required=True,
        help=(
            "schema-v1 source-closure artifact; repeat for a portfolio"
        ),
    )
    closure_stats.add_argument("--json-output", required=True)
    closure_stats.add_argument("--markdown-output", required=True)
    boot_phase_stats = subparsers.add_parser(
        "summarize-boot-phase",
        help=(
            "classify final vmlinux functions only by loader-ready startup "
            "observations"
        ),
    )
    boot_phase_stats.add_argument("graph")
    boot_phase_stats.add_argument("--observations", required=True)
    boot_phase_stats.add_argument("--vmlinux", required=True)
    boot_phase_stats.add_argument("--nm", default="nm")
    boot_phase_stats.add_argument("--json-output", required=True)
    boot_phase_stats.add_argument("--markdown-output", required=True)
    return parser


def _validate_graph(path: str, *, json_output: bool) -> int:
    graph = load_reference_graph(path)
    edge_kinds = Counter(edge.kind.value for edge in graph.edges)
    unresolved = edge_kinds.get(EdgeKind.UNRESOLVED_CALL.value, 0)
    summary = {
        "valid": True,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "unresolved_calls": unresolved,
        "edge_kinds": dict(sorted(edge_kinds.items())),
        "metadata": graph.metadata,
    }
    if json_output:
        print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
    else:
        print(f"valid graph: {path}")
        print(f"nodes: {summary['nodes']}")
        print(f"edges: {summary['edges']}")
        print(f"unresolved calls: {unresolved}")
        for kind, count in summary["edge_kinds"].items():
            print(f"edge {kind}: {count}")
    return 0


def _load_optional_json_object(
    path: str | None, *, context: str
) -> dict[str, object]:
    if path is None:
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphValidationError(f"cannot read {context}: {error}") from error
    if not isinstance(value, dict):
        raise GraphValidationError(f"{context} root must be an object")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-graph":
            return _validate_graph(args.graph, json_output=args.json)
        if args.command == "plan":
            graph = load_reference_graph(args.graph)
            policy = (
                load_planner_policy(args.policy, graph)
                if args.policy
                else PlannerPolicy()
            )
            observations = None
            if args.observations:
                observations = load_boot_observations(
                    args.observations, graph
                )
                for node_id, contexts in observations.node_contexts.items():
                    node = graph.nodes[node_id]
                    phases = observations.node_phases.get(
                        node_id, frozenset()
                    )
                    graph.merge_node(
                        replace(
                            node,
                            attributes=node.attributes.union(
                                {
                                    f"observed_context={context.value}"
                                    for context in contexts
                                }
                                | {
                                    f"observed_phase={phase.value}"
                                    for phase in phases
                                }
                            ),
                        )
                    )
                policy = replace(
                    policy,
                    observed_boot_functions=(
                        policy.observed_boot_functions
                        | observations.observed_node_ids
                    ),
                    loader_ready_observed=(
                        observations.loader_ready_observed
                    ),
                    pre_loader_observed_functions=(
                        policy.pre_loader_observed_functions
                        | observations.pre_loader_node_ids
                    ),
                    post_loader_observed_functions=(
                        policy.post_loader_observed_functions
                        | observations.post_loader_node_ids
                    ),
                    unknown_phase_observed_functions=(
                        policy.unknown_phase_observed_functions
                        | observations.unknown_phase_node_ids
                    ),
                )
            if policy.enforce_loader_ready_phase and (
                observations is None
                or not observations.loader_ready_observed
            ):
                raise GraphValidationError(
                    "loader-phase enforcement requires --observations "
                    "whose every trace contains the module-loader-ready "
                    "marker"
                )
            plan = plan_modules(graph, policy)
            report = module_plan_to_dict(
                graph,
                policy,
                plan,
                interface_stub_bytes=args.interface_stub_bytes,
                additional_interface_stub_bytes=(
                    args.additional_interface_stub_bytes
                ),
                minimum_estimated_savings_bytes=(
                    args.minimum_estimated_savings
                ),
            )
            write_json_atomic(args.json_output, report)
            write_text_atomic(
                args.markdown_output,
                render_markdown_report(report),
            )
            if args.manifest_output:
                manifest_inputs = {"reference_graph": args.graph}
                if args.policy:
                    manifest_inputs["planner_policy"] = args.policy
                if args.observations:
                    manifest_inputs["boot_observations"] = (
                        args.observations
                    )
                manifest = create_stage_manifest(
                    "module-plan",
                    inputs=manifest_inputs,
                    parameters={
                        "fail_on_unknown": args.fail_on_unknown,
                        "interface_stub_bytes": args.interface_stub_bytes,
                        "additional_interface_stub_bytes": (
                            args.additional_interface_stub_bytes
                        ),
                        "minimum_estimated_savings": (
                            args.minimum_estimated_savings
                        ),
                    },
                    tools={"kernel_modularizer": "schema-1"},
                    outputs={
                        "plan_json": args.json_output,
                        "plan_markdown": args.markdown_output,
                    },
                )
                write_json_atomic(args.manifest_output, manifest)
            unknown = report["summary"]["dispositions"].get("unknown", 0)
            print(
                f"planned functions={report['summary']['functions']} "
                f"candidates={report['summary']['candidates']} "
                f"unknown={unknown}"
            )
            return 1 if args.fail_on_unknown and unknown else 0
        if args.command == "rank-callback-tables":
            graph = load_reference_graph(args.graph)
            callback_plan = load_callback_plan(args.plan)
            callback_report = analyze_callback_tables(
                graph,
                callback_plan,
                minimum_release_savings_bytes=(
                    args.minimum_release_savings
                ),
            )
            write_json_atomic(args.json_output, callback_report)
            write_text_atomic(
                args.markdown_output,
                render_callback_table_markdown(callback_report),
            )
            callback_summary = callback_report["summary"]
            print(
                "callback_tables="
                f"{callback_summary['recognized_callback_tables']} "
                f"ready={callback_summary['ready_callback_tables']} "
                "ready_positive="
                f"{callback_summary['ready_positive_direct_tables']} "
                "complementary_pairs="
                f"{callback_summary['estimated_complementary_pair_portfolios']} "
                "positive_direct_net_bytes="
                f"{callback_summary['positive_direct_net_bytes_without_source_closure']}"
            )
            return 0
        if args.command == "prepare-callback-portfolio":
            graph = load_reference_graph(args.graph)
            portfolio = prepare_callback_portfolio(
                graph,
                args.graph,
                args.plan,
                source_extractor=args.source_extractor,
                compilation_database=args.compile_database,
                kernel_root=args.kernel_root,
                output_directory=args.output_directory,
                minimum_estimated_net_bytes=(
                    args.minimum_estimated_net_bytes
                ),
                include_unknown_size=args.include_unknown_size,
                maximum_source_groups=args.max_source_groups,
                only_sources=args.only_source,
                excluded_sources=args.exclude_source,
                module_prefix=args.module_prefix,
                alias_prefix=args.alias_prefix,
                integrate=args.integrate,
                integration_dry_run=args.integration_dry_run,
                resume=args.resume,
                fail_fast=args.fail_fast,
                allow_modified_sources=args.allow_modified_sources,
                json_output=args.json_output,
                markdown_output=args.markdown_output,
            )
            summary = portfolio["summary"]
            print(
                "portfolio_selected="
                f"{summary['selected_candidates']} "
                f"prepared={summary['prepared_candidates']} "
                f"integrated={summary['integrated_candidates']} "
                f"failed={summary['failed_candidates']} "
                "integration_failed="
                f"{summary['integration_failed_candidates']} "
                "moved_definitions="
                f"{summary['moved_source_function_definitions']}"
            )
            return 1 if (
                summary["failed_candidates"]
                or summary["integration_failed_candidates"]
            ) else 0
        if args.command == "generate-module":
            extraction = load_source_extraction(args.extraction)
            policy = load_backend_policy(args.policy)
            bundle = generate_module_bundle(extraction, policy)
            bundle.write(args.output_directory)
            if args.manifest_output:
                bundle_manifest = (
                    Path(args.output_directory) / "bundle.json"
                )
                manifest = create_stage_manifest(
                    "module-generation",
                    inputs={
                        "source_extraction": args.extraction,
                        "backend_policy": args.policy,
                    },
                    parameters={
                        "module_name": policy.module_name,
                        "interfaces": list(policy.interfaces),
                    },
                    tools={
                        "kernel_modularizer_backend": (
                            f"schema-{bundle.manifest['schema_version']}"
                        )
                    },
                    outputs={"bundle_manifest": bundle_manifest},
                )
                write_json_atomic(args.manifest_output, manifest)
            print(
                f"generated module={bundle.module_object} "
                f"interfaces={len(policy.interfaces)} "
                f"files={len(bundle.files) + 1}"
            )
            return 0
        if args.command == "observe-boot":
            graph = load_reference_graph(args.graph)
            observations = parse_ftrace_files(
                graph,
                args.trace,
                loader_ready_marker=args.loader_ready_marker,
                require_loader_ready_marker=(
                    args.require_loader_ready_marker
                ),
            )
            write_json_atomic(args.output, observations.to_dict())
            print(
                f"observed_nodes={len(observations.node_counts)} "
                f"parsed_lines={observations.parsed_lines} "
                f"unmatched_symbols={len(observations.unmatched_symbols)} "
                f"loader_ready_traces="
                f"{sum(timestamp is not None for timestamp in observations.loader_ready_timestamps)}"
                f"/{len(observations.trace_sha256)}"
            )
            return 0
        if args.command == "summarize-full-kernel":
            graph = load_reference_graph(args.graph)
            report = load_plan_report(args.plan)
            linked_symbols = read_linked_function_symbols(
                args.vmlinux, nm=args.nm
            )
            validations = {}
            for validation_path in args.successful_validations:
                loaded = load_successful_validations(validation_path)
                duplicate_candidates = sorted(
                    set(validations).intersection(loaded)
                )
                if duplicate_candidates:
                    raise GraphValidationError(
                        "successful validation candidate is repeated "
                        "across artifacts: "
                        + ", ".join(duplicate_candidates)
                    )
                validations.update(loaded)
            accounting = summarize_full_kernel(
                graph,
                report,
                linked_symbols,
                successful_candidates=validations,
                successful_function_ids={
                    function_id
                    for function_ids in validations.values()
                    for function_id in function_ids
                },
            )
            write_json_atomic(args.json_output, accounting)
            write_text_atomic(
                args.markdown_output,
                render_full_kernel_markdown(accounting),
            )
            categories = accounting["summary"]["categories"]
            print(
                f"linked_functions="
                f"{accounting['summary']['linked_function_symbols']} "
                f"core={categories['CORE']['functions']} "
                f"ready={categories['READY']['functions']} "
                f"blocked={categories['BLOCKED']['functions']} "
                f"unknown={categories['UNKNOWN']['functions']} "
                f"successful="
                f"{accounting['summary']['successful_modularized_functions']}"
            )
            return 0
        if args.command == "summarize-source-closures":
            graph = load_reference_graph(args.graph)
            report = load_plan_report(args.plan)
            linked_symbols = read_linked_function_symbols(
                args.vmlinux, nm=args.nm
            )
            closures = []
            for closure_path in args.source_closure:
                path = Path(closure_path)
                try:
                    closure = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise GraphValidationError(
                        f"cannot read source closure {path}: {error}"
                    ) from error
                if not isinstance(closure, dict):
                    raise GraphValidationError(
                        f"source closure {path} must be an object"
                    )
                closures.append((str(path), closure))
            accounting = summarize_source_closures(
                graph,
                report,
                linked_symbols,
                closures,
            )
            write_json_atomic(args.json_output, accounting)
            write_text_atomic(
                args.markdown_output,
                render_source_closure_markdown(accounting),
            )
            summary = accounting["summary"]
            print(
                f"source_closures={summary['source_closures']} "
                f"moved_definitions="
                f"{summary['moved_source_function_definitions']} "
                f"matched_final_functions="
                f"{summary['matched_final_elf_functions']} "
                f"matched_bytes={summary['matched_baseline_bytes']}"
            )
            return 0
        if args.command == "summarize-boot-phase":
            graph = load_reference_graph(args.graph)
            observations = load_boot_observations(
                args.observations, graph
            )
            linked_symbols = read_linked_function_symbols(
                args.vmlinux, nm=args.nm
            )
            accounting = summarize_boot_phase(
                graph, observations, linked_symbols
            )
            write_json_atomic(args.json_output, accounting)
            write_text_atomic(
                args.markdown_output,
                render_boot_phase_markdown(accounting),
            )
            categories = accounting["summary"]["categories"]
            print(
                f"linked_functions="
                f"{accounting['summary']['linked_function_symbols']} "
                f"boot_hot={categories['BOOT_HOT']['functions']} "
                f"post_boot_only="
                f"{categories['POST_BOOT_ONLY']['functions']} "
                f"boot_cold={categories['BOOT_COLD']['functions']} "
                f"unknown={categories['UNKNOWN']['functions']} "
                f"startup_defer_upper_bound="
                f"{accounting['summary']['startup_defer_upper_bound_functions']}"
            )
            return 0
        if args.command == "apply-bundle":
            result = apply_bundle(
                args.bundle_directory,
                args.kernel_root,
                dry_run=args.dry_run,
            )
            print(
                f"transaction={result.transaction_id} "
                f"changed={len(result.changed_paths)} "
                f"unchanged={len(result.already_applied_paths)} "
                f"dry_run={str(result.dry_run).lower()}"
            )
            return 0
        if args.command == "rollback-bundle":
            restored = rollback_transaction(
                args.kernel_root, args.transaction_id
            )
            print(
                f"transaction={args.transaction_id} "
                f"restored={len(restored)}"
            )
            return 0
        if args.command == "qemu-validate":
            scenario = load_qemu_scenario(args.scenario)
            result = run_qemu_scenario(
                scenario, log_path=args.log_output
            )
            write_json_atomic(args.json_output, result.to_dict())
            print(
                f"qemu_passed={str(result.passed).lower()} "
                f"steps={result.completed_steps} "
                f"elapsed={result.elapsed_seconds:.3f}s"
            )
            return 0
        if args.command == "qemu-benchmark":
            scenario = load_qemu_scenario(args.scenario)
            benchmark = run_qemu_benchmark(
                scenario,
                repeats=args.repeats,
                log_directory=args.log_directory,
            )
            payload = benchmark.to_dict()
            write_json_atomic(args.json_output, payload)
            print(
                f"qemu_benchmark_passed="
                f"{str(payload['passed']).lower()} "
                f"repeats={payload['repeats']} "
                f"median_ready_seconds="
                f"{payload['median']['ready_uptime_seconds']:.3f}"
            )
            return 0 if payload["passed"] else 1
        if args.command == "qemu-ab-benchmark":
            benchmark = run_qemu_ab_benchmark(
                load_qemu_scenario(args.baseline_scenario),
                load_qemu_scenario(args.modular_scenario),
                pairs=args.pairs,
                log_directory=args.log_directory,
                policy=BootResourcePolicy(
                    max_permanent_kernel_regression_kb=(
                        args.max_permanent_kernel_regression_kb
                    ),
                    max_ready_time_regression_seconds=(
                        args.max_ready_time_regression_seconds
                    ),
                    max_mem_available_regression_kb=(
                        args.max_mem_available_regression_kb
                    ),
                    max_slab_regression_kb=(
                        args.max_slab_regression_kb
                    ),
                ),
            )
            payload = benchmark.to_dict()
            write_json_atomic(args.json_output, payload)
            deltas = payload["comparison"][
                "deltas_modular_minus_baseline"
            ]
            print(
                f"qemu_ab_passed={str(payload['passed']).lower()} "
                f"pairs={payload['pairs']} "
                f"permanent_kernel_delta_kb="
                f"{deltas['permanent_kernel_kb']:.3f} "
                f"ready_time_delta_seconds="
                f"{deltas['ready_uptime_seconds']:.3f}"
            )
            return 0 if payload["passed"] else 1
        if args.command == "compare-boot":
            comparison = compare_boot_resources(
                args.baseline_log,
                args.modular_log,
                policy=BootResourcePolicy(
                    max_permanent_kernel_regression_kb=(
                        args.max_permanent_kernel_regression_kb
                    ),
                    max_ready_time_regression_seconds=(
                        args.max_ready_time_regression_seconds
                    ),
                    max_mem_available_regression_kb=(
                        args.max_mem_available_regression_kb
                    ),
                    max_slab_regression_kb=(
                        args.max_slab_regression_kb
                    ),
                ),
            )
            write_json_atomic(args.output, comparison.to_dict())
            print(
                f"boot_gate_passed={str(comparison.passed).lower()} "
                f"permanent_kernel_delta_kb="
                f"{comparison.deltas['permanent_kernel_kb']:.3f} "
                f"ready_time_delta_seconds="
                f"{comparison.deltas['ready_uptime_seconds']:.3f}"
            )
            return 0 if comparison.passed else 1
        if args.command == "prepare-callback-table":
            output = Path(args.output_directory)
            failure_expressions = _load_optional_json_object(
                args.failure_expressions, context="failure expressions"
            )
            module_defines = _load_optional_json_object(
                args.module_defines, context="module defines"
            )
            extraction_path = output / "source-extraction.json"
            backend_policy_path = output / "backend-policy.json"
            discovery_path = output / "callback-table-discovery.json"
            graph = load_reference_graph(args.graph)
            selection = prepare_callback_tables(
                graph,
                args.plan,
                args.table,
                source_extractor=args.source_extractor,
                compilation_database=args.compile_database,
                kernel_root=args.kernel_root,
                module_name=args.module_name,
                extraction_output=extraction_path,
                backend_policy_output=backend_policy_path,
                discovery_output=discovery_path,
                failure_expressions=failure_expressions,
                duplicate_functions=args.duplicate_function,
                resident_exports=args.resident_export,
                external_resident_exports=(
                    args.external_resident_export
                ),
                resident_inline_proxies=args.resident_inline_proxy,
                pack_resident_dependencies=(
                    args.pack_resident_dependencies
                ),
                initialize_packed_dependencies_at_load=(
                    args.initialize_packed_dependencies_at_load
                ),
                bind_packed_dependencies_on_first_use=(
                    args.bind_packed_dependencies_on_first_use
                ),
                direct_resident_dependencies=(
                    args.direct_resident_dependency
                ),
                module_defines=module_defines,
                resident_ro_after_init_globals=(
                    args.resident_ro_after_init_global
                ),
                autoload_alias=args.autoload_alias,
                expand_private_source_closure=(
                    args.expand_private_source_closure
                ),
                source_closure_output=output / "source-closure.json",
                excluded_interfaces=args.exclude_interface,
            )
            extraction = load_source_extraction(extraction_path)
            backend_policy = load_backend_policy(backend_policy_path)
            bundle = generate_module_bundle(extraction, backend_policy)
            bundle.write(output / "bundle")
            manifest = create_stage_manifest(
                "callback-table-preparation",
                inputs={
                    "reference_graph": args.graph,
                    "module_plan": args.plan,
                    "compile_database": (
                        Path(args.compile_database)
                        / "compile_commands.json"
                        if Path(args.compile_database).is_dir()
                        else args.compile_database
                    ),
                },
                parameters={
                    "candidate_id": selection["candidate_id"],
                    "callback_tables": selection["table_ids"],
                    "module_name": args.module_name,
                    "interfaces": selection["interface_ids"],
                    "resident_interfaces": selection[
                        "resident_interface_ids"
                    ],
                    "duplicate_functions": args.duplicate_function,
                    "resident_exports": args.resident_export,
                    "external_resident_exports": (
                        args.external_resident_export
                    ),
                    "resident_inline_proxies": (
                        args.resident_inline_proxy
                    ),
                    "pack_resident_dependencies": (
                        args.pack_resident_dependencies
                    ),
                    "initialize_packed_dependencies_at_load": (
                        args.initialize_packed_dependencies_at_load
                    ),
                    "bind_packed_dependencies_on_first_use": (
                        args.bind_packed_dependencies_on_first_use
                    ),
                    "direct_resident_dependencies": (
                        args.direct_resident_dependency
                    ),
                    "module_defines": module_defines,
                    "resident_ro_after_init_globals": (
                        args.resident_ro_after_init_global
                    ),
                    "autoload_alias": args.autoload_alias,
                    "expand_private_source_closure": (
                        args.expand_private_source_closure
                    ),
                    "excluded_interfaces": args.exclude_interface,
                },
                tools={"SourceExtractor": str(args.source_extractor)},
                outputs={
                    "callback_table_discovery": discovery_path,
                    "source_extraction": extraction_path,
                    "backend_policy": backend_policy_path,
                    "bundle_manifest": output / "bundle/bundle.json",
                    **(
                        {
                            "source_closure": output
                            / "source-closure.json"
                        }
                        if args.expand_private_source_closure
                        else {}
                    ),
                },
            )
            write_json_atomic(output / "manifest.json", manifest)
            print(
                "prepared callback_tables="
                f"{','.join(selection['table_symbols'])} "
                f"module={bundle.module_object} "
                f"interfaces={len(backend_policy.interfaces)}"
            )
            return 0
        if args.command == "prepare-candidate":
            output = Path(args.output_directory)
            failure_expressions = {}
            if args.failure_expressions:
                try:
                    failure_expressions = json.loads(
                        Path(args.failure_expressions).read_text(
                            encoding="utf-8"
                        )
                    )
                except (OSError, json.JSONDecodeError) as error:
                    raise GraphValidationError(
                        f"cannot read failure expressions: {error}"
                    ) from error
                if not isinstance(failure_expressions, dict):
                    raise GraphValidationError(
                        "failure expressions root must be an object"
                    )
            module_defines = {}
            if args.module_defines:
                try:
                    module_defines = json.loads(
                        Path(args.module_defines).read_text(
                            encoding="utf-8"
                        )
                    )
                except (OSError, json.JSONDecodeError) as error:
                    raise GraphValidationError(
                        f"cannot read module defines: {error}"
                    ) from error
                if not isinstance(module_defines, dict):
                    raise GraphValidationError(
                        "module defines root must be an object"
                    )
            extraction_path = output / "source-extraction.json"
            backend_policy_path = output / "backend-policy.json"
            graph = load_reference_graph(args.graph)
            prepare_candidate(
                graph,
                args.plan,
                args.candidate_id,
                source_extractor=args.source_extractor,
                compilation_database=args.compile_database,
                kernel_root=args.kernel_root,
                module_name=args.module_name,
                extraction_output=extraction_path,
                backend_policy_output=backend_policy_path,
                failure_expressions=failure_expressions,
                duplicate_functions=args.duplicate_function,
                resident_exports=args.resident_export,
                external_resident_exports=(
                    args.external_resident_export
                ),
                resident_inline_proxies=args.resident_inline_proxy,
                pack_resident_dependencies=(
                    args.pack_resident_dependencies
                ),
                initialize_packed_dependencies_at_load=(
                    args.initialize_packed_dependencies_at_load
                ),
                bind_packed_dependencies_on_first_use=(
                    args.bind_packed_dependencies_on_first_use
                ),
                direct_resident_dependencies=(
                    args.direct_resident_dependency
                ),
                module_defines=module_defines,
                resident_ro_after_init_globals=(
                    args.resident_ro_after_init_global
                ),
                autoload_alias=args.autoload_alias,
                expand_private_source_closure=(
                    args.expand_private_source_closure
                ),
                source_closure_output=output / "source-closure.json",
                merged_candidate_ids=args.merge_candidate,
                excluded_interfaces=args.exclude_interface,
                promoted_callback_tables=args.promote_callback_table,
            )
            extraction = load_source_extraction(extraction_path)
            backend_policy = load_backend_policy(backend_policy_path)
            bundle = generate_module_bundle(
                extraction, backend_policy
            )
            bundle.write(output / "bundle")
            manifest = create_stage_manifest(
                "candidate-preparation",
                inputs={
                    "reference_graph": args.graph,
                    "module_plan": args.plan,
                    "compile_database": (
                        Path(args.compile_database)
                        / "compile_commands.json"
                        if Path(args.compile_database).is_dir()
                        else args.compile_database
                    ),
                },
                parameters={
                    "candidate_id": args.candidate_id,
                    "module_name": args.module_name,
                    "duplicate_functions": args.duplicate_function,
                    "resident_exports": args.resident_export,
                    "external_resident_exports": (
                        args.external_resident_export
                    ),
                    "resident_inline_proxies": (
                        args.resident_inline_proxy
                    ),
                    "pack_resident_dependencies": (
                        args.pack_resident_dependencies
                    ),
                    "initialize_packed_dependencies_at_load": (
                        args.initialize_packed_dependencies_at_load
                    ),
                    "bind_packed_dependencies_on_first_use": (
                        args.bind_packed_dependencies_on_first_use
                    ),
                    "direct_resident_dependencies": (
                        args.direct_resident_dependency
                    ),
                    "module_defines": module_defines,
                    "resident_ro_after_init_globals": (
                        args.resident_ro_after_init_global
                    ),
                    "autoload_alias": args.autoload_alias,
                    "expand_private_source_closure": (
                        args.expand_private_source_closure
                    ),
                    "merged_candidate_ids": args.merge_candidate,
                    "excluded_interfaces": args.exclude_interface,
                    "promoted_callback_tables": (
                        args.promote_callback_table
                    ),
                },
                tools={"SourceExtractor": str(args.source_extractor)},
                outputs={
                    "source_extraction": extraction_path,
                    "backend_policy": backend_policy_path,
                    "bundle_manifest": output / "bundle/bundle.json",
                    **(
                        {
                            "source_closure": output
                            / "source-closure.json"
                        }
                        if args.expand_private_source_closure
                        else {}
                    ),
                },
            )
            write_json_atomic(output / "manifest.json", manifest)
            print(
                f"prepared candidate={args.candidate_id} "
                f"module={bundle.module_object} "
                f"interfaces={len(backend_policy.interfaces)}"
            )
            return 0
        if args.command == "validate-size":
            result = evaluate_size_gate(
                baseline_objects=args.baseline_object,
                resident_objects=args.resident_object,
                module_objects=args.module_object,
                minimum_resident_savings_bytes=(
                    args.minimum_resident_savings
                ),
                maximum_image_regression_bytes=(
                    args.maximum_image_regression
                ),
                baseline_images=args.baseline_image,
                resident_images=args.resident_image,
                baseline_linked_kernel=args.baseline_linked_kernel,
                resident_linked_kernel=args.resident_linked_kernel,
                maximum_linked_kernel_regression_bytes=(
                    args.maximum_linked_kernel_regression
                ),
                nm=args.nm,
            )
            write_json_atomic(args.output, result.to_dict())
            print(
                f"size_gate_passed={str(result.passed).lower()} "
                f"resident_savings="
                f"{result.unloaded_resident_savings_bytes} "
                f"loaded_module={result.loaded_module_bytes} "
                f"image_delta={result.image_delta_bytes} "
                f"linked_kernel_delta="
                f"{result.linked_kernel_delta_bytes}"
            )
            return 0 if result.passed else 1
        if args.command == "analyze-linker-alignment":
            symbols = LinkerAlignmentSymbols(
                payload_end=args.payload_end_symbol,
                aligned_start=args.aligned_start_symbol,
                aligned_end=args.aligned_end_symbol,
                next_aligned_start=args.next_aligned_start_symbol,
            )
            report = compare_linker_alignment(
                args.baseline_vmlinux,
                args.resident_vmlinux,
                nm=args.nm,
                alignment_bytes=args.alignment_bytes,
                symbols=symbols,
            )
            write_json_atomic(args.json_output, report)
            write_text_atomic(
                args.markdown_output,
                render_linker_alignment_markdown(report),
            )
            comparison = report["comparison"]
            print(
                "linker_alignment_payload_delta_bytes="
                f"{comparison['payload_before_alignment_delta_bytes']} "
                "final_text_delta_bytes="
                f"{comparison['final_text_range_delta_bytes']} "
                "remaining_to_boundary_bytes="
                f"{comparison['additional_payload_reduction_to_previous_boundary_bytes']}"
            )
            return 0
        if args.command == "extract-bitcode":
            result = extract_embedded_bitcode_tree(
                args.object_root,
                args.output_directory,
                jobs=args.jobs,
                overwrite=args.overwrite,
                link_inputs=args.link_input,
                archive_tool=args.ar,
            )
            write_json_atomic(args.manifest_output, result.to_dict())
            print(
                f"bitcode_extracted={len(result.entries)} "
                f"objects_scanned={result.scanned_objects} "
                f"skipped={result.skipped_objects}"
            )
            return 0
        if args.command == "enrich-sizes":
            graph = load_reference_graph(args.graph)
            result = enrich_graph_symbol_sizes(
                graph,
                args.facts_manifest,
                args.object_root,
                nm=args.nm,
                objdump=args.objdump,
            )
            write_reference_graph(args.graph_output, result.graph)
            write_json_atomic(args.report_output, result.report())
            print(
                f"sizes_matched={len(result.matched)} "
                f"exports_matched={len(result.exported_nodes)} "
                f"linker_registered="
                f"{len(result.linker_registered_nodes)} "
                f"nodes_missing={len(result.missing_nodes)} "
                f"objects_missing={len(result.missing_objects)}"
            )
            return 0
    except GraphValidationError as error:
        parser.exit(2, f"error: {error}\n")
    parser.error(f"unknown command {args.command!r}")
    return 2

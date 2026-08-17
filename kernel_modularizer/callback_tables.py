"""Discover resident callback tables that can front lazy implementations.

The ordinary module planner treats a function referenced by a permanent
operations table as CORE.  That is correct for the original address, but it
does not account for the backend's resident-wrapper transformation: the
table can keep the same address while only the implementation behind that
address moves to a module.  This module finds those boundaries without
turning every address-taken function into an unsafe module candidate.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from .errors import GraphValidationError
from .io import write_json_atomic
from .model import EdgeKind, EntityKind, ReferenceGraph, ReferenceNode
from .reporting import (
    DEFAULT_ADDITIONAL_INTERFACE_STUB_BYTES,
    DEFAULT_INTERFACE_STUB_BYTES,
)


CALLBACK_TABLE_REPORT_SCHEMA_VERSION = 1
DEFAULT_CALLBACK_RELEASE_SAVINGS_BYTES = 4096

_LAYOUT_SHAPE = re.compile(
    r"^(layout-shape:[^|]+\|[0-9]+)\|([0-9]+)$"
)
_TABLE_NAME_PATTERNS = {
    "file_operations": re.compile(
        r"(?:fops|file_ops|file_operations)$"
    ),
    "proc_ops": re.compile(r"proc_ops$"),
    "seq_operations": re.compile(r"seq_ops$"),
    "proto_ops": re.compile(r"proto_ops$"),
}

# Fields whose wrapper is entered from a normal VFS syscall and does not
# establish or tear down a persistent object lifetime.  Offsets come from
# LLVM's configured struct layout, rather than assuming source declaration
# order for randomized-layout builds.
_FILE_OPERATION_FIELDS = {
    8: ("llseek", True),
    16: ("read", True),
    24: ("write", True),
    32: ("read_iter", True),
    40: ("write_iter", True),
    48: ("iopoll", False),
    56: ("iterate", False),
    64: ("iterate_shared", False),
    72: ("poll", True),
    80: ("unlocked_ioctl", True),
    88: ("compat_ioctl", True),
    96: ("mmap", False),
    112: ("open", False),
    120: ("flush", False),
    128: ("release", False),
    136: ("fsync", True),
    144: ("fasync", True),
    152: ("lock", False),
    160: ("sendpage", True),
    168: ("get_unmapped_area", False),
    176: ("check_flags", True),
    184: ("flock", False),
    192: ("splice_write", True),
    200: ("splice_read", True),
    208: ("setlease", False),
    216: ("fallocate", True),
    224: ("show_fdinfo", True),
    232: ("copy_file_range", True),
    240: ("remap_file_range", True),
    248: ("fadvise", True),
}
_PROC_OPERATION_FIELDS = {
    8: ("proc_open", False),
    16: ("proc_read", True),
    24: ("proc_read_iter", True),
    32: ("proc_write", True),
    40: ("proc_lseek", True),
    48: ("proc_release", False),
    56: ("proc_poll", True),
    64: ("proc_ioctl", True),
    72: ("proc_compat_ioctl", True),
    80: ("proc_mmap", False),
    88: ("proc_get_unmapped_area", False),
}
_AUTO_FIELD_MAPS = {
    "file_operations": _FILE_OPERATION_FIELDS,
    "proc_ops": _PROC_OPERATION_FIELDS,
}
_LIFETIME_NAME = re.compile(
    r"(?:^|_)(?:open|release|close|destroy|free|exit|remove|shutdown|"
    r"fault|flush|evict|kill|mmap|unmap)(?:_|$)"
)

# On jump-label architectures, these macros eventually feed the static-key
# address to an inline-assembly immediate operand.  Rewriting a resident key
# as ``deps->key`` makes that address a run-time value and the module no longer
# compiles (x86 reports an invalid ``i`` constraint).  Keep the affected
# implementation resident until the backend can synthesize a dedicated
# resident boolean proxy for it.
_LINK_TIME_ADDRESS_MACROS = frozenset(
    {
        "static_branch_likely",
        "static_branch_unlikely",
    }
)


def load_callback_plan(path: str | Path) -> Mapping[str, Any]:
    """Load a module-plan report used as boot and disposition evidence."""

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphValidationError(
            f"cannot read module plan {path}: {error}"
        ) from error
    if not isinstance(value, Mapping):
        raise GraphValidationError("module plan root must be an object")
    return value


def analyze_callback_tables(
    graph: ReferenceGraph,
    plan: Mapping[str, Any],
    *,
    first_interface_stub_bytes: int | None = None,
    additional_interface_stub_bytes: int | None = None,
    minimum_release_savings_bytes: int = (
        DEFAULT_CALLBACK_RELEASE_SAVINGS_BYTES
    ),
) -> dict[str, Any]:
    """Rank callback-table implementation splits across the whole graph."""

    graph.validate()
    policy = plan.get("policy")
    decisions_raw = plan.get("decisions")
    summary = plan.get("summary", {})
    if not isinstance(policy, Mapping):
        raise GraphValidationError("module plan.policy must be an object")
    if not isinstance(decisions_raw, list):
        raise GraphValidationError("module plan.decisions must be an array")
    if not isinstance(summary, Mapping):
        raise GraphValidationError("module plan.summary must be an object")

    first_stub = _nonnegative_integer(
        first_interface_stub_bytes,
        summary.get(
            "interface_stub_bytes", DEFAULT_INTERFACE_STUB_BYTES
        ),
        "first interface stub bytes",
    )
    additional_stub = _nonnegative_integer(
        additional_interface_stub_bytes,
        summary.get(
            "additional_interface_stub_bytes",
            DEFAULT_ADDITIONAL_INTERFACE_STUB_BYTES,
        ),
        "additional interface stub bytes",
    )
    release_threshold = _nonnegative_integer(
        minimum_release_savings_bytes,
        DEFAULT_CALLBACK_RELEASE_SAVINGS_BYTES,
        "minimum callback release savings bytes",
    )
    evidence = _boot_evidence(policy)
    decisions = {
        item["id"]: item
        for item in decisions_raw
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }

    table_facts, raw_initializer_edges = _collect_table_facts(graph)
    family_shapes = _infer_family_shapes(table_facts, graph)
    opportunities = [
        _table_opportunity(
            facts,
            graph=graph,
            family_shapes=family_shapes,
            policy=policy,
            boot_evidence=evidence,
            decisions=decisions,
            first_stub=first_stub,
            additional_stub=additional_stub,
        )
        for facts in table_facts
    ]
    recognized = [item for item in opportunities if item["family"]]
    opportunities = sorted(
        recognized,
        key=lambda item: (
            item["status"] == "READY",
            item["estimated_direct_net_bytes"]
            if item["estimated_direct_net_bytes"] is not None
            else -1,
            item["known_interface_bytes"],
            item["table_id"],
        ),
        reverse=True,
    )
    source_groups = _source_groups(
        opportunities,
        graph=graph,
        first_stub=first_stub,
        additional_stub=additional_stub,
    )
    release_portfolios = _release_portfolios(
        source_groups, minimum_savings_bytes=release_threshold
    )
    status_counts = Counter(item["status"] for item in opportunities)
    unique_initializer_targets = sum(
        len(facts["local_targets"]) for facts in table_facts
    )
    return {
        "schema_version": CALLBACK_TABLE_REPORT_SCHEMA_VERSION,
        "analysis": "resident-callback-table-implementation-split",
        "boot_evidence": evidence,
        "stub_cost_model": {
            "first_interface_bytes": first_stub,
            "additional_interface_bytes": additional_stub,
        },
        "family_layout_shapes": dict(sorted(family_shapes.items())),
        "summary": {
            "global_objects_with_local_function_initializers": len(
                table_facts
            ),
            "raw_local_initializer_edges": raw_initializer_edges,
            "unique_local_initializer_targets": unique_initializer_targets,
            "duplicate_initializer_evidence_edges_removed": (
                raw_initializer_edges - unique_initializer_targets
            ),
            "recognized_callback_tables": len(opportunities),
            "ready_callback_tables": status_counts.get("READY", 0),
            "ready_positive_direct_tables": sum(
                item["status"] == "READY"
                and item["estimated_direct_net_bytes"] is not None
                and item["estimated_direct_net_bytes"] > 0
                for item in opportunities
            ),
            "ready_source_groups": len(source_groups),
            "release_savings_threshold_bytes": release_threshold,
            "estimated_standalone_release_groups": sum(
                item["estimated_direct_net_bytes"] is not None
                and item["estimated_direct_net_bytes"] >= release_threshold
                for item in source_groups
            ),
            "estimated_complementary_pair_portfolios": len(
                release_portfolios
            ),
            "status_counts": dict(sorted(status_counts.items())),
            "positive_direct_net_bytes_without_source_closure": sum(
                max(item["estimated_direct_net_bytes"] or 0, 0)
                for item in opportunities
                if item["status"] == "READY"
            ),
        },
        "release_portfolios": release_portfolios,
        "source_groups": source_groups,
        "tables": opportunities,
    }


def prepare_callback_tables(
    graph: ReferenceGraph,
    plan_path: str | Path,
    table_selectors: Sequence[str],
    *,
    source_extractor: str | Path,
    compilation_database: str | Path,
    kernel_root: str | Path,
    module_name: str,
    extraction_output: str | Path,
    backend_policy_output: str | Path,
    discovery_output: str | Path | None = None,
    failure_expressions: Mapping[str, str] | None = None,
    duplicate_functions: Sequence[str] = (),
    resident_exports: Sequence[str] = (),
    external_resident_exports: Sequence[str] = (),
    resident_inline_proxies: Sequence[str] = (),
    pack_resident_dependencies: bool = False,
    initialize_packed_dependencies_at_load: bool = False,
    bind_packed_dependencies_on_first_use: bool = False,
    direct_resident_dependencies: Sequence[str] = (),
    module_defines: Mapping[str, str] | None = None,
    resident_ro_after_init_globals: Sequence[str] = (),
    autoload_alias: str | None = None,
    expand_private_source_closure: bool = False,
    source_closure_output: str | Path | None = None,
    excluded_interfaces: Sequence[str] = (),
) -> Mapping[str, Any]:
    """Prepare one or more READY tables without a syscall candidate seed."""

    from .candidate import prepare_candidate

    selectors = _unique_strings(table_selectors, "callback table")
    if not selectors:
        raise GraphValidationError("at least one callback table is required")
    plan = load_callback_plan(plan_path)
    report = analyze_callback_tables(graph, plan)
    selected = _select_table_reports(report, graph, selectors)
    not_ready = [
        f"{item['table_symbol']} ({item['status']})"
        for item in selected
        if item["status"] != "READY"
    ]
    if not_ready:
        raise GraphValidationError(
            "callback table is not eligible for automatic extraction: "
            + ", ".join(not_ready)
        )
    function_ids = list(
        dict.fromkeys(
            function_id
            for item in selected
            for function_id in item["recommended_interface_ids"]
        )
    )
    if not function_ids:
        raise GraphValidationError(
            "selected callback tables have no cold process interfaces"
        )
    table_ids = [item["table_id"] for item in selected]
    resident_ids = {
        function_id
        for item in selected
        for function_id in item["resident_interface_ids"]
    }
    explicit_exclusions = _resolve_selected_interfaces(
        graph,
        {
            function_id
            for item in selected
            for function_id in item["all_local_interface_ids"]
        },
        excluded_interfaces,
    )
    excluded_id_set = resident_ids.union(explicit_exclusions)
    function_ids = [
        function_id
        for function_id in function_ids
        if function_id not in explicit_exclusions
    ]
    if not function_ids:
        raise GraphValidationError(
            "interface exclusion removed every callback-table entry"
        )
    identity = hashlib.sha256(
        "\0".join(sorted(table_ids)).encode("utf-8")
    ).hexdigest()[:16]
    candidate_id = f"callback-table:{identity}"
    automatic_unbridgeable: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory(
        prefix="linux-modularizer-callback-plan-"
    ) as temporary:
        synthetic_path = Path(temporary) / "plan.json"
        for _iteration in range(len(function_ids) + 1):
            active_function_ids = [
                item for item in function_ids if item not in excluded_id_set
            ]
            if not active_function_ids:
                raise GraphValidationError(
                    "unrepresentable resident dependencies removed every "
                    "callback-table entry"
                )
            write_json_atomic(
                synthetic_path,
                {
                    "schema_version": 1,
                    "candidates": [
                        {
                            "id": candidate_id,
                            "functions": active_function_ids,
                            "owned_globals": [],
                            "interfaces": active_function_ids,
                            "readiness": "READY",
                        }
                    ],
                },
            )
            prepare_candidate(
                graph,
                synthetic_path,
                candidate_id,
                source_extractor=source_extractor,
                compilation_database=compilation_database,
                kernel_root=kernel_root,
                module_name=module_name,
                extraction_output=extraction_output,
                backend_policy_output=backend_policy_output,
                failure_expressions=failure_expressions,
                duplicate_functions=duplicate_functions,
                resident_exports=resident_exports,
                external_resident_exports=external_resident_exports,
                resident_inline_proxies=resident_inline_proxies,
                pack_resident_dependencies=pack_resident_dependencies,
                initialize_packed_dependencies_at_load=(
                    initialize_packed_dependencies_at_load
                ),
                bind_packed_dependencies_on_first_use=(
                    bind_packed_dependencies_on_first_use
                ),
                direct_resident_dependencies=(
                    direct_resident_dependencies
                ),
                module_defines=module_defines,
                resident_ro_after_init_globals=(
                    resident_ro_after_init_globals
                ),
                autoload_alias=autoload_alias,
                expand_private_source_closure=(
                    expand_private_source_closure
                ),
                source_closure_output=source_closure_output,
                excluded_interfaces=sorted(excluded_id_set),
                promoted_callback_tables=table_ids,
            )
            blocked = _unbridgeable_interface_dependencies(
                extraction_output,
                backend_policy_output,
                graph=graph,
                interface_ids=active_function_ids,
            )
            new_blocked = set(blocked) - excluded_id_set
            if not new_blocked:
                function_ids = active_function_ids
                break
            for function_id in sorted(new_blocked):
                automatic_unbridgeable[function_id] = blocked[function_id]
            excluded_id_set.update(new_blocked)
        else:
            raise GraphValidationError(
                "callback-table bridgeability analysis did not converge"
            )
    excluded_ids = sorted(excluded_id_set)
    if discovery_output is not None:
        write_json_atomic(
            discovery_output,
            {
                **report,
                "selection": {
                    "candidate_id": candidate_id,
                    "table_ids": table_ids,
                    "table_symbols": [
                        item["table_symbol"] for item in selected
                    ],
                    "recommended_interface_ids": function_ids,
                    "recommended_interface_symbols": [
                        graph.nodes[item].symbol for item in function_ids
                    ],
                    "resident_interface_ids": excluded_ids,
                    "resident_interface_symbols": [
                        graph.nodes[item].symbol for item in excluded_ids
                    ],
                    "automatic_unbridgeable_interfaces": {
                        graph.nodes[item].symbol: reasons
                        for item, reasons in sorted(
                            automatic_unbridgeable.items()
                        )
                    },
                },
            },
        )
    return {
        "candidate_id": candidate_id,
        "table_ids": table_ids,
        "table_symbols": [item["table_symbol"] for item in selected],
        "interface_ids": function_ids,
        "interface_symbols": [
            graph.nodes[item].symbol for item in function_ids
        ],
        "resident_interface_ids": excluded_ids,
        "resident_interface_symbols": [
            graph.nodes[item].symbol for item in excluded_ids
        ],
    }


def render_callback_table_markdown(report: Mapping[str, Any]) -> str:
    """Render the compact, review-oriented view of a discovery report."""

    summary = report["summary"]
    lines = [
        "# Callback-table modularization opportunities",
        "",
        "The table and its original callback addresses stay resident; only "
        "the implementations behind selected process-context callbacks move.",
        "",
        "## Summary",
        "",
        f"- Recognized tables: {summary['recognized_callback_tables']}",
        f"- READY tables: {summary['ready_callback_tables']}",
        "- READY tables with positive direct estimate: "
        f"{summary['ready_positive_direct_tables']}",
        "- Duplicate initializer evidence edges removed: "
        f"{summary['duplicate_initializer_evidence_edges_removed']}",
        "- Positive direct estimate before private-source closure: "
        f"{summary['positive_direct_net_bytes_without_source_closure']} B",
        "- Estimated standalone release groups: "
        f"{summary['estimated_standalone_release_groups']}",
        "- Complementary two-group portfolios: "
        f"{summary['estimated_complementary_pair_portfolios']}",
        "",
        "## Highest-ranked tables",
        "",
        "| Table | Source | Family | Status | Interfaces | Direct net |",
        "|---|---|---|---:|---:|---:|",
    ]
    for item in report["tables"][:50]:
        net = item["estimated_direct_net_bytes"]
        lines.append(
            f"| `{item['table_symbol']}` | `{item['source_path']}` | "
            f"{item['family']} | {item['status']} | "
            f"{len(item['recommended_interface_ids'])} | "
            f"{net if net is not None else 'unknown'} B |"
        )
    lines.extend(
        [
            "",
            "## Complementary release portfolios",
            "",
            "Each group is below the release threshold by itself; the "
            "pair reaches it after accounting for each group's resident "
            "wrapper estimate.",
            "",
            "| Sources | Interfaces | Estimated combined net |",
            "|---|---:|---:|",
        ]
    )
    for item in report.get("release_portfolios", [])[:20]:
        sources = "<br>".join(
            f"`{source}`" for source in item["source_paths"]
        )
        lines.append(
            f"| {sources} | {item['interface_count']} | "
            f"{item['estimated_direct_net_bytes']} B |"
        )
    lines.extend(
        [
            "",
            "`READY` is a source-extraction gate, not final proof. Object "
            "size, link closure, unload/reload lifetime, concurrency, and "
            "boot A/B gates still have to pass.",
            "",
        ]
    )
    return "\n".join(lines)


def _collect_table_facts(
    graph: ReferenceGraph,
) -> tuple[list[dict[str, Any]], int]:
    result = []
    raw_edges = 0
    for table in graph.nodes.values():
        if table.kind is not EntityKind.GLOBAL or not table.source_path:
            continue
        table_unit = table.translation_unit or table.source_path
        targets: dict[str, dict[str, Any]] = {}
        external_targets: set[str] = set()
        shapes: set[str] = set()
        for edge in graph.outgoing(
            table.id, kinds={EdgeKind.GLOBAL_INITIALIZER}
        ):
            target = graph.nodes[edge.target]
            if target.kind is not EntityKind.FUNCTION:
                continue
            parsed = _parse_layout_shape(edge.field_path)
            if parsed is not None:
                shapes.add(parsed[0])
            target_unit = target.translation_unit or target.source_path
            if (
                not target.source_path
                or "definition" not in target.attributes
                or target_unit != table_unit
            ):
                if target.source_path:
                    external_targets.add(target.id)
                continue
            raw_edges += 1
            fact = targets.setdefault(
                target.id,
                {"node": target, "offsets_by_shape": defaultdict(set)},
            )
            if parsed is not None:
                fact["offsets_by_shape"][parsed[0]].add(parsed[1])
        if targets:
            result.append(
                {
                    "node": table,
                    "local_targets": targets,
                    "external_targets": sorted(external_targets),
                    "shapes": shapes,
                }
            )
    return result, raw_edges


def _infer_family_shapes(
    table_facts: Sequence[Mapping[str, Any]],
    graph: ReferenceGraph,
) -> dict[str, str]:
    del graph
    votes: dict[str, Counter[str]] = {
        family: Counter() for family in _TABLE_NAME_PATTERNS
    }
    for facts in table_facts:
        symbol = facts["node"].symbol.lower()
        shapes = facts["shapes"]
        for family, pattern in _TABLE_NAME_PATTERNS.items():
            if pattern.search(symbol):
                for shape in shapes:
                    votes[family][shape] += 1
    selected: dict[str, str] = {}
    claimed: set[str] = set()
    for family in (
        "file_operations",
        "proc_ops",
        "seq_operations",
        "proto_ops",
    ):
        ranked = sorted(
            votes[family].items(), key=lambda item: (-item[1], item[0])
        )
        for shape, _count in ranked:
            if shape not in claimed:
                selected[family] = shape
                claimed.add(shape)
                break
    return selected


def _table_opportunity(
    facts: Mapping[str, Any],
    *,
    graph: ReferenceGraph,
    family_shapes: Mapping[str, str],
    policy: Mapping[str, Any],
    boot_evidence: Mapping[str, Any],
    decisions: Mapping[str, Mapping[str, Any]],
    first_stub: int,
    additional_stub: int,
) -> dict[str, Any]:
    table = facts["node"]
    matching_families = [
        family
        for family, shape in family_shapes.items()
        if shape in facts["shapes"]
    ]
    family = matching_families[0] if len(matching_families) == 1 else None
    family_shape = family_shapes.get(family) if family else None
    observed = set(_string_array(policy, "observed_boot_functions"))
    pre = set(_string_array(policy, "pre_loader_observed_functions"))
    post = set(_string_array(policy, "post_loader_observed_functions"))
    hard_attributes = set(_string_array(policy, "hard_attributes"))
    hard_attributes.update(_string_array(policy, "resident_attributes"))
    nonloadable = set(_string_array(policy, "non_loadable_contexts"))
    callbacks = []
    recommended_ids = []
    resident_ids = []
    for target_id, target_fact in sorted(
        facts["local_targets"].items()
    ):
        node = target_fact["node"]
        offsets = sorted(
            target_fact["offsets_by_shape"].get(family_shape, ())
        )
        classification, reasons, fields = _classify_callback(
            node,
            family=family,
            offsets=offsets,
            observed=observed,
            hard_attributes=hard_attributes,
            nonloadable_contexts=nonloadable,
            boot_proven=bool(boot_evidence["complete_for_init_cold"]),
        )
        if classification == "DEFERRED_PROCESS_CALLBACK":
            recommended_ids.append(target_id)
        else:
            resident_ids.append(target_id)
        decision = decisions.get(target_id, {})
        callbacks.append(
            {
                "id": target_id,
                "symbol": node.symbol,
                "size_bytes": node.size_bytes,
                "field_offsets": offsets,
                "field_names": fields,
                "contexts": sorted(item.value for item in node.contexts),
                "attributes": sorted(node.attributes),
                "boot_observed": target_id in observed,
                "pre_loader_observed": target_id in pre,
                "post_loader_observed": target_id in post,
                "planner_disposition": decision.get("disposition"),
                "classification": classification,
                "reasons": reasons,
            }
        )
    known = sum(
        graph.nodes[item].size_bytes or 0 for item in recommended_ids
    )
    unknown = sum(
        graph.nodes[item].size_bytes is None for item in recommended_ids
    )
    stub = _stub_cost(len(recommended_ids), first_stub, additional_stub)
    net = None if unknown else known - stub
    if family not in _AUTO_FIELD_MAPS:
        status = "REVIEW_REQUIRED"
        status_reasons = [
            "table family does not have an automatic process-context policy"
        ]
    elif not boot_evidence["complete_for_init_cold"]:
        status = "BOOT_EVIDENCE_MISSING"
        status_reasons = [
            "initcall coverage and loader-ready evidence are required"
        ]
    elif not recommended_ids:
        status = "NO_SAFE_COLD_CALLBACK"
        status_reasons = [
            "no unobserved, process-context callback remains after guards"
        ]
    else:
        status = "READY"
        status_reasons = []
    return {
        "table_id": table.id,
        "table_symbol": table.symbol,
        "source_path": table.source_path,
        "family": family,
        "layout_shape": family_shape,
        "status": status,
        "status_reasons": status_reasons,
        "all_local_interface_ids": sorted(facts["local_targets"]),
        "recommended_interface_ids": recommended_ids,
        "recommended_interface_symbols": [
            graph.nodes[item].symbol for item in recommended_ids
        ],
        "resident_interface_ids": resident_ids,
        "resident_interface_symbols": [
            graph.nodes[item].symbol for item in resident_ids
        ],
        "external_resident_target_ids": facts["external_targets"],
        "known_interface_bytes": known,
        "unknown_size_interfaces": unknown,
        "estimated_stub_bytes": stub,
        "estimated_direct_net_bytes": net,
        "callbacks": callbacks,
    }


def _classify_callback(
    node: ReferenceNode,
    *,
    family: str | None,
    offsets: Sequence[int],
    observed: set[str],
    hard_attributes: set[str],
    nonloadable_contexts: set[str],
    boot_proven: bool,
) -> tuple[str, list[str], list[str]]:
    reasons = []
    if not boot_proven:
        reasons.append("boot trace does not prove complete init coverage")
    if node.id in observed:
        reasons.append("function was observed during startup")
    attributes = sorted(node.attributes.intersection(hard_attributes))
    if attributes:
        reasons.append("resident attributes: " + ", ".join(attributes))
    contexts = sorted(
        context.value
        for context in node.contexts
        if context.value in nonloadable_contexts
    )
    if contexts:
        reasons.append("non-loadable contexts: " + ", ".join(contexts))
    field_map = _AUTO_FIELD_MAPS.get(family)
    fields = []
    if field_map is None:
        reasons.append("callback-table family requires review")
    elif not offsets:
        reasons.append("LLVM field offset is not available")
    else:
        for offset in offsets:
            field = field_map.get(offset)
            if field is None:
                fields.append(f"offset_{offset}")
                reasons.append(f"unknown callback field offset {offset}")
            else:
                fields.append(field[0])
                if not field[1]:
                    reasons.append(
                        f"{field[0]} establishes, tears down, or carries "
                        "persistent state"
                    )
    if _LIFETIME_NAME.search(node.symbol.lower()) and not fields:
        reasons.append("function name indicates a lifetime boundary")
    if reasons:
        return "RESIDENT_GUARD", list(dict.fromkeys(reasons)), fields
    return "DEFERRED_PROCESS_CALLBACK", [], fields


def _source_groups(
    opportunities: Sequence[Mapping[str, Any]],
    *,
    graph: ReferenceGraph,
    first_stub: int,
    additional_stub: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in opportunities:
        if item["status"] != "READY":
            continue
        source = item["source_path"]
        group = grouped.setdefault(
            source, {"tables": [], "interfaces": set()}
        )
        group["tables"].append(item["table_id"])
        group["interfaces"].update(item["recommended_interface_ids"])
    result = []
    for source, value in grouped.items():
        interfaces = sorted(value["interfaces"])
        known = sum(graph.nodes[item].size_bytes or 0 for item in interfaces)
        unknown = sum(
            graph.nodes[item].size_bytes is None for item in interfaces
        )
        stub = _stub_cost(len(interfaces), first_stub, additional_stub)
        result.append(
            {
                "source_path": source,
                "table_ids": sorted(value["tables"]),
                "table_symbols": [
                    graph.nodes[item].symbol
                    for item in sorted(value["tables"])
                ],
                "interface_ids": interfaces,
                "interface_symbols": [
                    graph.nodes[item].symbol for item in interfaces
                ],
                "known_interface_bytes": known,
                "unknown_size_interfaces": unknown,
                "estimated_stub_bytes": stub,
                "estimated_direct_net_bytes": (
                    None if unknown else known - stub
                ),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["estimated_direct_net_bytes"]
            if item["estimated_direct_net_bytes"] is not None
            else -1,
            item["known_interface_bytes"],
            item["source_path"],
        ),
        reverse=True,
    )


def _release_portfolios(
    source_groups: Sequence[Mapping[str, Any]],
    *,
    minimum_savings_bytes: int,
) -> list[dict[str, Any]]:
    """Return complementary pairs that cross the production size gate.

    A source group that already reaches the threshold does not need a
    portfolio.  Unknown, zero, and negative estimates are excluded.  Pair
    estimates are additive because each source group already includes its
    own first-interface and additional-interface resident stub costs.
    Actual source closure, final linking, compression, and runtime gates are
    intentionally left to the backend validation stages.
    """

    subthreshold = [
        item
        for item in source_groups
        if item["estimated_direct_net_bytes"] is not None
        and 0 < item["estimated_direct_net_bytes"] < minimum_savings_bytes
    ]
    portfolios = []
    for first_index, first in enumerate(subthreshold):
        for second in subthreshold[first_index + 1 :]:
            estimated = (
                first["estimated_direct_net_bytes"]
                + second["estimated_direct_net_bytes"]
            )
            if estimated < minimum_savings_bytes:
                continue
            groups = sorted(
                (first, second), key=lambda item: item["source_path"]
            )
            portfolios.append(
                {
                    "source_paths": [
                        item["source_path"] for item in groups
                    ],
                    "source_group_estimated_net_bytes": [
                        item["estimated_direct_net_bytes"] for item in groups
                    ],
                    "table_ids": sorted(
                        table_id
                        for item in groups
                        for table_id in item["table_ids"]
                    ),
                    "table_symbols": sorted(
                        symbol
                        for item in groups
                        for symbol in item["table_symbols"]
                    ),
                    "interface_count": sum(
                        len(item["interface_ids"]) for item in groups
                    ),
                    "estimated_direct_net_bytes": estimated,
                }
            )
    return sorted(
        portfolios,
        key=lambda item: (
            item["estimated_direct_net_bytes"],
            -item["interface_count"],
            item["source_paths"],
        ),
        reverse=True,
    )


def _unbridgeable_interface_dependencies(
    extraction_path: str | Path,
    backend_policy_path: str | Path,
    *,
    graph: ReferenceGraph,
    interface_ids: Sequence[str],
) -> dict[str, list[str]]:
    """Find interfaces whose resident dependencies cannot cross the ABI.

    Clang describes a file-local anonymous aggregate as, for example,
    ``struct (unnamed struct at file.c:10:1)[4]``.  That diagnostic spelling
    is not valid C and the anonymous type has no cross-translation-unit name.
    A resident object of that type therefore cannot be placed in the typed
    dependency table.  Likewise, a jump-label static key used by
    ``static_branch_*`` must retain a link-time-constant address and cannot be
    accessed through that table.  Keep only the interfaces whose module-side
    closure reaches an invalid object or expression; unrelated callbacks from
    the same table remain extractable.
    """

    extraction_file = Path(extraction_path)
    policy_file = Path(backend_policy_path)
    # Tests may replace prepare_candidate with a recorder.  No generated
    # artifacts means there is no second-stage bridgeability evidence.
    if not extraction_file.is_file() or not policy_file.is_file():
        return {}
    try:
        extraction = json.loads(extraction_file.read_text(encoding="utf-8"))
        backend_policy = json.loads(policy_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphValidationError(
            f"cannot inspect callback-table bridgeability: {error}"
        ) from error
    if not isinstance(extraction, Mapping) or not isinstance(
        backend_policy, Mapping
    ):
        raise GraphValidationError(
            "callback-table bridgeability artifacts must be objects"
        )
    functions = _entity_map(extraction, "functions")
    globals_ = _entity_map(extraction, "globals")
    resident_functions = set(
        _artifact_string_array(backend_policy, "resident_exports")
    )
    resident_globals = set(
        _artifact_string_array(
            backend_policy, "external_resident_exports"
        )
    ).union(
        _artifact_string_array(backend_policy, "resident_ro_after_init_globals")
    )
    invalid_global_reasons = {
        symbol: ["unspellable resident type: " + symbol]
        for symbol, value in globals_.items()
        if symbol in resident_globals
        and _type_is_unspellable(value.get("type"))
    }
    invalid_globals = set(invalid_global_reasons)

    module_entities: dict[tuple[str, str], Mapping[str, Any]] = {
        **{
            ("function", symbol): value
            for symbol, value in functions.items()
            if symbol not in resident_functions
        },
        **{
            ("global", symbol): value
            for symbol, value in globals_.items()
            if symbol not in resident_globals
        },
    }

    static_key_globals = {
        symbol
        for symbol, value in globals_.items()
        if symbol in resident_globals
        and _type_requires_link_time_address(value.get("type"))
    }
    invalid_entity_reasons: dict[tuple[str, str], list[str]] = {}
    for key, value in module_entities.items():
        if key[0] != "function":
            continue
        macros = set(_artifact_string_array(value, "macros"))
        branch_macros = macros.intersection(_LINK_TIME_ADDRESS_MACROS)
        raw_dependencies = value.get("dependencies")
        if not isinstance(raw_dependencies, Mapping):
            continue
        address_taken = set(
            _artifact_string_array(
                raw_dependencies, "address_taken_globals"
            )
        )
        keys = address_taken.intersection(static_key_globals)
        if not branch_macros or not keys:
            continue
        invalid_entity_reasons[key] = [
            "link-time constant static-key address: "
            + symbol
            + " ("
            + ", ".join(sorted(branch_macros))
            + ")"
            for symbol in sorted(keys)
        ]

    if not invalid_globals and not invalid_entity_reasons:
        return {}

    def dependencies(
        entity: Mapping[str, Any]
    ) -> set[tuple[str, str]]:
        raw = entity.get("dependencies")
        if not isinstance(raw, Mapping):
            return set()
        result = {
            ("function", symbol)
            for symbol in _artifact_string_array(raw, "functions")
            if ("function", symbol) in module_entities
        }
        result.update(
            ("global", symbol)
            for symbol in _artifact_string_array(raw, "globals")
            if ("global", symbol) in module_entities
            or symbol in invalid_globals
        )
        return result

    dependency_map = {
        key: dependencies(value) for key, value in module_entities.items()
    }
    result: dict[str, list[str]] = {}
    for interface_id in interface_ids:
        symbol = graph.nodes[interface_id].symbol
        root = ("function", symbol)
        pending = [root]
        visited: set[tuple[str, str]] = set()
        reached_reasons: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            reached_reasons.update(
                invalid_entity_reasons.get(current, ())
            )
            for dependency in dependency_map.get(current, ()):
                if (
                    dependency[0] == "global"
                    and dependency[1] in invalid_globals
                ):
                    reached_reasons.update(
                        invalid_global_reasons[dependency[1]]
                    )
                elif dependency in module_entities:
                    pending.append(dependency)
        if reached_reasons:
            result[interface_id] = sorted(reached_reasons)
    return result


def _entity_map(
    artifact: Mapping[str, Any], key: str
) -> dict[str, Mapping[str, Any]]:
    raw = artifact.get(key, [])
    if not isinstance(raw, list):
        raise GraphValidationError(f"source extraction.{key} must be an array")
    result = {}
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(
            item.get("symbol"), str
        ):
            raise GraphValidationError(
                f"source extraction.{key} contains an invalid entity"
            )
        result[item["symbol"]] = item
    return result


def _artifact_string_array(
    artifact: Mapping[str, Any], key: str
) -> list[str]:
    raw = artifact.get(key, [])
    if not isinstance(raw, list) or not all(
        isinstance(item, str) for item in raw
    ):
        raise GraphValidationError(f"artifact.{key} must be an array")
    return list(raw)


def _type_is_unspellable(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "(unnamed struct at ",
            "(unnamed union at ",
            "(anonymous struct at ",
            "(anonymous union at ",
        )
    )


def _type_requires_link_time_address(value: Any) -> bool:
    """Return whether a resident object is a jump-label static key."""

    if not isinstance(value, str):
        return False
    return re.search(r"\bstatic_key(?:_(?:true|false))?\b", value) is not None


def _boot_evidence(policy: Mapping[str, Any]) -> dict[str, Any]:
    phase_aware = policy.get("phase_aware_boot") is True
    initcalls = policy.get("trace_covers_initcalls") is True
    loader_ready = policy.get("loader_ready_observed") is True
    return {
        "phase_aware_boot": phase_aware,
        "trace_covers_initcalls": initcalls,
        "loader_ready_observed": loader_ready,
        "complete_for_init_cold": phase_aware and initcalls and loader_ready,
        "observed_boot_functions": len(
            _string_array(policy, "observed_boot_functions")
        ),
        "pre_loader_observed_functions": len(
            _string_array(policy, "pre_loader_observed_functions")
        ),
        "post_loader_observed_functions": len(
            _string_array(policy, "post_loader_observed_functions")
        ),
    }


def _parse_layout_shape(field_path: str | None) -> tuple[str, int] | None:
    if not field_path:
        return None
    match = _LAYOUT_SHAPE.fullmatch(field_path)
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def _stub_cost(count: int, first: int, additional: int) -> int:
    if count <= 0:
        return 0
    return first + additional * (count - 1)


def _nonnegative_integer(
    override: int | None, fallback: Any, context: str
) -> int:
    value = fallback if override is None else override
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GraphValidationError(f"{context} must be a non-negative integer")
    return value


def _string_array(value: Mapping[str, Any], key: str) -> list[str]:
    raw = value.get(key, [])
    if not isinstance(raw, list) or not all(
        isinstance(item, str) for item in raw
    ):
        raise GraphValidationError(f"module plan.policy.{key} must be an array")
    return list(raw)


def _select_table_reports(
    report: Mapping[str, Any],
    graph: ReferenceGraph,
    selectors: Sequence[str],
) -> list[Mapping[str, Any]]:
    tables = report.get("tables", [])
    result = []
    for selector in selectors:
        matches = [
            item
            for item in tables
            if item["table_id"] == selector
            or item["table_symbol"] == selector
        ]
        if len(matches) != 1:
            graph_matches = [
                node.id
                for node in graph.nodes.values()
                if node.kind is EntityKind.GLOBAL
                and (node.id == selector or node.symbol == selector)
            ]
            detail = (
                "unrecognized table"
                if graph_matches
                else "no graph global"
            )
            raise GraphValidationError(
                f"callback table selector {selector!r} matched "
                f"{len(matches)} ranked tables ({detail}); use a "
                "linkage-aware table id"
            )
        if matches[0] not in result:
            result.append(matches[0])
    return result


def _resolve_selected_interfaces(
    graph: ReferenceGraph,
    allowed: set[str],
    selectors: Sequence[str],
) -> set[str]:
    result = set()
    for selector in _unique_strings(selectors, "excluded interface"):
        matches = [
            node_id
            for node_id in allowed
            if node_id == selector or graph.nodes[node_id].symbol == selector
        ]
        if len(matches) != 1:
            raise GraphValidationError(
                f"excluded interface selector {selector!r} matched "
                f"{len(matches)} selected table callbacks"
            )
        result.add(matches[0])
    return result


def _unique_strings(values: Sequence[str], context: str) -> list[str]:
    result = []
    for value in values:
        if not isinstance(value, str) or not value:
            raise GraphValidationError(f"{context} must be a non-empty string")
        if value not in result:
            result.append(value)
    return result

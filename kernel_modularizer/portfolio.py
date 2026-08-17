"""Batch preparation of callback-table modularization portfolios.

The callback analyzer and source backend intentionally operate on one
reviewable boundary at a time.  This module adds the missing orchestration
layer: select source-disjoint callback groups, prepare every group in an
isolated directory, preserve failures as data, and optionally integrate the
successful bundles transactionally.

``PREPARED`` means that LLVM/Clang closure analysis and bundle generation
completed.  It deliberately does not mean that the generated module passed a
kernel build or a runtime lifecycle test; those remain later validation
states.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence

from .backend import generate_module_bundle, load_backend_policy
from .callback_tables import (
    analyze_callback_tables,
    load_callback_plan,
    prepare_callback_tables,
)
from .errors import GraphValidationError
from .extraction import load_source_extraction
from .integration import apply_bundle
from .io import write_json_atomic, write_text_atomic
from .manifest import sha256_file
from .model import ReferenceGraph


CALLBACK_PORTFOLIO_SCHEMA_VERSION = 1
DEFAULT_MODULE_PREFIX = "deferred_auto_cb"
DEFAULT_ALIAS_PREFIX = "lmautocb"
_GENERATED_SOURCE_MARKERS = (
    "linux/linux_modularizer/",
    "/* linux-modular interface:",
    "/* linux-modular:",
)
_IDENTIFIER_PART = re.compile(r"[^A-Za-z0-9_]+")


def plan_callback_portfolio(
    callback_report: Mapping[str, Any],
    *,
    minimum_estimated_net_bytes: int = 0,
    include_unknown_size: bool = True,
    maximum_source_groups: int = 0,
    only_sources: Sequence[str] = (),
    excluded_sources: Sequence[str] = (),
) -> dict[str, Any]:
    """Select ranked source groups and explain every rejected group.

    A zero maximum means no limit.  Unknown-size groups are kept by default
    for exploratory extraction because their real closure and object size are
    established only by later stages.
    """

    if not isinstance(minimum_estimated_net_bytes, int):
        raise GraphValidationError(
            "minimum estimated net bytes must be an integer"
        )
    if not isinstance(maximum_source_groups, int) or maximum_source_groups < 0:
        raise GraphValidationError(
            "maximum source groups must be a non-negative integer"
        )
    only = _unique_paths(only_sources, "only source")
    excluded = _unique_paths(excluded_sources, "excluded source")
    overlap = sorted(set(only).intersection(excluded))
    if overlap:
        raise GraphValidationError(
            "sources cannot be both selected and excluded: "
            + ", ".join(overlap)
        )
    raw_groups = callback_report.get("source_groups")
    if not isinstance(raw_groups, list):
        raise GraphValidationError(
            "callback report.source_groups must be an array"
        )
    groups = [_source_group(value, index) for index, value in enumerate(raw_groups)]
    available = {group["source_path"] for group in groups}
    missing = sorted(set(only) - available)
    if missing:
        raise GraphValidationError(
            "requested source group is absent from callback analysis: "
            + ", ".join(missing)
        )
    ranked = sorted(groups, key=_source_group_rank, reverse=True)
    selected = []
    rejected = []
    for group in ranked:
        source = group["source_path"]
        net = group["estimated_direct_net_bytes"]
        reason = None
        if only and source not in only:
            reason = "ONLY_SOURCE_FILTER"
        elif source in excluded:
            reason = "EXCLUDED_SOURCE"
        elif net is None and not include_unknown_size:
            reason = "UNKNOWN_SIZE_EXCLUDED"
        elif net is not None and net < minimum_estimated_net_bytes:
            reason = "ESTIMATE_BELOW_MINIMUM"
        elif maximum_source_groups and len(selected) >= maximum_source_groups:
            reason = "MAXIMUM_SOURCE_GROUPS"
        if reason is None:
            selected.append(group)
        else:
            rejected.append({**group, "selection_reason": reason})
    rejection_counts = Counter(
        item["selection_reason"] for item in rejected
    )
    return {
        "schema_version": CALLBACK_PORTFOLIO_SCHEMA_VERSION,
        "policy": {
            "minimum_estimated_net_bytes": minimum_estimated_net_bytes,
            "include_unknown_size": include_unknown_size,
            "maximum_source_groups": maximum_source_groups,
            "only_sources": list(only),
            "excluded_sources": list(excluded),
        },
        "summary": {
            "discovered_ready_source_groups": len(groups),
            "selected_source_groups": len(selected),
            "selected_callback_tables": sum(
                len(item["table_ids"]) for item in selected
            ),
            "selected_interfaces_before_source_closure": sum(
                len(item["interface_ids"]) for item in selected
            ),
            "selected_known_estimated_net_bytes": sum(
                item["estimated_direct_net_bytes"] or 0
                for item in selected
            ),
            "selected_unknown_size_groups": sum(
                item["estimated_direct_net_bytes"] is None
                for item in selected
            ),
            "rejected_source_groups": len(rejected),
            "rejection_counts": dict(sorted(rejection_counts.items())),
        },
        "selected_source_groups": selected,
        "rejected_source_groups": rejected,
    }


def prepare_callback_portfolio(
    graph: ReferenceGraph,
    graph_path: str | Path,
    plan_path: str | Path,
    *,
    source_extractor: str | Path,
    compilation_database: str | Path,
    kernel_root: str | Path,
    output_directory: str | Path,
    minimum_estimated_net_bytes: int = 0,
    include_unknown_size: bool = True,
    maximum_source_groups: int = 0,
    only_sources: Sequence[str] = (),
    excluded_sources: Sequence[str] = (),
    module_prefix: str = DEFAULT_MODULE_PREFIX,
    alias_prefix: str = DEFAULT_ALIAS_PREFIX,
    integrate: bool = False,
    integration_dry_run: bool = False,
    resume: bool = True,
    fail_fast: bool = False,
    allow_modified_sources: bool = False,
    json_output: str | Path | None = None,
    markdown_output: str | Path | None = None,
) -> dict[str, Any]:
    """Analyze and prepare every selected callback source group.

    Failures are isolated by default.  The report is atomically rewritten
    after every group, so an interrupted run can resume from per-group result
    artifacts without trusting partially generated directories.
    """

    graph.validate()
    if integration_dry_run and not integrate:
        raise GraphValidationError(
            "integration dry-run requires integrate=True"
        )
    prefix = _validated_identifier(module_prefix, "module prefix")
    alias = _validated_identifier(alias_prefix, "autoload alias prefix")
    kernel = Path(kernel_root).resolve()
    root = Path(output_directory).resolve()
    graph_artifact = _required_file(graph_path, "reference graph")
    plan_artifact = _required_file(plan_path, "module plan")
    extractor_artifact = _required_file(
        source_extractor, "SourceExtractor"
    )
    compile_artifact = _compile_database_file(compilation_database)
    callback_plan = load_callback_plan(plan_artifact)
    callback_report = analyze_callback_tables(graph, callback_plan)
    selection = plan_callback_portfolio(
        callback_report,
        minimum_estimated_net_bytes=minimum_estimated_net_bytes,
        include_unknown_size=include_unknown_size,
        maximum_source_groups=maximum_source_groups,
        only_sources=only_sources,
        excluded_sources=excluded_sources,
    )
    inputs = {
        "reference_graph": _artifact_record(
            graph_artifact, label="reference_graph"
        ),
        "module_plan": _artifact_record(
            plan_artifact, label="module_plan"
        ),
        "compile_database": _artifact_record(
            compile_artifact, label="compile_database"
        ),
        "source_extractor": _artifact_record(
            extractor_artifact, label="source_extractor"
        ),
    }
    report_path = (
        Path(json_output).resolve()
        if json_output is not None
        else root / "portfolio.json"
    )
    markdown_path = (
        Path(markdown_output).resolve()
        if markdown_output is not None
        else root / "portfolio.md"
    )
    report: dict[str, Any] = {
        "schema_version": CALLBACK_PORTFOLIO_SCHEMA_VERSION,
        "analysis": "automatic-callback-source-portfolio-extraction",
        "inputs": inputs,
        "kernel_root": str(kernel),
        "output_directory": str(root),
        "policy": {
            **selection["policy"],
            "module_prefix": prefix,
            "alias_prefix": alias,
            "pack_resident_dependencies": "auto_when_nonempty",
            "bind_packed_dependencies_on_first_use": "auto_when_nonempty",
            "integrate": integrate,
            "integration_dry_run": integration_dry_run,
            "resume": resume,
            "fail_fast": fail_fast,
            "allow_modified_sources": allow_modified_sources,
        },
        "callback_analysis_summary": callback_report["summary"],
        "selection_summary": selection["summary"],
        "selected_source_groups": selection["selected_source_groups"],
        "rejected_source_groups": selection["rejected_source_groups"],
        "candidates": [],
        "summary": {},
        "state_definitions": {
            "PREPARED": (
                "source closure and module bundle generated; kernel build "
                "and runtime validation are still required"
            ),
            "INTEGRATED": (
                "bundle transaction applied; kernel build and runtime "
                "validation are still required"
            ),
            "SKIPPED_ALREADY_MODULARIZED": (
                "the source already contains a linux-modular rewrite and "
                "the stale graph was not forced"
            ),
            "FAILED": "candidate-local analysis or generation failed",
        },
    }
    _write_portfolio(report, report_path, markdown_path)

    for group in selection["selected_source_groups"]:
        record = _candidate_base_record(
            group,
            kernel=kernel,
            root=root,
            module_prefix=prefix,
            alias_prefix=alias,
            input_records=inputs,
        )
        group_root = Path(record["output_directory"])
        result_path = group_root / "candidate-result.json"
        resumed = (
            _load_resumable_candidate(
                result_path, record["fingerprint"], kernel=kernel
            )
            if resume
            else None
        )
        if resumed is not None:
            record = dict(resumed)
            record["resumed"] = True
            if integrate and record["status"] != "INTEGRATED":
                record = _integrate_candidate(
                    record,
                    kernel=kernel,
                    dry_run=integration_dry_run,
                )
                write_json_atomic(result_path, record)
            report["candidates"].append(record)
            _write_portfolio(report, report_path, markdown_path)
            continue

        source = _kernel_source(kernel, group["source_path"])
        if not allow_modified_sources and _source_is_already_modularized(source):
            record.update(
                {
                    "status": "SKIPPED_ALREADY_MODULARIZED",
                    "validation_level": "NOT_PREPARED",
                    "build_validated": False,
                    "runtime_validated": False,
                    "reason": (
                        "source already contains a linux-modular rewrite; "
                        "regenerate the graph or pass allow-modified-sources"
                    ),
                }
            )
            write_json_atomic(result_path, record)
            report["candidates"].append(record)
            _write_portfolio(report, report_path, markdown_path)
            continue

        try:
            record = _prepare_candidate_group(
                graph,
                plan_artifact,
                group,
                record,
                source_extractor=extractor_artifact,
                compilation_database=compile_artifact,
                kernel=kernel,
                input_records=inputs,
            )
            if integrate:
                record = _integrate_candidate(
                    record,
                    kernel=kernel,
                    dry_run=integration_dry_run,
                )
        except (GraphValidationError, OSError, subprocess.CalledProcessError) as error:
            record.update(
                {
                    "status": "FAILED",
                    "validation_level": "NOT_PREPARED",
                    "build_validated": False,
                    "runtime_validated": False,
                    "error": _error_record(error),
                }
            )
            write_json_atomic(result_path, record)
            report["candidates"].append(record)
            _write_portfolio(report, report_path, markdown_path)
            if fail_fast:
                raise
            continue
        write_json_atomic(result_path, record)
        report["candidates"].append(record)
        _write_portfolio(report, report_path, markdown_path)
    return report


def render_callback_portfolio_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact operator-facing portfolio report."""

    summary = report.get("summary", {})
    selection = report.get("selection_summary", {})
    lines = [
        "# Automatic callback extraction portfolio",
        "",
        "`PREPARED` only means that source closure analysis and bundle "
        "generation succeeded. A full kernel build, size gate, unload/reload "
        "test, and workload validation are still required before release.",
        "",
        "## Summary",
        "",
        "- READY source groups discovered: "
        f"{selection.get('discovered_ready_source_groups', 0)}",
        f"- Source groups selected: {selection.get('selected_source_groups', 0)}",
        f"- Prepared bundles: {summary.get('prepared_candidates', 0)}",
        f"- Integrated bundles: {summary.get('integrated_candidates', 0)}",
        f"- Failed candidates: {summary.get('failed_candidates', 0)}",
        "- Moved source definitions in prepared closures: "
        f"{summary.get('moved_source_function_definitions', 0)}",
        "- Lazy interfaces after bridgeability cuts: "
        f"{summary.get('lazy_interfaces', 0)}",
        "",
        "## Candidate results",
        "",
        "| State | Source | Tables | Interfaces | Moved definitions | Module / reason |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in report.get("candidates", []):
        status = item.get("status", "PENDING")
        detail = item.get("module_name", "-")
        if status == "FAILED":
            detail = item.get("error", {}).get("message", "failed")
        elif status == "SKIPPED_ALREADY_MODULARIZED":
            detail = item.get("reason", "already modularized")
        detail = str(detail).replace("|", "\\|").replace("\n", " ")
        if len(detail) > 120:
            detail = detail[:117] + "..."
        lines.append(
            f"| {status} | `{item.get('source_path', '-')}` | "
            f"{len(item.get('table_ids', []))} | "
            f"{item.get('lazy_interfaces', 0)} | "
            f"{item.get('moved_source_functions', 0)} | {detail} |"
        )
    rejected = report.get("rejected_source_groups", [])
    if rejected:
        lines.extend(
            [
                "",
                "## Selection rejections",
                "",
                "| Source | Estimate | Reason |",
                "|---|---:|---|",
            ]
        )
        for item in rejected:
            estimate = item.get("estimated_direct_net_bytes")
            rendered = "unknown" if estimate is None else f"{estimate} B"
            lines.append(
                f"| `{item['source_path']}` | {rendered} | "
                f"{item['selection_reason']} |"
            )
    lines.append("")
    return "\n".join(lines)


def _prepare_candidate_group(
    graph: ReferenceGraph,
    plan_path: Path,
    group: Mapping[str, Any],
    record: dict[str, Any],
    *,
    source_extractor: Path,
    compilation_database: Path,
    kernel: Path,
    input_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    group_root = Path(record["output_directory"])
    extraction_path = group_root / "source-extraction.json"
    backend_policy_path = group_root / "backend-policy.json"
    discovery_path = group_root / "callback-table-discovery.json"
    closure_path = group_root / "source-closure.json"
    selection = prepare_callback_tables(
        graph,
        plan_path,
        group["table_ids"],
        source_extractor=source_extractor,
        compilation_database=compilation_database,
        kernel_root=kernel,
        module_name=record["module_name"],
        extraction_output=extraction_path,
        backend_policy_output=backend_policy_path,
        discovery_output=discovery_path,
        pack_resident_dependencies=False,
        bind_packed_dependencies_on_first_use=False,
        autoload_alias=record["autoload_alias"],
        expand_private_source_closure=True,
        source_closure_output=closure_path,
    )
    policy_value = _load_json_object(
        backend_policy_path, "backend policy"
    )
    packed_dependencies = bool(
        policy_value.get("pack_resident_dependencies", False)
    )
    policy_value["bind_packed_dependencies_on_first_use"] = (
        packed_dependencies
    )
    write_json_atomic(backend_policy_path, policy_value)
    extraction = load_source_extraction(extraction_path)
    policy = load_backend_policy(backend_policy_path)
    bundle = generate_module_bundle(extraction, policy)
    bundle_root = group_root / "bundle"
    bundle.write(bundle_root)
    closure = _load_json_object(closure_path, "source closure")
    closure_summary = closure.get("summary", {})
    if not isinstance(closure_summary, Mapping):
        raise GraphValidationError("source closure.summary must be an object")
    artifacts = {
        "callback_table_discovery": _artifact_record(discovery_path),
        "source_extraction": _artifact_record(extraction_path),
        "backend_policy": _artifact_record(backend_policy_path),
        "source_closure": _artifact_record(closure_path),
        "bundle_manifest": _artifact_record(bundle_root / "bundle.json"),
    }
    manifest_path = group_root / "manifest.json"
    write_json_atomic(
        manifest_path,
        {
            "schema_version": 1,
            "stage": "automatic-callback-source-preparation",
            "fingerprint": record["fingerprint"],
            "inputs": list(input_records.values()),
            "parameters": {
                "source_path": group["source_path"],
                "table_ids": group["table_ids"],
                "module_name": record["module_name"],
                "autoload_alias": record["autoload_alias"],
                "pack_resident_dependencies": packed_dependencies,
                "bind_packed_dependencies_on_first_use": (
                    packed_dependencies
                ),
            },
            "outputs": [
                {**artifact, "label": label}
                for label, artifact in artifacts.items()
            ],
        },
    )
    artifacts["stage_manifest"] = _artifact_record(manifest_path)
    return {
        **record,
        "status": "PREPARED",
        "validation_level": "SOURCE_BUNDLE_GENERATED",
        "build_validated": False,
        "runtime_validated": False,
        "resumed": False,
        "candidate_id": selection["candidate_id"],
        "lazy_interfaces": len(selection["interface_ids"]),
        "interface_ids": selection["interface_ids"],
        "interface_symbols": selection["interface_symbols"],
        "resident_interface_ids": selection["resident_interface_ids"],
        "resident_interface_symbols": selection[
            "resident_interface_symbols"
        ],
        "moved_source_functions": _integer_field(
            closure_summary, "moved_functions"
        ),
        "moved_source_globals": _integer_field(
            closure_summary, "moved_globals"
        ),
        "llvm_graph_coverage_complete": bool(
            closure_summary.get("llvm_graph_coverage_complete", False)
        ),
        "dependency_pack_mode": (
            "module_first_use_initialized"
            if packed_dependencies
            else "not_required"
        ),
        "artifacts": artifacts,
        "bundle_directory": str(bundle_root),
    }


def _integrate_candidate(
    record: Mapping[str, Any],
    *,
    kernel: Path,
    dry_run: bool,
) -> dict[str, Any]:
    updated = dict(record)
    try:
        result = apply_bundle(
            updated["bundle_directory"], kernel, dry_run=dry_run
        )
    except GraphValidationError as error:
        updated["integration"] = {
            "status": "FAILED",
            "error": _error_record(error),
        }
        return updated
    updated["integration"] = {
        "status": "DRY_RUN" if dry_run else "APPLIED",
        "transaction_id": result.transaction_id,
        "changed_paths": list(result.changed_paths),
        "already_applied_paths": list(result.already_applied_paths),
    }
    if not dry_run:
        updated["status"] = "INTEGRATED"
        updated["validation_level"] = "SOURCE_INTEGRATED"
    return updated


def _candidate_base_record(
    group: Mapping[str, Any],
    *,
    kernel: Path,
    root: Path,
    module_prefix: str,
    alias_prefix: str,
    input_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    identity = hashlib.sha256(
        "\0".join(group["table_ids"]).encode("utf-8")
    ).hexdigest()[:12]
    slug = _source_slug(group["source_path"])
    module_name = _bounded_identifier(
        module_prefix, slug, identity, maximum=55
    )
    autoload_alias = _bounded_identifier(
        alias_prefix, "cb", identity, maximum=55
    )
    output = root / f"{slug[:40]}-{identity}"
    source = _kernel_source(kernel, group["source_path"])
    fingerprint_core = {
        "input_sha256": {
            label: item["sha256"] for label, item in input_records.items()
        },
        "source_path": group["source_path"],
        "table_ids": group["table_ids"],
        "module_name": module_name,
        "autoload_alias": autoload_alias,
        "dependency_pack_policy": "auto_nonempty_first_use",
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_core,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "job_id": f"callback-source:{identity}",
        "fingerprint": fingerprint,
        "source_path": group["source_path"],
        "source_sha256": sha256_file(source),
        "table_ids": group["table_ids"],
        "table_symbols": group["table_symbols"],
        "interface_ids_before_source_closure": group["interface_ids"],
        "interface_symbols_before_source_closure": group[
            "interface_symbols"
        ],
        "known_interface_bytes": group["known_interface_bytes"],
        "unknown_size_interfaces": group["unknown_size_interfaces"],
        "estimated_stub_bytes": group["estimated_stub_bytes"],
        "estimated_direct_net_bytes": group[
            "estimated_direct_net_bytes"
        ],
        "module_name": module_name,
        "autoload_alias": autoload_alias,
        "output_directory": str(output),
    }


def _write_portfolio(
    report: dict[str, Any], json_path: Path, markdown_path: Path
) -> None:
    report["summary"] = _portfolio_summary(report)
    write_json_atomic(json_path, report)
    write_text_atomic(markdown_path, render_callback_portfolio_markdown(report))


def _portfolio_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    candidates = report.get("candidates", [])
    states = Counter(item.get("status", "UNKNOWN") for item in candidates)
    prepared = [
        item
        for item in candidates
        if item.get("status") in {"PREPARED", "INTEGRATED"}
    ]
    return {
        "selected_candidates": report.get("selection_summary", {}).get(
            "selected_source_groups", 0
        ),
        "completed_candidates": len(candidates),
        "pending_candidates": max(
            report.get("selection_summary", {}).get(
                "selected_source_groups", 0
            )
            - len(candidates),
            0,
        ),
        "states": dict(sorted(states.items())),
        "prepared_candidates": len(prepared),
        "integrated_candidates": states.get("INTEGRATED", 0),
        "failed_candidates": states.get("FAILED", 0),
        "integration_failed_candidates": sum(
            item.get("integration", {}).get("status") == "FAILED"
            for item in candidates
        ),
        "resumed_candidates": sum(bool(item.get("resumed")) for item in candidates),
        "lazy_interfaces": sum(item.get("lazy_interfaces", 0) for item in prepared),
        "moved_source_function_definitions": sum(
            item.get("moved_source_functions", 0) for item in prepared
        ),
        "moved_source_global_definitions": sum(
            item.get("moved_source_globals", 0) for item in prepared
        ),
        "all_prepared_graph_closures_complete": bool(prepared)
        and all(
            item.get("llvm_graph_coverage_complete", False)
            for item in prepared
        ),
        "build_validated_candidates": sum(
            bool(item.get("build_validated")) for item in candidates
        ),
        "runtime_validated_candidates": sum(
            bool(item.get("runtime_validated")) for item in candidates
        ),
    }


def _load_resumable_candidate(
    path: Path, fingerprint: str, *, kernel: Path
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("fingerprint") != fingerprint:
        return None
    if value.get("status") not in {"PREPARED", "INTEGRATED"}:
        return None
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return None
    for raw in artifacts.values():
        if not isinstance(raw, Mapping):
            return None
        artifact_path = raw.get("path")
        expected = raw.get("sha256")
        size = raw.get("size_bytes")
        if not isinstance(artifact_path, str) or not isinstance(expected, str):
            return None
        artifact = Path(artifact_path)
        if not artifact.is_file() or artifact.stat().st_size != size:
            return None
        if sha256_file(artifact) != expected:
            return None
    if not _resumable_source_matches(value, kernel=kernel):
        return None
    return value


def _resumable_source_matches(
    value: Mapping[str, Any], *, kernel: Path
) -> bool:
    source_path = value.get("source_path")
    original_sha256 = value.get("source_sha256")
    if not isinstance(source_path, str) or not isinstance(
        original_sha256, str
    ):
        return False
    try:
        source = _kernel_source(kernel, source_path)
        current_sha256 = sha256_file(source)
    except GraphValidationError:
        return False
    if value.get("status") == "PREPARED":
        return current_sha256 == original_sha256
    bundle_directory = value.get("bundle_directory")
    if not isinstance(bundle_directory, str):
        return False
    try:
        manifest = _load_json_object(
            Path(bundle_directory) / "bundle.json", "bundle manifest"
        )
    except GraphValidationError:
        return False
    replacements = manifest.get("resident_replacements")
    if not isinstance(replacements, list):
        return False
    matching = [
        item
        for item in replacements
        if isinstance(item, Mapping)
        and item.get("kernel_path") == source_path
    ]
    return len(matching) == 1 and current_sha256 == matching[0].get(
        "generated_sha256"
    )


def _source_group(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GraphValidationError(
            f"callback report.source_groups[{index}] must be an object"
        )
    source = value.get("source_path")
    if not isinstance(source, str) or not source:
        raise GraphValidationError(
            f"callback report.source_groups[{index}].source_path is invalid"
        )
    result = dict(value)
    for field in ("table_ids", "table_symbols", "interface_ids", "interface_symbols"):
        raw = result.get(field)
        if not isinstance(raw, list) or not all(
            isinstance(item, str) and item for item in raw
        ):
            raise GraphValidationError(
                f"callback source group {source}.{field} must be a string array"
            )
    for field in (
        "known_interface_bytes",
        "unknown_size_interfaces",
        "estimated_stub_bytes",
    ):
        _integer_field(result, field)
    net = result.get("estimated_direct_net_bytes")
    if net is not None and not isinstance(net, int):
        raise GraphValidationError(
            f"callback source group {source}.estimated_direct_net_bytes "
            "must be an integer or null"
        )
    return result


def _source_group_rank(item: Mapping[str, Any]) -> tuple[Any, ...]:
    net = item["estimated_direct_net_bytes"]
    return (
        net is not None,
        net if net is not None else -1,
        item["known_interface_bytes"],
        item["source_path"],
    )


def _compile_database_file(path: str | Path) -> Path:
    candidate = Path(path).resolve()
    if candidate.is_dir():
        candidate = candidate / "compile_commands.json"
    return _required_file(candidate, "compile database")


def _required_file(path: str | Path, context: str) -> Path:
    artifact = Path(path).resolve()
    if not artifact.is_file():
        raise GraphValidationError(f"{context} is missing: {artifact}")
    return artifact


def _kernel_source(kernel: Path, relative: str) -> Path:
    source = (kernel / relative).resolve()
    try:
        source.relative_to(kernel)
    except ValueError as error:
        raise GraphValidationError(
            f"source path escapes kernel root: {relative}"
        ) from error
    if not source.is_file():
        raise GraphValidationError(f"kernel source is missing: {source}")
    return source


def _source_is_already_modularized(path: Path) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphValidationError(f"cannot inspect source {path}: {error}") from error
    return any(marker in source for marker in _GENERATED_SOURCE_MARKERS)


def _artifact_record(
    path: str | Path, *, label: str | None = None
) -> dict[str, Any]:
    artifact = _required_file(path, "artifact")
    record = {
        "path": str(artifact),
        "sha256": sha256_file(artifact),
        "size_bytes": artifact.stat().st_size,
    }
    if label is not None:
        record["label"] = label
    return record


def _load_json_object(path: Path, context: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphValidationError(f"cannot read {context}: {error}") from error
    if not isinstance(value, dict):
        raise GraphValidationError(f"{context} root must be an object")
    return value


def _integer_field(value: Mapping[str, Any], field: str) -> int:
    result = value.get(field)
    if not isinstance(result, int) or result < 0:
        raise GraphValidationError(f"{field} must be a non-negative integer")
    return result


def _validated_identifier(value: str, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise GraphValidationError(f"{context} must be a non-empty identifier")
    if _IDENTIFIER_PART.search(value) or value[0].isdigit():
        raise GraphValidationError(
            f"{context} must contain only letters, digits and underscores "
            "and cannot begin with a digit"
        )
    return value


def _bounded_identifier(
    prefix: str, slug: str, identity: str, *, maximum: int
) -> str:
    reserve = len(prefix) + len(identity) + 2
    if reserve >= maximum:
        raise GraphValidationError(
            f"identifier prefix {prefix!r} is too long"
        )
    middle = slug[: maximum - reserve]
    return f"{prefix}_{middle}_{identity}"


def _source_slug(source: str) -> str:
    stem = source[:-2] if source.endswith(".c") else source
    slug = _IDENTIFIER_PART.sub("_", stem).strip("_").lower()
    if not slug:
        return "source"
    return slug


def _unique_paths(values: Sequence[str], context: str) -> tuple[str, ...]:
    result = []
    for value in values:
        if not isinstance(value, str) or not value:
            raise GraphValidationError(f"{context} must be a non-empty string")
        normalized = Path(value).as_posix()
        if normalized.startswith("/") or ".." in Path(normalized).parts:
            raise GraphValidationError(
                f"{context} must be relative to the kernel root: {value}"
            )
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _error_record(error: Exception) -> dict[str, Any]:
    message = str(error)
    if isinstance(error, subprocess.CalledProcessError):
        stderr = error.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if isinstance(stderr, str) and stderr.strip():
            message = stderr.strip()
    if len(message) > 4000:
        message = message[:3997] + "..."
    return {"type": type(error).__name__, "message": message}

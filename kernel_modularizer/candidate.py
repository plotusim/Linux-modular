"""Bridge one analyzed candidate to Clang source extraction inputs."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

from .errors import GraphValidationError
from .extraction import load_source_extraction
from .io import write_json_atomic
from .model import EdgeKind, EntityKind, Linkage, ReferenceGraph
from .source_closure import select_private_source_closure


def prepare_candidate(
    graph: ReferenceGraph,
    report_path: str | Path,
    candidate_id: str,
    *,
    source_extractor: str | Path,
    compilation_database: str | Path,
    kernel_root: str | Path,
    module_name: str,
    extraction_output: str | Path,
    backend_policy_output: str | Path,
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
    merged_candidate_ids: Sequence[str] = (),
    excluded_interfaces: Sequence[str] = (),
    promoted_callback_tables: Sequence[str] = (),
) -> None:
    graph.validate()
    report = _load_object(report_path, "module plan")
    candidates = report.get("candidates")
    if not isinstance(candidates, list):
        raise GraphValidationError("module plan.candidates must be an array")
    requested_candidate_ids = _unique_strings(
        [candidate_id, *merged_candidate_ids], context="candidate"
    )
    selected_candidates = []
    for candidate_index, requested_id in enumerate(requested_candidate_ids):
        matches = [
            item
            for item in candidates
            if isinstance(item, Mapping) and item.get("id") == requested_id
        ]
        if len(matches) != 1:
            raise GraphValidationError(
                f"candidate {requested_id!r} was not found exactly once"
            )
        selected = matches[0]
        _validate_extraction_readiness(
            selected,
            candidate_id=requested_id,
            primary=candidate_index == 0,
        )
        selected_candidates.append(selected)
    function_ids = list(
        dict.fromkeys(
            node_id
            for selected in selected_candidates
            for node_id in _string_array(selected, "functions")
        )
    )
    global_ids = list(
        dict.fromkeys(
            node_id
            for selected in selected_candidates
            for node_id in _string_array(selected, "owned_globals")
        )
    )
    interface_ids = list(
        dict.fromkeys(
            node_id
            for selected in selected_candidates
            for node_id in _string_array(selected, "interfaces")
        )
    )
    if not interface_ids:
        raise GraphValidationError(
            f"candidate {candidate_id} has no resident interface"
        )
    missing_ids = sorted(
        set(function_ids + global_ids + interface_ids)
        - set(graph.nodes)
    )
    if missing_ids:
        raise GraphValidationError(
            "module plan references graph nodes that no longer exist: "
            + ", ".join(missing_ids)
        )
    callback_table_ids = _resolve_global_selectors(
        graph,
        promoted_callback_tables,
        context="promoted callback table",
    )
    promoted_interface_ids = _callback_table_interface_ids(
        graph, callback_table_ids
    )
    function_ids = list(
        dict.fromkeys([*function_ids, *promoted_interface_ids])
    )
    interface_ids = list(
        dict.fromkeys([*interface_ids, *promoted_interface_ids])
    )
    excluded_interface_ids = _resolve_interface_selectors(
        graph,
        interface_ids,
        excluded_interfaces,
    )
    non_seed_exclusions = sorted(
        set(excluded_interface_ids) - set(function_ids)
    )
    if non_seed_exclusions:
        raise GraphValidationError(
            "excluded interfaces are not candidate functions: "
            + ", ".join(non_seed_exclusions)
        )
    excluded_interface_set = set(excluded_interface_ids)
    function_ids = [
        node_id
        for node_id in function_ids
        if node_id not in excluded_interface_set
    ]
    interface_ids = [
        node_id
        for node_id in interface_ids
        if node_id not in excluded_interface_set
    ]
    promoted_interface_ids = [
        node_id
        for node_id in promoted_interface_ids
        if node_id not in excluded_interface_set
    ]
    if not interface_ids:
        raise GraphValidationError(
            "interface exclusion removed every resident lazy-load entry"
        )
    duplicate_ids = _resolve_function_selectors(
        graph,
        duplicate_functions,
        context="duplicate function",
    )
    resident_export_ids = _resolve_function_selectors(
        graph,
        resident_exports,
        context="resident export",
    )
    overlap = sorted(
        set(duplicate_ids).intersection(resident_export_ids)
    )
    if overlap:
        raise GraphValidationError(
            "functions cannot be both duplicated and resident exports: "
            + ", ".join(overlap)
        )
    candidate_overlap = sorted(
        set(function_ids).intersection(
            set(duplicate_ids).union(resident_export_ids)
        )
    )
    if candidate_overlap:
        raise GraphValidationError(
            "audited closure functions must not already belong to the "
            "candidate: "
            + ", ".join(candidate_overlap)
        )
    external_exports = _unique_strings(
        external_resident_exports,
        context="external resident export",
    )
    explicit_inline_proxies = _unique_strings(
        resident_inline_proxies,
        context="resident inline proxy",
    )
    direct_dependencies = _unique_strings(
        direct_resident_dependencies,
        context="direct resident dependency",
    )

    kernel = Path(kernel_root).resolve()
    extractor = Path(source_extractor).resolve()
    if not extractor.is_file():
        raise GraphValidationError(
            f"SourceExtractor does not exist: {extractor}"
        )
    compile_database = Path(compilation_database).resolve()
    database_directory = (
        compile_database.parent
        if compile_database.is_file()
        else compile_database
    )
    if not (database_directory / "compile_commands.json").is_file():
        raise GraphValidationError(
            "compile_commands.json is missing under "
            f"{database_directory}"
        )

    grouped: dict[str, dict[str, list[str]]] = {}
    movable_by_source: dict[str, dict[str, list[str]]] = {}
    for node_id in function_ids:
        node = graph.nodes[node_id]
        if node.kind is not EntityKind.FUNCTION:
            raise GraphValidationError(
                f"candidate function is not a function node: {node_id}"
            )
        source = _node_source(node.source_path, kernel, node_id)
        grouped.setdefault(
            str(source), {"functions": [], "globals": []}
        )["functions"].append(node.symbol)
        movable_by_source.setdefault(
            str(source), {"functions": [], "globals": []}
        )["functions"].append(node.symbol)
    for node_id in (*duplicate_ids, *resident_export_ids):
        node = graph.nodes[node_id]
        source = _node_source(node.source_path, kernel, node_id)
        grouped.setdefault(
            str(source), {"functions": [], "globals": []}
        )["functions"].append(node.symbol)
    for node_id in global_ids:
        node = graph.nodes[node_id]
        if node.kind is not EntityKind.GLOBAL:
            raise GraphValidationError(
                f"owned global is not a global node: {node_id}"
            )
        source = _node_source(node.source_path, kernel, node_id)
        grouped.setdefault(
            str(source), {"functions": [], "globals": []}
        )["globals"].append(node.symbol)
        movable_by_source.setdefault(
            str(source), {"functions": [], "globals": []}
        )["globals"].append(node.symbol)

    selected_owner_node_ids = set(function_ids + global_ids)
    selected_seed_symbols = {
        graph.nodes[node_id].symbol for node_id in function_ids
    }
    synthetic_owner_symbols = set()
    for symbol in selected_seed_symbols:
        if symbol.startswith("__se_compat_sys_"):
            synthetic_owner_symbols.add(
                "__do_compat_sys_"
                + symbol.removeprefix("__se_compat_sys_")
            )
        elif symbol.startswith("__se_sys_"):
            synthetic_owner_symbols.add(
                "__do_sys_" + symbol.removeprefix("__se_sys_")
            )
    selected_owner_node_ids.update(
        node.id
        for node in graph.nodes.values()
        if node.symbol in synthetic_owner_symbols
    )

    merged: dict[str, Any] = {
        "schema_version": 1,
        "offset_encoding": "utf-8-bytes",
        "functions": [],
        "globals": [],
        "includes": [],
        "declarations": [],
        "macro_definitions": [],
    }
    closure_reports: list[Mapping[str, Any]] = []
    auto_duplicate_functions: list[str] = []
    auto_resident_functions: list[str] = []
    auto_resident_globals: list[str] = []
    explicit_protected_by_source: dict[str, list[str]] = {}
    for node_id in (*duplicate_ids, *resident_export_ids):
        node = graph.nodes[node_id]
        source = _node_source(node.source_path, kernel, node_id)
        explicit_protected_by_source.setdefault(str(source), []).append(
            node.symbol
        )
    with tempfile.TemporaryDirectory(
        prefix="linux-modularizer-extract-"
    ) as temporary:
        temporary_root = Path(temporary)
        for index, (source, selectors) in enumerate(
            sorted(grouped.items())
        ):
            output = temporary_root / f"{index}.json"
            selected_functions = set(selectors["functions"])
            selected_globals = set(selectors["globals"])
            source_unit = Path(source).relative_to(kernel).as_posix()
            for closure_iteration in range(32):
                command = [
                    str(extractor),
                    "-p",
                    str(database_directory),
                    "--output",
                    str(output),
                ]
                if (
                    expand_private_source_closure
                    and source in movable_by_source
                ):
                    command.extend(
                        ["--all-main-functions", "--all-main-globals"]
                    )
                for symbol in sorted(selected_functions):
                    command.extend(["--function", symbol])
                for symbol in sorted(selected_globals):
                    command.extend(["--global", symbol])
                command.append(source)
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    detail = (
                        completed.stderr.strip()
                        or completed.stdout.strip()
                    )
                    raise GraphValidationError(
                        f"SourceExtractor failed for {source}: {detail}"
                    )
                payload = _load_object(output, "SourceExtractor output")
                if payload.get("schema_version") != 1:
                    raise GraphValidationError(
                        "SourceExtractor returned an unsupported schema "
                        f"for {source}"
                    )
                if payload.get(
                    "offset_encoding", "utf-8-bytes"
                ) != "utf-8-bytes":
                    raise GraphValidationError(
                        "SourceExtractor returned an unsupported offset "
                        f"encoding for {source}"
                    )
                if not (
                    expand_private_source_closure
                    and source in movable_by_source
                ):
                    break
                missing_functions, missing_globals = (
                    _same_unit_macro_generated_dependencies(
                        payload,
                        graph=graph,
                        translation_unit=source_unit,
                    )
                )
                new_functions = set(missing_functions) - selected_functions
                new_globals = set(missing_globals) - selected_globals
                if not new_functions and not new_globals:
                    break
                selected_functions.update(new_functions)
                selected_globals.update(new_globals)
            else:
                raise GraphValidationError(
                    "macro-generated source dependency discovery did not "
                    f"converge for {source}"
                )
            if expand_private_source_closure and source in movable_by_source:
                source_path = Path(source)
                selection = select_private_source_closure(
                    payload,
                    graph=graph,
                    translation_unit=(
                        source_path.relative_to(kernel).as_posix()
                    ),
                    seed_functions=movable_by_source[source]["functions"],
                    seed_globals=movable_by_source[source]["globals"],
                    protected_functions=(
                        explicit_protected_by_source.get(source, [])
                    ),
                    protected_symbols=external_exports,
                    selected_owner_node_ids=sorted(
                        selected_owner_node_ids
                    ),
                    resident_wrapper_functions=[
                        graph.nodes[item].symbol
                        for item in promoted_interface_ids
                        if graph.nodes[item].source_path == source_unit
                    ],
                )
                payload = dict(selection.extraction)
                closure_reports.append(selection.report)
                auto_duplicate_functions.extend(
                    selection.auto_duplicate_functions
                )
                auto_resident_functions.extend(
                    selection.auto_resident_functions
                )
                auto_resident_globals.extend(
                    selection.auto_resident_globals
                )
            for key in (
                "functions",
                "globals",
                "includes",
                "declarations",
                "macro_definitions",
            ):
                values = payload.get(key, [])
                if not isinstance(values, list):
                    raise GraphValidationError(
                        f"SourceExtractor output.{key} must be an array"
                    )
                merged[key].extend(values)

    auto_duplicate_functions = list(
        dict.fromkeys(auto_duplicate_functions)
    )
    auto_resident_functions = list(
        dict.fromkeys(auto_resident_functions)
    )
    auto_resident_globals = list(dict.fromkeys(auto_resident_globals))
    if set(auto_resident_functions).intersection(
        set(auto_resident_globals)
    ):
        raise GraphValidationError(
            "automatic source closure found an ambiguous function/global "
            "resident dependency"
        )
    resident_function_symbols = {
        graph.nodes[item].symbol for item in resident_export_ids
    }.union(auto_resident_functions)
    auto_external_exports = _infer_external_resident_exports(
        graph,
        merged,
        resident_function_symbols=resident_function_symbols,
        existing_exports={
            *external_exports,
            *auto_resident_globals,
        },
    )
    (
        graph_external_exports,
        graph_direct_dependencies,
        graph_inline_proxies,
    ) = (
        _infer_graph_resident_exports(
            graph,
            merged,
            kernel_root=kernel,
            resident_function_symbols=resident_function_symbols,
            existing_exports={
                *external_exports,
                *auto_resident_globals,
                *auto_external_exports,
            },
            redirected_exact_symbols={
                *resident_function_symbols,
                *auto_resident_globals,
                *external_exports,
                *auto_external_exports,
                *explicit_inline_proxies,
            }
            - set(direct_dependencies),
        )
    )
    trace_inline_proxies = _infer_trace_inline_proxies(
        merged,
        resident_symbols={
            *resident_function_symbols,
            *auto_resident_globals,
        },
        inferred_dependencies={
            *auto_external_exports,
            *graph_external_exports,
        },
    )
    auto_inline_proxies = list(
        dict.fromkeys(
            [*graph_inline_proxies, *trace_inline_proxies]
        )
    )
    all_inline_proxies = list(
        dict.fromkeys(
            [*explicit_inline_proxies, *auto_inline_proxies]
        )
    )
    redirected_tracepoint_internals = _tracepoint_internals(
        all_inline_proxies
    )
    auto_external_exports = [
        symbol
        for symbol in auto_external_exports
        if symbol not in redirected_tracepoint_internals
    ]
    graph_external_exports = [
        symbol
        for symbol in graph_external_exports
        if symbol not in redirected_tracepoint_internals
    ]
    graph_direct_dependencies = [
        symbol
        for symbol in graph_direct_dependencies
        if symbol not in redirected_tracepoint_internals
    ]
    auto_external_exports = list(
        dict.fromkeys(
            [*auto_external_exports, *graph_external_exports]
        )
    )
    auto_direct_dependencies = list(
        dict.fromkeys(graph_direct_dependencies)
    )
    if expand_private_source_closure:
        inline_proxy_set = set(all_inline_proxies)
        for closure_report in closure_reports:
            moved = closure_report.get("moved")
            if not isinstance(moved, dict):
                continue
            functions = moved.get("functions")
            if isinstance(functions, list):
                moved["functions"] = [
                    symbol
                    for symbol in functions
                    if symbol not in inline_proxy_set
                ]
        closure_path = (
            Path(source_closure_output)
            if source_closure_output is not None
            else Path(extraction_output).with_name("source-closure.json")
        )
        write_json_atomic(
            closure_path,
            {
                "schema_version": 1,
                "candidate_id": candidate_id,
                "merged_candidate_ids": list(merged_candidate_ids),
                "excluded_interface_ids": excluded_interface_ids,
                "excluded_interface_symbols": [
                    graph.nodes[item].symbol
                    for item in excluded_interface_ids
                ],
                "promoted_callback_tables": [
                    graph.nodes[item].symbol for item in callback_table_ids
                ],
                "promoted_callback_interfaces": [
                    graph.nodes[item].symbol
                    for item in promoted_interface_ids
                ],
                "translation_units": closure_reports,
                "summary": {
                    "moved_functions": sum(
                        len(value["moved"]["functions"])
                        for value in closure_reports
                    ),
                    "moved_globals": sum(
                        len(value["moved"]["globals"])
                        for value in closure_reports
                    ),
                    "auto_duplicate_functions": len(
                        auto_duplicate_functions
                    ),
                    "auto_resident_functions": len(
                        auto_resident_functions
                    ),
                    "auto_resident_globals": len(auto_resident_globals),
                    "auto_external_resident_exports": len(
                        auto_external_exports
                    ),
                    "auto_resident_inline_proxies": len(
                        auto_inline_proxies
                    ),
                    "auto_direct_resident_dependencies": len(
                        auto_direct_dependencies
                    ),
                    "llvm_graph_coverage_complete": all(
                        value["llvm_graph_coverage_complete"]
                        for value in closure_reports
                    ),
                },
                "auto_resident_inline_proxies": auto_inline_proxies,
                "redirected_tracepoint_internals": sorted(
                    redirected_tracepoint_internals
                ),
            },
        )

    write_json_atomic(extraction_output, merged)
    # Reloading verifies all exact AST ranges against the current kernel tree.
    load_source_extraction(extraction_output)

    interface_symbols = [graph.nodes[item].symbol for item in interface_ids]
    if len(set(interface_symbols)) != len(interface_symbols):
        raise GraphValidationError(
            "candidate has ambiguous same-named interfaces; split the "
            "candidate or use linkage-aware backend identities"
        )
    failures = _resolve_failure_expressions(
        failure_expressions or {}, graph, interface_ids
    )
    write_json_atomic(
        backend_policy_output,
        {
            "schema_version": 1,
            "module_name": module_name,
            "kernel_root": str(kernel),
            "interfaces": interface_symbols,
            "failure_expressions": failures,
            "duplicate_functions": [
                graph.nodes[item].symbol for item in duplicate_ids
            ]
            + auto_duplicate_functions,
            "resident_exports": [
                graph.nodes[item].symbol for item in resident_export_ids
            ]
            + auto_resident_functions,
            "external_resident_exports": list(
                dict.fromkeys(
                    [
                        *external_exports,
                        *auto_resident_globals,
                        *auto_external_exports,
                    ]
                )
            ),
            "resident_inline_proxies": all_inline_proxies,
            "pack_resident_dependencies": (
                pack_resident_dependencies
                or bool(
                    auto_resident_functions
                    or auto_resident_globals
                    or auto_external_exports
                    or all_inline_proxies
                )
            ),
            "initialize_packed_dependencies_at_load": (
                initialize_packed_dependencies_at_load
            ),
            "bind_packed_dependencies_on_first_use": (
                bind_packed_dependencies_on_first_use
            ),
            "direct_resident_dependencies": list(
                dict.fromkeys(
                    [*direct_dependencies, *auto_direct_dependencies]
                )
            ),
            "module_defines": dict(module_defines or {}),
            "resident_ro_after_init_globals": _unique_strings(
                resident_ro_after_init_globals,
                context="resident ro-after-init global",
            ),
            "autoload_alias": autoload_alias,
        },
    )


def _infer_trace_inline_proxies(
    extraction: Mapping[str, Any],
    *,
    resident_symbols: set[str],
    inferred_dependencies: set[str],
) -> list[str]:
    """Redirect exact trace wrapper calls through the packed ABI.

    Linux trace event headers define ``trace_<event>`` as static inline
    functions. Moving a caller into a module otherwise exposes the wrapper's
    private ``__tracepoint_*`` and ``__traceiter_*`` implementation details.
    When both implementation symbols appear in the conservative LLVM
    boundary and the wrapper call has an exact main-file AST range, keep an
    addressable copy of the inline wrapper in the resident translation unit
    and pass its function pointer through the dependency table instead.
    """

    entities = [
        *extraction.get("functions", []),
        *extraction.get("globals", []),
    ]
    wrappers: set[str] = set()
    for item in entities:
        if not isinstance(item, Mapping):
            continue
        symbol = item.get("symbol")
        if symbol in resident_symbols:
            continue
        references = item.get("references", [])
        if not isinstance(references, list):
            continue
        for reference in references:
            if not isinstance(reference, Mapping):
                continue
            wrapper = reference.get("symbol")
            if (
                reference.get("kind") == "function"
                and isinstance(wrapper, str)
                and wrapper.startswith("trace_")
                and len(wrapper) > len("trace_")
            ):
                wrappers.add(wrapper)

    result = []
    for wrapper in sorted(wrappers):
        event = wrapper.removeprefix("trace_")
        internals = {
            "__tracepoint_" + event,
            "__traceiter_" + event,
        }
        if internals.issubset(inferred_dependencies):
            result.append(wrapper)
    return result


def _tracepoint_internals(inline_proxies: Sequence[str]) -> set[str]:
    """Return graph-only trace internals replaced by resident wrappers."""

    result: set[str] = set()
    for wrapper in inline_proxies:
        if not wrapper.startswith("trace_") or len(wrapper) <= len("trace_"):
            continue
        event = wrapper.removeprefix("trace_")
        result.update(
            {
                "__tracepoint_" + event,
                "__traceiter_" + event,
            }
        )
    return result


def _infer_external_resident_exports(
    graph: ReferenceGraph,
    extraction: Mapping[str, Any],
    *,
    resident_function_symbols: set[str],
    existing_exports: set[str],
) -> list[str]:
    """Infer MODPOST boundaries from AST uses and LLVM symbol facts.

    Only source-defined, external-linkage entities that are absent from the
    kernel export surface qualify. Header inline helpers, compiler builtins,
    declarations without a configured definition and ambiguous internal
    symbols are deliberately ignored.
    """

    if not _export_surface_is_complete(graph):
        # Absence of an ``exported`` attribute is meaningful only after the
        # graph has been reconciled with the configured kernel's symvers.
        # A raw LLVM graph cannot distinguish an unexported definition from
        # an export surface that simply was not imported yet.
        return []

    functions = extraction.get("functions", [])
    globals_ = extraction.get("globals", [])
    if not isinstance(functions, list) or not isinstance(globals_, list):
        raise GraphValidationError(
            "source extraction functions/globals must be arrays"
        )
    module_function_symbols = {
        item.get("symbol")
        for item in functions
        if isinstance(item, Mapping)
        and isinstance(item.get("symbol"), str)
        and item.get("symbol") not in resident_function_symbols
    }
    module_global_symbols = {
        item.get("symbol")
        for item in globals_
        if isinstance(item, Mapping)
        and isinstance(item.get("symbol"), str)
    }
    dependency_kinds: dict[str, set[EntityKind]] = {}
    for item in [*functions, *globals_]:
        if not isinstance(item, Mapping):
            continue
        symbol = item.get("symbol")
        if symbol not in module_function_symbols.union(
            module_global_symbols
        ):
            continue
        dependencies = item.get("dependencies")
        if not isinstance(dependencies, Mapping):
            continue
        for key, kind in (
            ("functions", EntityKind.FUNCTION),
            ("globals", EntityKind.GLOBAL),
        ):
            values = dependencies.get(key, [])
            if not isinstance(values, list):
                continue
            for dependency in values:
                if isinstance(dependency, str) and dependency:
                    dependency_kinds.setdefault(dependency, set()).add(
                        kind
                    )

    inferred = []
    module_symbols = module_function_symbols.union(module_global_symbols)
    for symbol, kinds in sorted(dependency_kinds.items()):
        if (
            symbol in module_symbols
            or symbol in resident_function_symbols
            or symbol in existing_exports
            or len(kinds) != 1
        ):
            continue
        kind = next(iter(kinds))
        matches = [
            node
            for node in graph.nodes.values()
            if node.symbol == symbol
            and node.kind is kind
            and _is_unexported_source_definition(node)
        ]
        if len(matches) != 1:
            continue
        inferred.append(symbol)
    return inferred


def _same_unit_macro_generated_dependencies(
    extraction: Mapping[str, Any],
    *,
    graph: ReferenceGraph,
    translation_unit: str,
) -> tuple[list[str], list[str]]:
    """Find AST dependencies whose same-TU definition was macro-spelled.

    ``--all-main-*`` intentionally skips declarations whose name originates
    in a macro expansion. Linux uses those forms for persistent objects such
    as ``DEFINE_MUTEX`` and ``DECLARE_WAIT_QUEUE_HEAD``. Their references are
    still present in AST dependency lists and in the configured LLVM graph,
    so rerunning SourceExtractor with an explicit selector recovers the exact
    type and declaration range instead of waiting for a generated module to
    fail with an undeclared identifier.
    """

    functions = extraction.get("functions", [])
    globals_ = extraction.get("globals", [])
    if not isinstance(functions, list) or not isinstance(globals_, list):
        raise GraphValidationError(
            "source extraction functions/globals must be arrays"
        )
    known = {
        EntityKind.FUNCTION: {
            item.get("symbol")
            for item in functions
            if isinstance(item, Mapping)
        },
        EntityKind.GLOBAL: {
            item.get("symbol")
            for item in globals_
            if isinstance(item, Mapping)
        },
    }
    dependencies = {
        EntityKind.FUNCTION: set(),
        EntityKind.GLOBAL: set(),
    }
    for item in [*functions, *globals_]:
        if not isinstance(item, Mapping):
            continue
        facts = item.get("dependencies")
        if not isinstance(facts, Mapping):
            continue
        for key, kind in (
            ("functions", EntityKind.FUNCTION),
            ("globals", EntityKind.GLOBAL),
        ):
            values = facts.get(key, [])
            if isinstance(values, list):
                dependencies[kind].update(
                    value
                    for value in values
                    if isinstance(value, str) and value
                )

    result: dict[EntityKind, set[str]] = {
        EntityKind.FUNCTION: set(),
        EntityKind.GLOBAL: set(),
    }
    same_unit_definitions = {
        (node.kind, node.symbol)
        for node in graph.nodes.values()
        if node.kind in {EntityKind.FUNCTION, EntityKind.GLOBAL}
        and "definition" in node.attributes
        and not node.attributes.intersection(
            {"__always_inline", "inline_hint"}
        )
        # Header inline definitions inherit the consuming translation-unit
        # identity in LLVM. Only a main-file source path proves that an
        # explicit SourceExtractor selector can recover the declaration.
        and node.source_path == translation_unit
    }
    for kind in (EntityKind.FUNCTION, EntityKind.GLOBAL):
        missing = dependencies[kind] - known[kind]
        result[kind].update(
            symbol
            for symbol in missing
            if (kind, symbol) in same_unit_definitions
        )
    return (
        sorted(result[EntityKind.FUNCTION]),
        sorted(result[EntityKind.GLOBAL]),
    )


def _infer_graph_resident_exports(
    graph: ReferenceGraph,
    extraction: Mapping[str, Any],
    *,
    kernel_root: str | Path,
    resident_function_symbols: set[str],
    existing_exports: set[str],
    redirected_exact_symbols: set[str],
) -> tuple[list[str], list[str], list[str]]:
    """Recover link dependencies visible only after header/macro inlining.

    AST references provide exact source ranges and can be redirected through
    the packed table. LLVM may additionally show references introduced by a
    header inline or macro expansion whose spelling is outside the main file.
    A graph-only dependency normally needs a direct export. When it is below
    an addressable header-inline function whose call has an exact AST range,
    however, redirect that outer call through the packed dependency table and
    keep the whole inline implementation resident instead.
    """

    if not _export_surface_is_complete(graph):
        return [], [], []
    functions = extraction.get("functions", [])
    globals_ = extraction.get("globals", [])
    if not isinstance(functions, list) or not isinstance(globals_, list):
        raise GraphValidationError(
            "source extraction functions/globals must be arrays"
        )

    root = Path(kernel_root).resolve()
    module_entities: list[Mapping[str, Any]] = []
    module_symbols: set[str] = set()
    exact_reference_symbols: set[str] = set()
    for item, is_function in [
        *((item, True) for item in functions),
        *((item, False) for item in globals_),
    ]:
        if not isinstance(item, Mapping):
            continue
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue
        if is_function and symbol in resident_function_symbols:
            continue
        module_entities.append(item)
        module_symbols.add(symbol)
        references = item.get("references", [])
        if isinstance(references, list):
            exact_reference_symbols.update(
                reference.get("symbol")
                for reference in references
                if isinstance(reference, Mapping)
                and isinstance(reference.get("symbol"), str)
            )

    source_node_ids: set[str] = set()
    for item in module_entities:
        symbol = item["symbol"]
        source_path = item.get("source_path")
        if not isinstance(source_path, str):
            continue
        try:
            unit = Path(source_path).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        symbols = {symbol}
        if symbol.startswith("__se_compat_sys_"):
            symbols.add(
                "__do_compat_sys_"
                + symbol.removeprefix("__se_compat_sys_")
            )
        elif symbol.startswith("__se_sys_"):
            symbols.add("__do_sys_" + symbol.removeprefix("__se_sys_"))
        source_node_ids.update(
            node.id
            for node in graph.nodes.values()
            if node.symbol in symbols
            and (
                node.source_path == unit
                or node.translation_unit == unit
            )
        )

    dependency_kinds = {
        EdgeKind.DIRECT_CALL,
        EdgeKind.ADDRESS_TAKEN,
        EdgeKind.GLOBAL_READ,
        EdgeKind.GLOBAL_WRITE,
        EdgeKind.GLOBAL_INITIALIZER,
    }

    def is_inline_definition(node: Any) -> bool:
        return (
            node.kind is EntityKind.FUNCTION
            and "definition" in node.attributes
            and bool(
                node.attributes.intersection(
                    {"__always_inline", "inline_hint"}
                )
            )
        )

    boundary_memo: dict[str, bool] = {}

    def reaches_unexported_boundary(
        node_id: str,
        visiting: set[str] | None = None,
    ) -> bool:
        cached = boundary_memo.get(node_id)
        if cached is not None:
            return cached
        active = set() if visiting is None else set(visiting)
        if node_id in active:
            return False
        active.add(node_id)
        for edge in graph.outgoing(node_id, kinds=dependency_kinds):
            target = graph.nodes[edge.target]
            if (
                target.symbol in module_symbols
                or target.symbol in resident_function_symbols
                or target.symbol in existing_exports
            ):
                continue
            if _is_unexported_source_definition(target):
                boundary_memo[node_id] = True
                return True
            if (
                edge.kind is EdgeKind.DIRECT_CALL
                and is_inline_definition(target)
                and reaches_unexported_boundary(target.id, active)
            ):
                boundary_memo[node_id] = True
                return True
        boundary_memo[node_id] = False
        return False

    analysis_node_ids = set(source_node_ids)
    pending_inline_owners = list(source_node_ids)
    inline_proxies: set[str] = set()
    while pending_inline_owners:
        source_id = pending_inline_owners.pop()
        for edge in graph.outgoing(
            source_id, kinds={EdgeKind.DIRECT_CALL}
        ):
            target = graph.nodes[edge.target]
            if not is_inline_definition(target):
                continue
            if target.symbol in exact_reference_symbols:
                if target.symbol in redirected_exact_symbols:
                    continue
                if (
                    "__always_inline" not in target.attributes
                    and reaches_unexported_boundary(target.id)
                ):
                    inline_proxies.add(target.symbol)
                    continue
            if target.id in analysis_node_ids:
                continue
            analysis_node_ids.add(target.id)
            pending_inline_owners.append(target.id)
    inferred: set[str] = set()
    direct: set[str] = set()
    for source_id in analysis_node_ids:
        for edge in graph.outgoing(source_id, kinds=dependency_kinds):
            target = graph.nodes[edge.target]
            if (
                target.symbol in module_symbols
                or target.symbol in resident_function_symbols
                or target.symbol in existing_exports
                or not _is_unexported_source_definition(target)
            ):
                continue
            inferred.add(target.symbol)
            if target.symbol not in exact_reference_symbols:
                direct.add(target.symbol)
    return sorted(inferred), sorted(direct), sorted(inline_proxies)


def _export_surface_is_complete(graph: ReferenceGraph) -> bool:
    enrichment = graph.metadata.get("symbol_size_enrichment")
    exported_nodes = (
        enrichment.get("exported_nodes")
        if isinstance(enrichment, Mapping)
        else None
    )
    return isinstance(exported_nodes, int) and not isinstance(
        exported_nodes, bool
    )


def _is_unexported_source_definition(node: Any) -> bool:
    return (
        node.kind in {EntityKind.FUNCTION, EntityKind.GLOBAL}
        and node.linkage is Linkage.EXTERNAL
        and node.source_path is not None
        and "definition" in node.attributes
        and not node.attributes.intersection(
            {
                "exported",
                "shared_api",
                "__always_inline",
                "inline_hint",
            }
        )
    )


def _resolve_failure_expressions(
    expressions: Mapping[str, str],
    graph: ReferenceGraph,
    interface_ids: list[str],
) -> dict[str, str]:
    result = {}
    by_symbol = {graph.nodes[item].symbol: item for item in interface_ids}
    for selector, expression in expressions.items():
        if not isinstance(selector, str) or not isinstance(expression, str):
            raise GraphValidationError(
                "failure expressions must map strings to strings"
            )
        if selector in interface_ids:
            symbol = graph.nodes[selector].symbol
        elif selector in by_symbol:
            symbol = selector
        else:
            raise GraphValidationError(
                f"failure expression selector is not an interface: "
                f"{selector!r}"
            )
        result[symbol] = expression
    return result


def _validate_extraction_readiness(
    candidate: Mapping[str, Any],
    *,
    candidate_id: str,
    primary: bool,
) -> None:
    """Prevent composite extraction from bypassing planner safety gates.

    A primary candidate must already be READY.  A merged compatibility
    companion may still lack machine-code size data because the real AST,
    Kbuild and size gates replace that estimate.  Phase or execution-context
    uncertainty is never repairable merely by putting two candidates in one
    module.
    """

    readiness = candidate.get("readiness")
    if readiness == "READY":
        return
    if primary or readiness != "NEEDS_EVIDENCE":
        required = "READY" if primary else "READY/size-only NEEDS_EVIDENCE"
        raise GraphValidationError(
            f"candidate {candidate_id} is not eligible for extraction; "
            f"required {required}, got {readiness!r}"
        )

    reasons = _string_array(candidate, "readiness_reasons")
    allowed_reasons = {"missing function size data"}
    unsupported = sorted(set(reasons) - allowed_reasons)
    executable_edges = candidate.get("executable_interface_edges")
    load_safe_edges = candidate.get("load_safe_interface_edges")
    phase = candidate.get("loader_phase_classification")
    if (
        not reasons
        or unsupported
        or phase != "LAZY_READY"
        or not isinstance(executable_edges, int)
        or isinstance(executable_edges, bool)
        or executable_edges <= 0
        or not isinstance(load_safe_edges, int)
        or isinstance(load_safe_edges, bool)
        or load_safe_edges != executable_edges
    ):
        details = ", ".join(unsupported) if unsupported else repr(reasons)
        raise GraphValidationError(
            f"merged candidate {candidate_id} has evidence gaps that a "
            "composite source closure cannot repair: "
            f"reasons={details}, phase={phase!r}, "
            f"load_safe_edges={load_safe_edges!r}/{executable_edges!r}"
        )


def _resolve_function_selectors(
    graph: ReferenceGraph,
    selectors: Sequence[str],
    *,
    context: str,
) -> list[str]:
    """Resolve audited closure selectors without losing linkage identity."""

    result = []
    for selector in _unique_strings(selectors, context=context):
        if selector in graph.nodes:
            matches = [selector]
        else:
            matches = [
                node.id
                for node in graph.nodes.values()
                if node.kind is EntityKind.FUNCTION
                and node.symbol == selector
                and node.source_path
            ]
        if len(matches) != 1:
            raise GraphValidationError(
                f"{context} selector {selector!r} matched "
                f"{len(matches)} source functions; use a linkage-aware "
                "graph node id"
            )
        node_id = matches[0]
        node = graph.nodes[node_id]
        if node.kind is not EntityKind.FUNCTION:
            raise GraphValidationError(
                f"{context} selector is not a function: {selector!r}"
            )
        result.append(node_id)
    return result


def _resolve_global_selectors(
    graph: ReferenceGraph,
    selectors: Sequence[str],
    *,
    context: str,
) -> list[str]:
    """Resolve reviewed global selectors without collapsing linkage."""

    result = []
    for selector in _unique_strings(selectors, context=context):
        if selector in graph.nodes:
            matches = [selector]
        else:
            matches = [
                node.id
                for node in graph.nodes.values()
                if node.kind is EntityKind.GLOBAL
                and node.symbol == selector
                and node.source_path
            ]
        if len(matches) != 1:
            raise GraphValidationError(
                f"{context} selector {selector!r} matched "
                f"{len(matches)} source globals; use a linkage-aware "
                "graph node id"
            )
        node_id = matches[0]
        if graph.nodes[node_id].kind is not EntityKind.GLOBAL:
            raise GraphValidationError(
                f"{context} selector is not a global: {selector!r}"
            )
        result.append(node_id)
    return result


def _callback_table_interface_ids(
    graph: ReferenceGraph, table_ids: Sequence[str]
) -> list[str]:
    """Promote same-TU callbacks held by reviewed resident tables.

    The table itself remains resident and keeps pointing at generated lazy
    wrappers with the original function names. Only implementations behind
    same-translation-unit GLOBAL_INITIALIZER edges become module entries.
    This deliberately requires an explicit table selector: the graph alone
    cannot prove that every callback field is process-context load-safe.
    """

    result: list[str] = []
    for table_id in table_ids:
        table = graph.nodes[table_id]
        table_unit = table.translation_unit or table.source_path
        if not table_unit:
            raise GraphValidationError(
                f"promoted callback table lacks a source unit: {table_id}"
            )
        callbacks = []
        for edge in graph.outgoing(
            table_id, kinds={EdgeKind.GLOBAL_INITIALIZER}
        ):
            target = graph.nodes[edge.target]
            target_unit = target.translation_unit or target.source_path
            if (
                target.kind is EntityKind.FUNCTION
                and target.source_path
                and target_unit == table_unit
                and "definition" in target.attributes
            ):
                callbacks.append(target.id)
        callbacks = list(dict.fromkeys(sorted(callbacks)))
        if not callbacks:
            raise GraphValidationError(
                "promoted callback table has no same-source function "
                f"initializers: {table.symbol}"
            )
        result.extend(callbacks)
    return list(dict.fromkeys(result))


def _resolve_interface_selectors(
    graph: ReferenceGraph,
    interface_ids: Sequence[str],
    selectors: Sequence[str],
) -> list[str]:
    """Resolve an audited subset only within the selected interfaces."""

    allowed = set(interface_ids)
    result: list[str] = []
    for selector in _unique_strings(
        selectors, context="excluded interface"
    ):
        if selector in allowed:
            matches = [selector]
        else:
            matches = [
                node_id
                for node_id in interface_ids
                if graph.nodes[node_id].symbol == selector
            ]
        if len(matches) != 1:
            raise GraphValidationError(
                f"excluded interface selector {selector!r} matched "
                f"{len(matches)} candidate interfaces; use a "
                "linkage-aware graph node id"
            )
        if matches[0] not in result:
            result.append(matches[0])
    return result


def _unique_strings(
    values: Sequence[str],
    *,
    context: str,
) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value:
            raise GraphValidationError(
                f"{context} selectors must be non-empty strings"
            )
        if value in seen:
            raise GraphValidationError(
                f"duplicate {context} selector: {value!r}"
            )
        seen.add(value)
        result.append(value)
    return result


def _node_source(
    source_path: str | None, kernel_root: Path, node_id: str
) -> Path:
    if not source_path:
        raise GraphValidationError(
            f"graph node {node_id} has no source_path"
        )
    source = Path(source_path)
    if not source.is_absolute():
        source = kernel_root / source
    source = source.resolve()
    try:
        source.relative_to(kernel_root)
    except ValueError as error:
        raise GraphValidationError(
            f"graph node {node_id} source is outside kernel root: {source}"
        ) from error
    if not source.is_file():
        raise GraphValidationError(
            f"graph node {node_id} source is missing: {source}"
        )
    return source


def _load_object(
    path: str | Path, context: str
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


def _string_array(value: Mapping[str, Any], key: str) -> list[str]:
    result = value.get(key)
    if not isinstance(result, list) or not all(
        isinstance(item, str) and item for item in result
    ):
        raise GraphValidationError(
            f"candidate.{key} must be an array of node ids"
        )
    return list(result)

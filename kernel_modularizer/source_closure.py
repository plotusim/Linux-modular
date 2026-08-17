"""Conservative AST closure expansion checked by the LLVM reference graph."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .errors import GraphValidationError
from .model import EdgeKind, EntityKind, ReferenceGraph


_NON_OWNERSHIP_EDGES = frozenset(
    {EdgeKind.INDIRECT_CALL, EdgeKind.UNRESOLVED_CALL}
)
_NON_MOVABLE_GLOBAL_ATTRIBUTES = frozenset(
    {"section", "cleanup", "alias", "weak", "retain", "used", "tls_model"}
)
_NON_MOVABLE_GLOBAL_MACROS = frozenset(
    {
        "__initdata",
        "__initconst",
        "__exitdata",
        "__meminitdata",
        "__memexitdata",
        "__ro_after_init",
        "__read_mostly",
    }
)


@dataclass(frozen=True)
class SourceClosureSelection:
    """The movable side of one source unit and its resident cut set."""

    extraction: Mapping[str, Any]
    auto_duplicate_functions: tuple[str, ...]
    auto_resident_functions: tuple[str, ...]
    auto_resident_globals: tuple[str, ...]
    report: Mapping[str, Any]


def select_private_source_closure(
    payload: Mapping[str, Any],
    *,
    graph: ReferenceGraph,
    translation_unit: str,
    seed_functions: Sequence[str],
    seed_globals: Sequence[str] = (),
    protected_functions: Sequence[str] = (),
    protected_symbols: Sequence[str] = (),
    selected_owner_node_ids: Sequence[str] = (),
    resident_wrapper_functions: Sequence[str] = (),
    require_graph_coverage: bool = True,
) -> SourceClosureSelection:
    """Expand seeds through same-file definitions and cut shared entities.

    The AST supplies exact source dependencies.  Incoming LLVM graph edges
    catch users in other translation units and macro-generated definitions,
    which ``--all-main-*`` discovery cannot see.  Indirect-call target edges
    are deliberately not ownership edges: a generic dispatcher does not hold
    a link-time reference to every callback target.  The callback's address
    owner (normally a global initializer) is checked separately.
    """

    graph.validate()
    unit = _normalize_unit(translation_unit)
    trusted_owner_ids = set(
        _unique(selected_owner_node_ids, "selected owner node")
    )
    missing_owner_ids = sorted(trusted_owner_ids - set(graph.nodes))
    if missing_owner_ids:
        raise GraphValidationError(
            "selected source-closure owners are absent from the LLVM "
            "reference graph: " + ", ".join(missing_owner_ids)
        )
    invalid_owner_ids = sorted(
        node_id
        for node_id in trusted_owner_ids
        if graph.nodes[node_id].kind
        not in {EntityKind.FUNCTION, EntityKind.GLOBAL}
    )
    if invalid_owner_ids:
        raise GraphValidationError(
            "selected source-closure owners must be functions or globals: "
            + ", ".join(invalid_owner_ids)
        )
    functions = _entities(payload, "functions")
    globals_ = _entities(payload, "globals")
    entities = {
        **{("function", name): value for name, value in functions.items()},
        **{("global", name): value for name, value in globals_.items()},
    }
    seeds = {
        *(("function", name) for name in _unique(seed_functions, "seed function")),
        *(("global", name) for name in _unique(seed_globals, "seed global")),
    }
    missing_seeds = sorted(_display(key) for key in seeds - entities.keys())
    if missing_seeds:
        raise GraphValidationError(
            "source closure seeds are absent from SourceExtractor output: "
            + ", ".join(missing_seeds)
        )

    protected_function_names = set(
        _unique(protected_functions, "protected function")
    )
    wrapper_function_names = set(
        _unique(resident_wrapper_functions, "resident wrapper function")
    )
    unknown_wrappers = sorted(wrapper_function_names - set(functions))
    if unknown_wrappers:
        raise GraphValidationError(
            "resident wrapper functions are absent from SourceExtractor "
            "output: " + ", ".join(unknown_wrappers)
        )
    protected_names = set(_unique(protected_symbols, "protected symbol"))
    explicit_boundaries = {
        key
        for key in entities
        if (
            (key[0] == "function" and key[1] in protected_function_names)
            or key[1] in protected_names
        )
    }
    source_address_taken_globals = {
        name
        for value in entities.values()
        for name in _optional_dependency_names(
            value.get("dependencies"), "address_taken_globals"
        )
        if name in globals_
    }
    intrinsic_reasons: dict[tuple[str, str], str] = {}
    for name, value in functions.items():
        key = ("function", name)
        if _must_remain_resident_function(value):
            intrinsic_reasons[key] = "weak_linkage"
        elif (
            name not in wrapper_function_names
            and _has_escaped_function_address(graph, unit, key)
        ):
            intrinsic_reasons[key] = "escaped_function_address"
    for name in globals_:
        key = ("global", name)
        if name in source_address_taken_globals:
            # Storing a global object's address in a VMA, callback owner or
            # another long-lived resident object can outlive the temporary
            # module reference held by the lazy function wrapper.  LLVM's
            # ordinary GLOBAL_READ edge does not encode that publication,
            # so use the exact Clang unary-& fact as a conservative frontier.
            state_reason = "source_address_taken_global"
        elif globals_[name].get("source_form") == "macro_declaration_group":
            # One macro invocation may define several variables and helper
            # functions. Until that complete co-definition group is modeled,
            # keep the invocation resident instead of moving only one AST
            # declaration and silently splitting its ownership.
            state_reason = "macro_generated_declaration_group"
        else:
            state_reason = _non_movable_global_reason(globals_[name])
            if state_reason is None:
                state_reason = _module_lifetime_state_reason(
                    graph, unit, key
                )
        if state_reason is not None:
            intrinsic_reasons[key] = state_reason
    intrinsic_boundaries = set(intrinsic_reasons)
    if seeds.intersection(explicit_boundaries):
        raise GraphValidationError(
            "source closure seeds cannot also be protected resident entities"
        )
    intrinsic_seeds = sorted(
        _display(key) for key in seeds.intersection(intrinsic_boundaries)
    )
    if intrinsic_seeds:
        raise GraphValidationError(
            "source closure seeds have non-movable linkage/attributes: "
            + ", ".join(intrinsic_seeds)
        )

    dependency_map = {
        key: _local_dependencies(value, functions, globals_)
        for key, value in entities.items()
    }
    users: dict[tuple[str, str], set[tuple[str, str]]] = {
        key: set() for key in entities
    }
    for owner, dependencies in dependency_map.items():
        for dependency in dependencies:
            users[dependency].add(owner)

    full_reach = _closure(seeds, dependency_map, frozenset())
    dynamic_boundaries: set[tuple[str, str]] = set()
    evidence: dict[tuple[str, str], dict[str, Any]] = {}

    while True:
        boundaries = explicit_boundaries.union(
            intrinsic_boundaries, dynamic_boundaries
        )
        reach = _closure(seeds, dependency_map, boundaries)
        selected_graph_ids = _selected_graph_ids(
            graph, unit, reach, seed_functions
        )
        selected_graph_ids.update(trusted_owner_ids)
        discovered: set[tuple[str, str]] = set()
        for key in sorted(reach - seeds):
            ast_users = sorted(
                _display(owner) for owner in users[key] - reach
            )
            graph_users = _outside_graph_users(
                graph,
                unit,
                key,
                selected_graph_ids,
            )
            if not ast_users and not graph_users:
                continue
            discovered.add(key)
            item = evidence.setdefault(
                key,
                {
                    "kind": key[0],
                    "symbol": key[1],
                    "ast_users": [],
                    "graph_users": [],
                },
            )
            item["ast_users"] = sorted(
                set(item["ast_users"]).union(ast_users)
            )
            by_edge = {
                (
                    value["source_id"],
                    value["edge_kind"],
                    value["target_id"],
                ): value
                for value in [*item["graph_users"], *graph_users]
            }
            item["graph_users"] = [by_edge[key] for key in sorted(by_edge)]
        new_boundaries = discovered - dynamic_boundaries
        if not new_boundaries:
            break
        dynamic_boundaries.update(new_boundaries)

    boundaries = explicit_boundaries.union(
        intrinsic_boundaries, dynamic_boundaries
    )
    reach = _closure(seeds, dependency_map, boundaries)
    live_boundaries = {
        dependency
        for owner in reach
        for dependency in dependency_map[owner]
        if dependency in boundaries
    }
    auto_live = live_boundaries - explicit_boundaries
    auto_duplicates = {
        key
        for key in auto_live
        if key[0] == "function"
        and _is_static_inline(functions[key[1]])
        and dependency_map[key].issubset(reach)
        and not _has_unextracted_local_dependencies(
            functions[key[1]], graph, unit, functions, globals_
        )
    }
    auto_resident_live = auto_live - auto_duplicates

    missing_graph = sorted(
        _display(key)
        for key in reach
        if not _matching_graph_nodes(graph, unit, key)
    )
    if require_graph_coverage and missing_graph:
        raise GraphValidationError(
            "LLVM reference graph does not cover the selected source "
            "closure: " + ", ".join(missing_graph)
        )

    selected_function_names = {
        name for kind, name in reach if kind == "function"
    }
    selected_function_names.update(
        name for name in protected_function_names if name in functions
    )
    selected_function_names.update(
        name for kind, name in auto_live if kind == "function"
    )
    selected_global_names = {
        name for kind, name in reach if kind == "global"
    }
    selected_global_names.update(
        name for kind, name in auto_live if kind == "global"
    )

    filtered = dict(payload)
    filtered["functions"] = _filtered_entities(
        functions, selected_function_names
    )
    filtered["globals"] = _filtered_entities(
        globals_, selected_global_names
    )

    external_functions, external_globals = _external_dependencies(
        reach, dependency_map, entities
    )
    frontier_report = [
        {
            **evidence.get(
                key,
                {
                    "kind": key[0],
                    "symbol": key[1],
                    "ast_users": [],
                    "graph_users": [],
                },
            ),
            "live_dependency": key in live_boundaries,
            "explicit": key in explicit_boundaries,
            "intrinsic_resident": key in intrinsic_boundaries,
            "intrinsic_resident_reason": intrinsic_reasons.get(key),
            "resolution": (
                "duplicate_into_module"
                if key in auto_duplicates
                else "explicit_resident"
                if key in explicit_boundaries
                else "resident_dependency"
            ),
        }
        for key in sorted(boundaries)
        if key in full_reach
    ]
    report = {
        "schema_version": 1,
        "analysis": "ast-forward-closure+llvm-incoming-ownership",
        "translation_unit": unit,
        "seeds": {
            "functions": sorted(seed_functions),
            "globals": sorted(seed_globals),
        },
        "composite_owner_node_ids": sorted(trusted_owner_ids),
        "resident_wrapper_functions": sorted(wrapper_function_names),
        "all_reachable": {
            "functions": sorted(
                name for kind, name in full_reach if kind == "function"
            ),
            "globals": sorted(
                name for kind, name in full_reach if kind == "global"
            ),
        },
        "moved": {
            "functions": sorted(
                name for kind, name in reach if kind == "function"
            ),
            "globals": sorted(
                name for kind, name in reach if kind == "global"
            ),
        },
        "resident_frontier": frontier_report,
        "auto_duplicate_functions": sorted(
            name for kind, name in auto_duplicates
        ),
        "auto_resident_functions": sorted(
            name
            for kind, name in auto_resident_live
            if kind == "function"
        ),
        "auto_resident_globals": sorted(
            name for kind, name in auto_resident_live if kind == "global"
        ),
        "external_dependencies": {
            "functions": external_functions,
            "globals": external_globals,
        },
        "llvm_graph_missing": missing_graph,
        "llvm_graph_coverage_complete": not missing_graph,
    }
    return SourceClosureSelection(
        extraction=filtered,
        auto_duplicate_functions=tuple(
            report["auto_duplicate_functions"]
        ),
        auto_resident_functions=tuple(report["auto_resident_functions"]),
        auto_resident_globals=tuple(report["auto_resident_globals"]),
        report=report,
    )


def _entities(
    payload: Mapping[str, Any], key: str
) -> dict[str, Mapping[str, Any]]:
    values = payload.get(key)
    if not isinstance(values, list):
        raise GraphValidationError(f"SourceExtractor output.{key} must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise GraphValidationError(
                f"SourceExtractor output.{key}[{index}] must be an object"
            )
        symbol = value.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise GraphValidationError(
                f"SourceExtractor output.{key}[{index}].symbol is invalid"
            )
        previous = result.get(symbol)
        if previous is None or _definition_rank(value) > _definition_rank(
            previous
        ):
            result[symbol] = value
    return result


def _filtered_entities(
    values: Mapping[str, Mapping[str, Any]], selected_names: set[str]
) -> list[Mapping[str, Any]]:
    return sorted(
        (values[name] for name in selected_names),
        key=lambda value: (
            str(value.get("source_path", "")),
            int(value.get("start_offset", 0)),
        ),
    )


def _definition_rank(value: Mapping[str, Any]) -> tuple[int, int, int]:
    """Prefer a real initialized definition over a forward declaration."""

    return (
        int(bool(value.get("has_initializer", False))),
        len(str(value.get("source", ""))),
        int(value.get("start_offset", 0)),
    )


def _is_static_inline(value: Mapping[str, Any]) -> bool:
    if value.get("storage") != "static":
        return False
    attributes = value.get("attributes", [])
    macros = value.get("macros", [])
    return (
        isinstance(attributes, list)
        and isinstance(macros, list)
        and (
            "gnu_inline" in attributes
            or "inline" in attributes
            or "always_inline" in attributes
            or "inline" in macros
            or "__always_inline" in macros
        )
    )


def _must_remain_resident_function(value: Mapping[str, Any]) -> bool:
    """Return whether source linkage itself forbids moving the definition.

    A weak definition is an overridable link-time fallback. Moving that body
    into a module would freeze the fallback implementation and bypass a strong
    architecture definition selected by the final link. It is safe to keep the
    symbol resident and call the link-resolved address through the generated
    dependency boundary.
    """

    attributes = value.get("attributes", [])
    return isinstance(attributes, list) and "weak" in attributes


def _has_escaped_function_address(
    graph: ReferenceGraph,
    unit: str,
    key: tuple[str, str],
) -> bool:
    """Detect an address use not explained by an ordinary direct call.

    The LLVM fact stream may emit both ADDRESS_TAKEN and DIRECT_CALL for the
    same source/target pair. That pair is an ordinary synchronous call. An
    unmatched address fact can be stored in a work item, timer, RCU callback
    or another object whose lifetime outlives the lazy wrapper's module
    reference. Until a subsystem-specific lifetime pin is synthesized, the
    callback and its downstream source closure must remain resident.
    """

    for node_id in _matching_graph_nodes(graph, unit, key):
        direct_sources = {
            edge.source
            for edge in graph.incoming(
                node_id, kinds={EdgeKind.DIRECT_CALL}
            )
        }
        if any(
            edge.source not in direct_sources
            for edge in graph.incoming(
                node_id, kinds={EdgeKind.ADDRESS_TAKEN}
            )
        ):
            return True
    return False


def _module_lifetime_state_reason(
    graph: ReferenceGraph,
    unit: str,
    key: tuple[str, str],
) -> str | None:
    """Keep runtime-mutated state outside an empty-lifecycle module.

    The generated module currently has no subsystem-specific teardown or
    state-transfer hook.  Moving a writable private global would therefore
    reset it to its C initializer after every unload/reload cycle even when
    the surrounding resident subsystem still owns live state derived from
    it.  A configured LLVM GLOBAL_WRITE fact, or the escaped address of a
    mutable object such as a mutex/waitqueue, is sufficient to make that
    reset observable, so the value remains resident and is reached through
    the generated typed dependency boundary instead.

    Merely having a non-const ELF section is not enough: legacy Linux sources
    contain writable declarations that are never mutated after relocation.
    Requiring a real write/address edge preserves read-only-in-practice
    tables.
    """

    if key[0] != "global":
        return None
    nodes = [
        graph.nodes[node_id]
        for node_id in _matching_graph_nodes(graph, unit, key)
    ]
    if not any("mutable" in node.attributes for node in nodes):
        return None
    if any(
        True
        for node in nodes
        for _edge in graph.incoming(
            node.id, kinds={EdgeKind.GLOBAL_WRITE}
        )
    ):
        return "mutable_module_lifetime_state"
    if any(
        True
        for node in nodes
        for _edge in graph.incoming(
            node.id, kinds={EdgeKind.ADDRESS_TAKEN}
        )
    ):
        return "escaped_mutable_global_address"
    return None


def _non_movable_global_reason(value: Mapping[str, Any]) -> str | None:
    """Mirror backend source-marker safety as an ownership frontier.

    A private closure should become smaller when it reaches a linker-section,
    per-CPU, static-key or lifetime-marked object, rather than generating a
    bundle that the backend can only reject later.
    """

    attributes = value.get("attributes", [])
    macros = value.get("macros", [])
    if not isinstance(attributes, list) or not isinstance(macros, list):
        return "invalid_source_marker_metadata"
    markers = sorted(
        set(attributes).intersection(_NON_MOVABLE_GLOBAL_ATTRIBUTES)
        | set(macros).intersection(_NON_MOVABLE_GLOBAL_MACROS)
        | {
            macro
            for macro in macros
            if "PER_CPU" in macro or "STATIC_KEY" in macro
        }
    )
    if not markers:
        return None
    return "non_movable_source_marker:" + ",".join(markers)


def _local_dependencies(
    entity: Mapping[str, Any],
    functions: Mapping[str, Any],
    globals_: Mapping[str, Any],
) -> set[tuple[str, str]]:
    dependencies = entity.get("dependencies")
    if not isinstance(dependencies, Mapping):
        raise GraphValidationError(
            f"{entity.get('symbol', '<entity>')}: dependencies must be an object"
        )
    result = {
        ("function", name)
        for name in _dependency_names(dependencies, "functions")
        if name in functions
    }
    result.update(
        ("global", name)
        for name in _dependency_names(dependencies, "globals")
        if name in globals_
    )
    return result


def _has_unextracted_local_dependencies(
    entity: Mapping[str, Any],
    graph: ReferenceGraph,
    unit: str,
    functions: Mapping[str, Any],
    globals_: Mapping[str, Any],
) -> bool:
    """Detect same-TU definitions hidden from AST all-main discovery.

    Linux declaration macros can give a static global a macro expansion
    location. SourceExtractor deliberately excludes such definitions from
    all-main discovery, but function dependencies and the LLVM graph still
    expose their symbol. Duplicating an inline helper without that state would
    create an uncompilable or, worse, state-split module copy.
    """

    dependencies = entity.get("dependencies")
    if not isinstance(dependencies, Mapping):
        raise GraphValidationError(
            f"{entity.get('symbol', '<entity>')}: dependencies must be an object"
        )
    for kind, known, key_name in (
        ("function", functions, "functions"),
        ("global", globals_, "globals"),
    ):
        for name in _dependency_names(dependencies, key_name):
            if name in known:
                continue
            if _matching_graph_nodes(graph, unit, (kind, name)):
                return True
    return False


def _dependency_names(
    dependencies: Mapping[str, Any], key: str
) -> tuple[str, ...]:
    values = dependencies.get(key, [])
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value for value in values
    ):
        raise GraphValidationError(f"dependencies.{key} must be a string array")
    return tuple(values)


def _optional_dependency_names(
    dependencies: Any, key: str
) -> tuple[str, ...]:
    if not isinstance(dependencies, Mapping):
        return ()
    values = dependencies.get(key, [])
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value for value in values
    ):
        raise GraphValidationError(
            f"dependencies.{key} must be an array of non-empty strings"
        )
    return tuple(values)


def _closure(
    seeds: Iterable[tuple[str, str]],
    dependencies: Mapping[tuple[str, str], set[tuple[str, str]]],
    boundaries: Iterable[tuple[str, str]],
) -> set[tuple[str, str]]:
    blocked = set(boundaries)
    result = set(seeds)
    pending = list(result)
    while pending:
        owner = pending.pop()
        for dependency in dependencies[owner]:
            if dependency in blocked or dependency in result:
                continue
            result.add(dependency)
            pending.append(dependency)
    return result


def _matching_graph_nodes(
    graph: ReferenceGraph,
    unit: str,
    key: tuple[str, str],
) -> tuple[str, ...]:
    kind, symbol = key
    expected = EntityKind.FUNCTION if kind == "function" else EntityKind.GLOBAL
    return tuple(
        sorted(
            node.id
            for node in graph.nodes.values()
            if node.kind is expected
            and node.symbol == symbol
            and _node_belongs_to_unit(node.source_path, node.translation_unit, unit)
        )
    )


def _selected_graph_ids(
    graph: ReferenceGraph,
    unit: str,
    reach: Iterable[tuple[str, str]],
    seed_functions: Sequence[str],
) -> set[str]:
    result = {
        node_id
        for key in reach
        for node_id in _matching_graph_nodes(graph, unit, key)
    }
    synthetic = set()
    for symbol in seed_functions:
        if symbol.startswith("__se_compat_sys_"):
            synthetic.add("__do_compat_sys_" + symbol.removeprefix("__se_compat_sys_"))
        elif symbol.startswith("__se_sys_"):
            synthetic.add("__do_sys_" + symbol.removeprefix("__se_sys_"))
    result.update(
        node.id
        for node in graph.nodes.values()
        if node.symbol in synthetic
        and _node_belongs_to_unit(node.source_path, node.translation_unit, unit)
    )
    return result


def _outside_graph_users(
    graph: ReferenceGraph,
    unit: str,
    key: tuple[str, str],
    selected_graph_ids: set[str],
) -> list[dict[str, str]]:
    result = []
    for target_id in _matching_graph_nodes(graph, unit, key):
        for edge in graph.incoming(target_id):
            if edge.kind in _NON_OWNERSHIP_EDGES or edge.source in selected_graph_ids:
                continue
            source = graph.nodes[edge.source]
            result.append(
                {
                    "source_id": source.id,
                    "source_symbol": source.symbol,
                    "source_path": source.source_path or "",
                    "edge_kind": edge.kind.value,
                    "target_id": target_id,
                }
            )
    return sorted(
        result,
        key=lambda value: (
            value["source_id"], value["edge_kind"], value["target_id"]
        ),
    )


def _external_dependencies(
    reach: set[tuple[str, str]],
    local_dependencies: Mapping[tuple[str, str], set[tuple[str, str]]],
    entities: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    functions: set[str] = set()
    globals_: set[str] = set()
    for owner in reach:
        dependencies = entities[owner]["dependencies"]
        functions.update(_dependency_names(dependencies, "functions"))
        globals_.update(_dependency_names(dependencies, "globals"))
    functions.difference_update(name for kind, name in reach if kind == "function")
    globals_.difference_update(name for kind, name in reach if kind == "global")
    return sorted(functions), sorted(globals_)


def _node_belongs_to_unit(
    source_path: str | None,
    translation_unit: str | None,
    expected: str,
) -> bool:
    candidates = [value for value in (source_path, translation_unit) if value]
    return any(
        _normalize_unit(value) == expected
        or _normalize_unit(value).endswith("/" + expected)
        for value in candidates
    )


def _normalize_unit(value: str) -> str:
    normalized = str(PurePosixPath(value))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _unique(values: Sequence[str], context: str) -> tuple[str, ...]:
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value:
            raise GraphValidationError(f"{context} names must be non-empty strings")
        if value in seen:
            raise GraphValidationError(f"duplicate {context}: {value!r}")
        seen.add(value)
        result.append(value)
    return tuple(result)


def _display(key: tuple[str, str]) -> str:
    return f"{key[0]}:{key[1]}"

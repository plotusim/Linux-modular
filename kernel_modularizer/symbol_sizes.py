"""Enrich graph nodes with machine-code sizes from the analyzed build."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Mapping

from .errors import GraphValidationError
from .model import EntityKind, Linkage, ReferenceGraph


_FUNCTION_TYPES = frozenset("TtWwi")
_GLOBAL_TYPES = frozenset("BbDdGgRrSsVvCc")
_LINKER_FUNCTION_SECTION_PREFIXES = (".pci_fixup_",)
_LINKER_GLOBAL_SECTIONS = frozenset({".x86_cpu_dev.init"})


@dataclass(frozen=True)
class _LinkerRegistrations:
    functions: frozenset[str] = frozenset()
    globals: frozenset[str] = frozenset()

    def symbols_for(self, kind: EntityKind) -> frozenset[str]:
        if kind is EntityKind.FUNCTION:
            return self.functions
        if kind is EntityKind.GLOBAL:
            return self.globals
        return frozenset()


@dataclass(frozen=True)
class SizeEnrichmentResult:
    graph: ReferenceGraph
    matched: tuple[Mapping[str, Any], ...]
    exported_nodes: tuple[str, ...]
    linker_registered_nodes: tuple[str, ...]
    missing_nodes: tuple[str, ...]
    missing_objects: tuple[str, ...]

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "stage": "symbol-size-enrichment",
            "summary": {
                "matched_nodes": len(self.matched),
                "exported_nodes": len(self.exported_nodes),
                "linker_registered_nodes": len(
                    self.linker_registered_nodes
                ),
                "missing_nodes": len(self.missing_nodes),
                "missing_objects": len(self.missing_objects),
            },
            "matched": list(self.matched),
            "exported_nodes": list(self.exported_nodes),
            "linker_registered_nodes": list(
                self.linker_registered_nodes
            ),
            "missing_nodes": list(self.missing_nodes),
            "missing_objects": list(self.missing_objects),
        }


def enrich_graph_symbol_sizes(
    graph: ReferenceGraph,
    facts_manifest: str | Path,
    object_root: str | Path,
    *,
    nm: str | Path = "nm",
    objdump: str | Path = "objdump",
) -> SizeEnrichmentResult:
    graph.validate()
    root = Path(object_root).resolve()
    if not root.is_dir():
        raise GraphValidationError(
            f"kernel object root is not a directory: {root}"
        )
    translation_units = _load_translation_unit_objects(
        facts_manifest, root
    )
    symbol_cache: dict[Path, dict[str, tuple[str, int]]] = {}
    linker_registered_by_object: dict[Path, _LinkerRegistrations] = {}
    missing_objects = {
        relative
        for relative in translation_units.values()
        if not (root / relative).is_file()
    }
    exported_symbols = set()
    for relative_object in sorted(set(translation_units.values())):
        object_path = root / relative_object
        if not object_path.is_file():
            continue
        object_symbols = _read_nm_symbols(object_path, nm)
        symbol_cache[object_path] = object_symbols
        linker_registered_by_object[object_path] = (
            _read_linker_registrations(object_path, objdump)
        )
        exported_symbols.update(
            name.removeprefix("__ksymtab_")
            for name in object_symbols
            if name.startswith("__ksymtab_")
        )

    enriched = ReferenceGraph(metadata=dict(graph.metadata))
    matched = []
    exported_nodes = []
    linker_registered_nodes = []
    missing_nodes = []
    for node in graph.nodes.values():
        if node.kind not in {EntityKind.FUNCTION, EntityKind.GLOBAL}:
            enriched.add_node(node)
            continue
        enriched_node = node
        if (
            node.linkage is Linkage.EXTERNAL
            and node.symbol in exported_symbols
        ):
            enriched_node = replace(
                node,
                attributes=node.attributes.union({"exported"}),
            )
            exported_nodes.append(node.id)
        source = node.translation_unit or node.source_path
        if not source:
            missing_nodes.append(node.id)
            enriched.add_node(enriched_node)
            continue
        normalized = _normalize_source(source)
        relative_object = translation_units.get(normalized)
        if relative_object is None:
            missing_nodes.append(node.id)
            enriched.add_node(enriched_node)
            continue
        object_path = root / relative_object
        if not object_path.is_file():
            missing_nodes.append(node.id)
            enriched.add_node(enriched_node)
            continue
        if object_path not in symbol_cache:
            symbol_cache[object_path] = _read_nm_symbols(object_path, nm)
            linker_registered_by_object[object_path] = (
                _read_linker_registrations(object_path, objdump)
            )
        if (
            node.symbol
            in linker_registered_by_object.get(
                object_path, _LinkerRegistrations()
            ).symbols_for(node.kind)
            and "linker_registered" not in enriched_node.attributes
        ):
            enriched_node = replace(
                enriched_node,
                attributes=enriched_node.attributes.union(
                    {"linker_registered"}
                ),
            )
            linker_registered_nodes.append(node.id)
        object_symbols = symbol_cache[object_path]
        symbol = object_symbols.get(node.symbol)
        expected_types = (
            _FUNCTION_TYPES
            if node.kind is EntityKind.FUNCTION
            else _GLOBAL_TYPES
        )
        if symbol is None or symbol[0] not in expected_types:
            missing_nodes.append(node.id)
            enriched.add_node(enriched_node)
            continue
        size = symbol[1]
        if node.size_bytes is not None and node.size_bytes != size:
            raise GraphValidationError(
                f"existing size for {node.id} is {node.size_bytes}, "
                f"but {relative_object} reports {size}"
            )
        enriched.add_node(replace(enriched_node, size_bytes=size))
        matched.append(
            {
                "node_id": node.id,
                "object": relative_object,
                "size_bytes": size,
                "symbol_type": symbol[0],
            }
        )
    for edge in graph.edges:
        enriched.add_edge(edge)
    enriched.metadata["symbol_size_enrichment"] = {
        "facts_manifest": str(Path(facts_manifest).resolve()),
        "object_root": str(root),
        "matched_nodes": len(matched),
        "exported_nodes": len(exported_nodes),
        "linker_registered_nodes": len(linker_registered_nodes),
        "missing_nodes": len(missing_nodes),
    }
    enriched.validate()
    return SizeEnrichmentResult(
        graph=enriched,
        matched=tuple(sorted(matched, key=lambda item: item["node_id"])),
        exported_nodes=tuple(sorted(exported_nodes)),
        linker_registered_nodes=tuple(sorted(linker_registered_nodes)),
        missing_nodes=tuple(sorted(missing_nodes)),
        missing_objects=tuple(sorted(missing_objects)),
    )


def _load_translation_unit_objects(
    manifest_path: str | Path, object_root: Path
) -> dict[str, str]:
    artifact = Path(manifest_path)
    try:
        raw = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphValidationError(
            f"cannot read LLVM facts manifest {artifact}: {error}"
        ) from error
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise GraphValidationError(
            "LLVM facts manifest must use schema_version 1"
        )
    units = raw.get("translation_units")
    if not isinstance(units, list):
        raise GraphValidationError(
            "LLVM facts manifest.translation_units must be an array"
        )
    result = {}
    for index, value in enumerate(units):
        if not isinstance(value, Mapping):
            raise GraphValidationError(
                f"translation_units[{index}] must be an object"
            )
        translation_unit = value.get("translation_unit")
        bitcode = value.get("bitcode")
        if not isinstance(translation_unit, str) or not isinstance(
            bitcode, str
        ):
            raise GraphValidationError(
                f"translation_units[{index}] needs string bitcode/TU paths"
            )
        normalized = _normalize_source(translation_unit)
        bitcode_path = PurePosixPath(bitcode)
        if bitcode_path.is_absolute() or ".." in bitcode_path.parts:
            raise GraphValidationError(
                f"unsafe bitcode path in facts manifest: {bitcode!r}"
            )
        object_relative = bitcode_path.with_suffix(".o").as_posix()
        object_path = (object_root / object_relative).resolve()
        try:
            object_path.relative_to(object_root)
        except ValueError as error:
            raise GraphValidationError(
                f"object path escapes object root: {object_relative}"
            ) from error
        existing = result.get(normalized)
        if existing is not None and existing != object_relative:
            raise GraphValidationError(
                f"translation unit {normalized!r} maps to multiple objects"
            )
        result[normalized] = object_relative
    return result


def _normalize_source(path: str) -> str:
    value = str(PurePosixPath(path))
    while value.startswith("./"):
        value = value[2:]
    return value


def _read_nm_symbols(
    object_path: Path, nm: str | Path
) -> dict[str, tuple[str, int]]:
    try:
        completed = subprocess.run(
            [
                str(nm),
                "-S",
                "--defined-only",
                "--format=posix",
                str(object_path),
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
            f"cannot read symbols from {object_path}: {detail}"
        ) from error
    result = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4 or len(fields[1]) != 1:
            continue
        name, symbol_type, _value, raw_size = fields[:4]
        try:
            size = int(raw_size, 16)
        except ValueError:
            continue
        previous = result.get(name)
        current = (symbol_type, size)
        if previous is not None and previous != current:
            raise GraphValidationError(
                f"ambiguous sized symbol {name!r} in {object_path}"
            )
        result[name] = current
    return result


def _read_linker_registrations(
    object_path: Path,
    objdump: str | Path,
) -> _LinkerRegistrations:
    """Resolve relocations emitted by linker callback registries.

    Some registries point directly at functions, while others point at a
    global callback table.  Keeping those cases distinct avoids treating
    unrelated constants in the same ELF section as executable roots.
    """

    try:
        completed = subprocess.run(
            [str(objdump), "-t", "-r", str(object_path)],
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
            f"cannot read linker registrations from {object_path}: "
            f"{detail}"
        ) from error

    symbols_by_kind_and_location: dict[
        EntityKind, dict[tuple[str, int], set[str]]
    ] = {
        EntityKind.FUNCTION: {},
        EntityKind.GLOBAL: {},
    }
    names_by_kind: dict[EntityKind, set[str]] = {
        EntityKind.FUNCTION: set(),
        EntityKind.GLOBAL: set(),
    }
    lines = completed.stdout.splitlines()
    for line in lines:
        fields = line.split()
        marker_value = next(
            (value for value in ("F", "O") if value in fields),
            None,
        )
        if marker_value is None:
            continue
        marker = fields.index(marker_value)
        if marker < 1 or len(fields) <= marker + 3:
            continue
        try:
            address = int(fields[0], 16)
            int(fields[marker + 2], 16)
        except ValueError:
            continue
        section = fields[marker + 1]
        symbol = fields[marker + 3]
        kind = (
            EntityKind.FUNCTION
            if marker_value == "F"
            else EntityKind.GLOBAL
        )
        names_by_kind[kind].add(symbol)
        symbols_by_kind_and_location[kind].setdefault(
            (section, address), set()
        ).add(symbol)

    registered: dict[EntityKind, set[str]] = {
        EntityKind.FUNCTION: set(),
        EntityKind.GLOBAL: set(),
    }
    relocation_section: str | None = None
    header_pattern = re.compile(
        r"^RELOCATION RECORDS FOR \[(?P<section>.+)\]:$"
    )
    target_pattern = re.compile(
        r"^(?P<base>.+?)(?P<operator>[+-])0x(?P<offset>[0-9a-fA-F]+)$"
    )
    for line in lines:
        stripped = line.strip()
        header = header_pattern.match(stripped)
        if header:
            relocation_section = header.group("section")
            continue
        target_kind = _linker_registration_target_kind(
            relocation_section
        )
        if target_kind is None:
            continue
        fields = stripped.split()
        if len(fields) < 3:
            continue
        try:
            int(fields[0], 16)
        except ValueError:
            continue
        target = fields[2]
        target_names = names_by_kind[target_kind]
        target_locations = symbols_by_kind_and_location[target_kind]
        if target in target_names:
            registered[target_kind].add(target)
            continue
        match = target_pattern.match(target)
        if match:
            base = match.group("base")
            if base in target_names:
                registered[target_kind].add(base)
                continue
            if match.group("operator") == "+":
                address = int(match.group("offset"), 16)
                registered[target_kind].update(
                    target_locations.get((base, address), ())
                )
            continue
        registered[target_kind].update(
            target_locations.get((target, 0), ())
        )
    return _LinkerRegistrations(
        functions=frozenset(registered[EntityKind.FUNCTION]),
        globals=frozenset(registered[EntityKind.GLOBAL]),
    )


def _linker_registration_target_kind(
    section: str | None,
) -> EntityKind | None:
    if section is None:
        return None
    if section.startswith(_LINKER_FUNCTION_SECTION_PREFIXES):
        return EntityKind.FUNCTION
    if section in _LINKER_GLOBAL_SECTIONS:
        return EntityKind.GLOBAL
    return None

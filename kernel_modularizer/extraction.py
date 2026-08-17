"""Validated source facts emitted by the Clang AST extractor."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any, Mapping

from .errors import GraphValidationError, SchemaVersionError


EXTRACTION_SCHEMA_VERSION = 1


def _require(
    value: Mapping[str, Any],
    key: str,
    expected_type: type,
    context: str,
) -> Any:
    result = value.get(key)
    if not isinstance(result, expected_type):
        raise GraphValidationError(
            f"{context}.{key} must be {expected_type.__name__}"
        )
    return result


def _integer(value: Mapping[str, Any], key: str, context: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool) or result < 0:
        raise GraphValidationError(
            f"{context}.{key} must be a non-negative integer"
        )
    return result


@dataclass(frozen=True)
class SourceReference:
    kind: str
    symbol: str
    offset: int
    length: int

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], context: str
    ) -> "SourceReference":
        kind = _require(value, "kind", str, context)
        if kind not in {"function", "global", "enum"}:
            raise GraphValidationError(
                f"{context}.kind has unsupported value {kind!r}"
            )
        return cls(
            kind=kind,
            symbol=_require(value, "symbol", str, context),
            offset=_integer(value, "offset", context),
            length=_integer(value, "length", context),
        )


@dataclass(frozen=True)
class SourceSlice:
    source_path: str
    start_offset: int
    end_offset: int
    source: str

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], context: str
    ) -> "SourceSlice":
        return cls(
            source_path=_require(value, "source_path", str, context),
            start_offset=_integer(value, "start_offset", context),
            end_offset=_integer(value, "end_offset", context),
            source=_require(value, "source", str, context),
        )

    def verify_source(self) -> None:
        _verify_plain_source_range(
            self.source_path,
            self.start_offset,
            self.end_offset,
            self.source,
            "prior function declaration",
        )


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], context: str
    ) -> "Parameter":
        return cls(
            name=_require(value, "name", str, context),
            type=_require(value, "type", str, context),
        )


@dataclass(frozen=True)
class Include:
    offset: int
    source_path: str
    written: str
    resolved: str
    angled: bool

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], context: str
    ) -> "Include":
        angled = value.get("angled")
        if not isinstance(angled, bool):
            raise GraphValidationError(f"{context}.angled must be bool")
        return cls(
            offset=_integer(value, "offset", context),
            source_path=_require(value, "source_path", str, context),
            written=_require(value, "written", str, context),
            resolved=_require(value, "resolved", str, context),
            angled=angled,
        )

    @property
    def directive(self) -> str:
        left, right = ("<", ">") if self.angled else ('"', '"')
        return f"#include {left}{self.written}{right}"


@dataclass(frozen=True)
class LocalDeclaration:
    kind: str
    name: str
    identifiers: tuple[str, ...]
    source_path: str
    start_offset: int
    end_offset: int
    start_line: int
    end_line: int
    source: str

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], context: str
    ) -> "LocalDeclaration":
        kind = _require(value, "kind", str, context)
        if kind not in {"typedef", "enum", "record"}:
            raise GraphValidationError(
                f"{context}.kind has unsupported value {kind!r}"
            )
        identifiers = value.get("identifiers")
        if not isinstance(identifiers, list) or not all(
            isinstance(item, str) and item for item in identifiers
        ):
            raise GraphValidationError(
                f"{context}.identifiers must be an array of "
                "non-empty strings"
            )
        return cls(
            kind=kind,
            name=_require(value, "name", str, context),
            identifiers=tuple(identifiers),
            source_path=_require(value, "source_path", str, context),
            start_offset=_integer(value, "start_offset", context),
            end_offset=_integer(value, "end_offset", context),
            start_line=_integer(value, "start_line", context),
            end_line=_integer(value, "end_line", context),
            source=_require(value, "source", str, context),
        )

    def verify_source(self) -> None:
        _verify_plain_source_range(
            self.source_path,
            self.start_offset,
            self.end_offset,
            self.source,
            f"declaration {self.name}",
        )


@dataclass(frozen=True)
class MacroDefinition:
    name: str
    source_path: str
    start_offset: int
    end_offset: int
    source: str

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], context: str
    ) -> "MacroDefinition":
        return cls(
            name=_require(value, "name", str, context),
            source_path=_require(value, "source_path", str, context),
            start_offset=_integer(value, "start_offset", context),
            end_offset=_integer(value, "end_offset", context),
            source=_require(value, "source", str, context),
        )

    def verify_source(self) -> None:
        _verify_plain_source_range(
            self.source_path,
            self.start_offset,
            self.end_offset,
            self.source,
            f"macro {self.name}",
        )


def _verify_plain_source_range(
    source_path: str,
    start_offset: int,
    end_offset: int,
    source: str,
    context: str,
) -> None:
    path = Path(source_path)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphValidationError(
            f"cannot verify extracted source {path}: {error}"
        ) from error
    if end_offset > len(content) or start_offset > end_offset:
        raise GraphValidationError(
            f"{context}: extracted range exceeds {path}"
        )
    if content[start_offset:end_offset] != source:
        raise GraphValidationError(
            f"{context}: source changed after AST extraction; "
            "rerun SourceExtractor"
        )


@dataclass(frozen=True)
class Dependencies:
    functions: tuple[str, ...]
    globals: tuple[str, ...]
    enums: tuple[str, ...]
    declarations: tuple[str, ...]
    address_taken_globals: tuple[str, ...]

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], context: str
    ) -> "Dependencies":
        def names(key: str) -> tuple[str, ...]:
            raw = value.get(key)
            if not isinstance(raw, list) or not all(
                isinstance(item, str) and item for item in raw
            ):
                raise GraphValidationError(
                    f"{context}.{key} must be an array of non-empty strings"
                )
            return tuple(raw)

        return cls(
            functions=names("functions"),
            globals=names("globals"),
            enums=names("enums"),
            declarations=(
                names("declarations") if "declarations" in value else ()
            ),
            address_taken_globals=(
                names("address_taken_globals")
                if "address_taken_globals" in value
                else ()
            ),
        )


@dataclass(frozen=True)
class SourceEntity:
    symbol: str
    name_spelling: str
    source_path: str
    start_offset: int
    end_offset: int
    start_line: int
    end_line: int
    name_offset: int
    name_length: int
    source: str
    dependencies: Dependencies
    references: tuple[SourceReference, ...]
    macros: tuple[str, ...]

    def verify_source(self) -> None:
        path = Path(self.source_path)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise GraphValidationError(
                f"cannot verify extracted source {path}: {error}"
            ) from error
        if self.end_offset > len(content):
            raise GraphValidationError(
                f"{self.symbol}: extracted range exceeds {path}"
            )
        if content[self.start_offset : self.end_offset] != self.source:
            raise GraphValidationError(
                f"{self.symbol}: source changed after AST extraction; "
                "rerun SourceExtractor"
            )
        relative_name = self.name_offset - self.start_offset
        if relative_name < 0 or (
            self.source[
                relative_name : relative_name + self.name_length
            ]
            != self.name_spelling
        ):
            raise GraphValidationError(
                f"{self.symbol}: AST name range does not match source"
            )
        for reference in self.references:
            relative = reference.offset - self.start_offset
            if relative < 0 or relative + reference.length > len(self.source):
                raise GraphValidationError(
                    f"{self.symbol}: reference {reference.symbol!r} "
                    "falls outside its source range"
                )
            if (
                self.source[relative : relative + reference.length]
                != reference.symbol
            ):
                raise GraphValidationError(
                    f"{self.symbol}: reference range for "
                    f"{reference.symbol!r} does not match source"
                )


@dataclass(frozen=True)
class ExtractedFunction(SourceEntity):
    source_form: str
    signature: str
    return_type: str
    return_category: str
    storage: str
    variadic: bool
    has_unnamed_parameter: bool
    parameters: tuple[Parameter, ...]
    forward_arguments: tuple[str, ...]
    attributes: tuple[str, ...]
    prior_declarations: tuple[SourceSlice, ...]
    body_start_offset: int
    body_end_offset: int

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], context: str
    ) -> "ExtractedFunction":
        base = _entity_fields(value, context)
        parameters_raw = value.get("parameters")
        if not isinstance(parameters_raw, list):
            raise GraphValidationError(f"{context}.parameters must be an array")
        parameters = tuple(
            Parameter.from_dict(
                _mapping(item, f"{context}.parameters[{index}]"),
                f"{context}.parameters[{index}]",
            )
            for index, item in enumerate(parameters_raw)
        )
        forward = value.get("forward_arguments")
        if not isinstance(forward, list) or not all(
            isinstance(item, str) for item in forward
        ):
            raise GraphValidationError(
                f"{context}.forward_arguments must be an array of strings"
            )
        attributes = value.get("attributes")
        if not isinstance(attributes, list) or not all(
            isinstance(item, str) for item in attributes
        ):
            raise GraphValidationError(
                f"{context}.attributes must be an array of strings"
            )
        prior_raw = value.get("prior_declarations", [])
        if not isinstance(prior_raw, list):
            raise GraphValidationError(
                f"{context}.prior_declarations must be an array"
            )
        variadic = value.get("variadic")
        unnamed = value.get("has_unnamed_parameter")
        if not isinstance(variadic, bool) or not isinstance(unnamed, bool):
            raise GraphValidationError(
                f"{context} variadic/unnamed flags must be bool"
            )
        source_form = value.get("source_form", "function")
        if source_form not in {"function", "syscall_define"}:
            raise GraphValidationError(
                f"{context}.source_form has unsupported value "
                f"{source_form!r}"
            )
        result = cls(
            **base,
            source_form=source_form,
            signature=_require(value, "signature", str, context),
            return_type=_require(value, "return_type", str, context),
            return_category=_require(
                value, "return_category", str, context
            ),
            storage=_require(value, "storage", str, context),
            variadic=variadic,
            has_unnamed_parameter=unnamed,
            parameters=parameters,
            forward_arguments=tuple(forward),
            attributes=tuple(attributes),
            prior_declarations=tuple(
                SourceSlice.from_dict(
                    _mapping(item, f"{context}.prior_declarations[{index}]"),
                    f"{context}.prior_declarations[{index}]",
                )
                for index, item in enumerate(prior_raw)
            ),
            body_start_offset=_integer(
                value, "body_start_offset", context
            ),
            body_end_offset=_integer(value, "body_end_offset", context),
        )
        if tuple(parameter.name for parameter in parameters) != result.forward_arguments:
            raise GraphValidationError(
                f"{context}: parameters and forward_arguments disagree"
            )
        return result


@dataclass(frozen=True)
class ExtractedGlobal(SourceEntity):
    source_form: str
    type: str
    storage: str
    has_initializer: bool
    attributes: tuple[str, ...]

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], context: str
    ) -> "ExtractedGlobal":
        initialized = value.get("has_initializer")
        if not isinstance(initialized, bool):
            raise GraphValidationError(
                f"{context}.has_initializer must be bool"
            )
        attributes = value.get("attributes", [])
        if not isinstance(attributes, list) or not all(
            isinstance(item, str) for item in attributes
        ):
            raise GraphValidationError(
                f"{context}.attributes must be an array of strings"
            )
        source_form = value.get("source_form", "global")
        if source_form not in {"global", "macro_declaration_group"}:
            raise GraphValidationError(
                f"{context}.source_form has unsupported value "
                f"{source_form!r}"
            )
        return cls(
            **_entity_fields(value, context),
            source_form=source_form,
            type=_require(value, "type", str, context),
            storage=_require(value, "storage", str, context),
            has_initializer=initialized,
            attributes=tuple(attributes),
        )


@dataclass(frozen=True)
class SourceExtraction:
    functions: tuple[ExtractedFunction, ...]
    globals: tuple[ExtractedGlobal, ...]
    includes: tuple[Include, ...]
    declarations: tuple[LocalDeclaration, ...] = ()
    macro_definitions: tuple[MacroDefinition, ...] = ()

    def verify_sources(self) -> None:
        seen: set[tuple[str, str, str]] = set()
        for kind, entities in (
            ("function", self.functions),
            ("global", self.globals),
        ):
            for entity in entities:
                key = (kind, entity.source_path, entity.symbol)
                if key in seen:
                    raise GraphValidationError(
                        f"duplicate extracted {kind}: "
                        f"{entity.source_path}:{entity.symbol}"
                    )
                seen.add(key)
                entity.verify_source()
                if isinstance(entity, ExtractedFunction):
                    for declaration in entity.prior_declarations:
                        declaration.verify_source()
                        if declaration.source_path != entity.source_path:
                            raise GraphValidationError(
                                f"{entity.symbol}: prior declaration is "
                                "in another source file"
                            )
                        if declaration.end_offset > entity.start_offset:
                            raise GraphValidationError(
                                f"{entity.symbol}: prior declaration does "
                                "not precede the definition"
                            )
        for declaration in self.declarations:
            declaration.verify_source()
        for definition in self.macro_definitions:
            definition.verify_source()


def load_source_extraction(
    path: str | Path, *, verify_sources: bool = True
) -> SourceExtraction:
    artifact = Path(path)
    try:
        value = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphValidationError(
            f"cannot read source extraction {artifact}: {error}"
        ) from error
    root = _mapping(value, "source extraction")
    if root.get("schema_version") != EXTRACTION_SCHEMA_VERSION:
        raise SchemaVersionError(
            "unsupported source extraction schema_version "
            f"{root.get('schema_version')!r}"
        )
    offset_encoding = root.get("offset_encoding", "utf-8-bytes")
    if offset_encoding not in {
        "utf-8-bytes",
        "unicode-code-points",
    }:
        raise GraphValidationError(
            "source extraction.offset_encoding has unsupported value "
            f"{offset_encoding!r}"
        )
    extraction = SourceExtraction(
        functions=_entity_array(
            root, "functions", ExtractedFunction.from_dict
        ),
        globals=_entity_array(root, "globals", ExtractedGlobal.from_dict),
        includes=_entity_array(root, "includes", Include.from_dict),
        declarations=_entity_array(
            root, "declarations", LocalDeclaration.from_dict
        ),
        macro_definitions=_entity_array(
            root, "macro_definitions", MacroDefinition.from_dict
        ),
    )
    if offset_encoding == "utf-8-bytes":
        extraction = _normalize_utf8_byte_offsets(extraction)
    if verify_sources:
        extraction.verify_sources()
    return extraction


def _normalize_utf8_byte_offsets(
    extraction: SourceExtraction,
) -> SourceExtraction:
    """Translate Clang byte offsets into Python string indices.

    Clang's SourceManager reports byte offsets. Python slices Unicode strings
    by code point, so using the raw values corrupts every range after a
    non-ASCII source character. Schema-v1 artifacts without an explicit
    encoding came from SourceExtractor and therefore use UTF-8 byte offsets.
    """

    offsets_by_path: dict[str, set[int]] = {}

    def add(path: str, *offsets: int) -> None:
        offsets_by_path.setdefault(path, set()).update(offsets)

    for entity in [*extraction.functions, *extraction.globals]:
        add(
            entity.source_path,
            entity.start_offset,
            entity.end_offset,
            entity.name_offset,
            entity.name_offset + entity.name_length,
        )
        for reference in entity.references:
            add(
                entity.source_path,
                reference.offset,
                reference.offset + reference.length,
            )
        if isinstance(entity, ExtractedFunction):
            add(
                entity.source_path,
                entity.body_start_offset,
                entity.body_end_offset,
            )
            for declaration in entity.prior_declarations:
                add(
                    declaration.source_path,
                    declaration.start_offset,
                    declaration.end_offset,
                )
    for include in extraction.includes:
        add(include.source_path, include.offset)
    for declaration in extraction.declarations:
        add(
            declaration.source_path,
            declaration.start_offset,
            declaration.end_offset,
        )
    for definition in extraction.macro_definitions:
        add(
            definition.source_path,
            definition.start_offset,
            definition.end_offset,
        )

    character_offsets: dict[str, dict[int, int]] = {}
    for source_path, offsets in offsets_by_path.items():
        path = Path(source_path)
        try:
            content = path.read_bytes()
        except OSError as error:
            raise GraphValidationError(
                f"cannot normalize extracted offsets for {path}: {error}"
            ) from error
        mapping: dict[int, int] = {}
        previous_byte = 0
        previous_character = 0
        for offset in sorted(offsets):
            if offset < previous_byte or offset > len(content):
                raise GraphValidationError(
                    f"extracted byte offset {offset} is outside {path}"
                )
            try:
                segment = content[previous_byte:offset].decode("utf-8")
            except UnicodeDecodeError as error:
                raise GraphValidationError(
                    f"extracted byte offset {offset} splits UTF-8 in {path}"
                ) from error
            previous_character += len(segment)
            mapping[offset] = previous_character
            previous_byte = offset
        character_offsets[source_path] = mapping

    def converted_entity(entity: SourceEntity) -> SourceEntity:
        mapping = character_offsets[entity.source_path]
        references = tuple(
            replace(
                reference,
                offset=mapping[reference.offset],
                length=(
                    mapping[reference.offset + reference.length]
                    - mapping[reference.offset]
                ),
            )
            for reference in entity.references
        )
        common: dict[str, Any] = {
            "start_offset": mapping[entity.start_offset],
            "end_offset": mapping[entity.end_offset],
            "name_offset": mapping[entity.name_offset],
            "name_length": (
                mapping[entity.name_offset + entity.name_length]
                - mapping[entity.name_offset]
            ),
            "references": references,
        }
        if isinstance(entity, ExtractedFunction):
            prior_declarations = tuple(
                replace(
                    declaration,
                    start_offset=character_offsets[
                        declaration.source_path
                    ][declaration.start_offset],
                    end_offset=character_offsets[
                        declaration.source_path
                    ][declaration.end_offset],
                )
                for declaration in entity.prior_declarations
            )
            common.update(
                {
                    "body_start_offset": mapping[
                        entity.body_start_offset
                    ],
                    "body_end_offset": mapping[entity.body_end_offset],
                    "prior_declarations": prior_declarations,
                }
            )
        return replace(entity, **common)

    return SourceExtraction(
        functions=tuple(
            converted_entity(function)
            for function in extraction.functions
        ),
        globals=tuple(
            converted_entity(global_value)
            for global_value in extraction.globals
        ),
        includes=tuple(
            replace(
                include,
                offset=character_offsets[include.source_path][
                    include.offset
                ],
            )
            for include in extraction.includes
        ),
        declarations=tuple(
            replace(
                declaration,
                start_offset=character_offsets[declaration.source_path][
                    declaration.start_offset
                ],
                end_offset=character_offsets[declaration.source_path][
                    declaration.end_offset
                ],
            )
            for declaration in extraction.declarations
        ),
        macro_definitions=tuple(
            replace(
                definition,
                start_offset=character_offsets[definition.source_path][
                    definition.start_offset
                ],
                end_offset=character_offsets[definition.source_path][
                    definition.end_offset
                ],
            )
            for definition in extraction.macro_definitions
        ),
    )


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GraphValidationError(f"{context} must be an object")
    return value


def _entity_array(
    root: Mapping[str, Any], key: str, parser: Any
) -> tuple[Any, ...]:
    values = root.get(key)
    if values is None and key in {
        "globals",
        "declarations",
        "macro_definitions",
    }:
        values = []
    if not isinstance(values, list):
        raise GraphValidationError(f"source extraction.{key} must be an array")
    return tuple(
        parser(
            _mapping(value, f"{key}[{index}]"),
            f"{key}[{index}]",
        )
        for index, value in enumerate(values)
    )


def _entity_fields(
    value: Mapping[str, Any], context: str
) -> dict[str, Any]:
    dependencies = Dependencies.from_dict(
        _mapping(value.get("dependencies"), f"{context}.dependencies"),
        f"{context}.dependencies",
    )
    references_raw = value.get("references", [])
    if not isinstance(references_raw, list):
        raise GraphValidationError(f"{context}.references must be an array")
    macros = value.get("macros")
    if not isinstance(macros, list) or not all(
        isinstance(item, str) for item in macros
    ):
        raise GraphValidationError(
            f"{context}.macros must be an array of strings"
        )
    symbol = _require(value, "symbol", str, context)
    name_spelling = value.get("name_spelling", symbol)
    if not isinstance(name_spelling, str) or not name_spelling:
        raise GraphValidationError(
            f"{context}.name_spelling must be a non-empty string"
        )
    return {
        "symbol": symbol,
        "name_spelling": name_spelling,
        "source_path": _require(value, "source_path", str, context),
        "start_offset": _integer(value, "start_offset", context),
        "end_offset": _integer(value, "end_offset", context),
        "start_line": _integer(value, "start_line", context),
        "end_line": _integer(value, "end_line", context),
        "name_offset": _integer(value, "name_offset", context),
        "name_length": _integer(value, "name_length", context),
        "source": _require(value, "source", str, context),
        "dependencies": dependencies,
        "references": tuple(
            SourceReference.from_dict(
                _mapping(item, f"{context}.references[{index}]"),
                f"{context}.references[{index}]",
            )
            for index, item in enumerate(references_raw)
        ),
        "macros": tuple(macros),
    }

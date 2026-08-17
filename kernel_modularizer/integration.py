"""Transactional, hash-checked integration of generated kernel bundles."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .backend import BACKEND_SCHEMA_VERSION
from .errors import GraphValidationError
from .io import write_json_atomic, write_text_atomic


@dataclass(frozen=True)
class IntegrationResult:
    transaction_id: str
    changed_paths: tuple[str, ...]
    already_applied_paths: tuple[str, ...]
    dry_run: bool


def apply_bundle(
    bundle_directory: str | Path,
    kernel_root: str | Path,
    *,
    dry_run: bool = False,
) -> IntegrationResult:
    bundle_root = Path(bundle_directory).resolve()
    kernel = Path(kernel_root).resolve()
    manifest_path = bundle_root / "bundle.json"
    manifest = _load_json(manifest_path, "bundle manifest")
    if manifest.get("schema_version") != BACKEND_SCHEMA_VERSION:
        raise GraphValidationError(
            "unsupported bundle schema_version "
            f"{manifest.get('schema_version')!r}"
        )
    module = manifest.get("module_object")
    if not isinstance(module, str) or not module:
        raise GraphValidationError("bundle manifest.module_object is missing")
    for required in ("Makefile", "Kconfig", "kernel/Makefile"):
        if not (kernel / required).is_file():
            raise GraphValidationError(
                f"{kernel} is not a supported kernel tree: "
                f"missing {required}"
            )

    direct_writes: dict[str, str] = {}
    integration_fragments: dict[str, str] = {}
    protected_generated_paths: set[str] = set()
    for index, record_value in enumerate(
        _array(manifest, "generated_files")
    ):
        record = _mapping(
            record_value, f"generated_files[{index}]"
        )
        relative = _safe_relative(
            _string(record, "path", f"generated_files[{index}]")
        )
        source = _bundle_file(bundle_root, relative)
        content = _verified_text(
            source,
            _string(record, "sha256", f"generated_files[{index}]"),
        )
        if relative.startswith("integration/"):
            integration_fragments[relative] = content
        else:
            direct_writes[relative] = content
            protected_generated_paths.add(relative)

    for index, record_value in enumerate(
        _array(manifest, "resident_replacements")
    ):
        context = f"resident_replacements[{index}]"
        record = _mapping(record_value, context)
        kernel_path = record.get("kernel_path")
        if not isinstance(kernel_path, str):
            raise GraphValidationError(
                f"{context}.kernel_path is null; regenerate with kernel_root"
            )
        relative = _safe_relative(kernel_path)
        source = _bundle_file(
            bundle_root,
            _safe_relative(_string(record, "bundle_path", context)),
        )
        generated = _verified_text(
            source, _string(record, "generated_sha256", context)
        )
        destination = _kernel_path(kernel, relative)
        if not destination.is_file():
            raise GraphValidationError(
                f"resident source is missing: {destination}"
            )
        current = destination.read_text(encoding="utf-8")
        current_hash = _sha256(current)
        original_hash = _string(record, "original_sha256", context)
        generated_hash = _string(record, "generated_sha256", context)
        if current_hash not in {original_hash, generated_hash}:
            raise GraphValidationError(
                f"resident source changed since extraction: {relative}"
            )
        direct_writes[relative] = generated

    make_fragment = integration_fragments.get(
        "integration/Makefile.fragment"
    )
    kconfig_fragment = integration_fragments.get(
        "integration/Kconfig.fragment"
    )
    if make_fragment is None or kconfig_fragment is None:
        raise GraphValidationError(
            "bundle is missing Kbuild/Kconfig integration fragments"
        )

    direct_writes["kernel/Makefile"] = _marked_block(
        (kernel / "kernel/Makefile").read_text(encoding="utf-8"),
        "linux-modularizer:directory",
        "obj-y += linux_modularizer/\n",
    )
    direct_writes["Kconfig"] = _marked_block(
        (kernel / "Kconfig").read_text(encoding="utf-8"),
        "linux-modularizer:directory",
        'source "kernel/linux_modularizer/Kconfig"\n',
    )
    module_makefile = kernel / "kernel/linux_modularizer/Makefile"
    module_kconfig = kernel / "kernel/linux_modularizer/Kconfig"
    direct_writes["kernel/linux_modularizer/Makefile"] = _marked_block(
        _read_optional(module_makefile),
        f"linux-modularizer:{module}",
        make_fragment,
    )
    direct_writes["kernel/linux_modularizer/Kconfig"] = _marked_block(
        _read_optional(module_kconfig),
        f"linux-modularizer:{module}",
        kconfig_fragment,
    )

    changed = []
    unchanged = []
    for relative, content in sorted(direct_writes.items()):
        destination = _kernel_path(kernel, relative)
        current = _read_optional(destination)
        if current == content:
            unchanged.append(relative)
        else:
            if destination.exists() and relative in protected_generated_paths:
                raise GraphValidationError(
                    f"generated artifact collision: {relative}"
                )
            changed.append(relative)

    fingerprint = hashlib.sha256(manifest_path.read_bytes())
    for relative in changed:
        destination = _kernel_path(kernel, relative)
        before = _read_optional(destination)
        fingerprint.update(b"\0path\0")
        fingerprint.update(relative.encode("utf-8"))
        fingerprint.update(
            b"\0file\0" if destination.is_file() else b"\0absent\0"
        )
        fingerprint.update(_sha256(before).encode("ascii"))
    transaction_id = fingerprint.hexdigest()[:16]
    result = IntegrationResult(
        transaction_id=transaction_id,
        changed_paths=tuple(changed),
        already_applied_paths=tuple(unchanged),
        dry_run=dry_run,
    )
    if dry_run or not changed:
        return result

    transaction_root = (
        kernel / ".linux-modularizer/transactions" / transaction_id
    )
    transaction_manifest = transaction_root / "transaction.json"
    records = []
    if transaction_manifest.exists():
        previous = _load_json(
            transaction_manifest, "transaction manifest"
        )
        if previous.get("status") != "rolled_back":
            raise GraphValidationError(
                f"transaction {transaction_id} already exists but bundle "
                "is not fully applied"
            )
        records = _replay_records(
            previous,
            kernel,
            changed,
            direct_writes,
        )
    else:
        for relative in changed:
            destination = _kernel_path(kernel, relative)
            existed = destination.is_file()
            before = _read_optional(destination)
            if existed:
                write_text_atomic(
                    transaction_root / "backup" / relative, before
                )
            records.append(
                {
                    "path": relative,
                    "existed": existed,
                    "before_sha256": (
                        _sha256(before) if existed else None
                    ),
                    "applied_sha256": _sha256(
                        direct_writes[relative]
                    ),
                }
            )
    write_json_atomic(
        transaction_manifest,
        {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "bundle_manifest": str(manifest_path),
            "status": "prepared",
            "files": records,
        },
    )
    for relative in changed:
        write_text_atomic(
            _kernel_path(kernel, relative), direct_writes[relative]
        )
    write_json_atomic(
        transaction_manifest,
        {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "bundle_manifest": str(manifest_path),
            "status": "applied",
            "files": records,
        },
    )
    return result


def _replay_records(
    manifest: Mapping[str, Any],
    kernel: Path,
    changed: list[str],
    direct_writes: Mapping[str, str],
) -> list[Mapping[str, Any]]:
    records = [
        _mapping(value, f"files[{index}]")
        for index, value in enumerate(_array(manifest, "files"))
    ]
    recorded_paths = [
        _safe_relative(_string(record, "path", f"files[{index}]"))
        for index, record in enumerate(records)
    ]
    if recorded_paths != changed:
        raise GraphValidationError(
            "rolled-back transaction path set does not match bundle"
        )
    for index, (relative, record) in enumerate(
        zip(recorded_paths, records)
    ):
        context = f"files[{index}]"
        expected_applied = _string(
            record, "applied_sha256", context
        )
        if _sha256(direct_writes[relative]) != expected_applied:
            raise GraphValidationError(
                f"rolled-back transaction content changed: {relative}"
            )
        destination = _kernel_path(kernel, relative)
        existed = record.get("existed")
        if existed is True:
            expected_before = _string(
                record, "before_sha256", context
            )
            if (
                not destination.is_file()
                or _sha256(_read_optional(destination))
                != expected_before
            ):
                raise GraphValidationError(
                    f"cannot replay modified path: {relative}"
                )
        elif existed is False:
            if destination.exists():
                raise GraphValidationError(
                    f"cannot replay newly occupied path: {relative}"
                )
        else:
            raise GraphValidationError(
                f"{context}.existed must be bool"
            )
    return records


def rollback_transaction(
    kernel_root: str | Path,
    transaction_id: str,
) -> tuple[str, ...]:
    if not transaction_id or any(
        character not in "0123456789abcdef" for character in transaction_id
    ):
        raise GraphValidationError("invalid transaction id")
    kernel = Path(kernel_root).resolve()
    transaction_root = (
        kernel / ".linux-modularizer/transactions" / transaction_id
    )
    manifest_path = transaction_root / "transaction.json"
    manifest = _load_json(manifest_path, "transaction manifest")
    if manifest.get("status") != "applied":
        raise GraphValidationError(
            f"transaction {transaction_id} is not in applied state"
        )
    records = _array(manifest, "files")

    for index, value in enumerate(records):
        context = f"files[{index}]"
        record = _mapping(value, context)
        relative = _safe_relative(_string(record, "path", context))
        destination = _kernel_path(kernel, relative)
        current = _read_optional(destination)
        if _sha256(current) != _string(
            record, "applied_sha256", context
        ):
            raise GraphValidationError(
                f"cannot rollback modified path: {relative}"
            )

    restored = []
    for index, value in enumerate(reversed(records)):
        context = f"files[{len(records) - index - 1}]"
        record = _mapping(value, context)
        relative = _safe_relative(_string(record, "path", context))
        destination = _kernel_path(kernel, relative)
        if record.get("existed") is True:
            backup = transaction_root / "backup" / relative
            content = _verified_text(
                backup, _string(record, "before_sha256", context)
            )
            write_text_atomic(destination, content)
        elif record.get("existed") is False:
            destination.unlink()
        else:
            raise GraphValidationError(f"{context}.existed must be bool")
        restored.append(relative)
    manifest["status"] = "rolled_back"
    write_json_atomic(manifest_path, manifest)
    return tuple(restored)


def _marked_block(content: str, marker: str, body: str) -> str:
    begin = f"# BEGIN {marker}"
    end = f"# END {marker}"
    if begin in content or end in content:
        if begin not in content or end not in content:
            raise GraphValidationError(
                f"incomplete integration marker {marker!r}"
            )
        start = content.index(begin)
        finish = content.index(end, start) + len(end)
        replacement = f"{begin}\n{body.rstrip()}\n{end}"
        return content[:start] + replacement + content[finish:]
    prefix = content
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    return f"{prefix}{begin}\n{body.rstrip()}\n{end}\n"


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphValidationError(f"cannot read {path}: {error}") from error


def _verified_text(path: Path, expected_hash: str) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphValidationError(
            f"cannot read bundle artifact {path}: {error}"
        ) from error
    if _sha256(content) != expected_hash:
        raise GraphValidationError(
            f"bundle artifact hash mismatch: {path}"
        )
    return content


def _bundle_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise GraphValidationError(
            f"bundle path escapes bundle root: {relative}"
        ) from error
    return path


def _kernel_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise GraphValidationError(
            f"kernel path escapes kernel root: {relative}"
        ) from error
    return path


def _safe_relative(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not value:
        raise GraphValidationError(f"unsafe relative path: {value!r}")
    return path.as_posix()


def _load_json(path: Path, context: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphValidationError(
            f"cannot read {context} {path}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise GraphValidationError(f"{context} must be an object")
    return value


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GraphValidationError(f"{context} must be an object")
    return value


def _array(value: Mapping[str, Any], key: str) -> list[Any]:
    result = value.get(key)
    if not isinstance(result, list):
        raise GraphValidationError(f"{key} must be an array")
    return result


def _string(value: Mapping[str, Any], key: str, context: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise GraphValidationError(f"{context}.{key} must be a string")
    return result


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

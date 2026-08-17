"""Extract Clang embedded bitcode from a configured kernel object tree."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
from typing import Any, Sequence

from .errors import GraphValidationError
from .io import write_bytes_atomic
from .size_validation import read_elf_sections


_BITCODE_MAGICS = (b"BC\xc0\xde", b"\xde\xc0\x17\x0b")


@dataclass(frozen=True)
class EmbeddedBitcodeEntry:
    object_path: str
    object_sha256: str
    bitcode_path: str
    bitcode_sha256: str
    bitcode_bytes: int
    raw_bitcode_object: bool
    sidecar_bitcode: bool
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "object": self.object_path,
            "object_sha256": self.object_sha256,
            "bitcode": self.bitcode_path,
            "bitcode_sha256": self.bitcode_sha256,
            "bitcode_bytes": self.bitcode_bytes,
            "raw_bitcode_object": self.raw_bitcode_object,
            "sidecar_bitcode": self.sidecar_bitcode,
            "reused": self.reused,
        }


@dataclass(frozen=True)
class EmbeddedBitcodeResult:
    object_root: str
    output_root: str
    scanned_objects: int
    skipped_objects: int
    entries: tuple[EmbeddedBitcodeEntry, ...]
    link_inputs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "stage": "embedded-bitcode-extraction",
            "object_root": self.object_root,
            "output_root": self.output_root,
            "selection": {
                "mode": "link-inputs" if self.link_inputs else "object-tree",
                "link_inputs": list(self.link_inputs),
            },
            "summary": {
                "scanned_objects": self.scanned_objects,
                "extracted_bitcode": len(self.entries),
                "skipped_without_bitcode": self.skipped_objects,
                "reused": sum(item.reused for item in self.entries),
            },
            "translation_units": [
                item.to_dict() for item in self.entries
            ],
        }


def extract_embedded_bitcode_tree(
    object_root: str | Path,
    output_root: str | Path,
    *,
    jobs: int | None = None,
    overwrite: bool = False,
    link_inputs: Sequence[str | Path] = (),
    archive_tool: str | Path = "ar",
) -> EmbeddedBitcodeResult:
    objects = Path(object_root).resolve()
    output = Path(output_root).resolve()
    if not objects.is_dir():
        raise GraphValidationError(
            f"kernel object root is not a directory: {objects}"
        )
    selected_jobs = jobs if jobs is not None else max(1, os.cpu_count() or 1)
    if selected_jobs < 1:
        raise GraphValidationError("bitcode extraction jobs must be positive")
    normalized_link_inputs = tuple(
        _relative_input_path(objects, item) for item in link_inputs
    )
    object_paths = (
        _objects_from_link_inputs(
            objects,
            normalized_link_inputs,
            archive_tool=archive_tool,
        )
        if normalized_link_inputs
        else sorted(objects.rglob("*.o"))
    )
    if not object_paths:
        raise GraphValidationError(f"no .o files found under {objects}")

    entries = []
    skipped = 0
    failures = []
    with ThreadPoolExecutor(max_workers=selected_jobs) as executor:
        futures = {
            executor.submit(
                _extract_one,
                path,
                object_root=objects,
                output_root=output,
                overwrite=overwrite,
            ): path
            for path in object_paths
        }
        for future in as_completed(futures):
            path = futures[future]
            try:
                entry = future.result()
            except Exception as error:
                failures.append((path, error))
                continue
            if entry is None:
                skipped += 1
            else:
                entries.append(entry)
    if failures:
        detail = "; ".join(
            f"{path.relative_to(objects)}: {error}"
            for path, error in sorted(failures, key=lambda item: str(item[0]))
        )
        raise GraphValidationError(
            f"embedded bitcode extraction failed: {detail}"
        )
    entries.sort(key=lambda item: item.object_path)
    if not entries:
        raise GraphValidationError(
            "no embedded or sidecar bitcode was found; build the kernel "
            "with Clang and KCFLAGS=-save-temps=obj"
        )
    return EmbeddedBitcodeResult(
        object_root=str(objects),
        output_root=str(output),
        scanned_objects=len(object_paths),
        skipped_objects=skipped,
        entries=tuple(entries),
        link_inputs=normalized_link_inputs,
    )


def _relative_input_path(
    object_root: Path,
    link_input: str | Path,
) -> str:
    path = Path(link_input)
    resolved = path.resolve() if path.is_absolute() else (object_root / path).resolve()
    try:
        return resolved.relative_to(object_root).as_posix()
    except ValueError as error:
        raise GraphValidationError(
            f"link input is outside the kernel object root: {path}"
        ) from error


def _objects_from_link_inputs(
    object_root: Path,
    link_inputs: Sequence[str],
    *,
    archive_tool: str | Path,
) -> list[Path]:
    selected: set[Path] = set()
    visited_archives: set[Path] = set()

    def visit(path: Path) -> None:
        resolved = path.resolve()
        try:
            resolved.relative_to(object_root)
        except ValueError as error:
            raise GraphValidationError(
                f"linked object is outside the kernel object root: {path}"
            ) from error
        if not resolved.is_file():
            raise GraphValidationError(f"link input does not exist: {path}")
        if resolved.suffix == ".o":
            selected.add(resolved)
            return
        if resolved.suffix != ".a":
            raise GraphValidationError(
                f"link input must be an ELF object or archive: {path}"
            )
        if resolved in visited_archives:
            return
        visited_archives.add(resolved)
        try:
            completed = subprocess.run(
                [str(archive_tool), "t", str(resolved)],
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
                f"cannot list link archive {resolved}: {detail}"
            ) from error
        for member in completed.stdout.splitlines():
            if not member.strip():
                continue
            member_path = Path(member.strip())
            if not member_path.is_absolute():
                member_path = resolved.parent / member_path
            if member_path.suffix in {".o", ".a"}:
                visit(member_path)

    for item in link_inputs:
        visit(object_root / item)
    return sorted(selected)


def _extract_one(
    object_path: Path,
    *,
    object_root: Path,
    output_root: Path,
    overwrite: bool,
) -> EmbeddedBitcodeEntry | None:
    relative = object_path.relative_to(object_root)
    try:
        with object_path.open("rb") as stream:
            prefix = stream.read(4)
    except OSError as error:
        raise GraphValidationError(
            f"cannot read kernel object {object_path}: {error}"
        ) from error
    raw_bitcode = prefix in _BITCODE_MAGICS
    sidecar_bitcode = False
    if raw_bitcode:
        try:
            payload = object_path.read_bytes()
        except OSError as error:
            raise GraphValidationError(
                f"cannot read bitcode object {object_path}: {error}"
            ) from error
    elif prefix == b"\x7fELF":
        matches = [
            section
            for section in read_elf_sections(object_path)
            if section.name == ".llvmbc"
        ]
        if len(matches) > 1:
            raise GraphValidationError(
                f"{object_path} has multiple .llvmbc sections"
            )
        if matches:
            section = matches[0]
            try:
                with object_path.open("rb") as stream:
                    stream.seek(section.offset)
                    payload = stream.read(section.size)
            except OSError as error:
                raise GraphValidationError(
                    f"cannot read .llvmbc from {object_path}: {error}"
                ) from error
            if len(payload) != section.size:
                raise GraphValidationError(
                    f"truncated .llvmbc section in {object_path}"
                )
        else:
            sidecar = object_path.with_suffix(".bc")
            if not sidecar.is_file():
                return None
            try:
                payload = sidecar.read_bytes()
            except OSError as error:
                raise GraphValidationError(
                    f"cannot read sidecar bitcode {sidecar}: {error}"
                ) from error
            sidecar_bitcode = True
    else:
        return None
    if payload[:4] not in _BITCODE_MAGICS:
        raise GraphValidationError(
            f"invalid embedded LLVM bitcode magic in {object_path}"
        )

    destination = output_root / relative.with_suffix(".bc")
    payload_sha = hashlib.sha256(payload).hexdigest()
    reused = False
    if destination.exists() and not overwrite:
        try:
            existing = destination.read_bytes()
        except OSError as error:
            raise GraphValidationError(
                f"cannot verify existing bitcode {destination}: {error}"
            ) from error
        if hashlib.sha256(existing).hexdigest() != payload_sha:
            raise GraphValidationError(
                f"stale bitcode output exists: {destination}; "
                "rerun with --overwrite"
            )
        reused = True
    else:
        write_bytes_atomic(destination, payload)
    return EmbeddedBitcodeEntry(
        object_path=relative.as_posix(),
        object_sha256=_sha256(object_path),
        bitcode_path=relative.with_suffix(".bc").as_posix(),
        bitcode_sha256=payload_sha,
        bitcode_bytes=len(payload),
        raw_bitcode_object=raw_bitcode,
        sidecar_bitcode=sidecar_bitcode,
        reused=reused,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise GraphValidationError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()

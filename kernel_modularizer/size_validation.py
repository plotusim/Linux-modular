"""Post-link resident-size validation for generated kernel modules.

File size and ``bzImage`` size are useful release metrics, but neither is a
reliable per-candidate optimization metric: debug information, compression
and alignment can move them in either direction. This module reads affected
ELF object sections for attribution, final ``vmlinux`` linker boundaries for
layout-aware resident accounting, and optionally enforces an image-growth
ceiling as a release gate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct
import subprocess
from typing import Any, Iterable

from .errors import GraphValidationError


SIZE_REPORT_SCHEMA_VERSION = 2

_ELF_MAGIC = b"\x7fELF"
_ELFCLASS32 = 1
_ELFCLASS64 = 2
_ELFDATA2LSB = 1
_ELFDATA2MSB = 2
_SHN_XINDEX = 0xFFFF
_SHT_NOBITS = 8
_SHF_WRITE = 0x1
_SHF_ALLOC = 0x2
_SHF_EXECINSTR = 0x4


@dataclass(frozen=True)
class ElfSection:
    name: str
    section_type: int
    flags: int
    offset: int
    size: int


@dataclass(frozen=True)
class ObjectMeasurement:
    path: str
    sha256: str
    file_bytes: int
    permanent_alloc_bytes: int
    startup_alloc_bytes: int
    discarded_alloc_bytes: int
    executable_bytes: int
    writable_bytes: int
    readonly_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "file_bytes": self.file_bytes,
            "permanent_alloc_bytes": self.permanent_alloc_bytes,
            "startup_alloc_bytes": self.startup_alloc_bytes,
            "discarded_alloc_bytes": self.discarded_alloc_bytes,
            "permanent_breakdown": {
                "executable_bytes": self.executable_bytes,
                "writable_bytes": self.writable_bytes,
                "readonly_bytes": self.readonly_bytes,
            },
        }


@dataclass(frozen=True)
class ArtifactMeasurement:
    path: str
    sha256: str
    file_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "file_bytes": self.file_bytes,
        }


@dataclass(frozen=True)
class LinkedKernelMeasurement:
    """Linux permanent footprint derived from its linker boundary symbols.

    These are the same four ranges used by the kernel's early ``Memory:``
    report.  Unlike summing ELF section payloads, the ranges preserve linker
    padding and page-alignment effects caused by kallsyms and ksymtab growth.
    """

    path: str
    sha256: str
    file_bytes: int
    text_start: int
    text_end: int
    rodata_start: int
    rodata_end: int
    data_start: int
    data_end: int
    bss_start: int
    bss_end: int

    @property
    def code_bytes(self) -> int:
        return self.text_end - self.text_start

    @property
    def rodata_bytes(self) -> int:
        return self.rodata_end - self.rodata_start

    @property
    def writable_data_bytes(self) -> int:
        return self.data_end - self.data_start

    @property
    def bss_bytes(self) -> int:
        return self.bss_end - self.bss_start

    @property
    def permanent_resident_bytes(self) -> int:
        return (
            self.code_bytes
            + self.rodata_bytes
            + self.writable_data_bytes
            + self.bss_bytes
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "file_bytes": self.file_bytes,
            "permanent_resident_bytes": self.permanent_resident_bytes,
            "ranges": {
                "code": {
                    "start": self.text_start,
                    "end": self.text_end,
                    "bytes": self.code_bytes,
                },
                "rodata": {
                    "start": self.rodata_start,
                    "end": self.rodata_end,
                    "bytes": self.rodata_bytes,
                },
                "writable_data": {
                    "start": self.data_start,
                    "end": self.data_end,
                    "bytes": self.writable_data_bytes,
                },
                "bss": {
                    "start": self.bss_start,
                    "end": self.bss_end,
                    "bytes": self.bss_bytes,
                },
            },
        }


@dataclass(frozen=True)
class SizeGateResult:
    baseline_objects: tuple[ObjectMeasurement, ...]
    resident_objects: tuple[ObjectMeasurement, ...]
    module_objects: tuple[ObjectMeasurement, ...]
    minimum_resident_savings_bytes: int
    maximum_image_regression_bytes: int | None
    maximum_linked_kernel_regression_bytes: int | None = None
    baseline_images: tuple[ArtifactMeasurement, ...] = ()
    resident_images: tuple[ArtifactMeasurement, ...] = ()
    baseline_linked_kernel: LinkedKernelMeasurement | None = None
    resident_linked_kernel: LinkedKernelMeasurement | None = None

    @property
    def baseline_resident_bytes(self) -> int:
        return sum(
            item.permanent_alloc_bytes for item in self.baseline_objects
        )

    @property
    def modular_resident_bytes(self) -> int:
        return sum(
            item.permanent_alloc_bytes for item in self.resident_objects
        )

    @property
    def unloaded_resident_savings_bytes(self) -> int:
        return self.baseline_resident_bytes - self.modular_resident_bytes

    @property
    def loaded_module_bytes(self) -> int:
        return sum(
            item.permanent_alloc_bytes for item in self.module_objects
        )

    @property
    def loaded_delta_bytes(self) -> int:
        return (
            self.modular_resident_bytes
            + self.loaded_module_bytes
            - self.baseline_resident_bytes
        )

    @property
    def image_delta_bytes(self) -> int | None:
        if not self.baseline_images and not self.resident_images:
            return None
        return sum(
            item.file_bytes for item in self.resident_images
        ) - sum(item.file_bytes for item in self.baseline_images)

    @property
    def linked_kernel_delta_bytes(self) -> int | None:
        if (
            self.baseline_linked_kernel is None
            or self.resident_linked_kernel is None
        ):
            return None
        return (
            self.resident_linked_kernel.permanent_resident_bytes
            - self.baseline_linked_kernel.permanent_resident_bytes
        )

    @property
    def passed(self) -> bool:
        resident_passed = (
            self.unloaded_resident_savings_bytes
            >= self.minimum_resident_savings_bytes
        )
        image_passed = (
            self.maximum_image_regression_bytes is None
            or (
                self.image_delta_bytes is not None
                and self.image_delta_bytes
                <= self.maximum_image_regression_bytes
            )
        )
        linked_kernel_passed = (
            self.maximum_linked_kernel_regression_bytes is None
            or (
                self.linked_kernel_delta_bytes is not None
                and self.linked_kernel_delta_bytes
                <= self.maximum_linked_kernel_regression_bytes
            )
        )
        return resident_passed and image_passed and linked_kernel_passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SIZE_REPORT_SCHEMA_VERSION,
            "passed": self.passed,
            "gate": {
                "metric": "permanent_allocatable_elf_bytes",
                "minimum_resident_savings_bytes": (
                    self.minimum_resident_savings_bytes
                ),
                "maximum_image_regression_bytes": (
                    self.maximum_image_regression_bytes
                ),
                "maximum_linked_kernel_regression_bytes": (
                    self.maximum_linked_kernel_regression_bytes
                ),
            },
            "summary": {
                "baseline_resident_bytes": self.baseline_resident_bytes,
                "modular_resident_bytes": self.modular_resident_bytes,
                "unloaded_resident_savings_bytes": (
                    self.unloaded_resident_savings_bytes
                ),
                "loaded_module_bytes": self.loaded_module_bytes,
                "loaded_delta_bytes": self.loaded_delta_bytes,
                "image_delta_bytes": self.image_delta_bytes,
                "linked_kernel_delta_bytes": (
                    self.linked_kernel_delta_bytes
                ),
            },
            "baseline_objects": [
                item.to_dict() for item in self.baseline_objects
            ],
            "resident_objects": [
                item.to_dict() for item in self.resident_objects
            ],
            "module_objects": [
                item.to_dict() for item in self.module_objects
            ],
            "baseline_images": [
                item.to_dict() for item in self.baseline_images
            ],
            "resident_images": [
                item.to_dict() for item in self.resident_images
            ],
            "baseline_linked_kernel": (
                self.baseline_linked_kernel.to_dict()
                if self.baseline_linked_kernel is not None
                else None
            ),
            "resident_linked_kernel": (
                self.resident_linked_kernel.to_dict()
                if self.resident_linked_kernel is not None
                else None
            ),
        }


def measure_elf_object(path: str | Path) -> ObjectMeasurement:
    artifact = Path(path)
    sections = read_elf_sections(artifact)
    permanent = startup = discarded = 0
    executable = writable = readonly = 0
    for section in sections:
        if not section.flags & _SHF_ALLOC:
            continue
        if _is_discarded_section(section.name):
            discarded += section.size
            continue
        if _is_startup_section(section.name):
            startup += section.size
            continue
        permanent += section.size
        if section.flags & _SHF_EXECINSTR:
            executable += section.size
        elif section.flags & _SHF_WRITE:
            writable += section.size
        else:
            readonly += section.size
    return ObjectMeasurement(
        path=str(artifact.resolve()),
        sha256=_sha256(artifact),
        file_bytes=_file_size(artifact),
        permanent_alloc_bytes=permanent,
        startup_alloc_bytes=startup,
        discarded_alloc_bytes=discarded,
        executable_bytes=executable,
        writable_bytes=writable,
        readonly_bytes=readonly,
    )


def measure_artifact(path: str | Path) -> ArtifactMeasurement:
    artifact = Path(path)
    return ArtifactMeasurement(
        path=str(artifact.resolve()),
        sha256=_sha256(artifact),
        file_bytes=_file_size(artifact),
    )


def measure_linked_kernel(
    path: str | Path, *, nm: str | Path = "nm"
) -> LinkedKernelMeasurement:
    artifact = Path(path)
    required = {
        "_text",
        "_etext",
        "__start_rodata",
        "__end_rodata",
        "_sdata",
        "_edata",
        "__bss_start",
        "__bss_stop",
    }
    try:
        completed = subprocess.run(
            [
                str(nm),
                "--defined-only",
                "--format=posix",
                str(artifact),
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
            f"cannot read linked-kernel symbols from {artifact}: {detail}"
        ) from error

    symbols: dict[str, int] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[0] not in required:
            continue
        try:
            address = int(fields[2], 16)
        except ValueError:
            continue
        previous = symbols.get(fields[0])
        if previous is not None and previous != address:
            raise GraphValidationError(
                f"ambiguous linked-kernel symbol {fields[0]!r} "
                f"in {artifact}"
            )
        symbols[fields[0]] = address
    missing = sorted(required - symbols.keys())
    if missing:
        raise GraphValidationError(
            f"linked kernel {artifact} is missing boundary symbols: "
            + ", ".join(missing)
        )

    ranges = (
        ("code", symbols["_text"], symbols["_etext"]),
        (
            "rodata",
            symbols["__start_rodata"],
            symbols["__end_rodata"],
        ),
        ("writable data", symbols["_sdata"], symbols["_edata"]),
        ("bss", symbols["__bss_start"], symbols["__bss_stop"]),
    )
    for name, start, end in ranges:
        if end < start:
            raise GraphValidationError(
                f"linked kernel {artifact} has inverted {name} range"
            )

    return LinkedKernelMeasurement(
        path=str(artifact.resolve()),
        sha256=_sha256(artifact),
        file_bytes=_file_size(artifact),
        text_start=symbols["_text"],
        text_end=symbols["_etext"],
        rodata_start=symbols["__start_rodata"],
        rodata_end=symbols["__end_rodata"],
        data_start=symbols["_sdata"],
        data_end=symbols["_edata"],
        bss_start=symbols["__bss_start"],
        bss_end=symbols["__bss_stop"],
    )


def evaluate_size_gate(
    *,
    baseline_objects: Iterable[str | Path],
    resident_objects: Iterable[str | Path],
    module_objects: Iterable[str | Path] = (),
    minimum_resident_savings_bytes: int = 4096,
    maximum_image_regression_bytes: int | None = None,
    baseline_images: Iterable[str | Path] = (),
    resident_images: Iterable[str | Path] = (),
    baseline_linked_kernel: str | Path | None = None,
    resident_linked_kernel: str | Path | None = None,
    maximum_linked_kernel_regression_bytes: int | None = None,
    nm: str | Path = "nm",
) -> SizeGateResult:
    if minimum_resident_savings_bytes < 0:
        raise GraphValidationError(
            "minimum resident savings must be non-negative"
        )
    if (
        maximum_image_regression_bytes is not None
        and maximum_image_regression_bytes < 0
    ):
        raise GraphValidationError(
            "maximum image regression must be non-negative"
        )
    if (
        maximum_linked_kernel_regression_bytes is not None
        and maximum_linked_kernel_regression_bytes < 0
    ):
        raise GraphValidationError(
            "maximum linked-kernel regression must be non-negative"
        )
    baseline = tuple(measure_elf_object(path) for path in baseline_objects)
    resident = tuple(measure_elf_object(path) for path in resident_objects)
    modules = tuple(measure_elf_object(path) for path in module_objects)
    if not baseline:
        raise GraphValidationError(
            "size gate requires at least one baseline ELF object"
        )
    if not resident:
        raise GraphValidationError(
            "size gate requires at least one modular resident ELF object"
        )
    measured_baseline_images = tuple(
        measure_artifact(path) for path in baseline_images
    )
    measured_resident_images = tuple(
        measure_artifact(path) for path in resident_images
    )
    if bool(measured_baseline_images) != bool(measured_resident_images):
        raise GraphValidationError(
            "image comparison requires both baseline and resident images"
        )
    if len(measured_baseline_images) != len(measured_resident_images):
        raise GraphValidationError(
            "image comparison requires equal baseline and resident counts"
        )
    if maximum_image_regression_bytes is not None and (
        not measured_baseline_images or not measured_resident_images
    ):
        raise GraphValidationError(
            "image regression gate requires baseline and resident images"
        )
    if bool(baseline_linked_kernel) != bool(resident_linked_kernel):
        raise GraphValidationError(
            "linked-kernel comparison requires both baseline and resident "
            "vmlinux artifacts"
        )
    measured_baseline_linked = (
        measure_linked_kernel(baseline_linked_kernel, nm=nm)
        if baseline_linked_kernel is not None
        else None
    )
    measured_resident_linked = (
        measure_linked_kernel(resident_linked_kernel, nm=nm)
        if resident_linked_kernel is not None
        else None
    )
    if measured_baseline_linked is not None:
        if maximum_linked_kernel_regression_bytes is None:
            maximum_linked_kernel_regression_bytes = 0
    elif maximum_linked_kernel_regression_bytes is not None:
        raise GraphValidationError(
            "linked-kernel regression gate requires baseline and resident "
            "vmlinux artifacts"
        )
    return SizeGateResult(
        baseline_objects=baseline,
        resident_objects=resident,
        module_objects=modules,
        minimum_resident_savings_bytes=minimum_resident_savings_bytes,
        maximum_image_regression_bytes=maximum_image_regression_bytes,
        maximum_linked_kernel_regression_bytes=(
            maximum_linked_kernel_regression_bytes
        ),
        baseline_images=measured_baseline_images,
        resident_images=measured_resident_images,
        baseline_linked_kernel=measured_baseline_linked,
        resident_linked_kernel=measured_resident_linked,
    )


def _is_startup_section(name: str) -> bool:
    return (
        name.startswith((".init", ".meminit", ".cpuinit"))
        or ".initcall" in name
        or name.startswith(("__init", "___init"))
    )


def _is_discarded_section(name: str) -> bool:
    return (
        name.startswith((".discard", ".exit", ".memexit", ".cpuexit"))
        or name.startswith(("__exit", "___exit"))
    )


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError as error:
        raise GraphValidationError(
            f"cannot stat size artifact {path}: {error}"
        ) from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError as error:
        raise GraphValidationError(
            f"cannot hash size artifact {path}: {error}"
        ) from error
    return digest.hexdigest()


def read_elf_sections(path: str | Path) -> tuple[ElfSection, ...]:
    path = Path(path)
    try:
        stream = path.open("rb")
    except OSError as error:
        raise GraphValidationError(f"cannot open ELF {path}: {error}") from error
    with stream:
        identity = stream.read(16)
        if len(identity) != 16 or identity[:4] != _ELF_MAGIC:
            raise GraphValidationError(f"not an ELF artifact: {path}")
        elf_class = identity[4]
        data_encoding = identity[5]
        if data_encoding == _ELFDATA2LSB:
            byte_order = "<"
        elif data_encoding == _ELFDATA2MSB:
            byte_order = ">"
        else:
            raise GraphValidationError(
                f"unsupported ELF byte order in {path}: {data_encoding}"
            )
        if elf_class == _ELFCLASS64:
            header_format = byte_order + "HHIQQQIHHHHHH"
            section_format = byte_order + "IIQQQQIIQQ"
        elif elf_class == _ELFCLASS32:
            header_format = byte_order + "HHIIIIIHHHHHH"
            section_format = byte_order + "IIIIIIIIII"
        else:
            raise GraphValidationError(
                f"unsupported ELF class in {path}: {elf_class}"
            )

        header = _unpack_exact(
            stream, struct.calcsize(header_format), header_format, path
        )
        section_offset = header[5]
        section_entry_size = header[10]
        section_count = header[11]
        string_section_index = header[12]
        expected_section_size = struct.calcsize(section_format)
        if section_entry_size < expected_section_size:
            raise GraphValidationError(
                f"invalid ELF section entry size in {path}: "
                f"{section_entry_size}"
            )

        file_size = _file_size(path)

        def raw_section(index: int) -> tuple[int, ...]:
            offset = section_offset + index * section_entry_size
            if (
                index < 0
                or offset < 0
                or offset + expected_section_size > file_size
            ):
                raise GraphValidationError(
                    f"ELF section header {index} is outside {path}"
                )
            stream.seek(offset)
            return _unpack_exact(
                stream, expected_section_size, section_format, path
            )

        section_zero = raw_section(0)
        if section_count == 0:
            section_count = section_zero[5]
        if string_section_index == _SHN_XINDEX:
            string_section_index = section_zero[6]
        if section_count <= 0 or section_count > 1_000_000:
            raise GraphValidationError(
                f"invalid ELF section count in {path}: {section_count}"
            )
        if string_section_index >= section_count:
            raise GraphValidationError(
                f"invalid ELF section-name table index in {path}"
            )

        raw_sections = [raw_section(index) for index in range(section_count)]
        string_section = raw_sections[string_section_index]
        string_offset = string_section[4]
        string_size = string_section[5]
        if (
            string_section[1] == _SHT_NOBITS
            or string_offset + string_size > file_size
        ):
            raise GraphValidationError(
                f"invalid ELF section-name table in {path}"
            )
        stream.seek(string_offset)
        names = stream.read(string_size)
        if len(names) != string_size:
            raise GraphValidationError(
                f"truncated ELF section-name table in {path}"
            )

        result = []
        for raw in raw_sections:
            result.append(
                ElfSection(
                    name=_section_name(names, raw[0], path),
                    section_type=raw[1],
                    flags=raw[2],
                    offset=raw[4],
                    size=raw[5],
                )
            )
        return tuple(result)


def _unpack_exact(
    stream: Any, length: int, format_string: str, path: Path
) -> tuple[int, ...]:
    value = stream.read(length)
    if len(value) != length:
        raise GraphValidationError(f"truncated ELF structure in {path}")
    return struct.unpack(format_string, value)


def _section_name(table: bytes, offset: int, path: Path) -> str:
    if offset >= len(table):
        raise GraphValidationError(
            f"ELF section name offset is outside table in {path}"
        )
    end = table.find(b"\0", offset)
    if end < 0:
        raise GraphValidationError(
            f"unterminated ELF section name in {path}"
        )
    return table[offset:end].decode("utf-8", errors="replace")

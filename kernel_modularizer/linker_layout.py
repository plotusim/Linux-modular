"""Diagnose linker alignment barriers that absorb modularization savings."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .errors import GraphValidationError


LINKER_LAYOUT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LinkerAlignmentSymbols:
    text_start: str = "_text"
    text_end: str = "_etext"
    payload_end: str = "__kprobes_text_end"
    aligned_start: str = "__entry_text_start"
    aligned_end: str = "__entry_text_end"
    next_aligned_start: str = "__softirqentry_text_start"

    def values(self) -> tuple[str, ...]:
        return (
            self.text_start,
            self.text_end,
            self.payload_end,
            self.aligned_start,
            self.aligned_end,
            self.next_aligned_start,
        )


@dataclass(frozen=True)
class LinkerAlignmentMeasurement:
    path: str
    sha256: str
    file_bytes: int
    alignment_bytes: int
    symbols: LinkerAlignmentSymbols
    addresses: Mapping[str, int]

    @property
    def text_bytes(self) -> int:
        return (
            self.addresses[self.symbols.text_end]
            - self.addresses[self.symbols.text_start]
        )

    @property
    def payload_before_alignment_bytes(self) -> int:
        return (
            self.addresses[self.symbols.payload_end]
            - self.addresses[self.symbols.text_start]
        )

    @property
    def padding_before_aligned_region_bytes(self) -> int:
        return (
            self.addresses[self.symbols.aligned_start]
            - self.addresses[self.symbols.payload_end]
        )

    @property
    def aligned_region_payload_bytes(self) -> int:
        return (
            self.addresses[self.symbols.aligned_end]
            - self.addresses[self.symbols.aligned_start]
        )

    @property
    def padding_after_aligned_region_bytes(self) -> int:
        return (
            self.addresses[self.symbols.next_aligned_start]
            - self.addresses[self.symbols.aligned_end]
        )

    @property
    def payload_reduction_to_previous_boundary_bytes(self) -> int:
        previous = (
            self.addresses[self.symbols.aligned_start]
            - self.alignment_bytes
        )
        return max(
            0,
            self.addresses[self.symbols.payload_end] - previous,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "file_bytes": self.file_bytes,
            "alignment_bytes": self.alignment_bytes,
            "addresses": {
                name: self.addresses[name]
                for name in self.symbols.values()
            },
            "text_bytes": self.text_bytes,
            "payload_before_alignment_bytes": (
                self.payload_before_alignment_bytes
            ),
            "padding_before_aligned_region_bytes": (
                self.padding_before_aligned_region_bytes
            ),
            "aligned_region_payload_bytes": (
                self.aligned_region_payload_bytes
            ),
            "padding_after_aligned_region_bytes": (
                self.padding_after_aligned_region_bytes
            ),
            "payload_reduction_to_previous_boundary_bytes": (
                self.payload_reduction_to_previous_boundary_bytes
            ),
        }


def measure_linker_alignment(
    path: str | Path,
    *,
    nm: str | Path = "nm",
    alignment_bytes: int = 2 * 1024 * 1024,
    symbols: LinkerAlignmentSymbols | None = None,
) -> LinkerAlignmentMeasurement:
    """Measure one aligned text island and the payload threshold before it."""

    if (
        not isinstance(alignment_bytes, int)
        or isinstance(alignment_bytes, bool)
        or alignment_bytes <= 0
        or alignment_bytes & (alignment_bytes - 1)
    ):
        raise GraphValidationError(
            "linker alignment must be a positive power of two"
        )
    artifact = Path(path)
    selected = symbols or LinkerAlignmentSymbols()
    addresses = _read_symbol_addresses(
        artifact,
        selected.values(),
        nm=nm,
    )
    ordered = [addresses[name] for name in selected.values()]
    text_start, text_end, payload_end, aligned_start, aligned_end, next_start = (
        ordered
    )
    if not (
        text_start <= payload_end <= aligned_start <= aligned_end
        <= next_start <= text_end
    ):
        raise GraphValidationError(
            f"linked-kernel alignment symbols are out of order in {artifact}"
        )
    for name in (selected.aligned_start, selected.next_aligned_start):
        if addresses[name] % alignment_bytes:
            raise GraphValidationError(
                f"linked-kernel symbol {name} is not aligned to "
                f"{alignment_bytes} bytes in {artifact}"
            )
    return LinkerAlignmentMeasurement(
        path=str(artifact.resolve()),
        sha256=_sha256(artifact),
        file_bytes=_file_size(artifact),
        alignment_bytes=alignment_bytes,
        symbols=selected,
        addresses=addresses,
    )


def compare_linker_alignment(
    baseline_path: str | Path,
    resident_path: str | Path,
    *,
    nm: str | Path = "nm",
    alignment_bytes: int = 2 * 1024 * 1024,
    symbols: LinkerAlignmentSymbols | None = None,
) -> dict[str, Any]:
    selected = symbols or LinkerAlignmentSymbols()
    baseline = measure_linker_alignment(
        baseline_path,
        nm=nm,
        alignment_bytes=alignment_bytes,
        symbols=selected,
    )
    resident = measure_linker_alignment(
        resident_path,
        nm=nm,
        alignment_bytes=alignment_bytes,
        symbols=selected,
    )
    payload_delta = (
        resident.payload_before_alignment_bytes
        - baseline.payload_before_alignment_bytes
    )
    text_delta = resident.text_bytes - baseline.text_bytes
    absorbed = max(0, -payload_delta) if text_delta == 0 else 0
    remaining = resident.payload_reduction_to_previous_boundary_bytes
    return {
        "schema_version": LINKER_LAYOUT_SCHEMA_VERSION,
        "analysis": "aligned-text-island-threshold",
        "symbols": {
            "text_start": selected.text_start,
            "text_end": selected.text_end,
            "payload_end": selected.payload_end,
            "aligned_start": selected.aligned_start,
            "aligned_end": selected.aligned_end,
            "next_aligned_start": selected.next_aligned_start,
        },
        "baseline": baseline.to_dict(),
        "resident": resident.to_dict(),
        "comparison": {
            "payload_before_alignment_delta_bytes": payload_delta,
            "final_text_range_delta_bytes": text_delta,
            "payload_reduction_absorbed_by_alignment_bytes": absorbed,
            "additional_payload_reduction_to_previous_boundary_bytes": (
                remaining
            ),
            "alignment_step_bytes": alignment_bytes,
            "threshold_crossed": remaining == 0,
        },
        "interpretation": (
            "The pre-alignment payload became smaller but the final text "
            "range did not; the aligned island absorbed the reduction."
            if absorbed
            else "The final text range reflects the payload/layout change."
        ),
    }


def render_linker_alignment_markdown(report: Mapping[str, Any]) -> str:
    baseline = report["baseline"]
    resident = report["resident"]
    comparison = report["comparison"]
    return "\n".join(
        [
            "# Linked-kernel alignment analysis",
            "",
            "| Metric | Baseline | Resident |",
            "|---|---:|---:|",
            (
                "| Payload before aligned region | "
                f"{baseline['payload_before_alignment_bytes']:,} B | "
                f"{resident['payload_before_alignment_bytes']:,} B |"
            ),
            (
                "| Padding before aligned region | "
                f"{baseline['padding_before_aligned_region_bytes']:,} B | "
                f"{resident['padding_before_aligned_region_bytes']:,} B |"
            ),
            (
                "| Final text range | "
                f"{baseline['text_bytes']:,} B | "
                f"{resident['text_bytes']:,} B |"
            ),
            "",
            (
                "- Payload delta: "
                f"{comparison['payload_before_alignment_delta_bytes']:+,} B"
            ),
            (
                "- Final text delta: "
                f"{comparison['final_text_range_delta_bytes']:+,} B"
            ),
            (
                "- Additional payload reduction needed to reach the "
                "previous alignment boundary: "
                f"{comparison['additional_payload_reduction_to_previous_boundary_bytes']:,} B"
            ),
            (
                "- Alignment step if the layout crosses that boundary: "
                f"{comparison['alignment_step_bytes']:,} B"
            ),
            "",
            str(report["interpretation"]),
            "",
        ]
    )


def _read_symbol_addresses(
    artifact: Path,
    names: tuple[str, ...],
    *,
    nm: str | Path,
) -> dict[str, int]:
    required = set(names)
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
    result: dict[str, int] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[0] not in required:
            continue
        try:
            address = int(fields[2], 16)
        except ValueError:
            continue
        previous = result.get(fields[0])
        if previous is not None and previous != address:
            raise GraphValidationError(
                f"ambiguous linked-kernel symbol {fields[0]!r} in {artifact}"
            )
        result[fields[0]] = address
    missing = sorted(required - result.keys())
    if missing:
        raise GraphValidationError(
            f"linked kernel {artifact} is missing alignment symbols: "
            + ", ".join(missing)
        )
    return result


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError as error:
        raise GraphValidationError(
            f"cannot stat linked kernel {path}: {error}"
        ) from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise GraphValidationError(
            f"cannot hash linked kernel {path}: {error}"
        ) from error
    return digest.hexdigest()

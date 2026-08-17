"""Parse and compare startup resource measurements from serial boot logs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
from statistics import median
from typing import Any, Iterable, Mapping

from .errors import GraphValidationError


BOOT_RESOURCE_REPORT_SCHEMA_VERSION = 1
_KERNEL_MEMORY = re.compile(
    r"Memory:\s+"
    r"(?P<available>\d+)K/(?P<total>\d+)K available \("
    r"(?P<code>\d+)K kernel code,\s+"
    r"(?P<rwdata>\d+)K rwdata,\s+"
    r"(?P<rodata>\d+)K rodata,\s+"
    r"(?P<init>\d+)K init,\s+"
    r"(?P<bss>\d+)K bss,\s+"
    r"(?P<reserved>\d+)K reserved"
)
_READY_MARKER = "LINUX_MODULARIZER_METRICS"
_READY_INTEGER_KEYS = (
    "mem_available_kb",
    "mem_free_kb",
    "slab_kb",
    "sreclaimable_kb",
    "sunreclaim_kb",
)


@dataclass(frozen=True)
class BootMetrics:
    log_path: str
    log_sha256: str
    kernel_available_kb: int
    kernel_total_kb: int
    kernel_code_kb: int
    rwdata_kb: int
    rodata_kb: int
    init_kb: int
    bss_kb: int
    reserved_kb: int
    ready_uptime_seconds: float | None = None
    mem_available_kb: int | None = None
    mem_free_kb: int | None = None
    slab_kb: int | None = None
    sreclaimable_kb: int | None = None
    sunreclaim_kb: int | None = None

    @property
    def permanent_kernel_kb(self) -> int:
        return (
            self.kernel_code_kb
            + self.rwdata_kb
            + self.rodata_kb
            + self.bss_kb
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "log_path": self.log_path,
            "log_sha256": self.log_sha256,
            "kernel_boot_memory": {
                "available_kb": self.kernel_available_kb,
                "total_kb": self.kernel_total_kb,
                "kernel_code_kb": self.kernel_code_kb,
                "rwdata_kb": self.rwdata_kb,
                "rodata_kb": self.rodata_kb,
                "init_kb": self.init_kb,
                "bss_kb": self.bss_kb,
                "reserved_kb": self.reserved_kb,
                "permanent_kernel_kb": self.permanent_kernel_kb,
            },
            "ready_state": {
                "uptime_seconds": self.ready_uptime_seconds,
                "mem_available_kb": self.mem_available_kb,
                "mem_free_kb": self.mem_free_kb,
                "slab_kb": self.slab_kb,
                "sreclaimable_kb": self.sreclaimable_kb,
                "sunreclaim_kb": self.sunreclaim_kb,
            },
        }


@dataclass(frozen=True)
class BootResourcePolicy:
    max_permanent_kernel_regression_kb: float = 0
    max_ready_time_regression_seconds: float = 0.25
    max_mem_available_regression_kb: float = 128
    max_slab_regression_kb: float = 64

    def __post_init__(self) -> None:
        for name, value in (
            (
                "max_permanent_kernel_regression_kb",
                self.max_permanent_kernel_regression_kb,
            ),
            (
                "max_ready_time_regression_seconds",
                self.max_ready_time_regression_seconds,
            ),
            (
                "max_mem_available_regression_kb",
                self.max_mem_available_regression_kb,
            ),
            ("max_slab_regression_kb", self.max_slab_regression_kb),
        ):
            if not math.isfinite(value) or value < 0:
                raise GraphValidationError(f"{name} must be non-negative")


@dataclass(frozen=True)
class BootResourceComparison:
    baseline: tuple[BootMetrics, ...]
    modular: tuple[BootMetrics, ...]
    policy: BootResourcePolicy
    baseline_median: Mapping[str, float]
    modular_median: Mapping[str, float]
    deltas: Mapping[str, float]
    checks: tuple[Mapping[str, Any], ...]

    @property
    def passed(self) -> bool:
        return all(bool(check["passed"]) for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BOOT_RESOURCE_REPORT_SCHEMA_VERSION,
            "passed": self.passed,
            "policy": {
                "max_permanent_kernel_regression_kb": (
                    self.policy.max_permanent_kernel_regression_kb
                ),
                "max_ready_time_regression_seconds": (
                    self.policy.max_ready_time_regression_seconds
                ),
                "max_mem_available_regression_kb": (
                    self.policy.max_mem_available_regression_kb
                ),
                "max_slab_regression_kb": (
                    self.policy.max_slab_regression_kb
                ),
            },
            "baseline": {
                "samples": [item.to_dict() for item in self.baseline],
                "median": dict(self.baseline_median),
            },
            "modular": {
                "samples": [item.to_dict() for item in self.modular],
                "median": dict(self.modular_median),
            },
            "deltas_modular_minus_baseline": dict(self.deltas),
            "checks": list(self.checks),
        }


def parse_boot_log(
    path: str | Path, *, require_ready_metrics: bool = True
) -> BootMetrics:
    artifact = Path(path)
    try:
        content = artifact.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise GraphValidationError(
            f"cannot read boot log {artifact}: {error}"
        ) from error
    memory_matches = list(_KERNEL_MEMORY.finditer(content))
    if not memory_matches:
        raise GraphValidationError(
            f"boot log has no Linux Memory summary: {artifact}"
        )
    memory_values = {
        tuple(match.group(key) for key in _KERNEL_MEMORY.groupindex)
        for match in memory_matches
    }
    if len(memory_values) != 1:
        raise GraphValidationError(
            f"boot log contains conflicting Linux Memory summaries: "
            f"{artifact}"
        )
    memory = memory_matches[-1]
    ready_values = _parse_ready_metrics(content, artifact)
    if require_ready_metrics and ready_values is None:
        raise GraphValidationError(
            f"boot log has no {_READY_MARKER} line: {artifact}"
        )
    ready_values = ready_values or {}
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return BootMetrics(
        log_path=str(artifact.resolve()),
        log_sha256=digest,
        kernel_available_kb=int(memory.group("available")),
        kernel_total_kb=int(memory.group("total")),
        kernel_code_kb=int(memory.group("code")),
        rwdata_kb=int(memory.group("rwdata")),
        rodata_kb=int(memory.group("rodata")),
        init_kb=int(memory.group("init")),
        bss_kb=int(memory.group("bss")),
        reserved_kb=int(memory.group("reserved")),
        ready_uptime_seconds=ready_values.get("uptime_seconds"),
        mem_available_kb=ready_values.get("mem_available_kb"),
        mem_free_kb=ready_values.get("mem_free_kb"),
        slab_kb=ready_values.get("slab_kb"),
        sreclaimable_kb=ready_values.get("sreclaimable_kb"),
        sunreclaim_kb=ready_values.get("sunreclaim_kb"),
    )


def compare_boot_resources(
    baseline_logs: Iterable[str | Path],
    modular_logs: Iterable[str | Path],
    *,
    policy: BootResourcePolicy | None = None,
) -> BootResourceComparison:
    baseline = tuple(parse_boot_log(path) for path in baseline_logs)
    modular = tuple(parse_boot_log(path) for path in modular_logs)
    if not baseline or not modular:
        raise GraphValidationError(
            "boot comparison requires baseline and modular logs"
        )
    selected_policy = policy or BootResourcePolicy()
    baseline_median = _aggregate(baseline)
    modular_median = _aggregate(modular)
    deltas = {
        key: modular_median[key] - baseline_median[key]
        for key in baseline_median
    }
    checks = (
        _maximum_check(
            "permanent_kernel_memory",
            deltas["permanent_kernel_kb"],
            selected_policy.max_permanent_kernel_regression_kb,
            "KiB modular minus baseline; lower is better",
        ),
        _maximum_check(
            "ready_time",
            deltas["ready_uptime_seconds"],
            selected_policy.max_ready_time_regression_seconds,
            "seconds modular minus baseline; lower is better",
        ),
        _minimum_check(
            "available_memory",
            deltas["mem_available_kb"],
            -selected_policy.max_mem_available_regression_kb,
            "KiB modular minus baseline; higher is better",
        ),
        _maximum_check(
            "slab_memory",
            deltas["slab_kb"],
            selected_policy.max_slab_regression_kb,
            "KiB modular minus baseline; lower is better",
        ),
    )
    return BootResourceComparison(
        baseline=baseline,
        modular=modular,
        policy=selected_policy,
        baseline_median=baseline_median,
        modular_median=modular_median,
        deltas=deltas,
        checks=checks,
    )


def _parse_ready_metrics(
    content: str, path: Path
) -> dict[str, int | float] | None:
    parsed = []
    for line in content.splitlines():
        marker = line.find(_READY_MARKER)
        if marker < 0:
            continue
        values: dict[str, int | float] = {}
        for token in line[marker + len(_READY_MARKER) :].split():
            if "=" not in token:
                continue
            key, raw_value = token.split("=", 1)
            try:
                if key == "uptime_seconds":
                    parsed_float = float(raw_value)
                    if not math.isfinite(parsed_float):
                        raise ValueError("value is not finite")
                    values[key] = parsed_float
                elif key in _READY_INTEGER_KEYS:
                    parsed_integer = int(raw_value)
                    if parsed_integer < 0:
                        raise ValueError("value is negative")
                    values[key] = parsed_integer
            except ValueError as error:
                raise GraphValidationError(
                    f"invalid {key} in boot metrics line {path}: "
                    f"{raw_value!r}"
                ) from error
        required = {"uptime_seconds", *_READY_INTEGER_KEYS}
        missing = sorted(required.difference(values))
        if missing:
            raise GraphValidationError(
                f"incomplete boot metrics line in {path}: "
                + ", ".join(missing)
            )
        parsed.append(values)
    if not parsed:
        return None
    if any(value != parsed[0] for value in parsed[1:]):
        raise GraphValidationError(
            f"conflicting {_READY_MARKER} lines in {path}"
        )
    return parsed[0]


def _aggregate(samples: tuple[BootMetrics, ...]) -> dict[str, float]:
    fields = {
        "permanent_kernel_kb": [
            float(item.permanent_kernel_kb) for item in samples
        ],
        "kernel_code_kb": [
            float(item.kernel_code_kb) for item in samples
        ],
        "rwdata_kb": [float(item.rwdata_kb) for item in samples],
        "rodata_kb": [float(item.rodata_kb) for item in samples],
        "bss_kb": [float(item.bss_kb) for item in samples],
        "ready_uptime_seconds": [
            _required(item.ready_uptime_seconds, "ready uptime")
            for item in samples
        ],
        "mem_available_kb": [
            _required(item.mem_available_kb, "MemAvailable")
            for item in samples
        ],
        "mem_free_kb": [
            _required(item.mem_free_kb, "MemFree") for item in samples
        ],
        "slab_kb": [
            _required(item.slab_kb, "Slab") for item in samples
        ],
        "sreclaimable_kb": [
            _required(item.sreclaimable_kb, "SReclaimable")
            for item in samples
        ],
        "sunreclaim_kb": [
            _required(item.sunreclaim_kb, "SUnreclaim")
            for item in samples
        ],
    }
    return {key: float(median(values)) for key, values in fields.items()}


def _required(value: int | float | None, name: str) -> float:
    if value is None:
        raise GraphValidationError(f"boot sample is missing {name}")
    return float(value)


def _maximum_check(
    name: str, observed: float, maximum: float, explanation: str
) -> Mapping[str, Any]:
    return {
        "name": name,
        "passed": observed <= maximum,
        "observed_delta": observed,
        "maximum_allowed": maximum,
        "explanation": explanation,
    }


def _minimum_check(
    name: str, observed: float, minimum: float, explanation: str
) -> Mapping[str, Any]:
    return {
        "name": name,
        "passed": observed >= minimum,
        "observed_delta": observed,
        "minimum_allowed": minimum,
        "explanation": explanation,
    }

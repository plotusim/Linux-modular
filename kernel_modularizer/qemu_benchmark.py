"""Run repeatable serial QEMU startup measurements."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .boot_metrics import (
    BootMetrics,
    BootResourceComparison,
    BootResourcePolicy,
    compare_boot_resources,
    parse_boot_log,
)
from .errors import GraphValidationError
from .qemu import QemuResult, QemuScenario, run_qemu_scenario


@dataclass(frozen=True)
class QemuBenchmarkRun:
    index: int
    result: QemuResult
    metrics: BootMetrics

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "qemu": self.result.to_dict(),
            "metrics": self.metrics.to_dict(),
        }


@dataclass(frozen=True)
class QemuBenchmarkResult:
    runs: tuple[QemuBenchmarkRun, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "passed": all(item.result.passed for item in self.runs),
            "repeats": len(self.runs),
            "median": {
                "ready_uptime_seconds": float(
                    median(
                        _ready_uptime(item.metrics)
                        for item in self.runs
                    )
                ),
                "permanent_kernel_kb": float(
                    median(
                        item.metrics.permanent_kernel_kb
                        for item in self.runs
                    )
                ),
                "mem_available_kb": float(
                    median(
                        _ready_integer(
                            item.metrics.mem_available_kb,
                            "MemAvailable",
                        )
                        for item in self.runs
                    )
                ),
                "slab_kb": float(
                    median(
                        _ready_integer(item.metrics.slab_kb, "Slab")
                        for item in self.runs
                    )
                ),
            },
            "runs": [item.to_dict() for item in self.runs],
        }


@dataclass(frozen=True)
class QemuABBenchmarkResult:
    execution_order: tuple[tuple[str, str], ...]
    baseline: QemuBenchmarkResult
    modular: QemuBenchmarkResult
    comparison: BootResourceComparison

    @property
    def passed(self) -> bool:
        return (
            all(item.result.passed for item in self.baseline.runs)
            and all(item.result.passed for item in self.modular.runs)
            and self.comparison.passed
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "passed": self.passed,
            "pairs": len(self.execution_order),
            "method": (
                "balanced adjacent A/B pairs; odd pairs run baseline first "
                "and even pairs run modular first"
            ),
            "execution_order": [
                {"pair": index, "order": list(order)}
                for index, order in enumerate(self.execution_order, start=1)
            ],
            "baseline": self.baseline.to_dict(),
            "modular": self.modular.to_dict(),
            "comparison": self.comparison.to_dict(),
        }


def run_qemu_benchmark(
    scenario: QemuScenario,
    *,
    repeats: int,
    log_directory: str | Path,
) -> QemuBenchmarkResult:
    if repeats < 1 or repeats > 100:
        raise GraphValidationError(
            "QEMU benchmark repeats must be in [1, 100]"
        )
    logs = Path(log_directory)
    try:
        logs.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise GraphValidationError(
            f"cannot create QEMU benchmark log directory {logs}: {error}"
        ) from error
    runs = []
    for index in range(1, repeats + 1):
        log_path = logs / f"run-{index:03d}.log"
        result = run_qemu_scenario(scenario, log_path=log_path)
        metrics = parse_boot_log(log_path)
        runs.append(
            QemuBenchmarkRun(
                index=index,
                result=result,
                metrics=metrics,
            )
        )
    return QemuBenchmarkResult(runs=tuple(runs))


def run_qemu_ab_benchmark(
    baseline_scenario: QemuScenario,
    modular_scenario: QemuScenario,
    *,
    pairs: int,
    log_directory: str | Path,
    policy: BootResourcePolicy | None = None,
) -> QemuABBenchmarkResult:
    """Run balanced adjacent A/B pairs to reduce host-time order bias."""

    if pairs < 2 or pairs > 50 or pairs % 2:
        raise GraphValidationError(
            "QEMU A/B benchmark pairs must be an even number in [2, 50]"
        )
    logs = Path(log_directory)
    baseline_logs = logs / "baseline"
    modular_logs = logs / "modular"
    try:
        baseline_logs.mkdir(parents=True, exist_ok=True)
        modular_logs.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise GraphValidationError(
            f"cannot create QEMU A/B log directory {logs}: {error}"
        ) from error

    runs: dict[str, list[QemuBenchmarkRun]] = {
        "baseline": [],
        "modular": [],
    }
    scenarios = {
        "baseline": baseline_scenario,
        "modular": modular_scenario,
    }
    execution_order = []
    for pair in range(1, pairs + 1):
        order = (
            ("baseline", "modular")
            if pair % 2
            else ("modular", "baseline")
        )
        execution_order.append(order)
        for arm in order:
            log_path = logs / arm / f"run-{pair:03d}.log"
            result = run_qemu_scenario(
                scenarios[arm], log_path=log_path
            )
            runs[arm].append(
                QemuBenchmarkRun(
                    index=pair,
                    result=result,
                    metrics=parse_boot_log(log_path),
                )
            )

    baseline = QemuBenchmarkResult(runs=tuple(runs["baseline"]))
    modular = QemuBenchmarkResult(runs=tuple(runs["modular"]))
    comparison = compare_boot_resources(
        (item.metrics.log_path for item in baseline.runs),
        (item.metrics.log_path for item in modular.runs),
        policy=policy,
    )
    return QemuABBenchmarkResult(
        execution_order=tuple(execution_order),
        baseline=baseline,
        modular=modular,
        comparison=comparison,
    )


def _ready_uptime(metrics: BootMetrics) -> float:
    if metrics.ready_uptime_seconds is None:
        raise GraphValidationError("QEMU boot log is missing ready uptime")
    return metrics.ready_uptime_seconds


def _ready_integer(value: int | None, name: str) -> int:
    if value is None:
        raise GraphValidationError(f"QEMU boot log is missing {name}")
    return value

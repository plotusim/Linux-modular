from pathlib import Path
import json
import tempfile
import unittest

from kernel_modularizer.boot_metrics import (
    BootResourcePolicy,
    compare_boot_resources,
    parse_boot_log,
)
from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.cli import main


def _log(
    *,
    code=100,
    rwdata=20,
    rodata=30,
    bss=40,
    uptime=1.5,
    available=900000,
    slab=12000,
):
    return (
        "[    0.100000] Memory: 900000K/1000000K available "
        f"({code}K kernel code, {rwdata}K rwdata, {rodata}K rodata, "
        f"10K init, {bss}K bss, 100000K reserved, 0K cma-reserved)\n"
        "LINUX_MODULARIZER_METRICS "
        f"uptime_seconds={uptime} mem_available_kb={available} "
        "mem_free_kb=800000 "
        f"slab_kb={slab} sreclaimable_kb=7000 "
        "sunreclaim_kb=5000\n"
        "LINUX_MODULARIZER_READY\n"
    )


class BootMetricsTests(unittest.TestCase):
    def test_parses_kernel_and_ready_state_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boot.log"
            path.write_text(_log(), encoding="utf-8")

            metrics = parse_boot_log(path)

            self.assertEqual(metrics.permanent_kernel_kb, 190)
            self.assertEqual(metrics.ready_uptime_seconds, 1.5)
            self.assertEqual(metrics.mem_available_kb, 900000)
            self.assertEqual(metrics.slab_kb, 12000)

    def test_comparison_uses_medians_and_passes_improvement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = []
            modular = []
            for index, uptime in enumerate((1.4, 1.5, 1.6)):
                path = root / f"baseline-{index}.log"
                path.write_text(_log(uptime=uptime), encoding="utf-8")
                baseline.append(path)
            for index, uptime in enumerate((1.3, 1.4, 1.5)):
                path = root / f"modular-{index}.log"
                path.write_text(
                    _log(
                        code=90,
                        uptime=uptime,
                        available=900100,
                        slab=11990,
                    ),
                    encoding="utf-8",
                )
                modular.append(path)

            result = compare_boot_resources(baseline, modular)

            self.assertTrue(result.passed)
            self.assertEqual(result.deltas["permanent_kernel_kb"], -10)
            self.assertAlmostEqual(
                result.deltas["ready_uptime_seconds"], -0.1
            )
            self.assertEqual(result.deltas["mem_available_kb"], 100)

    def test_gate_rejects_boot_time_and_memory_regression(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.log"
            modular = root / "modular.log"
            baseline.write_text(_log(), encoding="utf-8")
            modular.write_text(
                _log(code=110, uptime=2.0, available=899000),
                encoding="utf-8",
            )

            result = compare_boot_resources(
                [baseline],
                [modular],
                policy=BootResourcePolicy(
                    max_ready_time_regression_seconds=0.1,
                    max_mem_available_regression_kb=128,
                ),
            )

            self.assertFalse(result.passed)
            failed = {
                check["name"]
                for check in result.checks
                if not check["passed"]
            }
            self.assertEqual(
                failed,
                {
                    "permanent_kernel_memory",
                    "ready_time",
                    "available_memory",
                },
            )

    def test_missing_ready_metrics_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boot.log"
            path.write_text(
                _log().split("LINUX_MODULARIZER_METRICS", 1)[0],
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                GraphValidationError, "no LINUX_MODULARIZER_METRICS"
            ):
                parse_boot_log(path)

    def test_compare_boot_cli_writes_gate_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.log"
            modular = root / "modular.log"
            report = root / "report.json"
            baseline.write_text(_log(), encoding="utf-8")
            modular.write_text(_log(code=90), encoding="utf-8")

            status = main(
                [
                    "compare-boot",
                    "--baseline-log",
                    str(baseline),
                    "--modular-log",
                    str(modular),
                    "--output",
                    str(report),
                ]
            )

            self.assertEqual(status, 0)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["passed"])
            self.assertEqual(
                payload["deltas_modular_minus_baseline"][
                    "permanent_kernel_kb"
                ],
                -10,
            )


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import sys
import tempfile
import unittest

from kernel_modularizer.qemu import load_qemu_scenario
from kernel_modularizer.qemu_benchmark import (
    run_qemu_ab_benchmark,
    run_qemu_benchmark,
)


ROOT = Path(__file__).resolve().parents[1]


class QemuBenchmarkTests(unittest.TestCase):
    def scenario(self, root, name="scenario.json"):
        scenario_path = root / name
        scenario_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": [
                        sys.executable,
                        str(ROOT / "tests/fixtures/fake_qemu.py"),
                    ],
                    "boot_expect": "LINUX_MODULARIZER_READY",
                    "boot_timeout_seconds": 3,
                    "steps": [],
                    "shutdown_command": "poweroff -f",
                    "shutdown_timeout_seconds": 3,
                }
            ),
            encoding="utf-8",
        )
        return load_qemu_scenario(scenario_path)

    def test_repeated_runs_capture_parseable_resource_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_qemu_benchmark(
                self.scenario(root),
                repeats=3,
                log_directory=root / "logs",
            )
            payload = result.to_dict()

            self.assertTrue(payload["passed"])
            self.assertEqual(payload["repeats"], 3)
            self.assertEqual(
                payload["median"]["ready_uptime_seconds"], 1.5
            )
            self.assertEqual(
                payload["median"]["permanent_kernel_kb"], 190
            )
            self.assertEqual(
                len(list((root / "logs").glob("run-*.log"))), 3
            )

    def test_ab_runs_use_balanced_adjacent_order_and_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_qemu_ab_benchmark(
                self.scenario(root, "baseline.json"),
                self.scenario(root, "modular.json"),
                pairs=2,
                log_directory=root / "ab",
            )
            payload = result.to_dict()

            self.assertTrue(payload["passed"])
            self.assertEqual(
                payload["execution_order"],
                [
                    {
                        "pair": 1,
                        "order": ["baseline", "modular"],
                    },
                    {
                        "pair": 2,
                        "order": ["modular", "baseline"],
                    },
                ],
            )
            self.assertEqual(
                payload["comparison"][
                    "deltas_modular_minus_baseline"
                ]["ready_uptime_seconds"],
                0.0,
            )
            self.assertEqual(
                len(list((root / "ab/baseline").glob("run-*.log"))),
                2,
            )
            self.assertEqual(
                len(list((root / "ab/modular").glob("run-*.log"))),
                2,
            )


if __name__ == "__main__":
    unittest.main()

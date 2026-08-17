import json
from pathlib import Path
import sys
import tempfile
import unittest

from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.qemu import (
    load_qemu_scenario,
    run_qemu_scenario,
)


ROOT = Path(__file__).resolve().parents[1]


class QemuRunnerTests(unittest.TestCase):
    def test_serial_scenario_runs_all_expectations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenario_path = root / "scenario.json"
            log_path = root / "serial.log"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "command": [
                            sys.executable,
                            str(
                                ROOT
                                / "tests/fixtures/fake_qemu.py"
                            ),
                        ],
                        "boot_expect": "LINUX_MODULARIZER_READY",
                        "boot_timeout_seconds": 3,
                        "steps": [
                            {
                                "send": "trigger",
                                "expect": "RESULT_OK",
                                "timeout_seconds": 3,
                            },
                            {
                                "send": "check-module",
                                "expect": "AUTOLOAD_OK",
                                "timeout_seconds": 3,
                            },
                        ],
                        "shutdown_command": "poweroff -f",
                        "shutdown_timeout_seconds": 3,
                    }
                ),
                encoding="utf-8",
            )
            result = run_qemu_scenario(
                load_qemu_scenario(scenario_path),
                log_path=log_path,
            )

            self.assertTrue(result.passed)
            self.assertEqual(result.completed_steps, 2)
            self.assertEqual(result.returncode, 0)
            self.assertIn(
                "LINUX_MODULARIZER_READY",
                log_path.read_text(encoding="utf-8"),
            )

    def test_timeout_is_a_hard_failure_and_preserves_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenario_path = root / "scenario.json"
            log_path = root / "serial.log"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "command": [
                            sys.executable,
                            str(
                                ROOT
                                / "tests/fixtures/fake_qemu.py"
                            ),
                        ],
                        "boot_expect": "NEVER_PRINTED",
                        "boot_timeout_seconds": 0.1,
                        "steps": [],
                        "shutdown_command": None,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                GraphValidationError, "timed out"
            ):
                run_qemu_scenario(
                    load_qemu_scenario(scenario_path),
                    log_path=log_path,
                )
            self.assertIn(
                "booting fixture",
                log_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()

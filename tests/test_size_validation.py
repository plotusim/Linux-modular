import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from kernel_modularizer.cli import main
from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.size_validation import (
    evaluate_size_gate,
    measure_elf_object,
    measure_linked_kernel,
)


class SizeValidationTests(unittest.TestCase):
    def test_reads_elf_sections_without_external_dependencies(self):
        executable = Path("/bin/true")
        if not executable.is_file():
            self.skipTest("/bin/true is unavailable")

        measurement = measure_elf_object(executable)

        self.assertGreater(measurement.permanent_alloc_bytes, 0)
        self.assertEqual(
            measurement.permanent_alloc_bytes,
            measurement.executable_bytes
            + measurement.writable_bytes
            + measurement.readonly_bytes,
        )
        self.assertEqual(len(measurement.sha256), 64)

    def test_rejects_non_elf_input(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "not-elf"
            artifact.write_text("plain text", encoding="utf-8")

            with self.assertRaisesRegex(GraphValidationError, "not an ELF"):
                measure_elf_object(artifact)

    def test_gate_requires_a_real_resident_reduction(self):
        executable = Path("/bin/true")
        if not executable.is_file():
            self.skipTest("/bin/true is unavailable")

        passing = evaluate_size_gate(
            baseline_objects=[executable],
            resident_objects=[executable],
            minimum_resident_savings_bytes=0,
        )
        failing = evaluate_size_gate(
            baseline_objects=[executable],
            resident_objects=[executable],
            minimum_resident_savings_bytes=1,
        )

        self.assertTrue(passing.passed)
        self.assertFalse(failing.passed)
        self.assertEqual(failing.unloaded_resident_savings_bytes, 0)

    def test_cli_writes_report_and_returns_failure_for_regression(self):
        executable = Path("/bin/true")
        if not executable.is_file():
            self.skipTest("/bin/true is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "size.json"
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                status = main(
                    [
                        "validate-size",
                        "--baseline-object",
                        str(executable),
                        "--resident-object",
                        str(executable),
                        "--minimum-resident-savings",
                        "1",
                        "--output",
                        str(report),
                    ]
                )

            self.assertEqual(status, 1)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertFalse(payload["passed"])
            self.assertIn("size_gate_passed=false", output.getvalue())

    def test_optional_image_gate_rejects_compressed_growth(self):
        executable = Path("/bin/true")
        if not executable.is_file():
            self.skipTest("/bin/true is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_image = root / "baseline.img"
            resident_image = root / "resident.img"
            baseline_image.write_bytes(b"x" * 100)
            resident_image.write_bytes(b"x" * 110)

            result = evaluate_size_gate(
                baseline_objects=[executable],
                resident_objects=[executable],
                minimum_resident_savings_bytes=0,
                baseline_images=[baseline_image],
                resident_images=[resident_image],
                maximum_image_regression_bytes=5,
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.image_delta_bytes, 10)

    def test_image_comparison_requires_matching_pairs(self):
        executable = Path("/bin/true")
        if not executable.is_file():
            self.skipTest("/bin/true is unavailable")
        with self.assertRaisesRegex(
            GraphValidationError, "both baseline and resident"
        ):
            evaluate_size_gate(
                baseline_objects=[executable],
                resident_objects=[executable],
                minimum_resident_savings_bytes=0,
                baseline_images=[executable],
            )

    def test_linked_kernel_gate_rejects_linker_boundary_growth(self):
        executable = Path("/bin/true")
        if not executable.is_file():
            self.skipTest("/bin/true is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline-vmlinux"
            resident = root / "resident-vmlinux"
            baseline.write_bytes(b"baseline")
            resident.write_bytes(b"resident")

            def nm_result(command, **_kwargs):
                rodata_end = (
                    "5000" if command[-1] == str(resident) else "4000"
                )
                stdout = "\n".join(
                    [
                        "_text T 1000",
                        "_etext T 2000",
                        "__start_rodata D 3000",
                        f"__end_rodata R {rodata_end}",
                        "_sdata D 6000",
                        "_edata D 7000",
                        "__bss_start B 8000",
                        "__bss_stop B 9000",
                    ]
                )
                return mock.Mock(stdout=stdout, stderr="", returncode=0)

            with mock.patch(
                "kernel_modularizer.size_validation.subprocess.run",
                side_effect=nm_result,
            ):
                result = evaluate_size_gate(
                    baseline_objects=[executable],
                    resident_objects=[executable],
                    minimum_resident_savings_bytes=0,
                    baseline_linked_kernel=baseline,
                    resident_linked_kernel=resident,
                )

            self.assertFalse(result.passed)
            self.assertEqual(result.linked_kernel_delta_bytes, 4096)
            self.assertEqual(
                result.maximum_linked_kernel_regression_bytes, 0
            )
            self.assertEqual(
                result.to_dict()["schema_version"], 2
            )

    def test_linked_kernel_measurement_requires_all_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "vmlinux"
            artifact.write_bytes(b"not actually parsed when nm is mocked")
            completed = mock.Mock(
                stdout="_text T 1000\n_etext T 2000\n",
                stderr="",
                returncode=0,
            )
            with mock.patch(
                "kernel_modularizer.size_validation.subprocess.run",
                return_value=completed,
            ):
                with self.assertRaisesRegex(
                    GraphValidationError, "missing boundary symbols"
                ):
                    measure_linked_kernel(artifact)

    def test_linked_kernel_comparison_requires_a_pair(self):
        executable = Path("/bin/true")
        if not executable.is_file():
            self.skipTest("/bin/true is unavailable")
        with self.assertRaisesRegex(
            GraphValidationError, "both baseline and resident"
        ):
            evaluate_size_gate(
                baseline_objects=[executable],
                resident_objects=[executable],
                minimum_resident_savings_bytes=0,
                baseline_linked_kernel=executable,
            )


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.linker_layout import (
    compare_linker_alignment,
    measure_linker_alignment,
    render_linker_alignment_markdown,
)


class LinkerLayoutTests(unittest.TestCase):
    def _nm_result(self, payload_end):
        return mock.Mock(
            stdout="\n".join(
                [
                    "_text T 81000000",
                    "_etext T 81a01e08",
                    f"__kprobes_text_end T {payload_end:x}",
                    "__entry_text_start T 81800000",
                    "__entry_text_end T 81801717",
                    "__softirqentry_text_start T 81a00000",
                ]
            ),
            stderr="",
            returncode=0,
        )

    def test_reports_payload_savings_absorbed_by_pmd_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline-vmlinux"
            resident = root / "resident-vmlinux"
            baseline.write_bytes(b"baseline")
            resident.write_bytes(b"resident")

            def nm_result(command, **_kwargs):
                if command[-1] == str(baseline):
                    return self._nm_result(0x8163E8B0)
                return self._nm_result(0x816368B0)

            with mock.patch(
                "kernel_modularizer.linker_layout.subprocess.run",
                side_effect=nm_result,
            ):
                report = compare_linker_alignment(
                    baseline,
                    resident,
                )

        comparison = report["comparison"]
        self.assertEqual(
            comparison["payload_before_alignment_delta_bytes"],
            -32768,
        )
        self.assertEqual(comparison["final_text_range_delta_bytes"], 0)
        self.assertEqual(
            comparison["payload_reduction_absorbed_by_alignment_bytes"],
            32768,
        )
        self.assertEqual(
            comparison[
                "additional_payload_reduction_to_previous_boundary_bytes"
            ],
            223408,
        )
        self.assertIn(
            "223,408 B",
            render_linker_alignment_markdown(report),
        )

    def test_rejects_an_unaligned_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "vmlinux"
            artifact.write_bytes(b"fixture")
            result = self._nm_result(0x8163E8B0)
            result.stdout = result.stdout.replace(
                "__entry_text_start T 81800000",
                "__entry_text_start T 81800001",
            )
            with mock.patch(
                "kernel_modularizer.linker_layout.subprocess.run",
                return_value=result,
            ):
                with self.assertRaisesRegex(
                    GraphValidationError, "not aligned"
                ):
                    measure_linker_alignment(artifact)

    def test_alignment_must_be_a_power_of_two(self):
        with self.assertRaisesRegex(GraphValidationError, "power of two"):
            measure_linker_alignment("unused", alignment_bytes=3000)


if __name__ == "__main__":
    unittest.main()

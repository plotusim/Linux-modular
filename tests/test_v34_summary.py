import json
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = (
    PROJECT_ROOT
    / "docs"
    / "full-kernel-v34-source-support-summary.json"
)


class V34SummaryTests(unittest.TestCase):
    def setUp(self):
        self.summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))

    def test_final_accounting_is_a_complete_partition(self):
        accounting = self.summary["final_elf_accounting"]
        categories = ("CORE", "READY", "BLOCKED", "UNKNOWN")
        self.assertEqual(
            sum(accounting[name]["functions"] for name in categories),
            accounting["total_functions"],
        )
        self.assertEqual(
            sum(accounting[name]["bytes"] for name in categories),
            accounting["total_bytes"],
        )
        for name in categories:
            self.assertEqual(
                accounting[name]["ratio_percent"],
                round(
                    100
                    * accounting[name]["functions"]
                    / accounting["total_functions"],
                    6,
                ),
            )

    def test_mechanism_and_release_sets_are_not_conflated(self):
        module = self.summary["new_experimental_module"]
        mechanism = self.summary["combined_mechanism_validated"]
        release = self.summary["combined_strict_release"]
        self.assertEqual(mechanism["modules"], release["modules"] + 1)
        self.assertEqual(
            mechanism["selected_source_functions"],
            release["selected_source_functions"]
            + module["selected_source_functions"],
        )
        self.assertEqual(
            mechanism["matched_final_elf_functions"],
            release["matched_final_elf_functions"]
            + module["matched_final_elf_functions"],
        )
        self.assertEqual(
            mechanism["matched_baseline_bytes"],
            release["matched_baseline_bytes"]
            + module["matched_baseline_bytes"],
        )
        self.assertEqual(
            mechanism["unloaded_resident_object_savings_bytes"],
            release["unloaded_resident_object_savings_bytes"]
            + self.summary["incremental_size"][
                "unloaded_resident_savings_bytes"
            ],
        )
        accounting = self.summary["final_elf_accounting"]
        self.assertEqual(
            mechanism["matched_final_elf_function_ratio_percent"],
            round(
                100
                * mechanism["matched_final_elf_functions"]
                / accounting["total_functions"],
                6,
            ),
        )
        self.assertEqual(
            mechanism["matched_baseline_byte_ratio_percent"],
            round(
                100
                * mechanism["matched_baseline_bytes"]
                / accounting["total_bytes"],
                6,
            ),
        )
        self.assertTrue(
            self.summary["interpretation"][
                "new_module_mechanism_validated"
            ]
        )
        self.assertFalse(
            self.summary["interpretation"][
                "new_module_release_qualified"
            ]
        )

    def test_incremental_size_math_and_strict_rejection(self):
        size = self.summary["incremental_size"]
        self.assertEqual(
            size["baseline_resident_object_bytes"]
            - size["modular_resident_object_bytes"],
            size["unloaded_resident_savings_bytes"],
        )
        self.assertEqual(
            size["loaded_module_bytes"]
            - size["unloaded_resident_savings_bytes"],
            size["all_loaded_delta_bytes"],
        )
        self.assertEqual(
            size["modular_linked_permanent_bytes"]
            - size["baseline_linked_permanent_bytes"],
            size["linked_permanent_delta_bytes"],
        )
        self.assertEqual(
            size["modular_bzimage_bytes"]
            - size["baseline_bzimage_bytes"],
            size["bzimage_delta_bytes"],
        )
        self.assertTrue(size["exploratory_gate_passed"])
        self.assertFalse(size["strict_release_gate_passed"])
        self.assertGreater(size["bzimage_delta_bytes"], 0)

    def test_linker_alignment_accounts_for_payload_reduction(self):
        layout = self.summary["linker_alignment"]
        self.assertEqual(
            layout["modular_payload_before_alignment_bytes"]
            - layout["baseline_payload_before_alignment_bytes"],
            layout["payload_delta_bytes"],
        )
        self.assertEqual(
            layout["modular_padding_before_alignment_bytes"]
            - layout["baseline_padding_before_alignment_bytes"],
            -layout["payload_delta_bytes"],
        )
        self.assertEqual(layout["final_text_delta_bytes"], 0)
        self.assertGreater(
            layout[
                "additional_payload_reduction_to_previous_boundary_bytes"
            ],
            0,
        )

    def test_runtime_gates_pass(self):
        lifecycle = self.summary["qemu_lifecycle"]
        self.assertTrue(lifecycle["passed"])
        self.assertEqual(lifecycle["repeats"], 4)
        self.assertEqual(lifecycle["completed_steps_per_run"], 8)
        self.assertTrue(self.summary["startup_ab"]["passed"])
        self.assertEqual(self.summary["startup_ab"]["pairs"], 8)

    def test_all_provenance_hashes_are_sha256(self):
        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.endswith("sha256"):
                        self.assertEqual(len(child), 64)
                        int(child, 16)
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(self.summary)


if __name__ == "__main__":
    unittest.main()

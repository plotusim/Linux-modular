import json
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = (
    PROJECT_ROOT
    / "docs"
    / "full-kernel-v33-source-closure-summary.json"
)


class V33SummaryTests(unittest.TestCase):
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

    def test_new_module_totals_and_size_gate_agree(self):
        validated = self.summary["new_validated_modules"]
        modules = validated["per_module"]
        size = self.summary["strict_size_gate"]
        self.assertEqual(set(modules), set(validated["modules"]))
        for field in (
            "selected_source_functions",
            "selected_source_globals",
            "matched_final_elf_functions",
            "matched_baseline_bytes",
            "baseline_resident_object_bytes",
            "modular_resident_object_bytes",
            "unloaded_object_savings_bytes",
            "module_permanent_bytes",
        ):
            total_name = (
                "loaded_module_bytes"
                if field == "module_permanent_bytes"
                else field
            )
            self.assertEqual(
                sum(module[field] for module in modules.values()),
                validated[total_name],
            )
        for field in (
            "baseline_resident_object_bytes",
            "modular_resident_object_bytes",
            "unloaded_object_savings_bytes",
            "loaded_module_bytes",
            "all_new_modules_loaded_delta_bytes",
        ):
            size_field = {
                "baseline_resident_object_bytes": (
                    "baseline_affected_resident_bytes"
                ),
                "modular_resident_object_bytes": (
                    "modular_affected_resident_bytes"
                ),
                "unloaded_object_savings_bytes": (
                    "unloaded_resident_object_savings_bytes"
                ),
            }.get(field, field)
            self.assertEqual(validated[field], size[size_field])

    def test_combined_success_uses_final_elf_denominator(self):
        accounting = self.summary["final_elf_accounting"]
        new = self.summary["new_validated_modules"]
        combined = self.summary["combined_validated_function_modules"]
        self.assertEqual(combined["selected_source_functions"], 70 + new["selected_source_functions"])
        self.assertEqual(combined["matched_final_elf_functions"], 45 + new["matched_final_elf_functions"])
        self.assertEqual(combined["matched_baseline_bytes"], 22359 + new["matched_baseline_bytes"])
        self.assertEqual(
            combined["matched_final_elf_function_ratio_percent"],
            round(
                100
                * combined["matched_final_elf_functions"]
                / accounting["total_functions"],
                6,
            ),
        )
        self.assertEqual(
            combined["matched_baseline_byte_ratio_percent"],
            round(
                100
                * combined["matched_baseline_bytes"]
                / accounting["total_bytes"],
                6,
            ),
        )

    def test_strict_gates_and_claim_are_consistent(self):
        size = self.summary["strict_size_gate"]
        self.assertTrue(size["passed"])
        self.assertTrue(self.summary["qemu_lifecycle"]["passed"])
        self.assertTrue(self.summary["startup_ab"]["passed"])
        self.assertEqual(
            size["baseline_affected_resident_bytes"]
            - size["modular_affected_resident_bytes"],
            size["unloaded_resident_object_savings_bytes"],
        )
        self.assertEqual(
            size["loaded_module_bytes"]
            - size["unloaded_resident_object_savings_bytes"],
            size["all_new_modules_loaded_delta_bytes"],
        )
        self.assertEqual(
            size["modular_linked_permanent_bytes"]
            - size["baseline_linked_permanent_bytes"],
            size["linked_permanent_delta_bytes"],
        )
        self.assertEqual(
            size["modular_bzimage_bytes"] - size["baseline_bzimage_bytes"],
            size["bzimage_delta_bytes"],
        )
        self.assertEqual(
            self.summary["interpretation"][
                "measured_startup_page_reduction_bytes"
            ],
            -size["linked_permanent_delta_bytes"],
        )
        self.assertFalse(
            self.summary["rejected_explorations"]["io_uring"]["accepted"]
        )

    def test_linker_alignment_explains_zero_text_delta(self):
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
            self.summary["final_elf_accounting"]["READY"]["bytes"],
        )

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

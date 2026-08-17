import json
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = PROJECT_ROOT / "docs" / "full-kernel-v32-solid-summary.json"


class V32SummaryTests(unittest.TestCase):
    def setUp(self):
        self.summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))

    def test_accounting_is_a_complete_partition(self):
        accounting = self.summary["planner"]["final_elf_accounting"]
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
            expected = round(
                100
                * accounting[name]["functions"]
                / accounting["total_functions"],
                6,
            )
            self.assertEqual(accounting[name]["ratio_percent"], expected)

    def test_success_ratios_use_the_final_elf_denominator(self):
        accounting = self.summary["planner"]["final_elf_accounting"]
        validated = self.summary["validated_function_modules"]

        self.assertEqual(
            validated["matched_final_elf_function_ratio_percent"],
            round(
                100
                * validated["matched_final_elf_functions"]
                / accounting["total_functions"],
                6,
            ),
        )

        modules = validated["per_module"]
        self.assertEqual(set(modules), set(validated["modules"]))
        for field in (
            "selected_source_functions",
            "matched_final_elf_functions",
            "dependency_symbols_before_packing",
            "dependency_export_boundaries_after_packing",
        ):
            self.assertEqual(
                sum(module[field] for module in modules.values()),
                validated[field],
            )
        self.assertEqual(
            validated["matched_baseline_byte_ratio_percent"],
            round(
                100
                * validated["matched_baseline_bytes"]
                / accounting["total_bytes"],
                6,
            ),
        )

    def test_size_and_runtime_claims_are_self_consistent(self):
        size = self.summary["strict_size_gate"]
        modules = self.summary["validated_function_modules"]["per_module"]
        self.assertTrue(size["passed"])
        self.assertEqual(
            sum(
                module["baseline_resident_object_bytes"]
                for module in modules.values()
            ),
            size["baseline_affected_resident_bytes"],
        )
        self.assertEqual(
            sum(
                module["modular_resident_object_bytes"]
                for module in modules.values()
            ),
            size["modular_affected_resident_bytes"],
        )
        self.assertEqual(
            sum(
                module["unloaded_object_savings_bytes"]
                for module in modules.values()
            ),
            size["unloaded_resident_object_savings_bytes"],
        )
        self.assertEqual(
            sum(
                module["module_permanent_bytes"]
                for module in modules.values()
            ),
            size["loaded_module_bytes"],
        )
        self.assertEqual(
            size["baseline_affected_resident_bytes"]
            - size["modular_affected_resident_bytes"],
            size["unloaded_resident_object_savings_bytes"],
        )
        self.assertEqual(
            size["loaded_module_bytes"]
            - size["unloaded_resident_object_savings_bytes"],
            size["all_modules_loaded_delta_bytes"],
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
        self.assertTrue(self.summary["qemu_lifecycle"]["passed"])
        self.assertTrue(self.summary["startup_ab"]["passed"])

    def test_provenance_hashes_are_sha256(self):
        hash_fields = (
            self.summary["reference_graph"]["sha256"],
            self.summary["fresh_boot_evidence"]["trace_sha256"],
            self.summary["planner"]["plan_sha256"],
            self.summary["validated_function_modules"]["accounting_sha256"],
            self.summary["validated_function_modules"][
                "successful_validation_sha256"
            ],
            self.summary["strict_size_gate"]["report_sha256"],
            self.summary["qemu_lifecycle"]["trigger_sha256"],
            self.summary["qemu_lifecycle"]["initramfs_sha256"],
            self.summary["qemu_lifecycle"]["scenario_sha256"],
            self.summary["qemu_lifecycle"]["result_sha256"],
            self.summary["startup_ab"]["result_sha256"],
        )
        for digest in hash_fields:
            self.assertEqual(len(digest), 64)
            int(digest, 16)


if __name__ == "__main__":
    unittest.main()

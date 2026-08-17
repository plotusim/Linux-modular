import json
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = (
    PROJECT_ROOT
    / "docs"
    / "full-kernel-v35-state-safe-swap-summary.json"
)


class V35SummaryTests(unittest.TestCase):
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

    def test_new_module_accounting_is_exact(self):
        module = self.summary["new_strict_module"]
        accounting = self.summary["final_elf_accounting"]
        self.assertEqual(module["selected_source_functions"], 28)
        self.assertEqual(
            len(module["matched_functions"]),
            module["matched_final_elf_functions"],
        )
        self.assertEqual(
            sum(item["size_bytes"] for item in module["matched_functions"]),
            module["matched_baseline_bytes"],
        )
        self.assertEqual(
            module["matched_function_ratio_percent"],
            round(
                100
                * module["matched_final_elf_functions"]
                / accounting["total_functions"],
                6,
            ),
        )
        self.assertEqual(
            module["matched_byte_ratio_percent"],
            round(
                100
                * module["matched_baseline_bytes"]
                / accounting["total_bytes"],
                6,
            ),
        )
        self.assertTrue(module["llvm_graph_coverage_complete"])

    def test_lifetime_state_stays_resident(self):
        module = self.summary["new_strict_module"]
        globals_ = set(module["auto_resident_frontier_globals"])
        reasons = module["intrinsic_resident_reasons"]
        self.assertIn("swap_info", globals_)
        self.assertIn("swapon_mutex", globals_)
        self.assertEqual(
            reasons["swap_info"], "mutable_module_lifetime_state"
        )
        self.assertEqual(
            reasons["swap_discard_work"], "escaped_function_address"
        )
        self.assertEqual(reasons["max_swapfile_size"], "weak_linkage")
        self.assertEqual(module["selected_source_globals"], 0)

    def test_incremental_size_math_and_strict_acceptance(self):
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
        self.assertTrue(size["strict_release_gate_passed"])
        self.assertGreater(size["unloaded_resident_savings_bytes"], 0)
        self.assertLess(size["bzimage_delta_bytes"], 0)
        self.assertEqual(size["linked_permanent_delta_bytes"], 0)

    def test_linker_alignment_explains_zero_final_page_delta(self):
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
            layout["additional_payload_reduction_to_previous_boundary_bytes"],
            0,
        )

    def test_mechanism_and_strict_sets_reconcile(self):
        mechanism = self.summary["combined_mechanism_validated"]
        strict = self.summary["combined_strict_qualified"]
        accounting = self.summary["final_elf_accounting"]
        self.assertEqual(mechanism["modules"], strict["modules"] + 1)
        self.assertEqual(
            mechanism["selected_source_functions"],
            strict["selected_source_functions"] + 9,
        )
        self.assertEqual(
            mechanism["matched_final_elf_functions"],
            strict["matched_final_elf_functions"] + 4,
        )
        self.assertEqual(
            mechanism["matched_baseline_bytes"],
            strict["matched_baseline_bytes"] + 2013,
        )
        self.assertEqual(
            mechanism["unloaded_resident_object_savings_bytes"],
            strict["unloaded_resident_object_savings_bytes"] + 1613,
        )
        for result in (mechanism, strict):
            self.assertEqual(
                result["matched_final_elf_function_ratio_percent"],
                round(
                    100
                    * result["matched_final_elf_functions"]
                    / accounting["total_functions"],
                    6,
                ),
            )
            self.assertEqual(
                result["matched_baseline_byte_ratio_percent"],
                round(
                    100
                    * result["matched_baseline_bytes"]
                    / accounting["total_bytes"],
                    6,
                ),
            )

    def test_runtime_gates_pass(self):
        lifecycle = self.summary["qemu_lifecycle"]
        self.assertTrue(lifecycle["passed"])
        self.assertEqual(lifecycle["repeats"], 4)
        self.assertEqual(lifecycle["completed_steps_per_run"], 11)
        self.assertEqual(len(lifecycle["runs"]), lifecycle["repeats"])
        self.assertTrue(all(run["elapsed_seconds"] > 0 for run in lifecycle["runs"]))
        self.assertTrue(self.summary["startup_ab"]["passed"])
        self.assertEqual(self.summary["startup_ab"]["pairs"], 8)
        self.assertEqual(self.summary["startup_ab"]["boots"], 16)

    def test_all_provenance_hashes_are_sha256(self):
        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.endswith("sha256"):
                        self.assertIsInstance(child, str)
                        self.assertEqual(len(child), 64)
                        int(child, 16)
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(self.summary)


if __name__ == "__main__":
    unittest.main()

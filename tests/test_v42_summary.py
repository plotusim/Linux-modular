import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "docs/full-kernel-v42-expanded-callback-summary.json"
V41_PATH = ROOT / "docs/full-kernel-v41-broad-callback-summary.json"


class V42SummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        cls.v41 = json.loads(V41_PATH.read_text(encoding="utf-8"))

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
        self.assertAlmostEqual(
            sum(accounting[name]["ratio_percent"] for name in categories),
            100.0,
            places=5,
        )
        self.assertAlmostEqual(
            sum(accounting[name]["byte_ratio_percent"] for name in categories),
            100.0,
            places=5,
        )

    def test_relaxed_selection_expands_planner_core_callbacks(self):
        selection = self.summary["selection"]
        modules = self.summary["modules"]
        self.assertTrue(selection["relaxed_exploration_requested"])
        self.assertEqual(len(modules), 15)
        self.assertEqual(selection["selected_source_translation_units"], 15)
        self.assertEqual(selection["selected_callback_tables"], 15)
        self.assertEqual(
            sum(module["lazy_interfaces"] for module in modules.values()),
            selection["selected_lazy_interfaces"],
        )
        self.assertEqual(selection["selected_lazy_interfaces"], 49)
        self.assertEqual(
            selection["moved_functions_originally_classified_CORE"], 89
        )
        self.assertEqual(
            selection["moved_functions_originally_classified_READY"], 0
        )
        self.assertEqual(
            selection["excluded_after_build_analysis"]["candidate"],
            "task_mmu",
        )

    def test_source_closure_and_elf_accounting_reconcile(self):
        modules = self.summary["modules"]
        accounting = self.summary["source_closure_elf_accounting"]
        self.assertTrue(accounting["passed"])
        self.assertEqual(
            sum(module["moved_source_functions"] for module in modules.values()),
            accounting["moved_source_function_definitions"],
        )
        self.assertEqual(
            sum(
                module["matched_final_elf_functions"]
                for module in modules.values()
            ),
            accounting["matched_final_elf_functions"],
        )
        self.assertEqual(
            sum(module["matched_baseline_bytes"] for module in modules.values()),
            accounting["matched_baseline_bytes"],
        )
        self.assertEqual(
            accounting["unmatched_inlined_or_eliminated_definitions"],
            accounting["moved_source_function_definitions"]
            - accounting["matched_final_elf_functions"],
        )
        self.assertTrue(
            all(
                module["llvm_graph_coverage_complete"]
                for module in modules.values()
            )
        )

    def test_combined_size_gate_reconciles_every_module(self):
        modules = self.summary["modules"]
        combined = self.summary["incremental_size"]["combined_strict_gate"]
        self.assertTrue(combined["passed"])
        for module in modules.values():
            self.assertEqual(
                module["baseline_resident_object_bytes"]
                - module["modular_resident_object_bytes"],
                module["unloaded_resident_savings_bytes"],
            )
            self.assertEqual(
                module["loaded_module_bytes"]
                - module["unloaded_resident_savings_bytes"],
                module["all_loaded_delta_bytes"],
            )
            self.assertGreater(module["unloaded_resident_savings_bytes"], 0)
        for field in (
            "baseline_resident_object_bytes",
            "modular_resident_object_bytes",
            "unloaded_resident_savings_bytes",
            "loaded_module_bytes",
            "all_loaded_delta_bytes",
        ):
            self.assertEqual(
                sum(module[field] for module in modules.values()),
                combined[field],
            )
        self.assertEqual(
            combined["loaded_module_bytes"]
            - combined["unloaded_resident_savings_bytes"],
            combined["all_loaded_delta_bytes"],
        )
        self.assertLess(combined["linked_permanent_delta_bytes"], 0)
        self.assertLess(combined["bzimage_delta_bytes"], 0)

    def test_build_and_lifecycle_validation_pass(self):
        build = self.summary["build"]
        self.assertTrue(build["passed"])
        self.assertTrue(build["modpost_passed"])
        self.assertEqual(build["built_modules"], len(self.summary["modules"]))
        self.assertEqual(build["bzimage_file_bytes"], 4_998_080)
        lifecycle = self.summary["qemu_lifecycle"]
        self.assertTrue(lifecycle["passed"])
        self.assertEqual(
            lifecycle["completed_steps_total"],
            lifecycle["repeats"] * lifecycle["completed_steps_per_run"],
        )
        for field in (
            "initially_absent",
            "real_feature_first_use_autoload_each",
            "unload_each",
            "all_modules_reload_and_unload",
            "same_eventfd_cross_unload_reload",
            "four_process_concurrent_first_bind",
            "dmesg_clean",
            "relay_read_and_poll_covered",
        ):
            self.assertTrue(lifecycle[field])
        self.assertFalse(lifecycle["relay_splice_success_claimed"])

    def test_startup_ab_preserves_the_failed_first_batch(self):
        startup = self.summary["startup_ab"]
        self.assertTrue(startup["passed"])
        self.assertEqual(startup["pairs"], 16)
        self.assertEqual(startup["boots"], 32)
        self.assertFalse(startup["first_eight_pair_batch_passed"])
        self.assertTrue(startup["second_eight_pair_batch_passed"])
        self.assertTrue(startup["combined_sixteen_pair_batch_passed"])
        self.assertIn("+80 KiB", startup["first_batch_failure"])
        delta = startup["deltas_modular_minus_baseline"]
        self.assertEqual(delta["permanent_kernel_kb"], -1.0)
        self.assertLessEqual(delta["ready_uptime_seconds"], 0.25)
        self.assertGreaterEqual(delta["mem_available_kb"], -128)
        self.assertLessEqual(delta["slab_kb"], 64)

    def test_cumulative_totals_reconcile_with_v41(self):
        previous = self.v41["combined_strict_qualified"]
        current = self.summary["combined_strict_qualified"]
        increment = self.summary["v42_increment"]
        for field in (
            "modules",
            "selected_source_functions",
            "matched_final_elf_functions",
            "matched_baseline_bytes",
            "unloaded_resident_object_savings_bytes",
        ):
            increment_field = "new_modules" if field == "modules" else field
            self.assertEqual(
                current[field], previous[field] + increment[increment_field]
            )
        accounting = self.summary["final_elf_accounting"]
        self.assertAlmostEqual(
            current["matched_final_elf_function_ratio_percent"],
            100.0
            * current["matched_final_elf_functions"]
            / accounting["total_functions"],
            places=6,
        )
        self.assertAlmostEqual(
            current["matched_baseline_byte_ratio_percent"],
            100.0 * current["matched_baseline_bytes"] / accounting["total_bytes"],
            places=6,
        )
        self.assertEqual(
            current["current_bzimage_delta_bytes"],
            current["current_bzimage_bytes"] - current["pure_v32_bzimage_bytes"],
        )

    def test_provenance_interpretation_and_readme_are_honest(self):
        def validate_sha(value):
            self.assertIsInstance(value, str)
            self.assertEqual(len(value), 64)
            int(value, 16)

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.endswith("sha256"):
                        validate_sha(child)
                    elif key.endswith("sha256s"):
                        for digest in child:
                            validate_sha(digest)
                    else:
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(self.summary)

        interpretation = self.summary["interpretation"]
        self.assertTrue(
            interpretation["relaxed_selection_still_passed_strict_release_gate"]
        )
        self.assertIn("31033", interpretation["all_loaded_tradeoff"])
        self.assertIn("not sufficient", interpretation["theory"])
        self.assertIn("task_mmu", interpretation["excluded_candidate"])
        self.assertIn("not reported", interpretation["relay_limit"])
        self.assertIn("first 8-pair batch failed", interpretation["startup_noise"])

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("v42", readme)
        self.assertIn("298/28,959", readme)
        self.assertIn("V42_EXPANDED_CALLBACK_PORTFOLIO_RESULT.md", readme)


if __name__ == "__main__":
    unittest.main()

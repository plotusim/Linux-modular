import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "docs/full-kernel-v41-broad-callback-summary.json"
V40_PATH = ROOT / "docs/full-kernel-v40-portfolio-summary.json"


class V41SummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        cls.v40 = json.loads(V40_PATH.read_text(encoding="utf-8"))

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
            sum(
                accounting[name]["byte_ratio_percent"]
                for name in categories
            ),
            100.0,
            places=5,
        )

    def test_relaxed_selection_rewrites_planner_core_callbacks(self):
        selection = self.summary["selection"]
        self.assertTrue(selection["relaxed_exploration_requested"])
        self.assertEqual(selection["selected_source_translation_units"], 4)
        self.assertEqual(selection["selected_callback_tables"], 23)
        self.assertEqual(selection["selected_lazy_interfaces"], 37)
        self.assertEqual(
            selection["moved_functions_originally_classified_CORE"], 62
        )
        self.assertEqual(
            selection["moved_functions_originally_classified_READY"], 0
        )

    def test_source_closures_and_elf_matches_reconcile(self):
        modules = self.summary["modules"]
        accounting = self.summary["source_closure_elf_accounting"]
        self.assertEqual(len(modules), 4)
        self.assertEqual(
            sum(item["moved_source_functions"] for item in modules.values()),
            accounting["moved_source_function_definitions"],
        )
        self.assertEqual(
            sum(
                len(item["moved_source_globals"])
                for item in modules.values()
            ),
            accounting["moved_source_global_definitions"],
        )
        self.assertEqual(
            sum(
                item["matched_final_elf_functions"]
                for item in modules.values()
            ),
            accounting["matched_final_elf_functions"],
        )
        self.assertEqual(
            sum(item["matched_baseline_bytes"] for item in modules.values()),
            accounting["matched_baseline_bytes"],
        )
        for module in modules.values():
            self.assertTrue(module["llvm_graph_coverage_complete"])
            self.assertEqual(
                len(module["matched_functions"]),
                module["matched_final_elf_functions"],
            )
            self.assertEqual(
                sum(item["size_bytes"] for item in module["matched_functions"]),
                module["matched_baseline_bytes"],
            )
        self.assertEqual(
            accounting["unmatched_inlined_or_eliminated_definitions"],
            accounting["moved_source_function_definitions"]
            - accounting["matched_final_elf_functions"],
        )

    def test_combined_strict_size_gate_reconciles_isolated_observations(self):
        size = self.summary["incremental_size"]
        combined = size["combined_strict_gate"]
        isolated = size["isolated_observations"].values()
        self.assertTrue(combined["passed"])
        self.assertEqual(
            sum(item["unloaded_resident_savings_bytes"] for item in isolated),
            combined["unloaded_resident_savings_bytes"],
        )
        self.assertEqual(
            combined["loaded_module_bytes"]
            - combined["unloaded_resident_savings_bytes"],
            combined["all_loaded_delta_bytes"],
        )
        self.assertLess(combined["linked_permanent_delta_bytes"], 0)
        self.assertLess(combined["bzimage_delta_bytes"], 0)

    def test_build_linker_lifecycle_and_startup_gates_pass(self):
        self.assertTrue(self.summary["build"]["passed"])
        self.assertTrue(self.summary["build"]["modpost_passed"])
        self.assertEqual(self.summary["test_suite"]["tests_passed_total"], 276)
        layout = self.summary["linker_alignment"]
        self.assertEqual(layout["incremental_payload_delta_bytes"], -12608)
        self.assertEqual(layout["final_text_delta_bytes"], 0)
        self.assertGreater(
            layout["additional_payload_reduction_to_previous_boundary_bytes"],
            0,
        )
        lifecycle = self.summary["qemu_lifecycle"]
        self.assertTrue(lifecycle["passed"])
        self.assertEqual(
            lifecycle["completed_steps_total"],
            lifecycle["repeats"] * lifecycle["completed_steps_per_run"],
        )
        for key in (
            "initially_absent",
            "isolated_first_use_autoload_each",
            "same_fd_cross_unload_reload_each",
            "four_process_concurrent_first_use",
            "all_modules_unload_reload",
            "dmesg_clean",
        ):
            self.assertTrue(lifecycle[key])
        startup = self.summary["startup_ab"]
        self.assertTrue(startup["passed"])
        self.assertEqual(startup["pairs"], 16)
        self.assertEqual(startup["boots"], 32)
        delta = startup["deltas_modular_minus_baseline"]
        self.assertLessEqual(delta["permanent_kernel_kb"], 0)
        self.assertLessEqual(delta["ready_uptime_seconds"], 0.25)
        self.assertGreaterEqual(delta["mem_available_kb"], -128)
        self.assertLessEqual(delta["slab_kb"], 64)

    def test_cumulative_totals_reconcile_with_v40(self):
        previous = self.v40["combined_strict_qualified"]
        current = self.summary["combined_strict_qualified"]
        increment = self.summary["v41_increment"]
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
            current["current_bzimage_bytes"]
            - current["pure_v32_bzimage_bytes"],
        )

    def test_provenance_hashes_and_interpretation_are_honest(self):
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
        interpretation = self.summary["interpretation"]
        self.assertTrue(
            interpretation["relaxed_selection_still_passed_strict_release_gate"]
        )
        self.assertTrue(
            interpretation["all_new_moved_functions_were_planner_core"]
        )
        self.assertEqual(
            interpretation["measured_startup_page_reduction_bytes"], 0
        )
        self.assertIn("not sufficient", interpretation["theory"])
        self.assertIn("systemd", interpretation["workload_limit"])
        self.assertIn("13343", interpretation["all_loaded_tradeoff"])

    def test_readme_exposes_current_result(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("v41", readme)
        self.assertIn("242/28,959", readme)
        self.assertIn("V41_BROAD_CALLBACK_PORTFOLIO_RESULT.md", readme)


if __name__ == "__main__":
    unittest.main()

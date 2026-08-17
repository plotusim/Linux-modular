import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "docs/full-kernel-v40-portfolio-summary.json"
V39_PATH = ROOT / "docs/full-kernel-v39-trace-callback-summary.json"


class V40SummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        cls.v39 = json.loads(V39_PATH.read_text(encoding="utf-8"))

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

    def test_full_graph_recommends_the_validated_portfolio(self):
        ranking = self.summary["callback_portfolio_ranking"]
        self.assertEqual(ranking["recognized_callback_tables"], 685)
        self.assertEqual(ranking["ready_callback_tables"], 154)
        self.assertEqual(ranking["release_savings_threshold_bytes"], 4096)
        self.assertEqual(ranking["estimated_standalone_release_groups"], 1)
        self.assertEqual(
            ranking["estimated_complementary_pair_portfolios"], 54
        )
        self.assertEqual(ranking["selected_portfolio_rank"], 3)
        self.assertEqual(ranking["selected_estimated_direct_net_bytes"], 6288)
        self.assertEqual(
            set(ranking["selected_source_paths"]),
            {"fs/proc/base.c", "drivers/dma-buf/sync_file.c"},
        )

    def test_source_closures_and_final_elf_matches_are_exact(self):
        modules = self.summary["modules"]
        proc = modules["deferred_proc_base_v40_cb"]
        sync = modules["deferred_sync_file_v40_cb"]
        self.assertEqual(len(proc["selected_callback_tables"]), 13)
        self.assertEqual(len(proc["selected_lazy_interfaces"]), 20)
        self.assertEqual(len(proc["moved_functions"]), 25)
        self.assertEqual(len(sync["selected_lazy_interfaces"]), 2)
        self.assertEqual(len(sync["moved_functions"]), 10)
        for module in (proc, sync):
            self.assertTrue(module["llvm_graph_coverage_complete"])
            self.assertEqual(
                len(module["matched_functions"]),
                module["matched_final_elf_functions"],
            )
            self.assertEqual(
                sum(item["size_bytes"] for item in module["matched_functions"]),
                module["matched_baseline_bytes"],
            )

    def test_complementary_size_gate_is_not_conflated_with_individual_gates(self):
        size = self.summary["incremental_size"]
        proc = size["individual_proc_gate"]
        sync = size["individual_sync_file_gate"]
        combined = size["combined_portfolio_gate"]
        self.assertFalse(proc["passed"])
        self.assertGreater(proc["bzimage_delta_bytes"], 0)
        self.assertFalse(sync["passed"])
        self.assertLess(sync["unloaded_resident_savings_bytes"], 4096)
        self.assertTrue(combined["passed"])
        self.assertEqual(
            combined["unloaded_resident_savings_bytes"],
            proc["unloaded_resident_savings_bytes"]
            + sync["unloaded_resident_savings_bytes"],
        )
        self.assertEqual(
            combined["loaded_module_bytes"],
            proc["loaded_module_bytes"] + sync["loaded_module_bytes"],
        )
        self.assertEqual(
            combined["all_loaded_delta_bytes"],
            combined["loaded_module_bytes"]
            - combined["unloaded_resident_savings_bytes"],
        )
        self.assertLess(combined["bzimage_delta_bytes"], 0)
        self.assertEqual(combined["linked_permanent_delta_bytes"], 0)

    def test_build_and_linker_alignment_evidence_agree(self):
        build = self.summary["build"]
        layout = self.summary["linker_alignment"]
        self.assertTrue(build["passed"])
        self.assertTrue(build["modpost_passed"])
        self.assertEqual(build["generated_source_warnings"], 0)
        self.assertEqual(
            layout["incremental_payload_delta_bytes"],
            layout["current_payload_before_alignment_bytes"]
            - layout["v39_payload_before_alignment_bytes"],
        )
        self.assertEqual(
            layout["cumulative_payload_delta_bytes"],
            layout["current_payload_before_alignment_bytes"]
            - layout["pure_v32_payload_before_alignment_bytes"],
        )
        self.assertEqual(layout["final_text_delta_bytes"], 0)
        self.assertGreater(
            layout["additional_payload_reduction_to_previous_boundary_bytes"],
            0,
        )

    def test_runtime_lifecycle_and_startup_gates_pass(self):
        lifecycle = self.summary["qemu_lifecycle"]
        startup = self.summary["startup_ab"]
        self.assertTrue(lifecycle["passed"])
        self.assertEqual(
            lifecycle["completed_steps_total"],
            lifecycle["repeats"] * lifecycle["completed_steps_per_run"],
        )
        for key in (
            "proc_first_use_autoload",
            "proc_same_fd_cross_unload_reload",
            "sync_same_fd_cross_unload_reload",
            "four_way_concurrent_first_use",
            "both_modules_unload_reload",
            "dmesg_clean",
        ):
            self.assertTrue(lifecycle[key])
        self.assertTrue(startup["passed"])
        self.assertEqual(startup["boots"], startup["pairs"] * 2)
        self.assertLessEqual(
            startup["deltas_modular_minus_baseline"][
                "permanent_kernel_kb"
            ],
            0,
        )
        self.assertLessEqual(
            startup["deltas_modular_minus_baseline"][
                "ready_uptime_seconds"
            ],
            0.25,
        )
        self.assertGreaterEqual(
            startup["deltas_modular_minus_baseline"]["mem_available_kb"],
            -128,
        )
        self.assertLessEqual(
            startup["deltas_modular_minus_baseline"]["slab_kb"], 64
        )

    def test_cumulative_strict_totals_reconcile_with_v39(self):
        previous = self.v39["combined_strict_qualified"]
        current = self.summary["combined_strict_qualified"]
        increment = self.summary["v40_increment"]
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
            100.0 * current["matched_baseline_bytes"]
            / accounting["total_bytes"],
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
        self.assertTrue(interpretation["new_portfolio_release_qualified"])
        self.assertEqual(
            interpretation["measured_startup_page_reduction_bytes"], 0
        )
        self.assertIn("necessary", interpretation["theory"])
        self.assertIn("__init", interpretation["init_only_rule"])
        self.assertIn("does not automatically unload", interpretation["demand_rule"])


if __name__ == "__main__":
    unittest.main()

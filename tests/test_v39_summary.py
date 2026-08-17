import json
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = (
    PROJECT_ROOT
    / "docs"
    / "full-kernel-v39-trace-callback-summary.json"
)


class V39SummaryTests(unittest.TestCase):
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

    def test_full_graph_callback_census_is_reconciled(self):
        census = self.summary["callback_table_census"]
        self.assertEqual(
            census["ready_callback_tables"]
            + census["review_required_callback_tables"]
            + census["no_safe_cold_callback_tables"],
            census["recognized_callback_tables"],
        )
        self.assertEqual(
            census["raw_local_initializer_edges"]
            - census["duplicate_initializer_evidence_edges_removed"],
            census["unique_local_initializer_targets"],
        )
        self.assertGreater(census["ready_positive_direct_tables"], 0)
        self.assertGreater(
            census["positive_direct_net_bytes_without_source_closure"],
            0,
        )

    def test_bridgeability_guards_are_scoped(self):
        trace = self.summary["v39_trace"]
        self.assertEqual(len(trace["selected_callback_tables"]), 21)
        self.assertEqual(trace["ranked_cold_interfaces"], 35)
        self.assertEqual(len(trace["selected_lazy_interfaces"]), 33)
        self.assertEqual(
            set(trace["automatic_unbridgeable_interfaces"]),
            {"tracing_mark_write", "tracing_stats_read"},
        )
        self.assertIn(
            "static-key",
            trace["automatic_unbridgeable_interfaces"]
            ["tracing_mark_write"][0],
        )
        self.assertIn(
            "unspellable",
            trace["automatic_unbridgeable_interfaces"]
            ["tracing_stats_read"][0],
        )
        self.assertTrue(trace["llvm_graph_coverage_complete"])

    def test_new_module_has_exact_elf_accounting(self):
        trace = self.summary["v39_trace"]
        accounting = self.summary["final_elf_accounting"]
        self.assertEqual(
            len(trace["moved_functions"]),
            trace["selected_source_functions"],
        )
        self.assertEqual(
            len(trace["matched_functions"]),
            trace["matched_final_elf_functions"],
        )
        self.assertEqual(
            sum(item["size_bytes"] for item in trace["matched_functions"]),
            trace["matched_baseline_bytes"],
        )
        self.assertEqual(
            trace["matched_function_ratio_percent"],
            round(
                100
                * trace["matched_final_elf_functions"]
                / accounting["total_functions"],
                6,
            ),
        )
        self.assertEqual(
            trace["matched_byte_ratio_percent"],
            round(
                100
                * trace["matched_baseline_bytes"]
                / accounting["total_bytes"],
                6,
            ),
        )

    def test_strict_size_math_and_cumulative_gain(self):
        size = self.summary["v39_incremental_size"]
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
            size["modular_bzimage_bytes"]
            - size["baseline_bzimage_bytes"],
            size["bzimage_delta_bytes"],
        )
        self.assertTrue(size["strict_release_gate_passed"])
        self.assertGreaterEqual(
            size["unloaded_resident_savings_bytes"],
            size["minimum_resident_savings_bytes"],
        )
        self.assertEqual(size["linked_permanent_delta_bytes"], 0)

        strict = self.summary["combined_strict_qualified"]
        increment = self.summary["v39_increment"]
        self.assertEqual(strict["modules"], 15)
        self.assertEqual(strict["selected_source_functions"], 274)
        self.assertEqual(strict["matched_final_elf_functions"], 175)
        self.assertEqual(
            strict["current_bzimage_bytes"]
            - strict["pure_v32_bzimage_bytes"],
            strict["current_bzimage_delta_bytes"],
        )
        self.assertEqual(
            increment["unloaded_resident_object_savings_bytes"],
            size["unloaded_resident_savings_bytes"],
        )

    def test_linker_alignment_explains_zero_page_gain(self):
        layout = self.summary["linker_alignment"]
        self.assertEqual(
            layout["current_payload_before_alignment_bytes"]
            - layout["pure_v32_payload_before_alignment_bytes"],
            layout["cumulative_payload_delta_bytes"],
        )
        self.assertEqual(
            layout["current_padding_before_alignment_bytes"]
            - layout["pure_v32_padding_before_alignment_bytes"],
            -layout["cumulative_payload_delta_bytes"],
        )
        self.assertEqual(layout["final_text_delta_bytes"], 0)
        self.assertEqual(
            self.summary["interpretation"]
            ["measured_startup_page_reduction_bytes"],
            0,
        )

    def test_runtime_lifecycle_and_startup_gates_pass(self):
        lifecycle = self.summary["qemu_lifecycle"]
        self.assertTrue(lifecycle["passed"])
        self.assertEqual(lifecycle["repeats"], 4)
        self.assertEqual(lifecycle["completed_steps_per_run"], 10)
        self.assertTrue(lifecycle["open_fd_module_unload"])
        self.assertTrue(lifecycle["same_fd_callback_reload"])
        self.assertTrue(lifecycle["trace_pipe_poll_read_splice_covered"])
        self.assertTrue(lifecycle["raw_buffer_poll_read_splice_covered"])
        self.assertTrue(lifecycle["four_way_concurrent_first_use"])
        self.assertTrue(lifecycle["dmesg_clean"])
        startup = self.summary["startup_ab"]
        self.assertTrue(startup["passed"])
        self.assertEqual(startup["pairs"], 8)
        self.assertEqual(startup["boots"], 16)
        self.assertEqual(
            startup["deltas_modular_minus_baseline"]
            ["permanent_kernel_kb"],
            0.0,
        )

    def test_all_provenance_hashes_are_sha256(self):
        self.assertEqual(
            self.summary["test_suite"]["tests_passed_total"], 254
        )

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

import json
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = (
    PROJECT_ROOT
    / "docs"
    / "full-kernel-v38-callback-table-summary.json"
)


class V38SummaryTests(unittest.TestCase):
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

    def test_new_modules_have_exact_elf_accounting(self):
        accounting = self.summary["final_elf_accounting"]
        for key in ("v37_io_uring", "v38_perf"):
            module = self.summary[key]
            self.assertEqual(
                len(module["moved_functions"]),
                module["selected_source_functions"],
            )
            self.assertEqual(
                len(module["matched_functions"]),
                module["matched_final_elf_functions"],
            )
            self.assertEqual(
                sum(
                    item["size_bytes"]
                    for item in module["matched_functions"]
                ),
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

    def test_callback_table_promotion_is_scoped_and_reviewable(self):
        perf = self.summary["v38_perf"]
        self.assertEqual(perf["promoted_callback_table"], "perf_fops")
        self.assertEqual(
            set(perf["promoted_callback_interfaces"]),
            {
                "perf_compat_ioctl",
                "perf_fasync",
                "perf_ioctl",
                "perf_mmap",
                "perf_poll",
                "perf_read",
            },
        )
        self.assertEqual(
            perf["excluded_callback_interfaces"], ["perf_release"]
        )
        self.assertTrue(perf["llvm_graph_coverage_complete"])
        self.assertEqual(perf["resident_dependency_export_count"], 0)
        gain = perf["callback_promotion_gain_vs_syscall_only"]
        self.assertEqual(
            gain["callback_closure_unloaded_resident_savings_bytes"],
            self.summary["v38_incremental_size"][
                "unloaded_resident_savings_bytes"
            ],
        )
        self.assertGreater(gain["resident_savings_multiplier"], 3.0)

    def test_published_runtime_tables_remain_resident(self):
        perf = self.summary["v38_perf"]
        self.assertEqual(perf["moved_globals"], ["if_tokens"])
        self.assertIn("perf_fops", perf["auto_resident_globals"])
        self.assertIn("perf_mmap_vmops", perf["auto_resident_globals"])
        self.assertEqual(
            perf["lifetime_frontier"]["perf_mmap_vmops"],
            "source_address_taken_global",
        )
        for callback in (
            "perf_mmap_open",
            "perf_mmap_close",
            "perf_mmap_fault",
            "perf_release",
        ):
            self.assertEqual(
                perf["lifetime_frontier"][callback],
                "escaped_function_address",
            )

    def test_strict_size_math_and_cumulative_gain(self):
        size = self.summary["v38_incremental_size"]
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
        increment = self.summary["v37_v38_increment"]
        self.assertEqual(strict["modules"], 14)
        self.assertEqual(strict["selected_source_functions"], 231)
        self.assertEqual(strict["matched_final_elf_functions"], 140)
        self.assertEqual(
            strict["current_bzimage_bytes"]
            - strict["pure_v32_bzimage_bytes"],
            strict["current_bzimage_delta_bytes"],
        )
        self.assertEqual(
            increment["unloaded_resident_object_savings_bytes"],
            self.summary["v37_io_uring"][
                "unloaded_resident_savings_bytes"
            ]
            + size["unloaded_resident_savings_bytes"],
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
            self.summary["interpretation"][
                "measured_startup_page_reduction_bytes"
            ],
            0,
        )

    def test_runtime_lifecycle_and_startup_gates_pass(self):
        lifecycle = self.summary["qemu_lifecycle"]
        self.assertTrue(lifecycle["passed"])
        self.assertEqual(lifecycle["repeats"], 4)
        self.assertEqual(lifecycle["completed_steps_per_run"], 10)
        self.assertTrue(lifecycle["live_vma_module_unload"])
        self.assertTrue(lifecycle["live_vma_fault_after_unload"])
        self.assertTrue(lifecycle["live_vma_close_after_unload"])
        self.assertTrue(lifecycle["four_way_concurrent_first_use"])
        self.assertTrue(lifecycle["dmesg_clean"])
        startup = self.summary["startup_ab"]
        self.assertTrue(startup["passed"])
        self.assertEqual(startup["pairs"], 8)
        self.assertEqual(startup["boots"], 16)
        self.assertEqual(
            startup["deltas_modular_minus_baseline"][
                "permanent_kernel_kb"
            ],
            0.0,
        )

    def test_all_provenance_hashes_are_sha256(self):
        self.assertEqual(
            self.summary["test_suite"]["tests_passed_total"], 238
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

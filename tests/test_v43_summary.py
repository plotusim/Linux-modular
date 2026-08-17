import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "docs/full-kernel-v43-automatic-portfolio-summary.json"
V42_PATH = ROOT / "docs/full-kernel-v42-expanded-callback-summary.json"


class V43SummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        cls.v42 = json.loads(V42_PATH.read_text(encoding="utf-8"))

    def test_full_graph_portfolio_is_reconciled(self):
        portfolio = self.summary["automatic_portfolio"]
        census = portfolio["callback_census"]
        states = portfolio["result_states"]
        self.assertEqual(
            census["ready_callback_tables"],
            census["selected_callback_tables"],
        )
        self.assertEqual(
            sum(states.values()), census["selected_source_groups"]
        )
        self.assertEqual(states["PREPARED"], 33)
        self.assertEqual(states["FAILED"], len(portfolio["failure_boundaries"]))
        self.assertTrue(portfolio["all_prepared_llvm_graph_closures_complete"])
        self.assertEqual(
            sum(portfolio["prepared_original_planner_dispositions"].values()),
            portfolio["prepared_source_function_definitions"],
        )
        self.assertEqual(
            portfolio["prepared_original_planner_dispositions"]["CORE"], 139
        )
        self.assertIn("PREPARED proves", portfolio["state_semantics"])

    def test_retained_modules_reconcile_source_elf_and_size(self):
        modules = self.summary["modules"]
        closure = self.summary["source_closure_elf_accounting"]
        size = self.summary["incremental_size"]
        self.assertEqual(len(modules), 8)
        self.assertEqual(
            sum(item["moved_source_functions"] for item in modules.values()),
            closure["moved_source_function_definitions"],
        )
        self.assertEqual(
            sum(item["lazy_interfaces"] for item in modules.values()),
            self.summary["v43_increment"]["selected_lazy_interfaces"],
        )
        self.assertEqual(
            sum(item["matched_final_elf_functions"] for item in modules.values()),
            closure["matched_final_elf_functions"],
        )
        self.assertEqual(
            sum(item["matched_baseline_bytes"] for item in modules.values()),
            closure["matched_baseline_bytes"],
        )
        for item in modules.values():
            self.assertEqual(
                item["baseline_resident_object_bytes"]
                - item["modular_resident_object_bytes"],
                item["unloaded_resident_savings_bytes"],
            )
            self.assertEqual(
                item["loaded_module_bytes"]
                - item["unloaded_resident_savings_bytes"],
                item["all_loaded_delta_bytes"],
            )
            self.assertGreater(item["unloaded_resident_savings_bytes"], 0)
        for field in (
            "baseline_resident_object_bytes",
            "modular_resident_object_bytes",
            "unloaded_resident_savings_bytes",
            "loaded_module_bytes",
            "all_loaded_delta_bytes",
        ):
            self.assertEqual(
                sum(item[field] for item in modules.values()), size[field]
            )

    def test_relaxed_audition_preserves_rollbacks_and_alignment_failures(self):
        audition = self.summary["candidate_audition"]
        positive = audition["individual_object_positive"]
        rejected = audition["individual_object_non_positive_and_rolled_back"]
        self.assertEqual(len(positive), 6)
        self.assertEqual(sum(item["retained"] for item in positive), 2)
        self.assertTrue(all(item["unloaded_resident_savings_bytes"] > 0 for item in positive))
        self.assertTrue(all(item["unloaded_resident_savings_bytes"] <= 0 for item in rejected))
        trials = {item["modules"]: item for item in audition["combination_trials"]}
        self.assertTrue(trials[8]["passed"])
        self.assertEqual(trials[8]["linked_permanent_delta_bytes"], -64)
        self.assertFalse(trials[9]["passed"])
        self.assertFalse(trials[12]["passed"])
        self.assertEqual(trials[9]["linked_permanent_delta_bytes"], 4032)
        self.assertEqual(trials[12]["linked_permanent_delta_bytes"], 4032)

    def test_build_size_and_lifecycle_pass(self):
        size = self.summary["incremental_size"]
        self.assertTrue(size["passed"])
        self.assertFalse(size["gate"]["strict_4096_byte_candidate_gate"])
        self.assertEqual(size["unloaded_resident_savings_bytes"], 6924)
        self.assertEqual(size["linked_permanent_delta_bytes"], -64)
        self.assertEqual(size["bzimage_delta_bytes"], -9280)
        build = self.summary["build"]
        self.assertTrue(build["passed"])
        self.assertTrue(build["modpost_passed"])
        self.assertEqual(build["new_modules_built"], len(self.summary["modules"]))
        self.assertEqual(build["bzimage_file_bytes"], 4_988_800)
        lifecycle = self.summary["qemu_lifecycle"]
        self.assertTrue(lifecycle["passed"])
        self.assertEqual(
            lifecycle["completed_steps_total"],
            lifecycle["repeats"] * lifecycle["completed_steps_per_run"],
        )
        for field in (
            "all_eight_absent_at_loader_ready",
            "real_feature_first_use_autoload_each",
            "unload_each",
            "reload_after_unload",
            "combined_reload_and_unload",
            "timerfd_poll_and_read_covered",
            "mqueue_open_poll_and_read_covered",
            "regmap_read_and_cache_controls_covered",
            "dmesg_clean",
        ):
            self.assertTrue(lifecycle[field])

    def test_cumulative_result_reconciles_with_v42(self):
        previous = self.v42["combined_strict_qualified"]
        increment = self.summary["v43_increment"]
        current = self.summary["combined_validated_result"]
        mapping = {
            "modules": "new_modules",
            "selected_source_functions": "selected_source_functions",
            "matched_final_elf_functions": "matched_final_elf_functions",
            "matched_baseline_bytes": "matched_baseline_bytes",
            "unloaded_resident_object_savings_bytes": (
                "unloaded_resident_object_savings_bytes"
            ),
        }
        for total_field, increment_field in mapping.items():
            self.assertEqual(
                current[total_field],
                previous[total_field] + increment[increment_field],
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

    def test_final_accounting_remains_a_complete_partition(self):
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

    def test_provenance_and_readme_do_not_overclaim(self):
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
        self.assertIn("not sufficient", interpretation["theory"])
        self.assertIn("one-byte", interpretation["relaxed_scope"])
        self.assertIn("4032", interpretation["alignment_limit"])
        self.assertIn("only while", interpretation["runtime_limit"])
        self.assertIn("did not run", interpretation["startup_limit"])
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("prepare-callback-portfolio", readme)
        self.assertIn("350/28,959", readme)
        self.assertIn("V43_AUTOMATIC_PORTFOLIO_RESULT.md", readme)


if __name__ == "__main__":
    unittest.main()

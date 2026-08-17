import json
from pathlib import Path
import tempfile
import unittest

from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.full_kernel_stats import (
    LinkedFunctionSymbol,
    load_successful_candidates,
    load_successful_validations,
    render_full_kernel_markdown,
    render_source_closure_markdown,
    summarize_full_kernel,
    summarize_source_closures,
)
from kernel_modularizer.model import Linkage, ReferenceGraph, ReferenceNode


class FullKernelStatsTests(unittest.TestCase):
    def make_graph_and_plan(self):
        graph = ReferenceGraph()
        functions = [
            ("core_fn", 10),
            ("unknown_fn", 20),
            ("ready_fn", 30),
            ("blocked_fn", 40),
        ]
        for symbol, size in functions:
            graph.add_node(
                ReferenceNode.function(
                    symbol,
                    Linkage.EXTERNAL,
                    size_bytes=size,
                )
            )
        ready_id = "candidate:ready"
        blocked_id = "candidate:blocked"
        plan = {
            "schema_version": 1,
            "decisions": [
                {
                    "id": "fn:external:core_fn",
                    "disposition": "core",
                    "candidate_id": None,
                },
                {
                    "id": "fn:external:unknown_fn",
                    "disposition": "unknown",
                    "candidate_id": None,
                },
                {
                    "id": "fn:external:ready_fn",
                    "disposition": "module",
                    "candidate_id": ready_id,
                },
                {
                    "id": "fn:external:blocked_fn",
                    "disposition": "module",
                    "candidate_id": blocked_id,
                },
            ],
            "candidates": [
                {
                    "id": ready_id,
                    "readiness": "READY",
                    "functions": ["fn:external:ready_fn"],
                },
                {
                    "id": blocked_id,
                    "readiness": "BLOCKED",
                    "functions": ["fn:external:blocked_fn"],
                },
            ],
        }
        return graph, plan

    def test_final_elf_is_primary_denominator_and_unmatched_is_unknown(self):
        graph, plan = self.make_graph_and_plan()
        symbols = [
            LinkedFunctionSymbol("core_fn", "T", 10),
            LinkedFunctionSymbol("unknown_fn", "T", 20),
            LinkedFunctionSymbol("ready_fn", "T", 30),
            LinkedFunctionSymbol("blocked_fn", "T", 40),
            LinkedFunctionSymbol("assembly_fn", "T", 50),
            LinkedFunctionSymbol("zero_alias", "t", 0),
        ]

        report = summarize_full_kernel(
            graph,
            plan,
            symbols,
            successful_candidates={"candidate:ready"},
        )

        summary = report["summary"]
        self.assertEqual(summary["linked_function_symbols"], 5)
        self.assertEqual(summary["graph_to_linked_exact_matches"], 4)
        categories = summary["categories"]
        self.assertEqual(categories["CORE"]["functions"], 1)
        self.assertEqual(categories["READY"]["functions"], 1)
        self.assertEqual(categories["BLOCKED"]["functions"], 1)
        self.assertEqual(categories["UNKNOWN"]["functions"], 2)
        self.assertEqual(
            report["linked_accounting"]["zero_size_text_symbols_excluded"],
            1,
        )
        self.assertEqual(summary["successful_modularized_functions"], 1)
        self.assertEqual(
            report["successful_modularization"]["ratio_percent"], 20.0
        )
        self.assertEqual(
            report["successful_modularization"]["bytes"], 30
        )
        markdown = render_full_kernel_markdown(report)
        self.assertIn("| READY | 1 | 20.000000%", markdown)

    def test_duplicate_match_prefers_conservative_category(self):
        graph = ReferenceGraph()
        core = ReferenceNode.function(
            "same", Linkage.EXTERNAL, size_bytes=8
        )
        ready = ReferenceNode.function(
            "same",
            Linkage.INTERNAL,
            translation_unit="other.c",
            size_bytes=8,
        )
        graph.add_node(core)
        graph.add_node(ready)
        plan = {
            "schema_version": 1,
            "decisions": [
                {
                    "id": core.id,
                    "disposition": "core",
                    "candidate_id": None,
                },
                {
                    "id": ready.id,
                    "disposition": "module",
                    "candidate_id": "candidate:ready",
                },
            ],
            "candidates": [
                {
                    "id": "candidate:ready",
                    "readiness": "READY",
                    "functions": [ready.id],
                }
            ],
        }

        report = summarize_full_kernel(
            graph,
            plan,
            [LinkedFunctionSymbol("same", "T", 8)],
        )

        categories = report["summary"]["categories"]
        self.assertEqual(categories["CORE"]["functions"], 1)
        self.assertEqual(categories["READY"]["functions"], 0)
        self.assertEqual(
            report["coverage"]["unlinked_graph_functions_excluded"], 1
        )

    def test_success_artifact_requires_all_gates(self):
        passing = {
            "candidate_id": "candidate:ok",
            "source_extraction_passed": True,
            "kernel_build_passed": True,
            "qemu_boot_passed": True,
            "modprobe_passed": True,
            "unload_passed": True,
            "size_gate_passed": True,
            "startup_ab_passed": True,
        }
        failing = dict(passing)
        failing["candidate_id"] = "candidate:not-built"
        failing["kernel_build_passed"] = False
        no_size_win = dict(passing)
        no_size_win["candidate_id"] = "candidate:no-size-win"
        no_size_win["size_gate_passed"] = False
        passing["moved_function_ids"] = [
            "fn:external:ready_fn",
            "fn:internal:unit.c:helper",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "validations.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "validations": [
                            passing,
                            failing,
                            no_size_win,
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                load_successful_candidates(path), {"candidate:ok"}
            )
            self.assertEqual(
                load_successful_validations(path),
                {
                    "candidate:ok": (
                        "fn:external:ready_fn",
                        "fn:internal:unit.c:helper",
                    )
                },
            )

    def test_validated_source_closure_counts_linked_helpers(self):
        graph, plan = self.make_graph_and_plan()
        helper = ReferenceNode.function(
            "helper",
            Linkage.INTERNAL,
            translation_unit="unit.c",
            size_bytes=7,
        )
        graph.add_node(helper)
        plan["decisions"].append(
            {
                "id": helper.id,
                "disposition": "core",
                "candidate_id": None,
            }
        )

        report = summarize_full_kernel(
            graph,
            plan,
            [
                LinkedFunctionSymbol("ready_fn", "T", 30),
                LinkedFunctionSymbol("helper", "t", 7),
            ],
            successful_candidates={"candidate:ready"},
            successful_function_ids={helper.id},
        )

        self.assertEqual(
            report["successful_modularization"]["functions"], 2
        )
        self.assertEqual(
            report["successful_modularization"]["bytes"], 37
        )
        self.assertEqual(
            report["successful_modularization"][
                "validated_function_ids"
            ],
            ["fn:external:ready_fn", helper.id],
        )

    def test_blocked_candidate_cannot_count_as_success(self):
        graph, plan = self.make_graph_and_plan()
        with self.assertRaisesRegex(
            GraphValidationError, "only READY candidates"
        ):
            summarize_full_kernel(
                graph,
                plan,
                [LinkedFunctionSymbol("blocked_fn", "T", 40)],
                successful_candidates={"candidate:blocked"},
            )

    def test_source_closure_accounting_separates_definitions_from_elf(self):
        graph = ReferenceGraph()
        moved = ReferenceNode.function(
            "moved",
            Linkage.INTERNAL,
            translation_unit="unit.c",
            source_path="unit.c",
            size_bytes=7,
        )
        inlined = ReferenceNode.function(
            "inlined",
            Linkage.INTERNAL,
            translation_unit="unit.c",
            source_path="unit.c",
        )
        graph.add_node(moved)
        graph.add_node(inlined)
        plan = {
            "schema_version": 1,
            "decisions": [
                {
                    "id": moved.id,
                    "disposition": "core",
                    "candidate_id": None,
                },
                {
                    "id": inlined.id,
                    "disposition": "core",
                    "candidate_id": None,
                },
            ],
            "candidates": [],
        }
        closure = {
            "schema_version": 1,
            "candidate_id": "callback-table:test",
            "translation_units": [
                {
                    "translation_unit": "unit.c",
                    "moved": {
                        "functions": ["moved", "inlined"],
                        "globals": ["private_table"],
                    },
                }
            ],
        }

        report = summarize_source_closures(
            graph,
            plan,
            [
                LinkedFunctionSymbol("moved", "t", 7),
                LinkedFunctionSymbol("assembly_only", "T", 11),
            ],
            [("closure.json", closure)],
        )

        summary = report["summary"]
        self.assertEqual(summary["moved_source_function_definitions"], 2)
        self.assertEqual(summary["moved_source_global_definitions"], 1)
        self.assertEqual(summary["matched_final_elf_functions"], 1)
        self.assertEqual(summary["matched_baseline_bytes"], 7)
        self.assertEqual(report["baseline"]["function_symbols"], 2)
        item = report["closures"][0]
        self.assertEqual(item["matched_functions"][0]["name"], "moved")
        self.assertEqual(
            item["unmatched_functions"][0]["reason"],
            "no_positive_graph_size",
        )
        markdown = render_source_closure_markdown(report)
        self.assertIn("2 moved C function definitions", markdown)
        self.assertIn("50.000000%", markdown)

    def test_source_closure_exact_match_keeps_conservative_duplicate(self):
        graph = ReferenceGraph()
        core = ReferenceNode.function(
            "same",
            Linkage.EXTERNAL,
            source_path="core.c",
            size_bytes=8,
        )
        moved = ReferenceNode.function(
            "same",
            Linkage.INTERNAL,
            translation_unit="module.c",
            source_path="module.c",
            size_bytes=8,
        )
        graph.add_node(core)
        graph.add_node(moved)
        plan = {
            "schema_version": 1,
            "decisions": [
                {
                    "id": core.id,
                    "disposition": "core",
                    "candidate_id": None,
                },
                {
                    "id": moved.id,
                    "disposition": "module",
                    "candidate_id": "candidate:ready",
                },
            ],
            "candidates": [
                {
                    "id": "candidate:ready",
                    "readiness": "READY",
                    "functions": [moved.id],
                }
            ],
        }
        closure = {
            "schema_version": 1,
            "candidate_id": "callback-table:test",
            "translation_units": [
                {
                    "translation_unit": "module.c",
                    "moved": {"functions": ["same"], "globals": []},
                }
            ],
        }

        report = summarize_source_closures(
            graph,
            plan,
            [LinkedFunctionSymbol("same", "T", 8)],
            [("closure.json", closure)],
        )

        self.assertEqual(
            report["summary"]["matched_final_elf_functions"], 0
        )
        self.assertEqual(
            report["closures"][0]["unmatched_functions"][0]["reason"],
            "duplicate_exact_match_displaced",
        )


if __name__ == "__main__":
    unittest.main()

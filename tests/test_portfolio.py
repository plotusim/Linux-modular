import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.io import (
    write_json_atomic,
    write_reference_graph,
    write_text_atomic,
)
from kernel_modularizer.manifest import verify_stage_manifest
from kernel_modularizer.model import ReferenceGraph
from kernel_modularizer.portfolio import (
    plan_callback_portfolio,
    prepare_callback_portfolio,
)


def source_group(source, net, *, known=1000, interfaces=1):
    stem = source.replace("/", "_").removesuffix(".c")
    return {
        "source_path": source,
        "table_ids": [f"global:{stem}_fops"],
        "table_symbols": [f"{stem}_fops"],
        "interface_ids": [f"function:{stem}_{index}" for index in range(interfaces)],
        "interface_symbols": [f"{stem}_{index}" for index in range(interfaces)],
        "known_interface_bytes": known,
        "unknown_size_interfaces": 1 if net is None else 0,
        "estimated_stub_bytes": 640,
        "estimated_direct_net_bytes": net,
    }


class FakeBundle:
    module_object = "deferred_fixture"

    def write(self, root):
        write_json_atomic(
            Path(root) / "bundle.json",
            {
                "schema_version": 1,
                "module_object": self.module_object,
                "resident_replacements": [],
                "generated_files": [],
            },
        )


class CallbackPortfolioTests(unittest.TestCase):
    def test_relaxed_selection_keeps_zero_and_unknown_groups(self):
        report = {
            "source_groups": [
                source_group("fs/large.c", 900),
                source_group("fs/zero.c", 0),
                source_group("fs/negative.c", -1),
                source_group("fs/unknown.c", None),
            ]
        }

        selection = plan_callback_portfolio(report)

        self.assertEqual(
            [item["source_path"] for item in selection["selected_source_groups"]],
            ["fs/large.c", "fs/zero.c", "fs/unknown.c"],
        )
        self.assertEqual(
            selection["summary"]["selected_unknown_size_groups"], 1
        )
        self.assertEqual(
            selection["rejected_source_groups"][0]["selection_reason"],
            "ESTIMATE_BELOW_MINIMUM",
        )

        aggressive = plan_callback_portfolio(
            report,
            minimum_estimated_net_bytes=-1,
            include_unknown_size=False,
            maximum_source_groups=2,
        )
        self.assertEqual(
            [item["source_path"] for item in aggressive["selected_source_groups"]],
            ["fs/large.c", "fs/zero.c"],
        )
        reasons = {
            item["source_path"]: item["selection_reason"]
            for item in aggressive["rejected_source_groups"]
        }
        self.assertEqual(reasons["fs/negative.c"], "MAXIMUM_SOURCE_GROUPS")
        self.assertEqual(reasons["fs/unknown.c"], "UNKNOWN_SIZE_EXCLUDED")

    def test_selection_rejects_ambiguous_or_missing_source_filters(self):
        report = {"source_groups": [source_group("fs/demo.c", 100)]}
        with self.assertRaisesRegex(GraphValidationError, "both"):
            plan_callback_portfolio(
                report,
                only_sources=["fs/demo.c"],
                excluded_sources=["fs/demo.c"],
            )
        with self.assertRaisesRegex(GraphValidationError, "absent"):
            plan_callback_portfolio(
                report, only_sources=["fs/missing.c"]
            )

    def test_batch_isolates_failures_writes_manifest_and_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph = ReferenceGraph(metadata={"fixture": True})
            graph_path = root / "graph.json"
            plan_path = root / "plan.json"
            compile_database = root / "compile_commands.json"
            extractor = root / "SourceExtractor"
            kernel = root / "linux"
            output = root / "portfolio"
            write_reference_graph(graph_path, graph)
            write_json_atomic(plan_path, {"schema_version": 1})
            write_json_atomic(compile_database, {"commands": []})
            write_text_atomic(extractor, "fixture extractor\n")
            write_text_atomic(kernel / "fs/success.c", "int success;\n")
            write_text_atomic(kernel / "fs/failure.c", "int failure;\n")
            callback_report = {
                "summary": {"ready_source_groups": 2},
                "source_groups": [
                    source_group("fs/success.c", 1000, interfaces=2),
                    source_group("fs/failure.c", 500),
                ],
            }

            def prepare(_graph, _plan, tables, **kwargs):
                if tables[0].endswith("failure_fops"):
                    raise GraphValidationError("fixture boundary failure")
                write_json_atomic(kwargs["extraction_output"], {"functions": []})
                write_json_atomic(kwargs["backend_policy_output"], {"interfaces": []})
                write_json_atomic(
                    kwargs["discovery_output"], {"selection": {}}
                )
                write_json_atomic(
                    kwargs["source_closure_output"],
                    {
                        "summary": {
                            "moved_functions": 3,
                            "moved_globals": 1,
                            "llvm_graph_coverage_complete": True,
                        }
                    },
                )
                return {
                    "candidate_id": "callback-table:fixture",
                    "interface_ids": ["function:one", "function:two"],
                    "interface_symbols": ["one", "two"],
                    "resident_interface_ids": ["function:release"],
                    "resident_interface_symbols": ["release"],
                }

            patches = (
                patch(
                    "kernel_modularizer.portfolio.load_callback_plan",
                    return_value={},
                ),
                patch(
                    "kernel_modularizer.portfolio.analyze_callback_tables",
                    return_value=callback_report,
                ),
                patch(
                    "kernel_modularizer.portfolio.prepare_callback_tables",
                    side_effect=prepare,
                ),
                patch(
                    "kernel_modularizer.portfolio.load_source_extraction",
                    return_value=object(),
                ),
                patch(
                    "kernel_modularizer.portfolio.load_backend_policy",
                    return_value=SimpleNamespace(interfaces=("one", "two")),
                ),
                patch(
                    "kernel_modularizer.portfolio.generate_module_bundle",
                    return_value=FakeBundle(),
                ),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                report = prepare_callback_portfolio(
                    graph,
                    graph_path,
                    plan_path,
                    source_extractor=extractor,
                    compilation_database=compile_database,
                    kernel_root=kernel,
                    output_directory=output,
                )

            self.assertEqual(report["summary"]["prepared_candidates"], 1)
            self.assertEqual(report["summary"]["failed_candidates"], 1)
            self.assertEqual(
                report["summary"]["moved_source_function_definitions"], 3
            )
            success = next(
                item for item in report["candidates"]
                if item["status"] == "PREPARED"
            )
            failure = next(
                item for item in report["candidates"]
                if item["status"] == "FAILED"
            )
            self.assertIn("fixture boundary failure", failure["error"]["message"])
            manifest = json.loads(
                Path(success["artifacts"]["stage_manifest"]["path"]).read_text()
            )
            self.assertEqual(verify_stage_manifest(manifest), [])
            self.assertTrue((output / "portfolio.json").is_file())
            self.assertTrue((output / "portfolio.md").is_file())

            with patch(
                "kernel_modularizer.portfolio.load_callback_plan",
                return_value={},
            ), patch(
                "kernel_modularizer.portfolio.analyze_callback_tables",
                return_value=callback_report,
            ), patch(
                "kernel_modularizer.portfolio.prepare_callback_tables"
            ) as prepare_again:
                resumed = prepare_callback_portfolio(
                    graph,
                    graph_path,
                    plan_path,
                    source_extractor=extractor,
                    compilation_database=compile_database,
                    kernel_root=kernel,
                    output_directory=output,
                    only_sources=["fs/success.c"],
                )
            prepare_again.assert_not_called()
            self.assertEqual(resumed["summary"]["resumed_candidates"], 1)
            self.assertTrue(resumed["candidates"][0]["resumed"])

    def test_stale_graph_does_not_rewrite_an_already_modified_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph = ReferenceGraph(metadata={"fixture": True})
            graph_path = root / "graph.json"
            plan_path = root / "plan.json"
            compile_database = root / "compile_commands.json"
            extractor = root / "SourceExtractor"
            kernel = root / "linux"
            write_reference_graph(graph_path, graph)
            write_json_atomic(plan_path, {"schema_version": 1})
            write_json_atomic(compile_database, {"commands": []})
            write_text_atomic(extractor, "fixture extractor\n")
            write_text_atomic(
                kernel / "fs/demo.c",
                "#include <linux/linux_modularizer/old.h>\n",
            )
            callback_report = {
                "summary": {"ready_source_groups": 1},
                "source_groups": [source_group("fs/demo.c", 100)],
            }
            with patch(
                "kernel_modularizer.portfolio.load_callback_plan",
                return_value={},
            ), patch(
                "kernel_modularizer.portfolio.analyze_callback_tables",
                return_value=callback_report,
            ), patch(
                "kernel_modularizer.portfolio.prepare_callback_tables"
            ) as prepare:
                report = prepare_callback_portfolio(
                    graph,
                    graph_path,
                    plan_path,
                    source_extractor=extractor,
                    compilation_database=compile_database,
                    kernel_root=kernel,
                    output_directory=root / "output",
                )
            prepare.assert_not_called()
            self.assertEqual(
                report["candidates"][0]["status"],
                "SKIPPED_ALREADY_MODULARIZED",
            )


if __name__ == "__main__":
    unittest.main()

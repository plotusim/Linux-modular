from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from kernel_modularizer.cli import main
from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.io import write_reference_graph
from kernel_modularizer.model import Linkage, ReferenceGraph, ReferenceNode
from kernel_modularizer.observations import (
    BootPhase,
    DEFAULT_LOADER_READY_MARKER,
    load_boot_observations,
    parse_ftrace_files,
)


class BootObservationTests(unittest.TestCase):
    def make_graph(self):
        graph = ReferenceGraph()
        graph.add_node(
            ReferenceNode.function("start_kernel", Linkage.EXTERNAL)
        )
        graph.add_node(
            ReferenceNode.function(
                "probe",
                Linkage.INTERNAL,
                translation_unit="drivers/a.c",
            )
        )
        graph.add_node(
            ReferenceNode.function(
                "probe",
                Linkage.INTERNAL,
                translation_unit="drivers/b.c",
            )
        )
        graph.add_node(
            ReferenceNode.function("deferred", Linkage.EXTERNAL)
        )
        return graph

    def test_parser_maps_context_and_roots_ambiguous_symbols(self):
        graph = self.make_graph()
        trace = """# tracer: function
          <idle>-0 [000] d.h2 1.000: function: start_kernel <-x86_64_start
        worker-22 [001] .... 2.000: probe <-worker_thread
        worker-22 [001] d..1 2.050: deferred <-worker_thread
        worker-22 [001] .N.. 2.100: missing_symbol <-do_nmi
        worker-22 [001] .... 2.200: sched_switch: prev=worker
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace"
            path.write_text(trace, encoding="utf-8")
            observations = parse_ftrace_files(graph, [path])

        start = "fn:external:start_kernel"
        self.assertEqual(observations.node_counts[start], 1)
        self.assertEqual(
            {item.value for item in observations.node_contexts[start]},
            {"irq"},
        )
        self.assertEqual(len(observations.ambiguous_symbols["probe"]), 2)
        self.assertEqual(
            {
                item.value
                for item in observations.node_contexts[
                    "fn:external:deferred"
                ]
            },
            {"atomic"},
        )
        self.assertEqual(observations.unmatched_symbols["missing_symbol"], 1)
        self.assertEqual(
            observations.node_phases[start],
            {BootPhase.UNKNOWN},
        )
        self.assertEqual(
            observations.node_first_timestamps[start], 1.0
        )
        self.assertFalse(observations.loader_ready_observed)

    def test_loader_marker_partitions_pre_and_post_records(self):
        graph = self.make_graph()
        trace = f"""# tracer: function
        init-1 [000] .... 1.000: start_kernel <-rest_init
        init-1 [000] .... 1.500: deferred <-start_kernel
        init-1 [000] .... 2.000: tracing_mark_write: {DEFAULT_LOADER_READY_MARKER}
        init-1 [000] .... 2.000: deferred <-userspace
        init-1 [000] .... 2.100: probe <-userspace
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace"
            path.write_text(trace, encoding="utf-8")
            observations = parse_ftrace_files(
                graph,
                [path],
                require_loader_ready_marker=True,
            )

        deferred = "fn:external:deferred"
        self.assertTrue(observations.loader_ready_observed)
        self.assertEqual(
            observations.loader_ready_timestamps, (2.0,)
        )
        self.assertEqual(
            observations.node_phases[deferred],
            {BootPhase.PRE_LOADER, BootPhase.POST_LOADER},
        )
        self.assertEqual(
            observations.node_first_timestamps[deferred], 1.5
        )
        self.assertEqual(
            observations.node_last_timestamps[deferred], 2.0
        )
        self.assertIn(deferred, observations.pre_loader_node_ids)
        self.assertIn(deferred, observations.post_loader_node_ids)
        self.assertIn(
            deferred, observations.pre_and_post_loader_node_ids
        )
        self.assertEqual(
            observations.pre_loader_only_node_ids,
            {"fn:external:start_kernel"},
        )
        self.assertEqual(
            observations.post_loader_only_node_ids,
            {
                "fn:internal:drivers/a.c:probe",
                "fn:internal:drivers/b.c:probe",
            },
        )
        self.assertFalse(observations.unknown_phase_node_ids)

        summary = observations.to_dict()["summary"]
        self.assertEqual(summary["pre_loader_only_nodes"], 1)
        self.assertEqual(summary["post_loader_only_nodes"], 2)
        self.assertEqual(summary["pre_and_post_loader_nodes"], 1)

    def test_required_loader_marker_is_a_hard_failure(self):
        graph = self.make_graph()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace"
            path.write_text(
                "init-1 [000] .... 1.0: deferred <-start_kernel\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                GraphValidationError, "missing loader-ready marker"
            ):
                parse_ftrace_files(
                    graph,
                    [path],
                    require_loader_ready_marker=True,
                )

    def test_schema_one_observations_load_as_unknown_phase(self):
        graph = self.make_graph()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations-v1.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "summary": {
                            "parsed_lines": 1,
                            "ignored_lines": 0,
                        },
                        "trace_sha256": ["fixture"],
                        "observations": [
                            {
                                "node_id": "fn:external:deferred",
                                "count": 1,
                                "contexts": ["process"],
                            }
                        ],
                        "ambiguous_symbols": {},
                        "unmatched_symbols": {},
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_boot_observations(path, graph)

        self.assertEqual(
            loaded.unknown_phase_node_ids,
            {"fn:external:deferred"},
        )
        self.assertFalse(loaded.loader_ready_observed)

    def test_schema_two_rejects_inconsistent_loader_summary(self):
        graph = self.make_graph()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations-v2.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "summary": {
                            "parsed_lines": 1,
                            "ignored_lines": 0,
                        },
                        "trace_sha256": ["fixture"],
                        "loader_ready": {
                            "marker": DEFAULT_LOADER_READY_MARKER,
                            "all_traces_observed": True,
                            "timestamps_seconds": [None],
                        },
                        "observations": [
                            {
                                "node_id": "fn:external:deferred",
                                "count": 1,
                                "contexts": ["process"],
                                "phases": ["unknown"],
                                "first_timestamp_seconds": 1.0,
                                "last_timestamp_seconds": 1.0,
                            }
                        ],
                        "ambiguous_symbols": {},
                        "unmatched_symbols": {},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                GraphValidationError, "does not match"
            ):
                load_boot_observations(path, graph)

    def test_observations_round_trip_and_plan_cli(self):
        graph = self.make_graph()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph_path = root / "graph.json"
            trace_path = root / "trace"
            observations_path = root / "observations.json"
            plan_path = root / "plan.json"
            markdown_path = root / "plan.md"
            write_reference_graph(graph_path, graph)
            trace_path.write_text(
                "init-1 [000] .... 1.0: deferred <-start_kernel\n",
                encoding="utf-8",
            )

            self.assertEqual(
                main(
                    [
                        "observe-boot",
                        str(graph_path),
                        str(trace_path),
                        "--output",
                        str(observations_path),
                    ]
                ),
                0,
            )
            loaded = load_boot_observations(
                observations_path, graph
            )
            self.assertEqual(
                loaded.observed_node_ids,
                {"fn:external:deferred"},
            )

            self.assertEqual(
                main(
                    [
                        "plan",
                        str(graph_path),
                        "--observations",
                        str(observations_path),
                        "--json-output",
                        str(plan_path),
                        "--markdown-output",
                        str(markdown_path),
                    ]
                ),
                0,
            )
            report = json.loads(plan_path.read_text(encoding="utf-8"))
            decision = next(
                item
                for item in report["decisions"]
                if item["id"] == "fn:external:deferred"
            )
            self.assertEqual(decision["disposition"], "core")
            self.assertIn("observed in boot trace", decision["reasons"])

    def test_plan_cli_cannot_forge_loader_marker_in_policy(self):
        graph = self.make_graph()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph_path = root / "graph.json"
            policy_path = root / "policy.json"
            write_reference_graph(graph_path, graph)
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "phase_aware_boot": True,
                        "enforce_loader_ready_phase": True,
                        "loader_ready_observed": True,
                        "module_loader_roots": ["start_kernel"],
                    }
                ),
                encoding="utf-8",
            )

            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(
                        [
                            "plan",
                            str(graph_path),
                            "--policy",
                            str(policy_path),
                            "--json-output",
                            str(root / "plan.json"),
                            "--markdown-output",
                            str(root / "plan.md"),
                        ]
                    )

        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

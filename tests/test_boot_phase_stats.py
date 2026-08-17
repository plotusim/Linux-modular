import unittest

from kernel_modularizer.boot_phase_stats import (
    render_boot_phase_markdown,
    summarize_boot_phase,
)
from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.full_kernel_stats import LinkedFunctionSymbol
from kernel_modularizer.model import Linkage, ReferenceGraph, ReferenceNode
from kernel_modularizer.observations import (
    BootObservations,
    BootPhase,
    DEFAULT_LOADER_READY_MARKER,
)


class BootPhaseStatsTests(unittest.TestCase):
    def make_graph(self):
        graph = ReferenceGraph()
        for symbol, size, source_path in (
            ("pre", 10, "init/main.c"),
            ("both", 20, "kernel/work.c"),
            ("post", 30, "fs/open.c"),
            ("cold", 40, "drivers/example.c"),
            ("unphased", 50, "net/example.c"),
        ):
            graph.add_node(
                ReferenceNode.function(
                    symbol,
                    Linkage.EXTERNAL,
                    size_bytes=size,
                    source_path=source_path,
                )
            )
        return graph

    def make_observations(self):
        phases = {
            "fn:external:pre": frozenset({BootPhase.PRE_LOADER}),
            "fn:external:both": frozenset(
                {BootPhase.PRE_LOADER, BootPhase.POST_LOADER}
            ),
            "fn:external:post": frozenset({BootPhase.POST_LOADER}),
            "fn:external:unphased": frozenset({BootPhase.UNKNOWN}),
        }
        return BootObservations(
            node_counts={node_id: 1 for node_id in phases},
            node_contexts={node_id: frozenset() for node_id in phases},
            node_phases=phases,
            node_first_timestamps={},
            node_last_timestamps={},
            ambiguous_symbols={},
            unmatched_symbols={},
            trace_sha256=("digest",),
            loader_ready_marker=DEFAULT_LOADER_READY_MARKER,
            loader_ready_timestamps=(1.25,),
            parsed_lines=4,
            ignored_lines=0,
        )

    def test_final_elf_phase_split_is_independent_of_planner(self):
        graph = self.make_graph()
        observations = self.make_observations()
        symbols = [
            LinkedFunctionSymbol("pre", "T", 10),
            LinkedFunctionSymbol("both", "T", 20),
            LinkedFunctionSymbol("post", "T", 30),
            LinkedFunctionSymbol("cold", "T", 40),
            LinkedFunctionSymbol("unphased", "T", 50),
            LinkedFunctionSymbol("assembly_only", "T", 60),
            LinkedFunctionSymbol("zero_alias", "t", 0),
        ]

        report = summarize_boot_phase(graph, observations, symbols)

        summary = report["summary"]
        categories = summary["categories"]
        self.assertEqual(summary["linked_function_symbols"], 6)
        self.assertEqual(categories["BOOT_HOT"]["functions"], 2)
        self.assertEqual(categories["POST_BOOT_ONLY"]["functions"], 1)
        self.assertEqual(categories["BOOT_COLD"]["functions"], 1)
        self.assertEqual(categories["UNKNOWN"]["functions"], 2)
        self.assertEqual(
            summary["startup_defer_upper_bound_functions"], 2
        )
        self.assertEqual(summary["startup_defer_upper_bound_bytes"], 70)
        self.assertFalse(
            report["methodology"]["planner_dispositions_used"]
        )
        self.assertEqual(
            report["source_roots"]["BOOT_COLD"]["drivers"][
                "functions"
            ],
            1,
        )
        unit_accounting = report["source_unit_accounting"]
        self.assertEqual(
            unit_accounting["classifications"]["DEFER_ONLY"]["units"],
            2,
        )
        self.assertEqual(
            unit_accounting["classifications"]["MIXED_BOOT"]["units"],
            0,
        )
        self.assertEqual(
            unit_accounting["classifications"]["UNKNOWN"]["units"],
            1,
        )
        self.assertEqual(
            unit_accounting["defer_only_units"][0]["source_path"],
            "drivers/example.c",
        )
        markdown = render_boot_phase_markdown(report)
        self.assertIn("| BOOT_COLD | 1 |", markdown)
        self.assertIn("empirical upper bound", markdown)

    def test_duplicate_match_prefers_boot_hot(self):
        graph = ReferenceGraph()
        hot = ReferenceNode.function(
            "same", Linkage.EXTERNAL, size_bytes=8
        )
        cold = ReferenceNode.function(
            "same",
            Linkage.INTERNAL,
            translation_unit="other.c",
            size_bytes=8,
        )
        graph.add_node(hot)
        graph.add_node(cold)
        observations = BootObservations(
            node_counts={hot.id: 1},
            node_contexts={hot.id: frozenset()},
            node_phases={hot.id: frozenset({BootPhase.PRE_LOADER})},
            node_first_timestamps={},
            node_last_timestamps={},
            ambiguous_symbols={},
            unmatched_symbols={},
            trace_sha256=("digest",),
            loader_ready_marker=DEFAULT_LOADER_READY_MARKER,
            loader_ready_timestamps=(1.0,),
            parsed_lines=1,
            ignored_lines=0,
        )

        report = summarize_boot_phase(
            graph,
            observations,
            [LinkedFunctionSymbol("same", "T", 8)],
        )

        self.assertEqual(
            report["summary"]["categories"]["BOOT_HOT"]["functions"],
            1,
        )

    def test_loader_ready_marker_is_mandatory(self):
        graph = self.make_graph()
        observations = self.make_observations()
        observations = BootObservations(
            node_counts=observations.node_counts,
            node_contexts=observations.node_contexts,
            node_phases=observations.node_phases,
            node_first_timestamps={},
            node_last_timestamps={},
            ambiguous_symbols={},
            unmatched_symbols={},
            trace_sha256=("digest",),
            loader_ready_marker=DEFAULT_LOADER_READY_MARKER,
            loader_ready_timestamps=(None,),
            parsed_lines=4,
            ignored_lines=0,
        )

        with self.assertRaisesRegex(
            GraphValidationError, "requires a loader-ready marker"
        ):
            summarize_boot_phase(graph, observations, [])


if __name__ == "__main__":
    unittest.main()

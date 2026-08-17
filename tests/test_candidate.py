import unittest

from kernel_modularizer.candidate import (
    _callback_table_interface_ids,
    _infer_external_resident_exports,
    _infer_graph_resident_exports,
    _same_unit_macro_generated_dependencies,
    _validate_extraction_readiness,
)
from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.model import (
    EdgeKind,
    Linkage,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNode,
)


class CandidateExtractionReadinessTests(unittest.TestCase):
    def test_reviewed_callback_table_promotes_only_same_unit_functions(self):
        graph = ReferenceGraph()
        table = ReferenceNode.global_variable(
            "demo_fops",
            Linkage.INTERNAL,
            translation_unit="kernel/demo.c",
            source_path="kernel/demo.c",
            attributes={"definition"},
        )
        local = ReferenceNode.function(
            "demo_read",
            Linkage.INTERNAL,
            translation_unit="kernel/demo.c",
            source_path="kernel/demo.c",
            attributes={"definition"},
        )
        external = ReferenceNode.function(
            "no_llseek",
            Linkage.EXTERNAL,
            source_path="fs/read_write.c",
            attributes={"definition"},
        )
        for node in (table, local, external):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                table.id, local.id, EdgeKind.GLOBAL_INITIALIZER
            )
        )
        graph.add_edge(
            ReferenceEdge(
                table.id, external.id, EdgeKind.GLOBAL_INITIALIZER
            )
        )

        self.assertEqual(
            _callback_table_interface_ids(graph, [table.id]),
            [local.id],
        )

    def test_primary_candidate_must_be_ready(self):
        with self.assertRaisesRegex(
            GraphValidationError, "required READY"
        ):
            _validate_extraction_readiness(
                {"readiness": "NEEDS_EVIDENCE"},
                candidate_id="candidate:primary",
                primary=True,
            )

    def test_external_export_is_inferred_only_with_complete_symvers(self):
        graph = ReferenceGraph(
            metadata={
                "symbol_size_enrichment": {"exported_nodes": 1}
            }
        )
        for symbol, attributes in (
            ("hidden_dep", {"definition"}),
            ("exported_dep", {"definition", "exported"}),
            ("inline_dep", {"definition", "inline_hint"}),
        ):
            graph.add_node(
                ReferenceNode.function(
                    symbol,
                    Linkage.EXTERNAL,
                    source_path=f"kernel/{symbol}.c",
                    attributes=attributes,
                )
            )
        extraction = {
            "functions": [
                {
                    "symbol": "moved",
                    "dependencies": {
                        "functions": [
                            "hidden_dep",
                            "exported_dep",
                            "inline_dep",
                            "__builtin_unreachable",
                        ],
                        "globals": [],
                    },
                }
            ],
            "globals": [],
        }
        self.assertEqual(
            _infer_external_resident_exports(
                graph,
                extraction,
                resident_function_symbols=set(),
                existing_exports=set(),
            ),
            ["hidden_dep"],
        )

        graph.metadata.clear()
        self.assertEqual(
            _infer_external_resident_exports(
                graph,
                extraction,
                resident_function_symbols=set(),
                existing_exports=set(),
            ),
            [],
        )

    def test_graph_only_inline_dependency_becomes_direct_export(self):
        graph = ReferenceGraph(
            metadata={
                "symbol_size_enrichment": {"exported_nodes": 0}
            }
        )
        moved = ReferenceNode.function(
            "moved",
            Linkage.INTERNAL,
            translation_unit="kernel/demo.c",
            source_path="kernel/demo.c",
            attributes={"definition"},
        )
        hidden = ReferenceNode.global_variable(
            "hidden_lock",
            Linkage.EXTERNAL,
            source_path="kernel/demo.c",
            attributes={"definition", "mutable"},
        )
        inline_helper = ReferenceNode.function(
            "header_inline",
            Linkage.INTERNAL,
            translation_unit="kernel/demo.c",
            source_path="kernel/demo.c",
            attributes={"definition", "inline_hint"},
        )
        graph.add_node(moved)
        graph.add_node(hidden)
        graph.add_node(inline_helper)
        graph.add_edge(
            ReferenceEdge(
                moved.id, inline_helper.id, EdgeKind.DIRECT_CALL
            )
        )
        graph.add_edge(
            ReferenceEdge(
                inline_helper.id, hidden.id, EdgeKind.GLOBAL_READ
            )
        )
        extraction = {
            "functions": [
                {
                    "symbol": "moved",
                    "source_path": "/kernel/kernel/demo.c",
                    "references": [],
                }
            ],
            "globals": [],
        }

        self.assertEqual(
            _infer_graph_resident_exports(
                graph,
                extraction,
                kernel_root="/kernel",
                resident_function_symbols=set(),
                existing_exports=set(),
                redirected_exact_symbols=set(),
            ),
            (["hidden_lock"], ["hidden_lock"], []),
        )

        extraction["functions"][0]["references"] = [
            {"kind": "global", "symbol": "hidden_lock"}
        ]
        self.assertEqual(
            _infer_graph_resident_exports(
                graph,
                extraction,
                kernel_root="/kernel",
                resident_function_symbols=set(),
                existing_exports=set(),
                redirected_exact_symbols=set(),
            ),
            (["hidden_lock"], [], []),
        )

        extraction["functions"][0]["references"] = [
            {"kind": "function", "symbol": "header_inline"}
        ]
        self.assertEqual(
            _infer_graph_resident_exports(
                graph,
                extraction,
                kernel_root="/kernel",
                resident_function_symbols=set(),
                existing_exports=set(),
                redirected_exact_symbols=set(),
            ),
            ([], [], ["header_inline"]),
        )

    def test_macro_generated_main_file_dependency_is_reextracted(self):
        graph = ReferenceGraph()
        macro_state = ReferenceNode.global_variable(
            "macro_state",
            Linkage.INTERNAL,
            translation_unit="kernel/demo.c",
            source_path="kernel/demo.c",
            attributes={"definition", "mutable"},
        )
        header_inline = ReferenceNode.function(
            "header_inline",
            Linkage.INTERNAL,
            translation_unit="kernel/demo.c",
            source_path="include/linux/demo.h",
            attributes={"definition", "inline_hint"},
        )
        graph.add_node(macro_state)
        graph.add_node(header_inline)
        extraction = {
            "functions": [
                {
                    "symbol": "moved",
                    "dependencies": {
                        "functions": ["header_inline"],
                        "globals": ["macro_state"],
                    },
                }
            ],
            "globals": [],
        }

        self.assertEqual(
            _same_unit_macro_generated_dependencies(
                extraction,
                graph=graph,
                translation_unit="kernel/demo.c",
            ),
            ([], ["macro_state"]),
        )

    def test_merged_candidate_can_lack_only_size_data(self):
        _validate_extraction_readiness(
            {
                "readiness": "NEEDS_EVIDENCE",
                "readiness_reasons": ["missing function size data"],
                "loader_phase_classification": "LAZY_READY",
                "executable_interface_edges": 2,
                "load_safe_interface_edges": 2,
            },
            candidate_id="candidate:compat",
            primary=False,
        )

    def test_merged_candidate_cannot_bypass_phase_evidence(self):
        with self.assertRaisesRegex(
            GraphValidationError, "cannot repair"
        ):
            _validate_extraction_readiness(
                {
                    "readiness": "NEEDS_EVIDENCE",
                    "readiness_reasons": ["missing workload evidence"],
                    "loader_phase_classification": "UNKNOWN_PHASE",
                    "executable_interface_edges": 2,
                    "load_safe_interface_edges": 0,
                },
                candidate_id="candidate:unsafe",
                primary=False,
            )

    def test_merged_candidate_requires_every_entry_to_be_load_safe(self):
        with self.assertRaisesRegex(
            GraphValidationError, "cannot repair"
        ):
            _validate_extraction_readiness(
                {
                    "readiness": "NEEDS_EVIDENCE",
                    "readiness_reasons": ["missing function size data"],
                    "loader_phase_classification": "LAZY_READY",
                    "executable_interface_edges": 2,
                    "load_safe_interface_edges": 1,
                },
                candidate_id="candidate:partial",
                primary=False,
            )


if __name__ == "__main__":
    unittest.main()

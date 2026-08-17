import unittest

from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.model import (
    EdgeKind,
    Linkage,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNode,
)
from kernel_modularizer.source_closure import select_private_source_closure


def _function(symbol, functions=(), globals_=()):
    return {
        "symbol": symbol,
        "source_path": "/kernel/fs/demo.c",
        "start_offset": 0,
        "source": symbol,
        "dependencies": {
            "functions": list(functions),
            "globals": list(globals_),
            "enums": [],
        },
    }


def _global(symbol, functions=(), globals_=()):
    return {
        **_function(symbol, functions, globals_),
        "has_initializer": True,
    }


class SourceClosureTests(unittest.TestCase):
    def test_source_address_taken_global_remains_resident(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        table = ReferenceNode.global_variable(
            "published_table",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
            attributes={"definition", "immutable"},
        )
        graph.add_node(seed)
        graph.add_node(table)
        graph.add_edge(
            ReferenceEdge(seed.id, table.id, EdgeKind.GLOBAL_READ)
        )
        entry = _function("entry", globals_=("published_table",))
        entry["dependencies"]["address_taken_globals"] = [
            "published_table"
        ]

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [entry],
                "globals": [_global("published_table")],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )

        self.assertEqual(
            selection.auto_resident_globals, ("published_table",)
        )
        self.assertEqual(
            selection.report["resident_frontier"][0][
                "intrinsic_resident_reason"
            ],
            "source_address_taken_global",
        )

    def test_macro_declaration_group_is_an_intrinsic_resident_frontier(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        state = ReferenceNode.global_variable(
            "generated_state",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        graph.add_node(seed)
        graph.add_node(state)
        graph.add_edge(
            ReferenceEdge(seed.id, state.id, EdgeKind.GLOBAL_READ)
        )
        generated = _global("generated_state")
        generated["source_form"] = "macro_declaration_group"

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [
                    _function("entry", globals_=("generated_state",))
                ],
                "globals": [generated],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )

        self.assertEqual(
            selection.report["moved"]["functions"], ["entry"]
        )
        self.assertEqual(
            selection.auto_resident_globals, ("generated_state",)
        )
        self.assertEqual(
            selection.report["resident_frontier"][0][
                "intrinsic_resident_reason"
            ],
            "macro_generated_declaration_group",
        )

    def test_llvm_cross_unit_user_cuts_shared_global(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "__se_sys_demo",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        implementation = ReferenceNode.function(
            "__do_sys_demo",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        helper = ReferenceNode.function(
            "private_helper",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        callback = ReferenceNode.function(
            "resident_callback",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        operations = ReferenceNode.global_variable(
            "shared_operations",
            Linkage.EXTERNAL,
            source_path=unit,
        )
        other = ReferenceNode.function(
            "other_consumer",
            Linkage.INTERNAL,
            translation_unit="fs/other.c",
            source_path="fs/other.c",
        )
        dispatcher = ReferenceNode.function(
            "generic_dispatcher",
            Linkage.EXTERNAL,
            source_path="fs/dispatch.c",
        )
        for node in (
            seed,
            implementation,
            helper,
            callback,
            operations,
            other,
            dispatcher,
        ):
            graph.add_node(node)
        for edge in (
            ReferenceEdge(
                implementation.id, helper.id, EdgeKind.DIRECT_CALL
            ),
            ReferenceEdge(
                operations.id, callback.id, EdgeKind.GLOBAL_INITIALIZER
            ),
            ReferenceEdge(
                other.id, operations.id, EdgeKind.GLOBAL_READ
            ),
            # A dispatcher target is not a link-time owner.  The operations
            # initializer above is the ownership edge that matters.
            ReferenceEdge(
                dispatcher.id, callback.id, EdgeKind.INDIRECT_CALL
            ),
        ):
            graph.add_edge(edge)

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [
                    _function(
                        "__se_sys_demo",
                        ("private_helper",),
                        ("shared_operations",),
                    ),
                    _function("private_helper"),
                    _function("resident_callback"),
                ],
                "globals": [
                    _global(
                        "shared_operations", ("resident_callback",)
                    )
                ],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("__se_sys_demo",),
        )

        self.assertEqual(
            selection.report["moved"]["functions"],
            ["__se_sys_demo", "private_helper"],
        )
        self.assertEqual(selection.auto_resident_globals, ("shared_operations",))
        self.assertNotIn(
            "resident_callback", selection.report["moved"]["functions"]
        )
        frontier = selection.report["resident_frontier"]
        self.assertEqual(len(frontier), 1)
        self.assertEqual(frontier[0]["symbol"], "shared_operations")
        self.assertEqual(
            frontier[0]["graph_users"][0]["source_symbol"],
            "other_consumer",
        )

    def test_merged_candidate_owner_keeps_cross_unit_helper_movable(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        helper = ReferenceNode.function(
            "shared_helper",
            Linkage.EXTERNAL,
            source_path=unit,
        )
        merged_owner = ReferenceNode.function(
            "merged_entry",
            Linkage.INTERNAL,
            translation_unit="fs/compat.c",
            source_path="fs/compat.c",
        )
        for node in (seed, helper, merged_owner):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(seed.id, helper.id, EdgeKind.DIRECT_CALL)
        )
        graph.add_edge(
            ReferenceEdge(merged_owner.id, helper.id, EdgeKind.DIRECT_CALL)
        )
        payload = {
            "schema_version": 1,
            "offset_encoding": "utf-8-bytes",
            "functions": [
                _function("entry", ("shared_helper",)),
                _function("shared_helper"),
            ],
            "globals": [],
            "includes": [],
        }

        isolated = select_private_source_closure(
            payload,
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )
        merged = select_private_source_closure(
            payload,
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
            selected_owner_node_ids=(merged_owner.id,),
        )

        self.assertEqual(isolated.auto_resident_functions, ("shared_helper",))
        self.assertEqual(
            merged.report["moved"]["functions"],
            ["entry", "shared_helper"],
        )
        self.assertFalse(merged.auto_resident_functions)
        self.assertEqual(
            merged.report["composite_owner_node_ids"],
            [merged_owner.id],
        )

    def test_unknown_composite_owner_is_rejected(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        graph.add_node(seed)

        with self.assertRaisesRegex(
            GraphValidationError, "absent from the LLVM reference graph"
        ):
            select_private_source_closure(
                {
                    "schema_version": 1,
                    "offset_encoding": "utf-8-bytes",
                    "functions": [_function("entry")],
                    "globals": [],
                    "includes": [],
                },
                graph=graph,
                translation_unit=unit,
                seed_functions=("entry",),
                selected_owner_node_ids=("fn:missing",),
            )

    def test_shared_static_inline_frontier_is_duplicated(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        helper = ReferenceNode.function(
            "inline_helper",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        other = ReferenceNode.function(
            "other",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        for node in (seed, helper, other):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(seed.id, helper.id, EdgeKind.DIRECT_CALL)
        )
        graph.add_edge(
            ReferenceEdge(other.id, helper.id, EdgeKind.DIRECT_CALL)
        )
        inline = _function("inline_helper")
        inline.update(
            {
                "storage": "static",
                "attributes": ["gnu_inline", "no_instrument_function"],
                "macros": ["inline", "notrace"],
            }
        )

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [
                    _function("entry", ("inline_helper",)),
                    inline,
                    _function("other", ("inline_helper",)),
                ],
                "globals": [],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )

        self.assertEqual(
            selection.auto_duplicate_functions,
            ("inline_helper",),
        )
        self.assertFalse(selection.auto_resident_functions)
        frontier = selection.report["resident_frontier"]
        self.assertEqual(frontier[0]["resolution"], "duplicate_into_module")

    def test_weak_dependency_is_an_intrinsic_resident_frontier(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        graph.add_node(seed)
        weak = _function("weak_fallback")
        weak.update(
            {
                "storage": "none",
                "attributes": ["weak"],
                "macros": ["__weak"],
            }
        )

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [
                    _function("entry", ("weak_fallback",)),
                    weak,
                ],
                "globals": [],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )

        self.assertEqual(selection.report["moved"]["functions"], ["entry"])
        self.assertEqual(
            selection.auto_resident_functions,
            ("weak_fallback",),
        )
        self.assertTrue(
            selection.report["resident_frontier"][0][
                "intrinsic_resident"
            ]
        )
        self.assertTrue(
            selection.report["llvm_graph_coverage_complete"]
        )

    def test_escaped_callback_address_stays_resident(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        callback = ReferenceNode.function(
            "deferred_callback",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        for node in (seed, callback):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(seed.id, callback.id, EdgeKind.ADDRESS_TAKEN)
        )

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [
                    _function("entry", ("deferred_callback",)),
                    _function("deferred_callback"),
                ],
                "globals": [],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )

        self.assertEqual(
            selection.auto_resident_functions,
            ("deferred_callback",),
        )
        frontier = selection.report["resident_frontier"][0]
        self.assertEqual(
            frontier["intrinsic_resident_reason"],
            "escaped_function_address",
        )

    def test_direct_call_address_fact_does_not_create_false_frontier(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        helper = ReferenceNode.function(
            "helper",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        for node in (seed, helper):
            graph.add_node(node)
        for kind in (EdgeKind.ADDRESS_TAKEN, EdgeKind.DIRECT_CALL):
            graph.add_edge(ReferenceEdge(seed.id, helper.id, kind))

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [
                    _function("entry", ("helper",)),
                    _function("helper"),
                ],
                "globals": [],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )

        self.assertEqual(
            selection.report["moved"]["functions"],
            ["entry", "helper"],
        )
        self.assertFalse(selection.auto_resident_functions)

    def test_runtime_mutated_global_stays_resident_across_module_reload(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        state = ReferenceNode.global_variable(
            "persistent_sequence",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
            attributes=("definition", "mutable"),
        )
        for node in (seed, state):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(seed.id, state.id, EdgeKind.GLOBAL_WRITE)
        )

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [
                    _function("entry", globals_=("persistent_sequence",)),
                ],
                "globals": [_global("persistent_sequence")],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )

        self.assertEqual(selection.report["moved"]["functions"], ["entry"])
        self.assertEqual(selection.report["moved"]["globals"], [])
        self.assertEqual(
            selection.auto_resident_globals,
            ("persistent_sequence",),
        )
        frontier = selection.report["resident_frontier"][0]
        self.assertTrue(frontier["intrinsic_resident"])
        self.assertEqual(
            frontier["intrinsic_resident_reason"],
            "mutable_module_lifetime_state",
        )

    def test_unwritten_private_global_can_move(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        table = ReferenceNode.global_variable(
            "private_table",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
            attributes=("definition", "mutable"),
        )
        for node in (seed, table):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(seed.id, table.id, EdgeKind.GLOBAL_READ)
        )

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [
                    _function("entry", globals_=("private_table",)),
                ],
                "globals": [_global("private_table")],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )

        self.assertEqual(
            selection.report["moved"]["globals"],
            ["private_table"],
        )
        self.assertFalse(selection.auto_resident_globals)

    def test_shared_inline_with_resident_state_is_not_duplicated(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        helper = ReferenceNode.function(
            "locking_inline",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        other = ReferenceNode.function(
            "other",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        lock = ReferenceNode.global_variable(
            "resident_lock",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        for node in (seed, helper, other, lock):
            graph.add_node(node)
        for source in (seed, other):
            graph.add_edge(
                ReferenceEdge(source.id, helper.id, EdgeKind.DIRECT_CALL)
            )
        graph.add_edge(
            ReferenceEdge(helper.id, lock.id, EdgeKind.GLOBAL_READ)
        )
        inline = _function(
            "locking_inline", globals_=("resident_lock",)
        )
        inline.update(
            {
                "storage": "static",
                "attributes": ["inline"],
                "macros": ["inline"],
            }
        )

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [
                    _function("entry", ("locking_inline",)),
                    inline,
                    _function("other", ("locking_inline",)),
                ],
                "globals": [_global("resident_lock")],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )

        self.assertFalse(selection.auto_duplicate_functions)
        self.assertEqual(
            selection.auto_resident_functions,
            ("locking_inline",),
        )

    def test_inline_with_macro_generated_local_state_is_not_duplicated(self):
        unit = "fs/demo.c"
        graph = ReferenceGraph()
        seed = ReferenceNode.function(
            "entry",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        helper = ReferenceNode.function(
            "locking_inline",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        other = ReferenceNode.function(
            "other",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        hidden_lock = ReferenceNode.global_variable(
            "macro_generated_lock",
            Linkage.INTERNAL,
            translation_unit=unit,
            source_path=unit,
        )
        for node in (seed, helper, other, hidden_lock):
            graph.add_node(node)
        for source in (seed, other):
            graph.add_edge(
                ReferenceEdge(source.id, helper.id, EdgeKind.DIRECT_CALL)
            )
        graph.add_edge(
            ReferenceEdge(
                helper.id, hidden_lock.id, EdgeKind.GLOBAL_READ
            )
        )
        inline = _function(
            "locking_inline", globals_=("macro_generated_lock",)
        )
        inline.update(
            {
                "storage": "static",
                "attributes": ["inline"],
                "macros": ["inline"],
            }
        )

        selection = select_private_source_closure(
            {
                "schema_version": 1,
                "offset_encoding": "utf-8-bytes",
                "functions": [
                    _function("entry", ("locking_inline",)),
                    inline,
                    _function("other", ("locking_inline",)),
                ],
                # The declaration macro makes this definition unavailable to
                # AST all-main discovery even though LLVM still has a node.
                "globals": [],
                "includes": [],
            },
            graph=graph,
            translation_unit=unit,
            seed_functions=("entry",),
        )

        self.assertFalse(selection.auto_duplicate_functions)
        self.assertEqual(
            selection.auto_resident_functions,
            ("locking_inline",),
        )


if __name__ == "__main__":
    unittest.main()

import json
import unittest

from kernel_modularizer.errors import GraphValidationError, SchemaVersionError
from kernel_modularizer.model import (
    EdgeKind,
    EntityKind,
    ExecutionContext,
    FunctionIdentity,
    GlobalIdentity,
    Linkage,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNode,
)


class FunctionIdentityTests(unittest.TestCase):
    def test_external_declarations_share_one_identity(self):
        first = FunctionIdentity("schedule", Linkage.EXTERNAL, "kernel/sched/core.c")
        second = FunctionIdentity("schedule", Linkage.EXTERNAL, "kernel/entry.c")

        self.assertEqual(first.canonical, "fn:external:schedule")
        self.assertEqual(first, second)

    def test_internal_same_name_functions_do_not_collide(self):
        first = FunctionIdentity("probe", Linkage.INTERNAL, "drivers/a/core.c")
        second = FunctionIdentity("probe", Linkage.INTERNAL, "drivers/b/core.c")

        self.assertNotEqual(first.canonical, second.canonical)
        self.assertEqual(
            first.canonical,
            "fn:internal:drivers/a/core.c:probe",
        )

    def test_internal_symbol_requires_translation_unit(self):
        with self.assertRaisesRegex(
            GraphValidationError,
            "requires translation_unit",
        ):
            FunctionIdentity("probe", Linkage.INTERNAL)

    def test_translation_unit_cannot_escape_workspace(self):
        with self.assertRaisesRegex(GraphValidationError, "workspace-relative"):
            FunctionIdentity("probe", Linkage.INTERNAL, "../core.c")
        with self.assertRaisesRegex(GraphValidationError, "workspace-relative"):
            FunctionIdentity("probe", Linkage.INTERNAL, "/absolute/core.c")

    def test_global_identity_obeys_linkage(self):
        external = GlobalIdentity(
            "callback_table",
            Linkage.EXTERNAL,
            "drivers/demo.c",
        )
        first_internal = GlobalIdentity(
            "state",
            Linkage.INTERNAL,
            "drivers/a.c",
        )
        second_internal = GlobalIdentity(
            "state",
            Linkage.INTERNAL,
            "drivers/b.c",
        )

        self.assertEqual(
            external.canonical,
            "global:external:callback_table",
        )
        self.assertNotEqual(first_internal.canonical, second_internal.canonical)


class ReferenceGraphTests(unittest.TestCase):
    def make_graph(self):
        graph = ReferenceGraph(
            metadata={
                "kernel_commit": "fixture",
                "config_sha256": "abc123",
            }
        )
        caller = ReferenceNode.function(
            "start_kernel",
            Linkage.EXTERNAL,
            source_path="init/main.c",
            section=".init.text",
            contexts=[ExecutionContext.EARLY_BOOT],
        )
        callback = ReferenceNode.function(
            "probe",
            Linkage.INTERNAL,
            translation_unit="drivers/demo/core.c",
            source_path="drivers/demo/core.c",
            size_bytes=128,
        )
        ops = ReferenceNode.entity(
            EntityKind.GLOBAL,
            "demo_ops",
            owner="drivers/demo/core.c",
            source_path="drivers/demo/core.c",
        )
        for node in (caller, callback, ops):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                caller.id,
                ops.id,
                EdgeKind.GLOBAL_READ,
                location="init/main.c:10",
            )
        )
        graph.add_edge(
            ReferenceEdge(
                ops.id,
                callback.id,
                EdgeKind.CALLBACK_FIELD,
                location="drivers/demo/core.c:20",
                field_path="struct demo_ops.probe",
                evidence=frozenset({"llvm-global-initializer"}),
            )
        )
        return graph

    def test_round_trip_is_lossless_and_deterministic(self):
        graph = self.make_graph()

        first = graph.to_json()
        second = ReferenceGraph.from_json(first).to_json()

        self.assertEqual(first, second)
        decoded = json.loads(first)
        self.assertEqual(decoded["schema_version"], 1)
        self.assertEqual(len(decoded["nodes"]), 3)
        self.assertEqual(len(decoded["edges"]), 2)

    def test_duplicate_identical_edge_is_deduplicated(self):
        graph = self.make_graph()
        edge = next(iter(graph.edges))

        graph.add_edge(edge)

        self.assertEqual(len(graph.edges), 2)

    def test_conflicting_duplicate_node_is_rejected(self):
        graph = self.make_graph()
        existing = graph.nodes["fn:external:start_kernel"]
        conflicting = ReferenceNode(
            id=existing.id,
            kind=existing.kind,
            symbol=existing.symbol,
            linkage=existing.linkage,
            source_path="different/file.c",
        )

        with self.assertRaisesRegex(GraphValidationError, "conflicting"):
            graph.add_node(conflicting)

    def test_edge_with_missing_endpoint_is_rejected(self):
        graph = self.make_graph()

        with self.assertRaisesRegex(GraphValidationError, "missing node"):
            graph.add_edge(
                ReferenceEdge(
                    "fn:external:not_present",
                    "fn:external:start_kernel",
                    EdgeKind.DIRECT_CALL,
                )
            )

    def test_merge_node_combines_declaration_and_definition_facts(self):
        graph = ReferenceGraph()
        declaration = ReferenceNode.function(
            "external_api",
            Linkage.EXTERNAL,
            attributes={"declaration"},
        )
        definition = ReferenceNode.function(
            "external_api",
            Linkage.EXTERNAL,
            source_path="kernel/api.c",
            section=".text",
            attributes={"definition"},
        )

        graph.merge_node(declaration)
        graph.merge_node(definition)

        merged = graph.nodes[definition.id]
        self.assertEqual(merged.source_path, "kernel/api.c")
        self.assertEqual(
            merged.attributes,
            {"declaration", "definition"},
        )

    def test_unsupported_schema_is_rejected(self):
        raw = self.make_graph().to_dict()
        raw["schema_version"] = 999

        with self.assertRaisesRegex(SchemaVersionError, "unsupported"):
            ReferenceGraph.from_dict(raw)

    def test_outgoing_filter_preserves_edge_provenance(self):
        graph = self.make_graph()
        ops_id = "global:drivers/demo/core.c:demo_ops"

        outgoing = list(
            graph.outgoing(ops_id, kinds={EdgeKind.CALLBACK_FIELD})
        )

        self.assertEqual(len(outgoing), 1)
        self.assertEqual(outgoing[0].field_path, "struct demo_ops.probe")
        self.assertEqual(outgoing[0].evidence, {"llvm-global-initializer"})


if __name__ == "__main__":
    unittest.main()

import unittest

from kernel_modularizer.facts import (
    TranslationUnitFacts,
    merge_translation_unit_facts,
)
from kernel_modularizer.model import Linkage, ReferenceGraph, ReferenceNode
from kernel_modularizer.pointer_analysis import (
    AddressConstraint,
    CallConstraint,
    CopyConstraint,
    PointerProgram,
    solve_pointer_constraints,
)


class TranslationUnitFactsTests(unittest.TestCase):
    def make_unit(
        self,
        path,
        nodes,
        *,
        addresses=(),
        copies=(),
        calls=(),
    ):
        metadata = {
            "producer": "fixture",
            "translation_units": [path],
        }
        graph = ReferenceGraph(metadata=metadata)
        for node in nodes:
            graph.add_node(node)
        program = PointerProgram(
            addresses=set(addresses),
            copies=set(copies),
            metadata=metadata,
        )
        for call in calls:
            program.add_call(call)
        return TranslationUnitFacts(
            translation_unit=path,
            graph=graph,
            pointer_program=program,
            metadata={"producer": "fixture"},
        )

    def test_json_lines_round_trip(self):
        node = ReferenceNode.function(
            "local",
            Linkage.INTERNAL,
            translation_unit="a.c",
            source_path="a.c",
        )
        unit = self.make_unit(
            "a.c",
            [node],
            addresses=[AddressConstraint("value", node.id)],
        )

        restored = TranslationUnitFacts.from_json_lines(
            unit.to_json_lines()
        )

        self.assertEqual(restored.graph.to_dict(), unit.graph.to_dict())
        self.assertEqual(
            restored.pointer_program.to_dict()["addresses"],
            unit.pointer_program.to_dict()["addresses"],
        )

    def test_merge_preserves_same_named_internal_functions(self):
        first = ReferenceNode.function(
            "probe",
            Linkage.INTERNAL,
            translation_unit="drivers/a.c",
        )
        second = ReferenceNode.function(
            "probe",
            Linkage.INTERNAL,
            translation_unit="drivers/b.c",
        )

        graph, _ = merge_translation_unit_facts(
            [
                self.make_unit("drivers/a.c", [first]),
                self.make_unit("drivers/b.c", [second]),
            ]
        )

        self.assertIn(first.id, graph.nodes)
        self.assertIn(second.id, graph.nodes)
        self.assertEqual(len(graph.nodes), 2)

    def test_external_declaration_and_definition_merge(self):
        declaration = ReferenceNode.function(
            "api",
            Linkage.EXTERNAL,
            attributes={"declaration"},
        )
        definition = ReferenceNode.function(
            "api",
            Linkage.EXTERNAL,
            source_path="api.c",
            attributes={"definition"},
        )

        graph, _ = merge_translation_unit_facts(
            [
                self.make_unit("caller.c", [declaration]),
                self.make_unit("api.c", [definition]),
            ]
        )

        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(graph.nodes[definition.id].source_path, "api.c")

    def test_constraints_propagate_across_translation_units(self):
        caller = ReferenceNode.function("caller", Linkage.EXTERNAL)
        target = ReferenceNode.function("target", Linkage.EXTERNAL)
        first = self.make_unit(
            "caller.c",
            [caller, target],
            copies=[CopyConstraint("callee", "exported_pointer")],
            calls=[
                CallConstraint(
                    id="call:cross-tu",
                    caller=caller.id,
                    callee_pointer="callee",
                )
            ],
        )
        second = self.make_unit(
            "provider.c",
            [target],
            addresses=[AddressConstraint("exported_pointer", target.id)],
        )

        _, program = merge_translation_unit_facts([first, second])
        result = solve_pointer_constraints(program)

        self.assertEqual(
            result.call_targets["call:cross-tu"],
            {target.id},
        )


if __name__ == "__main__":
    unittest.main()

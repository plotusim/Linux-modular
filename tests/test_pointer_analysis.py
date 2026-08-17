import unittest

try:
    import pyroaring  # noqa: F401
except ImportError:
    pyroaring = None

from kernel_modularizer.model import (
    EdgeKind,
    Linkage,
    ReferenceGraph,
    ReferenceNode,
)
from kernel_modularizer.pointer_analysis import (
    AddressConstraint,
    CallConstraint,
    CopyConstraint,
    FunctionPointerSummary,
    GepConstraint,
    LoadConstraint,
    PointerProgram,
    StoreConstraint,
    memory_object_id,
    solve_pointer_constraints,
)


class PointerAnalysisTests(unittest.TestCase):
    def test_copy_chain_resolves_indirect_call_at_fixed_point(self):
        target = "fn:external:target"
        program = PointerProgram(
            addresses={AddressConstraint("late", target)},
            copies={
                CopyConstraint("middle", "late"),
                CopyConstraint("callee", "middle"),
            },
        )
        program.add_call(
            CallConstraint(
                id="call:1",
                caller="fn:external:caller",
                callee_pointer="callee",
            )
        )

        result = solve_pointer_constraints(program)

        self.assertEqual(result.call_targets["call:1"], {target})
        self.assertGreaterEqual(result.iterations, 2)
        self.assertEqual(result.unresolved_calls, set())

    def test_store_and_load_through_global_resolves_callback(self):
        target = "fn:external:callback"
        global_object = memory_object_id("demo.c", "callback_slot")
        program = PointerProgram(
            addresses={
                AddressConstraint("slot_address", global_object),
                AddressConstraint("callback_value", target),
            },
            stores={StoreConstraint("slot_address", "callback_value")},
            loads={LoadConstraint("loaded_callback", "slot_address")},
        )
        program.add_call(
            CallConstraint(
                id="call:global",
                caller="fn:external:invoke",
                callee_pointer="loaded_callback",
            )
        )

        result = solve_pointer_constraints(program)

        self.assertEqual(result.call_targets["call:global"], {target})

    def test_gep_keeps_callback_fields_separate(self):
        first = "fn:external:first_callback"
        second = "fn:external:second_callback"
        ops = memory_object_id("demo.c", "ops")
        program = PointerProgram(
            addresses={
                AddressConstraint("ops_address", ops),
                AddressConstraint("first_value", first),
                AddressConstraint("second_value", second),
            },
            geps={
                GepConstraint("first_field", "ops_address", "0.0"),
                GepConstraint("second_field", "ops_address", "0.1"),
            },
            stores={
                StoreConstraint("first_field", "first_value"),
                StoreConstraint("second_field", "second_value"),
            },
            loads={
                LoadConstraint("first_loaded", "first_field"),
                LoadConstraint("second_loaded", "second_field"),
            },
        )
        program.add_call(
            CallConstraint(
                id="call:first",
                caller="fn:external:invoke_first",
                callee_pointer="first_loaded",
            )
        )
        program.add_call(
            CallConstraint(
                id="call:second",
                caller="fn:external:invoke_second",
                callee_pointer="second_loaded",
            )
        )

        result = solve_pointer_constraints(program)

        self.assertEqual(result.call_targets["call:first"], {first})
        self.assertEqual(result.call_targets["call:second"], {second})

    def test_pointer_argument_flows_across_direct_call(self):
        callback = "fn:external:callback"
        register = "fn:external:register_callback"
        program = PointerProgram(
            addresses={AddressConstraint("actual_callback", callback)}
        )
        program.add_summary(
            FunctionPointerSummary(register, parameters=("formal_callback",))
        )
        program.add_call(
            CallConstraint(
                id="call:register",
                caller="fn:external:setup",
                direct_target=register,
                actuals=("actual_callback",),
            )
        )
        program.add_call(
            CallConstraint(
                id="call:inside_register",
                caller=register,
                callee_pointer="formal_callback",
            )
        )

        result = solve_pointer_constraints(program)

        self.assertEqual(
            result.call_targets["call:inside_register"],
            {callback},
        )

    def test_pointer_return_flows_to_indirect_call(self):
        callback = "fn:external:callback"
        factory = "fn:external:factory"
        program = PointerProgram(
            addresses={AddressConstraint("factory_return", callback)}
        )
        program.add_summary(
            FunctionPointerSummary(factory, result="factory_return")
        )
        program.add_call(
            CallConstraint(
                id="call:factory",
                caller="fn:external:setup",
                direct_target=factory,
                result="returned_callback",
            )
        )
        program.add_call(
            CallConstraint(
                id="call:returned",
                caller="fn:external:setup",
                callee_pointer="returned_callback",
            )
        )

        result = solve_pointer_constraints(program)

        self.assertEqual(result.call_targets["call:returned"], {callback})

    @unittest.skipUnless(pyroaring is not None, "pyroaring is unavailable")
    def test_hybrid_filters_incompatible_abi_targets_before_saturation(self):
        matching = "fn:external:matching"
        wrong_arity = "fn:external:wrong_arity"
        wrong_return = "fn:external:wrong_return"
        call_signature = "cc=0;ret=gpr;args=gpr;vararg=0"
        program = PointerProgram(
            addresses={
                AddressConstraint("matching_value", matching),
                AddressConstraint("wrong_arity_value", wrong_arity),
                AddressConstraint("wrong_return_value", wrong_return),
                AddressConstraint("ops_base", "obj:global:ops"),
            },
            geps={
                GepConstraint("callback_slot", "ops_base", "struct.ops|2"),
            },
            stores={
                StoreConstraint("callback_slot", "matching_value"),
                StoreConstraint("callback_slot", "wrong_arity_value"),
                StoreConstraint("callback_slot", "wrong_return_value"),
            },
            loads={
                LoadConstraint("loaded_callback", "callback_slot"),
            },
        )
        program.add_summary(
            FunctionPointerSummary(
                matching,
                signature=call_signature,
            )
        )
        program.add_summary(
            FunctionPointerSummary(
                wrong_arity,
                signature="cc=0;ret=gpr;args=gpr,gpr;vararg=0",
            )
        )
        program.add_summary(
            FunctionPointerSummary(
                wrong_return,
                signature="cc=0;ret=void;args=gpr;vararg=0",
            )
        )
        program.add_call(
            CallConstraint(
                id="call:callback",
                caller="fn:external:caller",
                actuals=(None,),
                callee_pointer="loaded_callback",
                signature=call_signature,
            )
        )

        result = solve_pointer_constraints(
            program,
            backend="hybrid",
            max_points_to_set=1,
        )

        self.assertEqual(
            result.call_targets["call:callback"], {matching}
        )
        self.assertNotIn("call:callback", result.unresolved_calls)

    @unittest.skipUnless(pyroaring is not None, "pyroaring is unavailable")
    def test_hybrid_partitions_budget_across_demanded_abi_domains(self):
        first = "fn:external:first"
        second = "fn:external:second"
        first_signature = "cc=0;ret=gpr;args=gpr;vararg=0"
        second_signature = "cc=0;ret=void;args=gpr,gpr;vararg=0"
        program = PointerProgram(
            addresses={
                AddressConstraint("shared_source", first),
                AddressConstraint("shared_source", second),
            },
            copies={
                CopyConstraint("first_callee", "shared_source"),
                CopyConstraint("second_callee", "shared_source"),
            },
        )
        program.add_summary(
            FunctionPointerSummary(first, signature=first_signature)
        )
        program.add_summary(
            FunctionPointerSummary(second, signature=second_signature)
        )
        program.add_call(
            CallConstraint(
                id="call:first",
                caller="fn:external:caller",
                actuals=(None,),
                callee_pointer="first_callee",
                signature=first_signature,
            )
        )
        program.add_call(
            CallConstraint(
                id="call:second",
                caller="fn:external:caller",
                actuals=(None, None),
                callee_pointer="second_callee",
                signature=second_signature,
            )
        )

        result = solve_pointer_constraints(
            program,
            backend="hybrid",
            max_points_to_set=1,
        )

        self.assertEqual(result.call_targets["call:first"], {first})
        self.assertEqual(result.call_targets["call:second"], {second})
        self.assertEqual(result.unresolved_calls, set())

    @unittest.skipUnless(pyroaring is not None, "pyroaring is unavailable")
    def test_reverse_slice_recovers_narrow_call_from_saturated_source(self):
        first = "fn:external:first"
        second = "fn:external:second"
        narrow = "fn:external:narrow"
        wide_signature = "cc=0;ret=gpr;args=gpr;vararg=0"
        narrow_signature = "cc=0;ret=void;args=gpr,gpr;vararg=0"
        program = PointerProgram(
            addresses={
                AddressConstraint("shared_source", first),
                AddressConstraint("shared_source", second),
                AddressConstraint("shared_source", narrow),
            },
            copies={
                CopyConstraint("wide_callee", "shared_source"),
                CopyConstraint("narrow_callee", "shared_source"),
            },
        )
        for target in (first, second):
            program.add_summary(
                FunctionPointerSummary(
                    target, signature=wide_signature
                )
            )
        program.add_summary(
            FunctionPointerSummary(
                narrow, signature=narrow_signature
            )
        )
        program.add_call(
            CallConstraint(
                id="call:wide",
                caller="fn:external:caller",
                actuals=(None,),
                callee_pointer="wide_callee",
                signature=wide_signature,
            )
        )
        program.add_call(
            CallConstraint(
                id="call:narrow",
                caller="fn:external:caller",
                actuals=(None, None),
                callee_pointer="narrow_callee",
                signature=narrow_signature,
            )
        )

        result = solve_pointer_constraints(
            program,
            backend="hybrid",
            max_points_to_set=1,
        )

        self.assertEqual(
            result.call_targets["call:narrow"], {narrow}
        )
        self.assertNotIn("call:narrow", result.unresolved_calls)
        self.assertIn("call:wide", result.unresolved_calls)
        self.assertEqual(result.field_slice_resolved_calls, 1)

    def test_unresolved_call_is_explicit_in_reference_graph(self):
        caller = ReferenceNode.function("caller", Linkage.EXTERNAL)
        graph = ReferenceGraph()
        graph.add_node(caller)
        program = PointerProgram()
        program.add_call(
            CallConstraint(
                id="call:unresolved",
                caller=caller.id,
                callee_pointer="unknown_pointer",
                encoded_function_base="fn:external:dispatch_base",
                location="demo.c:42",
            )
        )

        result = solve_pointer_constraints(program)
        result.add_call_edges(graph, program)

        self.assertEqual(result.unresolved_calls, {"call:unresolved"})
        unresolved_edges = [
            edge
            for edge in graph.edges
            if edge.kind is EdgeKind.UNRESOLVED_CALL
        ]
        self.assertEqual(len(unresolved_edges), 1)
        self.assertEqual(unresolved_edges[0].location, "demo.c:42")
        unresolved = graph.nodes[
            "unresolved:fn:external:caller:call:unresolved"
        ]
        self.assertIn(
            "encoded_function_base=fn:external:dispatch_base",
            unresolved.attributes,
        )

    def test_unresolved_call_preserves_callback_field_paths(self):
        caller = ReferenceNode.function("caller", Linkage.EXTERNAL)
        graph = ReferenceGraph()
        graph.add_node(caller)
        program = PointerProgram(
            geps={
                GepConstraint(
                    "callback_slot", "ops_pointer", "struct.ops|2"
                )
            },
            loads={
                LoadConstraint("loaded_callback", "callback_slot")
            },
        )
        program.add_call(
            CallConstraint(
                id="call:field",
                caller=caller.id,
                callee_pointer="loaded_callback",
            )
        )

        result = solve_pointer_constraints(program)
        result.add_call_edges(graph, program)

        unresolved = graph.nodes["unresolved:fn:external:caller:call:field"]
        self.assertIn(
            "callback_field=struct.ops|2", unresolved.attributes
        )

    def test_unresolved_call_preserves_callback_parameter(self):
        iterator = ReferenceNode.function(
            "iterator", Linkage.EXTERNAL
        )
        graph = ReferenceGraph()
        graph.add_node(iterator)
        program = PointerProgram()
        program.add_summary(
            FunctionPointerSummary(
                iterator.id, parameters=("callback_formal",)
            )
        )
        program.add_call(
            CallConstraint(
                id="call:parameter",
                caller=iterator.id,
                callee_pointer="callback_formal",
            )
        )

        result = solve_pointer_constraints(program)
        result.add_call_edges(graph, program)

        unresolved = graph.nodes[
            "unresolved:fn:external:iterator:call:parameter"
        ]
        self.assertIn(
            "callback_parameter=fn:external:iterator:0",
            unresolved.attributes,
        )

    def test_unresolved_call_follows_parameter_through_stack_slot(self):
        iterator = ReferenceNode.function(
            "iterator", Linkage.EXTERNAL
        )
        graph = ReferenceGraph()
        graph.add_node(iterator)
        program = PointerProgram(
            addresses={
                AddressConstraint("callback_slot", "obj:stack:callback")
            },
            loads={
                LoadConstraint("loaded_callback", "callback_slot")
            },
            stores={
                StoreConstraint("callback_slot", "callback_formal")
            },
        )
        program.add_summary(
            FunctionPointerSummary(
                iterator.id, parameters=("callback_formal",)
            )
        )
        program.add_call(
            CallConstraint(
                id="call:stack-parameter",
                caller=iterator.id,
                callee_pointer="loaded_callback",
            )
        )

        result = solve_pointer_constraints(program)
        result.add_call_edges(graph, program)

        unresolved = graph.nodes[
            "unresolved:fn:external:iterator:call:stack-parameter"
        ]
        self.assertIn(
            "callback_parameter=fn:external:iterator:0",
            unresolved.attributes,
        )
        self.assertIn(
            "callback_object=obj:stack:callback",
            unresolved.attributes,
        )

    def test_static_callback_argument_emits_scoped_edge(self):
        caller = ReferenceNode.function("caller", Linkage.EXTERNAL)
        iterator = ReferenceNode.function(
            "iterator", Linkage.EXTERNAL
        )
        callback = ReferenceNode.function(
            "callback", Linkage.EXTERNAL
        )
        graph = ReferenceGraph()
        for node in (caller, iterator, callback):
            graph.add_node(node)
        program = PointerProgram(
            addresses={
                AddressConstraint("callback_value", callback.id)
            }
        )
        program.add_summary(
            FunctionPointerSummary(
                iterator.id, parameters=("callback_formal",)
            )
        )
        program.add_summary(FunctionPointerSummary(callback.id))
        program.add_call(
            CallConstraint(
                id="call:iterator",
                caller=caller.id,
                direct_target=iterator.id,
                actuals=("callback_value",),
            )
        )
        program.add_call(
            CallConstraint(
                id="call:inside",
                caller=iterator.id,
                callee_pointer="callback_formal",
            )
        )

        result = solve_pointer_constraints(program)
        result.add_call_edges(graph, program)

        self.assertTrue(
            any(
                edge.kind is EdgeKind.FUNCTION_ARGUMENT
                and edge.target == callback.id
                and edge.field_path
                == "fn:external:iterator:0"
                for edge in graph.edges
            )
        )

    @unittest.skipUnless(pyroaring is not None, "pyroaring is unavailable")
    def test_budget_saturation_is_explicit_and_never_partially_resolved(self):
        program = PointerProgram(
            addresses={
                AddressConstraint("callee", f"fn:external:target_{index}")
                for index in range(3)
            }
        )
        program.add_call(
            CallConstraint(
                id="call:saturated",
                caller="fn:external:caller",
                callee_pointer="callee",
            )
        )

        result = solve_pointer_constraints(
            program,
            backend="roaring",
            max_points_to_set=2,
        )

        self.assertEqual(result.saturated_variables, 1)
        self.assertEqual(result.saturated_calls, 1)
        self.assertEqual(result.unresolved_calls, {"call:saturated"})
        self.assertEqual(result.call_targets["call:saturated"], set())
        self.assertEqual(result.points_to_limit, 2)

    @unittest.skipUnless(pyroaring is not None, "pyroaring is unavailable")
    def test_saturated_array_store_does_not_taint_other_field_family(self):
        arrays = {
            AddressConstraint("array_base", f"obj:array:{index}")
            for index in range(3)
        }
        callback = "fn:external:callback"
        program = PointerProgram(
            addresses={
                *arrays,
                AddressConstraint("page_value", "obj:page:value"),
                AddressConstraint("ops_base", "obj:global:ops"),
                AddressConstraint("callback_value", callback),
            },
            geps={
                GepConstraint("array_slot", "array_base", "*"),
                GepConstraint("callback_slot", "ops_base", "0.3"),
            },
            stores={
                StoreConstraint("array_slot", "page_value"),
                StoreConstraint("callback_slot", "callback_value"),
            },
            loads={
                LoadConstraint("loaded_callback", "callback_slot"),
            },
        )
        program.add_call(
            CallConstraint(
                id="call:callback",
                caller="fn:external:caller",
                callee_pointer="loaded_callback",
            )
        )

        result = solve_pointer_constraints(
            program,
            backend="roaring",
            max_points_to_set=2,
        )

        self.assertIsNone(result.global_memory_trigger)
        self.assertIn("*", result.saturated_memory_families)
        self.assertEqual(
            result.call_targets["call:callback"], {callback}
        )
        self.assertNotIn("call:callback", result.unresolved_calls)

    @unittest.skipUnless(pyroaring is not None, "pyroaring is unavailable")
    def test_hybrid_field_solver_recovers_callback_from_global_top(self):
        callback = "fn:external:callback"
        program = PointerProgram(
            addresses={
                AddressConstraint(
                    "unknown_store_pointer", f"unknown:{index}"
                )
                for index in range(3)
            }
            | {
                AddressConstraint("unknown_value", "unknown:value"),
                AddressConstraint("ops_base", "obj:global:ops"),
                AddressConstraint("callback_value", callback),
            },
            geps={
                GepConstraint("callback_slot", "ops_base", "struct.ops|2"),
            },
            stores={
                StoreConstraint(
                    "unknown_store_pointer", "unknown_value"
                ),
                StoreConstraint("callback_slot", "callback_value"),
            },
            loads={
                LoadConstraint("loaded_callback", "callback_slot"),
            },
        )
        program.add_call(
            CallConstraint(
                id="call:callback",
                caller="fn:external:caller",
                callee_pointer="loaded_callback",
            )
        )

        result = solve_pointer_constraints(
            program,
            backend="hybrid",
            max_points_to_set=2,
        )

        self.assertEqual(result.field_resolved_calls, 1)
        self.assertEqual(
            result.call_targets["call:callback"], {callback}
        )
        self.assertNotIn("call:callback", result.unresolved_calls)

    @unittest.skipUnless(pyroaring is not None, "pyroaring is unavailable")
    def test_auto_uses_hybrid_field_fallback_when_roaring_is_available(self):
        callback = "fn:external:callback"
        program = PointerProgram(
            addresses={
                AddressConstraint(
                    "unknown_store_pointer", f"unknown:{index}"
                )
                for index in range(3)
            }
            | {
                AddressConstraint("unknown_value", "unknown:value"),
                AddressConstraint("ops_base", "obj:global:ops"),
                AddressConstraint("callback_value", callback),
            },
            geps={
                GepConstraint("callback_slot", "ops_base", "struct.ops|2"),
            },
            stores={
                StoreConstraint(
                    "unknown_store_pointer", "unknown_value"
                ),
                StoreConstraint("callback_slot", "callback_value"),
            },
            loads={
                LoadConstraint("loaded_callback", "callback_slot"),
            },
        )
        program.add_call(
            CallConstraint(
                id="call:callback",
                caller="fn:external:caller",
                callee_pointer="loaded_callback",
            )
        )

        result = solve_pointer_constraints(
            program,
            backend="auto",
            max_points_to_set=2,
        )

        self.assertEqual(
            result.solver, "roaring-worklist-v2-hybrid-bounded"
        )
        self.assertEqual(result.field_resolved_calls, 1)
        self.assertEqual(
            result.call_targets["call:callback"], {callback}
        )
        self.assertNotIn("call:callback", result.unresolved_calls)

    @unittest.skipUnless(pyroaring is not None, "pyroaring is unavailable")
    def test_hybrid_prefers_complete_object_sensitive_targets(self):
        first = "fn:external:first"
        second = "fn:external:second"
        shared_shape = "layout-shape:demo|8|0"
        program = PointerProgram(
            addresses={
                AddressConstraint("first_ops", "obj:global:first_ops"),
                AddressConstraint("second_ops", "obj:global:second_ops"),
                AddressConstraint("first_value", first),
                AddressConstraint("second_value", second),
            },
            geps={
                GepConstraint("first_slot", "first_ops", shared_shape),
                GepConstraint("second_slot", "second_ops", shared_shape),
            },
            stores={
                StoreConstraint("first_slot", "first_value"),
                StoreConstraint("second_slot", "second_value"),
            },
            loads={
                LoadConstraint("loaded_callback", "first_slot"),
            },
        )
        program.add_call(
            CallConstraint(
                id="call:callback",
                caller="fn:external:caller",
                callee_pointer="loaded_callback",
            )
        )

        result = solve_pointer_constraints(
            program,
            backend="hybrid",
            max_points_to_set=16,
        )

        self.assertEqual(
            result.call_targets["call:callback"], {first}
        )
        self.assertEqual(result.unresolved_calls, set())
        self.assertEqual(result.field_fallback_calls, 0)

    def test_program_round_trip_and_merge_are_deterministic(self):
        first = PointerProgram(
            addresses={
                AddressConstraint("pointer", "fn:external:callback")
            },
            metadata={"translation_units": ["a.c"]},
        )
        first.add_summary(
            FunctionPointerSummary(
                "fn:external:callback",
                parameters=("arg", None),
            )
        )
        second = PointerProgram(
            copies={CopyConstraint("other", "pointer")},
            metadata={"translation_units": ["b.c"]},
        )
        second.add_call(
            CallConstraint(
                id="call:encoded",
                caller="fn:external:caller",
                callee_pointer="encoded_target",
                encoded_function_base="fn:external:dispatch_base",
            )
        )

        first.merge(second)
        restored = PointerProgram.from_json(first.to_json())

        self.assertEqual(first.to_dict(), restored.to_dict())
        self.assertEqual(
            restored.metadata["translation_units"],
            ["a.c", "b.c"],
        )


if __name__ == "__main__":
    unittest.main()

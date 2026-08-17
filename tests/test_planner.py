import unittest

from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.model import (
    EdgeKind,
    EntityKind,
    ExecutionContext,
    Linkage,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNode,
)
from kernel_modularizer.planner import (
    Disposition,
    PlannerPolicy,
    plan_modules,
)


def function(
    symbol,
    *,
    linkage=Linkage.EXTERNAL,
    translation_unit=None,
    source_path=None,
    attributes=(),
    contexts=(),
    section=None,
    size_bytes=None,
):
    return ReferenceNode.function(
        symbol,
        linkage,
        translation_unit=translation_unit,
        source_path=source_path,
        attributes=attributes,
        contexts=contexts,
        section=section,
        size_bytes=size_bytes,
    )


class PlannerTests(unittest.TestCase):
    def test_boot_dependency_closure_stays_core(self):
        graph = ReferenceGraph()
        start = function("start_kernel", section=".init.text")
        direct = function("boot_direct")
        indirect = function("boot_indirect")
        for node in (start, direct, indirect):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(start.id, direct.id, EdgeKind.DIRECT_CALL)
        )
        graph.add_edge(
            ReferenceEdge(direct.id, indirect.id, EdgeKind.INDIRECT_CALL)
        )

        plan = plan_modules(graph)

        self.assertEqual(
            plan.decision_for(start.id).disposition,
            Disposition.CORE,
        )
        self.assertEqual(
            plan.decision_for(indirect.id).disposition,
            Disposition.CORE,
        )

    def test_observed_boot_function_is_a_hard_root(self):
        graph = ReferenceGraph()
        observed = function("observed")
        child = function("observed_child")
        graph.add_node(observed)
        graph.add_node(child)
        graph.add_edge(
            ReferenceEdge(observed.id, child.id, EdgeKind.DIRECT_CALL)
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(observed_boot_functions=frozenset({observed.id})),
        )

        self.assertEqual(
            plan.decision_for(child.id).disposition,
            Disposition.CORE,
        )

    def test_phase_aware_process_observation_creates_lazy_boundary(self):
        graph = ReferenceGraph()
        observed = function(
            "observed",
            attributes={"observed_context=process"},
        )
        deferred = function("deferred")
        graph.add_node(observed)
        graph.add_node(deferred)
        graph.add_edge(
            ReferenceEdge(
                observed.id, deferred.id, EdgeKind.DIRECT_CALL
            )
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(
                observed_boot_functions=frozenset({observed.id}),
                phase_aware_boot=True,
            ),
        )

        self.assertEqual(
            plan.decision_for(observed.id).disposition,
            Disposition.CORE,
        )
        self.assertEqual(
            plan.decision_for(deferred.id).disposition,
            Disposition.INTERFACE,
        )

    def test_phase_aware_atomic_observation_keeps_dependency_core(self):
        graph = ReferenceGraph()
        observed = function(
            "observed_atomic",
            attributes={"observed_context=atomic"},
        )
        dependency = function("atomic_dependency")
        graph.add_node(observed)
        graph.add_node(dependency)
        graph.add_edge(
            ReferenceEdge(
                observed.id, dependency.id, EdgeKind.DIRECT_CALL
            )
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(
                observed_boot_functions=frozenset({observed.id}),
                phase_aware_boot=True,
            ),
        )

        self.assertEqual(
            plan.decision_for(dependency.id).disposition,
            Disposition.CORE,
        )

    def test_pre_loader_process_observation_keeps_dependency_core(self):
        graph = ReferenceGraph()
        loader = function("module_loader")
        observed = function(
            "early_process",
            attributes={"observed_context=process"},
            contexts={ExecutionContext.PROCESS},
        )
        dependency = function("early_dependency")
        for node in (loader, observed, dependency):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                observed.id, dependency.id, EdgeKind.DIRECT_CALL
            )
        )
        policy = PlannerPolicy(
            observed_boot_functions=frozenset({observed.id}),
            module_loader_roots=frozenset({loader.id}),
            phase_aware_boot=True,
            enforce_loader_ready_phase=True,
            loader_ready_observed=True,
            pre_loader_observed_functions=frozenset(
                {observed.id}
            ),
        )

        plan = plan_modules(graph, policy)

        self.assertEqual(
            plan.decision_for(dependency.id).disposition,
            Disposition.CORE,
        )
        self.assertIn(
            "before module loader ready",
            " ".join(plan.decision_for(observed.id).reasons),
        )

    def test_post_loader_process_observation_creates_lazy_boundary(self):
        graph = ReferenceGraph()
        loader = function("module_loader")
        observed = function(
            "late_process",
            attributes={"observed_context=process"},
            contexts={ExecutionContext.PROCESS},
        )
        dependency = function("late_dependency")
        for node in (loader, observed, dependency):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                observed.id, dependency.id, EdgeKind.DIRECT_CALL
            )
        )
        policy = PlannerPolicy(
            observed_boot_functions=frozenset({observed.id}),
            module_loader_roots=frozenset({loader.id}),
            phase_aware_boot=True,
            enforce_loader_ready_phase=True,
            loader_ready_observed=True,
            post_loader_observed_functions=frozenset(
                {observed.id}
            ),
        )

        plan = plan_modules(graph, policy)

        self.assertEqual(
            plan.decision_for(dependency.id).disposition,
            Disposition.INTERFACE,
        )

    def test_post_loader_atomic_dependency_is_preload_candidate(self):
        graph = ReferenceGraph()
        loader = function("module_loader")
        callback = function(
            "late_atomic_callback",
            attributes={"observed_context=atomic"},
            contexts={ExecutionContext.ATOMIC},
        )
        dependency = function("preloaded_dependency")
        for node in (loader, callback, dependency):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                callback.id, dependency.id, EdgeKind.DIRECT_CALL
            )
        )
        policy = PlannerPolicy(
            observed_boot_functions=frozenset({callback.id}),
            module_loader_roots=frozenset({loader.id}),
            phase_aware_boot=True,
            enforce_loader_ready_phase=True,
            loader_ready_observed=True,
            identify_preload_candidates=True,
            post_loader_observed_functions=frozenset(
                {callback.id}
            ),
        )

        plan = plan_modules(graph, policy)

        self.assertEqual(
            plan.decision_for(dependency.id).disposition,
            Disposition.INTERFACE,
        )

    def test_loader_phase_enforcement_requires_marker_evidence(self):
        graph = ReferenceGraph()
        loader = function("module_loader")
        graph.add_node(loader)

        with self.assertRaisesRegex(
            GraphValidationError, "module-loader-ready marker"
        ):
            plan_modules(
                graph,
                PlannerPolicy(
                    module_loader_roots=frozenset({loader.id}),
                    phase_aware_boot=True,
                    enforce_loader_ready_phase=True,
                ),
            )

    def test_loader_phase_enforcement_rejects_unphased_observation(self):
        graph = ReferenceGraph()
        loader = function("module_loader")
        observed = function("observed")
        graph.add_node(loader)
        graph.add_node(observed)

        with self.assertRaisesRegex(
            GraphValidationError, "phase evidence for every observed"
        ):
            plan_modules(
                graph,
                PlannerPolicy(
                    observed_boot_functions=frozenset(
                        {observed.id}
                    ),
                    module_loader_roots=frozenset({loader.id}),
                    phase_aware_boot=True,
                    enforce_loader_ready_phase=True,
                    loader_ready_observed=True,
                ),
            )

    def test_trace_guard_skips_unobserved_indirect_boot_target(self):
        graph = ReferenceGraph()
        observed = function(
            "observed_atomic",
            attributes={"observed_context=atomic"},
        )
        deferred = function("unobserved_callback")
        graph.add_node(observed)
        graph.add_node(deferred)
        graph.add_edge(
            ReferenceEdge(
                observed.id, deferred.id, EdgeKind.INDIRECT_CALL
            )
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(
                observed_boot_functions=frozenset({observed.id}),
                phase_aware_boot=True,
                trace_guarded_indirect_boot=True,
            ),
        )

        self.assertEqual(
            plan.decision_for(observed.id).disposition,
            Disposition.CORE,
        )
        self.assertEqual(
            plan.decision_for(deferred.id).disposition,
            Disposition.INTERFACE,
        )

    def test_trace_guard_follows_observed_indirect_boot_target(self):
        graph = ReferenceGraph()
        observed = function(
            "observed_atomic",
            attributes={"observed_context=atomic"},
        )
        callback = function(
            "observed_callback",
            attributes={"observed_context=atomic"},
        )
        dependency = function("callback_dependency")
        for node in (observed, callback, dependency):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                observed.id, callback.id, EdgeKind.INDIRECT_CALL
            )
        )
        graph.add_edge(
            ReferenceEdge(
                callback.id, dependency.id, EdgeKind.DIRECT_CALL
            )
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(
                observed_boot_functions=frozenset(
                    {observed.id, callback.id}
                ),
                phase_aware_boot=True,
                trace_guarded_indirect_boot=True,
            ),
        )

        self.assertEqual(
            plan.decision_for(dependency.id).disposition,
            Disposition.CORE,
        )

    def test_trace_guard_does_not_prune_static_hard_root(self):
        graph = ReferenceGraph()
        static_root = function("static_root", section=".init.text")
        dependency = function("static_indirect_dependency")
        graph.add_node(static_root)
        graph.add_node(dependency)
        graph.add_edge(
            ReferenceEdge(
                static_root.id, dependency.id, EdgeKind.INDIRECT_CALL
            )
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(
                phase_aware_boot=True,
                trace_guarded_indirect_boot=True,
            ),
        )

        self.assertEqual(
            plan.decision_for(dependency.id).disposition,
            Disposition.CORE,
        )

    def test_initcall_trace_can_defer_unobserved_init_function(self):
        graph = ReferenceGraph()
        deferred = function(
            "unobserved_init",
            attributes={"__init"},
            section=".init.text",
        )
        graph.add_node(deferred)

        plan = plan_modules(
            graph,
            PlannerPolicy(
                phase_aware_boot=True,
                trace_covers_initcalls=True,
            ),
        )

        self.assertEqual(
            plan.decision_for(deferred.id).disposition,
            Disposition.MODULE,
        )

    def test_initcall_trace_keeps_observed_init_function_core(self):
        graph = ReferenceGraph()
        observed = function(
            "observed_init",
            attributes={"__init", "observed_context=process"},
            section=".init.text",
        )
        graph.add_node(observed)

        plan = plan_modules(
            graph,
            PlannerPolicy(
                observed_boot_functions=frozenset({observed.id}),
                phase_aware_boot=True,
                trace_covers_initcalls=True,
            ),
        )

        self.assertEqual(
            plan.decision_for(observed.id).disposition,
            Disposition.CORE,
        )

    def test_initcall_trace_does_not_remove_other_hard_reason(self):
        graph = ReferenceGraph()
        init_naked = function(
            "init_naked",
            attributes={"__init", "naked"},
            section=".init.text",
        )
        graph.add_node(init_naked)

        plan = plan_modules(
            graph,
            PlannerPolicy(
                phase_aware_boot=True,
                trace_covers_initcalls=True,
            ),
        )

        self.assertEqual(
            plan.decision_for(init_naked.id).disposition,
            Disposition.CORE,
        )

    def test_initcall_section_keeps_registered_callback_core(self):
        graph = ReferenceGraph()
        initcall_table = ReferenceNode.entity(
            EntityKind.LINKER_SECTION,
            "__initcall6_start",
            section=".initcall6.init",
        )
        callback = function("device_init")
        graph.add_node(initcall_table)
        graph.add_node(callback)
        graph.add_edge(
            ReferenceEdge(
                initcall_table.id,
                callback.id,
                EdgeKind.LINKER_SECTION,
            )
        )

        plan = plan_modules(graph)

        self.assertEqual(
            plan.decision_for(callback.id).disposition,
            Disposition.CORE,
        )

    def test_irqentry_section_keeps_dependency_core(self):
        graph = ReferenceGraph()
        entry = function("irq_entry", section=".irqentry.text")
        helper = function("irq_dependency")
        graph.add_node(entry)
        graph.add_node(helper)
        graph.add_edge(
            ReferenceEdge(entry.id, helper.id, EdgeKind.DIRECT_CALL)
        )

        plan = plan_modules(graph)

        self.assertEqual(
            plan.decision_for(helper.id).disposition,
            Disposition.CORE,
        )

    def test_resident_process_entry_uses_module_interface(self):
        graph = ReferenceGraph()
        api = function(
            "resident_api",
            attributes={"exported"},
            contexts={ExecutionContext.PROCESS},
        )
        entry = function("deferred_entry", size_bytes=80)
        helper = function("deferred_helper", size_bytes=20)
        for node in (api, entry, helper):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(api.id, entry.id, EdgeKind.DIRECT_CALL)
        )
        graph.add_edge(
            ReferenceEdge(entry.id, helper.id, EdgeKind.DIRECT_CALL)
        )

        plan = plan_modules(graph)

        self.assertEqual(
            plan.decision_for(api.id).disposition,
            Disposition.CORE,
        )
        self.assertEqual(
            plan.decision_for(entry.id).disposition,
            Disposition.INTERFACE,
        )
        self.assertEqual(
            plan.decision_for(helper.id).disposition,
            Disposition.MODULE,
        )
        entry_candidate = next(
            item for item in plan.candidates if entry.id in item.functions
        )
        self.assertEqual(entry_candidate.known_size_bytes, 100)
        self.assertEqual(entry_candidate.interfaces, (entry.id,))

    def test_irq_entry_and_dependencies_cannot_be_demand_loaded(self):
        graph = ReferenceGraph()
        irq_entry = function(
            "irq_entry",
            attributes={"exported"},
            contexts={ExecutionContext.IRQ},
        )
        helper = function("irq_helper")
        graph.add_node(irq_entry)
        graph.add_node(helper)
        graph.add_edge(
            ReferenceEdge(irq_entry.id, helper.id, EdgeKind.DIRECT_CALL)
        )

        plan = plan_modules(graph)

        self.assertEqual(
            plan.decision_for(helper.id).disposition,
            Disposition.CORE,
        )

    def test_resident_entry_without_context_proof_keeps_dependency_core(self):
        graph = ReferenceGraph()
        api = function("api", attributes={"exported"})
        helper = function("helper")
        graph.add_node(api)
        graph.add_node(helper)
        graph.add_edge(
            ReferenceEdge(api.id, helper.id, EdgeKind.DIRECT_CALL)
        )

        plan = plan_modules(graph)

        self.assertEqual(
            plan.decision_for(helper.id).disposition,
            Disposition.CORE,
        )

    def test_syscall_lazy_policy_requires_loader_bootstrap_roots(self):
        graph = ReferenceGraph()
        syscall = function(
            "__x64_sys_optional",
            attributes={"syscall_entry"},
        )
        graph.add_node(syscall)

        with self.assertRaisesRegex(
            GraphValidationError,
            "requires explicit module_loader_roots",
        ):
            plan_modules(
                graph,
                PlannerPolicy(syscall_entries_load_safe=True),
            )

    def test_syscall_lazy_policy_preserves_loader_and_defers_optional_body(self):
        graph = ReferenceGraph()
        loader = function(
            "__x64_sys_finit_module",
            attributes={"syscall_entry"},
        )
        loader_body = function("do_finit_module")
        optional = function(
            "__x64_sys_optional",
            attributes={"syscall_entry"},
        )
        optional_body = function("do_optional")
        for node in (loader, loader_body, optional, optional_body):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                loader.id,
                loader_body.id,
                EdgeKind.DIRECT_CALL,
            )
        )
        graph.add_edge(
            ReferenceEdge(
                optional.id,
                optional_body.id,
                EdgeKind.DIRECT_CALL,
            )
        )
        policy = PlannerPolicy(
            module_loader_roots=frozenset({loader.id}),
            phase_aware_boot=True,
            syscall_entries_load_safe=True,
        )

        plan = plan_modules(graph, policy)

        self.assertEqual(
            plan.decision_for(loader_body.id).disposition,
            Disposition.CORE,
        )
        self.assertEqual(
            plan.decision_for(optional.id).disposition,
            Disposition.CORE,
        )
        self.assertEqual(
            plan.decision_for(optional_body.id).disposition,
            Disposition.INTERFACE,
        )

    def test_structural_syscall_prefix_recovers_compat_entry_proof(self):
        graph = ReferenceGraph()
        loader = function(
            "__x64_sys_finit_module",
            attributes={"syscall_entry"},
        )
        compat_entry = function("__ia32_sys_optional")
        optional_body = function("do_optional")
        for node in (loader, compat_entry, optional_body):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                compat_entry.id,
                optional_body.id,
                EdgeKind.DIRECT_CALL,
            )
        )
        policy = PlannerPolicy(
            module_loader_roots=frozenset({loader.id}),
            phase_aware_boot=True,
            syscall_entries_load_safe=True,
            structural_syscall_entry_prefixes=("__ia32_sys_",),
            enforce_loader_ready_phase=True,
            loader_ready_observed=True,
        )

        plan = plan_modules(graph, policy)

        self.assertEqual(
            plan.decision_for(compat_entry.id).disposition,
            Disposition.CORE,
        )
        self.assertEqual(
            plan.decision_for(optional_body.id).disposition,
            Disposition.INTERFACE,
        )

    def test_structural_syscall_prefix_requires_reserved_shape(self):
        graph = ReferenceGraph()
        loader = function(
            "__x64_sys_finit_module",
            attributes={"syscall_entry"},
        )
        graph.add_node(loader)

        with self.assertRaisesRegex(
            GraphValidationError,
            "invalid structural syscall entry prefix",
        ):
            plan_modules(
                graph,
                PlannerPolicy(
                    module_loader_roots=frozenset({loader.id}),
                    syscall_entries_load_safe=True,
                    structural_syscall_entry_prefixes=("sys_",),
                ),
            )

    def test_load_safe_units_from_one_source_can_share_a_candidate(self):
        graph = ReferenceGraph()
        first_api = function("first_api", attributes={"exported"})
        second_api = function("second_api", attributes={"exported"})
        first_body = function(
            "first_body",
            source_path="optional.c",
            size_bytes=3000,
        )
        second_body = function(
            "second_body",
            source_path="optional.c",
            size_bytes=3000,
        )
        for node in (first_api, second_api, first_body, second_body):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                first_api.id,
                first_body.id,
                EdgeKind.DIRECT_CALL,
            )
        )
        graph.add_edge(
            ReferenceEdge(
                second_api.id,
                second_body.id,
                EdgeKind.DIRECT_CALL,
            )
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(
                load_safe_roots=frozenset(
                    {first_api.id, second_api.id}
                ),
                pack_load_safe_candidates_by_source=True,
            ),
        )

        self.assertEqual(len(plan.candidates), 1)
        self.assertEqual(
            plan.candidates[0].functions,
            tuple(sorted((first_body.id, second_body.id))),
        )
        self.assertEqual(
            plan.candidates[0].interfaces,
            tuple(sorted((first_body.id, second_body.id))),
        )
        self.assertEqual(plan.candidates[0].known_size_bytes, 6000)

    def test_reviewed_load_safe_root_can_form_lazy_interface(self):
        graph = ReferenceGraph()
        api = function("api", attributes={"exported"})
        helper = function("helper")
        graph.add_node(api)
        graph.add_node(helper)
        graph.add_edge(
            ReferenceEdge(api.id, helper.id, EdgeKind.DIRECT_CALL)
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(load_safe_roots=frozenset({api.id})),
        )

        self.assertEqual(
            plan.decision_for(helper.id).disposition,
            Disposition.INTERFACE,
        )

    def test_unresolved_indirect_call_blocks_automatic_extraction(self):
        graph = ReferenceGraph()
        caller = function("indirect_caller")
        possible_target = function("possible_target")
        target_dependency = function("target_dependency")
        unresolved = ReferenceNode.entity(
            EntityKind.UNRESOLVED,
            "callsite_42",
            owner="drivers/demo.c",
        )
        graph.add_node(caller)
        graph.add_node(possible_target)
        graph.add_node(target_dependency)
        graph.add_node(unresolved)
        graph.add_edge(
            ReferenceEdge(
                caller.id,
                unresolved.id,
                EdgeKind.UNRESOLVED_CALL,
            )
        )
        graph.add_edge(
            ReferenceEdge(
                caller.id,
                possible_target.id,
                EdgeKind.ADDRESS_TAKEN,
            )
        )
        graph.add_edge(
            ReferenceEdge(
                possible_target.id,
                target_dependency.id,
                EdgeKind.DIRECT_CALL,
            )
        )

        plan = plan_modules(graph)

        decision = plan.decision_for(caller.id)
        self.assertEqual(decision.disposition, Disposition.UNKNOWN)
        self.assertIn("unresolved indirect call", decision.reasons[0])
        self.assertEqual(
            plan.decision_for(possible_target.id).disposition,
            Disposition.UNKNOWN,
        )
        self.assertEqual(
            plan.decision_for(target_dependency.id).disposition,
            Disposition.UNKNOWN,
        )

    def test_unresolved_target_uncertainty_is_limited_by_abi_signature(self):
        call_signature = "cc=0;ret=gpr;args=gpr;vararg=0"
        graph = ReferenceGraph()
        caller = function("indirect_caller")
        matching = function(
            "matching",
            attributes={f"abi_signature={call_signature}"},
        )
        incompatible = function(
            "incompatible",
            attributes={
                "abi_signature=cc=0;ret=void;args=gpr,gpr;vararg=0"
            },
        )
        unresolved = ReferenceNode.entity(
            EntityKind.UNRESOLVED,
            "callsite_abi",
            owner=caller.id,
            attributes={
                "indirect_call",
                f"abi_signature={call_signature}",
            },
        )
        for node in (caller, matching, incompatible, unresolved):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                caller.id,
                unresolved.id,
                EdgeKind.UNRESOLVED_CALL,
            )
        )
        graph.add_edge(
            ReferenceEdge(
                caller.id,
                matching.id,
                EdgeKind.ADDRESS_TAKEN,
            )
        )
        graph.add_edge(
            ReferenceEdge(
                caller.id,
                incompatible.id,
                EdgeKind.ADDRESS_TAKEN,
            )
        )

        plan = plan_modules(graph)

        self.assertEqual(
            plan.decision_for(matching.id).disposition,
            Disposition.UNKNOWN,
        )
        self.assertNotEqual(
            plan.decision_for(incompatible.id).disposition,
            Disposition.UNKNOWN,
        )

    def test_unresolved_target_uncertainty_can_be_field_scoped(self):
        graph = ReferenceGraph()
        signature = "cc=0;ret=gpr;args=gpr;vararg=0"
        caller = function("caller")
        matching = function(
            "matching_field",
            attributes={f"abi_signature={signature}"},
        )
        other = function(
            "other_field",
            attributes={f"abi_signature={signature}"},
        )
        matching_table = ReferenceNode.global_variable(
            "matching_ops",
            Linkage.INTERNAL,
            translation_unit="matching.c",
            attributes={"immutable"},
        )
        other_table = ReferenceNode.global_variable(
            "other_ops",
            Linkage.INTERNAL,
            translation_unit="other.c",
            attributes={"immutable"},
        )
        unresolved = ReferenceNode.entity(
            EntityKind.UNRESOLVED,
            "call:field",
            owner=caller.id,
            attributes={
                "indirect_call",
                f"abi_signature={signature}",
                "callback_field=struct.ops|2",
            },
        )
        for node in (
            caller,
            matching,
            other,
            matching_table,
            other_table,
            unresolved,
        ):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                caller.id, unresolved.id, EdgeKind.UNRESOLVED_CALL
            )
        )
        graph.add_edge(
            ReferenceEdge(
                matching_table.id,
                matching.id,
                EdgeKind.GLOBAL_INITIALIZER,
                field_path="struct.ops|2",
            )
        )
        graph.add_edge(
            ReferenceEdge(
                other_table.id,
                other.id,
                EdgeKind.GLOBAL_INITIALIZER,
                field_path="struct.ops|3",
            )
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(field_scoped_unresolved_targets=True),
        )

        self.assertEqual(
            plan.decision_for(matching.id).disposition,
            Disposition.UNKNOWN,
        )
        self.assertEqual(
            plan.decision_for(other.id).disposition,
            Disposition.MODULE,
        )

    def test_unresolved_target_uncertainty_can_be_parameter_scoped(self):
        graph = ReferenceGraph()
        signature = "cc=0;ret=gpr;args=gpr;vararg=0"
        parameter = "fn:external:iterator:0"
        caller = function("iterator")
        matching = function(
            "matching_parameter",
            attributes={f"abi_signature={signature}"},
        )
        other = function(
            "other_parameter",
            attributes={f"abi_signature={signature}"},
        )
        unresolved = ReferenceNode.entity(
            EntityKind.UNRESOLVED,
            "call:parameter",
            owner=caller.id,
            attributes={
                "indirect_call",
                f"abi_signature={signature}",
                f"callback_parameter={parameter}",
            },
        )
        for node in (caller, matching, other, unresolved):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                caller.id, unresolved.id, EdgeKind.UNRESOLVED_CALL
            )
        )
        graph.add_edge(
            ReferenceEdge(
                caller.id,
                matching.id,
                EdgeKind.FUNCTION_ARGUMENT,
                field_path=parameter,
            )
        )
        graph.add_edge(
            ReferenceEdge(
                caller.id,
                other.id,
                EdgeKind.ADDRESS_TAKEN,
            )
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(field_scoped_unresolved_targets=True),
        )

        self.assertEqual(
            plan.decision_for(matching.id).disposition,
            Disposition.UNKNOWN,
        )
        self.assertEqual(
            plan.decision_for(other.id).disposition,
            Disposition.MODULE,
        )

    def test_unresolved_encoded_dispatch_uses_configured_global_domain(self):
        graph = ReferenceGraph()
        signature = (
            "cc=0;ret=gpr;args=gpr,gpr,gpr,gpr,gpr;vararg=0"
        )
        caller = function("interpreter")
        dispatch_base = function(
            "__dispatch_base",
            attributes={
                "exported",
                f"abi_signature={signature}",
            },
        )
        helper = function(
            "bpf_helper",
            attributes={f"abi_signature={signature}"},
        )
        unrelated = function(
            "unrelated_callback",
            attributes={f"abi_signature={signature}"},
        )
        helper_proto = ReferenceNode.global_variable(
            "bpf_helper_proto",
            Linkage.INTERNAL,
            translation_unit="helper.c",
            attributes={"immutable"},
        )
        unrelated_table = ReferenceNode.global_variable(
            "unrelated_ops",
            Linkage.INTERNAL,
            translation_unit="other.c",
            attributes={"immutable"},
        )
        unresolved = ReferenceNode.entity(
            EntityKind.UNRESOLVED,
            "call:encoded",
            owner=caller.id,
            attributes={
                "indirect_call",
                f"abi_signature={signature}",
                f"encoded_function_base={dispatch_base.id}",
            },
        )
        for node in (
            caller,
            dispatch_base,
            helper,
            unrelated,
            helper_proto,
            unrelated_table,
            unresolved,
        ):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                caller.id, unresolved.id, EdgeKind.UNRESOLVED_CALL
            )
        )
        graph.add_edge(
            ReferenceEdge(
                helper_proto.id,
                helper.id,
                EdgeKind.GLOBAL_INITIALIZER,
            )
        )
        graph.add_edge(
            ReferenceEdge(
                unrelated_table.id,
                unrelated.id,
                EdgeKind.GLOBAL_INITIALIZER,
            )
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(
                encoded_function_domains=(
                    (
                        dispatch_base.id,
                        ("bpf_*_proto",),
                    ),
                ),
            ),
        )

        self.assertEqual(
            plan.decision_for(helper.id).disposition,
            Disposition.UNKNOWN,
        )
        self.assertEqual(
            plan.decision_for(unrelated.id).disposition,
            Disposition.MODULE,
        )

    def test_recursive_scc_is_one_candidate(self):
        graph = ReferenceGraph()
        first = function("first")
        second = function("second")
        for node in (first, second):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(first.id, second.id, EdgeKind.DIRECT_CALL)
        )
        graph.add_edge(
            ReferenceEdge(second.id, first.id, EdgeKind.DIRECT_CALL)
        )

        plan = plan_modules(graph)

        self.assertEqual(len(plan.candidates), 1)
        self.assertEqual(
            plan.candidates[0].functions,
            tuple(sorted((first.id, second.id))),
        )

    def test_one_way_private_dependency_is_in_same_candidate(self):
        graph = ReferenceGraph()
        entry = function(
            "entry",
            attributes={"exported"},
            contexts={ExecutionContext.PROCESS},
        )
        outer = function("outer")
        helper = function(
            "helper",
            linkage=Linkage.INTERNAL,
            translation_unit="helper.c",
        )
        for node in (entry, outer, helper):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(entry.id, outer.id, EdgeKind.DIRECT_CALL)
        )
        graph.add_edge(
            ReferenceEdge(outer.id, helper.id, EdgeKind.DIRECT_CALL)
        )

        plan = plan_modules(graph)

        candidate = next(
            item for item in plan.candidates if outer.id in item.functions
        )
        self.assertEqual(
            set(candidate.functions), {outer.id, helper.id}
        )

    def test_non_exported_resident_dependency_blocks_module(self):
        graph = ReferenceGraph()
        resident = function(
            "resident",
            contexts={ExecutionContext.IRQ},
        )
        deferred = function("deferred")
        entry = function(
            "entry",
            attributes={"exported"},
            contexts={ExecutionContext.PROCESS},
        )
        for node in (resident, deferred, entry):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(entry.id, deferred.id, EdgeKind.DIRECT_CALL)
        )
        graph.add_edge(
            ReferenceEdge(
                deferred.id, resident.id, EdgeKind.DIRECT_CALL
            )
        )

        plan = plan_modules(graph)

        decision = plan.decision_for(deferred.id)
        self.assertEqual(decision.disposition, Disposition.UNKNOWN)
        self.assertIn("non-exported resident function", decision.reasons[0])

    def test_always_inline_helper_is_compile_time_visible(self):
        graph = ReferenceGraph()
        caller = function("caller")
        helper = function(
            "header_helper",
            attributes={"__always_inline"},
        )
        graph.add_node(caller)
        graph.add_node(helper)
        graph.add_edge(
            ReferenceEdge(
                caller.id,
                helper.id,
                EdgeKind.DIRECT_CALL,
            )
        )

        plan = plan_modules(graph)

        self.assertEqual(
            plan.decision_for(helper.id).disposition,
            Disposition.CORE,
        )
        self.assertEqual(
            plan.decision_for(caller.id).disposition,
            Disposition.MODULE,
        )

    def test_inline_hint_helper_is_compile_time_visible(self):
        graph = ReferenceGraph()
        caller = function("caller")
        helper = function(
            "header_helper",
            attributes={"inline_hint"},
        )
        graph.add_node(caller)
        graph.add_node(helper)
        graph.add_edge(
            ReferenceEdge(
                caller.id,
                helper.id,
                EdgeKind.DIRECT_CALL,
            )
        )

        plan = plan_modules(graph)

        self.assertEqual(
            plan.decision_for(helper.id).disposition,
            Disposition.CORE,
        )
        self.assertEqual(
            plan.decision_for(caller.id).disposition,
            Disposition.MODULE,
        )

    def test_linker_registered_callback_stays_core(self):
        graph = ReferenceGraph()
        callback = function(
            "registered_callback",
            attributes={"linker_registered"},
        )
        graph.add_node(callback)

        plan = plan_modules(graph)

        self.assertEqual(
            plan.decision_for(callback.id).disposition,
            Disposition.CORE,
        )

    def test_linker_registered_global_table_seeds_callbacks(self):
        graph = ReferenceGraph()
        registry = ReferenceNode.entity(
            EntityKind.GLOBAL,
            "vendor_ops",
            owner="vendor.c",
            attributes={"immutable", "linker_registered"},
        )
        callback = function("vendor_init")
        dependency = function("vendor_init_dependency")
        for node in (registry, callback, dependency):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                registry.id,
                callback.id,
                EdgeKind.GLOBAL_INITIALIZER,
            )
        )
        graph.add_edge(
            ReferenceEdge(
                callback.id,
                dependency.id,
                EdgeKind.DIRECT_CALL,
            )
        )

        plan = plan_modules(
            graph,
            PlannerPolicy(
                phase_aware_boot=True,
                dependency_edges=frozenset({EdgeKind.DIRECT_CALL}),
            ),
        )

        self.assertEqual(
            plan.decision_for(callback.id).disposition,
            Disposition.CORE,
        )
        self.assertEqual(
            plan.decision_for(dependency.id).disposition,
            Disposition.CORE,
        )

    def test_mutable_global_colocates_function_owners(self):
        graph = ReferenceGraph()
        first = function("first")
        second = function("second")
        state = ReferenceNode.entity(
            EntityKind.GLOBAL,
            "state",
            owner="demo.c",
            attributes={"mutable", "internal"},
        )
        for node in (first, second, state):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(first.id, state.id, EdgeKind.GLOBAL_WRITE)
        )
        graph.add_edge(
            ReferenceEdge(second.id, state.id, EdgeKind.GLOBAL_READ)
        )

        plan = plan_modules(graph)

        self.assertEqual(len(plan.candidates), 1)
        self.assertEqual(plan.candidates[0].owned_globals, (state.id,))

    def test_mutable_global_shared_with_core_is_unknown(self):
        graph = ReferenceGraph()
        core = function("core", attributes={"exported"})
        movable = function("movable")
        state = ReferenceNode.entity(
            EntityKind.GLOBAL,
            "state",
            owner="demo.c",
            attributes={"mutable"},
        )
        for node in (core, movable, state):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(core.id, state.id, EdgeKind.GLOBAL_WRITE)
        )
        graph.add_edge(
            ReferenceEdge(movable.id, state.id, EdgeKind.GLOBAL_READ)
        )

        plan = plan_modules(graph)

        decision = plan.decision_for(movable.id)
        self.assertEqual(decision.disposition, Disposition.UNKNOWN)
        self.assertIn("shares mutable global", decision.reasons[0])

    def test_compiler_metadata_is_not_runtime_mutable_state(self):
        graph = ReferenceGraph()
        core = function("core", attributes={"exported"})
        movable = function("movable")
        compiler_used = ReferenceNode.entity(
            EntityKind.GLOBAL,
            "llvm.compiler.used",
            owner="demo.c",
            attributes={"mutable", "internal"},
            section="llvm.metadata",
        )
        for node in (core, movable, compiler_used):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                compiler_used.id, core.id, EdgeKind.GLOBAL_INITIALIZER
            )
        )
        graph.add_edge(
            ReferenceEdge(
                compiler_used.id,
                movable.id,
                EdgeKind.GLOBAL_INITIALIZER,
            )
        )

        plan = plan_modules(graph)

        decision = plan.decision_for(movable.id)
        self.assertEqual(decision.disposition, Disposition.MODULE)
        self.assertFalse(
            any("shares mutable global" in reason for reason in decision.reasons)
        )

    def test_generated_and_retention_globals_are_not_extraction_owned(self):
        graph = ReferenceGraph()
        movable = function("movable")
        state = ReferenceNode.entity(
            EntityKind.GLOBAL,
            "state",
            owner="demo.c",
            attributes={"mutable", "internal"},
        )
        literal = ReferenceNode.entity(
            EntityKind.GLOBAL,
            ".str.1",
            owner="demo.c",
            attributes={"immutable", "compiler_generated", "internal"},
        )
        retention = ReferenceNode.entity(
            EntityKind.GLOBAL,
            "__UNIQUE_ID___addressable_movable1",
            owner="demo.c",
            attributes={"mutable", "internal"},
            section=".discard.addressable",
        )
        for node in (movable, state, literal, retention):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(movable.id, state.id, EdgeKind.GLOBAL_WRITE)
        )
        graph.add_edge(
            ReferenceEdge(movable.id, literal.id, EdgeKind.GLOBAL_READ)
        )
        graph.add_edge(
            ReferenceEdge(
                retention.id,
                movable.id,
                EdgeKind.GLOBAL_INITIALIZER,
            )
        )

        plan = plan_modules(graph)

        self.assertEqual(len(plan.candidates), 1)
        self.assertEqual(plan.candidates[0].owned_globals, (state.id,))

    def test_dead_code_requires_explicit_closed_world_policy(self):
        graph = ReferenceGraph()
        unused = function("unused")
        graph.add_node(unused)

        default_plan = plan_modules(graph)
        closed_plan = plan_modules(
            graph,
            PlannerPolicy(allow_dead_code_removal=True),
        )

        self.assertEqual(
            default_plan.decision_for(unused.id).disposition,
            Disposition.MODULE,
        )
        self.assertEqual(
            closed_plan.decision_for(unused.id).disposition,
            Disposition.DEAD,
        )

    def test_deferred_root_is_not_deleted(self):
        graph = ReferenceGraph()
        deferred = function("deferred")
        graph.add_node(deferred)

        plan = plan_modules(
            graph,
            PlannerPolicy(
                deferred_roots=frozenset({deferred.id}),
                allow_dead_code_removal=True,
            ),
        )

        self.assertEqual(
            plan.decision_for(deferred.id).disposition,
            Disposition.MODULE,
        )

    def test_unknown_policy_node_is_rejected(self):
        graph = ReferenceGraph()
        graph.add_node(function("present"))

        with self.assertRaisesRegex(GraphValidationError, "unknown node"):
            plan_modules(
                graph,
                PlannerPolicy(boot_roots=frozenset({"fn:external:missing"})),
            )


if __name__ == "__main__":
    unittest.main()

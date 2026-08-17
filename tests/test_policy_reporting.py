import json
from pathlib import Path
import tempfile
import unittest

from kernel_modularizer.cli import main
from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.io import write_json_atomic, write_reference_graph
from kernel_modularizer.model import (
    EdgeKind,
    ExecutionContext,
    Linkage,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNode,
)
from kernel_modularizer.planner import PlannerPolicy, plan_modules
from kernel_modularizer.policy import (
    planner_policy_from_dict,
    planner_policy_to_dict,
)
from kernel_modularizer.reporting import (
    module_plan_to_dict,
    render_markdown_report,
)


class PolicyAndReportingTests(unittest.TestCase):
    def make_graph(self):
        graph = ReferenceGraph(metadata={"kernel_commit": "fixture"})
        api = ReferenceNode.function(
            "api",
            Linkage.EXTERNAL,
            attributes={"exported"},
            contexts={ExecutionContext.PROCESS},
            size_bytes=32,
        )
        deferred = ReferenceNode.function(
            "deferred",
            Linkage.EXTERNAL,
            size_bytes=512,
        )
        graph.add_node(api)
        graph.add_node(deferred)
        graph.add_edge(
            ReferenceEdge(api.id, deferred.id, EdgeKind.DIRECT_CALL)
        )
        return graph

    def test_policy_resolves_unique_symbol_selector(self):
        graph = self.make_graph()

        policy = planner_policy_from_dict(
            {
                "schema_version": 1,
                "boot_roots": ["api"],
                "load_safe_roots": ["api"],
            },
            graph,
        )

        self.assertEqual(policy.boot_roots, {"fn:external:api"})
        self.assertEqual(policy.load_safe_roots, {"fn:external:api"})

    def test_policy_rejects_ambiguous_internal_symbol(self):
        graph = ReferenceGraph()
        graph.add_node(
            ReferenceNode.function(
                "probe",
                Linkage.INTERNAL,
                translation_unit="a.c",
            )
        )
        graph.add_node(
            ReferenceNode.function(
                "probe",
                Linkage.INTERNAL,
                translation_unit="b.c",
            )
        )

        with self.assertRaisesRegex(GraphValidationError, "ambiguous"):
            planner_policy_from_dict(
                {"schema_version": 1, "boot_roots": ["probe"]},
                graph,
            )

    def test_policy_resolves_encoded_function_domain_base(self):
        graph = ReferenceGraph()
        dispatch = ReferenceNode.function(
            "__dispatch_base",
            Linkage.EXTERNAL,
        )
        proto = ReferenceNode.global_variable(
            "bpf_helper_proto",
            Linkage.EXTERNAL,
        )
        graph.add_node(dispatch)
        graph.add_node(proto)

        policy = planner_policy_from_dict(
            {
                "schema_version": 1,
                "encoded_function_domains": {
                    "__dispatch_base": ["bpf_*_proto"]
                },
                "pack_load_safe_candidates_by_source": True,
            },
            graph,
        )

        self.assertEqual(
            policy.encoded_function_domains,
            (
                (
                    "fn:external:__dispatch_base",
                    ("bpf_*_proto",),
                ),
            ),
        )
        self.assertEqual(
            planner_policy_to_dict(policy)[
                "encoded_function_domains"
            ],
            {
                "fn:external:__dispatch_base": [
                    "bpf_*_proto"
                ]
            },
        )
        self.assertTrue(
            policy.pack_load_safe_candidates_by_source
        )
        self.assertTrue(
            planner_policy_to_dict(policy)[
                "pack_load_safe_candidates_by_source"
            ]
        )

    def test_loader_phase_policy_round_trip(self):
        graph = self.make_graph()
        policy = planner_policy_from_dict(
            {
                "schema_version": 1,
                "phase_aware_boot": True,
                "enforce_loader_ready_phase": True,
                "loader_ready_observed": True,
                "identify_preload_candidates": True,
                "syscall_entries_load_safe": True,
                "structural_syscall_entry_prefixes": [
                    "__x64_sys_",
                    "__ia32_sys_",
                ],
                "aggregate_savings_groups": True,
                "module_loader_roots": ["api"],
                "observed_boot_functions": ["api"],
                "post_loader_observed_functions": ["api"],
            },
            graph,
        )

        serialized = planner_policy_to_dict(policy)
        self.assertTrue(serialized["enforce_loader_ready_phase"])
        self.assertTrue(serialized["loader_ready_observed"])
        self.assertTrue(serialized["identify_preload_candidates"])
        self.assertEqual(
            serialized["structural_syscall_entry_prefixes"],
            ["__x64_sys_", "__ia32_sys_"],
        )
        self.assertTrue(serialized["aggregate_savings_groups"])
        self.assertEqual(
            serialized["post_loader_observed_functions"],
            ["fn:external:api"],
        )

    def test_report_contains_boundary_and_size_evidence(self):
        graph = self.make_graph()
        policy = PlannerPolicy()
        plan = plan_modules(graph, policy)

        report = module_plan_to_dict(graph, policy, plan)
        markdown = render_markdown_report(report)

        candidate = next(
            item
            for item in report["candidates"]
            if "fn:external:deferred" in item["functions"]
        )
        self.assertEqual(candidate["interfaces"], ["fn:external:deferred"])
        self.assertEqual(candidate["known_size_bytes"], 512)
        self.assertEqual(candidate["estimated_net_bytes"], -128)
        self.assertEqual(candidate["executable_interface_edges"], 1)
        self.assertEqual(candidate["load_safe_interface_edges"], 1)
        self.assertEqual(candidate["readiness"], "BLOCKED")
        self.assertEqual(
            report["summary"]["candidate_viability"],
            {
                "meeting_minimum_savings": 0,
                "meeting_savings_with_proven_load_safe_interface": 0,
                "positive_net": 0,
                "ready": 0,
                "ready_via_aggregate_savings": 0,
                "with_executable_interface": 1,
                "with_proven_load_safe_interface": 1,
                "with_resident_interface": 1,
            },
        )
        self.assertIn("Linux Function Modularization Plan", markdown)
        self.assertIn("fn:external:deferred", markdown)

    def test_candidate_without_lazy_load_interface_is_blocked(self):
        graph = ReferenceGraph()
        orphan = ReferenceNode.function(
            "orphan",
            Linkage.EXTERNAL,
            size_bytes=1024,
        )
        graph.add_node(orphan)

        report = module_plan_to_dict(
            graph, PlannerPolicy(), plan_modules(graph)
        )

        self.assertEqual(report["candidates"][0]["readiness"], "BLOCKED")
        self.assertIn(
            "no resident lazy-load interface",
            report["candidates"][0]["readiness_reasons"][0],
        )

    def test_observed_process_context_alone_needs_load_safe_evidence(self):
        graph = ReferenceGraph()
        observed = ReferenceNode.function(
            "observed",
            Linkage.EXTERNAL,
            attributes={"observed_context=process"},
            size_bytes=64,
        )
        deferred = ReferenceNode.function(
            "deferred",
            Linkage.EXTERNAL,
            size_bytes=8192,
        )
        graph.add_node(observed)
        graph.add_node(deferred)
        graph.add_edge(
            ReferenceEdge(
                observed.id, deferred.id, EdgeKind.DIRECT_CALL
            )
        )
        policy = PlannerPolicy(
            observed_boot_functions=frozenset({observed.id}),
            phase_aware_boot=True,
        )

        report = module_plan_to_dict(
            graph,
            policy,
            plan_modules(graph, policy),
            interface_stub_bytes=0,
            additional_interface_stub_bytes=0,
            minimum_estimated_savings_bytes=4096,
        )

        candidate = report["candidates"][0]
        self.assertEqual(candidate["readiness"], "NEEDS_EVIDENCE")
        self.assertIn(
            "caller context is not proven load-safe",
            candidate["readiness_reasons"][0],
        )

    def test_syscall_policy_can_produce_load_safe_ready_boundary(self):
        graph = ReferenceGraph()
        loader = ReferenceNode.function(
            "__x64_sys_finit_module",
            Linkage.EXTERNAL,
            attributes={"syscall_entry"},
            size_bytes=64,
        )
        loader_body = ReferenceNode.function(
            "do_finit_module",
            Linkage.EXTERNAL,
            size_bytes=128,
        )
        optional = ReferenceNode.function(
            "__x64_sys_optional",
            Linkage.EXTERNAL,
            attributes={"syscall_entry"},
            size_bytes=64,
        )
        optional_body = ReferenceNode.function(
            "do_optional",
            Linkage.EXTERNAL,
            size_bytes=8192,
        )
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

        report = module_plan_to_dict(
            graph,
            policy,
            plan_modules(graph, policy),
            interface_stub_bytes=768,
            minimum_estimated_savings_bytes=4096,
        )

        candidate = next(
            value
            for value in report["candidates"]
            if optional_body.id in value["functions"]
        )
        self.assertEqual(candidate["readiness"], "READY")
        self.assertEqual(candidate["executable_interface_edges"], 1)
        self.assertEqual(candidate["load_safe_interface_edges"], 1)
        self.assertEqual(
            report["summary"]["candidate_viability"]["ready"],
            1,
        )
        self.assertTrue(
            report["policy"]["syscall_entries_load_safe"]
        )
        self.assertEqual(
            report["policy"]["module_loader_roots"],
            [loader.id],
        )

    def test_loader_phase_report_marks_syscall_boundary_lazy_ready(self):
        graph = ReferenceGraph()
        loader = ReferenceNode.function(
            "__x64_sys_finit_module",
            Linkage.EXTERNAL,
            attributes={"syscall_entry"},
            size_bytes=64,
        )
        optional = ReferenceNode.function(
            "__x64_sys_optional",
            Linkage.EXTERNAL,
            attributes={"syscall_entry"},
            size_bytes=64,
        )
        optional_body = ReferenceNode.function(
            "do_optional",
            Linkage.EXTERNAL,
            size_bytes=8192,
        )
        for node in (loader, optional, optional_body):
            graph.add_node(node)
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
            enforce_loader_ready_phase=True,
            loader_ready_observed=True,
        )

        report = module_plan_to_dict(
            graph,
            policy,
            plan_modules(graph, policy),
            minimum_estimated_savings_bytes=4096,
        )
        candidate = next(
            item
            for item in report["candidates"]
            if optional_body.id in item["functions"]
        )

        self.assertEqual(
            candidate["loader_phase_classification"],
            "LAZY_READY",
        )
        self.assertEqual(candidate["readiness"], "READY")
        self.assertEqual(
            report["summary"]["loader_phase"][
                "candidate_classifications"
            ],
            {
                "EARLY_CORE": 0,
                "PRELOAD": 0,
                "LAZY_READY": 1,
                "UNKNOWN_PHASE": 0,
            },
        )

    def test_loader_phase_report_marks_atomic_boundary_preload(self):
        graph = ReferenceGraph()
        loader = ReferenceNode.function(
            "module_loader",
            Linkage.EXTERNAL,
            size_bytes=64,
        )
        callback = ReferenceNode.function(
            "late_callback",
            Linkage.EXTERNAL,
            attributes={"observed_context=atomic"},
            contexts={ExecutionContext.ATOMIC},
            size_bytes=64,
        )
        dependency = ReferenceNode.function(
            "preloaded_dependency",
            Linkage.EXTERNAL,
            size_bytes=8192,
        )
        for node in (loader, callback, dependency):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                callback.id,
                dependency.id,
                EdgeKind.DIRECT_CALL,
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

        report = module_plan_to_dict(
            graph,
            policy,
            plan_modules(graph, policy),
            minimum_estimated_savings_bytes=4096,
        )
        candidate = next(
            item
            for item in report["candidates"]
            if dependency.id in item["functions"]
        )

        self.assertEqual(
            candidate["loader_phase_classification"], "PRELOAD"
        )
        self.assertEqual(candidate["readiness"], "NEEDS_EVIDENCE")
        self.assertIn(
            "requires explicit preload",
            " ".join(candidate["readiness_reasons"]),
        )

    def test_positive_candidates_can_meet_savings_as_release_group(self):
        graph = ReferenceGraph()
        first_api = ReferenceNode.function(
            "first_api",
            Linkage.EXTERNAL,
            attributes={"exported"},
            size_bytes=64,
        )
        second_api = ReferenceNode.function(
            "second_api",
            Linkage.EXTERNAL,
            attributes={"exported"},
            size_bytes=64,
        )
        first_body = ReferenceNode.function(
            "first_body",
            Linkage.EXTERNAL,
            source_path="first.c",
            size_bytes=3000,
        )
        second_body = ReferenceNode.function(
            "second_body",
            Linkage.EXTERNAL,
            source_path="second.c",
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
        policy = PlannerPolicy(
            load_safe_roots=frozenset(
                {first_api.id, second_api.id}
            ),
            aggregate_savings_groups=True,
        )

        report = module_plan_to_dict(
            graph,
            policy,
            plan_modules(graph, policy),
            interface_stub_bytes=640,
            additional_interface_stub_bytes=384,
            minimum_estimated_savings_bytes=4096,
        )

        self.assertEqual(
            report["summary"]["candidate_readiness"],
            {"READY": 2},
        )
        self.assertEqual(
            report["summary"]["candidate_viability"][
                "ready_via_aggregate_savings"
            ],
            2,
        )
        groups = report["summary"]["aggregate_savings"]["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["candidates"], 2)
        self.assertEqual(groups[0]["functions"], 2)
        self.assertEqual(groups[0]["estimated_net_bytes"], 4720)
        self.assertTrue(
            all(
                candidate["readiness_basis"]
                == "aggregate_savings_group"
                and candidate["individual_minimum_met"] is False
                and candidate["aggregate_savings_group"]
                == groups[0]["id"]
                for candidate in report["candidates"]
            )
        )

    def test_additional_interfaces_use_measured_amortized_cost(self):
        graph = ReferenceGraph()
        first_api = ReferenceNode.function(
            "first_api",
            Linkage.EXTERNAL,
            attributes={"exported"},
            size_bytes=64,
        )
        second_api = ReferenceNode.function(
            "second_api",
            Linkage.EXTERNAL,
            attributes={"exported"},
            size_bytes=64,
        )
        first_body = ReferenceNode.function(
            "first_body",
            Linkage.EXTERNAL,
            source_path="shared.c",
            size_bytes=700,
        )
        second_body = ReferenceNode.function(
            "second_body",
            Linkage.EXTERNAL,
            source_path="shared.c",
            size_bytes=700,
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
        policy = PlannerPolicy(
            load_safe_roots=frozenset(
                {first_api.id, second_api.id}
            ),
            pack_load_safe_candidates_by_source=True,
        )

        report = module_plan_to_dict(
            graph,
            policy,
            plan_modules(graph, policy),
            interface_stub_bytes=640,
            additional_interface_stub_bytes=384,
            minimum_estimated_savings_bytes=0,
        )

        self.assertEqual(len(report["candidates"]), 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["estimated_stub_bytes"], 1024)
        self.assertEqual(candidate["estimated_net_bytes"], 376)
        self.assertEqual(candidate["readiness"], "READY")
        self.assertEqual(
            report["summary"]["additional_interface_stub_bytes"],
            384,
        )

    def test_aggregate_savings_never_hides_evidence_failure(self):
        graph = ReferenceGraph()
        proven_api = ReferenceNode.function(
            "proven_api",
            Linkage.EXTERNAL,
            attributes={"exported"},
            size_bytes=64,
        )
        unknown_api = ReferenceNode.function(
            "unknown_api",
            Linkage.EXTERNAL,
            attributes={"observed_context=process"},
            size_bytes=64,
        )
        proven_body = ReferenceNode.function(
            "proven_body",
            Linkage.EXTERNAL,
            source_path="proven.c",
            size_bytes=3000,
        )
        unknown_body = ReferenceNode.function(
            "unknown_body",
            Linkage.EXTERNAL,
            source_path="unknown.c",
            size_bytes=3000,
        )
        for node in (proven_api, unknown_api, proven_body, unknown_body):
            graph.add_node(node)
        graph.add_edge(
            ReferenceEdge(
                proven_api.id,
                proven_body.id,
                EdgeKind.DIRECT_CALL,
            )
        )
        graph.add_edge(
            ReferenceEdge(
                unknown_api.id,
                unknown_body.id,
                EdgeKind.DIRECT_CALL,
            )
        )
        policy = PlannerPolicy(
            observed_boot_functions=frozenset({unknown_api.id}),
            load_safe_roots=frozenset({proven_api.id}),
            phase_aware_boot=True,
            aggregate_savings_groups=True,
        )

        report = module_plan_to_dict(
            graph,
            policy,
            plan_modules(graph, policy),
            interface_stub_bytes=640,
            additional_interface_stub_bytes=384,
            minimum_estimated_savings_bytes=4096,
        )

        self.assertEqual(
            report["summary"]["aggregate_savings"]["groups"],
            [],
        )
        self.assertTrue(
            any(
                candidate["readiness"] != "READY"
                and any(
                    "caller context is not proven load-safe" in reason
                    for reason in candidate["readiness_reasons"]
                )
                for candidate in report["candidates"]
            )
        )

    def test_deferred_init_requires_registration_rewrite_evidence(self):
        graph = ReferenceGraph()
        observed = ReferenceNode.function(
            "observed",
            Linkage.EXTERNAL,
            attributes={"observed_context=process"},
            size_bytes=64,
        )
        deferred = ReferenceNode.function(
            "deferred_init",
            Linkage.EXTERNAL,
            attributes={"__init"},
            section=".init.text",
            size_bytes=8192,
        )
        graph.add_node(observed)
        graph.add_node(deferred)
        graph.add_edge(
            ReferenceEdge(
                observed.id, deferred.id, EdgeKind.DIRECT_CALL
            )
        )
        policy = PlannerPolicy(
            observed_boot_functions=frozenset({observed.id}),
            load_safe_roots=frozenset({observed.id}),
            phase_aware_boot=True,
            trace_covers_initcalls=True,
        )

        report = module_plan_to_dict(
            graph,
            policy,
            plan_modules(graph, policy),
            interface_stub_bytes=0,
            additional_interface_stub_bytes=0,
            minimum_estimated_savings_bytes=4096,
        )

        candidate = report["candidates"][0]
        self.assertEqual(candidate["readiness"], "NEEDS_EVIDENCE")
        self.assertIn(
            "lacks a reviewed initcall registration rewrite",
            candidate["readiness_reasons"][0],
        )

    def test_plan_cli_writes_deterministic_artifacts(self):
        graph = self.make_graph()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph_path = root / "graph.json"
            policy_path = root / "policy.json"
            json_output = root / "plan.json"
            markdown_output = root / "plan.md"
            manifest_output = root / "manifest.json"
            write_reference_graph(graph_path, graph)
            write_json_atomic(
                policy_path,
                {
                    "schema_version": 1,
                    "resident_roots": ["api"],
                },
            )

            result = main(
                [
                    "plan",
                    str(graph_path),
                    "--policy",
                    str(policy_path),
                    "--json-output",
                    str(json_output),
                    "--markdown-output",
                    str(markdown_output),
                    "--manifest-output",
                    str(manifest_output),
                ]
            )

            self.assertEqual(result, 0)
            report = json.loads(json_output.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["functions"], 2)
            self.assertEqual(
                report["summary"]["additional_interface_stub_bytes"],
                96,
            )
            self.assertTrue(markdown_output.read_text(encoding="utf-8"))
            manifest = json.loads(
                manifest_output.read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["stage"], "module-plan")


if __name__ == "__main__":
    unittest.main()

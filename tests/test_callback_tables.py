import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kernel_modularizer.callback_tables import (
    analyze_callback_tables,
    prepare_callback_tables,
    render_callback_table_markdown,
)
from kernel_modularizer.model import (
    EdgeKind,
    EntityKind,
    Linkage,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNode,
)


FILE_SHAPE = "layout-shape:file-operations|264"
SEQ_SHAPE = "layout-shape:seq-operations|32"


def function(symbol, size=100, source="fs/demo.c", attributes=()):
    return ReferenceNode.function(
        symbol,
        Linkage.INTERNAL,
        translation_unit=source,
        source_path=source,
        size_bytes=size,
        attributes={"definition", *attributes},
    )


def table(symbol, source="fs/demo.c"):
    return ReferenceNode.global_variable(
        symbol,
        Linkage.INTERNAL,
        translation_unit=source,
        source_path=source,
        attributes={"definition"},
    )


def connect(graph, owner, target, shape, offset):
    # LLVM emits several provenance views for one aggregate slot.  The
    # analyzer must count the target once while retaining the layout offset.
    for field_path in (
        f"{shape}|{offset}",
        f"layout:{owner.id}|264|{offset}",
        f"{{ ptr, ptr }}|{offset // 8}",
    ):
        graph.add_edge(
            ReferenceEdge(
                owner.id,
                target.id,
                EdgeKind.GLOBAL_INITIALIZER,
                field_path=field_path,
                evidence=frozenset({"llvm-reference-facts"}),
            )
        )


def plan(*nodes, observed=(), complete=True):
    return {
        "schema_version": 1,
        "policy": {
            "phase_aware_boot": complete,
            "trace_covers_initcalls": complete,
            "loader_ready_observed": complete,
            "observed_boot_functions": [item.id for item in observed],
            "pre_loader_observed_functions": [item.id for item in observed],
            "post_loader_observed_functions": [],
            "hard_attributes": ["__init", "early_boot"],
            "resident_attributes": ["exported", "syscall_entry"],
            "non_loadable_contexts": ["atomic", "irq", "nmi"],
        },
        "summary": {
            "interface_stub_bytes": 640,
            "additional_interface_stub_bytes": 96,
        },
        "decisions": [
            {
                "id": item.id,
                "symbol": item.symbol,
                "disposition": "core",
            }
            for item in nodes
        ],
    }


class CallbackTableAnalysisTests(unittest.TestCase):
    def test_deduplicates_edges_and_keeps_lifetime_and_boot_callbacks(self):
        graph = ReferenceGraph()
        anchor = table("anchor_fops", "fs/anchor.c")
        anchor_read = function("anchor_read", source="fs/anchor.c")
        target_table = table("opaque_callbacks")
        ioctl = function("demo_ioctl", 3200)
        poll = function("demo_poll", 400)
        release = function("demo_release", 250)
        boot_read = function("demo_read", 900)
        for node in (
            anchor,
            anchor_read,
            target_table,
            ioctl,
            poll,
            release,
            boot_read,
        ):
            graph.add_node(node)
        connect(graph, anchor, anchor_read, FILE_SHAPE, 16)
        connect(graph, target_table, ioctl, FILE_SHAPE, 80)
        connect(graph, target_table, poll, FILE_SHAPE, 72)
        connect(graph, target_table, release, FILE_SHAPE, 128)
        connect(graph, target_table, boot_read, FILE_SHAPE, 16)

        report = analyze_callback_tables(
            graph,
            plan(
                anchor_read,
                ioctl,
                poll,
                release,
                boot_read,
                observed=(boot_read,),
            ),
        )

        selected = next(
            item
            for item in report["tables"]
            if item["table_symbol"] == "opaque_callbacks"
        )
        self.assertEqual(selected["family"], "file_operations")
        self.assertEqual(selected["status"], "READY")
        self.assertEqual(
            selected["recommended_interface_symbols"],
            ["demo_ioctl", "demo_poll"],
        )
        self.assertEqual(
            set(selected["resident_interface_symbols"]),
            {"demo_read", "demo_release"},
        )
        self.assertEqual(selected["known_interface_bytes"], 3600)
        self.assertEqual(selected["estimated_stub_bytes"], 736)
        self.assertEqual(selected["estimated_direct_net_bytes"], 2864)
        self.assertEqual(
            report["summary"][
                "duplicate_initializer_evidence_edges_removed"
            ],
            10,
        )
        release_report = next(
            item
            for item in selected["callbacks"]
            if item["symbol"] == "demo_release"
        )
        self.assertEqual(release_report["field_names"], ["release"])
        self.assertIn("persistent state", release_report["reasons"][0])

    def test_seq_table_and_missing_boot_proof_are_not_automatic(self):
        graph = ReferenceGraph()
        seq = table("demo_seq_ops")
        show = function("demo_show")
        fops = table("demo_fops", "fs/other.c")
        read = function("demo_read", source="fs/other.c")
        for node in (seq, show, fops, read):
            graph.add_node(node)
        connect(graph, seq, show, SEQ_SHAPE, 24)
        connect(graph, fops, read, FILE_SHAPE, 16)

        report = analyze_callback_tables(
            graph, plan(show, read, complete=False)
        )
        by_name = {item["table_symbol"]: item for item in report["tables"]}
        self.assertEqual(by_name["demo_seq_ops"]["status"], "REVIEW_REQUIRED")
        self.assertEqual(
            by_name["demo_fops"]["status"], "BOOT_EVIDENCE_MISSING"
        )
        self.assertFalse(report["boot_evidence"]["complete_for_init_cold"])

    def test_source_group_deduplicates_shared_wrappers(self):
        graph = ReferenceGraph()
        first = table("first_fops")
        second = table("second_fops")
        shared = function("shared_read", 1200)
        first_ioctl = function("first_ioctl", 1800)
        for node in (first, second, shared, first_ioctl):
            graph.add_node(node)
        connect(graph, first, shared, FILE_SHAPE, 16)
        connect(graph, first, first_ioctl, FILE_SHAPE, 80)
        connect(graph, second, shared, FILE_SHAPE, 16)

        report = analyze_callback_tables(
            graph, plan(shared, first_ioctl)
        )

        group = report["source_groups"][0]
        self.assertEqual(len(group["table_ids"]), 2)
        self.assertEqual(group["interface_symbols"], [
            "first_ioctl",
            "shared_read",
        ])
        self.assertEqual(group["known_interface_bytes"], 3000)
        self.assertEqual(group["estimated_stub_bytes"], 736)
        self.assertIn("Highest-ranked tables", render_callback_table_markdown(report))

    def test_recommends_complementary_subthreshold_release_pairs(self):
        graph = ReferenceGraph()
        first = table("first_fops", "fs/first.c")
        first_read = function("first_read", 3000, "fs/first.c")
        second = table("second_fops", "fs/second.c")
        second_read = function("second_read", 2600, "fs/second.c")
        standalone = table("standalone_fops", "fs/standalone.c")
        standalone_read = function(
            "standalone_read", 5000, "fs/standalone.c"
        )
        for node in (
            first,
            first_read,
            second,
            second_read,
            standalone,
            standalone_read,
        ):
            graph.add_node(node)
        connect(graph, first, first_read, FILE_SHAPE, 16)
        connect(graph, second, second_read, FILE_SHAPE, 16)
        connect(graph, standalone, standalone_read, FILE_SHAPE, 16)

        report = analyze_callback_tables(
            graph,
            plan(first_read, second_read, standalone_read),
            minimum_release_savings_bytes=4096,
        )

        self.assertEqual(
            report["summary"]["estimated_standalone_release_groups"], 1
        )
        self.assertEqual(
            report["summary"][
                "estimated_complementary_pair_portfolios"
            ],
            1,
        )
        portfolio = report["release_portfolios"][0]
        self.assertEqual(
            portfolio["source_paths"], ["fs/first.c", "fs/second.c"]
        )
        self.assertEqual(portfolio["estimated_direct_net_bytes"], 4320)
        self.assertEqual(portfolio["interface_count"], 2)
        markdown = render_callback_table_markdown(report)
        self.assertIn("Complementary release portfolios", markdown)
        self.assertIn("fs/first.c", markdown)

    def test_table_only_preparation_synthesizes_ready_candidate(self):
        graph = ReferenceGraph()
        fops = table("demo_fops")
        read = function("demo_read", 1800)
        release = function("demo_release", 100)
        for node in (fops, read, release):
            graph.add_node(node)
        connect(graph, fops, read, FILE_SHAPE, 16)
        connect(graph, fops, release, FILE_SHAPE, 128)

        captured = {}

        def capture(_graph, report_path, candidate_id, **kwargs):
            captured["plan"] = json.loads(Path(report_path).read_text())
            captured["candidate_id"] = candidate_id
            captured["kwargs"] = kwargs

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = root / "plan.json"
            plan_path.write_text(
                json.dumps(plan(read, release)), encoding="utf-8"
            )
            discovery = root / "discovery.json"
            with patch(
                "kernel_modularizer.candidate.prepare_candidate",
                side_effect=capture,
            ):
                selection = prepare_callback_tables(
                    graph,
                    plan_path,
                    ["demo_fops"],
                    source_extractor=root / "SourceExtractor",
                    compilation_database=root / "compile-db",
                    kernel_root=root / "linux",
                    module_name="deferred_demo",
                    extraction_output=root / "extraction.json",
                    backend_policy_output=root / "policy.json",
                    discovery_output=discovery,
                    expand_private_source_closure=True,
                )

            self.assertTrue(discovery.is_file())
            self.assertEqual(selection["interface_symbols"], ["demo_read"])
            self.assertEqual(
                selection["resident_interface_symbols"], ["demo_release"]
            )
            candidate = captured["plan"]["candidates"][0]
            self.assertEqual(candidate["readiness"], "READY")
            self.assertEqual(candidate["functions"], [read.id])
            self.assertEqual(captured["candidate_id"], candidate["id"])
            self.assertEqual(
                captured["kwargs"]["promoted_callback_tables"], [fops.id]
            )
            self.assertEqual(
                captured["kwargs"]["excluded_interfaces"], [release.id]
            )

    def test_table_only_preparation_drops_unspellable_resident_abi(self):
        graph = ReferenceGraph()
        fops = table("demo_fops")
        read = function("demo_read", 1800)
        poll = function("demo_poll", 900)
        release = function("demo_release", 100)
        for node in (fops, read, poll, release):
            graph.add_node(node)
        connect(graph, fops, read, FILE_SHAPE, 16)
        connect(graph, fops, poll, FILE_SHAPE, 72)
        connect(graph, fops, release, FILE_SHAPE, 128)

        calls = []

        def generate(_graph, report_path, candidate_id, **kwargs):
            candidate = json.loads(Path(report_path).read_text())[
                "candidates"
            ][0]
            calls.append(candidate["functions"])
            Path(kwargs["extraction_output"]).write_text(
                json.dumps(
                    {
                        "functions": [
                            {
                                "symbol": "demo_read",
                                "dependencies": {
                                    "functions": [],
                                    "globals": ["anonymous_table"],
                                },
                            },
                            {
                                "symbol": "demo_poll",
                                "dependencies": {
                                    "functions": [],
                                    "globals": [],
                                },
                            },
                        ],
                        "globals": [
                            {
                                "symbol": "anonymous_table",
                                "type": (
                                    "struct (unnamed struct at "
                                    "fs/demo.c:10:1)[2]"
                                ),
                                "dependencies": {
                                    "functions": [],
                                    "globals": [],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            Path(kwargs["backend_policy_output"]).write_text(
                json.dumps(
                    {
                        "resident_exports": [],
                        "external_resident_exports": ["anonymous_table"],
                        "resident_ro_after_init_globals": [],
                    }
                ),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = root / "plan.json"
            plan_path.write_text(
                json.dumps(plan(read, poll, release)), encoding="utf-8"
            )
            discovery = root / "discovery.json"
            with patch(
                "kernel_modularizer.candidate.prepare_candidate",
                side_effect=generate,
            ):
                selection = prepare_callback_tables(
                    graph,
                    plan_path,
                    ["demo_fops"],
                    source_extractor=root / "SourceExtractor",
                    compilation_database=root / "compile-db",
                    kernel_root=root / "linux",
                    module_name="deferred_demo",
                    extraction_output=root / "extraction.json",
                    backend_policy_output=root / "policy.json",
                    discovery_output=discovery,
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(set(calls[0]), {read.id, poll.id})
            self.assertEqual(calls[1], [poll.id])
            self.assertEqual(selection["interface_symbols"], ["demo_poll"])
            payload = json.loads(discovery.read_text())
            self.assertEqual(
                payload["selection"]["automatic_unbridgeable_interfaces"],
                {
                    "demo_read": [
                        "unspellable resident type: anonymous_table"
                    ]
                },
            )

    def test_table_only_preparation_drops_static_key_immediate_abi(self):
        graph = ReferenceGraph()
        fops = table("demo_fops")
        write = function("demo_write", 1800)
        poll = function("demo_poll", 900)
        release = function("demo_release", 100)
        for node in (fops, write, poll, release):
            graph.add_node(node)
        connect(graph, fops, write, FILE_SHAPE, 24)
        connect(graph, fops, poll, FILE_SHAPE, 72)
        connect(graph, fops, release, FILE_SHAPE, 128)

        calls = []

        def generate(_graph, report_path, candidate_id, **kwargs):
            candidate = json.loads(Path(report_path).read_text())[
                "candidates"
            ][0]
            calls.append(candidate["functions"])
            Path(kwargs["extraction_output"]).write_text(
                json.dumps(
                    {
                        "functions": [
                            {
                                "symbol": "demo_write",
                                "macros": ["static_branch_unlikely"],
                                "dependencies": {
                                    "address_taken_globals": ["demo_key"],
                                    "functions": [],
                                    "globals": ["demo_key"],
                                },
                            },
                            {
                                "symbol": "demo_poll",
                                "macros": [],
                                "dependencies": {
                                    "address_taken_globals": [],
                                    "functions": [],
                                    "globals": [],
                                },
                            },
                        ],
                        "globals": [
                            {
                                "symbol": "demo_key",
                                "type": "struct static_key_false",
                                "dependencies": {
                                    "functions": [],
                                    "globals": [],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            Path(kwargs["backend_policy_output"]).write_text(
                json.dumps(
                    {
                        "resident_exports": [],
                        "external_resident_exports": ["demo_key"],
                        "resident_ro_after_init_globals": [],
                    }
                ),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = root / "plan.json"
            plan_path.write_text(
                json.dumps(plan(write, poll, release)), encoding="utf-8"
            )
            discovery = root / "discovery.json"
            with patch(
                "kernel_modularizer.candidate.prepare_candidate",
                side_effect=generate,
            ):
                selection = prepare_callback_tables(
                    graph,
                    plan_path,
                    ["demo_fops"],
                    source_extractor=root / "SourceExtractor",
                    compilation_database=root / "compile-db",
                    kernel_root=root / "linux",
                    module_name="deferred_demo",
                    extraction_output=root / "extraction.json",
                    backend_policy_output=root / "policy.json",
                    discovery_output=discovery,
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(set(calls[0]), {write.id, poll.id})
            self.assertEqual(calls[1], [poll.id])
            self.assertEqual(selection["interface_symbols"], ["demo_poll"])
            payload = json.loads(discovery.read_text())
            self.assertEqual(
                payload["selection"]["automatic_unbridgeable_interfaces"],
                {
                    "demo_write": [
                        "link-time constant static-key address: demo_key "
                        "(static_branch_unlikely)"
                    ]
                },
            )


if __name__ == "__main__":
    unittest.main()

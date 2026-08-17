from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

from kernel_modularizer.facts import (
    TranslationUnitFacts,
    merge_translation_unit_facts,
)
from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.pointer_analysis import solve_pointer_constraints


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LlvmReferenceFactsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clang = shutil.which("clang")
        cls.clangxx = shutil.which("clang++")
        cls.opt = shutil.which("opt")
        cls.llvm_config = shutil.which("llvm-config")
        if not all((cls.clang, cls.clangxx, cls.opt, cls.llvm_config)):
            raise unittest.SkipTest("LLVM compiler, opt, or llvm-config is absent")

    def llvm_flags(self, option):
        output = subprocess.run(
            [self.llvm_config, option],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return shlex.split(output)

    def test_pass_emits_solvable_global_and_runtime_callbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            plugin = temporary / "ReferenceFacts.so"
            bitcode = temporary / "indirect_callbacks.bc"
            subprocess.run(
                [
                    self.clangxx,
                    *self.llvm_flags("--cxxflags"),
                    "-Wl,-znodelete",
                    "-fno-rtti",
                    "-fPIC",
                    "-shared",
                    str(
                        PROJECT_ROOT
                        / "Frontend/LLVM_PASS/ReferenceFacts.cpp"
                    ),
                    "-o",
                    str(plugin),
                    *self.llvm_flags("--ldflags"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    self.clang,
                    "-O0",
                    "-g",
                    "-emit-llvm",
                    "-c",
                    str(
                        PROJECT_ROOT
                        / "tests/fixtures/llvm/indirect_callbacks.c"
                    ),
                    "-o",
                    str(bitcode),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            major_version = int(
                subprocess.run(
                    [self.llvm_config, "--version"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                .stdout.split(".", 1)[0]
            )
            if major_version >= 16:
                pass_arguments = [
                    "-load-pass-plugin",
                    str(plugin),
                    "-passes=reference-facts",
                ]
            else:
                pass_arguments = [
                    "-load",
                    str(plugin),
                    "-enable-new-pm=0",
                    "-reference-facts",
                ]
            completed = subprocess.run(
                [
                    self.opt,
                    *pass_arguments,
                    f"-reference-facts-source-root={PROJECT_ROOT}",
                    "-disable-output",
                    str(bitcode),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            facts = TranslationUnitFacts.from_json_lines(completed.stdout)
            # Keep the reference solver assertion independent of whether the
            # optional Roaring backend is installed.  ``auto`` intentionally
            # selects the hybrid solver when pyroaring is available, and that
            # solver resolves the aggregate-copy callback below.
            result = solve_pointer_constraints(
                facts.pointer_program,
                backend="python",
            )
            try:
                hybrid_result = solve_pointer_constraints(
                    facts.pointer_program,
                    backend="hybrid",
                )
            except GraphValidationError as error:
                if "requires the optional" not in str(error):
                    raise
                hybrid_result = None
            available_completed = subprocess.run(
                [
                    self.opt,
                    *pass_arguments,
                    f"-reference-facts-source-root={PROJECT_ROOT}",
                    "-disable-output",
                    str(
                        PROJECT_ROOT
                        / "tests/fixtures/llvm/available_externally.ll"
                    ),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            available_facts = TranslationUnitFacts.from_json_lines(
                available_completed.stdout
            )
            override_facts = []
            for fixture in ("weak_default.ll", "strong_override.ll"):
                override_completed = subprocess.run(
                    [
                        self.opt,
                        *pass_arguments,
                        f"-reference-facts-source-root={PROJECT_ROOT}",
                        "-disable-output",
                        str(PROJECT_ROOT / "tests/fixtures/llvm" / fixture),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                override_facts.append(
                    TranslationUnitFacts.from_json_lines(
                        override_completed.stdout
                    )
                )
            override_graph, override_program = merge_translation_unit_facts(
                override_facts
            )

        self.assertFalse(
            any(
                node.id.startswith("fn:")
                and node.symbol.startswith("llvm.")
                for node in facts.graph.nodes.values()
            )
        )
        self.assertFalse(
            any(
                edge.target.startswith("fn:external:llvm.")
                for edge in facts.graph.edges
            )
        )
        self.assertIn(
            "global:internal:tests/fixtures/llvm/"
            "indirect_callbacks.c:llvm.compiler.used",
            facts.graph.nodes,
        )
        self.assertNotIn(
            "global:external:llvm.compiler.used",
            facts.graph.nodes,
        )
        compiler_used = facts.graph.nodes[
            "global:internal:tests/fixtures/llvm/"
            "indirect_callbacks.c:llvm.compiler.used"
        ]
        self.assertIn("compiler_metadata", compiler_used.attributes)
        self.assertIn("immutable", compiler_used.attributes)
        self.assertNotIn("mutable", compiler_used.attributes)
        string_literals = [
            node
            for node in facts.graph.nodes.values()
            if node.kind.value == "global"
            and (
                node.symbol == ".str"
                or node.symbol.startswith(".str.")
            )
        ]
        self.assertTrue(string_literals)
        self.assertTrue(
            all(
                "compiler_generated" in node.attributes
                for node in string_literals
            )
        )
        retention = next(
            node
            for node in facts.graph.nodes.values()
            if node.symbol == "retention_metadata"
        )
        self.assertIn("retention_metadata", retention.attributes)
        self.assertIn("immutable", retention.attributes)
        self.assertNotIn("mutable", retention.attributes)
        available = available_facts.graph.nodes["fn:external:header_helper"]
        self.assertIn("available_externally", available.attributes)
        self.assertIn("declaration", available.attributes)
        self.assertNotIn("definition", available.attributes)
        self.assertIsNone(available.source_path)
        inline_hint = available_facts.graph.nodes[
            "fn:internal:tests/fixtures/llvm/"
            "available_externally.ll:inline_hint_helper"
        ]
        self.assertIn("inline_hint", inline_hint.attributes)
        table_syscall = available_facts.graph.nodes[
            "fn:external:table_syscall"
        ]
        self.assertIn("syscall_entry", table_syscall.attributes)
        encoded_calls = [
            call
            for call in available_facts.pointer_program.calls.values()
            if call.caller == "fn:external:exercise_encoded_dispatch"
        ]
        self.assertEqual(len(encoded_calls), 1)
        self.assertEqual(
            encoded_calls[0].encoded_function_base,
            "fn:external:encoded_dispatch_base",
        )
        self.assertFalse(
            any(
                call.caller == "fn:external:header_helper"
                for call in available_facts.pointer_program.calls.values()
            )
        )
        self.assertFalse(
            any(
                call.caller == "fn:external:exercise_inline_asm"
                for call in facts.pointer_program.calls.values()
            )
        )
        replaceable = override_graph.nodes["fn:external:replaceable"]
        self.assertEqual(
            replaceable.source_path,
            "tests/fixtures/llvm/strong_override.ll",
        )
        self.assertIn("weak_for_linker", replaceable.attributes)
        replaceable_calls = [
            call
            for call in override_program.calls.values()
            if call.caller == "fn:external:replaceable"
        ]
        self.assertEqual(len(replaceable_calls), 2)
        self.assertEqual(
            len({call.id for call in replaceable_calls}),
            2,
        )
        indirect_targets = {
            target
            for call_id, targets in result.call_targets.items()
            if facts.pointer_program.calls[call_id].is_indirect
            for target in targets
        }
        self.assertIn("fn:external:external_callback", indirect_targets)
        self.assertIn(
            "fn:internal:tests/fixtures/llvm/indirect_callbacks.c:local_first",
            indirect_targets,
        )
        self.assertIn(
            "fn:internal:tests/fixtures/llvm/indirect_callbacks.c:local_second",
            indirect_targets,
        )
        nested_struct_paths = {
            gep.field_path
            for gep in facts.pointer_program.geps
            if "struct.nested_ops" in gep.field_path
        }
        self.assertTrue(
            any(path.endswith("|1") for path in nested_struct_paths),
            nested_struct_paths,
        )
        layout_paths = {
            gep.field_path
            for gep in facts.pointer_program.geps
            if gep.field_path.startswith("layout:")
        }
        self.assertTrue(
            any(
                "global:internal:tests/fixtures/llvm/"
                "indirect_callbacks.c:nested_callbacks" in path
                for path in layout_paths
            ),
            layout_paths,
        )
        self.assertTrue(
            all(path.startswith("layout:global:") for path in layout_paths),
            layout_paths,
        )
        shape_layout_paths = {
            gep.field_path
            for gep in facts.pointer_program.geps
            if gep.field_path.startswith("layout-shape:")
        }
        self.assertTrue(shape_layout_paths)
        nested_callers = {"fn:external:exercise_nested_callback"}
        nested_calls_by_caller = {
            caller: {
                call_id
                for call_id, call in facts.pointer_program.calls.items()
                if call.caller == caller
            }
            for caller in nested_callers
        }
        self.assertTrue(
            all(nested_calls_by_caller.values()),
            nested_calls_by_caller,
        )
        nested_calls = {
            call_id
            for call_ids in nested_calls_by_caller.values()
            for call_id in call_ids
        }
        self.assertEqual(
            {
                target
                for call_id in nested_calls
                for target in result.call_targets[call_id]
            },
            {
                "fn:internal:tests/fixtures/llvm/"
                "indirect_callbacks.c:local_first"
            },
        )
        copied_calls = {
            call_id
            for call_id, call in facts.pointer_program.calls.items()
            if call.caller
            == "fn:external:exercise_copied_nested_callback"
        }
        self.assertTrue(copied_calls)
        self.assertEqual(result.unresolved_calls, copied_calls)
        if hybrid_result is not None:
            self.assertEqual(
                {
                    target
                    for call_id in copied_calls
                    for target in hybrid_result.call_targets[call_id]
                },
                {
                    "fn:internal:tests/fixtures/llvm/"
                    "indirect_callbacks.c:local_first"
                },
            )
            self.assertEqual(hybrid_result.unresolved_calls, set())


if __name__ == "__main__":
    unittest.main()

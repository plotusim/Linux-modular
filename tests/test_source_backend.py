import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from kernel_modularizer.backend import BackendPolicy, generate_module_bundle
from kernel_modularizer.candidate import prepare_candidate
from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.extraction import load_source_extraction
from kernel_modularizer.io import write_json_atomic
from kernel_modularizer.integration import (
    apply_bundle,
    rollback_transaction,
)
from kernel_modularizer.model import (
    EdgeKind,
    ExecutionContext,
    Linkage,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNode,
)
from kernel_modularizer.planner import PlannerPolicy, plan_modules
from kernel_modularizer.reporting import module_plan_to_dict


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/backend/backend_candidate.c"
SYSCALL_FIXTURE = (
    ROOT / "tests/fixtures/backend/syscall_candidate.c"
)
RESIDENT_HELPER_FIXTURE = (
    ROOT / "tests/fixtures/backend/resident_helper_candidate.c"
)
UNICODE_FIXTURE = (
    ROOT / "tests/fixtures/backend/unicode_candidate.c"
)
INLINE_EXPORT_FIXTURE = (
    ROOT / "tests/fixtures/backend/inline_export_candidate.c"
)
INLINE_PROXY_FIXTURE = (
    ROOT / "tests/fixtures/backend/inline_proxy_candidate.c"
)
SOURCE_INLINE_PROXY_FIXTURE = (
    ROOT / "tests/fixtures/backend/source_inline_proxy_candidate.c"
)
LOCAL_SUPPORT_FIXTURE = (
    ROOT / "tests/fixtures/backend/local_support_candidate.c"
)
MACRO_BOUNDARY_FIXTURE = (
    ROOT / "tests/fixtures/backend/macro_boundary_candidate.c"
)
MACRO_GROUP_FIXTURE = (
    ROOT / "tests/fixtures/backend/macro_group_candidate.c"
)
ADDRESS_TAKEN_FIXTURE = (
    ROOT / "tests/fixtures/backend/address_taken_candidate.c"
)
CONDITIONAL_GLOBAL_FIXTURE = (
    ROOT / "tests/fixtures/backend/conditional_global_candidate.c"
)
LATE_INCLUDE_FIXTURE = (
    ROOT / "tests/fixtures/backend/late_include_candidate.c"
)
IMPLICIT_RECORD_FIXTURE = (
    ROOT / "tests/fixtures/backend/implicit_record_candidate.c"
)
LLVM_ROOT = Path("/opt/llvm-custom")


def _clang_tooling_available():
    return (
        (LLVM_ROOT / "bin/clang++").is_file()
        and (LLVM_ROOT / "bin/llvm-config").is_file()
        and any((LLVM_ROOT / "lib").glob("libclang-cpp.so*"))
    )


@unittest.skipUnless(
    _clang_tooling_available(), "Clang LibTooling is not installed"
)
class SourceBackendIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.extractor = cls.root / "SourceExtractor"
        llvm_config = LLVM_ROOT / "bin/llvm-config"
        cxxflags = subprocess.check_output(
            [str(llvm_config), "--cxxflags"], text=True
        ).split()
        ldflags = subprocess.check_output(
            [
                str(llvm_config),
                "--ldflags",
                "--system-libs",
                "--libs",
            ],
            text=True,
        ).split()
        clang_cpp = sorted(
            (LLVM_ROOT / "lib").glob("libclang-cpp.so*")
        )[0]
        subprocess.run(
            [
                str(LLVM_ROOT / "bin/clang++"),
                *cxxflags,
                "-std=c++17",
                str(
                    ROOT
                    / "Backend/AutoBackend/cpp/SourceExtractor.cpp"
                ),
                "-o",
                str(cls.extractor),
                str(clang_cpp),
                f"-Wl,-rpath,{LLVM_ROOT / 'lib'}",
                *ldflags,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def extract(self, source=FIXTURE, name="extraction.json"):
        output = self.root / name
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "helper_add",
                "--function",
                "deferred_add",
                "--function",
                "deferred_reset",
                "--global",
                "deferred_bias",
                "--output",
                str(output),
                str(source),
                "--",
                "-std=c11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return output

    def test_ast_extraction_and_structured_generation(self):
        extraction_path = self.extract()
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="deferred_math",
                interfaces=("deferred_add", "deferred_reset"),
            ),
        )

        module = bundle.files[
            "kernel/linux_modularizer/deferred_math.c"
        ]
        resident = bundle.source_replacements[str(FIXTURE)]
        self.assertIn("linux_mod_impl_helper_add_", module)
        self.assertNotIn("return helper_add(value);", module)
        self.assertIn('request_module("deferred_math")', resident)
        self.assertEqual(resident.count("symbol_get("), 2)
        self.assertEqual(resident.count("symbol_put("), 2)
        self.assertEqual(resident.count("shared lazy-loader boundary"), 1)
        self.assertNotIn("try_module_get", resident)
        self.assertNotIn("synchronize_rcu", resident)
        self.assertIn("system_state < SYSTEM_RUNNING", resident)
        self.assertNotIn("kmalloc", resident)
        self.assertNotIn("kfree", resident)
        self.assertNotIn("DEFINE_SPINLOCK(", resident)
        self.assertNotIn("DEFINE_MUTEX(", resident)
        self.assertNotIn("EXPORT_SYMBOL_GPL(", resident)
        self.assertEqual(module.count("EXPORT_SYMBOL_GPL(mx"), 1)
        self.assertIn("const struct mo", module)
        self.assertIn("operations->f_", resident)
        self.assertNotIn("smp_store_release(", resident)
        self.assertNotIn(", NULL);", module)
        self.assertNotIn(
            "static backend_value_t deferred_bias = 7;", resident
        )

        output = self.root / "bundle"
        bundle.write(output)
        fake_kernel = ROOT / "tests/fixtures/fake_kernel"
        original_include = ROOT / "tests/fixtures/backend"
        generated_include = output / "include"
        resident_path = next(
            output.glob("resident/*/backend_candidate.c")
        )
        module_path = (
            output / "kernel/linux_modularizer/deferred_math.c"
        )
        for source in (resident_path, module_path):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=c11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{fake_kernel}",
                    f"-I{generated_include}",
                    f"-I{original_include}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_global_range_crosses_inactive_preprocessor_branch(self):
        extraction_path = self.root / "conditional-global.json"
        subprocess.run(
            [
                str(self.extractor),
                "--global",
                "conditional_text",
                "--output",
                str(extraction_path),
                str(CONDITIONAL_GLOBAL_FIXTURE),
                "--",
                "-std=c11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        self.assertEqual(len(extraction.globals), 1)
        source = extraction.globals[0].source
        self.assertIn("inactive; text", source)
        self.assertTrue(source.rstrip().endswith(";"))

    def test_tracepoint_dependency_uses_static_call_aware_export(self):
        extraction = load_source_extraction(
            self.extract(name="tracepoint-export-extraction.json")
        )
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="tracepoint_deferred",
                interfaces=("deferred_add",),
                external_resident_exports=(
                    "__traceiter_fixture_event",
                    "__tracepoint_fixture_event",
                    "task_work_run",
                ),
            ),
        )
        resident = bundle.source_replacements[str(FIXTURE)]
        self.assertIn("#include <linux/tracepoint.h>", resident)
        self.assertIn(
            "EXPORT_TRACEPOINT_SYMBOL_GPL(fixture_event);", resident
        )
        self.assertNotIn(
            "EXPORT_SYMBOL_GPL(__traceiter_fixture_event);", resident
        )
        self.assertNotIn(
            "EXPORT_SYMBOL_GPL(__tracepoint_fixture_event);", resident
        )
        self.assertIn("EXPORT_SYMBOL_GPL(task_work_run);", resident)
        self.assertEqual(
            bundle.manifest["tracepoint_resident_exports"],
            ["fixture_event"],
        )
        self.assertEqual(
            bundle.manifest["direct_external_resident_exports"],
            ["task_work_run"],
        )
        self.assertEqual(
            bundle.manifest["resident_dependency_export_count"], 5
        )

    def test_header_inline_call_can_use_resident_dependency_proxy(self):
        extraction_path = self.root / "inline-proxy-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "deferred_traced",
                "--output",
                str(extraction_path),
                str(INLINE_PROXY_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="inline_proxy_deferred",
                interfaces=("deferred_traced",),
                resident_inline_proxies=("trace_fixture_proxy",),
                pack_resident_dependencies=True,
                bind_packed_dependencies_on_first_use=True,
            ),
        )

        module = bundle.files[
            "kernel/linux_modularizer/inline_proxy_deferred.c"
        ]
        resident = bundle.source_replacements[str(INLINE_PROXY_FIXTURE)]
        self.assertIn("->trace_fixture_proxy(value)", module)
        self.assertNotIn("\n\ttrace_fixture_proxy(value);", module)
        self.assertIn(
            "dependencies->trace_fixture_proxy = trace_fixture_proxy;",
            resident,
        )
        self.assertIn("->b(mf", resident)
        self.assertIn("static void mf", resident)
        self.assertIn("static DEFINE_MUTEX(ml", module)
        self.assertIn("\t.b = mb", module)
        self.assertNotIn(
            "EXPORT_SYMBOL_GPL(trace_fixture_proxy);", resident
        )
        self.assertNotIn("EXPORT_SYMBOL_GPL(mf", resident)
        self.assertEqual(
            bundle.manifest["resident_inline_proxies"],
            ["trace_fixture_proxy"],
        )
        self.assertEqual(
            bundle.manifest["resident_dependency_export_count"], 0
        )

        output = self.root / "inline-proxy-bundle"
        bundle.write(output)
        for source in (
            next(output.glob("resident/*/inline_proxy_candidate.c")),
            output
            / "kernel/linux_modularizer/inline_proxy_deferred.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_source_local_inline_proxy_remains_resident(self):
        extraction_path = self.root / "source-inline-proxy-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--all-main-functions",
                "--output",
                str(extraction_path),
                str(SOURCE_INLINE_PROXY_FIXTURE),
                "--",
                "-std=gnu11",
                f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="source_proxy_deferred",
                interfaces=("source_proxy_deferred",),
                resident_inline_proxies=("source_local_proxy",),
                pack_resident_dependencies=True,
                bind_packed_dependencies_on_first_use=True,
            ),
        )

        module = bundle.files[
            "kernel/linux_modularizer/source_proxy_deferred.c"
        ]
        resident = bundle.source_replacements[
            str(SOURCE_INLINE_PROXY_FIXTURE)
        ]
        self.assertIn(
            "static inline void source_local_proxy(int value)", resident
        )
        self.assertNotIn(
            "source_local_proxy moved to source_proxy_deferred.ko", resident
        )
        self.assertNotIn("linux_mod_impl_source_local_proxy", module)
        self.assertIn("void (*source_local_proxy)(int);", module)
        self.assertIn("->source_local_proxy(value)", module)

        output = self.root / "source-inline-proxy-bundle"
        bundle.write(output)
        for source in (
            next(output.glob("resident/*/source_inline_proxy_candidate.c")),
            output
            / "kernel/linux_modularizer/source_proxy_deferred.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_macro_argument_and_macro_generated_resident_global(self):
        extraction_path = self.root / "macro-boundary-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "macro_helper",
                "--function",
                "deferred_macro",
                "--global",
                "persistent_state",
                "--output",
                str(extraction_path),
                str(MACRO_BOUNDARY_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        state = next(
            item
            for item in extraction.globals
            if item.symbol == "persistent_state"
        )
        self.assertIn("DEFINE_PRIVATE_STATE(persistent_state)", state.source)
        deferred = next(
            item
            for item in extraction.functions
            if item.symbol == "deferred_macro"
        )
        helper_references = [
            reference
            for reference in deferred.references
            if reference.symbol == "macro_helper"
        ]
        self.assertTrue(helper_references)
        for reference in helper_references:
            source = Path(deferred.source_path).read_text(encoding="utf-8")
            self.assertEqual(
                source[
                    reference.offset : reference.offset + reference.length
                ],
                "macro_helper",
            )

        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="deferred_macro",
                interfaces=("deferred_macro",),
                external_resident_exports=("persistent_state",),
                pack_resident_dependencies=True,
            ),
        )
        module = bundle.files[
            "kernel/linux_modularizer/deferred_macro.c"
        ]
        resident = bundle.source_replacements[str(MACRO_BOUNDARY_FIXTURE)]
        self.assertIn("backend_value_t *persistent_state;", module)
        self.assertIn("linux_mod_impl_macro_helper_", module)
        self.assertNotIn("CALL_THROUGH_MACRO(macro_helper(value))", module)
        self.assertIn("static DEFINE_PRIVATE_STATE(persistent_state);", resident)
        self.assertIn("__ro_after_init", resident)
        self.assertEqual(
            bundle.manifest["resident_globals"],
            ["persistent_state"],
        )

        output = self.root / "macro-boundary-bundle"
        bundle.write(output)
        for source in (
            next(output.glob("resident/*/macro_boundary_candidate.c")),
            output
            / "kernel/linux_modularizer/deferred_macro.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_syscall_macro_interface_becomes_an_ordinary_module_function(self):
        extraction_path = self.root / "syscall-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "__se_sys_optional_call",
                "--output",
                str(extraction_path),
                str(SYSCALL_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        function = extraction.functions[0]
        self.assertEqual(
            function.symbol,
            "__se_sys_optional_call",
        )
        self.assertEqual(function.source_form, "syscall_define")
        self.assertEqual(function.name_spelling, "optional_call")

        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="optional_syscall",
                interfaces=("__se_sys_optional_call",),
            ),
        )
        module = bundle.files[
            "kernel/linux_modularizer/optional_syscall.c"
        ]
        resident = bundle.source_replacements[str(SYSCALL_FIXTURE)]
        self.assertNotIn("SYSCALL_DEFINE2(", module)
        self.assertIn(
            "static long linux_mod_impl___se_sys_optional_call_",
            module,
        )
        self.assertIn("SYSCALL_DEFINE2(optional_call", resident)
        self.assertIn('request_module("optional_syscall")', resident)

        output = self.root / "syscall-bundle"
        bundle.write(output)
        for source in (
            next(output.glob("resident/*/syscall_candidate.c")),
            output
            / "kernel/linux_modularizer/optional_syscall.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_compat_syscall_macro_uses_user_written_body(self):
        extraction_path = self.root / "compat-syscall-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "__se_compat_sys_compat_optional_call",
                "--output",
                str(extraction_path),
                str(SYSCALL_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        function = extraction.functions[0]
        self.assertEqual(
            function.symbol,
            "__se_compat_sys_compat_optional_call",
        )
        self.assertEqual(function.source_form, "syscall_define")
        self.assertEqual(function.name_spelling, "compat_optional_call")

        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="optional_compat_syscall",
                interfaces=(
                    "__se_compat_sys_compat_optional_call",
                ),
            ),
        )
        module = bundle.files[
            "kernel/linux_modularizer/optional_compat_syscall.c"
        ]
        resident = bundle.source_replacements[str(SYSCALL_FIXTURE)]
        self.assertNotIn("COMPAT_SYSCALL_DEFINE1(", module)
        self.assertIn(
            "linux_mod_impl___se_compat_sys_compat_optio_",
            module,
        )
        self.assertIn(
            "COMPAT_SYSCALL_DEFINE1(compat_optional_call", resident
        )

    def test_unsafe_static_helper_is_allowed_only_through_packed_table(self):
        extraction_path = self.root / "packed-inline-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "__se_compat_sys_compat_optional_call",
                "--function",
                "resident_inline",
                "--output",
                str(extraction_path),
                str(SYSCALL_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        with self.assertRaisesRegex(
            GraphValidationError, "non-exportable marker"
        ):
            generate_module_bundle(
                extraction,
                BackendPolicy(
                    module_name="direct_inline",
                    interfaces=(
                        "__se_compat_sys_compat_optional_call",
                    ),
                    resident_exports=("resident_inline",),
                ),
            )

        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="packed_inline",
                interfaces=(
                    "__se_compat_sys_compat_optional_call",
                ),
                resident_exports=("resident_inline",),
                pack_resident_dependencies=True,
            ),
        )
        module = bundle.files[
            "kernel/linux_modularizer/packed_inline.c"
        ]
        resident = bundle.source_replacements[str(SYSCALL_FIXTURE)]
        self.assertIn("(*resident_inline)(unsigned int);", module)
        self.assertIn("->resident_inline(value)", module)
        self.assertIn(
            "long resident_inline(unsigned int value)", resident
        )
        self.assertNotIn("EXPORT_SYMBOL_GPL(resident_inline)", resident)

    def test_explicit_external_resident_exports_are_manifested(self):
        extraction = load_source_extraction(self.extract())
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="deferred_math",
                interfaces=("deferred_add", "deferred_reset"),
                external_resident_exports=("resident_entry",),
            ),
        )

        resident = bundle.source_replacements[str(FIXTURE)]
        self.assertIn(
            "EXPORT_SYMBOL_GPL(resident_entry);",
            resident,
        )
        self.assertEqual(
            bundle.manifest["external_resident_exports"],
            ["resident_entry"],
        )

    def test_external_dependencies_can_share_one_typed_export(self):
        extraction_path = self.root / "packed-dependency-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "__se_sys_optional_call",
                "--output",
                str(extraction_path),
                str(SYSCALL_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="optional_syscall_packed",
                interfaces=("__se_sys_optional_call",),
                external_resident_exports=("fixture_dependency",),
                pack_resident_dependencies=True,
            ),
        )

        module = bundle.files[
            "kernel/linux_modularizer/optional_syscall_packed.c"
        ]
        resident = bundle.source_replacements[str(SYSCALL_FIXTURE)]
        self.assertIn("packed resident dependency ABI", module)
        self.assertIn("->fixture_dependency(value)", module)
        self.assertIn("#define mp", module)
        self.assertNotIn("static const struct mdt", module)
        self.assertNotIn(
            "EXPORT_SYMBOL_GPL(fixture_dependency);", resident
        )
        self.assertEqual(resident.count("EXPORT_SYMBOL_GPL(md"), 1)
        self.assertTrue(
            bundle.manifest["pack_resident_dependencies"]
        )
        self.assertEqual(
            bundle.manifest["resident_dependency_export_count"], 1
        )

        output = self.root / "packed-dependency-bundle"
        bundle.write(output)
        for source in (
            next(output.glob("resident/*/syscall_candidate.c")),
            output
            / "kernel/linux_modularizer/optional_syscall_packed.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_packed_dependency_must_have_an_exact_ast_reference(self):
        extraction = load_source_extraction(self.extract())
        with self.assertRaisesRegex(
            GraphValidationError, "no movable AST reference"
        ):
            generate_module_bundle(
                extraction,
                BackendPolicy(
                    module_name="deferred_math",
                    interfaces=("deferred_add", "deferred_reset"),
                    external_resident_exports=("fixture_dependency",),
                    pack_resident_dependencies=True,
                ),
            )

    def test_source_local_resident_helper_can_join_dependency_pack(self):
        extraction_path = self.root / "packed-resident-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "shared_resident_helper",
                "--function",
                "deferred_add",
                "--output",
                str(extraction_path),
                str(RESIDENT_HELPER_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="deferred_math_packed",
                interfaces=("deferred_add",),
                resident_exports=("shared_resident_helper",),
                pack_resident_dependencies=True,
            ),
        )

        module = bundle.files[
            "kernel/linux_modularizer/deferred_math_packed.c"
        ]
        resident = bundle.source_replacements[
            str(RESIDENT_HELPER_FIXTURE)
        ]
        self.assertIn("->shared_resident_helper(value)", module)
        self.assertIn(
            "static backend_value_t shared_resident_helper", resident
        )
        self.assertNotIn(
            "EXPORT_SYMBOL_GPL(shared_resident_helper);", resident
        )
        self.assertEqual(resident.count("EXPORT_SYMBOL_GPL(md"), 1)
        self.assertEqual(
            bundle.manifest["packed_resident_exports"],
            ["shared_resident_helper"],
        )
        self.assertEqual(
            bundle.manifest["resident_dependency_export_count"], 1
        )

        output = self.root / "packed-resident-bundle"
        bundle.write(output)
        for source in (
            next(
                output.glob(
                    "resident/*/resident_helper_candidate.c"
                )
            ),
            output / "kernel/linux_modularizer/deferred_math_packed.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_source_local_types_and_macros_follow_moved_closure(self):
        extraction_path = self.root / "local-support-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "shared_local",
                "--function",
                "deferred_local",
                "--global",
                "local_ro_bias",
                "--output",
                str(extraction_path),
                str(LOCAL_SUPPORT_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        self.assertGreaterEqual(len(extraction.declarations), 3)
        self.assertGreaterEqual(len(extraction.macro_definitions), 2)
        shared = next(
            item
            for item in extraction.functions
            if item.symbol == "shared_local"
        )
        self.assertEqual(len(shared.prior_declarations), 1)
        self.assertEqual(
            shared.prior_declarations[0].source.strip(),
            "static backend_value_t shared_local(struct local_args *args);",
        )
        deferred = next(
            item
            for item in extraction.functions
            if item.symbol == "deferred_local"
        )
        self.assertIn("local_actions", deferred.source)
        self.assertNotIn("local_actions", deferred.dependencies.globals)
        self.assertEqual(deferred.dependencies.address_taken_globals, ())
        self.assertEqual(
            {item.symbol for item in extraction.globals},
            {"local_ro_bias"},
        )

        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="local_support_lazy",
                interfaces=("deferred_local",),
                resident_exports=("shared_local",),
                pack_resident_dependencies=True,
                initialize_packed_dependencies_at_load=True,
                resident_ro_after_init_globals=("local_ro_bias",),
                autoload_alias="lm_local",
            ),
        )
        module = bundle.files[
            "kernel/linux_modularizer/local_support_lazy.c"
        ]
        resident = bundle.source_replacements[
            str(LOCAL_SUPPORT_FIXTURE)
        ]
        self.assertIn("typedef struct", module)
        self.assertIn("} local_args_template;", resident)
        self.assertIn("enum local_mode mode;\n};", module)
        self.assertLess(
            module.index("struct local_args;"),
            module.index("typedef backend_value_t (local_apply_fn)"),
        )
        self.assertIn("enum local_mode", module)
        self.assertIn("struct local_args", module)
        self.assertIn("#define LOCAL_BASE 3", module)
        self.assertIn("#define LOCAL_TOTAL(value)", module)
        self.assertIn('MODULE_ALIAS("lm_local")', module)
        self.assertIn('request_module("lm_local")', resident)
        self.assertIn("local_ro_bias __ro_after_init", resident)
        self.assertIn("static struct mdt", module)
        self.assertIn("void mf", resident)
        self.assertNotIn("const struct mdt", resident)
        self.assertGreater(
            resident.index("struct mdt"),
            resident.index("enum local_mode"),
        )
        self.assertEqual(
            {
                item["name"]
                for item in bundle.manifest["source_local_support"][
                    "macros"
                ]
            },
            {"LOCAL_BASE", "LOCAL_TOTAL"},
        )

        output = self.root / "local-support-bundle"
        bundle.write(output)
        for source in (
            next(
                output.glob(
                    "resident/*/local_support_candidate.c"
                )
            ),
            output
            / "kernel/linux_modularizer/local_support_lazy.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_member_access_carries_implicit_private_record_definition(self):
        extraction_path = self.root / "implicit-record-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "deferred_implicit_record",
                "--output",
                str(extraction_path),
                str(IMPLICIT_RECORD_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        deferred = extraction.functions[0]
        self.assertNotIn("struct implicit_record", deferred.source)
        self.assertEqual(
            deferred.dependencies.declarations,
            ("implicit_record",),
        )

        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="implicit_record_lazy",
                interfaces=("deferred_implicit_record",),
            ),
        )
        module = bundle.files[
            "kernel/linux_modularizer/implicit_record_lazy.c"
        ]
        self.assertIn("struct implicit_record {", module)
        self.assertLess(
            module.index("struct implicit_record {"),
            module.index("linux_mod_impl_deferred_implicit_record"),
        )

        output = self.root / "implicit-record-bundle"
        bundle.write(output)
        for source in (
            next(output.glob("resident/*/implicit_record_candidate.c")),
            output / "kernel/linux_modularizer/implicit_record_lazy.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_generated_header_snapshot_and_pr_fmt_hoisting(self):
        kernel = self.root / "generated-header-kernel"
        source_dir = kernel / "subsystem"
        build_dir = self.root / "generated-header-build" / "subsystem"
        source_dir.mkdir(parents=True, exist_ok=True)
        build_dir.mkdir(parents=True, exist_ok=True)
        source = source_dir / "candidate.c"
        source_header = source_dir / "source_local.h"
        generated_header = build_dir / "generated_value.h"
        generated_nested_header = build_dir / "generated_extra.h"
        source.write_text(
            '#define pr_fmt(fmt) "generated: " fmt\n'
            '#include "source_local.h"\n'
            '#include "generated_value.h"\n'
            "static const char *deferred_generated(void)\n"
            "{\n"
            "\treturn pr_fmt(GENERATED_TEXT GENERATED_EXTRA);\n"
            "}\n",
            encoding="utf-8",
        )
        source_header.write_text(
            '#include "generated_extra.h"\n', encoding="utf-8"
        )
        generated_header.write_text(
            '#define GENERATED_TEXT "value"\n', encoding="utf-8"
        )
        generated_nested_header.write_text(
            '#define GENERATED_EXTRA "-extra"\n', encoding="utf-8"
        )
        extraction_path = self.root / "generated-header-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "deferred_generated",
                "--output",
                str(extraction_path),
                str(source),
                "--",
                "-std=gnu11",
                f"-I{build_dir}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="generated_header_lazy",
                interfaces=("deferred_generated",),
                kernel_root=str(kernel),
            ),
        )
        module_path = (
            "kernel/linux_modularizer/generated_header_lazy.c"
        )
        module = bundle.files[module_path]
        snapshotted = bundle.manifest[
            "snapshotted_generated_headers"
        ]
        self.assertEqual(len(snapshotted), 2)
        snapshots_by_name = {
            Path(record["bundle_path"]).name: record["bundle_path"]
            for record in snapshotted
        }
        snapshot_path = snapshots_by_name["generated_value.h"]
        self.assertEqual(
            bundle.files[snapshot_path], '#define GENERATED_TEXT "value"\n'
        )
        nested_snapshot_path = snapshots_by_name["generated_extra.h"]
        self.assertEqual(
            bundle.files[nested_snapshot_path],
            '#define GENERATED_EXTRA "-extra"\n',
        )
        self.assertIn(
            "#include <"
            + snapshot_path.removeprefix("include/")
            + ">",
            module,
        )
        self.assertNotIn(str(build_dir), module)
        self.assertNotIn('#include "generated_value.h"', module)
        self.assertLess(
            module.index("#define pr_fmt"), module.index("#include")
        )
        self.assertIn(
            "CFLAGS_generated_header_lazy.o += "
            "-I$(srctree)/include/linux/linux_modularizer/generated/"
            "generated_header_lazy",
            bundle.files["integration/Makefile.fragment"],
        )

        output = self.root / "generated-header-bundle"
        bundle.write(output)
        integrated_module = kernel / module_path
        integrated_module.parent.mkdir(parents=True, exist_ok=True)
        integrated_module.write_text(module, encoding="utf-8")
        subprocess.run(
            [
                str(LLVM_ROOT / "bin/clang"),
                "-std=gnu11",
                "-fsyntax-only",
                "-Werror",
                f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                f"-I{output / 'include'}",
                "-I"
                + str(
                    output
                    / "include/linux/linux_modularizer/generated/"
                    "generated_header_lazy"
                ),
                str(integrated_module),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_moved_helper_removes_obsolete_prior_declaration(self):
        extraction_path = self.root / "prior-declaration-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "shared_local",
                "--function",
                "deferred_local",
                "--global",
                "local_ro_bias",
                "--output",
                str(extraction_path),
                str(LOCAL_SUPPORT_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="local_prior_lazy",
                interfaces=("deferred_local",),
            ),
        )
        module = bundle.files[
            "kernel/linux_modularizer/local_prior_lazy.c"
        ]
        resident = bundle.source_replacements[str(LOCAL_SUPPORT_FIXTURE)]
        prototype = (
            "static backend_value_t shared_local(struct local_args *args);"
        )
        self.assertNotIn(prototype, resident)
        self.assertNotIn(prototype, module)
        self.assertIn(
            "obsolete declaration for shared_local", resident
        )
        self.assertIn("local_actions", module)
        self.assertNotIn("local_actions", resident)

        output = self.root / "prior-declaration-bundle"
        bundle.write(output)
        for source in (
            next(
                output.glob("resident/*/local_support_candidate.c")
            ),
            output / "kernel/linux_modularizer/local_prior_lazy.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_macro_declaration_group_and_local_static_are_classified(self):
        extraction_path = self.root / "macro-group-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "deferred_macro_group",
                "--global",
                "format_attr_type",
                "--global",
                "format_shadow_type",
                "--global",
                "repeated_state",
                "--global",
                "repeated_state_ptr",
                "--output",
                str(extraction_path),
                str(MACRO_GROUP_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        globals_by_name = {
            item.symbol: item for item in extraction.globals
        }
        self.assertEqual(
            set(globals_by_name),
            {
                "format_attr_type",
                "format_shadow_type",
                "repeated_state",
                "repeated_state_ptr",
            },
        )
        self.assertEqual(
            {item.source_form for item in globals_by_name.values()},
            {"macro_declaration_group"},
        )
        self.assertEqual(
            globals_by_name["format_attr_type"].name_spelling,
            "type",
        )
        self.assertEqual(
            globals_by_name["format_shadow_type"].name_spelling,
            "type",
        )
        self.assertEqual(
            globals_by_name["repeated_state_ptr"].name_spelling,
            "repeated_state",
        )
        self.assertNotIn(
            "repeated_state",
            globals_by_name["repeated_state_ptr"].dependencies.globals,
        )
        self.assertNotIn(
            "repeated_state",
            globals_by_name[
                "repeated_state_ptr"
            ].dependencies.address_taken_globals,
        )
        function = extraction.functions[0]
        self.assertIn("local_actions", function.source)
        self.assertNotIn("local_actions", function.dependencies.globals)

        with self.assertRaisesRegex(
            GraphValidationError,
            "macro-generated declaration groups must remain resident",
        ):
            generate_module_bundle(
                extraction,
                BackendPolicy(
                    module_name="unsafe_macro_group",
                    interfaces=("deferred_macro_group",),
                ),
            )

    def test_global_unary_address_use_is_emitted_as_lifetime_fact(self):
        extraction_path = self.root / "address-taken-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "deferred_address",
                "--global",
                "published_state",
                "--output",
                str(extraction_path),
                str(ADDRESS_TAKEN_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        function = extraction.functions[0]
        self.assertEqual(
            function.dependencies.address_taken_globals,
            ("published_state",),
        )
        self.assertIn("published_state", function.dependencies.globals)

    def test_source_local_type_in_public_lazy_abi_is_rejected(self):
        extraction_path = self.root / "local-abi-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "shared_local",
                "--function",
                "deferred_local",
                "--output",
                str(extraction_path),
                str(LOCAL_SUPPORT_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        with self.assertRaisesRegex(
            GraphValidationError,
            "lazy interface ABI uses source-local type",
        ):
            generate_module_bundle(
                extraction,
                BackendPolicy(
                    module_name="local_abi_lazy",
                    interfaces=("shared_local",),
                ),
            )

    def test_packed_resident_initializer_uses_typed_trampoline(self):
        extraction_path = self.root / "packed-initializer-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "shared_resident_helper",
                "--function",
                "deferred_via_callback",
                "--global",
                "deferred_callback",
                "--output",
                str(extraction_path),
                str(RESIDENT_HELPER_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="deferred_initializer_packed",
                interfaces=("deferred_via_callback",),
                resident_exports=("shared_resident_helper",),
                pack_resident_dependencies=True,
            ),
        )

        module = bundle.files[
            "kernel/linux_modularizer/deferred_initializer_packed.c"
        ]
        self.assertIn("linux_mod_dep_", module)
        self.assertNotIn(
            "= mp", module
        )
        self.assertEqual(
            bundle.manifest["packed_initializer_trampolines"],
            ["shared_resident_helper"],
        )

        output = self.root / "packed-initializer-bundle"
        bundle.write(output)
        for source in (
            next(
                output.glob(
                    "resident/*/resident_helper_candidate.c"
                )
            ),
            output
            / "kernel/linux_modularizer/deferred_initializer_packed.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_hidden_dependency_can_be_forced_to_direct_export(self):
        extraction_path = self.root / "forced-direct-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "shared_resident_helper",
                "--function",
                "deferred_add",
                "--output",
                str(extraction_path),
                str(RESIDENT_HELPER_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        bundle = generate_module_bundle(
            load_source_extraction(extraction_path),
            BackendPolicy(
                module_name="forced_direct_packed",
                interfaces=("deferred_add",),
                resident_exports=("shared_resident_helper",),
                external_resident_exports=("fixture_dependency",),
                pack_resident_dependencies=True,
                direct_resident_dependencies=("fixture_dependency",),
            ),
        )

        module = bundle.files[
            "kernel/linux_modularizer/forced_direct_packed.c"
        ]
        resident = bundle.source_replacements[
            str(RESIDENT_HELPER_FIXTURE)
        ]
        self.assertIn("->shared_resident_helper(value)", module)
        self.assertIn("fixture_dependency(value)", module)
        self.assertIn(
            "EXPORT_SYMBOL_GPL(fixture_dependency);", resident
        )
        self.assertEqual(
            bundle.manifest[
                "forced_direct_resident_dependencies"
            ],
            ["fixture_dependency"],
        )
        self.assertEqual(
            bundle.manifest["resident_dependency_export_count"], 2
        )

    def test_static_shared_helper_can_remain_resident_and_be_exported(self):
        extraction_path = self.root / "resident-helper-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "shared_resident_helper",
                "--function",
                "deferred_add",
                "--output",
                str(extraction_path),
                str(RESIDENT_HELPER_FIXTURE),
                "--",
                "-std=c11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="deferred_math",
                interfaces=("deferred_add",),
                resident_exports=("shared_resident_helper",),
            ),
        )

        module = bundle.files[
            "kernel/linux_modularizer/deferred_math.c"
        ]
        resident = bundle.source_replacements[
            str(RESIDENT_HELPER_FIXTURE)
        ]
        self.assertIn(
            "backend_value_t shared_resident_helper"
            "(backend_value_t value);",
            module,
        )
        self.assertNotIn(
            "static backend_value_t shared_resident_helper",
            resident,
        )
        self.assertIn(
            "EXPORT_SYMBOL_GPL(shared_resident_helper);",
            resident,
        )

        output = self.root / "resident-helper-bundle"
        bundle.write(output)
        for source in (
            next(
                output.glob(
                    "resident/*/resident_helper_candidate.c"
                )
            ),
            output / "kernel/linux_modularizer/deferred_math.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=c11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_static_helper_can_be_duplicated_without_resident_edit(self):
        extraction_path = self.root / "duplicate-helper-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "shared_resident_helper",
                "--function",
                "deferred_add",
                "--output",
                str(extraction_path),
                str(RESIDENT_HELPER_FIXTURE),
                "--",
                "-std=c11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="deferred_math",
                interfaces=("deferred_add",),
                duplicate_functions=("shared_resident_helper",),
            ),
        )

        module = bundle.files[
            "kernel/linux_modularizer/deferred_math.c"
        ]
        resident = bundle.source_replacements[
            str(RESIDENT_HELPER_FIXTURE)
        ]
        self.assertIn(
            "linux_mod_impl_shared_resident_helper_", module
        )
        self.assertIn(
            "static backend_value_t shared_resident_helper", resident
        )
        self.assertNotIn(
            "EXPORT_SYMBOL_GPL(shared_resident_helper);", resident
        )
        self.assertEqual(
            bundle.manifest["duplicate_functions"],
            ["shared_resident_helper"],
        )

        output = self.root / "duplicate-helper-bundle"
        bundle.write(output)
        for source in (
            next(
                output.glob(
                    "resident/*/resident_helper_candidate.c"
                )
            ),
            output / "kernel/linux_modularizer/deferred_math.c",
        ):
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=c11",
                    "-fsyntax-only",
                    "-Werror",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_static_inline_notrace_helper_can_move(self):
        extraction_path = self.root / "inline-notrace-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "traced_inline_helper",
                "--function",
                "deferred_inline_call",
                "--output",
                str(extraction_path),
                str(INLINE_EXPORT_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="inline_notrace",
                interfaces=("deferred_inline_call",),
            ),
        )
        module = bundle.files[
            "kernel/linux_modularizer/inline_notrace.c"
        ]
        self.assertIn("static inline notrace", module)
        self.assertIn("linux_mod_impl_traced_inline_helper_", module)

    def test_preexisting_resident_export_is_reused(self):
        extraction_path = self.root / "preexisting-export-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "exported_resident_helper",
                "--function",
                "deferred_inline_call",
                "--output",
                str(extraction_path),
                str(INLINE_EXPORT_FIXTURE),
                "--",
                "-std=gnu11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="reuse_export",
                interfaces=("deferred_inline_call",),
                resident_exports=("exported_resident_helper",),
                pack_resident_dependencies=True,
            ),
        )
        resident = bundle.source_replacements[str(INLINE_EXPORT_FIXTURE)]
        self.assertEqual(
            resident.count("EXPORT_SYMBOL_GPL(exported_resident_helper)"),
            1,
        )
        self.assertEqual(
            bundle.manifest["preexisting_resident_exports"],
            ["exported_resident_helper"],
        )

    def test_utf8_byte_offsets_are_normalized_before_source_edits(self):
        extraction_path = self.root / "unicode-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "unicode_helper",
                "--function",
                "unicode_deferred",
                "--output",
                str(extraction_path),
                str(UNICODE_FIXTURE),
                "--",
                "-std=c11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        raw = json.loads(extraction_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["offset_encoding"], "utf-8-bytes")

        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="unicode_deferred",
                interfaces=("unicode_deferred",),
                module_defines={
                    "UNICODE_LIMIT": "9",
                    "FIXTURE_ARCH(value)": "0",
                },
            ),
        )
        resident = bundle.source_replacements[str(UNICODE_FIXTURE)]
        module = bundle.files[
            "kernel/linux_modularizer/unicode_deferred.c"
        ]
        self.assertIn("非 ASCII", resident)
        self.assertIn("linux-modular: unicode_helper moved", resident)
        self.assertIn(
            "linux_mod_impl_unicode_helper_",
            module,
        )
        self.assertIn("#define UNICODE_LIMIT 9", module)
        self.assertIn("#ifndef FIXTURE_ARCH", module)
        self.assertIn("#define FIXTURE_ARCH(value) 0", module)
        self.assertEqual(
            bundle.manifest["module_defines"],
            {
                "FIXTURE_ARCH(value)": "0",
                "UNICODE_LIMIT": "9",
            },
        )

    def test_all_main_discovery_captures_translation_unit_closure(self):
        extraction_path = self.root / "all-main-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--all-main-functions",
                "--all-main-globals",
                "--output",
                str(extraction_path),
                str(FIXTURE),
                "--",
                "-std=c11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        raw = json.loads(extraction_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["offset_encoding"], "utf-8-bytes")
        extraction = load_source_extraction(extraction_path)
        self.assertEqual(
            {function.symbol for function in extraction.functions},
            {
                "helper_add",
                "deferred_add",
                "deferred_reset",
                "collision_interface",
                "resident_entry",
            },
        )
        self.assertEqual(
            {global_.symbol for global_ in extraction.globals},
            {"deferred_bias"},
        )

    def test_runtime_preamble_keeps_original_late_include_offsets(self):
        extraction_path = self.root / "late-include-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--all-main-functions",
                "--all-main-globals",
                "--output",
                str(extraction_path),
                str(LATE_INCLUDE_FIXTURE),
                "--",
                "-std=gnu11",
                "-DCONFIG_LATE_INCLUDE_FIXTURE",
                f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="late_include_lazy",
                interfaces=("late_include_deferred",),
            ),
        )
        resident = bundle.source_replacements[str(LATE_INCLUDE_FIXTURE)]
        self.assertLess(
            resident.index('#include "backend_api.h"'),
            resident.index("#include <linux/cache.h>"),
        )
        self.assertLess(
            resident.index("#include <linux/cache.h>"),
            resident.index(
                "/* linux-modular interface: late_include_deferred */"
            ),
        )

        output = self.root / "late-include-bundle"
        bundle.write(output)
        sources = (
            next(output.glob("resident/*/late_include_candidate.c")),
            output / "kernel/linux_modularizer/late_include_lazy.c",
        )
        for source in sources:
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-std=gnu11",
                    "-fsyntax-only",
                    "-Werror",
                    "-DCONFIG_LATE_INCLUDE_FIXTURE",
                    f"-I{ROOT / 'tests/fixtures/fake_kernel'}",
                    f"-I{output / 'include'}",
                    f"-I{ROOT / 'tests/fixtures/backend'}",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_wrapper_locals_do_not_shadow_interface_parameters(self):
        extraction_path = self.root / "collision-extraction.json"
        subprocess.run(
            [
                str(self.extractor),
                "--function",
                "collision_interface",
                "--output",
                str(extraction_path),
                str(FIXTURE),
                "--",
                "-std=c11",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        extraction = load_source_extraction(extraction_path)
        bundle = generate_module_bundle(
            extraction,
            BackendPolicy(
                module_name="collision_lazy",
                interfaces=("collision_interface",),
            ),
        )
        resident = bundle.source_replacements[str(FIXTURE)]
        self.assertIn("linux_modular_result_", resident)
        self.assertIn("linux_modular_operations_", resident)
        self.assertNotIn(
            "backend_value_t linux_modular_result;\n",
            resident,
        )

    def test_module_defines_reject_preprocessor_injection(self):
        extraction = load_source_extraction(self.extract())
        with self.assertRaisesRegex(
            GraphValidationError, "single-line constant expressions"
        ):
            generate_module_bundle(
                extraction,
                BackendPolicy(
                    module_name="deferred_math",
                    interfaces=("deferred_add",),
                    module_defines={
                        "LIMIT": "9\n#include <linux/unsafe.h>"
                    },
                ),
            )

    def test_changed_source_is_rejected(self):
        extraction_path = self.extract()
        payload = json.loads(extraction_path.read_text(encoding="utf-8"))
        payload["functions"][0]["source"] += " "
        extraction_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(
            GraphValidationError, "source changed"
        ):
            load_source_extraction(extraction_path)

    def test_aggregate_return_requires_explicit_failure(self):
        extraction_path = self.extract()
        payload = json.loads(extraction_path.read_text(encoding="utf-8"))
        target = next(
            item
            for item in payload["functions"]
            if item["symbol"] == "deferred_add"
        )
        target["return_category"] = "record"
        extraction_path.write_text(json.dumps(payload), encoding="utf-8")
        extraction = load_source_extraction(extraction_path)

        with self.assertRaisesRegex(
            GraphValidationError, "explicit failure expression"
        ):
            generate_module_bundle(
                extraction,
                BackendPolicy(
                    module_name="deferred_math",
                    interfaces=("deferred_add",),
                ),
            )

    def test_special_section_function_is_rejected(self):
        extraction_path = self.extract()
        payload = json.loads(extraction_path.read_text(encoding="utf-8"))
        target = next(
            item
            for item in payload["functions"]
            if item["symbol"] == "deferred_add"
        )
        target["attributes"].append("section")
        extraction_path.write_text(json.dumps(payload), encoding="utf-8")
        extraction = load_source_extraction(extraction_path)

        with self.assertRaisesRegex(
            GraphValidationError, "non-movable marker"
        ):
            generate_module_bundle(
                extraction,
                BackendPolicy(
                    module_name="deferred_math",
                    interfaces=("deferred_add",),
                ),
            )

    def test_transactional_kernel_tree_integration_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            kernel = Path(directory) / "linux"
            (kernel / "kernel").mkdir(parents=True)
            (kernel / "Makefile").write_text(
                "VERSION = 5\n", encoding="utf-8"
            )
            (kernel / "Kconfig").write_text(
                'mainmenu "fixture"\n', encoding="utf-8"
            )
            (kernel / "kernel/Makefile").write_text(
                "obj-y += core.o\n", encoding="utf-8"
            )
            source_dir = kernel / "drivers/fixture"
            source_dir.mkdir(parents=True)
            source = source_dir / FIXTURE.name
            header = source_dir / "backend_api.h"
            shutil.copyfile(FIXTURE, source)
            shutil.copyfile(FIXTURE.with_name("backend_api.h"), header)
            original = source.read_text(encoding="utf-8")

            extraction_path = self.extract(
                source, "kernel-extraction.json"
            )
            extraction = load_source_extraction(extraction_path)
            bundle = generate_module_bundle(
                extraction,
                BackendPolicy(
                    module_name="deferred_math",
                    interfaces=("deferred_add", "deferred_reset"),
                    kernel_root=str(kernel),
                ),
            )
            bundle_root = Path(directory) / "bundle"
            bundle.write(bundle_root)

            dry_run = apply_bundle(
                bundle_root, kernel, dry_run=True
            )
            self.assertTrue(dry_run.changed_paths)
            self.assertEqual(
                source.read_text(encoding="utf-8"), original
            )

            applied = apply_bundle(bundle_root, kernel)
            self.assertIn(
                'request_module("deferred_math")',
                source.read_text(encoding="utf-8"),
            )
            self.assertTrue(
                (
                    kernel
                    / "kernel/linux_modularizer/deferred_math.c"
                ).is_file()
            )
            self.assertIn(
                "linux-modularizer:directory",
                (kernel / "Kconfig").read_text(encoding="utf-8"),
            )

            second = apply_bundle(bundle_root, kernel)
            self.assertFalse(second.changed_paths)
            restored = rollback_transaction(
                kernel, applied.transaction_id
            )
            self.assertTrue(restored)
            self.assertEqual(
                source.read_text(encoding="utf-8"), original
            )

            replayed = apply_bundle(bundle_root, kernel)
            self.assertEqual(
                replayed.transaction_id, applied.transaction_id
            )
            self.assertTrue(replayed.changed_paths)
            rollback_transaction(kernel, replayed.transaction_id)
            self.assertEqual(
                source.read_text(encoding="utf-8"), original
            )

            (kernel / "Kconfig").write_text(
                'mainmenu "fixture"\n# compositional base\n',
                encoding="utf-8",
            )
            composed = apply_bundle(bundle_root, kernel)
            self.assertNotEqual(
                composed.transaction_id, applied.transaction_id
            )
            rollback_transaction(kernel, composed.transaction_id)

    def test_plan_candidate_is_automatically_prepared(self):
        with tempfile.TemporaryDirectory() as directory:
            kernel = Path(directory) / "linux"
            source_dir = kernel / "drivers/fixture"
            source_dir.mkdir(parents=True)
            source = source_dir / FIXTURE.name
            shutil.copyfile(FIXTURE, source)
            shutil.copyfile(
                FIXTURE.with_name("backend_api.h"),
                source_dir / "backend_api.h",
            )
            relative = "drivers/fixture/backend_candidate.c"
            graph = ReferenceGraph()
            resident = ReferenceNode.function(
                "resident_entry",
                Linkage.EXTERNAL,
                source_path=relative,
                size_bytes=32,
                attributes={"exported", "definition"},
                contexts={ExecutionContext.PROCESS},
            )
            deferred = ReferenceNode.function(
                "deferred_add",
                Linkage.EXTERNAL,
                source_path=relative,
                size_bytes=4096,
                attributes={"definition"},
            )
            reset = ReferenceNode.function(
                "deferred_reset",
                Linkage.EXTERNAL,
                source_path=relative,
                size_bytes=4096,
                attributes={"definition"},
            )
            helper = ReferenceNode.function(
                "helper_add",
                Linkage.INTERNAL,
                translation_unit=relative,
                source_path=relative,
                size_bytes=2048,
                attributes={"definition"},
            )
            bias = ReferenceNode.global_variable(
                "deferred_bias",
                Linkage.INTERNAL,
                translation_unit=relative,
                source_path=relative,
                attributes={"definition", "mutable", "internal"},
            )
            for node in (resident, deferred, reset, helper, bias):
                graph.add_node(node)
            for edge in (
                ReferenceEdge(
                    resident.id, deferred.id, EdgeKind.DIRECT_CALL
                ),
                ReferenceEdge(
                    resident.id, reset.id, EdgeKind.DIRECT_CALL
                ),
                ReferenceEdge(
                    deferred.id, helper.id, EdgeKind.DIRECT_CALL
                ),
                ReferenceEdge(
                    helper.id, bias.id, EdgeKind.GLOBAL_READ
                ),
                ReferenceEdge(
                    reset.id, bias.id, EdgeKind.GLOBAL_WRITE
                ),
            ):
                graph.add_edge(edge)
            plan = plan_modules(graph)
            report = module_plan_to_dict(
                graph,
                PlannerPolicy(),
                plan,
            )
            candidate = next(
                item
                for item in report["candidates"]
                if deferred.id in item["functions"]
            )
            # Model an audited closure decision: helper_add is safe to
            # duplicate, while resident_entry stays in vmlinux and gains an
            # explicit export. The preparation bridge must extract both
            # source bodies and preserve their distinct backend policies.
            candidate["functions"].remove(helper.id)
            report_path = Path(directory) / "plan.json"
            write_json_atomic(report_path, report)
            compile_database = Path(directory) / "compile_commands.json"
            # Compilation databases are arrays, so write this artifact
            # directly for the LibTooling integration fixture.
            compile_database.write_text(
                json.dumps(
                    [
                        {
                            "directory": str(source_dir),
                            "arguments": [
                                str(LLVM_ROOT / "bin/clang"),
                                "-std=c11",
                                "-I",
                                str(source_dir),
                                "-c",
                                str(source),
                            ],
                            "file": str(source),
                        }
                    ]
                ),
                encoding="utf-8",
            )
            extraction_path = Path(directory) / "source.json"
            policy_path = Path(directory) / "backend-policy.json"

            prepare_candidate(
                graph,
                report_path,
                candidate["id"],
                source_extractor=self.extractor,
                compilation_database=compile_database,
                kernel_root=kernel,
                module_name="deferred_math",
                extraction_output=extraction_path,
                backend_policy_output=policy_path,
                duplicate_functions=(helper.id,),
                resident_exports=(resident.id,),
                external_resident_exports=("fixture_dependency",),
                module_defines={"FIXTURE_ARCH(value)": "0"},
            )

            extraction = load_source_extraction(extraction_path)
            self.assertEqual(
                {item.symbol for item in extraction.functions},
                {
                    "helper_add",
                    "deferred_add",
                    "deferred_reset",
                    "resident_entry",
                },
            )
            policy = json.loads(
                policy_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                set(policy["interfaces"]),
                {"deferred_add", "deferred_reset"},
            )
            self.assertEqual(
                policy["duplicate_functions"], ["helper_add"]
            )
            self.assertEqual(
                policy["resident_exports"], ["resident_entry"]
            )
            self.assertEqual(
                policy["external_resident_exports"],
                ["fixture_dependency"],
            )
            self.assertEqual(
                policy["module_defines"],
                {"FIXTURE_ARCH(value)": "0"},
            )

            split_extraction_path = (
                Path(directory) / "split-source.json"
            )
            split_policy_path = (
                Path(directory) / "split-backend-policy.json"
            )
            prepare_candidate(
                graph,
                report_path,
                candidate["id"],
                source_extractor=self.extractor,
                compilation_database=compile_database,
                kernel_root=kernel,
                module_name="deferred_math_split",
                extraction_output=split_extraction_path,
                backend_policy_output=split_policy_path,
                duplicate_functions=(helper.id,),
                resident_exports=(resident.id,),
                excluded_interfaces=(reset.id,),
            )
            split_extraction = load_source_extraction(
                split_extraction_path
            )
            self.assertEqual(
                {item.symbol for item in split_extraction.functions},
                {"helper_add", "deferred_add", "resident_entry"},
            )
            split_policy = json.loads(
                split_policy_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                split_policy["interfaces"], ["deferred_add"]
            )


if __name__ == "__main__":
    unittest.main()

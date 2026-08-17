import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from kernel_modularizer.model import (
    EntityKind,
    Linkage,
    ReferenceGraph,
    ReferenceNode,
)
from kernel_modularizer.symbol_sizes import enrich_graph_symbol_sizes


@unittest.skipUnless(
    shutil.which("cc") and shutil.which("nm") and shutil.which("objdump"),
    "C compiler, nm, and objdump are required",
)
class SymbolSizeEnrichmentTests(unittest.TestCase):
    def test_same_named_static_functions_use_their_own_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            objects = root / "objects/drivers"
            objects.mkdir(parents=True)
            source_a = root / "a.c"
            source_b = root / "b.c"
            source_a.write_text(
                "static int helper(int x) { return x + 1; }\n"
                "int entry_a(int x) { return helper(x); }\n"
                "__attribute__((used, section(\".pci_fixup_test\")))\n"
                "static int (*registered_helper)(int) = helper;\n"
                "struct cpu_dev { int (*init)(int); };\n"
                "static const struct cpu_dev vendor_ops = { helper };\n"
                "__attribute__((used, section(\".x86_cpu_dev.init\")))\n"
                "static const struct cpu_dev *registered_vendor_ops = "
                "&vendor_ops;\n"
                "const int __ksymtab_entry_a = 0;\n"
                "const int __ksymtab_export_only = 0;\n",
                encoding="utf-8",
            )
            source_b.write_text(
                "static int helper(int x) {\n"
                "  int y = 0; for (int i = 0; i < x; ++i) y += i;\n"
                "  return y;\n}\n"
                "int entry_b(int x) { return helper(x); }\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["cc", "-O0", "-c", str(source_a), "-o", str(objects / "a.o")],
                check=True,
            )
            subprocess.run(
                ["cc", "-O0", "-c", str(source_b), "-o", str(objects / "b.o")],
                check=True,
            )
            manifest = root / "facts-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "translation_units": [
                            {
                                "translation_unit": "drivers/a.c",
                                "bitcode": "drivers/a.bc",
                            },
                            {
                                "translation_unit": "drivers/b.c",
                                "bitcode": "drivers/b.bc",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            graph = ReferenceGraph()
            helper_a = ReferenceNode.function(
                "helper",
                Linkage.INTERNAL,
                translation_unit="drivers/a.c",
                source_path="drivers/a.c",
            )
            helper_b = ReferenceNode.function(
                "helper",
                Linkage.INTERNAL,
                translation_unit="drivers/b.c",
                source_path="drivers/b.c",
            )
            entry = ReferenceNode.function(
                "entry_a",
                Linkage.EXTERNAL,
                source_path="drivers/a.c",
            )
            export_only = ReferenceNode.function(
                "export_only",
                Linkage.EXTERNAL,
            )
            vendor_ops = ReferenceNode.entity(
                EntityKind.GLOBAL,
                "vendor_ops",
                owner="drivers/a.c",
                source_path="drivers/a.c",
            )
            for node in (
                helper_a,
                helper_b,
                export_only,
                entry,
                vendor_ops,
            ):
                graph.add_node(node)

            result = enrich_graph_symbol_sizes(
                graph, manifest, root / "objects"
            )

            self.assertEqual(len(result.matched), 4)
            self.assertEqual(result.missing_nodes, (export_only.id,))
            size_a = result.graph.nodes[helper_a.id].size_bytes
            size_b = result.graph.nodes[helper_b.id].size_bytes
            self.assertIsNotNone(size_a)
            self.assertIsNotNone(size_b)
            self.assertNotEqual(size_a, size_b)
            self.assertEqual(
                result.exported_nodes,
                (entry.id, export_only.id),
            )
            self.assertEqual(
                set(result.linker_registered_nodes),
                {helper_a.id, vendor_ops.id},
            )
            self.assertIn(
                "exported",
                result.graph.nodes[entry.id].attributes,
            )
            self.assertIn(
                "exported",
                result.graph.nodes[export_only.id].attributes,
            )
            self.assertIn(
                "linker_registered",
                result.graph.nodes[helper_a.id].attributes,
            )
            self.assertNotIn(
                "linker_registered",
                result.graph.nodes[helper_b.id].attributes,
            )
            self.assertIn(
                "linker_registered",
                result.graph.nodes[vendor_ops.id].attributes,
            )


if __name__ == "__main__":
    unittest.main()

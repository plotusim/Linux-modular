from pathlib import Path
import subprocess
import tempfile
import unittest

from kernel_modularizer.embedded_bitcode import (
    extract_embedded_bitcode_tree,
)


ROOT = Path(__file__).resolve().parents[1]
LLVM_ROOT = Path("/opt/llvm-custom")


def _llvm_available():
    return all(
        (LLVM_ROOT / f"bin/{tool}").is_file()
        for tool in ("clang", "llvm-ar", "opt")
    )


@unittest.skipUnless(_llvm_available(), "LLVM toolchain is unavailable")
class EmbeddedBitcodeTests(unittest.TestCase):
    def test_extracts_valid_bitcode_from_normal_elf_object(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            objects = root / "objects"
            output = root / "bitcode"
            source = ROOT / "tests/fixtures/llvm/indirect_callbacks.c"
            embedded = objects / "drivers/fixture.o"
            plain = objects / "drivers/plain.o"
            embedded.parent.mkdir(parents=True)
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-O2",
                    "-fembed-bitcode=all",
                    "-c",
                    str(source),
                    "-o",
                    str(embedded),
                ],
                check=True,
            )
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-O2",
                    "-c",
                    str(source),
                    "-o",
                    str(plain),
                ],
                check=True,
            )

            result = extract_embedded_bitcode_tree(
                objects, output, jobs=2
            )
            bitcode = output / "drivers/fixture.bc"

            self.assertEqual(result.scanned_objects, 2)
            self.assertEqual(result.skipped_objects, 1)
            self.assertEqual(len(result.entries), 1)
            self.assertTrue(bitcode.is_file())
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/opt"),
                    "-disable-output",
                    str(bitcode),
                ],
                check=True,
            )

            repeated = extract_embedded_bitcode_tree(
                objects, output, jobs=1
            )
            self.assertTrue(repeated.entries[0].reused)

    def test_extracts_save_temps_sidecar_for_kernel_compatible_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            objects = root / "objects"
            output = root / "bitcode"
            source = ROOT / "tests/fixtures/llvm/indirect_callbacks.c"
            sidecar_object = objects / "kernel/indirect_callbacks.o"
            sidecar_object.parent.mkdir(parents=True)
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/clang"),
                    "-O2",
                    "-save-temps=obj",
                    "-c",
                    str(source),
                    "-o",
                    str(sidecar_object),
                ],
                check=True,
            )

            result = extract_embedded_bitcode_tree(
                objects, output, jobs=1
            )

            self.assertEqual(len(result.entries), 1)
            self.assertTrue(result.entries[0].sidecar_bitcode)
            self.assertFalse(result.entries[0].raw_bitcode_object)
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/opt"),
                    "-disable-output",
                    str(output / "kernel/indirect_callbacks.bc"),
                ],
                check=True,
            )

    def test_restricts_extraction_to_thin_link_archive_members(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            objects = root / "objects"
            output = root / "bitcode"
            source = ROOT / "tests/fixtures/llvm/indirect_callbacks.c"
            linked = objects / "kernel/linked.o"
            unrelated = objects / "drivers/unrelated.o"
            archive = objects / "kernel/built-in.a"
            linked.parent.mkdir(parents=True)
            unrelated.parent.mkdir(parents=True)
            for destination in (linked, unrelated):
                subprocess.run(
                    [
                        str(LLVM_ROOT / "bin/clang"),
                        "-O2",
                        "-fembed-bitcode=all",
                        "-c",
                        str(source),
                        "-o",
                        str(destination),
                    ],
                    check=True,
                )
            subprocess.run(
                [
                    str(LLVM_ROOT / "bin/llvm-ar"),
                    "rcT",
                    str(archive),
                    str(linked),
                ],
                check=True,
            )

            result = extract_embedded_bitcode_tree(
                objects,
                output,
                jobs=1,
                link_inputs=["kernel/built-in.a"],
                archive_tool=LLVM_ROOT / "bin/llvm-ar",
            )

            self.assertEqual(result.scanned_objects, 1)
            self.assertEqual(result.link_inputs, ("kernel/built-in.a",))
            self.assertEqual(
                [entry.object_path for entry in result.entries],
                ["kernel/linked.o"],
            )
            self.assertFalse((output / "drivers/unrelated.bc").exists())


if __name__ == "__main__":
    unittest.main()

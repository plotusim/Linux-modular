import gzip
from pathlib import Path
import subprocess
import tempfile
import unittest

from kernel_modularizer.initramfs import (
    DEFAULT_INIT,
    build_boot_trace_initramfs,
    build_validation_initramfs,
)
from kernel_modularizer.errors import GraphValidationError
from kernel_modularizer.observations import (
    DEFAULT_LOADER_READY_MARKER,
)


class InitramfsTests(unittest.TestCase):
    def test_ready_metrics_are_emitted_by_one_atomic_printf(self):
        metrics_lines = [
            line
            for line in DEFAULT_INIT.splitlines()
            if "LINUX_MODULARIZER_METRICS" in line
        ]
        self.assertEqual(len(metrics_lines), 1)
        self.assertEqual(DEFAULT_INIT.count('        printf "'), 1)

    def test_reproducible_archive_contains_module_and_test_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "demo.ko"
            module.write_bytes(b"fixture module")
            first = root / "first.cpio.gz"
            second = root / "second.cpio.gz"
            arguments = {
                "busybox": "/usr/bin/busybox",
                "kernel_release": "5.10.176-test",
                "modules": [module],
            }
            build_validation_initramfs(first, **arguments)
            build_validation_initramfs(second, **arguments)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            archive = root / "archive.cpio"
            archive.write_bytes(gzip.decompress(first.read_bytes()))
            listing = subprocess.run(
                ["cpio", "-it", "-F", str(archive)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("init", listing)
            self.assertIn("bin/awk", listing)
            self.assertIn("bin/modprobe", listing)
            self.assertIn("sbin/modprobe", listing)
            self.assertIn(
                "lib/modules/5.10.176-test/kernel/"
                "linux_modularizer/demo.ko",
                listing,
            )
            self.assertIn(
                b"LINUX_MODULARIZER_METRICS",
                archive.read_bytes(),
            )

    def test_boot_trace_archive_is_reproducible_and_has_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "trace-first.cpio.gz"
            second = root / "trace-second.cpio.gz"
            arguments = {
                "busybox": "/usr/bin/busybox",
                "kernel_release": "5.10.176-trace",
            }
            build_boot_trace_initramfs(first, **arguments)
            build_boot_trace_initramfs(second, **arguments)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            archive = gzip.decompress(first.read_bytes())
            self.assertIn(b"LINUX_MODULARIZER_TRACE_BEGIN", archive)
            self.assertIn(b"LINUX_MODULARIZER_TRACE_END", archive)
            self.assertIn(b"/sys/kernel/tracing/tracing_on", archive)
            self.assertIn(
                DEFAULT_LOADER_READY_MARKER.encode(), archive
            )
            self.assertIn(b"/sys/kernel/tracing/trace_marker", archive)
            self.assertIn(b"/proc/modules", archive)
            self.assertIn(b"/proc/sys/kernel/modprobe", archive)
            self.assertIn(b'read kernel_modprobe', archive)

    def test_installed_module_tree_and_extra_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = "5.10.176-profile"
            installed = root / "installed"
            release_root = installed / "lib" / "modules" / release
            module = release_root / "kernel" / "fs" / "demo.ko"
            module.parent.mkdir(parents=True)
            module.write_bytes(b"real module tree payload")
            (release_root / "modules.dep").write_text(
                "kernel/fs/demo.ko:\n", encoding="utf-8"
            )
            (release_root / "modules.alias").write_text(
                "alias profile-demo demo\n", encoding="utf-8"
            )
            (release_root / "build").symlink_to("/host/build")
            image = root / "filesystem.img"
            image.write_bytes(b"filesystem fixture")
            first = root / "tree-first.cpio.gz"
            second = root / "tree-second.cpio.gz"
            arguments = {
                "busybox": "/usr/bin/busybox",
                "kernel_release": release,
                "module_tree": installed,
                "extra_files": {"testdata/filesystem.img": image},
            }

            build_validation_initramfs(first, **arguments)
            build_validation_initramfs(second, **arguments)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            archive = root / "tree.cpio"
            archive.write_bytes(gzip.decompress(first.read_bytes()))
            listing = subprocess.run(
                ["cpio", "-it", "-F", str(archive)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn(
                f"lib/modules/{release}/kernel/fs/demo.ko", listing
            )
            self.assertIn(
                f"lib/modules/{release}/modules.dep", listing
            )
            self.assertIn("testdata/filesystem.img", listing)
            self.assertNotIn(f"lib/modules/{release}/build", listing)
            archive_bytes = archive.read_bytes()
            self.assertIn(b"kernel/fs/demo.ko:", archive_bytes)
            self.assertIn(b"filesystem fixture", archive_bytes)

    def test_module_tree_cannot_be_mixed_with_flat_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "demo.ko"
            module.write_bytes(b"fixture")
            with self.assertRaisesRegex(
                GraphValidationError, "mutually exclusive"
            ):
                build_validation_initramfs(
                    root / "bad.cpio.gz",
                    busybox="/usr/bin/busybox",
                    kernel_release="5.10.176-test",
                    modules=[module],
                    module_tree=root,
                )


if __name__ == "__main__":
    unittest.main()

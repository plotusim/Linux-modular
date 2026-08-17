from pathlib import Path
import tempfile
import unittest

from kernel_modularizer.manifest import (
    create_stage_manifest,
    verify_stage_manifest,
)


class ManifestTests(unittest.TestCase):
    def test_fingerprint_is_independent_of_mapping_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")

            left = create_stage_manifest(
                "fixture",
                inputs={"a": first, "b": second},
                parameters={"x": 1, "y": 2},
                tools={"clang": "22", "python": "3"},
            )
            right = create_stage_manifest(
                "fixture",
                inputs={"b": second, "a": first},
                parameters={"y": 2, "x": 1},
                tools={"python": "3", "clang": "22"},
            )

            self.assertEqual(left["fingerprint"], right["fingerprint"])
            self.assertEqual(verify_stage_manifest(left), [])

    def test_verifier_detects_changed_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "input"
            artifact.write_text("before", encoding="utf-8")
            manifest = create_stage_manifest(
                "fixture",
                inputs={"input": artifact},
                parameters={},
                tools={},
            )

            artifact.write_text("after-with-different-size", encoding="utf-8")

            issues = verify_stage_manifest(manifest)
            self.assertEqual(len(issues), 1)
            self.assertIn("size changed", issues[0])


if __name__ == "__main__":
    unittest.main()

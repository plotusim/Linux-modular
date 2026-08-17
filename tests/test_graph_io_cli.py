import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from kernel_modularizer.cli import main
from kernel_modularizer.io import (
    load_reference_graph,
    write_json_atomic,
    write_reference_graph,
)
from kernel_modularizer.model import Linkage, ReferenceGraph, ReferenceNode


class GraphIoTests(unittest.TestCase):
    def make_graph(self):
        graph = ReferenceGraph(metadata={"fixture": True})
        graph.add_node(
            ReferenceNode.function(
                "start_kernel",
                Linkage.EXTERNAL,
                source_path="init/main.c",
            )
        )
        return graph

    def test_atomic_write_and_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "graph.json"

            write_reference_graph(path, self.make_graph())
            loaded = load_reference_graph(path)

            self.assertEqual(loaded.to_dict(), self.make_graph().to_dict())
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_validate_graph_cli_json_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.json"
            write_reference_graph(path, self.make_graph())
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                result = main(["validate-graph", str(path), "--json"])

            self.assertEqual(result, 0)
            self.assertIn('"valid": true', output.getvalue())
            self.assertIn('"nodes": 1', output.getvalue())

    def test_json_writer_rejects_non_standard_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"

            with self.assertRaisesRegex(ValueError, "serialize"):
                write_json_atomic(path, {"invalid": float("nan")})


if __name__ == "__main__":
    unittest.main()

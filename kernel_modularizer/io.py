"""Safe filesystem I/O for reference-graph artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Union
import json

from .errors import GraphValidationError
from .model import ReferenceGraph


PathLike = Union[str, os.PathLike[str]]


def load_reference_graph(path: PathLike) -> ReferenceGraph:
    graph_path = Path(path)
    try:
        content = graph_path.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphValidationError(
            f"cannot read reference graph {graph_path}: {error}"
        ) from error
    return ReferenceGraph.from_json(content)


def write_reference_graph(path: PathLike, graph: ReferenceGraph) -> None:
    """Atomically replace ``path`` with a validated graph artifact."""

    graph.validate()
    write_text_atomic(path, graph.to_json() + "\n")


def write_json_atomic(path: PathLike, value: Mapping[str, Any]) -> None:
    try:
        content = json.dumps(
            dict(value),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise GraphValidationError(
            f"cannot serialize JSON artifact {path}: {error}"
        ) from error
    write_text_atomic(path, content + "\n")


def write_text_atomic(path: PathLike, content: str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, output_path)
    except OSError as error:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        raise GraphValidationError(
            f"cannot write artifact {output_path}: {error}"
        ) from error


def write_bytes_atomic(path: PathLike, content: bytes) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, output_path)
    except OSError as error:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        raise GraphValidationError(
            f"cannot write artifact {output_path}: {error}"
        ) from error

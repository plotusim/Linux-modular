#!/usr/bin/env python3
"""Run ReferenceFacts over configured-kernel bitcode with checked failures."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kernel_modularizer.facts import TranslationUnitFacts  # noqa: E402
from kernel_modularizer.io import write_json_atomic, write_text_atomic  # noqa: E402


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract linkage-aware LLVM reference facts.",
    )
    parser.add_argument("--opt", required=True, type=Path)
    parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--bitcode-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, os.cpu_count() or 1),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing per-TU fact artifacts",
    )
    return parser.parse_args(argv)


def _llvm_major(opt: Path) -> int:
    completed = subprocess.run(
        [str(opt), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    for token in completed.stdout.replace("(", " ").split():
        prefix = token.split(".", 1)[0]
        if prefix.isdigit():
            return int(prefix)
    raise RuntimeError(f"cannot determine LLVM version from {completed.stdout!r}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fact_path(bitcode: Path, bitcode_root: Path, output: Path) -> Path:
    relative = bitcode.relative_to(bitcode_root)
    return output / relative.with_suffix(".facts.jsonl")


def _extract_one(
    bitcode: Path,
    *,
    opt: Path,
    plugin: Path,
    bitcode_root: Path,
    source_root: Path,
    output: Path,
    llvm_major: int,
    overwrite: bool,
) -> Dict[str, object]:
    destination = _fact_path(bitcode, bitcode_root, output)
    if destination.exists() and not overwrite:
        raise RuntimeError(
            f"fact artifact already exists: {destination}; "
            "use a new run directory or --overwrite"
        )
    if llvm_major >= 16:
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
    command = [
        str(opt),
        *pass_arguments,
        f"-reference-facts-source-root={source_root.resolve()}",
        "-disable-output",
        str(bitcode),
    ]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    facts = TranslationUnitFacts.from_json_lines(completed.stdout)
    write_text_atomic(destination, facts.to_json_lines())
    return {
        "bitcode": str(bitcode.relative_to(bitcode_root)),
        "bitcode_sha256": _sha256(bitcode),
        "facts": str(destination.relative_to(output)),
        "translation_unit": facts.translation_unit,
        "nodes": len(facts.graph.nodes),
        "edges": len(facts.graph.edges),
        "calls": len(facts.pointer_program.calls),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    if args.jobs < 1:
        raise SystemExit("--jobs must be at least 1")
    for executable in (args.opt, args.plugin):
        if not executable.is_file():
            raise SystemExit(f"required file does not exist: {executable}")
    bitcode_files = sorted(args.bitcode_root.rglob("*.bc"))
    if not bitcode_files:
        raise SystemExit(f"no .bc files found under {args.bitcode_root}")

    llvm_major = _llvm_major(args.opt)
    results = []
    failures = []
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        future_to_path = {
            executor.submit(
                _extract_one,
                bitcode,
                opt=args.opt,
                plugin=args.plugin,
                bitcode_root=args.bitcode_root,
                source_root=args.source_root,
                output=args.output,
                llvm_major=llvm_major,
                overwrite=args.overwrite,
            ): bitcode
            for bitcode in bitcode_files
        }
        for future in as_completed(future_to_path):
            bitcode = future_to_path[future]
            try:
                results.append(future.result())
            except Exception as error:
                failures.append((bitcode, error))

    if failures:
        for bitcode, error in sorted(failures, key=lambda item: str(item[0])):
            print(f"error: {bitcode}: {error}", file=sys.stderr)
        return 1

    manifest = {
        "schema_version": 1,
        "stage": "llvm-reference-facts",
        "llvm_major": llvm_major,
        "source_root": str(args.source_root.resolve()),
        "bitcode_root": str(args.bitcode_root.resolve()),
        "translation_units": sorted(
            results,
            key=lambda item: str(item["bitcode"]),
        ),
    }
    write_json_atomic(args.output / "manifest.json", manifest)
    print(
        f"extracted {len(results)} translation units into {args.output}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

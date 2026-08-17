#!/usr/bin/env python3
"""Create a reproducible BusyBox initramfs for QEMU validation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kernel_modularizer.initramfs import (  # noqa: E402
    build_validation_initramfs,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--busybox", required=True)
    parser.add_argument("--kernel-release", required=True)
    module_source = parser.add_mutually_exclusive_group()
    module_source.add_argument("--module", action="append", default=[])
    module_source.add_argument(
        "--module-tree",
        help=(
            "modules_install root or matching lib/modules/<release> tree; "
            "preserves dependency and alias metadata"
        ),
    )
    parser.add_argument(
        "--extra-file",
        action="append",
        default=[],
        metavar="ARCHIVE_PATH=HOST_PATH",
        help="embed an additional regular file at a safe archive path",
    )
    parser.add_argument("--trigger")
    parser.add_argument("--init-script")
    args = parser.parse_args()
    init_script = None
    if args.init_script:
        init_script = Path(args.init_script).read_text(encoding="utf-8")
    extra_files = {}
    for specification in args.extra_file:
        archive_path, separator, host_path = specification.partition("=")
        if not separator or not archive_path or not host_path:
            parser.error(
                "--extra-file must use ARCHIVE_PATH=HOST_PATH"
            )
        if archive_path in extra_files:
            parser.error(
                f"duplicate --extra-file archive path {archive_path!r}"
            )
        extra_files[archive_path] = host_path
    keyword = {}
    if init_script is not None:
        keyword["init_script"] = init_script
    build_validation_initramfs(
        args.output,
        busybox=args.busybox,
        kernel_release=args.kernel_release,
        modules=args.module,
        module_tree=args.module_tree,
        extra_files=extra_files,
        trigger=args.trigger,
        **keyword,
    )
    print(f"initramfs written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

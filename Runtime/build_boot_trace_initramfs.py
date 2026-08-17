#!/usr/bin/env python3
"""Create a reproducible initramfs that exports early function tracing."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kernel_modularizer.initramfs import (  # noqa: E402
    build_boot_trace_initramfs,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--busybox", required=True)
    parser.add_argument("--kernel-release", required=True)
    args = parser.parse_args()
    build_boot_trace_initramfs(
        args.output,
        busybox=args.busybox,
        kernel_release=args.kernel_release,
    )
    print(f"boot-trace initramfs written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

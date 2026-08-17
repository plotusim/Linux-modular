#!/usr/bin/env python3
"""Generate a dependency-aware startup profile preferring loadable modules."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kernel_modularizer.config_profile import (  # noqa: E402
    generate_tristate_module_profile,
)
from kernel_modularizer.io import (  # noqa: E402
    write_json_atomic,
    write_text_atomic,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel-root", required=True)
    parser.add_argument("--base-config", required=True)
    parser.add_argument("--output-config", required=True)
    parser.add_argument("--report-output", required=True)
    parser.add_argument("--keep", action="append", default=[])
    parser.add_argument("--arch", default="x86")
    parser.add_argument("--srcarch")
    parser.add_argument("--cc", default="clang")
    parser.add_argument("--ld", default="ld.lld")
    args = parser.parse_args()
    config_text, report = generate_tristate_module_profile(
        args.kernel_root,
        args.base_config,
        keep_symbols=args.keep,
        arch=args.arch,
        srcarch=args.srcarch,
        cc=args.cc,
        ld=args.ld,
    )
    write_text_atomic(args.output_config, config_text)
    write_json_atomic(args.report_output, report)
    print(
        f"explicit_module_requests="
        f"{report['summary']['explicit_module_requests']} "
        f"effective_changes={report['summary']['effective_changes']} "
        f"requested_but_still_y="
        f"{report['summary']['requested_but_still_y']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

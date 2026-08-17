"""Generate workload-specific Kconfig profiles that prefer modules."""

from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from .errors import GraphValidationError


CONFIG_PROFILE_SCHEMA_VERSION = 1


def generate_tristate_module_profile(
    kernel_root: str | Path,
    base_config: str | Path,
    *,
    keep_symbols: Iterable[str] = (),
    arch: str = "x86",
    srcarch: str | None = None,
    cc: str = "clang",
    ld: str = "ld.lld",
) -> tuple[str, dict[str, Any]]:
    """Turn every eligible baseline ``y`` tristate into ``m``.

    Only symbols that were enabled in the supplied baseline are explicitly
    requested as modules. Kconfig dependency and choice propagation may alter
    related symbols (for example, selecting Reno when CUBIC becomes a module),
    and every effective transition is reported. An explicitly kept symbol
    must remain at its original value or profile generation fails.
    """

    try:
        import kconfiglib
    except ImportError as error:
        raise GraphValidationError(
            "automatic Kconfig profile generation requires kconfiglib"
        ) from error

    root = Path(kernel_root).resolve()
    config = Path(base_config).resolve()
    if not (root / "Kconfig").is_file():
        raise GraphValidationError(
            f"kernel root does not contain Kconfig: {root}"
        )
    if not config.is_file():
        raise GraphValidationError(
            f"base kernel config does not exist: {config}"
        )
    if not arch:
        raise GraphValidationError("arch must be non-empty")

    keep = frozenset(_normalize_symbol(value) for value in keep_symbols)
    environment = {
        "ARCH": arch,
        "SRCARCH": srcarch or arch,
        "srctree": str(root),
        "CC": cc,
        "LD": ld,
    }
    previous_environment = {
        name: os.environ.get(name) for name in environment
    }
    previous_directory = Path.cwd()
    temporary_config: str | None = None
    try:
        os.environ.update(environment)
        os.chdir(root)
        kconfig = kconfiglib.Kconfig(
            str(root / "Kconfig"), warn=False
        )
        kconfig.load_config(str(config))
        symbols = {
            symbol.name: symbol
            for symbol in kconfig.unique_defined_syms
            if symbol.name
        }
        unknown_keep = sorted(keep.difference(symbols))
        if unknown_keep:
            raise GraphValidationError(
                "unknown keep symbol(s): " + ", ".join(unknown_keep)
            )
        original = {
            name: symbol.str_value for name, symbol in symbols.items()
        }

        requested = set()
        changed = True
        while changed:
            changed = False
            for name in sorted(symbols):
                symbol = symbols[name]
                if (
                    name in keep
                    or name in requested
                    or original[name] != "y"
                    or symbol.type != kconfiglib.TRISTATE
                    or symbol.tri_value != 2
                    or 1 not in symbol.assignable
                ):
                    continue
                symbol.set_value(1)
                requested.add(name)
                changed = True

        violated_keep = sorted(
            name
            for name in keep
            if symbols[name].str_value != original[name]
        )
        if violated_keep:
            details = ", ".join(
                f"{name}:{original[name]}->{symbols[name].str_value}"
                for name in violated_keep
            )
            raise GraphValidationError(
                "module preference changed an explicitly kept symbol: "
                + details
            )

        transitions = []
        transition_counts: Counter[str] = Counter()
        for name in sorted(symbols):
            before = original[name]
            after = symbols[name].str_value
            if before == after:
                continue
            transition = f"{before}->{after}"
            transition_counts[transition] += 1
            transitions.append(
                {
                    "symbol": f"CONFIG_{name}",
                    "before": before,
                    "after": after,
                    "explicitly_requested": name in requested,
                }
            )

        with tempfile.NamedTemporaryFile(
            prefix="linux-modularizer-profile-",
            suffix=".config",
            delete=False,
        ) as temporary:
            temporary_config = temporary.name
        kconfig.write_config(temporary_config, save_old=False)
        config_text = Path(temporary_config).read_text(encoding="utf-8")
        report = {
            "schema_version": CONFIG_PROFILE_SCHEMA_VERSION,
            "stage": "tristate-module-profile",
            "kernel_root": str(root),
            "base_config": str(config),
            "architecture": {
                "arch": arch,
                "srcarch": srcarch or arch,
            },
            "policy": {
                "only_original_y_symbols": True,
                "never_explicitly_enable_disabled_features": True,
                "preference": "eligible tristate y to m",
                "keep_symbols": [
                    f"CONFIG_{name}" for name in sorted(keep)
                ],
            },
            "summary": {
                "defined_symbols": len(symbols),
                "explicit_module_requests": len(requested),
                "effective_changes": len(transitions),
                "transition_counts": dict(
                    sorted(transition_counts.items())
                ),
                "requested_but_still_y": sum(
                    symbols[name].str_value == "y" for name in requested
                ),
            },
            "transitions": transitions,
        }
        return config_text, report
    finally:
        os.chdir(previous_directory)
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if temporary_config is not None:
            try:
                Path(temporary_config).unlink()
            except FileNotFoundError:
                pass


def _normalize_symbol(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GraphValidationError("keep symbols must be non-empty strings")
    symbol = value.strip()
    if symbol.startswith("CONFIG_"):
        symbol = symbol[len("CONFIG_") :]
    if not symbol or not all(
        character.isupper()
        or character.isdigit()
        or character == "_"
        for character in symbol
    ):
        raise GraphValidationError(f"invalid Kconfig symbol {value!r}")
    return symbol

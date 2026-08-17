"""Build a small reproducible newc initramfs for QEMU module tests."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import io
from pathlib import Path, PurePosixPath
import stat
from typing import Iterable, Mapping

from .errors import GraphValidationError
from .observations import DEFAULT_LOADER_READY_MARKER


@dataclass(frozen=True)
class ArchiveEntry:
    path: str
    mode: int
    data: bytes = b""
    rdev_major: int = 0
    rdev_minor: int = 0


DEFAULT_INIT = """#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
read uptime_seconds _ < /proc/uptime
awk -v uptime_seconds="$uptime_seconds" '
    /^MemAvailable:/ { available = $2 }
    /^MemFree:/ { free = $2 }
    /^Slab:/ { slab = $2 }
    /^SReclaimable:/ { sreclaimable = $2 }
    /^SUnreclaim:/ { sunreclaim = $2 }
    END {
        printf "LINUX_MODULARIZER_METRICS uptime_seconds=%s mem_available_kb=%d mem_free_kb=%d slab_kb=%d sreclaimable_kb=%d sunreclaim_kb=%d\\n", uptime_seconds, available, free, slab, sreclaimable, sunreclaim
    }
' /proc/meminfo
echo LINUX_MODULARIZER_READY
setsid cttyhack sh
poweroff -f
"""

_BOOT_TRACE_INIT_TEMPLATE = r"""#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
mount -t tracefs nodev /sys/kernel/tracing
kernel_modprobe=
if test -x /sbin/modprobe &&
   ls /lib/modules/*/modules.dep >/dev/null 2>&1 &&
   test -r /proc/modules &&
   test -r /proc/sys/kernel/modprobe &&
   read kernel_modprobe < /proc/sys/kernel/modprobe &&
   test -n "$kernel_modprobe" &&
   test -d /sys/module &&
   test -e /sys/kernel/tracing/trace_marker; then
    echo @LOADER_READY_MARKER@ > /sys/kernel/tracing/trace_marker
else
    echo LINUX_MODULARIZER_MODULE_LOADER_NOT_READY
fi
if test -e /sys/kernel/tracing/tracing_on; then
    echo 0 > /sys/kernel/tracing/tracing_on
fi
echo LINUX_MODULARIZER_TRACE_BEGIN
awk '
    BEGIN {
        marker = "@LOADER_READY_MARKER@"
        phase = "pre"
    }
    /^#/ { next }
    {
        line = $0
        if (index(line, "tracing_mark_write: " marker)) {
            print line
            phase = "post"
            next
        }
        split(line, halves, ": ")
        if (length(halves) < 2) next
        payload = halves[length(halves)]
        sub(/^function:[[:space:]]*/, "", payload)
        split(payload, fields, /[[:space:]]+/)
        symbol = fields[1]
        sub(/\+0x[0-9a-fA-F]+.*/, "", symbol)
        key = phase ":" symbol
        if (symbol ~ /^[A-Za-z_][A-Za-z0-9_.$]*$/ && !seen[key]++) {
            print $0
        }
    }
' /sys/kernel/tracing/trace
echo LINUX_MODULARIZER_TRACE_END
read uptime_seconds _ < /proc/uptime
echo "LINUX_MODULARIZER_TRACE_UPTIME uptime_seconds=$uptime_seconds"
echo LINUX_MODULARIZER_READY
poweroff -f
"""
BOOT_TRACE_INIT = _BOOT_TRACE_INIT_TEMPLATE.replace(
    "@LOADER_READY_MARKER@", DEFAULT_LOADER_READY_MARKER
)


def build_validation_initramfs(
    output: str | Path,
    *,
    busybox: str | Path,
    kernel_release: str,
    modules: Iterable[str | Path] = (),
    module_tree: str | Path | None = None,
    extra_files: Mapping[str, str | Path] | None = None,
    trigger: str | Path | None = None,
    init_script: str = DEFAULT_INIT,
) -> None:
    if not kernel_release or "/" in kernel_release or "\0" in kernel_release:
        raise GraphValidationError(f"invalid kernel release {kernel_release!r}")
    busybox_path = Path(busybox)
    try:
        busybox_data = busybox_path.read_bytes()
    except OSError as error:
        raise GraphValidationError(
            f"cannot read static BusyBox {busybox_path}: {error}"
        ) from error
    if not init_script.startswith("#!"):
        raise GraphValidationError("init script must start with a shebang")
    module_paths = tuple(Path(module) for module in modules)
    if module_tree is not None and module_paths:
        raise GraphValidationError(
            "module_tree and individual modules are mutually exclusive"
        )

    entries = [
        _directory("bin"),
        _directory("dev"),
        _directory("etc"),
        _directory("lib"),
        _directory("lib/modules"),
        _directory(f"lib/modules/{kernel_release}"),
        _directory("proc"),
        _directory("sbin"),
        _directory("sys"),
        _directory("tmp", mode=0o1777),
        ArchiveEntry(
            "dev/console",
            stat.S_IFCHR | 0o600,
            rdev_major=5,
            rdev_minor=1,
        ),
        ArchiveEntry(
            "dev/null",
            stat.S_IFCHR | 0o666,
            rdev_major=1,
            rdev_minor=3,
        ),
        ArchiveEntry("bin/busybox", stat.S_IFREG | 0o755, busybox_data),
        ArchiveEntry(
            "sbin/modprobe",
            stat.S_IFLNK | 0o777,
            b"../bin/busybox",
        ),
        ArchiveEntry("init", stat.S_IFREG | 0o755, init_script.encode()),
    ]
    for applet in (
        "awk",
        "cttyhack",
        "grep",
        "insmod",
        "ls",
        "modprobe",
        "mount",
        "poweroff",
        "rmmod",
        "setsid",
        "sh",
        "sleep",
        "test",
        "umount",
    ):
        entries.append(
            ArchiveEntry(
                f"bin/{applet}",
                stat.S_IFLNK | 0o777,
                b"busybox",
            )
        )

    if module_tree is not None:
        tree_root = _resolve_module_tree(module_tree, kernel_release)
        entries.extend(
            _module_tree_entries(
                tree_root,
                archive_root=f"lib/modules/{kernel_release}",
            )
        )
    else:
        entries.extend(
            _individual_module_entries(module_paths, kernel_release)
        )

    if trigger is not None:
        trigger_path = Path(trigger)
        try:
            trigger_data = trigger_path.read_bytes()
        except OSError as error:
            raise GraphValidationError(
                f"cannot read trigger {trigger_path}: {error}"
            ) from error
        entries.append(
            ArchiveEntry(
                "bin/modularizer-trigger",
                stat.S_IFREG | 0o755,
                trigger_data,
            )
        )

    for archive_path, source in sorted((extra_files or {}).items()):
        normalized = _safe_archive_path(archive_path)
        _append_missing_parent_directories(entries, normalized)
        source_path = Path(source)
        try:
            source_stat = source_path.stat()
            source_data = source_path.read_bytes()
        except OSError as error:
            raise GraphValidationError(
                f"cannot read initramfs extra file {source_path}: {error}"
            ) from error
        if not stat.S_ISREG(source_stat.st_mode):
            raise GraphValidationError(
                f"initramfs extra file is not regular: {source_path}"
            )
        entries.append(
            ArchiveEntry(
                normalized,
                stat.S_IFREG | stat.S_IMODE(source_stat.st_mode),
                source_data,
            )
        )

    archive = _newc_archive(entries)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("wb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, mtime=0
            ) as compressed:
                compressed.write(archive)
    except OSError as error:
        raise GraphValidationError(
            f"cannot write initramfs {output_path}: {error}"
        ) from error


def build_boot_trace_initramfs(
    output: str | Path,
    *,
    busybox: str | Path,
    kernel_release: str,
) -> None:
    """Build an initramfs that freezes and exports a unique boot trace."""

    build_validation_initramfs(
        output,
        busybox=busybox,
        kernel_release=kernel_release,
        init_script=BOOT_TRACE_INIT,
    )


def _individual_module_entries(
    module_paths: Iterable[Path], kernel_release: str
) -> list[ArchiveEntry]:
    entries = []
    dependency_lines = []
    module_directory = (
        f"lib/modules/{kernel_release}/kernel/linux_modularizer"
    )
    paths = tuple(module_paths)
    if paths:
        entries.extend(
            (
                _directory(f"lib/modules/{kernel_release}/kernel"),
                _directory(module_directory),
            )
        )
    seen_modules = set()
    for module_path in sorted(paths, key=lambda item: item.name):
        if module_path.name in seen_modules:
            raise GraphValidationError(
                f"duplicate module basename {module_path.name!r}"
            )
        seen_modules.add(module_path.name)
        try:
            module_data = module_path.read_bytes()
        except OSError as error:
            raise GraphValidationError(
                f"cannot read module {module_path}: {error}"
            ) from error
        archive_path = f"{module_directory}/{module_path.name}"
        entries.append(
            ArchiveEntry(archive_path, stat.S_IFREG | 0o644, module_data)
        )
        dependency_lines.append(
            f"kernel/linux_modularizer/{module_path.name}:"
        )
    entries.append(
        ArchiveEntry(
            f"lib/modules/{kernel_release}/modules.dep",
            stat.S_IFREG | 0o644,
            ("\n".join(dependency_lines) + "\n").encode(),
        )
    )
    return entries


def _resolve_module_tree(
    module_tree: str | Path, kernel_release: str
) -> Path:
    supplied = Path(module_tree).resolve()
    installed_release = supplied / "lib" / "modules" / kernel_release
    root = installed_release if installed_release.is_dir() else supplied
    if not root.is_dir():
        raise GraphValidationError(
            f"module tree does not exist: {module_tree}"
        )
    if root.name != kernel_release:
        raise GraphValidationError(
            "module tree must be an install root or the matching "
            f"lib/modules/{kernel_release} directory: {module_tree}"
        )
    if not (root / "modules.dep").is_file():
        raise GraphValidationError(
            f"module tree is missing modules.dep: {root}"
        )
    return root


def _module_tree_entries(
    root: Path, *, archive_root: str
) -> list[ArchiveEntry]:
    entries = []
    for source in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        relative = source.relative_to(root)
        if relative.parts[0] in {"build", "source"}:
            continue
        archive_path = f"{archive_root}/{relative.as_posix()}"
        try:
            source_stat = source.lstat()
        except OSError as error:
            raise GraphValidationError(
                f"cannot inspect module-tree path {source}: {error}"
            ) from error
        permissions = stat.S_IMODE(source_stat.st_mode)
        if stat.S_ISDIR(source_stat.st_mode):
            entries.append(_directory(archive_path, mode=permissions))
        elif stat.S_ISREG(source_stat.st_mode):
            try:
                data = source.read_bytes()
            except OSError as error:
                raise GraphValidationError(
                    f"cannot read module-tree file {source}: {error}"
                ) from error
            entries.append(
                ArchiveEntry(
                    archive_path,
                    stat.S_IFREG | permissions,
                    data,
                )
            )
        elif stat.S_ISLNK(source_stat.st_mode):
            try:
                target = source.readlink().as_posix()
            except OSError as error:
                raise GraphValidationError(
                    f"cannot read module-tree symlink {source}: {error}"
                ) from error
            if PurePosixPath(target).is_absolute() or ".." in PurePosixPath(
                target
            ).parts:
                raise GraphValidationError(
                    f"unsafe module-tree symlink {source} -> {target}"
                )
            entries.append(
                ArchiveEntry(
                    archive_path,
                    stat.S_IFLNK | 0o777,
                    target.encode(),
                )
            )
        else:
            raise GraphValidationError(
                f"unsupported module-tree entry: {source}"
            )
    return entries


def _append_missing_parent_directories(
    entries: list[ArchiveEntry], path: str
) -> None:
    existing = {entry.path for entry in entries}
    parents = tuple(PurePosixPath(path).parents)
    for parent in reversed(parents[:-1]):
        normalized = parent.as_posix()
        if normalized not in existing:
            entries.append(_directory(normalized))
            existing.add(normalized)


def _directory(path: str, *, mode: int = 0o755) -> ArchiveEntry:
    return ArchiveEntry(path, stat.S_IFDIR | mode)


def _newc_archive(entries: Iterable[ArchiveEntry]) -> bytes:
    output = io.BytesIO()
    inode = 1
    seen = set()
    for entry in entries:
        normalized = _safe_archive_path(entry.path)
        if normalized in seen:
            raise GraphValidationError(
                f"duplicate initramfs path {normalized!r}"
            )
        seen.add(normalized)
        _write_newc_entry(output, inode, normalized, entry)
        inode += 1
    trailer = ArchiveEntry("TRAILER!!!", stat.S_IFREG)
    _write_newc_entry(output, inode, "TRAILER!!!", trailer)
    while output.tell() % 512:
        output.write(b"\0")
    return output.getvalue()


def _write_newc_entry(
    output: io.BytesIO,
    inode: int,
    path: str,
    entry: ArchiveEntry,
) -> None:
    name = path.encode("utf-8") + b"\0"
    values = (
        inode,
        entry.mode,
        0,
        0,
        1,
        0,
        len(entry.data),
        0,
        0,
        entry.rdev_major,
        entry.rdev_minor,
        len(name),
        0,
    )
    header = "070701" + "".join(f"{value:08x}" for value in values)
    output.write(header.encode("ascii"))
    output.write(name)
    _pad_four(output)
    output.write(entry.data)
    _pad_four(output)


def _pad_four(output: io.BytesIO) -> None:
    while output.tell() % 4:
        output.write(b"\0")


def _safe_archive_path(path: str) -> str:
    value = PurePosixPath(path)
    if value.is_absolute() or ".." in value.parts or not path:
        raise GraphValidationError(
            f"unsafe initramfs archive path {path!r}"
        )
    return value.as_posix()

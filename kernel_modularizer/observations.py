"""Parse ftrace records into conservative, phase-aware boot evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .errors import GraphValidationError, SchemaVersionError
from .model import EntityKind, ExecutionContext, ReferenceGraph


OBSERVATION_SCHEMA_VERSION = 2
LEGACY_OBSERVATION_SCHEMA_VERSION = 1
DEFAULT_LOADER_READY_MARKER = (
    "LINUX_MODULARIZER_MODULE_LOADER_READY"
)
_TRACE_PREFIX = re.compile(
    r"\[(?P<cpu>\d+)\]\s+"
    r"(?P<flags>[A-Za-z0-9.]+)\s+"
    r"(?P<timestamp>\d+\.\d+):\s*(?P<payload>.*)$"
)
_SYMBOL = re.compile(r"^([A-Za-z_][A-Za-z0-9_.$]*)")


class BootPhase(str, Enum):
    """Observed position relative to a usable module-loader marker."""

    PRE_LOADER = "pre_loader"
    POST_LOADER = "post_loader"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class _FunctionTraceRecord:
    timestamp: float
    symbol: str
    context: ExecutionContext


@dataclass(frozen=True)
class _LoaderReadyTraceRecord:
    timestamp: float


@dataclass(frozen=True)
class BootObservations:
    node_counts: Mapping[str, int]
    node_contexts: Mapping[str, frozenset[ExecutionContext]]
    node_phases: Mapping[str, frozenset[BootPhase]]
    node_first_timestamps: Mapping[str, float]
    node_last_timestamps: Mapping[str, float]
    ambiguous_symbols: Mapping[str, tuple[str, ...]]
    unmatched_symbols: Mapping[str, int]
    trace_sha256: tuple[str, ...]
    loader_ready_marker: str
    loader_ready_timestamps: tuple[float | None, ...]
    parsed_lines: int
    ignored_lines: int

    @property
    def observed_node_ids(self) -> frozenset[str]:
        return frozenset(self.node_counts)

    @property
    def pre_loader_node_ids(self) -> frozenset[str]:
        return frozenset(
            node_id
            for node_id, phases in self.node_phases.items()
            if BootPhase.PRE_LOADER in phases
        )

    @property
    def post_loader_node_ids(self) -> frozenset[str]:
        return frozenset(
            node_id
            for node_id, phases in self.node_phases.items()
            if BootPhase.POST_LOADER in phases
        )

    @property
    def unknown_phase_node_ids(self) -> frozenset[str]:
        return frozenset(
            node_id
            for node_id, phases in self.node_phases.items()
            if BootPhase.UNKNOWN in phases
        )

    @property
    def pre_and_post_loader_node_ids(self) -> frozenset[str]:
        return (
            self.pre_loader_node_ids
            & self.post_loader_node_ids
        )

    @property
    def pre_loader_only_node_ids(self) -> frozenset[str]:
        return (
            self.pre_loader_node_ids
            - self.post_loader_node_ids
            - self.unknown_phase_node_ids
        )

    @property
    def post_loader_only_node_ids(self) -> frozenset[str]:
        return (
            self.post_loader_node_ids
            - self.pre_loader_node_ids
            - self.unknown_phase_node_ids
        )

    @property
    def loader_ready_observed(self) -> bool:
        return (
            bool(self.trace_sha256)
            and len(self.loader_ready_timestamps)
            == len(self.trace_sha256)
            and all(
                timestamp is not None
                for timestamp in self.loader_ready_timestamps
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "summary": {
                "observed_nodes": len(self.node_counts),
                "parsed_lines": self.parsed_lines,
                "ignored_lines": self.ignored_lines,
                "ambiguous_symbols": len(self.ambiguous_symbols),
                "unmatched_symbols": len(self.unmatched_symbols),
                "pre_loader_nodes": len(self.pre_loader_node_ids),
                "post_loader_nodes": len(self.post_loader_node_ids),
                "pre_loader_only_nodes": len(
                    self.pre_loader_only_node_ids
                ),
                "post_loader_only_nodes": len(
                    self.post_loader_only_node_ids
                ),
                "pre_and_post_loader_nodes": len(
                    self.pre_and_post_loader_node_ids
                ),
                "unknown_phase_nodes": len(
                    self.unknown_phase_node_ids
                ),
                "loader_ready_traces": sum(
                    timestamp is not None
                    for timestamp in self.loader_ready_timestamps
                ),
                "traces": len(self.trace_sha256),
            },
            "trace_sha256": list(self.trace_sha256),
            "loader_ready": {
                "marker": self.loader_ready_marker,
                "all_traces_observed": self.loader_ready_observed,
                "timestamps_seconds": list(
                    self.loader_ready_timestamps
                ),
            },
            "observations": [
                {
                    "node_id": node_id,
                    "count": count,
                    "contexts": sorted(
                        context.value
                        for context in self.node_contexts.get(
                            node_id, frozenset()
                        )
                    ),
                    "phases": sorted(
                        phase.value
                        for phase in self.node_phases.get(
                            node_id, frozenset({BootPhase.UNKNOWN})
                        )
                    ),
                    "first_timestamp_seconds": (
                        self.node_first_timestamps.get(node_id)
                    ),
                    "last_timestamp_seconds": (
                        self.node_last_timestamps.get(node_id)
                    ),
                }
                for node_id, count in sorted(self.node_counts.items())
            ],
            "ambiguous_symbols": {
                symbol: list(nodes)
                for symbol, nodes in sorted(
                    self.ambiguous_symbols.items()
                )
            },
            "unmatched_symbols": dict(
                sorted(self.unmatched_symbols.items())
            ),
        }


def parse_ftrace_files(
    graph: ReferenceGraph,
    paths: Iterable[str | Path],
    *,
    loader_ready_marker: str = DEFAULT_LOADER_READY_MARKER,
    require_loader_ready_marker: bool = False,
) -> BootObservations:
    graph.validate()
    if not loader_ready_marker or "\n" in loader_ready_marker:
        raise GraphValidationError(
            "loader-ready marker must be one non-empty line"
        )
    by_symbol: dict[str, list[str]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind is EntityKind.FUNCTION:
            by_symbol[node.symbol].append(node.id)

    counts: Counter[str] = Counter()
    contexts: dict[str, set[ExecutionContext]] = defaultdict(set)
    phases: dict[str, set[BootPhase]] = defaultdict(set)
    first_timestamps: dict[str, float] = {}
    last_timestamps: dict[str, float] = {}
    ambiguous: dict[str, tuple[str, ...]] = {}
    unmatched: Counter[str] = Counter()
    digests = []
    loader_ready_timestamps: list[float | None] = []
    parsed_lines = 0
    ignored_lines = 0

    trace_paths = [Path(path) for path in paths]
    if not trace_paths:
        raise GraphValidationError("at least one ftrace file is required")
    for path in trace_paths:
        try:
            raw_content = path.read_bytes()
        except OSError as error:
            raise GraphValidationError(
                f"cannot read ftrace artifact {path}: {error}"
            ) from error
        content = raw_content.decode("utf-8", errors="replace")
        digests.append(hashlib.sha256(raw_content).hexdigest())
        function_records: list[
            tuple[int, _FunctionTraceRecord]
        ] = []
        marker_records: list[
            tuple[int, _LoaderReadyTraceRecord]
        ] = []
        for line_index, line in enumerate(content.splitlines()):
            parsed = _parse_trace_line(
                line, loader_ready_marker=loader_ready_marker
            )
            if parsed is None:
                ignored_lines += 1
                continue
            if isinstance(parsed, _LoaderReadyTraceRecord):
                marker_records.append((line_index, parsed))
                continue
            parsed_lines += 1
            function_records.append((line_index, parsed))

        if len(marker_records) > 1:
            raise GraphValidationError(
                f"ftrace artifact {path} contains multiple "
                f"{loader_ready_marker!r} markers"
            )
        marker_line = (
            marker_records[0][0] if marker_records else None
        )
        marker_timestamp = (
            marker_records[0][1].timestamp
            if marker_records
            else None
        )
        loader_ready_timestamps.append(marker_timestamp)
        if require_loader_ready_marker and marker_line is None:
            raise GraphValidationError(
                f"ftrace artifact {path} is missing loader-ready "
                f"marker {loader_ready_marker!r}"
            )

        for line_index, record in function_records:
            phase = (
                BootPhase.UNKNOWN
                if marker_line is None
                else (
                    BootPhase.PRE_LOADER
                    if line_index < marker_line
                    else BootPhase.POST_LOADER
                )
            )
            matches = tuple(
                sorted(by_symbol.get(record.symbol, ()))
            )
            if not matches:
                unmatched[record.symbol] += 1
                continue
            if len(matches) > 1:
                # A common ftrace configuration prints only the symbol.
                # Root every linkage candidate rather than guessing which
                # same-named static function executed.
                ambiguous[record.symbol] = matches
            for node_id in matches:
                counts[node_id] += 1
                contexts[node_id].add(record.context)
                phases[node_id].add(phase)
                first_timestamps[node_id] = min(
                    first_timestamps.get(
                        node_id, record.timestamp
                    ),
                    record.timestamp,
                )
                last_timestamps[node_id] = max(
                    last_timestamps.get(
                        node_id, record.timestamp
                    ),
                    record.timestamp,
                )

    return BootObservations(
        node_counts=dict(counts),
        node_contexts={
            node: frozenset(values) for node, values in contexts.items()
        },
        node_phases={
            node: frozenset(values) for node, values in phases.items()
        },
        node_first_timestamps=first_timestamps,
        node_last_timestamps=last_timestamps,
        ambiguous_symbols=ambiguous,
        unmatched_symbols=dict(unmatched),
        trace_sha256=tuple(digests),
        loader_ready_marker=loader_ready_marker,
        loader_ready_timestamps=tuple(loader_ready_timestamps),
        parsed_lines=parsed_lines,
        ignored_lines=ignored_lines,
    )


def load_boot_observations(
    path: str | Path, graph: ReferenceGraph
) -> BootObservations:
    artifact = Path(path)
    try:
        raw = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphValidationError(
            f"cannot read boot observations {artifact}: {error}"
        ) from error
    if not isinstance(raw, Mapping):
        raise GraphValidationError("boot observations must be an object")
    version = raw.get("schema_version")
    if version not in {
        LEGACY_OBSERVATION_SCHEMA_VERSION,
        OBSERVATION_SCHEMA_VERSION,
    }:
        raise SchemaVersionError(
            "unsupported boot observation schema_version "
            f"{version!r}"
        )
    values = raw.get("observations")
    if not isinstance(values, list):
        raise GraphValidationError(
            "boot observations.observations must be an array"
        )
    counts: dict[str, int] = {}
    contexts: dict[str, frozenset[ExecutionContext]] = {}
    phases: dict[str, frozenset[BootPhase]] = {}
    first_timestamps: dict[str, float] = {}
    last_timestamps: dict[str, float] = {}
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise GraphValidationError(
                f"observations[{index}] must be an object"
            )
        node_id = value.get("node_id")
        count = value.get("count")
        raw_contexts = value.get("contexts")
        if node_id not in graph.nodes:
            raise GraphValidationError(
                f"observation references unknown node {node_id!r}"
            )
        if graph.nodes[node_id].kind is not EntityKind.FUNCTION:
            raise GraphValidationError(
                f"observation references non-function node {node_id!r}"
            )
        if node_id in counts:
            raise GraphValidationError(
                f"duplicate observation for node {node_id!r}"
            )
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise GraphValidationError(
                f"observations[{index}].count must be positive"
            )
        if not isinstance(raw_contexts, list):
            raise GraphValidationError(
                f"observations[{index}].contexts must be an array"
            )
        try:
            resolved_contexts = frozenset(
                ExecutionContext(item) for item in raw_contexts
            )
        except (TypeError, ValueError) as error:
            raise GraphValidationError(
                f"observations[{index}] has invalid context: {error}"
            ) from error

        if version == LEGACY_OBSERVATION_SCHEMA_VERSION:
            resolved_phases = frozenset({BootPhase.UNKNOWN})
            first_timestamp = None
            last_timestamp = None
        else:
            raw_phases = value.get("phases")
            if not isinstance(raw_phases, list) or not raw_phases:
                raise GraphValidationError(
                    f"observations[{index}].phases must be a "
                    "non-empty array"
                )
            try:
                resolved_phases = frozenset(
                    BootPhase(item) for item in raw_phases
                )
            except (TypeError, ValueError) as error:
                raise GraphValidationError(
                    f"observations[{index}] has invalid phase: {error}"
                ) from error
            first_timestamp = _optional_timestamp(
                value.get("first_timestamp_seconds"),
                f"observations[{index}].first_timestamp_seconds",
            )
            last_timestamp = _optional_timestamp(
                value.get("last_timestamp_seconds"),
                f"observations[{index}].last_timestamp_seconds",
            )
            if (first_timestamp is None) != (last_timestamp is None):
                raise GraphValidationError(
                    f"observations[{index}] must provide both first "
                    "and last timestamps or neither"
                )
            if (
                first_timestamp is not None
                and last_timestamp is not None
                and first_timestamp > last_timestamp
            ):
                raise GraphValidationError(
                    f"observations[{index}] first timestamp is after "
                    "its last timestamp"
                )

        counts[node_id] = count
        contexts[node_id] = resolved_contexts
        phases[node_id] = resolved_phases
        if first_timestamp is not None and last_timestamp is not None:
            first_timestamps[node_id] = first_timestamp
            last_timestamps[node_id] = last_timestamp

    summary = raw.get("summary", {})
    trace_sha = raw.get("trace_sha256", [])
    ambiguous_raw = raw.get("ambiguous_symbols", {})
    unmatched_raw = raw.get("unmatched_symbols", {})
    if (
        not isinstance(summary, Mapping)
        or not isinstance(trace_sha, list)
        or not all(isinstance(item, str) for item in trace_sha)
    ):
        raise GraphValidationError("invalid boot observation summary")
    if not isinstance(ambiguous_raw, Mapping) or not isinstance(
        unmatched_raw, Mapping
    ):
        raise GraphValidationError("invalid boot observation diagnostics")

    if version == LEGACY_OBSERVATION_SCHEMA_VERSION:
        loader_ready_marker = DEFAULT_LOADER_READY_MARKER
        loader_ready_timestamps = tuple(None for _ in trace_sha)
    else:
        loader_ready = raw.get("loader_ready")
        if not isinstance(loader_ready, Mapping):
            raise GraphValidationError(
                "boot observations.loader_ready must be an object"
            )
        loader_ready_marker = loader_ready.get("marker")
        all_traces_observed = loader_ready.get(
            "all_traces_observed"
        )
        raw_loader_timestamps = loader_ready.get(
            "timestamps_seconds"
        )
        if (
            not isinstance(loader_ready_marker, str)
            or not loader_ready_marker
            or "\n" in loader_ready_marker
        ):
            raise GraphValidationError(
                "boot observations.loader_ready.marker is invalid"
            )
        if (
            not isinstance(raw_loader_timestamps, list)
            or len(raw_loader_timestamps) != len(trace_sha)
        ):
            raise GraphValidationError(
                "loader-ready timestamps must align with trace_sha256"
            )
        loader_ready_timestamps = tuple(
            _optional_timestamp(
                timestamp,
                f"loader_ready.timestamps_seconds[{index}]",
            )
            for index, timestamp in enumerate(raw_loader_timestamps)
        )
        derived_all_traces_observed = (
            bool(trace_sha)
            and all(
                timestamp is not None
                for timestamp in loader_ready_timestamps
            )
        )
        if (
            not isinstance(all_traces_observed, bool)
            or all_traces_observed
            != derived_all_traces_observed
        ):
            raise GraphValidationError(
                "boot observations.loader_ready.all_traces_observed "
                "does not match its per-trace timestamps"
            )

    return BootObservations(
        node_counts=counts,
        node_contexts=contexts,
        node_phases=phases,
        node_first_timestamps=first_timestamps,
        node_last_timestamps=last_timestamps,
        ambiguous_symbols={
            str(symbol): tuple(str(node) for node in nodes)
            for symbol, nodes in ambiguous_raw.items()
            if isinstance(nodes, list)
        },
        unmatched_symbols={
            str(symbol): int(count)
            for symbol, count in unmatched_raw.items()
            if isinstance(count, int)
        },
        trace_sha256=tuple(trace_sha),
        loader_ready_marker=loader_ready_marker,
        loader_ready_timestamps=loader_ready_timestamps,
        parsed_lines=int(summary.get("parsed_lines", 0)),
        ignored_lines=int(summary.get("ignored_lines", 0)),
    )


def _optional_timestamp(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise GraphValidationError(
            f"{field} must be a finite non-negative number or null"
        )
    return float(value)


def _parse_trace_line(
    line: str,
    *,
    loader_ready_marker: str,
) -> _FunctionTraceRecord | _LoaderReadyTraceRecord | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = _TRACE_PREFIX.search(line)
    if not match:
        return None
    timestamp = float(match.group("timestamp"))
    payload = match.group("payload").strip()
    for event_prefix in ("tracing_mark_write:", "trace_marker:"):
        if payload.startswith(event_prefix):
            marker = payload[len(event_prefix) :].strip()
            if marker == loader_ready_marker:
                return _LoaderReadyTraceRecord(timestamp)
            return None
    if payload.startswith("function:"):
        payload = payload[len("function:") :].lstrip()
    if payload.startswith("}"):
        return None
    symbol_match = _SYMBOL.match(payload)
    if not symbol_match:
        return None
    symbol = symbol_match.group(1)
    remainder = payload[len(symbol) :]
    if remainder.startswith(":"):
        # This is an arbitrary trace event, not a function-tracer record.
        return None
    flags = match.group("flags")
    if "N" in flags:
        context = ExecutionContext.NMI
    elif "h" in flags or "H" in flags:
        context = ExecutionContext.IRQ
    elif "s" in flags:
        context = ExecutionContext.ATOMIC
    elif "d" in flags or any(
        character.isdigit() and character != "0"
        for character in flags
    ):
        # ftrace's leading 'd' means local IRQs are disabled; a non-zero
        # preemption-depth suffix likewise forbids a sleeping module load.
        context = ExecutionContext.ATOMIC
    else:
        context = ExecutionContext.PROCESS
    return _FunctionTraceRecord(timestamp, symbol, context)

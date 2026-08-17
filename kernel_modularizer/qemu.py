"""Deterministic serial-console QEMU regression runner."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Any, Mapping

from .errors import GraphValidationError, SchemaVersionError
from .io import write_text_atomic


QEMU_SCENARIO_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class QemuStep:
    send: str
    expect: str
    timeout_seconds: float


@dataclass(frozen=True)
class QemuScenario:
    command: tuple[str, ...]
    boot_expect: str
    boot_timeout_seconds: float
    steps: tuple[QemuStep, ...]
    shutdown_command: str | None
    shutdown_timeout_seconds: float


@dataclass(frozen=True)
class QemuResult:
    passed: bool
    completed_steps: int
    elapsed_seconds: float
    returncode: int | None
    log: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "passed": self.passed,
            "completed_steps": self.completed_steps,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "returncode": self.returncode,
            "log_bytes": len(self.log.encode("utf-8")),
            "log_sha256": hashlib.sha256(
                self.log.encode("utf-8")
            ).hexdigest(),
        }


class _QemuWaitError(GraphValidationError):
    def __init__(self, message: str, log: str) -> None:
        super().__init__(message)
        self.log = log


def load_qemu_scenario(path: str | Path) -> QemuScenario:
    artifact = Path(path)
    try:
        raw = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphValidationError(
            f"cannot read QEMU scenario {artifact}: {error}"
        ) from error
    if not isinstance(raw, Mapping):
        raise GraphValidationError("QEMU scenario must be an object")
    if raw.get("schema_version") != QEMU_SCENARIO_SCHEMA_VERSION:
        raise SchemaVersionError(
            "unsupported QEMU scenario schema_version "
            f"{raw.get('schema_version')!r}"
        )
    command_raw = raw.get("command")
    if not isinstance(command_raw, list) or not all(
        isinstance(item, str) and item for item in command_raw
    ):
        raise GraphValidationError(
            "QEMU scenario.command must be a non-empty string array"
        )
    if not command_raw:
        raise GraphValidationError("QEMU scenario.command cannot be empty")
    boot_expect = raw.get("boot_expect")
    if not isinstance(boot_expect, str) or not boot_expect:
        raise GraphValidationError(
            "QEMU scenario.boot_expect must be a regex string"
        )
    steps_raw = raw.get("steps", [])
    if not isinstance(steps_raw, list):
        raise GraphValidationError("QEMU scenario.steps must be an array")
    steps = []
    for index, value in enumerate(steps_raw):
        if not isinstance(value, Mapping):
            raise GraphValidationError(f"steps[{index}] must be an object")
        send = value.get("send")
        expect = value.get("expect")
        timeout = value.get("timeout_seconds", 30)
        if not isinstance(send, str) or not isinstance(expect, str):
            raise GraphValidationError(
                f"steps[{index}] send/expect must be strings"
            )
        steps.append(
            QemuStep(
                send=send,
                expect=expect,
                timeout_seconds=_positive_timeout(
                    timeout, f"steps[{index}].timeout_seconds"
                ),
            )
        )
    shutdown = raw.get("shutdown_command", "poweroff -f")
    if shutdown is not None and not isinstance(shutdown, str):
        raise GraphValidationError(
            "QEMU scenario.shutdown_command must be string or null"
        )
    # Compile regexes during validation so malformed scenarios never launch.
    for context, pattern in [
        ("boot_expect", boot_expect),
        *((f"steps[{i}].expect", step.expect)
          for i, step in enumerate(steps)),
    ]:
        try:
            re.compile(pattern)
        except re.error as error:
            raise GraphValidationError(
                f"invalid {context} regex: {error}"
            ) from error
    return QemuScenario(
        command=tuple(command_raw),
        boot_expect=boot_expect,
        boot_timeout_seconds=_positive_timeout(
            raw.get("boot_timeout_seconds", 120),
            "boot_timeout_seconds",
        ),
        steps=tuple(steps),
        shutdown_command=shutdown,
        shutdown_timeout_seconds=_positive_timeout(
            raw.get("shutdown_timeout_seconds", 15),
            "shutdown_timeout_seconds",
        ),
    )


def run_qemu_scenario(
    scenario: QemuScenario,
    *,
    log_path: str | Path | None = None,
) -> QemuResult:
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            scenario.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except OSError as error:
        raise GraphValidationError(
            f"cannot launch QEMU command {scenario.command[0]!r}: {error}"
        ) from error
    assert process.stdout is not None
    assert process.stdin is not None

    console_chunks: queue.Queue[str | None] = queue.Queue()

    def read_console() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(
            errors="replace"
        )
        while True:
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                final_text = decoder.decode(b"", final=True)
                if final_text:
                    console_chunks.put(final_text)
                console_chunks.put(None)
                return
            text = decoder.decode(chunk)
            if text:
                console_chunks.put(text)

    reader = threading.Thread(target=read_console, daemon=True)
    reader.start()
    log = ""
    completed_steps = 0
    passed = False
    try:
        log = _wait_for_regex(
            console_chunks,
            log,
            scenario.boot_expect,
            scenario.boot_timeout_seconds,
            process,
        )
        for step in scenario.steps:
            process.stdin.write((step.send + "\n").encode())
            process.stdin.flush()
            start_offset = len(log)
            log = _wait_for_regex(
                console_chunks,
                log,
                step.expect,
                step.timeout_seconds,
                process,
                start_offset=start_offset,
            )
            completed_steps += 1
        passed = True
        if scenario.shutdown_command is not None and process.poll() is None:
            process.stdin.write(
                (scenario.shutdown_command + "\n").encode()
            )
            process.stdin.flush()
        try:
            process.wait(timeout=scenario.shutdown_timeout_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    except _QemuWaitError as error:
        log = error.log
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        reader.join(timeout=1)
        log = _drain(console_chunks, log)
        if log_path is not None:
            write_text_atomic(log_path, log)
        raise GraphValidationError(str(error)) from error
    finally:
        if process.stdin is not None:
            process.stdin.close()
        reader.join(timeout=1)
        log = _drain(console_chunks, log)
        process.stdout.close()

    if log_path is not None:
        write_text_atomic(log_path, log)
    return QemuResult(
        passed=passed,
        completed_steps=completed_steps,
        elapsed_seconds=time.monotonic() - started,
        returncode=process.returncode,
        log=log,
    )


def _wait_for_regex(
    console_chunks: "queue.Queue[str | None]",
    log: str,
    pattern: str,
    timeout: float,
    process: subprocess.Popen[bytes],
    *,
    start_offset: int = 0,
) -> str:
    expression = re.compile(pattern, re.MULTILINE)
    deadline = time.monotonic() + timeout
    while True:
        if expression.search(log, start_offset):
            return log
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _QemuWaitError(
                f"QEMU console timed out waiting for /{pattern}/", log
            )
        try:
            chunk = console_chunks.get(timeout=min(remaining, 0.25))
        except queue.Empty:
            if process.poll() is not None:
                raise _QemuWaitError(
                    f"QEMU exited with status {process.returncode} "
                    f"before /{pattern}/",
                    log,
                )
            continue
        if chunk is None:
            raise _QemuWaitError(
                f"QEMU console closed before /{pattern}/", log
            )
        log += chunk


def _drain(
    console_chunks: "queue.Queue[str | None]", log: str
) -> str:
    while True:
        try:
            chunk = console_chunks.get_nowait()
        except queue.Empty:
            return log
        if chunk is not None:
            log += chunk


def _positive_timeout(value: Any, context: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise GraphValidationError(f"{context} must be a number")
    result = float(value)
    if result <= 0 or result > 3600:
        raise GraphValidationError(
            f"{context} must be in the range (0, 3600]"
        )
    return result

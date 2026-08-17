"""Content-addressed manifests for reproducible analysis stages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import GraphValidationError


MANIFEST_SCHEMA_VERSION = 1


def sha256_file(path: str | Path) -> str:
    artifact = Path(path)
    digest = hashlib.sha256()
    try:
        with artifact.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise GraphValidationError(
            f"cannot hash artifact {artifact}: {error}"
        ) from error
    return digest.hexdigest()


def create_stage_manifest(
    stage: str,
    *,
    inputs: Mapping[str, str | Path],
    parameters: Mapping[str, Any],
    tools: Mapping[str, str],
    outputs: Mapping[str, str | Path] | None = None,
) -> dict[str, Any]:
    if not stage:
        raise GraphValidationError("manifest stage cannot be empty")
    input_records = _artifact_records(inputs)
    output_records = _artifact_records(outputs or {})
    reproducible_core = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "stage": stage,
        "inputs": [
            {
                "label": item["label"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in input_records
        ],
        "parameters": _json_value(parameters),
        "tools": dict(sorted(tools.items())),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            reproducible_core,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **reproducible_core,
        "fingerprint": fingerprint,
        "inputs": input_records,
        "outputs": output_records,
    }


def verify_stage_manifest(
    manifest: Mapping[str, Any],
) -> list[str]:
    issues = []
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        issues.append("unsupported manifest schema_version")
        return issues
    for category in ("inputs", "outputs"):
        records = manifest.get(category, [])
        if not isinstance(records, list):
            issues.append(f"{category} must be an array")
            continue
        for record in records:
            if not isinstance(record, Mapping):
                issues.append(f"{category} contains a non-object record")
                continue
            path = record.get("path")
            label = record.get("label", "<unknown>")
            if not isinstance(path, str):
                issues.append(f"{category} {label}: path is missing")
                continue
            artifact = Path(path)
            if not artifact.is_file():
                issues.append(f"{category} {label}: file is missing: {path}")
                continue
            actual_size = artifact.stat().st_size
            if actual_size != record.get("size_bytes"):
                issues.append(
                    f"{category} {label}: size changed "
                    f"from {record.get('size_bytes')} to {actual_size}"
                )
                continue
            actual_digest = sha256_file(artifact)
            if actual_digest != record.get("sha256"):
                issues.append(f"{category} {label}: sha256 changed")
    return issues


def _artifact_records(
    artifacts: Mapping[str, str | Path],
) -> list[dict[str, Any]]:
    records = []
    for label, raw_path in sorted(artifacts.items()):
        if not label:
            raise GraphValidationError("artifact label cannot be empty")
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise GraphValidationError(
                f"manifest artifact does not exist: {path}"
            )
        records.append(
            {
                "label": label,
                "path": str(path),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def _json_value(value: Any) -> Any:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        return json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise GraphValidationError(
            f"manifest value is not JSON serializable: {error}"
        ) from error

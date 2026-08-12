"""Freeze one validated Pixel RAG artifact into the offline viewer build.

The bridge deliberately embeds the engine artifact instead of translating evidence in Python.
That keeps one evidence document, lets the engine's existing closed-schema and semantic validator
remain authoritative, and gives the TypeScript viewer a static import with no runtime fetch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from moodboard.pixel_rag import (
    ARTIFACT_SCHEMA,
    PixelRagError,
    read_pixel_rag_artifact,
    validate_pixel_rag_artifact,
)

BRIDGE_FORMAT = "moodboard.viewer-pixel-rag-bridge.v1"
GENERATOR_REVISION = "moodboard.pixel-rag-viewer-bridge.v1"
_MAX_BRIDGE_BYTES = 16 * 1024 * 1024
_TOP_LEVEL_KEYS = frozenset({"artifact", "format_version", "generator_revision", "input", "state"})
_INPUT_KEYS = frozenset(
    {"artifact_id", "byte_size", "canonical_sha256", "schema_version", "sha256"}
)

__all__ = [
    "BRIDGE_FORMAT",
    "GENERATOR_REVISION",
    "PixelRagViewerBridgeError",
    "compile_viewer_pixel_rag_bridge",
    "fallback_viewer_pixel_rag_bridge",
    "read_viewer_pixel_rag_bridge",
    "validate_viewer_pixel_rag_bridge",
    "write_viewer_pixel_rag_bridge",
]


class PixelRagViewerBridgeError(PixelRagError):
    """A viewer bridge cannot preserve the engine artifact's frozen identity."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token!r}")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PixelRagViewerBridgeError(f"{label} must be an object")
    return value


def _closed(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        detail = f"unknown keys {unknown}" if unknown else f"missing keys {missing}"
        raise PixelRagViewerBridgeError(f"{label} is not closed: {detail}")


def _digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PixelRagViewerBridgeError(f"{label} must be a lowercase 64-hex digest")
    return value


def fallback_viewer_pixel_rag_bridge() -> dict[str, Any]:
    """Return the only accepted sentinel for the presentation-owned fixture fallback."""

    return {
        "artifact": None,
        "format_version": BRIDGE_FORMAT,
        "generator_revision": GENERATOR_REVISION,
        "input": None,
        "state": "fallback",
    }


def compile_viewer_pixel_rag_bridge(source: Path) -> dict[str, Any]:
    """Validate and pin one canonical engine artifact for static viewer import."""

    path = Path(source)
    artifact = read_pixel_rag_artifact(path)
    raw = path.read_bytes()
    canonical = _canonical_bytes(artifact)
    if raw != canonical:
        raise PixelRagViewerBridgeError(
            "Pixel RAG viewer input must use the engine's canonical JSON encoding"
        )
    input_sha256 = hashlib.sha256(raw).hexdigest()
    bridge: dict[str, Any] = {
        "artifact": artifact,
        "format_version": BRIDGE_FORMAT,
        "generator_revision": GENERATOR_REVISION,
        "input": {
            "artifact_id": artifact["artifact_id"],
            "byte_size": len(raw),
            "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
            "schema_version": artifact["schema_version"],
            "sha256": input_sha256,
        },
        "state": "projected",
    }
    validate_viewer_pixel_rag_bridge(bridge)
    return bridge


def validate_viewer_pixel_rag_bridge(value: Mapping[str, Any]) -> None:
    """Fail closed on bridge shape, engine schema, or any pinned identity drift."""

    bridge = _mapping(value, "Pixel RAG viewer bridge")
    _closed(bridge, _TOP_LEVEL_KEYS, "Pixel RAG viewer bridge")
    if bridge["format_version"] != BRIDGE_FORMAT:
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge format_version drifted")
    if bridge["generator_revision"] != GENERATOR_REVISION:
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge generator_revision drifted")

    state = bridge["state"]
    if state == "fallback":
        if bridge["input"] is not None or bridge["artifact"] is not None:
            raise PixelRagViewerBridgeError("fallback bridge cannot carry input or artifact data")
        return
    if state != "projected":
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge state is unsupported")

    input_identity = _mapping(bridge["input"], "Pixel RAG viewer bridge input")
    _closed(input_identity, _INPUT_KEYS, "Pixel RAG viewer bridge input")
    artifact = _mapping(bridge["artifact"], "Pixel RAG viewer bridge artifact")

    # This is the engine's complete schema + semantic validator, not a viewer-side substitute.
    validate_pixel_rag_artifact(artifact)
    canonical = _canonical_bytes(artifact)
    canonical_sha256 = hashlib.sha256(canonical).hexdigest()
    if input_identity["schema_version"] != ARTIFACT_SCHEMA:
        raise PixelRagViewerBridgeError("Pixel RAG viewer input schema_version drifted")
    if input_identity["artifact_id"] != artifact["artifact_id"]:
        raise PixelRagViewerBridgeError("Pixel RAG viewer input artifact_id drifted")
    if _digest(input_identity["canonical_sha256"], "input canonical_sha256") != canonical_sha256:
        raise PixelRagViewerBridgeError("Pixel RAG viewer input canonical_sha256 drifted")
    if _digest(input_identity["sha256"], "input sha256") != canonical_sha256:
        raise PixelRagViewerBridgeError(
            "Pixel RAG viewer input sha256 drifted from canonical bytes"
        )
    byte_size = input_identity["byte_size"]
    if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size != len(canonical):
        raise PixelRagViewerBridgeError("Pixel RAG viewer input byte_size drifted")


def write_viewer_pixel_rag_bridge(value: Mapping[str, Any], destination: Path) -> None:
    """Atomically replace the generated viewer asset with canonical bridge JSON."""

    validate_viewer_pixel_rag_bridge(value)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def read_viewer_pixel_rag_bridge(path: Path) -> dict[str, Any]:
    """Read a canonical generated asset and repeat every build-time drift check."""

    source = Path(path)
    if not source.is_file():
        raise PixelRagViewerBridgeError(f"Pixel RAG viewer bridge is missing: {source}")
    if source.stat().st_size > _MAX_BRIDGE_BYTES:
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge exceeds the byte ceiling")
    raw = source.read_bytes()
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PixelRagViewerBridgeError(
            f"Pixel RAG viewer bridge is invalid JSON: {error}"
        ) from error
    bridge = dict(_mapping(value, "Pixel RAG viewer bridge"))
    validate_viewer_pixel_rag_bridge(bridge)
    if raw != _canonical_bytes(bridge):
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge is not canonical JSON")
    return bridge


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m moodboard.pixel_rag_viewer",
        description="Embed one validated Pixel RAG artifact into the offline viewer build.",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--input", type=Path)
    action.add_argument("--check", type=Path)
    parser.add_argument("--write", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.check is not None:
        if arguments.write is not None:
            raise SystemExit("BLOCKED: --write cannot be combined with --check")
        bridge = read_viewer_pixel_rag_bridge(arguments.check)
    else:
        if arguments.write is None:
            raise SystemExit("BLOCKED: --input requires --write")
        try:
            bridge = compile_viewer_pixel_rag_bridge(arguments.input)
            write_viewer_pixel_rag_bridge(bridge, arguments.write)
        except (OSError, PixelRagError, ValueError) as error:
            raise SystemExit(f"BLOCKED: {error}") from error
    print(
        json.dumps(
            {
                "artifact_id": bridge["input"]["artifact_id"] if bridge["input"] else None,
                "bridge": str((arguments.check or arguments.write).resolve()),
                "state": bridge["state"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

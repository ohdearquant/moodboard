"""Compile one governed, intent-scoped Pixel RAG demonstration artifact.

The compiler is deliberately downstream of retrieval.  It does not call a model and it never
invents a score: a closed measurements document records the Khive search rows and the exact
Lattice descriptor that produced them.  This module binds those rows to the reviewed demo
manifest, applies the two declared intent filters, projects exact-cosine order inside each
corpus, and freezes the resulting control plans as a self-identifying JSON artifact.

Image generation remains an explicit external boundary.  Optional output files are hashed and
described, but they are not called Khive BlobStore assets until a matching record id and BLAKE3
ContentRef are supplied.  Edit-verification metrics likewise appear only when they are present
in the measured input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import uuid
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from blake3 import blake3
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from PIL import Image, UnidentifiedImageError

from moodboard.encoders import KhiveProtocolError, VisualDescriptor

MEASUREMENTS_SCHEMA = "moodboard.pixel-rag-measurements.v1"
ARTIFACT_SCHEMA = "moodboard.pixel-rag-artifact.v1"
COMPILER_REVISION = "moodboard.pixel-rag-compiler.v2"
MEASUREMENTS_SCHEMA_PATH = (
    Path(__file__).with_name("schema") / "pixel_rag_measurements_v1.schema.json"
)
ARTIFACT_SCHEMA_PATH = Path(__file__).with_name("schema") / "pixel_rag_artifact_v1.schema.json"
DEMO_MANIFEST_SCHEMA_PATH = Path(__file__).with_name("schema") / "demo_manifest_v1.schema.json"
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_OUTPUT_BYTES = 24 * 1024 * 1024
_MAX_IMAGE_SIDE = 8192
_MAX_IMAGE_PIXELS = 40_000_000
_HEX = frozenset("0123456789abcdef")
_INTENTS = ("local_replace", "global_restyle")
_EVIDENCE_BINDING_KINDS = (
    "evaluation_preregistration",
    "intent_freeze",
    "verification_summary",
)
_RETRIEVAL_METRIC_IDS = ("precision_at_3", "ndcg_at_5", "mrr", "recall_at_5")
_STRUCTURAL_ROUTING_INTERPRETATION = "structural_routing_control_not_learned_retrieval_quality"
_LEGACY_CONTRACTS = {
    "artifact_schema_sha256": "a317962da489b7471866286dc5bfab9429fe5f9caed1a9e3c2e92259a3a7fbd5",
    "measurements_schema_sha256": (
        "475d95a387765167f1e7109c9bc8cc549abc3e4bb12c4446fbf292eb4106965d"
    ),
    "source_manifest_schema_sha256": (
        "212d49d8a18a42ab95566417cb2b943efd62436177cd55c91a60cb6051f545c5"
    ),
}
_COLLECTION_BY_INTENT = {
    "local_replace": "fruit-lemon",
    "global_restyle": "style-claude-lorrain",
}
_MIME_BY_FORMAT = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}

__all__ = [
    "ARTIFACT_SCHEMA",
    "COMPILER_REVISION",
    "ExternalOutput",
    "MEASUREMENTS_SCHEMA",
    "PixelRagError",
    "compile_pixel_rag_artifact",
    "read_pixel_rag_artifact",
    "validate_pixel_rag_artifact",
    "write_pixel_rag_artifact",
]


class PixelRagError(ValueError):
    """An input cannot support the claimed Pixel RAG evidence artifact."""


@dataclass(frozen=True, slots=True)
class ExternalOutput:
    """One external generator result, optionally already registered in Khive."""

    path: Path
    khive_record_id: str | None = None
    expected_content_ref: str | None = None

    def __post_init__(self) -> None:
        if (self.khive_record_id is None) != (self.expected_content_ref is None):
            raise ValueError("khive_record_id and expected_content_ref must be supplied together")
        if self.khive_record_id is not None:
            _uuid(self.khive_record_id, "external output khive_record_id")
            _digest(self.expected_content_ref, "external output expected_content_ref")


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token!r}")


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _load_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    source = Path(path)
    if not source.is_file():
        raise PixelRagError(f"{label} is missing or not a regular file: {source}")
    size = source.stat().st_size
    if size > _MAX_JSON_BYTES:
        raise PixelRagError(f"{label} exceeds the {_MAX_JSON_BYTES}-byte ceiling")
    raw = source.read_bytes()
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise PixelRagError(f"{label} is not strict UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise PixelRagError(f"{label} must be a JSON object")
    return value, raw


def _schema(path: Path) -> dict[str, Any]:
    try:
        schema = json.loads(path.read_bytes())
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, SchemaError, ValueError) as error:
        raise PixelRagError(f"Pixel RAG schema is unavailable or invalid: {path}") from error
    return schema


def _validate_schema(value: Mapping[str, Any], path: Path, *, label: str) -> None:
    errors = sorted(
        Draft202012Validator(_schema(path)).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise PixelRagError(
            f"{label} violates its closed schema at {location}: {errors[0].message}"
        )


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _HEX:
        raise PixelRagError(f"{field} must be 64 lowercase hexadecimal characters")
    return value


def _uuid(value: Any, field: str) -> str:
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except ValueError as error:
        raise PixelRagError(f"{field} must be a canonical UUID") from error
    if parsed is None or str(parsed) != value:
        raise PixelRagError(f"{field} must be a canonical UUID")
    return value


def _finite(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PixelRagError(f"{field} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
        raise PixelRagError(f"{field} must be within [{minimum}, {maximum}]")
    return numeric


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PixelRagError(f"{field} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise PixelRagError(f"{field} must be an RFC 3339 UTC timestamp") from error
    if parsed.tzinfo != UTC:
        raise PixelRagError(f"{field} must be UTC")
    return value


def _validated_descriptor(value: Any) -> tuple[VisualDescriptor, dict[str, Any]]:
    try:
        descriptor = VisualDescriptor.parse(value)
    except KhiveProtocolError as error:
        message = str(error)
        if "inference" in message:
            message = f"Pixel RAG requires Lattice inference 0.9.0: {message}"
        raise PixelRagError(message) from error
    return descriptor, descriptor.to_json_dict()


def _manifest_assets(
    manifest_path: Path,
) -> tuple[dict[str, Any], bytes, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    manifest, raw = _load_json(manifest_path, label="demo manifest")
    _validate_schema(manifest, DEMO_MANIFEST_SCHEMA_PATH, label="demo manifest")
    sidecar = Path(manifest_path).with_name("manifest.sha256")
    measured_manifest_sha = hashlib.sha256(raw).hexdigest()
    if sidecar.exists():
        expected_line = f"{measured_manifest_sha}  manifest.json\n"
        try:
            sidecar_text = sidecar.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise PixelRagError("manifest.sha256 is not readable UTF-8") from error
        if sidecar_text != expected_line:
            raise PixelRagError("manifest.sha256 does not bind the supplied manifest bytes")

    root = Path(manifest_path).resolve().parent
    by_id: dict[str, dict[str, Any]] = {}
    by_content_ref: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(manifest["assets"]):
        asset_id = row["asset_id"]
        content_ref = row["khive_content_ref"]
        if asset_id in by_id or content_ref in by_content_ref:
            raise PixelRagError("demo manifest repeats an asset id or Khive ContentRef")
        candidate = root / row["local_path"]
        if candidate.is_symlink():
            raise PixelRagError(f"demo manifest asset {asset_id!r} must not be a symlink")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError) as error:
            raise PixelRagError(
                f"demo manifest asset {index} is missing or escapes its run directory"
            ) from error
        if not resolved.is_file():
            raise PixelRagError(f"demo manifest asset {asset_id!r} is not a regular file")
        data = resolved.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise PixelRagError(f"demo manifest asset {asset_id!r} SHA-256 mismatch")
        if blake3(data).hexdigest() != content_ref:
            raise PixelRagError(f"demo manifest asset {asset_id!r} Khive ContentRef mismatch")
        if len(data) != row["byte_size"]:
            raise PixelRagError(f"demo manifest asset {asset_id!r} byte size mismatch")
        by_id[asset_id] = row
        by_content_ref[content_ref] = row
    return manifest, raw, by_id, by_content_ref


def _validated_measurements(
    measurements_path: Path,
) -> tuple[dict[str, Any], bytes, VisualDescriptor, dict[str, Any]]:
    measurements, raw = _load_json(measurements_path, label="Pixel RAG measurements")
    _validate_schema(
        measurements,
        MEASUREMENTS_SCHEMA_PATH,
        label="Pixel RAG measurements",
    )
    _timestamp(measurements["generated_at"], "measurements.generated_at")
    descriptor, descriptor_json = _validated_descriptor(measurements["descriptor"])
    intents = measurements["intents"]
    if [intent["id"] for intent in intents] != list(_INTENTS):
        raise PixelRagError("measurements intents must be local_replace then global_restyle")
    if intents[0]["namespace"] == intents[1]["namespace"]:
        raise PixelRagError("the two intent retrieval namespaces must be distinct")
    for intent in intents:
        postprocess = intent["generator"].get("deterministic_postprocess")
        if (
            postprocess is not None
            and postprocess["method"] == "restore_source_pixels_outside_confirmed_region"
        ):
            raise PixelRagError("deterministic compositor method overclaims exact source pixels")
        previous = math.inf
        record_ids: set[str] = set()
        content_refs: set[str] = set()
        for index, hit in enumerate(intent["hits"], 1):
            if hit["rank"] != index:
                raise PixelRagError(f"{intent['id']} hits must have contiguous source ranks")
            score = _finite(hit["score"], f"{intent['id']} hit {index} score", -1.0, 1.0)
            if score > previous:
                raise PixelRagError(f"{intent['id']} hits are not in non-increasing score order")
            previous = score
            record_id = _uuid(hit["record_id"], f"{intent['id']} hit {index} record_id")
            content_ref = _digest(hit["content_ref"], f"{intent['id']} hit {index} content_ref")
            if record_id in record_ids or content_ref in content_refs:
                raise PixelRagError(f"{intent['id']} repeats a search hit")
            record_ids.add(record_id)
            content_refs.add(content_ref)
    return measurements, raw, descriptor, descriptor_json


def _evidence_sha256(measurements: Mapping[str, Any], *, measurements_path: Path) -> dict[str, str]:
    bindings = measurements.get("evidence_bindings")
    if bindings is None:
        return {}
    if [binding["kind"] for binding in bindings] != list(_EVIDENCE_BINDING_KINDS):
        raise PixelRagError(
            "evidence bindings must be preregistration, intent freeze, then verification summary"
        )
    identities: dict[str, str] = {}
    root = measurements_path.resolve().parent
    for binding in bindings:
        kind = binding["kind"]
        source = Path(binding["path"])
        if not source.is_absolute():
            source = root / source
        if source.is_symlink():
            raise PixelRagError(f"evidence binding {kind} must not be a symlink")
        _document, raw = _load_json(source, label=f"evidence binding {kind}")
        measured = hashlib.sha256(raw).hexdigest()
        expected = _digest(binding["sha256"], f"evidence binding {kind} sha256")
        if measured != expected:
            raise PixelRagError(f"evidence binding {kind} SHA-256 mismatch")
        identities[kind] = measured
    return identities


def _manifest_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": row["asset_id"],
        "artist": row["artist"],
        "collection": row["collection"],
        "image": dict(row["image"]),
        "license": dict(row["license"]),
        "sha256": row["sha256"],
        "source_page_url": row["source_page_url"],
        "title": row["title"],
    }


def _metric_rows(
    *,
    evidence_status: str,
    filtered: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    relevance: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if relevance is None:
        reason = "explicit relevance judgments were not supplied"
        return [
            {
                "id": metric_id,
                "reason": reason,
                "source": evidence_status,
                "state": "not_computed",
                "value": None,
            }
            for metric_id in _RETRIEVAL_METRIC_IDS
        ]

    gains: dict[str, int] = {}
    allowed = {hit["content_ref"] for hit, _row in filtered}
    for index, judgment in enumerate(relevance):
        content_ref = _digest(judgment["content_ref"], f"relevance_judgments[{index}].content_ref")
        gain = judgment["gain"]
        if content_ref not in allowed:
            raise PixelRagError("a relevance judgment is outside the intent-filtered corpus")
        if content_ref in gains:
            raise PixelRagError("relevance judgments repeat a candidate")
        gains[content_ref] = gain

    top3 = filtered[:3]
    precision = sum(gains.get(hit["content_ref"], 0) > 0 for hit, _row in top3) / 3.0
    retrieved_gains = [gains.get(hit["content_ref"], 0) for hit, _row in filtered[:5]]
    dcg = sum((2**gain - 1) / math.log2(rank + 1) for rank, gain in enumerate(retrieved_gains, 1))
    ideal_gains = sorted(gains.values(), reverse=True)[:5]
    ideal_dcg = sum((2**gain - 1) / math.log2(rank + 1) for rank, gain in enumerate(ideal_gains, 1))
    ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
    first_relevant_rank = next(
        (
            rank
            for rank, (hit, _row) in enumerate(filtered, 1)
            if gains.get(hit["content_ref"], 0) > 0
        ),
        None,
    )
    total_relevant = sum(gain > 0 for gain in gains.values())
    relevant_retrieved = sum(gains.get(hit["content_ref"], 0) > 0 for hit, _row in filtered[:5])
    return [
        {
            "id": "precision_at_3",
            "judged_relevant": sum(gains.get(hit["content_ref"], 0) > 0 for hit, _row in top3),
            "k": 3,
            "source": evidence_status,
            "state": "computed",
            "value": precision,
        },
        {
            "id": "ndcg_at_5",
            "gain_scale": [0, 1, 2, 3],
            "k": 5,
            "source": evidence_status,
            "state": "computed",
            "value": ndcg,
        },
        {
            "id": "mrr",
            "source": evidence_status,
            "state": "computed",
            "value": 1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0,
        },
        {
            "id": "recall_at_5",
            "k": 5,
            "relevant_retrieved": relevant_retrieved,
            "source": evidence_status,
            "state": "computed",
            "total_relevant": total_relevant,
            "value": relevant_retrieved / total_relevant if total_relevant else 0.0,
        },
    ]


def _raw_diagnostics(
    *,
    evidence_status: str,
    raw: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    relevance: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "exact_score_order": [
            {
                "asset_id": row["asset_id"],
                "content_ref": hit["content_ref"],
                "score": float(hit["score"]),
                "source_search_rank": hit["rank"],
            }
            for hit, row in raw
        ],
        "gate": "ungated",
        "interpretation": ("experimental_qwen_visual_embedding_geometry_not_probability"),
        "metrics": _metric_rows(
            evidence_status=evidence_status,
            filtered=raw,
            relevance=relevance,
        ),
        "probabilistic_interpretation": False,
    }


def _experimental_diagnostics(
    measurements: Mapping[str, Any],
) -> dict[str, Any] | None:
    value = measurements.get("experimental_visual_embedding_diagnostics")
    if value is None:
        return None
    diagnostics = dict(value)
    local = diagnostics["local_output_region_intent_alignment"]
    local_margin = float(local["mean_lemon_cosine"]) - float(local["mean_apple_cosine"])
    if not math.isclose(
        float(local["mean_lemon_minus_apple_margin"]),
        local_margin,
        rel_tol=0.0,
        abs_tol=1e-15,
    ):
        raise PixelRagError("experimental lemon-minus-apple margin is inconsistent")
    affinity = diagnostics["restyle_style_affinity"]
    style_margin = float(affinity["claude_centroid_cosine"]) - float(
        affinity["vangogh_centroid_cosine"]
    )
    if not math.isclose(
        float(affinity["claude_minus_vangogh_margin"]),
        style_margin,
        rel_tol=0.0,
        abs_tol=1e-15,
    ):
        raise PixelRagError("experimental Claude-minus-Van-Gogh margin is inconsistent")
    return diagnostics


def _verification(value: Sequence[Mapping[str, Any]], *, evidence_status: str) -> dict[str, Any]:
    metrics: list[dict[str, Any]] = []
    for index, source in enumerate(value):
        measured = _finite(source["value"], f"verification_metrics[{index}].value", -1.0, 1.0)
        threshold = _finite(
            source["threshold"], f"verification_metrics[{index}].threshold", -1.0, 1.0
        )
        operator = source["operator"]
        passed = (
            measured >= threshold if operator == "greater_than_or_equal" else measured <= threshold
        )
        metrics.append(
            {
                **(
                    {"method_revision": source["method_revision"]}
                    if "method_revision" in source
                    else {"evidence_field": dict(source["evidence_field"])}
                ),
                "id": source["id"],
                "operator": operator,
                "passed": passed,
                "source": evidence_status,
                "threshold": threshold,
                "value": measured,
            }
        )
    if not metrics:
        return {"metrics": [], "status": "not_run"}
    return {
        "metrics": metrics,
        "status": "passed" if all(metric["passed"] for metric in metrics) else "failed",
    }


def _external_output(
    external: ExternalOutput | None,
    *,
    generator: Mapping[str, Any],
    rollback_record_id: str,
    rollback_content_ref: str,
) -> dict[str, Any] | None:
    if external is None:
        return None
    path = Path(external.path)
    if not path.is_file() or path.is_symlink():
        raise PixelRagError(f"external output is missing or not a regular file: {path}")
    size = path.stat().st_size
    if size <= 0 or size > _MAX_OUTPUT_BYTES:
        raise PixelRagError("external output violates the encoded-byte ceiling")
    data = path.read_bytes()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image_format = image.format
                width, height = image.size
                if (
                    image_format not in _MIME_BY_FORMAT
                    or width <= 0
                    or height <= 0
                    or width > _MAX_IMAGE_SIDE
                    or height > _MAX_IMAGE_SIDE
                    or width * height > _MAX_IMAGE_PIXELS
                ):
                    raise PixelRagError("external output violates the image contract")
                image.verify()
    except (
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise PixelRagError("external output is not a bounded valid image") from error
    content_ref = blake3(data).hexdigest()
    if external.expected_content_ref is not None and external.expected_content_ref != content_ref:
        raise PixelRagError("external output does not match its expected Khive content_ref")
    if external.khive_record_id is None:
        registration: dict[str, Any] = {"state": "not_registered"}
    else:
        registration = {
            "content_ref": content_ref,
            "record_id": external.khive_record_id,
            "state": "registered",
        }
    return {
        "blob_store_registration": registration,
        "byte_size": size,
        # Local filesystem paths are deliberately not serialized into a portable evidence
        # artifact. Bytes remain discoverable by their SHA-256 / ContentRef identities.
        "external_location": {"kind": "identity_only"},
        "generator": dict(generator),
        "image": {"format": image_format, "height": height, "width": width},
        "media_type": _MIME_BY_FORMAT[image_format],
        "output_content_ref": content_ref,
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "rollback": {
            "content_ref": rollback_content_ref,
            "record_id": rollback_record_id,
        },
        "state": "precomputed_external_output",
    }


def _require_distinct_rejected_outputs(
    intent_id: str,
    selected_output: Mapping[str, Any] | None,
    negative_output_evidence: Sequence[Mapping[str, Any]],
) -> None:
    if selected_output is None:
        return
    for index, negative in enumerate(negative_output_evidence):
        rejected_output = negative["output"]
        if (
            rejected_output["output_sha256"] == selected_output["output_sha256"]
            or rejected_output["output_content_ref"] == selected_output["output_content_ref"]
        ):
            raise PixelRagError(
                f"{intent_id} rejected output {index} must be byte/identity-distinct "
                "from the selected output"
            )


def _stages(intent_id: str) -> list[dict[str, str]]:
    region_detail = (
        "Use only the operator-confirmed normalized tree rectangle."
        if intent_id == "local_replace"
        else "Use the immutable complete source frame; no local mask is asserted."
    )
    return [
        {
            "detail": (
                "Apply the intent metadata gate, then project exact Khive/Lattice cosine order."
            ),
            "executor": "khive_visual_retrieval",
            "id": "retrieval",
        },
        {
            "detail": region_detail,
            "executor": "human_confirmation" if intent_id == "local_replace" else "intent_router",
            "id": "region",
        },
        {
            "detail": "Bind prompt, protected content, evidence ContentRefs, and edit constraints.",
            "executor": "deterministic_control_plan",
            "id": "conditioning",
        },
        {
            "detail": "Execute outside Moodboard/Khive; the provider and mode remain explicit.",
            "executor": "external_generator",
            "id": "external_generation",
        },
        {
            "detail": (
                "Record only explicitly supplied retrieval and edit-verification measurements."
            ),
            "executor": "recorded_verifier",
            "id": "verification",
        },
        {
            "detail": (
                "Register new bytes as a fresh Khive asset when a matching record is supplied."
            ),
            "executor": "khive_blob_store",
            "id": "immutable_output",
        },
        {
            "detail": (
                "Rollback selects the immutable source ContentRef; source bytes are never "
                "overwritten."
            ),
            "executor": "content_ref_pointer",
            "id": "rollback",
        },
    ]


def _intent_artifact(
    source: Mapping[str, Any],
    measurement: Mapping[str, Any],
    *,
    by_content_ref: Mapping[str, dict[str, Any]],
    descriptor: VisualDescriptor,
    evidence_status: str,
    historical_outputs: Sequence[ExternalOutput],
    output: ExternalOutput | None,
    source_record: Mapping[str, Any],
) -> dict[str, Any]:
    intent_id = measurement["id"]
    collection = _COLLECTION_BY_INTENT[intent_id]
    raw: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for hit in measurement["hits"]:
        row = by_content_ref.get(hit["content_ref"])
        if row is None:
            raise PixelRagError(
                f"{intent_id} search hit ContentRef is not present in the governed manifest"
            )
        raw.append((hit, row))
    filtered = [(hit, row) for hit, row in raw if row["collection"] == collection]
    expected_refs = {
        content_ref
        for content_ref, row in by_content_ref.items()
        if row["collection"] == collection
    }
    filtered_refs = {hit["content_ref"] for hit, _row in filtered}
    if filtered_refs != expected_refs:
        raise PixelRagError(
            f"{intent_id} measured search rows do not cover the complete governed target corpus"
        )
    filtered.sort(key=lambda item: (-float(item[0]["score"]), int(item[0]["rank"])))
    relevance = measurement["relevance_judgments"]
    if relevance is not None:
        raw_refs = {hit["content_ref"] for hit, _row in raw}
        seen_judgments: set[str] = set()
        for index, judgment in enumerate(relevance):
            content_ref = _digest(
                judgment["content_ref"], f"relevance_judgments[{index}].content_ref"
            )
            if content_ref not in raw_refs:
                raise PixelRagError("a relevance judgment is outside the raw retrieval")
            if content_ref in seen_judgments:
                raise PixelRagError("relevance judgments repeat a candidate")
            seen_judgments.add(content_ref)
        if evidence_status == "measured_run" and seen_judgments != raw_refs:
            raise PixelRagError(
                "measured retrieval metrics require relevance judgments for every raw hit"
            )
        filtered_refs_for_judgments = {hit["content_ref"] for hit, _row in filtered}
        routed_relevance: Sequence[Mapping[str, Any]] | None = [
            judgment
            for judgment in relevance
            if judgment["content_ref"] in filtered_refs_for_judgments
        ]
    else:
        routed_relevance = None
    exact_order = [
        {
            "asset_id": row["asset_id"],
            "content_ref": hit["content_ref"],
            "score": float(hit["score"]),
            "source_search_rank": hit["rank"],
        }
        for hit, row in filtered
    ]
    ranked_evidence = []
    for rank, (hit, row) in enumerate(filtered[:3], 1):
        ranked_evidence.append(
            {
                **_manifest_identity(row),
                "khive": {
                    "content_ref": hit["content_ref"],
                    "record_id": hit["record_id"],
                },
                "rank": rank,
                "score": {
                    "descriptor_fingerprint": descriptor.fingerprint,
                    "kind": "cosine_similarity",
                    "value": float(hit["score"]),
                },
                "source_search_rank": hit["rank"],
            }
        )

    region = measurement["region"]
    if intent_id == "local_replace":
        if region is None or region["confirmation"]["method"] not in {
            "human_confirmed",
            "evidence_bound_human_confirmation",
        }:
            raise PixelRagError("local_replace requires a human-confirmed normalized region")
        x = _finite(region["x"], "local_replace region.x", 0.0, 1.0)
        y = _finite(region["y"], "local_replace region.y", 0.0, 1.0)
        width = _finite(region["width"], "local_replace region.width", 0.0, 1.0)
        height = _finite(region["height"], "local_replace region.height", 0.0, 1.0)
        if width <= 0 or height <= 0 or x + width > 1.0 or y + height > 1.0:
            raise PixelRagError("local_replace normalized region escapes the source frame")
        if region["confirmation"]["method"] == "human_confirmed":
            _timestamp(region["confirmation"]["confirmed_at"], "region confirmation time")
        query_granularity = "human_confirmed_region"
        protected = [
            "all pixels outside the confirmed region",
            "camera geometry",
            "water and ground",
            "scene lighting",
        ]
        target = "mature lemon tree"
    else:
        if region is not None:
            raise PixelRagError("global_restyle must use the whole frame without a local region")
        if measurement["query_record"]["content_ref"] != source_record["content_ref"]:
            raise PixelRagError("global_restyle query must be the immutable source asset")
        if measurement["query_record"]["sha256"] != source["sha256"]:
            raise PixelRagError("global_restyle query SHA-256 must bind the source bytes")
        query_granularity = "whole_frame"
        protected = ["source composition", "subject layout", "relative object positions"]
        target = "Claude Lorrain luminous pastoral style"

    route = {
        "hard_filter": {"field": "collection", "operator": "equals", "value": collection},
        "namespace": measurement["namespace"],
        "query": dict(measurement["query_record"]),
        "query_granularity": query_granularity,
        "region": region,
    }
    verification = _verification(
        measurement["verification_metrics"], evidence_status=evidence_status
    )
    negative_measurements = measurement.get("negative_output_evidence", [])
    if len(negative_measurements) != len(historical_outputs):
        raise PixelRagError(
            f"{intent_id} negative output measurements and files must have equal length"
        )
    negative_output_evidence = []
    evidence_ids: set[str] = set()
    for index, (negative, external) in enumerate(
        zip(negative_measurements, historical_outputs, strict=True)
    ):
        evidence_id = negative["evidence_id"]
        if evidence_id in evidence_ids:
            raise PixelRagError(f"{intent_id} repeats a negative output evidence_id")
        evidence_ids.add(evidence_id)
        negative_verification = _verification(
            negative["verification_metrics"], evidence_status=evidence_status
        )
        if negative_verification["status"] != "failed":
            raise PixelRagError(
                f"{intent_id} negative output evidence {index} must fail its verifier"
            )
        negative_output = _external_output(
            external,
            generator=negative["generator"],
            rollback_record_id=source_record["record_id"],
            rollback_content_ref=source_record["content_ref"],
        )
        if negative_output is None:  # pragma: no cover - external is statically non-null
            raise AssertionError("negative external output unexpectedly missing")
        if negative_output["blob_store_registration"]["state"] != "registered":
            raise PixelRagError(
                f"{intent_id} negative output evidence {index} must be registered in Khive"
            )
        negative_output_evidence.append(
            {
                "disposition": negative["disposition"],
                "evidence_id": evidence_id,
                "output": negative_output,
                "verification": negative_verification,
            }
        )
    selected_output = _external_output(
        output,
        generator=measurement["generator"],
        rollback_record_id=source_record["record_id"],
        rollback_content_ref=source_record["content_ref"],
    )
    _require_distinct_rejected_outputs(
        intent_id,
        selected_output,
        negative_output_evidence,
    )
    return {
        "designer_prompt": measurement["designer_prompt"],
        "id": intent_id,
        "negative_output_evidence": negative_output_evidence,
        "output": selected_output,
        "plan": {
            "conditioning": {
                "evidence_content_refs": [
                    evidence["khive"]["content_ref"] for evidence in ranked_evidence
                ],
                "protected": protected,
                "target": target,
            },
            "stages": _stages(intent_id),
        },
        "retrieval": {
            "exact_score_order": exact_order,
            "hard_filter_applied_before_rank_projection": True,
            "method": "khive_exact_cosine_over_lattice_visual_embeddings",
            "metrics": _metric_rows(
                evidence_status=evidence_status,
                filtered=filtered,
                relevance=routed_relevance,
            ),
            "metrics_interpretation": _STRUCTURAL_ROUTING_INTERPRETATION,
            "ranked_evidence": ranked_evidence,
            "raw_diagnostics": _raw_diagnostics(
                evidence_status=evidence_status,
                raw=raw,
                relevance=relevance,
            ),
            "raw_hit_count": len(raw),
        },
        "route": route,
        "verification": verification,
    }


def _artifact_id(artifact: Mapping[str, Any]) -> str:
    identity = {key: value for key, value in artifact.items() if key != "artifact_id"}
    return hashlib.sha256(_canonical_bytes(identity)).hexdigest()


def _output_identities(
    intents: Sequence[Mapping[str, Any]], *, compiler_revision: str
) -> dict[str, Any]:
    if compiler_revision == "moodboard.pixel-rag-compiler.v1":
        return {
            intent["id"]: {
                "content_ref": intent["output"]["output_content_ref"],
                "sha256": intent["output"]["output_sha256"],
            }
            for intent in intents
            if intent["output"] is not None
        }
    return {
        intent["id"]: {
            "negative": [
                {
                    "content_ref": evidence["output"]["output_content_ref"],
                    "evidence_id": evidence["evidence_id"],
                    "sha256": evidence["output"]["output_sha256"],
                }
                for evidence in intent.get("negative_output_evidence", [])
            ],
            "selected": (
                {
                    "content_ref": intent["output"]["output_content_ref"],
                    "sha256": intent["output"]["output_sha256"],
                }
                if intent["output"] is not None
                else None
            ),
        }
        for intent in intents
    }


def _run_fingerprint(
    *,
    compiler_revision: str,
    contracts: Mapping[str, Any],
    manifest_sha256: str,
    measurements_sha256: str,
    intents: Sequence[Mapping[str, Any]],
) -> str:
    payload: dict[str, Any] = {
        "compiler_revision": compiler_revision,
        "manifest_sha256": manifest_sha256,
        "measurements_sha256": measurements_sha256,
        "outputs": _output_identities(intents, compiler_revision=compiler_revision),
    }
    if compiler_revision != "moodboard.pixel-rag-compiler.v1":
        payload["contracts"] = dict(contracts)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def compile_pixel_rag_artifact(
    *,
    manifest_path: Path,
    measurements_path: Path,
    external_outputs: Mapping[str, ExternalOutput] | None = None,
    historical_external_outputs: Mapping[str, Sequence[ExternalOutput]] | None = None,
) -> dict[str, Any]:
    """Compile and validate one frozen Pixel RAG artifact without model recomputation."""

    manifest, manifest_raw, by_id, by_content_ref = _manifest_assets(Path(manifest_path))
    measured_path = Path(measurements_path)
    measurements, measurements_raw, descriptor, descriptor_json = _validated_measurements(
        measured_path
    )
    evidence_sha256 = _evidence_sha256(
        measurements,
        measurements_path=measured_path,
    )
    if measurements["evidence_status"] == "measured_run" and not evidence_sha256:
        raise PixelRagError("measured_run requires the complete frozen evidence bindings")
    confirmation = measurements["intents"][0]["region"]["confirmation"]
    if confirmation["method"] == "evidence_bound_human_confirmation" and (
        confirmation["evidence_sha256"] != evidence_sha256.get("evaluation_preregistration")
    ):
        raise PixelRagError("region confirmation does not bind the preregistration bytes")
    for intent in measurements["intents"]:
        for metric in [
            *intent["verification_metrics"],
            *(
                evidence_metric
                for negative in intent.get("negative_output_evidence", [])
                for evidence_metric in negative["verification_metrics"]
            ),
        ]:
            field = metric.get("evidence_field")
            if field is not None and (
                field["evidence_sha256"] != evidence_sha256.get("verification_summary")
            ):
                raise PixelRagError("verification metric does not bind the verification bytes")
    experimental_diagnostics = _experimental_diagnostics(measurements)
    outputs = dict(external_outputs or {})
    if set(outputs) - set(_INTENTS):
        raise PixelRagError("external_outputs contains an unknown intent")
    if any(not isinstance(output, ExternalOutput) for output in outputs.values()):
        raise TypeError("external_outputs values must be ExternalOutput instances")
    historical = {
        intent_id: tuple(intent_outputs)
        for intent_id, intent_outputs in (historical_external_outputs or {}).items()
    }
    if set(historical) - set(_INTENTS):
        raise PixelRagError("historical_external_outputs contains an unknown intent")
    if any(
        not isinstance(output, ExternalOutput)
        for intent_outputs in historical.values()
        for output in intent_outputs
    ):
        raise TypeError("historical_external_outputs values must contain ExternalOutput instances")

    source = by_id.get("fruit_apple_garden")
    if source is None:
        raise PixelRagError("governed manifest does not contain fruit_apple_garden")
    source_record = measurements["source_record"]
    _uuid(source_record["record_id"], "measurements.source_record.record_id")
    if source_record["content_ref"] != source["khive_content_ref"]:
        raise PixelRagError("measurements source record does not bind fruit_apple_garden")
    source_artifact = {
        **_manifest_identity(source),
        "khive": {
            "content_ref": source_record["content_ref"],
            "record_id": source_record["record_id"],
        },
    }

    evidence_status = measurements["evidence_status"]
    intents = [
        _intent_artifact(
            source,
            measurement,
            by_content_ref=by_content_ref,
            descriptor=descriptor,
            evidence_status=evidence_status,
            historical_outputs=historical.get(measurement["id"], ()),
            output=outputs.get(measurement["id"]),
            source_record=source_record,
        )
        for measurement in measurements["intents"]
    ]
    left_refs = {
        evidence["khive"]["content_ref"] for evidence in intents[0]["retrieval"]["ranked_evidence"]
    }
    right_refs = {
        evidence["khive"]["content_ref"] for evidence in intents[1]["retrieval"]["ranked_evidence"]
    }
    union = left_refs | right_refs
    cross_metrics = [
        {
            "id": "intent_top3_jaccard",
            "intersection_count": len(left_refs & right_refs),
            "interpretation": _STRUCTURAL_ROUTING_INTERPRETATION,
            "source": evidence_status,
            "union_count": len(union),
            "value": len(left_refs & right_refs) / len(union) if union else 0.0,
        }
    ]
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    measurements_sha = hashlib.sha256(measurements_raw).hexdigest()
    contracts = {
        "artifact_schema_sha256": hashlib.sha256(ARTIFACT_SCHEMA_PATH.read_bytes()).hexdigest(),
        "measurements_schema_sha256": hashlib.sha256(
            MEASUREMENTS_SCHEMA_PATH.read_bytes()
        ).hexdigest(),
        "source_manifest_schema_sha256": hashlib.sha256(
            DEMO_MANIFEST_SCHEMA_PATH.read_bytes()
        ).hexdigest(),
    }
    run_fingerprint = _run_fingerprint(
        compiler_revision=COMPILER_REVISION,
        contracts=contracts,
        manifest_sha256=manifest_sha,
        measurements_sha256=measurements_sha,
        intents=intents,
    )
    provenance: dict[str, Any] = {
        "compiler_revision": COMPILER_REVISION,
        "generated_at": measurements["generated_at"],
        "khive_revision": measurements["khive_revision"],
        "measurements_sha256": measurements_sha,
        "run_fingerprint": run_fingerprint,
    }
    if evidence_sha256:
        provenance["evidence_sha256"] = evidence_sha256
    if measurements.get("projection") is not None:
        provenance["projection"] = dict(measurements["projection"])
    artifact: dict[str, Any] = {
        "artifact_id": "0" * 64,
        "contracts": contracts,
        "cross_intent_metrics": cross_metrics,
        "descriptor": descriptor_json,
        "evidence_status": evidence_status,
        "experimental_visual_embedding_diagnostics": experimental_diagnostics,
        "intents": intents,
        "provenance": provenance,
        "schema_version": ARTIFACT_SCHEMA,
        "source": source_artifact,
        "source_manifest": {
            "catalog_sha256": manifest["catalog_sha256"],
            "dataset_id": manifest["dataset_id"],
            "manifest_sha256": manifest_sha,
            "retrieved_at": manifest["retrieved_at"],
        },
    }
    artifact["artifact_id"] = _artifact_id(artifact)
    validate_pixel_rag_artifact(artifact)
    return artifact


def _validate_artifact(value: Mapping[str, Any]) -> None:
    _validate_schema(value, ARTIFACT_SCHEMA_PATH, label="Pixel RAG artifact")
    _validated_descriptor(value["descriptor"])
    expected_contracts = {
        "artifact_schema_sha256": hashlib.sha256(ARTIFACT_SCHEMA_PATH.read_bytes()).hexdigest(),
        "measurements_schema_sha256": hashlib.sha256(
            MEASUREMENTS_SCHEMA_PATH.read_bytes()
        ).hexdigest(),
        "source_manifest_schema_sha256": hashlib.sha256(
            DEMO_MANIFEST_SCHEMA_PATH.read_bytes()
        ).hexdigest(),
    }
    legacy_contract_fixture = (
        value["evidence_status"] == "contract_fixture"
        and value["provenance"]["compiler_revision"] == "moodboard.pixel-rag-compiler.v1"
        and value["contracts"] == _LEGACY_CONTRACTS
    )
    current_contract_artifact = (
        value["provenance"]["compiler_revision"] == COMPILER_REVISION
        and value["contracts"] == expected_contracts
    )
    if not current_contract_artifact and not legacy_contract_fixture:
        raise PixelRagError(
            "Pixel RAG artifact compiler/schema identity tuple does not match this reader"
        )
    measured = _artifact_id(value)
    if value["artifact_id"] != measured:
        raise PixelRagError("Pixel RAG artifact_id does not match its canonical contents")
    expected_run_fingerprint = _run_fingerprint(
        compiler_revision=value["provenance"]["compiler_revision"],
        contracts=value["contracts"],
        manifest_sha256=value["source_manifest"]["manifest_sha256"],
        measurements_sha256=value["provenance"]["measurements_sha256"],
        intents=value["intents"],
    )
    if value["provenance"]["run_fingerprint"] != expected_run_fingerprint:
        raise PixelRagError("Pixel RAG artifact run fingerprint does not match its bound inputs")
    if [intent["id"] for intent in value["intents"]] != list(_INTENTS):
        raise PixelRagError("Pixel RAG artifact intents are not in the stable declared order")
    expected_stages = [
        "retrieval",
        "region",
        "conditioning",
        "external_generation",
        "verification",
        "immutable_output",
        "rollback",
    ]
    evidence_sha256 = value["provenance"].get("evidence_sha256", {})
    if evidence_sha256 and set(evidence_sha256) != set(_EVIDENCE_BINDING_KINDS):
        raise PixelRagError("Pixel RAG artifact evidence bindings are incomplete")
    if value["evidence_status"] == "measured_run" and set(evidence_sha256) != set(
        _EVIDENCE_BINDING_KINDS
    ):
        raise PixelRagError("measured Pixel RAG artifact evidence bindings are incomplete")
    source_record = value["source"]["khive"]
    for intent in value["intents"]:
        intent_id = intent["id"]
        expected_collection = _COLLECTION_BY_INTENT[intent_id]
        expected_granularity = (
            "human_confirmed_region" if intent_id == "local_replace" else "whole_frame"
        )
        if (
            intent["route"]["hard_filter"]["value"] != expected_collection
            or intent["route"]["query_granularity"] != expected_granularity
        ):
            raise PixelRagError(f"{intent_id} route does not match its declared intent policy")
        if (intent["route"]["region"] is None) != (intent_id == "global_restyle"):
            raise PixelRagError(f"{intent_id} region does not match its declared granularity")
        if intent_id == "global_restyle" and (
            intent["route"]["query"]["content_ref"] != source_record["content_ref"]
            or intent["route"]["query"]["sha256"] != value["source"]["sha256"]
        ):
            raise PixelRagError("whole-frame query must bind the immutable source identity")
        region = intent["route"]["region"]
        if region is not None:
            confirmation = region["confirmation"]
            if confirmation["method"] == "evidence_bound_human_confirmation" and (
                confirmation["evidence_sha256"] != evidence_sha256.get("evaluation_preregistration")
            ):
                raise PixelRagError("artifact region confirmation evidence is inconsistent")
        if [stage["id"] for stage in intent["plan"]["stages"]] != expected_stages:
            raise PixelRagError(f"{intent_id} control stages are not in their declared order")
        scores = [row["score"] for row in intent["retrieval"]["exact_score_order"]]
        if scores != sorted(scores, reverse=True):
            raise PixelRagError("Pixel RAG artifact exact score order is invalid")
        evidence = intent["retrieval"]["ranked_evidence"]
        if [row["rank"] for row in evidence] != [1, 2, 3]:
            raise PixelRagError(f"{intent_id} evidence ranks must be exactly [1, 2, 3]")
        if any(row["collection"] != expected_collection for row in evidence):
            raise PixelRagError(f"{intent_id} evidence escapes its hard-filtered corpus")
        if [row["khive"]["content_ref"] for row in evidence] != [
            row["content_ref"] for row in intent["retrieval"]["exact_score_order"][:3]
        ]:
            raise PixelRagError(f"{intent_id} evidence does not project the exact score order")
        for card, exact in zip(
            evidence,
            intent["retrieval"]["exact_score_order"][:3],
            strict=True,
        ):
            if (
                card["asset_id"] != exact["asset_id"]
                or card["source_search_rank"] != exact["source_search_rank"]
                or card["score"]["value"] != exact["score"]
                or card["score"]["descriptor_fingerprint"] != value["descriptor"]["fingerprint"]
            ):
                raise PixelRagError(
                    f"{intent_id} ranked card fields do not bind the exact retrieval row"
                )
        if any(
            metric["source"] != value["evidence_status"]
            for metric in intent["retrieval"]["metrics"]
        ):
            raise PixelRagError("retrieval metric evidence source does not match the run")
        raw_diagnostics = intent["retrieval"].get("raw_diagnostics")
        if not legacy_contract_fixture and (
            raw_diagnostics is None
            or intent["retrieval"].get("metrics_interpretation")
            != _STRUCTURAL_ROUTING_INTERPRETATION
        ):
            raise PixelRagError("current Pixel RAG artifact requires extended retrieval evidence")
        expected_metric_ids = (
            _RETRIEVAL_METRIC_IDS[:2] if legacy_contract_fixture else _RETRIEVAL_METRIC_IDS
        )
        if tuple(metric["id"] for metric in intent["retrieval"]["metrics"]) != tuple(
            expected_metric_ids
        ):
            raise PixelRagError("Pixel RAG artifact retrieval metric sequence is incomplete")
        for verification in [
            intent["verification"],
            *(negative["verification"] for negative in intent.get("negative_output_evidence", [])),
        ]:
            expected_status = (
                "not_run"
                if not verification["metrics"]
                else (
                    "passed"
                    if all(metric["passed"] for metric in verification["metrics"])
                    else "failed"
                )
            )
            if verification["status"] != expected_status:
                raise PixelRagError("Pixel RAG artifact verification status is inconsistent")
            for metric in verification["metrics"]:
                expected_passed = (
                    metric["value"] >= metric["threshold"]
                    if metric["operator"] == "greater_than_or_equal"
                    else metric["value"] <= metric["threshold"]
                )
                if metric["passed"] != expected_passed:
                    raise PixelRagError("Pixel RAG artifact verifier arithmetic is inconsistent")
                field = metric.get("evidence_field")
                if field is not None and (
                    field["evidence_sha256"] != evidence_sha256.get("verification_summary")
                ):
                    raise PixelRagError("artifact verification evidence is inconsistent")
        negative_outputs = intent.get("negative_output_evidence", [])
        if any(negative["verification"]["status"] != "failed" for negative in negative_outputs):
            raise PixelRagError("negative output evidence must retain a failed verifier")
        if any(
            negative["output"]["blob_store_registration"]["state"] != "registered"
            for negative in negative_outputs
        ):
            raise PixelRagError("negative output evidence must retain Khive registration")
        _require_distinct_rejected_outputs(intent_id, intent["output"], negative_outputs)
        for label, output in [
            ("selected output", intent["output"]),
            *(("negative output", negative["output"]) for negative in negative_outputs),
        ]:
            if output is None:
                continue
            if current_contract_artifact and output["external_location"] != {
                "kind": "identity_only"
            }:
                raise PixelRagError(f"current {label} must use identity_only external location")
            registration = output["blob_store_registration"]
            if (
                registration["state"] == "registered"
                and registration["content_ref"] != output["output_content_ref"]
            ):
                raise PixelRagError(f"{label} registration does not match its output ContentRef")
            if output["rollback"] != source_record:
                raise PixelRagError(f"{label} rollback does not bind the immutable source")
        if raw_diagnostics is not None:
            raw_scores = [row["score"] for row in raw_diagnostics["exact_score_order"]]
            if raw_scores != sorted(raw_scores, reverse=True):
                raise PixelRagError("Pixel RAG artifact raw score order is invalid")
            if len(raw_scores) != intent["retrieval"]["raw_hit_count"]:
                raise PixelRagError("Pixel RAG artifact raw hit count is inconsistent")
            if raw_diagnostics["probabilistic_interpretation"] is not False:
                raise PixelRagError("raw visual cosine diagnostics must not be probabilistic")
            if any(
                metric["source"] != value["evidence_status"]
                for metric in raw_diagnostics["metrics"]
            ):
                raise PixelRagError("raw retrieval metric evidence source does not match the run")
            if tuple(metric["id"] for metric in raw_diagnostics["metrics"]) != tuple(
                _RETRIEVAL_METRIC_IDS
            ):
                raise PixelRagError(
                    "Pixel RAG artifact raw retrieval metric sequence is incomplete"
                )
            raw_rows = [
                (
                    row["asset_id"],
                    row["content_ref"],
                    row["score"],
                    row["source_search_rank"],
                )
                for row in raw_diagnostics["exact_score_order"]
            ]
            routed_rows = [
                (
                    row["asset_id"],
                    row["content_ref"],
                    row["score"],
                    row["source_search_rank"],
                )
                for row in intent["retrieval"]["exact_score_order"]
            ]
            positions = [raw_rows.index(row) if row in raw_rows else -1 for row in routed_rows]
            if any(position < 0 for position in positions) or positions != sorted(positions):
                raise PixelRagError(
                    "routed retrieval must be a stable subsequence of raw retrieval"
                )
    diagnostics = value.get("experimental_visual_embedding_diagnostics")
    if diagnostics is not None:
        _experimental_diagnostics({"experimental_visual_embedding_diagnostics": diagnostics})
    left = {
        row["khive"]["content_ref"] for row in value["intents"][0]["retrieval"]["ranked_evidence"]
    }
    right = {
        row["khive"]["content_ref"] for row in value["intents"][1]["retrieval"]["ranked_evidence"]
    }
    cross = value["cross_intent_metrics"][0]
    union = left | right
    expected_jaccard = len(left & right) / len(union) if union else 0.0
    if (
        cross["intersection_count"] != len(left & right)
        or cross["union_count"] != len(union)
        or cross["value"] != expected_jaccard
        or cross["source"] != value["evidence_status"]
        or (
            current_contract_artifact
            and cross.get("interpretation") != _STRUCTURAL_ROUTING_INTERPRETATION
        )
    ):
        raise PixelRagError("intent_top3_jaccard does not match the ranked evidence")


def validate_pixel_rag_artifact(value: Mapping[str, Any]) -> None:
    """Validate one closed artifact with the compiler's complete semantic contract."""

    _validate_artifact(value)


def write_pixel_rag_artifact(artifact: Mapping[str, Any], destination: Path) -> None:
    """Atomically publish a validated immutable artifact without overwriting a prior run."""

    document = dict(artifact)
    validate_pixel_rag_artifact(document)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(output):
        raise FileExistsError(f"refusing to overwrite immutable Pixel RAG artifact: {output}")
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(document))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_pixel_rag_artifact(path: Path) -> dict[str, Any]:
    """Read, close-validate, and identity-check one frozen Pixel RAG artifact."""

    value, _raw = _load_json(Path(path), label="Pixel RAG artifact")
    validate_pixel_rag_artifact(value)
    return value


def _output(arguments: argparse.Namespace, intent_id: str) -> ExternalOutput | None:
    prefix = "local" if intent_id == "local_replace" else "restyle"
    path = getattr(arguments, f"{prefix}_output")
    record_id = getattr(arguments, f"{prefix}_record_id")
    content_ref = getattr(arguments, f"{prefix}_content_ref")
    if path is None:
        if record_id is not None or content_ref is not None:
            raise PixelRagError(f"--{prefix}-record-id/content-ref require --{prefix}-output")
        return None
    return ExternalOutput(
        path=path,
        khive_record_id=record_id,
        expected_content_ref=content_ref,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m moodboard.pixel_rag",
        description="Freeze governed manifest + measured Khive retrieval into Pixel RAG JSON.",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    for prefix in ("local", "restyle"):
        parser.add_argument(f"--{prefix}-output", type=Path)
        parser.add_argument(f"--{prefix}-record-id")
        parser.add_argument(f"--{prefix}-content-ref")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        outputs = {
            intent_id: output
            for intent_id in _INTENTS
            if (output := _output(arguments, intent_id)) is not None
        }
        artifact = compile_pixel_rag_artifact(
            manifest_path=arguments.manifest,
            measurements_path=arguments.measurements,
            external_outputs=outputs,
        )
        write_pixel_rag_artifact(artifact, arguments.output)
    except (FileExistsError, OSError, PixelRagError, ValueError) as error:
        raise SystemExit(f"BLOCKED: {error}") from error
    print(
        json.dumps(
            {
                "artifact_id": artifact["artifact_id"],
                "evidence_status": artifact["evidence_status"],
                "output": str(arguments.output.resolve()),
                "run_fingerprint": artifact["provenance"]["run_fingerprint"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the real demonstration command
    raise SystemExit(main())

"""Canonical features for Khive's governed pairwise-preference head.

This module owns the producer semantics that ADR-149 intentionally leaves to
the application.  It does not infer features from a serialized report: the
three visual summaries and effective local support require the complete
candidate-local geometry available during ``moodboard rank``.

The frozen mapping is:

* cosine similarity is mapped from ``[-1, 1]`` to ``[0, 1]`` before max,
  top-three mean, and full local mean aggregation;
* local support and effective support divide by the total reference count;
* palette, tone, and composition distances become compatibility via ``1-d``;
* conformal p and interval width retain their native ``[0, 1]`` meanings.

Changing any formula requires a new feature-schema version even if the names
and vector length stay unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_PRODUCER_ID",
    "FEATURE_PRODUCER_REVISION",
    "FEATURE_SCHEMA_CANONICAL_JSON",
    "FEATURE_SCHEMA_ID",
    "FEATURE_SCHEMA_VERSION",
    "FEATURE_SEMANTICS_CANONICAL_JSON",
    "PreferenceFeatures",
    "PreferenceCandidate",
    "PreferenceFeatureArtifact",
    "build_preference_features",
    "read_preference_feature_artifact",
    "write_preference_feature_artifact",
]

FEATURE_SCHEMA_VERSION: Final = "moodboard.preference-features.v1"
FEATURE_NAMES: Final = (
    "visual_local_max_similarity_01",
    "visual_local_top3_mean_similarity_01",
    "visual_local_mean_similarity_01",
    "style_conformal_p",
    "style_interval_width",
    "local_support_fraction",
    "local_effective_support_fraction",
    "palette_compatibility",
    "tone_compatibility",
    "composition_compatibility",
)
FEATURE_SCHEMA_CANONICAL_JSON: Final = (
    b'{"bounds":[0.0,1.0],"dtype":"float32","features":['
    b'"visual_local_max_similarity_01",'
    b'"visual_local_top3_mean_similarity_01",'
    b'"visual_local_mean_similarity_01",'
    b'"style_conformal_p",'
    b'"style_interval_width",'
    b'"local_support_fraction",'
    b'"local_effective_support_fraction",'
    b'"palette_compatibility",'
    b'"tone_compatibility",'
    b'"composition_compatibility"],'
    b'"pair_transform":"left_minus_right",'
    b'"schema_version":"moodboard.preference-features.v1"}'
)
FEATURE_SCHEMA_ID: Final = hashlib.sha256(FEATURE_SCHEMA_CANONICAL_JSON).hexdigest()
FEATURE_PRODUCER_REVISION: Final = "moodboard.preference-producer.v1"
FEATURE_SEMANTICS_CANONICAL_JSON: Final = (
    b'{"composition_compatibility":"1-composition_distance",'
    b'"feature_schema_id":"f691fc73bf9a50d72157e21601fa579caa707bf2c448df546c63e915b4e42175",'
    b'"local_effective_support_fraction":"n_eff_local/n_references",'
    b'"local_support_fraction":"n_local/n_references",'
    b'"palette_compatibility":"1-palette_distance",'
    b'"producer_revision":"moodboard.preference-producer.v1",'
    b'"style_conformal_p":"candidate_transductive_conformal_p",'
    b'"style_interval_width":"interval_high-interval_low",'
    b'"tone_compatibility":"1-tone_distance",'
    b'"visual_local_max_similarity_01":"max(local_transformed_similarities)",'
    b'"visual_local_mean_similarity_01":"mean(local_transformed_similarities)",'
    b'"visual_local_top3_mean_similarity_01":"mean(top3(local_transformed_similarities))",'
    b'"visual_similarity_transform":"clip((cosine+1)/2,0,1)"}'
)
FEATURE_PRODUCER_ID: Final = hashlib.sha256(FEATURE_SEMANTICS_CANONICAL_JSON).hexdigest()

_UNIT_NORM_ATOL = 1.0e-5
_HEX = frozenset("0123456789abcdef")
_ARTIFACT_SCHEMA_VERSION = "moodboard.preference-feature-artifact.v2"
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "board_entity_id",
        "board_id",
        "model_key",
        "descriptor_fingerprint",
        "source_report_sha256",
        "feature_schema_id",
        "producer_revision",
        "producer_id",
        "candidate_pool_sha256",
        "scope_sha256",
        "candidates",
    }
)
_CANDIDATE_KEYS = frozenset({"label", "asset_id", "content_ref", "source_rank", "features"})


@dataclass(frozen=True, slots=True, eq=False)
class PreferenceFeatures:
    """One immutable float32 row in Khive's exact model-input order."""

    values: np.ndarray

    def __post_init__(self) -> None:
        values = np.asarray(self.values)
        if values.shape != (len(FEATURE_NAMES),) or values.dtype != np.dtype("float32"):
            raise ValueError("preference features must be one exact 10-element float32 row")
        if not np.isfinite(values).all() or np.any(values < 0.0) or np.any(values > 1.0):
            raise ValueError("preference features must be finite and in [0,1]")
        owned = np.array(values, dtype=np.float32, order="C", copy=True)
        owned.flags.writeable = False
        object.__setattr__(self, "values", owned)

    def as_wire(self) -> list[float]:
        """Return JSON-safe numbers without changing float32 identity."""

        return [float(value) for value in self.values]

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PreferenceFeatures) and np.array_equal(self.values, other.values)


def _lower_hex_64(value: object, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _HEX:
        raise ValueError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def _canonical_uuid(value: object, *, label: str) -> str:
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except ValueError as error:
        raise ValueError(f"{label} must be a bare canonical UUID") from error
    if parsed is None or str(parsed) != value:
        raise ValueError(f"{label} must be a bare canonical UUID")
    return value


@dataclass(frozen=True, slots=True)
class PreferenceCandidate:
    """One scored Khive asset plus the exact frozen row shown to a user."""

    label: str
    asset_id: str
    content_ref: str
    source_rank: int
    features: PreferenceFeatures | np.ndarray

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("preference candidate label must be a non-empty string")
        if len(self.label.encode("utf-8")) > 512:
            raise ValueError("preference candidate label must be at most 512 UTF-8 bytes")
        _canonical_uuid(self.asset_id, label="preference candidate asset_id")
        _lower_hex_64(self.content_ref, label="preference candidate content_ref")
        if (
            isinstance(self.source_rank, bool)
            or not isinstance(self.source_rank, int)
            or self.source_rank < 1
            or self.source_rank > 2**32 - 1
        ):
            raise ValueError("preference candidate source_rank must be a positive u32")
        if not isinstance(self.features, PreferenceFeatures):
            object.__setattr__(self, "features", PreferenceFeatures(np.asarray(self.features)))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, PreferenceCandidate)
            and self.label == other.label
            and self.asset_id == other.asset_id
            and self.content_ref == other.content_ref
            and self.source_rank == other.source_rank
            and self.features == other.features
        )

    def to_json_dict(self) -> dict[str, object]:
        assert isinstance(self.features, PreferenceFeatures)
        return {
            "label": self.label,
            "asset_id": self.asset_id,
            "content_ref": self.content_ref,
            "source_rank": self.source_rank,
            "features": self.features.as_wire(),
        }


def _candidate_pool_digest(candidates: Sequence[PreferenceCandidate]) -> str:
    digest = hashlib.sha256()
    digest.update(b"moodboard-candidate-pool-v1\0")
    digest.update(bytes.fromhex(FEATURE_SCHEMA_ID))
    digest.update(bytes.fromhex(FEATURE_PRODUCER_ID))
    ordered = sorted(candidates, key=lambda candidate: candidate.asset_id)
    digest.update(struct.pack("<I", len(ordered)))
    for candidate in ordered:
        digest.update(uuid.UUID(candidate.asset_id).bytes)
        digest.update(bytes.fromhex(candidate.content_ref))
        digest.update(struct.pack("<I", candidate.source_rank))
        assert isinstance(candidate.features, PreferenceFeatures)
        digest.update(candidate.features.values.astype("<f4", copy=False).tobytes(order="C"))
    return digest.hexdigest()


def _artifact_scope_digest(
    *,
    board_entity_id: str,
    board_id: str,
    model_key: str,
    descriptor_fingerprint: str,
    source_report_sha256: str,
    feature_schema_id: str,
    producer_revision: str,
    producer_id: str,
    candidate_pool_sha256: str,
) -> str:
    """Bind the complete learning scope without conflating it with pool identity."""

    payload = json.dumps(
        {
            "schema_version": _ARTIFACT_SCHEMA_VERSION,
            "board_entity_id": board_entity_id,
            "board_id": board_id,
            "model_key": model_key,
            "descriptor_fingerprint": descriptor_fingerprint,
            "source_report_sha256": source_report_sha256,
            "feature_schema_id": feature_schema_id,
            "producer_revision": producer_revision,
            "producer_id": producer_id,
            "candidate_pool_sha256": candidate_pool_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(b"moodboard-preference-artifact-scope-v2\0" + payload).hexdigest()


@dataclass(frozen=True, slots=True)
class PreferenceFeatureArtifact:
    """Closed handoff between rank-time geometry and Khive preference events."""

    schema_version: str
    board_entity_id: str
    board_id: str
    model_key: str
    descriptor_fingerprint: str
    source_report_sha256: str
    feature_schema_id: str
    producer_revision: str
    producer_id: str
    candidate_pool_sha256: str
    scope_sha256: str
    candidates: tuple[PreferenceCandidate, ...]

    @classmethod
    def build(
        cls,
        *,
        board_entity_id: str,
        board_id: str,
        model_key: str,
        descriptor_fingerprint: str,
        source_report_sha256: str,
        candidates: Sequence[PreferenceCandidate],
    ) -> PreferenceFeatureArtifact:
        frozen = tuple(candidates)
        candidate_pool_sha256 = _candidate_pool_digest(frozen)
        scope_sha256 = _artifact_scope_digest(
            board_entity_id=board_entity_id,
            board_id=board_id,
            model_key=model_key,
            descriptor_fingerprint=descriptor_fingerprint,
            source_report_sha256=source_report_sha256,
            feature_schema_id=FEATURE_SCHEMA_ID,
            producer_revision=FEATURE_PRODUCER_REVISION,
            producer_id=FEATURE_PRODUCER_ID,
            candidate_pool_sha256=candidate_pool_sha256,
        )
        return cls(
            schema_version=_ARTIFACT_SCHEMA_VERSION,
            board_entity_id=board_entity_id,
            board_id=board_id,
            model_key=model_key,
            descriptor_fingerprint=descriptor_fingerprint,
            source_report_sha256=source_report_sha256,
            feature_schema_id=FEATURE_SCHEMA_ID,
            producer_revision=FEATURE_PRODUCER_REVISION,
            producer_id=FEATURE_PRODUCER_ID,
            candidate_pool_sha256=candidate_pool_sha256,
            scope_sha256=scope_sha256,
            candidates=frozen,
        )

    def __post_init__(self) -> None:
        if self.schema_version != _ARTIFACT_SCHEMA_VERSION:
            raise ValueError("unsupported preference feature artifact schema_version")
        _canonical_uuid(self.board_entity_id, label="preference artifact board_entity_id")
        _lower_hex_64(self.board_id, label="preference artifact board_id")
        _lower_hex_64(
            self.descriptor_fingerprint, label="preference artifact descriptor_fingerprint"
        )
        _lower_hex_64(self.source_report_sha256, label="preference artifact source_report_sha256")
        model_prefix = f"moodboard_{self.descriptor_fingerprint}_"
        dimension = (
            self.model_key.removeprefix(model_prefix) if isinstance(self.model_key, str) else ""
        )
        if (
            not isinstance(self.model_key, str)
            or not self.model_key.isascii()
            or not self.model_key.startswith(model_prefix)
            or not dimension.isdigit()
            or dimension.startswith("0")
            or not 1 <= int(dimension) <= 8192
        ):
            raise ValueError("preference artifact model_key must bind its descriptor fingerprint")
        if self.feature_schema_id != FEATURE_SCHEMA_ID:
            raise ValueError("preference artifact feature_schema_id is not the supported schema")
        if self.producer_revision != FEATURE_PRODUCER_REVISION:
            raise ValueError("preference artifact producer_revision is not supported")
        if self.producer_id != FEATURE_PRODUCER_ID:
            raise ValueError("preference artifact producer_id is not supported")
        _lower_hex_64(self.candidate_pool_sha256, label="candidate_pool_sha256")
        if not self.candidates:
            raise ValueError("preference artifact must contain at least one candidate")
        if any(not isinstance(candidate, PreferenceCandidate) for candidate in self.candidates):
            raise ValueError("preference artifact candidates must be PreferenceCandidate values")
        asset_ids = [candidate.asset_id for candidate in self.candidates]
        content_refs = [candidate.content_ref for candidate in self.candidates]
        if len(set(asset_ids)) != len(asset_ids) or len(set(content_refs)) != len(content_refs):
            raise ValueError("preference artifact candidates must have unique asset/content IDs")
        measured = _candidate_pool_digest(self.candidates)
        if measured != self.candidate_pool_sha256:
            raise ValueError("preference artifact candidate_pool_sha256 does not match candidates")
        _lower_hex_64(self.scope_sha256, label="preference artifact scope_sha256")
        measured_scope = _artifact_scope_digest(
            board_entity_id=self.board_entity_id,
            board_id=self.board_id,
            model_key=self.model_key,
            descriptor_fingerprint=self.descriptor_fingerprint,
            source_report_sha256=self.source_report_sha256,
            feature_schema_id=self.feature_schema_id,
            producer_revision=self.producer_revision,
            producer_id=self.producer_id,
            candidate_pool_sha256=self.candidate_pool_sha256,
        )
        if measured_scope != self.scope_sha256:
            raise ValueError("preference artifact scope_sha256 does not match its immutable scope")

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, PreferenceFeatureArtifact)
            and self.schema_version == other.schema_version
            and self.board_entity_id == other.board_entity_id
            and self.board_id == other.board_id
            and self.model_key == other.model_key
            and self.descriptor_fingerprint == other.descriptor_fingerprint
            and self.source_report_sha256 == other.source_report_sha256
            and self.feature_schema_id == other.feature_schema_id
            and self.producer_revision == other.producer_revision
            and self.producer_id == other.producer_id
            and self.candidate_pool_sha256 == other.candidate_pool_sha256
            and self.scope_sha256 == other.scope_sha256
            and self.candidates == other.candidates
        )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "board_entity_id": self.board_entity_id,
            "board_id": self.board_id,
            "model_key": self.model_key,
            "descriptor_fingerprint": self.descriptor_fingerprint,
            "source_report_sha256": self.source_report_sha256,
            "feature_schema_id": self.feature_schema_id,
            "producer_revision": self.producer_revision,
            "producer_id": self.producer_id,
            "candidate_pool_sha256": self.candidate_pool_sha256,
            "scope_sha256": self.scope_sha256,
            "candidates": [candidate.to_json_dict() for candidate in self.candidates],
        }


def _reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token!r}")


def _exact_keys(value: dict[str, object], expected: frozenset[str], *, label: str) -> None:
    if frozenset(value) != expected:
        raise ValueError(f"{label} does not have the exact closed key set")


def read_preference_feature_artifact(path: Path) -> PreferenceFeatureArtifact:
    """Read, strictly validate, and rederive every identity-bearing digest."""

    data = path.read_bytes()
    if len(data) > _MAX_ARTIFACT_BYTES:
        raise ValueError("preference feature artifact exceeds the 16 MiB input ceiling")
    try:
        document = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(
            f"preference feature artifact is not strict UTF-8 JSON: {error}"
        ) from error
    if not isinstance(document, dict):
        raise ValueError("preference feature artifact must be a JSON object")
    _exact_keys(document, _ARTIFACT_KEYS, label="preference feature artifact")
    raw_candidates = document["candidates"]
    if not isinstance(raw_candidates, list):
        raise ValueError("preference feature artifact candidates must be an array")
    candidates: list[PreferenceCandidate] = []
    for index, raw in enumerate(raw_candidates):
        if not isinstance(raw, dict):
            raise ValueError(f"preference candidate {index} must be an object")
        _exact_keys(raw, _CANDIDATE_KEYS, label=f"preference candidate {index}")
        features = raw["features"]
        if not isinstance(features, list) or len(features) != len(FEATURE_NAMES):
            raise ValueError(f"preference candidate {index} features must have length 10")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in features
        ):
            raise ValueError(f"preference candidate {index} features must be plain numbers")
        candidates.append(
            PreferenceCandidate(
                label=raw["label"],
                asset_id=raw["asset_id"],
                content_ref=raw["content_ref"],
                source_rank=raw["source_rank"],
                features=np.asarray(features, dtype=np.float32),
            )
        )
    return PreferenceFeatureArtifact(
        schema_version=document["schema_version"],
        board_entity_id=document["board_entity_id"],
        board_id=document["board_id"],
        model_key=document["model_key"],
        descriptor_fingerprint=document["descriptor_fingerprint"],
        source_report_sha256=document["source_report_sha256"],
        feature_schema_id=document["feature_schema_id"],
        producer_revision=document["producer_revision"],
        producer_id=document["producer_id"],
        candidate_pool_sha256=document["candidate_pool_sha256"],
        scope_sha256=document["scope_sha256"],
        candidates=tuple(candidates),
    )


def write_preference_feature_artifact(artifact: PreferenceFeatureArtifact, path: Path) -> None:
    """Atomically publish one closed feature artifact after intrinsic validation."""

    if not isinstance(artifact, PreferenceFeatureArtifact):
        raise TypeError("artifact must be a PreferenceFeatureArtifact")
    # __post_init__ already rederived the digest. Serializing with allow_nan=False
    # is the final wire fence before atomic publication.
    payload = (
        json.dumps(
            artifact.to_json_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _unit_rows(value: np.ndarray, *, label: str, ndim: int) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != ndim or array.size == 0 or not np.isfinite(array).all():
        raise ValueError(f"{label} must be a non-empty finite {ndim}-D array")
    axis = 1 if ndim == 2 else None
    norms = np.linalg.norm(array, axis=axis)
    if not np.allclose(norms, 1.0, rtol=0.0, atol=_UNIT_NORM_ATOL):
        raise ValueError(f"{label} rows must be L2-normalized")
    return array


def _unit_number(value: float, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a plain finite number in [0,1]")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise ValueError(f"{label} must be a plain finite number in [0,1]")
    return numeric


def build_preference_features(
    *,
    candidate_embedding: np.ndarray,
    reference_embeddings: np.ndarray,
    local_member_indices: Sequence[int],
    style_conformal_p: float,
    style_interval: tuple[float, float],
    local_effective_size: float,
    palette_distance: float,
    tone_distance: float,
    composition_distance: float,
) -> PreferenceFeatures:
    """Materialize one governed row from complete candidate-local rank state."""

    candidate = _unit_rows(candidate_embedding, label="candidate_embedding", ndim=1)
    references = _unit_rows(reference_embeddings, label="reference_embeddings", ndim=2)
    if references.shape[1] != candidate.shape[0]:
        raise ValueError("candidate and reference embedding dimensions must match")

    members = tuple(local_member_indices)
    if not members:
        raise ValueError("local_member_indices must contain at least one reference")
    if any(isinstance(index, bool) or not isinstance(index, int) for index in members):
        raise ValueError("local_member_indices must contain plain integers")
    if len(set(members)) != len(members):
        raise ValueError("local_member_indices must not contain duplicates")
    if min(members) < 0 or max(members) >= references.shape[0]:
        raise ValueError("local_member_indices contains an out-of-range reference")

    p_value = _unit_number(style_conformal_p, label="style_conformal_p")
    if not isinstance(style_interval, tuple) or len(style_interval) != 2:
        raise ValueError("style_interval must be an exact (low, high) tuple")
    interval_low = _unit_number(style_interval[0], label="style_interval.low")
    interval_high = _unit_number(style_interval[1], label="style_interval.high")
    if interval_low > interval_high:
        raise ValueError("style_interval.low must not exceed style_interval.high")

    if isinstance(local_effective_size, bool) or not isinstance(local_effective_size, (int, float)):
        raise ValueError("local_effective_size must be a finite number")
    effective = float(local_effective_size)
    if not math.isfinite(effective) or not 0.0 < effective <= len(members):
        raise ValueError("local_effective_size must be in (0, local reference count]")

    distances = (
        _unit_number(palette_distance, label="palette_distance"),
        _unit_number(tone_distance, label="tone_distance"),
        _unit_number(composition_distance, label="composition_distance"),
    )

    local_cosines = references[np.asarray(members, dtype=np.intp)] @ candidate
    similarities_01 = np.clip((local_cosines + 1.0) * 0.5, 0.0, 1.0)
    descending = np.sort(similarities_01)[::-1]
    top3 = descending[: min(3, descending.size)]
    reference_count = float(references.shape[0])

    values = np.asarray(
        [
            float(descending[0]),
            float(top3.mean()),
            float(descending.mean()),
            p_value,
            interval_high - interval_low,
            len(members) / reference_count,
            effective / reference_count,
            1.0 - distances[0],
            1.0 - distances[1],
            1.0 - distances[2],
        ],
        dtype=np.float32,
    )
    return PreferenceFeatures(values)

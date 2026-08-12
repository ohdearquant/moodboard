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
import math
from collections.abc import Sequence
from dataclasses import dataclass
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
    "build_preference_features",
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


@dataclass(frozen=True, slots=True)
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

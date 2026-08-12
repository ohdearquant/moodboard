from __future__ import annotations

import hashlib

import numpy as np
import pytest

from moodboard.preference import (
    FEATURE_NAMES,
    FEATURE_PRODUCER_ID,
    FEATURE_PRODUCER_REVISION,
    FEATURE_SCHEMA_CANONICAL_JSON,
    FEATURE_SCHEMA_ID,
    FEATURE_SEMANTICS_CANONICAL_JSON,
    build_preference_features,
)


def test_feature_schema_identity_matches_khive_adr_149() -> None:
    assert FEATURE_NAMES == (
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
    assert hashlib.sha256(FEATURE_SCHEMA_CANONICAL_JSON).hexdigest() == FEATURE_SCHEMA_ID
    assert FEATURE_SCHEMA_ID == "f691fc73bf9a50d72157e21601fa579caa707bf2c448df546c63e915b4e42175"


def test_feature_producer_semantics_have_independent_identity() -> None:
    assert FEATURE_PRODUCER_REVISION == "moodboard.preference-producer.v1"
    assert hashlib.sha256(FEATURE_SEMANTICS_CANONICAL_JSON).hexdigest() == FEATURE_PRODUCER_ID
    assert b'"visual_similarity_transform":"clip((cosine+1)/2,0,1)"' in (
        FEATURE_SEMANTICS_CANONICAL_JSON
    )
    assert b'"local_effective_support_fraction":"n_eff_local/n_references"' in (
        FEATURE_SEMANTICS_CANONICAL_JSON
    )


def test_build_preference_features_has_frozen_semantics() -> None:
    candidate = np.array([1.0, 0.0], dtype=np.float64)
    references = np.array(
        [
            [1.0, 0.0],
            [0.6, 0.8],
            [0.0, 1.0],
            [-1.0, 0.0],
        ],
        dtype=np.float64,
    )

    row = build_preference_features(
        candidate_embedding=candidate,
        reference_embeddings=references,
        local_member_indices=(0, 1, 2),
        style_conformal_p=0.75,
        style_interval=(0.40, 0.85),
        local_effective_size=2.5,
        palette_distance=0.10,
        tone_distance=0.25,
        composition_distance=0.80,
    )

    # Cosines [1.0, 0.6, 0.0] map to [1.0, 0.8, 0.5] in the governed [0,1] space.
    np.testing.assert_array_equal(
        row.values,
        np.array(
            [
                1.0,
                (1.0 + 0.8 + 0.5) / 3.0,
                (1.0 + 0.8 + 0.5) / 3.0,
                0.75,
                0.45,
                0.75,
                0.625,
                0.90,
                0.75,
                0.20,
            ],
            dtype=np.float32,
        ),
    )
    assert row.as_wire() == [float(value) for value in row.values]


def test_visual_top3_uses_only_three_best_local_references() -> None:
    candidate = np.array([1.0, 0.0], dtype=np.float64)
    references = np.array([[1.0, 0.0], [0.8, 0.6], [0.6, 0.8], [0.0, 1.0]], dtype=np.float64)
    row = build_preference_features(
        candidate_embedding=candidate,
        reference_embeddings=references,
        local_member_indices=(0, 1, 2, 3),
        style_conformal_p=0.5,
        style_interval=(0.25, 0.75),
        local_effective_size=4.0,
        palette_distance=0.0,
        tone_distance=0.0,
        composition_distance=0.0,
    )

    assert row.values[1] == pytest.approx(np.float32((1.0 + 0.9 + 0.8) / 3.0))
    assert row.values[2] == pytest.approx(np.float32((1.0 + 0.9 + 0.8 + 0.5) / 4.0))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("style_conformal_p", float("nan")),
        ("style_conformal_p", 1.01),
        ("local_effective_size", -0.1),
        ("palette_distance", -0.01),
        ("tone_distance", 1.01),
    ],
)
def test_build_preference_features_rejects_noncanonical_inputs(field: str, value: float) -> None:
    arguments = {
        "candidate_embedding": np.array([1.0, 0.0]),
        "reference_embeddings": np.array([[1.0, 0.0], [0.0, 1.0]]),
        "local_member_indices": (0, 1),
        "style_conformal_p": 0.5,
        "style_interval": (0.25, 0.75),
        "local_effective_size": 2.0,
        "palette_distance": 0.25,
        "tone_distance": 0.25,
        "composition_distance": 0.25,
    }
    arguments[field] = value
    with pytest.raises(ValueError):
        build_preference_features(**arguments)


def test_build_preference_features_rejects_bad_geometry_and_local_members() -> None:
    base = {
        "candidate_embedding": np.array([1.0, 0.0]),
        "reference_embeddings": np.array([[1.0, 0.0], [0.0, 1.0]]),
        "local_member_indices": (0, 1),
        "style_conformal_p": 0.5,
        "style_interval": (0.25, 0.75),
        "local_effective_size": 2.0,
        "palette_distance": 0.25,
        "tone_distance": 0.25,
        "composition_distance": 0.25,
    }

    for override in (
        {"candidate_embedding": np.array([2.0, 0.0])},
        {"reference_embeddings": np.array([[1.0, 0.0], [0.0, 2.0]])},
        {"local_member_indices": ()},
        {"local_member_indices": (0, 0)},
        {"local_member_indices": (0, 2)},
        {"local_effective_size": 2.1},
        {"style_interval": (0.75, 0.25)},
    ):
        with pytest.raises(ValueError):
            build_preference_features(**(base | override))

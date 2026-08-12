from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import numpy as np
import pytest

from moodboard.preference import (
    FEATURE_NAMES,
    FEATURE_PRODUCER_ID,
    FEATURE_PRODUCER_REVISION,
    FEATURE_SCHEMA_CANONICAL_JSON,
    FEATURE_SCHEMA_ID,
    FEATURE_SEMANTICS_CANONICAL_JSON,
    PreferenceCandidate,
    PreferenceFeatureArtifact,
    build_preference_features,
    read_preference_feature_artifact,
    write_preference_feature_artifact,
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


def _feature_row(offset: float) -> np.ndarray:
    return np.linspace(offset, offset + 0.09, 10, dtype=np.float32)


def test_preference_feature_artifact_round_trips_and_binds_candidate_pool(tmp_path: Path) -> None:
    first_id = str(uuid.UUID("00000000-0000-4000-8000-000000000001"))
    second_id = str(uuid.UUID("00000000-0000-4000-8000-000000000002"))
    artifact = PreferenceFeatureArtifact.build(
        board_entity_id="00000000-0000-4000-8000-000000000010",
        board_id="a" * 64,
        model_key="moodboard_" + "b" * 64 + "_1024",
        descriptor_fingerprint="b" * 64,
        source_report_sha256="c" * 64,
        candidates=(
            PreferenceCandidate(
                label="first.png",
                asset_id=first_id,
                content_ref="d" * 64,
                source_rank=1,
                features=_feature_row(0.1),
            ),
            PreferenceCandidate(
                label="second.png",
                asset_id=second_id,
                content_ref="e" * 64,
                source_rank=2,
                features=_feature_row(0.2),
            ),
        ),
    )
    destination = tmp_path / "features.json"

    write_preference_feature_artifact(artifact, destination)
    loaded = read_preference_feature_artifact(destination)

    assert loaded == artifact
    assert loaded.board_entity_id == "00000000-0000-4000-8000-000000000010"
    assert loaded.schema_version == "moodboard.preference-feature-artifact.v2"
    assert len(loaded.scope_sha256) == 64
    assert loaded.feature_schema_id == FEATURE_SCHEMA_ID
    assert loaded.producer_id == FEATURE_PRODUCER_ID
    assert len(loaded.candidate_pool_sha256) == 64
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["candidates"][0]["features"] == artifact.candidates[0].features.as_wire()


def test_candidate_pool_identity_moves_with_features_but_not_display_label() -> None:
    common = {
        "asset_id": "00000000-0000-4000-8000-000000000001",
        "content_ref": "d" * 64,
        "source_rank": 1,
    }
    first = PreferenceFeatureArtifact.build(
        board_entity_id="00000000-0000-4000-8000-000000000010",
        board_id="a" * 64,
        model_key="moodboard_" + "b" * 64 + "_1024",
        descriptor_fingerprint="b" * 64,
        source_report_sha256="c" * 64,
        candidates=(PreferenceCandidate(label="before.png", features=_feature_row(0.1), **common),),
    )
    renamed = PreferenceFeatureArtifact.build(
        board_entity_id=first.board_entity_id,
        board_id=first.board_id,
        model_key=first.model_key,
        descriptor_fingerprint=first.descriptor_fingerprint,
        source_report_sha256=first.source_report_sha256,
        candidates=(PreferenceCandidate(label="after.png", features=_feature_row(0.1), **common),),
    )
    moved = PreferenceFeatureArtifact.build(
        board_entity_id=first.board_entity_id,
        board_id=first.board_id,
        model_key=first.model_key,
        descriptor_fingerprint=first.descriptor_fingerprint,
        source_report_sha256=first.source_report_sha256,
        candidates=(
            PreferenceCandidate(label="before.png", features=_feature_row(0.11), **common),
        ),
    )

    assert renamed.candidate_pool_sha256 == first.candidate_pool_sha256
    assert moved.candidate_pool_sha256 != first.candidate_pool_sha256


def test_preference_feature_artifact_reader_rejects_tampering_and_unknown_keys(
    tmp_path: Path,
) -> None:
    artifact = PreferenceFeatureArtifact.build(
        board_entity_id="00000000-0000-4000-8000-000000000010",
        board_id="a" * 64,
        model_key="moodboard_" + "b" * 64 + "_1024",
        descriptor_fingerprint="b" * 64,
        source_report_sha256="c" * 64,
        candidates=(
            PreferenceCandidate(
                label="candidate.png",
                asset_id="00000000-0000-4000-8000-000000000001",
                content_ref="d" * 64,
                source_rank=1,
                features=_feature_row(0.1),
            ),
        ),
    )
    destination = tmp_path / "features.json"
    write_preference_feature_artifact(artifact, destination)
    document = json.loads(destination.read_text(encoding="utf-8"))

    for mutation in (
        lambda value: value.update({"unexpected": True}),
        lambda value: value["candidates"][0]["features"].__setitem__(0, 0.9),
        lambda value: value.__setitem__("feature_schema_id", "0" * 64),
        lambda value: value.__setitem__("producer_id", "0" * 64),
        lambda value: value.__setitem__(
            "board_entity_id", "00000000-0000-4000-8000-000000000011"
        ),
    ):
        changed = json.loads(json.dumps(document))
        mutation(changed)
        destination.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError):
            read_preference_feature_artifact(destination)


def test_preference_artifact_scope_identity_moves_with_board_entity() -> None:
    common = {
        "board_id": "a" * 64,
        "model_key": "moodboard_" + "b" * 64 + "_1024",
        "descriptor_fingerprint": "b" * 64,
        "source_report_sha256": "c" * 64,
        "candidates": (
            PreferenceCandidate(
                label="candidate.png",
                asset_id="00000000-0000-4000-8000-000000000001",
                content_ref="d" * 64,
                source_rank=1,
                features=_feature_row(0.1),
            ),
        ),
    }
    first = PreferenceFeatureArtifact.build(
        board_entity_id="00000000-0000-4000-8000-000000000010", **common
    )
    second = PreferenceFeatureArtifact.build(
        board_entity_id="00000000-0000-4000-8000-000000000011", **common
    )

    assert first.candidate_pool_sha256 == second.candidate_pool_sha256
    assert first.scope_sha256 != second.scope_sha256


def test_preference_artifact_rejects_non_hex_scope_digest_inputs() -> None:
    with pytest.raises(ValueError, match="board_id"):
        PreferenceFeatureArtifact.build(
            board_entity_id="00000000-0000-4000-8000-000000000010",
            board_id="G" * 64,
            model_key="moodboard_" + "b" * 64 + "_1024",
            descriptor_fingerprint="b" * 64,
            source_report_sha256="c" * 64,
            candidates=(
                PreferenceCandidate(
                    label="candidate.png",
                    asset_id="00000000-0000-4000-8000-000000000001",
                    content_ref="d" * 64,
                    source_rank=1,
                    features=_feature_row(0.1),
                ),
            ),
        )


def test_preference_artifact_model_key_must_bind_descriptor_fingerprint() -> None:
    with pytest.raises(ValueError, match="model_key"):
        PreferenceFeatureArtifact.build(
            board_entity_id="00000000-0000-4000-8000-000000000010",
            board_id="a" * 64,
            model_key="moodboard_" + "0" * 64 + "_1024",
            descriptor_fingerprint="b" * 64,
            source_report_sha256="c" * 64,
            candidates=(
                PreferenceCandidate(
                    label="candidate.png",
                    asset_id="00000000-0000-4000-8000-000000000001",
                    content_ref="d" * 64,
                    source_rank=1,
                    features=_feature_row(0.1),
                ),
            ),
        )

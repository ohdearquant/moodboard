"""Contracts for the offline real-artifact authority used by the OpenRouter E2E.

The one-shot evaluation must not invent board or retrieval identities.  These tests build closed
artifacts with the production writers, load them through the public readers, and independently
pin the two retrieval digests so the implementation cannot make a label look authoritative.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rfc8785

import eval.openrouter_real_e2e_authority as authority_module
from eval.openrouter_real_e2e_authority import (
    ELIGIBLE_CORPUS_IDENTITY_VERSION,
    ROUTE_POLICY_IDENTITY_VERSION,
    ROUTE_POLICY_VERSION,
    OpenRouterRealE2EAuthorityError,
    load_openrouter_real_e2e_authority,
)
from moodboard.board import (
    board_fit_policy_id,
    board_representation_id,
    build_board,
    read_board,
    write_board,
)
from moodboard.pixel_rag import (
    compile_pixel_rag_artifact,
    write_pixel_rag_artifact,
)
from tests.test_pixel_rag import _add_evidence_bindings, _manifest, _measurements


def _identity(domain: str, projection: dict[str, Any]) -> str:
    return hashlib.sha256(domain.encode("utf-8") + b"\0" + rfc8785.dumps(projection)).hexdigest()


def _artifacts(tmp_path: Path, *, measured: bool = True) -> tuple[Path, Path, dict[str, Any]]:
    board_path = tmp_path / "authority.brand.mb"
    board = build_board(
        name="OpenRouter real E2E authority fixture",
        reference_ids=("reference-a", "reference-b", "reference-c"),
        reference_content_hashes=("a" * 64, "b" * 64, "c" * 64),
        reference_embeddings=np.eye(3, dtype=np.float32),
        model_repo="khive:qwen3.5-vlm-pooled-visual",
        model_revision="0123456789abcdef" * 4,
        metric="cosine",
        k=2,
        cluster_cut=0.35,
        dup_cut=0.05,
        n_eff=3.0,
        built_at="2026-08-17T03:15:00Z",
    )
    write_board(board, board_path)

    pixel_root = tmp_path / "pixel"
    manifest_path, by_id = _manifest(pixel_root)
    measurements_path = _measurements(
        pixel_root / "measurements.json",
        by_id,
        status="measured_run" if measured else "contract_fixture",
    )
    if measured:
        measurements = json.loads(measurements_path.read_text(encoding="utf-8"))
        for intent in measurements["intents"]:
            intent["relevance_judgments"] = None
        measurements_path.write_text(
            json.dumps(measurements, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        _add_evidence_bindings(measurements_path, pixel_root)
    pixel = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )
    pixel_path = pixel_root / "pixel-rag-artifact.json"
    write_pixel_rag_artifact(pixel, pixel_path)
    return board_path, pixel_path, pixel


def _eligible_projection(pixel: dict[str, Any]) -> dict[str, Any]:
    local = next(intent for intent in pixel["intents"] if intent["id"] == "local_replace")
    hard_filter = local["route"]["hard_filter"]
    return {
        "schema_version": ELIGIBLE_CORPUS_IDENTITY_VERSION,
        "source_manifest": {
            "catalog_sha256": pixel["source_manifest"]["catalog_sha256"],
            "dataset_id": pixel["source_manifest"]["dataset_id"],
            "manifest_sha256": pixel["source_manifest"]["manifest_sha256"],
        },
        "field": hard_filter["field"],
        "operator": hard_filter["operator"],
        "value": hard_filter["value"],
        "assets": sorted(
            (
                {"asset_id": row["asset_id"], "content_ref": row["content_ref"]}
                for row in local["retrieval"]["exact_score_order"]
            ),
            key=lambda row: (row["asset_id"], row["content_ref"]),
        ),
    }


def test_loads_closed_artifacts_and_derives_exact_authority(tmp_path: Path) -> None:
    board_path, pixel_path, pixel = _artifacts(tmp_path)

    authority = load_openrouter_real_e2e_authority(
        board_path=board_path,
        pixel_rag_path=pixel_path,
    )

    board = read_board(board_path)
    local = next(intent for intent in pixel["intents"] if intent["id"] == "local_replace")
    assert authority.board_id == board.board_id
    assert authority.representation_id == board_representation_id(board)
    assert authority.fit_policy_id == board_fit_policy_id(board)
    assert authority.evidence_artifact_id == pixel["artifact_id"]
    assert authority.board_artifact_sha256 == hashlib.sha256(board_path.read_bytes()).hexdigest()
    assert (
        authority.pixel_rag_artifact_sha256 == hashlib.sha256(pixel_path.read_bytes()).hexdigest()
    )
    assert authority.board_artifact_bytes == board_path.read_bytes()
    assert authority.pixel_rag_artifact_bytes == pixel_path.read_bytes()

    expected_cards = local["retrieval"]["ranked_evidence"]
    assert [reference.manifest_asset_id for reference in authority.references] == [
        card["asset_id"] for card in expected_cards
    ]
    assert [reference.khive_record_id for reference in authority.references] == [
        card["khive"]["record_id"] for card in expected_cards
    ]
    assert [reference.content_ref for reference in authority.references] == [
        card["khive"]["content_ref"] for card in expected_cards
    ]
    assert [reference.content_sha256 for reference in authority.references] == [
        card["sha256"] for card in expected_cards
    ]
    assert [reference.routed_rank for reference in authority.references] == [1, 2, 3]
    assert [reference.source_search_rank for reference in authority.references] == [
        card["source_search_rank"] for card in expected_cards
    ]
    assert [reference.source_similarity for reference in authority.references] == [
        card["score"]["value"] for card in expected_cards
    ]
    assert {reference.collection for reference in authority.references} == {"fruit-lemon"}

    eligible_projection = _eligible_projection(pixel)
    eligible_digest = _identity(ELIGIBLE_CORPUS_IDENTITY_VERSION, eligible_projection)
    assert authority.eligible_corpus_sha256 == eligible_digest
    assert [member.manifest_asset_id for member in authority.eligible_corpus] == [
        row["asset_id"] for row in eligible_projection["assets"]
    ]
    route_projection = {
        "schema_version": ROUTE_POLICY_VERSION,
        "eligible_corpus_sha256": eligible_digest,
        "namespace": local["route"]["namespace"],
        "field": "collection",
        "operator": "equals",
        "value": "fruit-lemon",
        "empty_result_policy": "no_ungated_fallback",
        "interpretation": "structural_routing_control_not_learned_retrieval_quality",
    }
    assert authority.route_policy_id == _identity(ROUTE_POLICY_IDENTITY_VERSION, route_projection)


def test_result_and_nested_rows_are_frozen_and_raw_bytes_are_repr_safe(tmp_path: Path) -> None:
    board_path, pixel_path, _pixel = _artifacts(tmp_path)
    authority = load_openrouter_real_e2e_authority(
        board_path=board_path,
        pixel_rag_path=pixel_path,
    )

    with pytest.raises(FrozenInstanceError):
        authority.board_id = "f" * 64  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        authority.references[0].content_ref = "f" * 64  # type: ignore[misc]
    rendered = repr(authority)
    assert authority.board_artifact_bytes.hex() not in rendered
    assert authority.pixel_rag_artifact_bytes.hex() not in rendered
    assert "board_artifact_bytes=" not in rendered
    assert "pixel_rag_artifact_bytes=" not in rendered


@pytest.mark.parametrize(
    ("target", "payload", "code"),
    [
        ("board", b"not a board", "board_artifact_invalid"),
        ("pixel", b'{"not":"a pixel artifact"}\n', "pixel_rag_artifact_invalid"),
    ],
)
def test_invalid_artifacts_fail_with_stable_secret_free_codes(
    tmp_path: Path,
    target: str,
    payload: bytes,
    code: str,
) -> None:
    board_path, pixel_path, _pixel = _artifacts(tmp_path)
    selected = board_path if target == "board" else pixel_path
    selected.write_bytes(payload)

    with pytest.raises(OpenRouterRealE2EAuthorityError) as captured:
        load_openrouter_real_e2e_authority(
            board_path=board_path,
            pixel_rag_path=pixel_path,
        )

    assert captured.value.code == code
    assert str(captured.value) == code
    assert str(selected) not in str(captured.value)
    assert "not a board" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True


def test_fifo_authority_path_is_rejected_without_blocking(tmp_path: Path) -> None:
    board_path, pixel_path, _pixel = _artifacts(tmp_path)
    board_path.unlink()
    os.mkfifo(board_path, 0o600)

    with pytest.raises(OpenRouterRealE2EAuthorityError) as captured:
        load_openrouter_real_e2e_authority(
            board_path=board_path,
            pixel_rag_path=pixel_path,
        )

    assert captured.value.code == "board_artifact_unavailable"


def test_reader_exception_details_never_escape_the_public_error(
    monkeypatch, tmp_path: Path
) -> None:
    board_path, pixel_path, _pixel = _artifacts(tmp_path)

    def explode(_path: Path):
        raise RuntimeError("credential-looking-private-sentinel")

    monkeypatch.setattr(authority_module, "read_board", explode)
    with pytest.raises(OpenRouterRealE2EAuthorityError) as captured:
        load_openrouter_real_e2e_authority(
            board_path=board_path,
            pixel_rag_path=pixel_path,
        )

    assert captured.value.code == "board_artifact_invalid"
    assert "sentinel" not in str(captured.value)


def test_contract_fixture_cannot_be_promoted_to_real_evidence(tmp_path: Path) -> None:
    board_path, pixel_path, _pixel = _artifacts(tmp_path, measured=False)

    with pytest.raises(OpenRouterRealE2EAuthorityError) as captured:
        load_openrouter_real_e2e_authority(
            board_path=board_path,
            pixel_rag_path=pixel_path,
        )

    assert captured.value.code == "pixel_rag_evidence_not_measured"


def test_public_pixel_read_and_validation_are_both_used(monkeypatch, tmp_path: Path) -> None:
    board_path, pixel_path, _pixel = _artifacts(tmp_path)
    calls: list[str] = []
    original_read = authority_module.read_pixel_rag_artifact
    original_validate = authority_module.validate_pixel_rag_artifact

    def observed_read(path: Path):
        calls.append("read")
        return original_read(path)

    def observed_validate(value):
        calls.append("validate")
        return original_validate(value)

    monkeypatch.setattr(authority_module, "read_pixel_rag_artifact", observed_read)
    monkeypatch.setattr(authority_module, "validate_pixel_rag_artifact", observed_validate)

    load_openrouter_real_e2e_authority(
        board_path=board_path,
        pixel_rag_path=pixel_path,
    )

    assert calls == ["read", "validate"]

"""RED tests for ADR-0014's board sub-identities.

The intent packet carries the existing whole-board hash plus two narrower identities.  These
tests deliberately derive their expected bytes independently from the production helpers so a
projection or domain-tag drift cannot make both sides agree by accident.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

import numpy as np
import rfc8785

from moodboard.board import (
    FIT_POLICY_SCHEMA,
    BrandBoard,
    board_fit_policy_id,
    board_representation_id,
    build_board,
)

_REPRESENTATION_DOMAIN = "moodboard.board-representation.v1"
_FIT_POLICY_DOMAIN = "moodboard-fit-policy.v1"


def _board() -> BrandBoard:
    return build_board(
        name="ADR-0014 golden board",
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
        built_at="2026-08-16T20:30:00Z",
    )


def _independent_identity(domain_tag: str, projection: dict[str, Any]) -> str:
    return hashlib.sha256(
        domain_tag.encode("utf-8") + b"\0" + rfc8785.dumps(projection)
    ).hexdigest()


def test_board_representation_identity_has_an_exact_rfc8785_golden_vector() -> None:
    board = _board()
    projection = {
        "model_repo": board.model_repo,
        "model_revision": board.model_revision,
        "model_dim": board.model_dim,
    }

    expected = _independent_identity(_REPRESENTATION_DOMAIN, projection)

    assert expected == "bc8c6490f9e896b7113cde1bab860ca7e73b11e0814ff771134864a4f3af3e62"
    assert board_representation_id(board) == expected
    assert board_representation_id(board) == board_representation_id(board)


def test_board_fit_policy_identity_has_an_exact_rfc8785_golden_vector() -> None:
    board = _board()
    projection = {
        "schema_version": FIT_POLICY_SCHEMA,
        "metric": board.metric,
        "k": board.k,
        "k_cap": board.k_cap,
        "cluster_cut": board.cluster_cut,
        "dup_cut": board.dup_cut,
        "min_category_size": board.min_category_size,
        "interval_level": board.interval_level,
        "far_outlier_iqr_multiplier": board.far_outlier_iqr_multiplier,
    }

    expected = _independent_identity(_FIT_POLICY_DOMAIN, projection)

    assert expected == "161428658c8d7dfd7625d3a9633e556adf08ac3b80035cc2098cb7dc25748682"
    assert board_fit_policy_id(board) == expected
    assert board_fit_policy_id(board) == board_fit_policy_id(board)


def test_fit_policy_source_is_provenance_only_but_a_score_moving_field_is_not() -> None:
    board = _board()

    renamed_source = replace(
        board,
        far_outlier_iqr_multiplier_source="registry/v2.json#/far_outlier",
    )
    changed_threshold = replace(board, far_outlier_iqr_multiplier=2.0)

    assert board_fit_policy_id(renamed_source) == board_fit_policy_id(board)
    assert board_fit_policy_id(changed_threshold) != board_fit_policy_id(board)


def test_sub_identities_are_stable_when_unrelated_board_provenance_changes() -> None:
    board = _board()
    provenance_only = replace(
        board,
        name="A renamed display board",
        built_at="2026-08-17T01:02:03Z",
    )

    assert board_representation_id(provenance_only) == board_representation_id(board)
    assert board_fit_policy_id(provenance_only) == board_fit_policy_id(board)


def test_representation_and_fit_identities_have_distinct_domains() -> None:
    board = _board()

    assert board_representation_id(board) != board_fit_policy_id(board)

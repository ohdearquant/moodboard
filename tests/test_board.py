"""Tests for `moodboard.board`: the board hash (ADR-0005) and the `brand.mb` artifact.

All fixtures here are synthetic. No dataset download, no real encoder: `board.py` never
imports `encoders.py` or `conformal.py`, so its tests exercise only what it actually owns,
the hash and the artifact container, against plain arrays and strings a caller would hand it.
"""

import json
import zipfile

import numpy as np
import pytest

from moodboard.board import (
    BOARD_HASH_VERSION,
    BrandBoard,
    board_hash,
    build_board,
    read_board,
    write_board,
)


def _hashes(n: int, prefix: str = "ref") -> list[str]:
    return [f"{prefix}-{i:02d}-sha256deadbeef" for i in range(n)]


def _embeddings(n: int, dim: int = 6, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vectors = rng.normal(size=(n, dim)).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / norms


class TestBoardHash:
    def test_deterministic_for_identical_arguments(self):
        h1 = board_hash(_hashes(5), "classical-v1", "1", "cosine", 5, 0.35, 0.05)
        h2 = board_hash(_hashes(5), "classical-v1", "1", "cosine", 5, 0.35, 0.05)
        assert h1 == h2

    def test_is_a_sha256_hex_digest(self):
        h = board_hash(_hashes(3), "classical-v1", "1", "cosine", 5, 0.35, 0.05)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_stable_under_reordering_references(self):
        refs = _hashes(8)
        shuffled = list(reversed(refs))
        h1 = board_hash(refs, "classical-v1", "1", "cosine", 5, 0.35, 0.05)
        h2 = board_hash(shuffled, "classical-v1", "1", "cosine", 5, 0.35, 0.05)
        assert h1 == h2

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda kw: kw.update(model_repo="clip-vit-l"),
            lambda kw: kw.update(model_revision="2"),
            lambda kw: kw.update(metric="euclidean"),
            lambda kw: kw.update(k=4),
            lambda kw: kw.update(cluster_cut=0.36),
            lambda kw: kw.update(dup_cut=0.06),
        ],
    )
    def test_changes_when_any_fitting_parameter_changes(self, mutate):
        base = dict(
            reference_content_hashes=_hashes(5),
            model_repo="classical-v1",
            model_revision="1",
            metric="cosine",
            k=5,
            cluster_cut=0.35,
            dup_cut=0.05,
        )
        baseline = board_hash(**base)
        mutate(base)
        mutated = board_hash(**base)
        assert baseline != mutated

    def test_changes_when_reference_set_changes(self):
        h1 = board_hash(_hashes(5), "classical-v1", "1", "cosine", 5, 0.35, 0.05)
        h2 = board_hash(_hashes(6), "classical-v1", "1", "cosine", 5, 0.35, 0.05)
        assert h1 != h2

    def test_canonical_serialisation_has_no_insignificant_whitespace(self):
        # Reconstruct the exact payload board_hash hashes and check its serialisation is the
        # compact form the ADR requires, not merely that two calls agree with each other.
        refs = _hashes(3)
        payload = {
            "v": BOARD_HASH_VERSION,
            "refs": sorted(refs),
            "model": {"repo": "classical-v1", "revision": "1"},
            "fit": {"metric": "cosine", "k": 5, "cluster_cut": 0.35, "dup_cut": 0.05},
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        assert " " not in canonical
        import hashlib

        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert expected == board_hash(refs, "classical-v1", "1", "cosine", 5, 0.35, 0.05)


class TestBuildBoard:
    def _build(self, n=5, dim=6, **overrides):
        kwargs = dict(
            name="acme-fw25",
            reference_ids=[f"r{i}" for i in range(n)],
            reference_content_hashes=_hashes(n),
            reference_embeddings=_embeddings(n, dim),
            model_repo="classical-v1",
            model_revision="1",
            metric="cosine",
            k=min(5, n - 1),
            cluster_cut=0.35,
            dup_cut=0.05,
            n_eff=float(n),
            built_at="2026-08-07T00:00:00Z",
        )
        kwargs.update(overrides)
        return build_board(**kwargs)

    def test_board_id_matches_board_hash_with_the_same_arguments(self):
        n = 5
        refs = _hashes(n)
        board = self._build(n=n, reference_content_hashes=refs)
        expected = board_hash(refs, "classical-v1", "1", "cosine", board.k, 0.35, 0.05)
        assert board.board_id == expected

    def test_model_repo_and_revision_are_passed_through_from_the_encoder_fields(self):
        board = self._build(model_repo="csd-vit-l", model_revision="rev-7")
        assert board.model_repo == "csd-vit-l"
        assert board.model_revision == "rev-7"

    def test_model_dim_is_derived_from_the_embeddings_not_a_free_parameter(self):
        board = self._build(dim=9)
        assert board.model_dim == 9
        assert board.reference_embeddings.shape == (5, 9)

    def test_rejects_mismatched_id_and_hash_lengths(self):
        with pytest.raises(ValueError):
            build_board(
                name="x",
                reference_ids=["a", "b"],
                reference_content_hashes=["only-one"],
                reference_embeddings=_embeddings(2),
                model_repo="classical-v1",
                model_revision="1",
                metric="cosine",
                k=1,
                cluster_cut=0.35,
                dup_cut=0.05,
                n_eff=2.0,
                built_at="2026-08-07T00:00:00Z",
            )

    def test_rejects_embeddings_row_count_mismatch(self):
        with pytest.raises(ValueError):
            self._build(n=5, reference_embeddings=_embeddings(4))

    def test_rejects_empty_board(self):
        with pytest.raises(ValueError):
            build_board(
                name="x",
                reference_ids=[],
                reference_content_hashes=[],
                reference_embeddings=np.zeros((0, 6), dtype=np.float32),
                model_repo="classical-v1",
                model_revision="1",
                metric="cosine",
                k=0,
                cluster_cut=0.35,
                dup_cut=0.05,
                n_eff=0.0,
                built_at="2026-08-07T00:00:00Z",
            )


class TestBrandMbRoundTrip:
    def _board(self, n=6, dim=5) -> BrandBoard:
        return build_board(
            name="acme-fw25",
            reference_ids=[f"r{i}" for i in range(n)],
            reference_content_hashes=_hashes(n),
            reference_embeddings=_embeddings(n, dim, seed=1),
            model_repo="classical-v1",
            model_revision="1",
            metric="cosine",
            k=min(5, n - 1),
            cluster_cut=0.35,
            dup_cut=0.05,
            n_eff=4.5,
            built_at="2026-08-07T12:00:00Z",
        )

    def test_round_trip_preserves_every_field(self, tmp_path):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)
        loaded = read_board(path)

        assert loaded.board_id == board.board_id
        assert loaded.name == board.name
        assert loaded.reference_ids == board.reference_ids
        assert loaded.reference_content_hashes == board.reference_content_hashes
        assert loaded.model_repo == board.model_repo
        assert loaded.model_revision == board.model_revision
        assert loaded.model_dim == board.model_dim
        assert loaded.metric == board.metric
        assert loaded.k == board.k
        assert loaded.cluster_cut == board.cluster_cut
        assert loaded.dup_cut == board.dup_cut
        assert loaded.n_eff == board.n_eff
        assert loaded.built_at == board.built_at
        np.testing.assert_array_equal(loaded.reference_embeddings, board.reference_embeddings)

    def test_written_artifact_is_a_zip_with_the_expected_members(self, tmp_path):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        assert names == {"meta.json", "embeddings.npy"}

    def test_creates_parent_directories(self, tmp_path):
        board = self._board()
        path = tmp_path / "nested" / "dir" / "brand.mb"
        write_board(board, path)
        assert path.exists()

    def test_tampered_board_id_is_rejected_on_read(self, tmp_path):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)

        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))
            embeddings_bytes = archive.read("embeddings.npy")
        meta["board_id"] = "0" * 64

        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings_bytes)

        with pytest.raises(ValueError, match="board_id"):
            read_board(path)

    def test_tampered_fitting_parameter_is_rejected_on_read(self, tmp_path):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)

        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))
            embeddings_bytes = archive.read("embeddings.npy")
        meta["fit"]["cluster_cut"] = 0.99  # board_id now describes a different fit

        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings_bytes)

        with pytest.raises(ValueError, match="board_id"):
            read_board(path)

    def test_wrong_format_marker_is_rejected(self, tmp_path):
        path = tmp_path / "brand.mb"
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", json.dumps({"format": "something-else"}))
            archive.writestr("embeddings.npy", b"")

        with pytest.raises(ValueError, match="not a moodboard brand.mb artifact"):
            read_board(path)

    def test_future_format_version_is_rejected(self, tmp_path):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)

        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))
            embeddings_bytes = archive.read("embeddings.npy")
        meta["format_version"] = 999

        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings_bytes)

        with pytest.raises(ValueError, match="format version"):
            read_board(path)

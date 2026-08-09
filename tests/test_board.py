"""Tests for `moodboard.board`: the board hash (ADR-0005) and the `brand.mb` artifact.

All fixtures here are synthetic. No dataset download, no real encoder: `board.py` never
imports `encoders.py` or `conformal.py`, so its tests exercise only what it actually owns,
the hash and the artifact container, against plain arrays and strings a caller would hand it.
"""

import hashlib
import io
import json
import zipfile
from dataclasses import replace

import numpy as np
import pytest

import moodboard.board as board_module
from moodboard.board import (
    BOARD_HASH_VERSION,
    BrandBoard,
    ReferenceAssetLocation,
    board_hash,
    build_board,
    read_board,
    write_board,
)


def _hashes(n: int, prefix: str = "ref") -> list[str]:
    return [hashlib.sha256(f"{prefix}-{i:02d}".encode()).hexdigest() for i in range(n)]


def _embeddings(n: int, dim: int = 6, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vectors = rng.normal(size=(n, dim)).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / norms


def _hash_arguments(n: int, *, dim: int = 6, seed: int = 0) -> dict:
    return {
        "reference_content_hashes": _hashes(n),
        "reference_embeddings": _embeddings(n, dim, seed),
        "model_repo": "classical-v1",
        "model_revision": "1",
        "metric": "cosine",
        "k": min(5, n - 1),
        "cluster_cut": 0.35,
        "dup_cut": 0.05,
    }


class TestBoardHash:
    def test_deterministic_for_identical_arguments(self):
        arguments = _hash_arguments(5)
        h1 = board_hash(**arguments)
        h2 = board_hash(**arguments)
        assert h1 == h2

    def test_is_a_sha256_hex_digest(self):
        h = board_hash(**_hash_arguments(3))
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_stable_under_reordering_references(self):
        refs = _hashes(8)
        embeddings = _embeddings(8)
        h1 = board_hash(refs, embeddings, "classical-v1", "1", "cosine", 5, 0.35, 0.05)
        h2 = board_hash(
            list(reversed(refs)),
            embeddings[::-1],
            "classical-v1",
            "1",
            "cosine",
            5,
            0.35,
            0.05,
        )
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
            lambda kw: kw.update(k_cap=4),
            lambda kw: kw.update(min_category_size=4),
            lambda kw: kw.update(interval_level=0.8),
            lambda kw: kw.update(far_outlier_iqr_multiplier=2.0),
        ],
    )
    def test_changes_when_any_fitting_parameter_changes(self, mutate):
        base = dict(
            reference_content_hashes=_hashes(5),
            reference_embeddings=_embeddings(5),
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
        h1 = board_hash(**_hash_arguments(5))
        h2 = board_hash(**_hash_arguments(6))
        assert h1 != h2

    def test_changes_when_embedding_geometry_changes_under_the_same_source_hashes(self):
        arguments = _hash_arguments(5)
        rotated = (
            np.asarray(arguments["reference_embeddings"])
            @ np.linalg.qr(np.random.default_rng(91).normal(size=(6, 6)))[0]
        )

        assert board_hash(**arguments) != board_hash(
            **{**arguments, "reference_embeddings": rotated}
        )

    def test_canonical_serialisation_has_no_insignificant_whitespace(self):
        # Reconstruct the exact payload board_hash hashes and check its serialisation is the
        # compact form the ADR requires, not merely that two calls agree with each other.
        refs = _hashes(3)
        embeddings = _embeddings(3)
        entries = sorted(
            [content_hash, hashlib.sha256(row.astype("<f4").tobytes()).hexdigest()]
            for content_hash, row in zip(refs, embeddings, strict=True)
        )
        embedding_payload = {
            "v": 1,
            "dtype": "float32-le",
            "shape": [3, 6],
            "entries": entries,
        }
        embedding_digest = hashlib.sha256(
            json.dumps(embedding_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        payload = {
            "v": BOARD_HASH_VERSION,
            "refs": sorted(refs),
            "reference_embeddings": {
                "sha256": embedding_digest,
                "shape": [3, 6],
                "dtype": "float32-le",
            },
            "model": {"repo": "classical-v1", "revision": "1"},
            "fit": {
                "schema_version": "moodboard-fit-policy.v1",
                "metric": "cosine",
                "k": 5,
                "k_cap": 5,
                "cluster_cut": 0.35,
                "dup_cut": 0.05,
                "min_category_size": 5,
                "interval_level": 0.9,
                "far_outlier_iqr_multiplier": 1.5,
            },
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        assert " " not in canonical
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert expected == "58c686e7fc00c3520c72b9dfb72a29119570cf17169b6bbe3fb25c787ed1ab06"
        assert expected == board_hash(
            refs, embeddings, "classical-v1", "1", "cosine", 5, 0.35, 0.05
        )


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
        expected = board_hash(
            refs,
            board.reference_embeddings,
            "classical-v1",
            "1",
            "cosine",
            board.k,
            0.35,
            0.05,
        )
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

    @pytest.mark.parametrize(
        "overrides",
        [
            {"name": ""},
            {"model_repo": ""},
            {"model_revision": ""},
            {"metric": ""},
            {"built_at": ""},
            {"k": True},
            {"k": -1},
            {"k_cap": True},
            {"k_cap": 0},
            {"min_category_size": True},
            {"min_category_size": 0},
            {"cluster_cut": float("nan")},
            {"cluster_cut": float("inf")},
            {"dup_cut": float("-inf")},
            {"interval_level": float("nan")},
            {"interval_level": 0.0},
            {"interval_level": 1.0},
            {"far_outlier_iqr_multiplier": float("nan")},
            {"far_outlier_iqr_multiplier": -1.0},
            {"far_outlier_iqr_multiplier_source": ""},
            {"n_eff": float("nan")},
            {"n_eff": float("inf")},
            {"n_eff": 0.0},
            {"n_eff": 0.5},
            {"n_eff": 6.0},
        ],
    )
    def test_rejects_metadata_that_cannot_form_a_valid_current_artifact(self, overrides):
        with pytest.raises(ValueError):
            self._build(**overrides)


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
        assert loaded.k_cap == board.k_cap
        assert loaded.cluster_cut == board.cluster_cut
        assert loaded.dup_cut == board.dup_cut
        assert loaded.min_category_size == board.min_category_size
        assert loaded.interval_level == board.interval_level
        assert loaded.far_outlier_iqr_multiplier == board.far_outlier_iqr_multiplier
        assert loaded.far_outlier_iqr_multiplier_source == board.far_outlier_iqr_multiplier_source
        assert loaded.n_eff == board.n_eff
        assert loaded.built_at == board.built_at
        assert loaded.reference_embedding_digest == board.reference_embedding_digest
        assert loaded.reference_asset_location_digest == board.reference_asset_location_digest
        assert loaded.integrity_verified is True
        np.testing.assert_array_equal(loaded.reference_embeddings, board.reference_embeddings)

    def test_written_artifact_is_a_zip_with_the_expected_members(self, tmp_path):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        assert names == {"meta.json", "embeddings.npy"}

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", ""),
            ("model_repo", ""),
            ("built_at", ""),
            ("k", True),
            ("k", -1),
            ("k_cap", 0),
            ("min_category_size", 0),
            ("cluster_cut", float("nan")),
            ("dup_cut", float("inf")),
            ("interval_level", 1.0),
            ("far_outlier_iqr_multiplier", -1.0),
            ("far_outlier_iqr_multiplier_source", ""),
            ("n_eff", float("nan")),
            ("n_eff", 7.0),
        ],
    )
    def test_writer_revalidates_public_brand_board_metadata(self, tmp_path, field, value):
        board = replace(self._board(), **{field: value})

        with pytest.raises(ValueError):
            write_board(board, tmp_path / "invalid.mb")

        assert not (tmp_path / "invalid.mb").exists()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("reference_ids", ("r0", "r0", "r2", "r3", "r4", "r5")),
            ("reference_ids", ("", "r1", "r2", "r3", "r4", "r5")),
            ("reference_content_hashes", ("not-a-sha256",) + tuple(_hashes(5))),
        ],
    )
    def test_writer_rejects_invalid_public_reference_identity(self, tmp_path, field, value):
        board = replace(self._board(), **{field: value})

        with pytest.raises(ValueError, match="reference_"):
            write_board(board, tmp_path / "invalid-reference.mb")

        assert not (tmp_path / "invalid-reference.mb").exists()

    def test_a_board_without_khive_locations_uses_verified_format_three(self, tmp_path):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)

        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))

        assert meta["format_version"] == 3
        assert meta["reference_embedding_digest"]["value"] == (board.reference_embedding_digest)
        assert "reference_asset_locations" not in meta
        assert read_board(path).reference_asset_locations == ()

    def test_khive_locations_round_trip_in_format_version_three(self, tmp_path):
        n = 6
        locations = tuple(
            ReferenceAssetLocation(
                asset_id=f"00000000-0000-0000-0000-{index + 1:012d}",
                content_ref=f"{index + 1:064x}",
                byte_identity="source-bytes",
            )
            for index in range(n)
        )
        board = build_board(
            name="acme-fw25",
            reference_ids=[f"r{i}" for i in range(n)],
            reference_content_hashes=_hashes(n),
            reference_embeddings=_embeddings(n, 5, seed=1),
            model_repo="khive:qwen3.5-vlm-pooled-visual",
            model_revision="f" * 64,
            metric="cosine",
            k=5,
            cluster_cut=0.35,
            dup_cut=0.05,
            n_eff=4.5,
            built_at="2026-08-07T12:00:00Z",
            reference_asset_locations=locations,
        )
        path = tmp_path / "brand.mb"

        write_board(board, path)
        loaded = read_board(path)

        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))
        assert meta["format_version"] == 3
        assert loaded.reference_asset_locations == locations
        assert (
            loaded.reference_asset_location_digest
            == meta["reference_asset_location_digest"]["value"]
        )
        assert meta["reference_asset_locations"][0] == {
            "asset_id": locations[0].asset_id,
            "content_ref": locations[0].content_ref,
            "byte_identity": "source-bytes",
        }

    def test_storage_locations_do_not_change_the_board_hash(self):
        kwargs = dict(
            name="acme-fw25",
            reference_ids=["r0", "r1"],
            reference_content_hashes=_hashes(2),
            reference_embeddings=_embeddings(2, 5, seed=1),
            model_repo="khive:qwen3.5-vlm-pooled-visual",
            model_revision="f" * 64,
            metric="cosine",
            k=1,
            cluster_cut=0.35,
            dup_cut=0.05,
            n_eff=2.0,
            built_at="2026-08-07T12:00:00Z",
        )
        first = build_board(
            **kwargs,
            reference_asset_locations=(
                ReferenceAssetLocation(
                    "00000000-0000-0000-0000-000000000001",
                    "1" * 64,
                    "source-bytes",
                ),
                ReferenceAssetLocation(
                    "00000000-0000-0000-0000-000000000002",
                    "2" * 64,
                    "source-bytes",
                ),
            ),
        )
        moved = build_board(
            **kwargs,
            reference_asset_locations=(
                ReferenceAssetLocation(
                    "00000000-0000-0000-0000-000000000003",
                    "3" * 64,
                    "canonical-png-rendition",
                ),
                ReferenceAssetLocation(
                    "00000000-0000-0000-0000-000000000004",
                    "4" * 64,
                    "canonical-png-rendition",
                ),
            ),
        )

        assert first.board_id == moved.board_id

    def test_version_three_requires_valid_locations_when_the_catalogue_is_present(self, tmp_path):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)
        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))
            embeddings_bytes = archive.read("embeddings.npy")
        meta["reference_asset_locations"] = [
            {
                "asset_id": "not-a-uuid",
                "content_ref": "x",
                "byte_identity": "source-bytes",
            }
        ]
        meta["reference_asset_location_digest"] = {
            "algorithm": "sha256",
            "canonicalization": "sorted-source-sha256-content-ref-byte-identity-v1",
            "value": "0" * 64,
        }
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings_bytes)

        with pytest.raises(ValueError, match="reference_asset_locations"):
            read_board(path)

    @pytest.mark.parametrize("byte_identity", ["", "source", "canonical-png", None])
    def test_location_rejects_unknown_byte_identity(self, byte_identity):
        with pytest.raises(ValueError, match="byte_identity"):
            ReferenceAssetLocation(
                "00000000-0000-0000-0000-000000000001",
                "1" * 64,
                byte_identity,
            )

    def test_version_three_location_objects_are_closed(self, tmp_path):
        locations = (
            ReferenceAssetLocation(
                "00000000-0000-0000-0000-000000000001",
                "1" * 64,
                "source-bytes",
            ),
            ReferenceAssetLocation(
                "00000000-0000-0000-0000-000000000002",
                "2" * 64,
                "source-bytes",
            ),
        )
        board = build_board(
            name="closed-locations",
            reference_ids=["r0", "r1"],
            reference_content_hashes=_hashes(2),
            reference_embeddings=_embeddings(2),
            model_repo="khive:qwen3.5-vlm-pooled-visual",
            model_revision="f" * 64,
            metric="cosine",
            k=1,
            cluster_cut=0.35,
            dup_cut=0.05,
            n_eff=2.0,
            built_at="2026-08-07T12:00:00Z",
            reference_asset_locations=locations,
        )
        path = tmp_path / "brand.mb"
        write_board(board, path)
        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))
            embeddings_bytes = archive.read("embeddings.npy")
        meta["reference_asset_locations"][0]["future"] = True
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings_bytes)

        with pytest.raises(ValueError, match="exactly the keys"):
            read_board(path)

    @pytest.mark.parametrize(
        ("field", "value"),
        [("content_ref", "a" * 64), ("byte_identity", "canonical-png-rendition")],
    )
    def test_location_content_identity_is_bound_by_a_separate_catalogue_digest(
        self, tmp_path, field, value
    ):
        locations = (
            ReferenceAssetLocation(
                "00000000-0000-0000-0000-000000000001",
                "1" * 64,
                "source-bytes",
            ),
            ReferenceAssetLocation(
                "00000000-0000-0000-0000-000000000002",
                "2" * 64,
                "source-bytes",
            ),
        )
        board = build_board(
            name="location-integrity",
            reference_ids=["r0", "r1"],
            reference_content_hashes=_hashes(2),
            reference_embeddings=_embeddings(2),
            model_repo="khive:qwen3.5-vlm-pooled-visual",
            model_revision="f" * 64,
            metric="cosine",
            k=1,
            cluster_cut=0.35,
            dup_cut=0.05,
            n_eff=2.0,
            built_at="2026-08-07T12:00:00Z",
            reference_asset_locations=locations,
        )
        path = tmp_path / "brand.mb"
        write_board(board, path)
        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))
            embeddings_bytes = archive.read("embeddings.npy")
        meta["reference_asset_locations"][0][field] = value
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings_bytes)

        with pytest.raises(ValueError, match="location digest does not match"):
            read_board(path)

    def test_republished_asset_id_does_not_move_content_catalogue_or_board_identity(self, tmp_path):
        locations = (
            ReferenceAssetLocation(
                "00000000-0000-0000-0000-000000000001",
                "1" * 64,
                "source-bytes",
            ),
            ReferenceAssetLocation(
                "00000000-0000-0000-0000-000000000002",
                "2" * 64,
                "source-bytes",
            ),
        )
        board = build_board(
            name="republished",
            reference_ids=["r0", "r1"],
            reference_content_hashes=_hashes(2),
            reference_embeddings=_embeddings(2),
            model_repo="khive:qwen3.5-vlm-pooled-visual",
            model_revision="f" * 64,
            metric="cosine",
            k=1,
            cluster_cut=0.35,
            dup_cut=0.05,
            n_eff=2.0,
            built_at="2026-08-07T12:00:00Z",
            reference_asset_locations=locations,
        )
        path = tmp_path / "brand.mb"
        write_board(board, path)
        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))
            embeddings_bytes = archive.read("embeddings.npy")
        meta["reference_asset_locations"][0]["asset_id"] = "00000000-0000-0000-0000-000000000002"
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings_bytes)

        republished = read_board(path)
        assert republished.board_id == board.board_id
        assert republished.reference_asset_location_digest == (
            board.reference_asset_location_digest
        )

    def test_legacy_v1_requires_an_explicit_unverified_read(self, tmp_path):
        board = self._board()
        current = tmp_path / "current.mb"
        legacy = tmp_path / "legacy-v1.mb"
        write_board(board, current)
        with zipfile.ZipFile(current) as archive:
            meta = json.loads(archive.read("meta.json"))
            embeddings_bytes = archive.read("embeddings.npy")
        meta["format_version"] = 1
        meta.pop("reference_embedding_digest")
        legacy_payload = {
            "v": 1,
            "refs": sorted(board.reference_content_hashes),
            "model": {"repo": board.model_repo, "revision": board.model_revision},
            "fit": {
                "metric": board.metric,
                "k": board.k,
                "cluster_cut": board.cluster_cut,
                "dup_cut": board.dup_cut,
            },
        }
        meta["fit"] = legacy_payload["fit"]
        meta["board_id"] = hashlib.sha256(
            json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with zipfile.ZipFile(legacy, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings_bytes)

        with pytest.raises(ValueError, match="legacy.*not bound to board_id"):
            read_board(legacy)
        loaded = read_board(legacy, allow_legacy_unverified=True)
        assert loaded.integrity_verified is False
        assert loaded.reference_embedding_digest is None
        with pytest.raises(ValueError, match="legacy-unverified"):
            write_board(loaded, tmp_path / "must-rebuild.mb")

    def test_creates_parent_directories(self, tmp_path):
        board = self._board()
        path = tmp_path / "nested" / "dir" / "brand.mb"
        write_board(board, path)
        assert path.exists()

    @pytest.mark.parametrize("member", ["meta.json", "embeddings.npy"])
    def test_reader_rejects_duplicate_archive_members(self, tmp_path, member):
        board = self._board()
        source = tmp_path / "source.mb"
        tampered = tmp_path / "duplicate.mb"
        write_board(board, source)
        with zipfile.ZipFile(source) as archive:
            meta = archive.read("meta.json")
            embeddings = archive.read("embeddings.npy")
        with zipfile.ZipFile(tampered, mode="w") as archive:
            archive.writestr("meta.json", meta)
            archive.writestr("embeddings.npy", embeddings)
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr(member, meta if member == "meta.json" else embeddings)

        with pytest.raises(ValueError, match="exactly one"):
            read_board(tampered)

    def test_reader_rejects_unknown_archive_members_and_oversized_meta(self, tmp_path, monkeypatch):
        board = self._board()
        source = tmp_path / "source.mb"
        extra = tmp_path / "extra.mb"
        write_board(board, source)
        with zipfile.ZipFile(source) as archive:
            meta = archive.read("meta.json")
            embeddings = archive.read("embeddings.npy")
        with zipfile.ZipFile(extra, mode="w") as archive:
            archive.writestr("meta.json", meta)
            archive.writestr("embeddings.npy", embeddings)
            archive.writestr("surprise.txt", "no")
        with pytest.raises(ValueError, match="exactly one"):
            read_board(extra)

        monkeypatch.setattr(board_module, "_MAX_META_BYTES", len(meta) - 1)
        with pytest.raises(ValueError, match="meta.json.*maximum"):
            read_board(source)

    def test_current_reader_rejects_zero_references_and_open_metadata(self, tmp_path):
        board = self._board()
        source = tmp_path / "source.mb"
        write_board(board, source)
        with zipfile.ZipFile(source) as archive:
            meta = json.loads(archive.read("meta.json"))
            embeddings = archive.read("embeddings.npy")
        meta["reference_ids"] = []
        meta["reference_content_hashes"] = []
        with zipfile.ZipFile(source, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings)
        with pytest.raises(ValueError, match="at least one reference"):
            read_board(source)

        meta["reference_ids"] = list(board.reference_ids)
        meta["reference_content_hashes"] = list(board.reference_content_hashes)
        meta["future"] = True
        with zipfile.ZipFile(source, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings)
        with pytest.raises(ValueError, match="unknown or missing keys"):
            read_board(source)

    def test_failed_atomic_replace_preserves_an_existing_board(self, tmp_path, monkeypatch):
        original = self._board()
        path = tmp_path / "brand.mb"
        write_board(original, path)
        before = path.read_bytes()
        replacement = self._board()

        def fail_replace(source, destination):
            raise OSError("injected replace failure")

        monkeypatch.setattr(board_module.os, "replace", fail_replace)
        with pytest.raises(OSError, match="injected replace failure"):
            write_board(replacement, path)

        assert path.read_bytes() == before
        assert not list(tmp_path.glob(".brand.mb.*.tmp"))

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

    def test_orthogonal_rotation_of_all_rows_is_rejected_even_though_geometry_is_preserved(
        self, tmp_path
    ):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)
        with zipfile.ZipFile(path) as archive:
            meta = archive.read("meta.json")
        rotation = np.linalg.qr(np.random.default_rng(44).normal(size=(5, 5)))[0]
        rotated = (board.reference_embeddings.astype(np.float64) @ rotation).astype("<f4")
        np.testing.assert_allclose(
            rotated @ rotated.T,
            board.reference_embeddings @ board.reference_embeddings.T,
            atol=2e-7,
        )
        embeddings_buf = io.BytesIO()
        np.save(embeddings_buf, rotated, allow_pickle=False)
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", meta)
            archive.writestr("embeddings.npy", embeddings_buf.getvalue())

        with pytest.raises(ValueError, match="embedding digest does not match"):
            read_board(path)

    @pytest.mark.parametrize(
        ("kind", "message"),
        [
            ("float64", "float32 matrix size"),
            ("int32", "little-endian float32"),
            ("wrong-shape", "does not match"),
            ("wrong-rows", "does not match"),
            ("non-finite", "non-finite"),
            ("infinite", "non-finite"),
            ("bad-norm", "unit-normalized"),
            ("zero-row", "unit-normalized"),
        ],
    )
    def test_reader_rejects_noncanonical_or_invalid_embedding_matrices(
        self, tmp_path, kind, message
    ):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)
        with zipfile.ZipFile(path) as archive:
            meta = archive.read("meta.json")
        changed = np.array(board.reference_embeddings, copy=True)
        if kind == "float64":
            changed = changed.astype(np.float64)
        elif kind == "int32":
            changed = changed.astype(np.int32)
        elif kind == "wrong-shape":
            changed = changed[:, :-1]
        elif kind == "wrong-rows":
            changed = changed[:-1]
        elif kind == "non-finite":
            changed[0, 0] = np.nan
        elif kind == "infinite":
            changed[0, 0] = np.inf
        elif kind == "bad-norm":
            changed *= np.float32(0.5)
        else:
            changed[0] = 0
        embeddings_buf = io.BytesIO()
        np.save(embeddings_buf, changed, allow_pickle=False)
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", meta)
            archive.writestr("embeddings.npy", embeddings_buf.getvalue())

        with pytest.raises(ValueError, match=message):
            read_board(path)

    def test_reader_bounds_computed_matrix_bytes_before_loading(self, tmp_path, monkeypatch):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)
        monkeypatch.setattr(
            board_module,
            "_MAX_EMBEDDING_BYTES",
            board.reference_embeddings.nbytes - 1,
        )

        with pytest.raises(ValueError, match="matrix requires.*maximum"):
            read_board(path)

    def test_reader_rejects_meta_model_dimension_drift_before_loading(self, tmp_path):
        board = self._board()
        path = tmp_path / "brand.mb"
        write_board(board, path)
        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))
            embeddings = archive.read("embeddings.npy")
        meta["model"]["dim"] += 1
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("meta.json", json.dumps(meta))
            archive.writestr("embeddings.npy", embeddings)

        with pytest.raises(ValueError, match="zip size.*does not match"):
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

"""The `brand.mb` board artifact and the board hash (ADR-0005).

This is the one place `board_id`, the value ADR-0002's report calls `board.id`, is computed.
`board_hash` is a pure function of its arguments: it does no I/O, imports nothing from the
rest of this package, and is safe to call from `report.py`'s `Board` construction, from
`build_board` below, and from `cli.py`, all with the identical result, because it is the
identical call.

`build_board` and the `brand.mb` reader/writer below own the file format, which
`INTERFACES.md` leaves to this module's own decision.
Everything they need (embeddings, the reference content hashes, the fitting parameters, the
model identity, and the already-computed `n_eff`) is produced elsewhere and handed in;
`cli.py`'s `build` command calls `encoders.py` to embed and `conformal.py` to fit before it
ever reaches this module, so `board.py` has no import-time dependency on either. The model
identity fields (`model_repo`, `model_revision`) are exactly an `Encoder`'s `name` and
`revision` attributes, passed through by the caller; this module does not define or import
the `Encoder` protocol, since a plain pair of strings is all the hash and the artifact need.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import uuid
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

BOARD_HASH_VERSION = 2
"""The literal "v" in the hashed payload. Bumped in the same change that adds a new fitting
parameter or score-bearing artifact input, per ADR-0005. This final v2 definition is the one
unreleased migration from v1 and includes embedding integrity plus the complete fit policy;
no intermediate v2 artifact was published."""

BRAND_MB_FORMAT = "moodboard-brand-mb"
BRAND_MB_FORMAT_VERSION = 3
"""Latest verified format. Versions 1 and 2 require an explicit unverified legacy read.
Format 3 is the single unreleased migration that adds both embedding integrity and the closed
runtime fit policy; no narrower intermediate format-3 artifact was published."""

_BRAND_MB_FORMAT_V1 = 1
_BRAND_MB_FORMAT_V2 = 2
_CONTENT_REF_HEX = frozenset("0123456789abcdef")
_EMBEDDING_CANONICALIZATION = "little-endian-float32-c-order-v1"
_EMBEDDING_DIGEST_VERSION = 1
_UNIT_NORM_ATOL = 1e-5
_LOCATION_CANONICALIZATION = "sorted-source-sha256-content-ref-byte-identity-v1"
_MAX_META_BYTES = 4 * 1024 * 1024
_MAX_EMBEDDING_BYTES = 64 * 1024 * 1024
_MAX_REFERENCES = 100_000
_MAX_MODEL_DIM = 8192
FIT_POLICY_SCHEMA = "moodboard-fit-policy.v1"
DEFAULT_K_CAP = 5
DEFAULT_MIN_CATEGORY_SIZE = 5
DEFAULT_INTERVAL_LEVEL = 0.9
DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER = 1.5
DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER_SOURCE = "docs/adr/0004-abstention.md"


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token!r}")


def _canonical_embedding_matrix(
    reference_embeddings: np.ndarray,
    expected_rows: int,
    *,
    require_storage_canonical: bool = False,
) -> np.ndarray:
    """Validate and return the canonical little-endian float32 reference matrix."""
    raw = np.asarray(reference_embeddings)
    if expected_rows < 1 or expected_rows > _MAX_REFERENCES:
        raise ValueError(f"reference count must be in 1..={_MAX_REFERENCES}, got {expected_rows}")
    if raw.ndim != 2 or raw.shape[0] != expected_rows or raw.shape[1] <= 0:
        raise ValueError(
            f"reference_embeddings must have shape ({expected_rows}, dim>0), one row per "
            f"reference; got {raw.shape}"
        )
    if raw.shape[1] > _MAX_MODEL_DIM:
        raise ValueError(f"reference embedding dimension exceeds {_MAX_MODEL_DIM}: {raw.shape[1]}")
    matrix_bytes = expected_rows * raw.shape[1] * np.dtype("<f4").itemsize
    if matrix_bytes > _MAX_EMBEDDING_BYTES:
        raise ValueError(
            f"reference embedding matrix requires {matrix_bytes} bytes; maximum is "
            f"{_MAX_EMBEDDING_BYTES}"
        )
    if not np.issubdtype(raw.dtype, np.number) or np.issubdtype(raw.dtype, np.complexfloating):
        raise ValueError("reference_embeddings must contain real numeric values")
    if require_storage_canonical and (raw.dtype.str != "<f4" or not raw.flags.c_contiguous):
        raise ValueError(
            "stored reference_embeddings must be little-endian float32 in C row-major order"
        )
    if require_storage_canonical and np.any((raw == 0) & np.signbit(raw)):
        raise ValueError("stored reference_embeddings contains non-canonical negative zero")

    matrix = np.array(raw, dtype=np.dtype("<f4"), order="C", copy=True)
    if not np.isfinite(matrix).all():
        raise ValueError("reference_embeddings contains a non-finite value")
    matrix[matrix == 0] = np.float32(0.0)
    norms = np.linalg.norm(matrix.astype(np.float64), axis=1)
    invalid = np.flatnonzero(~np.isclose(norms, 1.0, rtol=0.0, atol=_UNIT_NORM_ATOL))
    if invalid.size:
        index = int(invalid[0])
        raise ValueError(
            f"reference_embeddings row {index} is not unit-normalized: norm={norms[index]}"
        )
    matrix.setflags(write=False)
    return matrix


def _reference_embedding_digest(
    reference_content_hashes: Sequence[str], reference_embeddings: np.ndarray
) -> str:
    """Bind each source-content identity to its exact canonical embedding row."""
    entries = sorted(
        [content_hash, hashlib.sha256(row.tobytes(order="C")).hexdigest()]
        for content_hash, row in zip(reference_content_hashes, reference_embeddings, strict=True)
    )
    payload = {
        "v": _EMBEDDING_DIGEST_VERSION,
        "dtype": "float32-le",
        "shape": list(reference_embeddings.shape),
        "entries": entries,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _expected_npy_size(rows: int, dimensions: int) -> int:
    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        header,
        {"descr": "<f4", "fortran_order": False, "shape": (rows, dimensions)},
    )
    return len(header.getvalue()) + rows * dimensions * np.dtype("<f4").itemsize


def _legacy_board_hash(
    reference_content_hashes: Sequence[str],
    model_repo: str,
    model_revision: str,
    metric: str,
    k: int,
    cluster_cut: float,
    dup_cut: float,
) -> str:
    payload = {
        "v": 1,
        "refs": sorted(reference_content_hashes),
        "model": {"repo": model_repo, "revision": model_revision},
        "fit": {
            "metric": metric,
            "k": k,
            "cluster_cut": cluster_cut,
            "dup_cut": dup_cut,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def board_hash(
    reference_content_hashes: Sequence[str],
    reference_embeddings: np.ndarray,
    model_repo: str,
    model_revision: str,
    metric: str,
    k: int,
    cluster_cut: float,
    dup_cut: float,
    *,
    k_cap: int = DEFAULT_K_CAP,
    min_category_size: int = DEFAULT_MIN_CATEGORY_SIZE,
    interval_level: float = DEFAULT_INTERVAL_LEVEL,
    far_outlier_iqr_multiplier: float = DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER,
) -> str:
    """ADR-0005's board hash, computed in exactly one place.

    sha256 hex digest over the canonical JSON serialisation (sorted keys, no insignificant
    whitespace) of

        {"v": 2, "refs": sorted(reference_content_hashes),
         "reference_embeddings": {"sha256": ..., "shape": [n, dim],
                                   "dtype": "float32-le"},
         "model": {"repo": model_repo, "revision": model_revision},
         "fit": {"schema_version": "moodboard-fit-policy.v1", "metric": metric,
                 "k": k, "k_cap": k_cap, "cluster_cut": cluster_cut,
                 "dup_cut": dup_cut, "min_category_size": min_category_size,
                 "interval_level": interval_level,
                 "far_outlier_iqr_multiplier": far_outlier_iqr_multiplier}}

    The embedding digest is SHA-256 over a framed canonical object containing the shape and
    sorted ``[content_sha256, sha256(little-endian-float32-row)]`` pairs. It therefore binds
    every score-bearing row to its source identity while remaining stable when references and
    their rows are reordered together.

    `report.py`'s `Board.id` and the `brand.mb` artifact's own id both call this function;
    neither recomputes it independently.
    """
    content_hashes = tuple(reference_content_hashes)
    matrix = _canonical_embedding_matrix(reference_embeddings, len(content_hashes))
    embedding_digest = _reference_embedding_digest(content_hashes, matrix)
    payload = {
        "v": BOARD_HASH_VERSION,
        "refs": sorted(content_hashes),
        "reference_embeddings": {
            "sha256": embedding_digest,
            "shape": list(matrix.shape),
            "dtype": "float32-le",
        },
        "model": {"repo": model_repo, "revision": model_revision},
        "fit": {
            "schema_version": FIT_POLICY_SCHEMA,
            "metric": metric,
            "k": k,
            "k_cap": k_cap,
            "cluster_cut": cluster_cut,
            "dup_cut": dup_cut,
            "min_category_size": min_category_size,
            "interval_level": interval_level,
            "far_outlier_iqr_multiplier": far_outlier_iqr_multiplier,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReferenceAssetLocation:
    """The Khive entity, BlobStore reference, and byte contract for one reference."""

    asset_id: str
    content_ref: str
    byte_identity: Literal["source-bytes", "canonical-png-rendition"]

    def __post_init__(self) -> None:
        try:
            parsed = uuid.UUID(self.asset_id)
        except (AttributeError, ValueError) as error:
            raise ValueError("reference asset_id must be a canonical UUID") from error
        if str(parsed) != self.asset_id:
            raise ValueError("reference asset_id must be a canonical lowercase UUID")
        if (
            not isinstance(self.content_ref, str)
            or len(self.content_ref) != 64
            or not set(self.content_ref) <= _CONTENT_REF_HEX
        ):
            raise ValueError("reference content_ref must be 64 lowercase hex characters")
        if self.byte_identity not in {"source-bytes", "canonical-png-rendition"}:
            raise ValueError(
                "reference byte_identity must be 'source-bytes' or 'canonical-png-rendition'"
            )


def _reference_asset_location_digest(
    reference_content_hashes: Sequence[str],
    locations: Sequence[ReferenceAssetLocation],
) -> str:
    entries = sorted(
        [content_hash, location.content_ref, location.byte_identity]
        for content_hash, location in zip(reference_content_hashes, locations, strict=True)
    )
    canonical = json.dumps({"v": 1, "entries": entries}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_board_metadata_fields(
    *,
    name: str,
    model_repo: str,
    model_revision: str,
    metric: str,
    k: int,
    k_cap: int,
    cluster_cut: float,
    dup_cut: float,
    min_category_size: int,
    interval_level: float,
    far_outlier_iqr_multiplier: float,
    far_outlier_iqr_multiplier_source: str,
    n_eff: float,
    built_at: str,
    n_references: int,
) -> None:
    for field, value in (
        ("name", name),
        ("model_repo", model_repo),
        ("model_revision", model_revision),
        ("built_at", built_at),
        ("far_outlier_iqr_multiplier_source", far_outlier_iqr_multiplier_source),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")
    if metric != "cosine":
        raise ValueError("metric must be 'cosine' for the governed scoring engine")
    if n_references < 2:
        raise ValueError("a verified board needs at least two references")
    if not isinstance(k_cap, int) or isinstance(k_cap, bool) or k_cap < 1:
        raise ValueError("k_cap must be a positive plain integer")
    expected_k = min(k_cap, n_references - 1)
    if not isinstance(k, int) or isinstance(k, bool) or k != expected_k:
        raise ValueError(f"k must equal min(k_cap, n_references - 1) = {expected_k}; got {k!r}")
    if (
        not isinstance(min_category_size, int)
        or isinstance(min_category_size, bool)
        or min_category_size < 1
    ):
        raise ValueError("min_category_size must be a positive plain integer")
    for field, value in (("cluster_cut", cluster_cut), ("dup_cut", dup_cut)):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not np.isfinite(value)
            or not 0.0 <= value <= 2.0
        ):
            raise ValueError(f"{field} must be a finite number in [0,2]")
    if (
        isinstance(interval_level, bool)
        or not isinstance(interval_level, (int, float))
        or not np.isfinite(interval_level)
        or not 0.0 < interval_level < 1.0
    ):
        raise ValueError("interval_level must be a finite number strictly between 0 and 1")
    if (
        isinstance(far_outlier_iqr_multiplier, bool)
        or not isinstance(far_outlier_iqr_multiplier, (int, float))
        or not np.isfinite(far_outlier_iqr_multiplier)
        or far_outlier_iqr_multiplier < 0.0
    ):
        raise ValueError("far_outlier_iqr_multiplier must be a finite non-negative number")
    if (
        isinstance(n_eff, bool)
        or not isinstance(n_eff, (int, float))
        or not np.isfinite(n_eff)
        or n_eff < 1.0
        or n_eff > n_references + 1e-9
    ):
        raise ValueError(f"n_eff must be finite and lie in [1, {n_references}]")


def _validate_reference_identity(
    reference_ids: Sequence[str], reference_content_hashes: Sequence[str]
) -> None:
    if len(reference_content_hashes) != len(reference_ids):
        raise ValueError(
            "reference_ids and reference_content_hashes must describe the same references "
            "in the same order"
        )
    if any(not isinstance(item_id, str) or not item_id for item_id in reference_ids):
        raise ValueError("reference_ids must contain non-empty strings")
    if len(set(reference_ids)) != len(reference_ids):
        raise ValueError("reference_ids must be unique")
    if any(
        not isinstance(digest, str) or len(digest) != 64 or not set(digest) <= _CONTENT_REF_HEX
        for digest in reference_content_hashes
    ):
        raise ValueError("reference_content_hashes must contain 64-character lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class BrandBoard:
    """The fitted board plus the reference embeddings needed to score future candidates
    without re-embedding. `board_id` is always `board_hash(...)` called on this board's own
    fields; nothing constructs a `BrandBoard` with an id that does not match, because
    `build_board` is the only constructor and `read_board` re-verifies it on every load.
    """

    board_id: str
    name: str
    reference_ids: tuple[str, ...]
    reference_content_hashes: tuple[str, ...]
    reference_embeddings: np.ndarray  # (n, dim) float32, L2-normalised, row order == reference_ids
    model_repo: str
    model_revision: str
    model_dim: int
    metric: str
    k: int
    k_cap: int
    cluster_cut: float
    dup_cut: float
    min_category_size: int
    interval_level: float
    far_outlier_iqr_multiplier: float
    far_outlier_iqr_multiplier_source: str
    n_eff: float
    built_at: str  # RFC 3339
    reference_embedding_digest: str | None
    reference_asset_location_digest: str | None
    integrity_verified: bool
    reference_asset_locations: tuple[ReferenceAssetLocation, ...] = ()


def build_board(
    *,
    name: str,
    reference_ids: Sequence[str],
    reference_content_hashes: Sequence[str],
    reference_embeddings: np.ndarray,
    model_repo: str,
    model_revision: str,
    metric: str,
    k: int,
    cluster_cut: float,
    dup_cut: float,
    n_eff: float,
    built_at: str,
    k_cap: int = DEFAULT_K_CAP,
    min_category_size: int = DEFAULT_MIN_CATEGORY_SIZE,
    interval_level: float = DEFAULT_INTERVAL_LEVEL,
    far_outlier_iqr_multiplier: float = DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER,
    far_outlier_iqr_multiplier_source: str = DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER_SOURCE,
    reference_asset_locations: Sequence[ReferenceAssetLocation] = (),
) -> BrandBoard:
    """Assemble a `BrandBoard` from an already-fitted reference set and compute its id.

    `reference_embeddings` are the encoder's output for `reference_ids`, in the same order;
    `n_eff` is `conformal.kish_n_eff` over `conformal.duplicate_groups(reference_embeddings,
    dup_cut)` on this whole reference set, computed by the caller before this function is
    called. `model_repo` and `model_revision` are the fitting encoder's `name` and `revision`
    attributes, passed through verbatim: this is the only place those two strings enter the
    hash, and they must be the same two attributes `Provenance.model` and
    `Representation.style` read elsewhere when a report is built, or a report's declared model
    identity would disagree with the board it was scored against.
    """
    n = len(reference_ids)
    _validate_reference_identity(reference_ids, reference_content_hashes)
    if n == 0:
        raise ValueError("a board needs at least one reference")
    _validate_board_metadata_fields(
        name=name,
        model_repo=model_repo,
        model_revision=model_revision,
        metric=metric,
        k=k,
        k_cap=k_cap,
        cluster_cut=cluster_cut,
        dup_cut=dup_cut,
        min_category_size=min_category_size,
        interval_level=interval_level,
        far_outlier_iqr_multiplier=far_outlier_iqr_multiplier,
        far_outlier_iqr_multiplier_source=far_outlier_iqr_multiplier_source,
        n_eff=n_eff,
        built_at=built_at,
        n_references=n,
    )
    ids = tuple(reference_ids)
    content_hashes = tuple(reference_content_hashes)
    embeddings = _canonical_embedding_matrix(reference_embeddings, n)
    locations = tuple(reference_asset_locations)
    if locations and len(locations) != n:
        raise ValueError(
            f"reference_asset_locations has {len(locations)} entries but the board has {n} "
            "references; locations must be absent or complete and in reference order"
        )
    if any(not isinstance(location, ReferenceAssetLocation) for location in locations):
        raise ValueError("reference_asset_locations entries must be ReferenceAssetLocation values")

    board_id = board_hash(
        reference_content_hashes,
        embeddings,
        model_repo,
        model_revision,
        metric,
        k,
        cluster_cut,
        dup_cut,
        k_cap=k_cap,
        min_category_size=min_category_size,
        interval_level=interval_level,
        far_outlier_iqr_multiplier=far_outlier_iqr_multiplier,
    )
    return BrandBoard(
        board_id=board_id,
        name=name,
        reference_ids=ids,
        reference_content_hashes=content_hashes,
        reference_embeddings=embeddings,
        model_repo=model_repo,
        model_revision=model_revision,
        model_dim=embeddings.shape[1],
        metric=metric,
        k=k,
        k_cap=k_cap,
        cluster_cut=cluster_cut,
        dup_cut=dup_cut,
        min_category_size=min_category_size,
        interval_level=interval_level,
        far_outlier_iqr_multiplier=far_outlier_iqr_multiplier,
        far_outlier_iqr_multiplier_source=far_outlier_iqr_multiplier_source,
        n_eff=n_eff,
        built_at=built_at,
        reference_embedding_digest=_reference_embedding_digest(
            reference_content_hashes, embeddings
        ),
        reference_asset_location_digest=(
            _reference_asset_location_digest(reference_content_hashes, locations)
            if locations
            else None
        ),
        integrity_verified=True,
        reference_asset_locations=locations,
    )


def write_board(board: BrandBoard, path: Path) -> None:
    """Write `board` as a `brand.mb` artifact at `path`.

    The container is a zip archive (so it is inspectable with any zip tool) holding a JSON
    metadata entry and the embedding matrix as a `.npy` entry. `allow_pickle=False` on both the
    write below and the read in `read_board`: a `brand.mb` is a distributable artifact and
    unpickling one from an untrusted source must never be able to run arbitrary code.
    """
    if not board.integrity_verified:
        raise ValueError(
            "cannot write a legacy-unverified board as a verified artifact; rebuild it from "
            "its reference files"
        )
    _validate_reference_identity(board.reference_ids, board.reference_content_hashes)
    if (
        not isinstance(board.model_dim, int)
        or isinstance(board.model_dim, bool)
        or not 1 <= board.model_dim <= _MAX_MODEL_DIM
    ):
        raise ValueError(f"model_dim must be a plain integer in 1..={_MAX_MODEL_DIM}")
    _validate_board_metadata_fields(
        name=board.name,
        model_repo=board.model_repo,
        model_revision=board.model_revision,
        metric=board.metric,
        k=board.k,
        k_cap=board.k_cap,
        cluster_cut=board.cluster_cut,
        dup_cut=board.dup_cut,
        min_category_size=board.min_category_size,
        interval_level=board.interval_level,
        far_outlier_iqr_multiplier=board.far_outlier_iqr_multiplier,
        far_outlier_iqr_multiplier_source=board.far_outlier_iqr_multiplier_source,
        n_eff=board.n_eff,
        built_at=board.built_at,
        n_references=len(board.reference_ids),
    )
    embeddings = _canonical_embedding_matrix(
        board.reference_embeddings,
        len(board.reference_content_hashes),
        require_storage_canonical=True,
    )
    if embeddings.shape != (len(board.reference_ids), board.model_dim):
        raise ValueError(
            "reference_embeddings shape does not match reference_ids and stored model dimension"
        )
    embedding_digest = _reference_embedding_digest(board.reference_content_hashes, embeddings)
    if board.reference_embedding_digest != embedding_digest:
        raise ValueError(
            "reference_embedding_digest does not match the board's source-to-embedding mapping"
        )
    recomputed_id = board_hash(
        board.reference_content_hashes,
        embeddings,
        board.model_repo,
        board.model_revision,
        board.metric,
        board.k,
        board.cluster_cut,
        board.dup_cut,
        k_cap=board.k_cap,
        min_category_size=board.min_category_size,
        interval_level=board.interval_level,
        far_outlier_iqr_multiplier=board.far_outlier_iqr_multiplier,
    )
    if board.board_id != recomputed_id:
        raise ValueError(
            f"board claims board_id {board.board_id!r} but its fields hash to {recomputed_id!r}"
        )
    meta = {
        "format": BRAND_MB_FORMAT,
        "format_version": BRAND_MB_FORMAT_VERSION,
        "board_id": board.board_id,
        "name": board.name,
        "reference_ids": list(board.reference_ids),
        "reference_content_hashes": list(board.reference_content_hashes),
        "model": {
            "repo": board.model_repo,
            "revision": board.model_revision,
            "dim": board.model_dim,
        },
        "fit": {
            "schema_version": FIT_POLICY_SCHEMA,
            "metric": board.metric,
            "k": board.k,
            "k_cap": board.k_cap,
            "cluster_cut": board.cluster_cut,
            "dup_cut": board.dup_cut,
            "min_category_size": board.min_category_size,
            "interval_level": board.interval_level,
            "far_outlier_iqr_multiplier": board.far_outlier_iqr_multiplier,
            "far_outlier_iqr_multiplier_source": board.far_outlier_iqr_multiplier_source,
        },
        "n_eff": board.n_eff,
        "built_at": board.built_at,
        "reference_embedding_digest": {
            "algorithm": "sha256",
            "canonicalization": _EMBEDDING_CANONICALIZATION,
            "shape": list(embeddings.shape),
            "value": embedding_digest,
        },
    }
    if board.reference_asset_locations:
        if len(board.reference_asset_locations) != len(board.reference_ids):
            raise ValueError(
                "reference_asset_locations must contain exactly one entry per reference"
            )
        meta["reference_asset_locations"] = [
            {
                "asset_id": location.asset_id,
                "content_ref": location.content_ref,
                "byte_identity": location.byte_identity,
            }
            for location in board.reference_asset_locations
        ]
        location_digest = _reference_asset_location_digest(
            board.reference_content_hashes, board.reference_asset_locations
        )
        if board.reference_asset_location_digest != location_digest:
            raise ValueError(
                "reference_asset_location_digest does not match the board's immutable "
                "source/content location catalogue"
            )
        meta["reference_asset_location_digest"] = {
            "algorithm": "sha256",
            "canonicalization": _LOCATION_CANONICALIZATION,
            "value": location_digest,
        }
    elif board.reference_asset_location_digest is not None:
        raise ValueError(
            "reference_asset_location_digest must be absent when the board has no locations"
        )
    embeddings_buf = io.BytesIO()
    np.save(embeddings_buf, embeddings, allow_pickle=False)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "meta.json", json.dumps(meta, sort_keys=True, indent=2, allow_nan=False)
            )
            archive.writestr("embeddings.npy", embeddings_buf.getvalue())
        with temporary_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_board(path: Path, *, allow_legacy_unverified: bool = False) -> BrandBoard:
    """Read a `brand.mb` artifact back into a `BrandBoard`.

    Re-derives `board_id` from the stored fields with `board_hash` and raises `ValueError` if
    it does not match what the file claims: a `brand.mb` whose id disagrees with its own
    content is corrupt or hand-edited, and using it silently would let two callers score
    against the same file believing it was two different boards, or the reverse.

    Formats 1 and 2 predate embedding integrity. They are rejected by default because their
    stored vectors can move scores without moving their old board id. An explicit
    ``allow_legacy_unverified=True`` reads a structurally valid old artifact but marks the
    returned board ``integrity_verified=False``; it cannot be re-written as a current board.
    """
    path = Path(path)
    with zipfile.ZipFile(path, mode="r") as archive:
        members = archive.infolist()
        member_names = [member.filename for member in members]
        if len(members) != 2 or sorted(member_names) != ["embeddings.npy", "meta.json"]:
            raise ValueError(
                f"{path} must contain exactly one meta.json and one embeddings.npy member"
            )
        member_by_name = {member.filename: member for member in members}
        meta_info = member_by_name["meta.json"]
        if meta_info.file_size > _MAX_META_BYTES:
            raise ValueError(
                f"{path} meta.json is {meta_info.file_size} bytes; maximum is {_MAX_META_BYTES}"
            )
        meta_bytes = archive.read(meta_info)
        if len(meta_bytes) != meta_info.file_size:
            raise ValueError(f"{path} meta.json size disagrees with its zip member metadata")
        try:
            meta = json.loads(meta_bytes, parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"{path} meta.json is not strict JSON: {error}") from error
        if not isinstance(meta, dict):
            raise ValueError(f"{path} meta.json root must be an object")
        if meta.get("format") != BRAND_MB_FORMAT:
            raise ValueError(f"{path} is not a moodboard brand.mb artifact")
        format_version = meta.get("format_version")
        supported_versions = {
            _BRAND_MB_FORMAT_V1,
            _BRAND_MB_FORMAT_V2,
            BRAND_MB_FORMAT_VERSION,
        }
        if format_version not in supported_versions:
            raise ValueError(
                f"{path} was written by brand.mb format version {format_version!r}, "
                f"this reader supports versions 1 through {BRAND_MB_FORMAT_VERSION}"
            )
        legacy = format_version in {_BRAND_MB_FORMAT_V1, _BRAND_MB_FORMAT_V2}
        if legacy and not allow_legacy_unverified:
            raise ValueError(
                f"{path} is legacy brand.mb format version {format_version}, whose embeddings "
                "are not bound to board_id; pass allow_legacy_unverified=True only for an "
                "explicit unverified migration read"
            )

        if not legacy:
            current_keys = {
                "format",
                "format_version",
                "board_id",
                "name",
                "reference_ids",
                "reference_content_hashes",
                "model",
                "fit",
                "n_eff",
                "built_at",
                "reference_embedding_digest",
            }
            has_locations = "reference_asset_locations" in meta
            if has_locations:
                current_keys.update(
                    {"reference_asset_locations", "reference_asset_location_digest"}
                )
            if set(meta) != current_keys:
                raise ValueError(f"{path} format version 3 metadata has unknown or missing keys")

        raw_reference_ids = meta.get("reference_ids")
        raw_content_hashes = meta.get("reference_content_hashes")
        if not isinstance(raw_reference_ids, list) or not isinstance(raw_content_hashes, list):
            raise ValueError(f"{path} reference ids and content hashes must be arrays")
        reference_ids = tuple(raw_reference_ids)
        reference_content_hashes = tuple(raw_content_hashes)
        if not reference_ids:
            raise ValueError(f"{path} must contain at least one reference")
        if len(reference_ids) > _MAX_REFERENCES:
            raise ValueError(
                f"{path} has {len(reference_ids)} references; maximum is {_MAX_REFERENCES}"
            )
        if len(reference_content_hashes) != len(reference_ids):
            raise ValueError(f"{path} has different reference id and content-hash counts")
        if any(not isinstance(item_id, str) or not item_id for item_id in reference_ids):
            raise ValueError(f"{path} reference_ids must contain non-empty strings")
        if len(set(reference_ids)) != len(reference_ids):
            raise ValueError(f"{path} reference_ids must be unique")
        if any(
            not isinstance(digest, str) or len(digest) != 64 or not set(digest) <= _CONTENT_REF_HEX
            for digest in reference_content_hashes
        ):
            raise ValueError(
                f"{path} reference_content_hashes must contain lowercase SHA-256 values"
            )
        model = meta.get("model")
        if not isinstance(model, dict) or set(model) != {"repo", "revision", "dim"}:
            raise ValueError(f"{path} model must be a closed repo/revision/dim object")
        if any(
            not isinstance(model.get(key), str) or not model[key] for key in ("repo", "revision")
        ):
            raise ValueError(f"{path} model repo and revision must be non-empty strings")
        if (
            not isinstance(model.get("dim"), int)
            or isinstance(model.get("dim"), bool)
            or not 1 <= model["dim"] <= _MAX_MODEL_DIM
        ):
            raise ValueError(f"{path} model dimension must be an integer in 1..={_MAX_MODEL_DIM}")
        fit = meta.get("fit")
        legacy_fit_keys = {"metric", "k", "cluster_cut", "dup_cut"}
        current_fit_keys = {
            "schema_version",
            "metric",
            "k",
            "k_cap",
            "cluster_cut",
            "dup_cut",
            "min_category_size",
            "interval_level",
            "far_outlier_iqr_multiplier",
            "far_outlier_iqr_multiplier_source",
        }
        expected_fit_keys = legacy_fit_keys if legacy else current_fit_keys
        if not isinstance(fit, dict) or set(fit) != expected_fit_keys:
            raise ValueError(f"{path} fit must be a closed runtime-policy object")
        if not legacy and fit.get("schema_version") != FIT_POLICY_SCHEMA:
            raise ValueError(f"{path} uses an unsupported fit policy schema")
        if not isinstance(fit.get("metric"), str) or not fit["metric"]:
            raise ValueError(f"{path} fit metric must be a non-empty string")
        if (
            not isinstance(fit.get("k"), int)
            or isinstance(fit.get("k"), bool)
            or not 0 <= fit["k"] < len(reference_ids)
        ):
            raise ValueError(f"{path} fit k must be a plain integer in 0..{len(reference_ids) - 1}")
        if any(
            isinstance(fit.get(key), bool)
            or not isinstance(fit.get(key), (int, float))
            or not np.isfinite(fit[key])
            or not 0.0 <= fit[key] <= 2.0
            for key in ("cluster_cut", "dup_cut")
        ):
            raise ValueError(f"{path} fit cuts must be finite numbers")
        if not isinstance(meta.get("board_id"), str) or len(meta["board_id"]) != 64:
            raise ValueError(f"{path} board_id must be a SHA-256 string")
        if not isinstance(meta.get("name"), str) or not meta["name"]:
            raise ValueError(f"{path} name must be a non-empty string")
        if not isinstance(meta.get("built_at"), str) or not meta["built_at"]:
            raise ValueError(f"{path} built_at must be a non-empty string")
        if (
            isinstance(meta.get("n_eff"), bool)
            or not isinstance(meta.get("n_eff"), (int, float))
            or not np.isfinite(meta["n_eff"])
            or meta["n_eff"] <= 0
        ):
            raise ValueError(f"{path} n_eff must be a positive finite number")
        if not legacy:
            try:
                _validate_board_metadata_fields(
                    name=meta["name"],
                    model_repo=model["repo"],
                    model_revision=model["revision"],
                    metric=fit["metric"],
                    k=fit["k"],
                    k_cap=fit["k_cap"],
                    cluster_cut=fit["cluster_cut"],
                    dup_cut=fit["dup_cut"],
                    min_category_size=fit["min_category_size"],
                    interval_level=fit["interval_level"],
                    far_outlier_iqr_multiplier=fit["far_outlier_iqr_multiplier"],
                    far_outlier_iqr_multiplier_source=fit["far_outlier_iqr_multiplier_source"],
                    n_eff=meta["n_eff"],
                    built_at=meta["built_at"],
                    n_references=len(reference_ids),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{path} has invalid verified fit metadata: {error}") from error

        matrix_bytes = len(reference_ids) * model["dim"] * np.dtype("<f4").itemsize
        if matrix_bytes > _MAX_EMBEDDING_BYTES:
            raise ValueError(
                f"{path} reference embedding matrix requires {matrix_bytes} bytes; maximum "
                f"is {_MAX_EMBEDDING_BYTES}"
            )
        expected_npy_size = _expected_npy_size(len(reference_ids), model["dim"])
        embeddings_info = member_by_name["embeddings.npy"]
        if embeddings_info.file_size != expected_npy_size:
            raise ValueError(
                f"{path} embeddings.npy zip size {embeddings_info.file_size} does not match "
                f"the framed ({len(reference_ids)}, {model['dim']}) float32 matrix size "
                f"{expected_npy_size}"
            )
        embeddings_bytes = archive.read(embeddings_info)
        if len(embeddings_bytes) != embeddings_info.file_size:
            raise ValueError(f"{path} embeddings.npy size disagrees with its zip member metadata")

    npy_stream = io.BytesIO(embeddings_bytes)
    try:
        npy_version = np.lib.format.read_magic(npy_stream)
        if npy_version != (1, 0):
            raise ValueError(f"unsupported NPY version {npy_version}")
        npy_shape, npy_fortran_order, npy_dtype = np.lib.format.read_array_header_1_0(npy_stream)
    except (EOFError, ValueError) as error:
        raise ValueError(f"{path} embeddings.npy has an invalid bounded header: {error}") from error
    if (
        npy_shape != (len(reference_ids), model["dim"])
        or npy_fortran_order
        or npy_dtype.str != "<f4"
    ):
        raise ValueError(
            f"{path} embeddings.npy header must describe C-order little-endian float32 shape "
            f"({len(reference_ids)}, {model['dim']})"
        )
    if npy_stream.tell() + matrix_bytes != len(embeddings_bytes):
        raise ValueError(f"{path} embeddings.npy header/data framing is inconsistent")
    npy_stream.seek(0)
    embeddings = np.load(npy_stream, allow_pickle=False)
    if np.asarray(embeddings).shape != (len(reference_ids), model["dim"]):
        raise ValueError(
            f"{path} reference_embeddings shape {np.asarray(embeddings).shape} does not match "
            f"reference count {len(reference_ids)} and model dimension {model['dim']}"
        )
    canonical_embeddings = _canonical_embedding_matrix(
        embeddings,
        len(reference_ids),
        require_storage_canonical=True,
    )

    raw_locations = meta.get("reference_asset_locations")
    if format_version == _BRAND_MB_FORMAT_V1:
        if raw_locations is not None:
            raise ValueError(
                f"{path} is format version 1 but carries reference_asset_locations, which "
                "belongs to version 2"
            )
        locations: tuple[ReferenceAssetLocation, ...] = ()
    elif raw_locations is None and format_version == BRAND_MB_FORMAT_VERSION:
        locations = ()
    else:
        if not isinstance(raw_locations, list) or len(raw_locations) != len(reference_ids):
            raise ValueError(
                f"{path} reference_asset_locations must contain exactly one object per reference"
            )
        try:
            expected_location_keys = {"asset_id", "content_ref", "byte_identity"}
            for index, location in enumerate(raw_locations):
                if not isinstance(location, dict):
                    raise ValueError(f"entry {index} is not an object")
                if set(location) != expected_location_keys:
                    raise ValueError(
                        f"entry {index} must have exactly the keys "
                        "asset_id, content_ref, and byte_identity"
                    )
            locations = tuple(
                ReferenceAssetLocation(
                    asset_id=location["asset_id"],
                    content_ref=location["content_ref"],
                    byte_identity=location["byte_identity"],
                )
                for location in raw_locations
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{path} has invalid reference_asset_locations: {error}") from error

    if legacy:
        stored_location_digest: str | None = None
    elif locations:
        location_integrity = meta.get("reference_asset_location_digest")
        if not isinstance(location_integrity, dict) or set(location_integrity) != {
            "algorithm",
            "canonicalization",
            "value",
        }:
            raise ValueError(
                f"{path} reference_asset_location_digest must be a closed integrity object"
            )
        if location_integrity.get("algorithm") != "sha256":
            raise ValueError(f"{path} uses an unsupported location digest algorithm")
        if location_integrity.get("canonicalization") != _LOCATION_CANONICALIZATION:
            raise ValueError(f"{path} uses an unsupported location canonicalization")
        actual_location_digest = _reference_asset_location_digest(
            reference_content_hashes, locations
        )
        stored_location_digest = location_integrity.get("value")
        if stored_location_digest != actual_location_digest:
            raise ValueError(
                f"{path} reference asset location digest does not match its immutable "
                "source/content catalogue; the file is corrupt or was hand-edited"
            )
    else:
        stored_location_digest = None

    actual_embedding_digest = _reference_embedding_digest(
        reference_content_hashes, canonical_embeddings
    )
    if legacy:
        stored_embedding_digest: str | None = None
        recomputed_id = _legacy_board_hash(
            reference_content_hashes,
            model["repo"],
            model["revision"],
            fit["metric"],
            fit["k"],
            fit["cluster_cut"],
            fit["dup_cut"],
        )
    else:
        integrity = meta.get("reference_embedding_digest")
        expected_integrity_keys = {"algorithm", "canonicalization", "shape", "value"}
        if not isinstance(integrity, dict) or set(integrity) != expected_integrity_keys:
            raise ValueError(f"{path} reference_embedding_digest must be a closed integrity object")
        if integrity.get("algorithm") != "sha256":
            raise ValueError(f"{path} uses an unsupported reference embedding digest algorithm")
        if integrity.get("canonicalization") != _EMBEDDING_CANONICALIZATION:
            raise ValueError(f"{path} uses an unsupported reference embedding canonicalization")
        if integrity.get("shape") != list(canonical_embeddings.shape):
            raise ValueError(
                f"{path} reference embedding digest shape does not match embeddings.npy"
            )
        stored_embedding_digest = integrity.get("value")
        if stored_embedding_digest != actual_embedding_digest:
            raise ValueError(
                f"{path} reference embedding digest does not match embeddings.npy and its "
                "source-content mapping; the file is corrupt or was hand-edited"
            )
        recomputed_id = board_hash(
            reference_content_hashes,
            canonical_embeddings,
            model["repo"],
            model["revision"],
            fit["metric"],
            fit["k"],
            fit["cluster_cut"],
            fit["dup_cut"],
            k_cap=fit["k_cap"],
            min_category_size=fit["min_category_size"],
            interval_level=fit["interval_level"],
            far_outlier_iqr_multiplier=fit["far_outlier_iqr_multiplier"],
        )
    if recomputed_id != meta["board_id"]:
        raise ValueError(
            f"{path} claims board_id {meta['board_id']!r} but its stored fields hash to "
            f"{recomputed_id!r}; the file is corrupt or was hand-edited"
        )

    return BrandBoard(
        board_id=meta["board_id"],
        name=meta["name"],
        reference_ids=reference_ids,
        reference_content_hashes=reference_content_hashes,
        reference_embeddings=canonical_embeddings,
        model_repo=model["repo"],
        model_revision=model["revision"],
        model_dim=model["dim"],
        metric=fit["metric"],
        k=fit["k"],
        k_cap=(fit["k_cap"] if not legacy else DEFAULT_K_CAP),
        cluster_cut=fit["cluster_cut"],
        dup_cut=fit["dup_cut"],
        min_category_size=(fit["min_category_size"] if not legacy else DEFAULT_MIN_CATEGORY_SIZE),
        interval_level=(fit["interval_level"] if not legacy else DEFAULT_INTERVAL_LEVEL),
        far_outlier_iqr_multiplier=(
            fit["far_outlier_iqr_multiplier"] if not legacy else DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER
        ),
        far_outlier_iqr_multiplier_source=(
            fit["far_outlier_iqr_multiplier_source"]
            if not legacy
            else DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER_SOURCE
        ),
        n_eff=meta["n_eff"],
        built_at=meta["built_at"],
        reference_embedding_digest=stored_embedding_digest,
        reference_asset_location_digest=stored_location_digest,
        integrity_verified=not legacy,
        reference_asset_locations=locations,
    )

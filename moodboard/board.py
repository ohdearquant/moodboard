"""The `brand.mb` board artifact and the board hash (ADR-0005).

This is the one place `board_id`, the value ADR-0002's report calls `board.id`, is computed.
`board_hash` is a pure function of its arguments: it does no I/O, imports nothing from the
rest of this package, and is safe to call from `report.py`'s `Board` construction, from
`build_board` below, and from `cli.py`, all with the identical result, because it is the
identical call.

`build_board` and the `brand.mb` reader/writer below own the file format, which
`IMPLEMENTATION_CONTRACT.md` and `INTERFACES.md` both leave to this module's own decision.
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
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

BOARD_HASH_VERSION = 1
"""The literal "v" in the hashed payload. Bumped in the same change that adds a new fitting
parameter capable of moving a score, per ADR-0005."""

BRAND_MB_FORMAT = "moodboard-brand-mb"
BRAND_MB_FORMAT_VERSION = 1


def board_hash(
    reference_content_hashes: Sequence[str],
    model_repo: str,
    model_revision: str,
    metric: str,
    k: int,
    cluster_cut: float,
    dup_cut: float,
) -> str:
    """ADR-0005's board hash, computed in exactly one place.

    sha256 hex digest over the canonical JSON serialisation (sorted keys, no insignificant
    whitespace) of

        {"v": 1, "refs": sorted(reference_content_hashes),
         "model": {"repo": model_repo, "revision": model_revision},
         "fit": {"metric": metric, "k": k, "cluster_cut": cluster_cut, "dup_cut": dup_cut}}

    Stable under reordering the references, since `refs` is sorted before hashing.
    `report.py`'s `Board.id` and the `brand.mb` artifact's own id both call this function;
    neither recomputes it independently.
    """
    payload = {
        "v": BOARD_HASH_VERSION,
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
    cluster_cut: float
    dup_cut: float
    n_eff: float
    built_at: str  # RFC 3339


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
    if len(reference_content_hashes) != n:
        raise ValueError(
            f"reference_ids has {n} entries but reference_content_hashes has "
            f"{len(reference_content_hashes)}; they must describe the same references in the "
            "same order"
        )
    embeddings = np.asarray(reference_embeddings, dtype=np.float32)
    if embeddings.ndim != 2 or embeddings.shape[0] != n:
        raise ValueError(
            f"reference_embeddings must have shape ({n}, dim), one row per reference; got "
            f"{embeddings.shape}"
        )
    if n == 0:
        raise ValueError("a board needs at least one reference")

    board_id = board_hash(
        reference_content_hashes,
        model_repo,
        model_revision,
        metric,
        k,
        cluster_cut,
        dup_cut,
    )
    return BrandBoard(
        board_id=board_id,
        name=name,
        reference_ids=tuple(reference_ids),
        reference_content_hashes=tuple(reference_content_hashes),
        reference_embeddings=embeddings,
        model_repo=model_repo,
        model_revision=model_revision,
        model_dim=embeddings.shape[1],
        metric=metric,
        k=k,
        cluster_cut=cluster_cut,
        dup_cut=dup_cut,
        n_eff=n_eff,
        built_at=built_at,
    )


def write_board(board: BrandBoard, path: Path) -> None:
    """Write `board` as a `brand.mb` artifact at `path`.

    The container is a zip archive (so it is inspectable with any zip tool) holding a JSON
    metadata entry and the embedding matrix as a `.npy` entry. `allow_pickle=False` on both the
    write below and the read in `read_board`: a `brand.mb` is a distributable artifact and
    unpickling one from an untrusted source must never be able to run arbitrary code.
    """
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
            "metric": board.metric,
            "k": board.k,
            "cluster_cut": board.cluster_cut,
            "dup_cut": board.dup_cut,
        },
        "n_eff": board.n_eff,
        "built_at": board.built_at,
    }
    embeddings_buf = io.BytesIO()
    np.save(embeddings_buf, board.reference_embeddings, allow_pickle=False)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("meta.json", json.dumps(meta, sort_keys=True, indent=2))
        archive.writestr("embeddings.npy", embeddings_buf.getvalue())


def read_board(path: Path) -> BrandBoard:
    """Read a `brand.mb` artifact back into a `BrandBoard`.

    Re-derives `board_id` from the stored fields with `board_hash` and raises `ValueError` if
    it does not match what the file claims: a `brand.mb` whose id disagrees with its own
    content is corrupt or hand-edited, and using it silently would let two callers score
    against the same file believing it was two different boards, or the reverse.
    """
    path = Path(path)
    with zipfile.ZipFile(path, mode="r") as archive:
        meta = json.loads(archive.read("meta.json"))
        if meta.get("format") != BRAND_MB_FORMAT:
            raise ValueError(f"{path} is not a moodboard brand.mb artifact")
        if meta.get("format_version") != BRAND_MB_FORMAT_VERSION:
            raise ValueError(
                f"{path} was written by brand.mb format version {meta.get('format_version')!r}, "
                f"this reader supports version {BRAND_MB_FORMAT_VERSION}"
            )
        embeddings = np.load(io.BytesIO(archive.read("embeddings.npy")), allow_pickle=False)

    model = meta["model"]
    fit = meta["fit"]
    reference_content_hashes = tuple(meta["reference_content_hashes"])
    recomputed_id = board_hash(
        reference_content_hashes,
        model["repo"],
        model["revision"],
        fit["metric"],
        fit["k"],
        fit["cluster_cut"],
        fit["dup_cut"],
    )
    if recomputed_id != meta["board_id"]:
        raise ValueError(
            f"{path} claims board_id {meta['board_id']!r} but its stored fields hash to "
            f"{recomputed_id!r}; the file is corrupt or was hand-edited"
        )

    return BrandBoard(
        board_id=meta["board_id"],
        name=meta["name"],
        reference_ids=tuple(meta["reference_ids"]),
        reference_content_hashes=reference_content_hashes,
        reference_embeddings=embeddings,
        model_repo=model["repo"],
        model_revision=model["revision"],
        model_dim=model["dim"],
        metric=fit["metric"],
        k=fit["k"],
        cluster_cut=fit["cluster_cut"],
        dup_cut=fit["dup_cut"],
        n_eff=meta["n_eff"],
        built_at=meta["built_at"],
    )

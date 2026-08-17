"""Load the immutable board and retrieval authority for the real OpenRouter evaluation.

This module is deliberately offline.  It does not discover artifacts, fetch media, or repair a
stale contract.  Callers must provide one ``brand.mb`` and one Pixel-RAG JSON artifact explicitly.
Their exact bounded bytes are snapshotted, passed through the public Moodboard readers, and
reduced to the closed identities needed by an intent packet.  Repository-adjacent delivery paths
are intentionally not embedded in this public helper.

The retrieval identities below are content-derived, not labels:

* the eligible-corpus identity binds the source-manifest identity, the exact collection gate,
  and the complete filtered corpus as an order-independent set of asset/ContentRef pairs; and
* the route-policy identity binds that corpus identity, namespace, gate, no-fallback policy, and
  the artifact's registered structural-routing interpretation.

Both projections use RFC 8785 through :mod:`moodboard.contracts` and distinct domain tags.  The
ordered generation references remain the validated top-three ``ranked_evidence`` projection;
they are not confused with the complete eligible corpus.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Final, NoReturn

from moodboard.board import (
    board_fit_policy_id,
    board_representation_id,
    read_board,
)
from moodboard.contracts import compute_projection_identity
from moodboard.pixel_rag import read_pixel_rag_artifact, validate_pixel_rag_artifact

ELIGIBLE_CORPUS_IDENTITY_VERSION: Final = "moodboard.openrouter-real-e2e.eligible-corpus.v1"
ROUTE_POLICY_VERSION: Final = "moodboard.intent-route.collection-gate.v1"
ROUTE_POLICY_IDENTITY_VERSION: Final = "moodboard.openrouter-real-e2e.route-policy.v1"
EMPTY_RESULT_POLICY: Final = "no_ungated_fallback"
LOCAL_REPLACE_INTENT: Final = "local_replace"
LOCAL_REPLACE_COLLECTION: Final = "fruit-lemon"
ROUTING_INTERPRETATION: Final = "structural_routing_control_not_learned_retrieval_quality"

_MAX_BOARD_ARTIFACT_BYTES: Final = 64 * 1024 * 1024
_MAX_PIXEL_RAG_ARTIFACT_BYTES: Final = 16 * 1024 * 1024
_READ_CHUNK_BYTES: Final = 1024 * 1024

__all__ = [
    "ELIGIBLE_CORPUS_IDENTITY_VERSION",
    "EMPTY_RESULT_POLICY",
    "EligibleCorpusMember",
    "LOCAL_REPLACE_COLLECTION",
    "LOCAL_REPLACE_INTENT",
    "LocalReplaceReference",
    "OpenRouterRealE2EAuthority",
    "OpenRouterRealE2EAuthorityError",
    "ROUTE_POLICY_VERSION",
    "ROUTE_POLICY_IDENTITY_VERSION",
    "ROUTING_INTERPRETATION",
    "load_openrouter_real_e2e_authority",
]


class OpenRouterRealE2EAuthorityError(RuntimeError):
    """The offline authority load failed at one stable, path-free boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EligibleCorpusMember:
    """One member of the collection-gated corpus, in canonical identity order."""

    manifest_asset_id: str
    content_ref: str


@dataclass(frozen=True, slots=True)
class LocalReplaceReference:
    """One ordered, fully identified Pixel-RAG reference card."""

    manifest_asset_id: str
    khive_record_id: str
    content_ref: str
    content_sha256: str
    collection: str
    title: str
    routed_rank: int
    source_search_rank: int
    source_similarity: float


@dataclass(frozen=True, slots=True)
class OpenRouterRealE2EAuthority:
    """Frozen real-artifact authority suitable for a later intent-packet freeze.

    Artifact bytes are retained so the confirmation/dispatch layer can byte-compare its frozen
    authorities.  They are intentionally absent from ``repr`` and their public identities are
    available separately, so diagnostics never render a large or private payload by accident.
    """

    board_id: str
    representation_id: str
    fit_policy_id: str
    evidence_artifact_id: str
    eligible_corpus_sha256: str
    route_policy_id: str
    eligible_corpus: tuple[EligibleCorpusMember, ...]
    references: tuple[LocalReplaceReference, ...]
    board_artifact_sha256: str
    pixel_rag_artifact_sha256: str
    board_artifact_bytes: bytes = dataclass_field(repr=False, compare=False)
    pixel_rag_artifact_bytes: bytes = dataclass_field(repr=False, compare=False)


def _fail(code: str) -> NoReturn:
    raise OpenRouterRealE2EAuthorityError(code) from None


def _read_bounded_regular_file(path: Path, *, limit: int, code: str) -> bytes:
    """Read one exact regular file without following a final-component symlink."""

    descriptor: int | None = None
    try:
        source = Path(path)
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size < 1 or before.st_size > limit:
            _fail(code)
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        descriptor = os.open(source, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != before.st_dev
            or metadata.st_ino != before.st_ino
            or metadata.st_size != before.st_size
            or metadata.st_size < 1
            or metadata.st_size > limit
        ):
            _fail(code)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                _fail(code)
        if total != metadata.st_size:
            _fail(code)
        return b"".join(chunks)
    except OpenRouterRealE2EAuthorityError:
        raise
    except Exception:
        _fail(code)
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _write_snapshot(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                _fail("artifact_snapshot_failed")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        return path
    except OpenRouterRealE2EAuthorityError:
        raise
    except Exception:
        _fail("artifact_snapshot_failed")
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _load_board(snapshot: Path) -> tuple[str, str, str]:
    try:
        board = read_board(snapshot)
        if board.integrity_verified is not True:
            _fail("board_artifact_unverified")
        return (
            board.board_id,
            board_representation_id(board),
            board_fit_policy_id(board),
        )
    except OpenRouterRealE2EAuthorityError:
        raise
    except Exception:
        _fail("board_artifact_invalid")


def _load_pixel_rag(snapshot: Path) -> dict[str, Any]:
    try:
        artifact = read_pixel_rag_artifact(snapshot)
        # Keep this explicit even though the reader currently closes the artifact itself.  The
        # helper's contract is to use both public read and validate boundaries and remains sound
        # if the reader implementation is ever separated from semantic validation.
        validate_pixel_rag_artifact(artifact)
        return artifact
    except Exception:
        _fail("pixel_rag_artifact_invalid")


def _require_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("pixel_rag_projection_invalid")
    return value


def _require_sequence(value: object) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail("pixel_rag_projection_invalid")
    return value


def _eligible_corpus_projection(
    artifact: Mapping[str, Any], local: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[EligibleCorpusMember, ...]]:
    try:
        route = _require_mapping(local["route"])
        hard_filter = _require_mapping(route["hard_filter"])
        retrieval = _require_mapping(local["retrieval"])
        exact_rows = _require_sequence(retrieval["exact_score_order"])
        source_manifest = _require_mapping(artifact["source_manifest"])
        members = tuple(
            sorted(
                (
                    EligibleCorpusMember(
                        manifest_asset_id=str(_require_mapping(row)["asset_id"]),
                        content_ref=str(_require_mapping(row)["content_ref"]),
                    )
                    for row in exact_rows
                ),
                key=lambda member: (member.manifest_asset_id, member.content_ref),
            )
        )
        if not members or len({member.manifest_asset_id for member in members}) != len(members):
            _fail("eligible_corpus_invalid")
        if len({member.content_ref for member in members}) != len(members):
            _fail("eligible_corpus_invalid")
        projection = {
            "schema_version": ELIGIBLE_CORPUS_IDENTITY_VERSION,
            "source_manifest": {
                "catalog_sha256": source_manifest["catalog_sha256"],
                "dataset_id": source_manifest["dataset_id"],
                "manifest_sha256": source_manifest["manifest_sha256"],
            },
            "field": hard_filter["field"],
            "operator": hard_filter["operator"],
            "value": hard_filter["value"],
            "assets": [
                {
                    "asset_id": member.manifest_asset_id,
                    "content_ref": member.content_ref,
                }
                for member in members
            ],
        }
        return projection, members
    except OpenRouterRealE2EAuthorityError:
        raise
    except Exception:
        _fail("pixel_rag_projection_invalid")


def _ordered_references(local: Mapping[str, Any]) -> tuple[LocalReplaceReference, ...]:
    try:
        retrieval = _require_mapping(local["retrieval"])
        cards = _require_sequence(retrieval["ranked_evidence"])
        references = tuple(
            LocalReplaceReference(
                manifest_asset_id=str(card["asset_id"]),
                khive_record_id=str(_require_mapping(card["khive"])["record_id"]),
                content_ref=str(_require_mapping(card["khive"])["content_ref"]),
                content_sha256=str(card["sha256"]),
                collection=str(card["collection"]),
                title=str(card["title"]),
                routed_rank=int(card["rank"]),
                source_search_rank=int(card["source_search_rank"]),
                source_similarity=float(_require_mapping(card["score"])["value"]),
            )
            for raw_card in cards
            for card in (_require_mapping(raw_card),)
        )
        if not references:
            _fail("local_replace_references_invalid")
        if [reference.routed_rank for reference in references] != list(
            range(1, len(references) + 1)
        ):
            _fail("local_replace_references_invalid")
        if any(reference.collection != LOCAL_REPLACE_COLLECTION for reference in references):
            _fail("local_replace_references_invalid")
        if len({reference.khive_record_id for reference in references}) != len(references):
            _fail("local_replace_references_invalid")
        if len({reference.content_ref for reference in references}) != len(references):
            _fail("local_replace_references_invalid")
        return references
    except OpenRouterRealE2EAuthorityError:
        raise
    except Exception:
        _fail("pixel_rag_projection_invalid")


def _project_pixel_rag(
    artifact: Mapping[str, Any],
) -> tuple[
    str,
    str,
    str,
    tuple[EligibleCorpusMember, ...],
    tuple[LocalReplaceReference, ...],
]:
    try:
        if artifact["evidence_status"] != "measured_run":
            _fail("pixel_rag_evidence_not_measured")
        intents = _require_sequence(artifact["intents"])
        local_matches = [
            _require_mapping(intent)
            for intent in intents
            if _require_mapping(intent).get("id") == LOCAL_REPLACE_INTENT
        ]
        if len(local_matches) != 1:
            _fail("local_replace_route_invalid")
        local = local_matches[0]
        route = _require_mapping(local["route"])
        hard_filter = _require_mapping(route["hard_filter"])
        retrieval = _require_mapping(local["retrieval"])
        if dict(hard_filter) != {
            "field": "collection",
            "operator": "equals",
            "value": LOCAL_REPLACE_COLLECTION,
        }:
            _fail("local_replace_route_invalid")
        if retrieval["hard_filter_applied_before_rank_projection"] is not True:
            _fail("local_replace_route_invalid")
        if retrieval["metrics_interpretation"] != ROUTING_INTERPRETATION:
            _fail("local_replace_route_invalid")

        eligible_projection, eligible_corpus = _eligible_corpus_projection(artifact, local)
        eligible_digest = compute_projection_identity(
            eligible_projection,
            domain_tag=ELIGIBLE_CORPUS_IDENTITY_VERSION,
        )
        route_projection = {
            "schema_version": ROUTE_POLICY_VERSION,
            "eligible_corpus_sha256": eligible_digest,
            "namespace": route["namespace"],
            "field": hard_filter["field"],
            "operator": hard_filter["operator"],
            "value": hard_filter["value"],
            "empty_result_policy": EMPTY_RESULT_POLICY,
            "interpretation": retrieval["metrics_interpretation"],
        }
        route_policy_id = compute_projection_identity(
            route_projection,
            domain_tag=ROUTE_POLICY_IDENTITY_VERSION,
        )
        references = _ordered_references(local)
        evidence_artifact_id = str(artifact["artifact_id"])
        return (
            evidence_artifact_id,
            eligible_digest,
            route_policy_id,
            eligible_corpus,
            references,
        )
    except OpenRouterRealE2EAuthorityError:
        raise
    except Exception:
        _fail("pixel_rag_projection_invalid")


def load_openrouter_real_e2e_authority(
    *,
    board_path: Path,
    pixel_rag_path: Path,
) -> OpenRouterRealE2EAuthority:
    """Load one exact offline authority or fail with a stable, detail-free code.

    The source paths are read-only.  Private owner-only temporary files ensure the public readers
    validate the exact bytes returned in the result rather than a later revision of either path.
    No network or credential boundary is reachable from this function.
    """

    board_bytes = _read_bounded_regular_file(
        board_path,
        limit=_MAX_BOARD_ARTIFACT_BYTES,
        code="board_artifact_unavailable",
    )
    pixel_bytes = _read_bounded_regular_file(
        pixel_rag_path,
        limit=_MAX_PIXEL_RAG_ARTIFACT_BYTES,
        code="pixel_rag_artifact_unavailable",
    )
    try:
        with tempfile.TemporaryDirectory(prefix="moodboard-real-e2e-authority-") as name:
            snapshot_root = Path(name)
            os.chmod(snapshot_root, 0o700)
            board_snapshot = _write_snapshot(snapshot_root, "authority.brand.mb", board_bytes)
            pixel_snapshot = _write_snapshot(snapshot_root, "pixel-rag-artifact.json", pixel_bytes)
            board_id, representation_id, fit_policy_id = _load_board(board_snapshot)
            artifact = _load_pixel_rag(pixel_snapshot)
    except OpenRouterRealE2EAuthorityError:
        raise
    except Exception:
        _fail("artifact_snapshot_failed")

    (
        evidence_artifact_id,
        eligible_corpus_sha256,
        route_policy_id,
        eligible_corpus,
        references,
    ) = _project_pixel_rag(artifact)
    return OpenRouterRealE2EAuthority(
        board_id=board_id,
        representation_id=representation_id,
        fit_policy_id=fit_policy_id,
        evidence_artifact_id=evidence_artifact_id,
        eligible_corpus_sha256=eligible_corpus_sha256,
        route_policy_id=route_policy_id,
        eligible_corpus=eligible_corpus,
        references=references,
        board_artifact_sha256=hashlib.sha256(board_bytes).hexdigest(),
        pixel_rag_artifact_sha256=hashlib.sha256(pixel_bytes).hexdigest(),
        board_artifact_bytes=board_bytes,
        pixel_rag_artifact_bytes=pixel_bytes,
    )

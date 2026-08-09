"""The report schema, the scored/abstained discriminated union, and the self-validator.

The types here are plain frozen, slotted dataclasses rather than pydantic models, because the
schema is fixed and fully typed and its one cross-field rule compares an asset's axis keys
against a sibling object's field, so a hand-written validator function is needed whichever
library builds the types and the smaller dependency therefore wins; the full reasoning is in
``INTERFACES.md``. The one validation dependency this module does take is ``jsonschema``, which
checks the emitted document against the committed JSON Schema file, a different job from typing
the in-memory objects.

This module defines the shapes every other module fills in. It imports nothing from the rest of
the package, so the dependency arrow runs from the computing modules to the schema and never
back.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import operator
import shlex
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from numbers import Integral, Real
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import jsonschema
from PIL import Image, UnidentifiedImageError

__all__ = [
    "AXES",
    "AXIS_ORDER",
    "INTERVAL_METHOD",
    "REPORT_MAX_BYTES",
    "SCHEMA_PATH",
    "SCHEMA_PATH_V1_0",
    "SCHEMA_PATH_V1_1",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_V1_0",
    "SCHEMA_VERSION_V1_1",
    "THUMBNAIL_MAX_COMPRESSED_BYTES",
    "THUMBNAIL_MAX_COUNT",
    "THUMBNAIL_MAX_DECODED_BYTES",
    "THUMBNAIL_MAX_PIXELS",
    "THUMBNAIL_MAX_SIDE",
    "THUMBNAIL_TOTAL_DECODED_BYTES",
    "AbstainedAsset",
    "AbstainedAssetV1_1",
    "Asset",
    "AssetV1_1",
    "AxisDefinition",
    "AxisMethod",
    "Board",
    "BoardFit",
    "BoardFitV1_1",
    "BoardStats",
    "BoardV1_1",
    "CandidateImage",
    "CandidateImageInput",
    "Category",
    "Comparisons",
    "EngineProvenance",
    "EngineProvenanceV1_1",
    "EngineSourceProvenance",
    "Exemplar",
    "Interval",
    "IntervalMethod",
    "Leverage",
    "ModelProvenance",
    "Provenance",
    "ProvenanceV1_1",
    "ReferenceEntry",
    "Report",
    "ReportV1_1",
    "Representation",
    "RepresentationV1_1",
    "SchemaProvenance",
    "ScoredAsset",
    "ScoredAssetV1_1",
    "StyleModelInfo",
    "Thumbnail",
    "Tightness",
    "UnsupportedSchemaVersionError",
    "axis_definitions_for",
    "from_json_dict",
    "report_schema_sha256",
    "read_report_bytes",
    "to_json_dict",
    "validate_axis_vocabulary",
    "validate_report",
    "write_report",
]

SCHEMA_VERSION_V1_0: Literal["1.0"] = "1.0"
SCHEMA_VERSION_V1_1: Literal["1.1"] = "1.1"

# Backwards-compatible names for the frozen v1.0 writer surface. New engine output names the
# v1.1 constant explicitly instead of silently changing what an import of SCHEMA_VERSION means.
SCHEMA_VERSION: Literal["1.0"] = SCHEMA_VERSION_V1_0

INTERVAL_METHOD: Literal["loo-jackknife-plus"] = "loo-jackknife-plus"

# The classical axis vocabulary, in the order a report lists it. ADR-0003 states that an axis
# failing its intervention test "loses its name in the report and appears as an unlabelled
# component, or comes out", so the vocabulary is data that can shrink at runtime rather than a
# fixed set of struct fields. AXIS_ORDER exists because INTERFACES.md pins the constant as a
# frozenset and also says a board carries the three names "in that order"; a frozenset cannot
# carry an order, so the ordered tuple is what a caller passes and AXES is derived from it.
AXIS_ORDER: tuple[str, ...] = ("palette", "tone", "composition")
AXES: frozenset[str] = frozenset(AXIS_ORDER)

SCHEMA_PATH_V1_0: Path = Path(__file__).parent / "schema" / "report_v1_0.schema.json"
SCHEMA_PATH_V1_1: Path = Path(__file__).parent / "schema" / "report_v1_1.schema.json"
SCHEMA_PATH: Path = SCHEMA_PATH_V1_0

# A report is a self-contained transport containing original metadata and bounded preview images,
# not an arbitrary media archive. This cap is deliberately far above the governed 50-reference,
# 250-candidate/thumbnail operating envelope while preventing untrusted JSON/base64 input from
# consuming unbounded memory before schema validation.
REPORT_MAX_BYTES = 128 * 1024 * 1024

# Shared Python consumer limits. The browser decoder independently pins the same values in its
# versioned consumer contract, while both the CLI validator and the standalone inliner flow
# through these constants. These are transport/decode safety ceilings, never aesthetic policy.
THUMBNAIL_MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
THUMBNAIL_MAX_COUNT = 512
THUMBNAIL_MAX_SIDE = 8_192
THUMBNAIL_MAX_PIXELS = 4_096 * 4_096
THUMBNAIL_MAX_DECODED_BYTES = 64 * 1024 * 1024
THUMBNAIL_TOTAL_DECODED_BYTES = 256 * 1024 * 1024

_SCHEMA_ID_V1_1 = "https://github.com/ohdearquant/moodboard/schema/report_v1_1.schema.json"
_THUMBNAIL_MIME_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def read_report_bytes(path: Path) -> bytes:
    """Read one report under the shared resource ceiling, including a grow-after-stat guard."""
    report_path = Path(path)
    measured_size = report_path.stat().st_size
    if measured_size > REPORT_MAX_BYTES:
        raise ValueError(
            f"{report_path} is {measured_size} bytes and exceeds the "
            f"{REPORT_MAX_BYTES}-byte report limit"
        )
    with report_path.open("rb") as stream:
        payload = stream.read(REPORT_MAX_BYTES + 1)
    if len(payload) > REPORT_MAX_BYTES:
        raise ValueError(
            f"{report_path} grew while being read and exceeds the "
            f"{REPORT_MAX_BYTES}-byte report limit"
        )
    return payload


class UnsupportedSchemaVersionError(ValueError):
    """A report version was not one of the complete versions this consumer names."""

    code = "unsupported_schema_version"


# ---------------------------------------------------------------------------
# Shared primitive types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Interval:
    """A calibrated interval around a score, or around a difference of two scores."""

    low: float
    high: float
    level: float
    method: Literal["loo-jackknife-plus"]


@dataclass(frozen=True, slots=True)
class Exemplar:
    """One reference an asset is close to, resolvable into the report's reference catalogue."""

    reference_id: str
    similarity: float


# ---------------------------------------------------------------------------
# Reference catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Thumbnail:
    """An inline image, so the viewer needs no network access at view time."""

    mime: str
    width: int
    height: int
    data_base64: str


@dataclass(frozen=True, slots=True)
class CandidateImage:
    """Original candidate identity plus a separately encoded inert display rendition."""

    content_sha256: str
    mime: str
    width: int
    height: int
    thumbnail: Thumbnail


@dataclass(frozen=True, slots=True)
class CandidateImageInput:
    """Original-image facts known by the writer before it serializes report v1.1.

    These values are deliberately separate from :class:`CandidateImage`: comparing the two is
    what prevents a report from substituting its preview digest or dimensions for the original
    candidate bytes consumed by the engine.
    """

    asset_id: str
    content_sha256: str
    mime: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ReferenceEntry:
    """A catalogue entry an asset's exemplars resolve into by ``reference_id``."""

    reference_id: str
    content_sha256: str
    mime: str
    width: int
    height: int
    thumbnail: Thumbnail


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StyleModelInfo:
    model: str
    revision: str
    dim: int


@dataclass(frozen=True, slots=True)
class AxisMethod:
    name: str
    revision: int


@dataclass(frozen=True, slots=True)
class AxisDefinition:
    axis_id: str
    label: str
    value_kind: Literal["conformal_p_value", "normalized_distance"]
    direction: Literal["higher_is_better_fit", "lower_is_closer"]
    aggregation: Literal["full_conformal_category", "mean_over_exemplars"]
    availability: Literal["scored_only", "all_assets"]
    uncertainty: Literal["asset_interval", "none"]
    method: AxisMethod


_AXIS_DEFINITIONS: Mapping[str, AxisDefinition] = {
    "style": AxisDefinition(
        axis_id="style",
        label="Style fit",
        value_kind="conformal_p_value",
        direction="higher_is_better_fit",
        aggregation="full_conformal_category",
        availability="scored_only",
        uncertainty="asset_interval",
        method=AxisMethod(name="full-conformal-p-value", revision=1),
    ),
    "palette": AxisDefinition(
        axis_id="palette",
        label="Palette distance",
        value_kind="normalized_distance",
        direction="lower_is_closer",
        aggregation="mean_over_exemplars",
        availability="all_assets",
        uncertainty="none",
        method=AxisMethod(name="palette-distance", revision=1),
    ),
    "tone": AxisDefinition(
        axis_id="tone",
        label="Tone distance",
        value_kind="normalized_distance",
        direction="lower_is_closer",
        aggregation="mean_over_exemplars",
        availability="all_assets",
        uncertainty="none",
        method=AxisMethod(name="tone-distance", revision=1),
    ),
    "composition": AxisDefinition(
        axis_id="composition",
        label="Composition distance",
        value_kind="normalized_distance",
        direction="lower_is_closer",
        aggregation="mean_over_exemplars",
        availability="all_assets",
        uncertainty="none",
        method=AxisMethod(name="composition-distance", revision=1),
    ),
}


def axis_definitions_for(axes: Sequence[str]) -> tuple[AxisDefinition, ...]:
    """Return ADR-0008's exact style-first metadata for the declared classical axes."""

    declared = tuple(axes)
    if len(set(declared)) != len(declared):
        raise ValueError("axis identifiers must be unique")
    if tuple(axis for axis in AXIS_ORDER if axis in declared) != declared:
        raise ValueError(
            f"report v1.1 axes must preserve the registered order {AXIS_ORDER!r}; got {declared!r}"
        )
    unknown = [axis for axis in declared if axis not in _AXIS_DEFINITIONS]
    if unknown:
        raise ValueError(f"report v1.1 has no governed definition for axes {unknown!r}")
    return (_AXIS_DEFINITIONS["style"], *(_AXIS_DEFINITIONS[axis] for axis in declared))


@dataclass(frozen=True, slots=True)
class Representation:
    style: StyleModelInfo
    axes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepresentationV1_1:
    style: StyleModelInfo
    axes: tuple[str, ...]
    axis_definitions: tuple[AxisDefinition, ...]


@dataclass(frozen=True, slots=True)
class IntervalMethod:
    """``replicates`` is always None: loo-jackknife-plus has no inner replicates."""

    method: Literal["loo-jackknife-plus"]
    replicates: None
    seed: int


@dataclass(frozen=True, slots=True)
class BoardFit:
    """The report-v1.0 projection of fit identity.

    The verified ``brand.mb`` policy also binds k_cap, min_category_size, interval_level and
    the far-outlier multiplier. Report v1.0 is closed and cannot add those fields in place;
    ADR-0008's v1.1 schema is the release blocker that makes the report self-contained.
    """

    metric: Literal["cosine"]
    k: int
    cluster_cut: float
    dup_cut: float
    interval: IntervalMethod


@dataclass(frozen=True, slots=True)
class BoardFitV1_1:
    """Every score-moving policy value persisted by verified ``brand.mb`` format 3."""

    metric: Literal["cosine"]
    k: int
    k_cap: int
    cluster_cut: float
    dup_cut: float
    min_category_size: int
    interval_level: float
    far_outlier_iqr_multiplier: float
    far_outlier_iqr_multiplier_source: str
    interval: IntervalMethod


@dataclass(frozen=True, slots=True)
class Category:
    category_id: str
    n_local: int
    member_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Board:
    """``id`` is not computed here. It is ``board.board_hash(...)``, computed once and echoed."""

    id: str
    name: str
    n_references: int
    n_eff: float
    requested_alpha: float
    supported_alpha: float
    built_at: str
    representation: Representation
    fit: BoardFit
    categories: tuple[Category, ...]


@dataclass(frozen=True, slots=True)
class BoardV1_1:
    id: str
    name: str
    n_references: int
    n_eff: float
    requested_alpha: float
    supported_alpha: float
    built_at: str
    representation: RepresentationV1_1
    fit: BoardFitV1_1
    categories: tuple[Category, ...]


# ---------------------------------------------------------------------------
# Board statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tightness:
    loo_mean: float
    loo_sd: float
    loo_quantiles: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class Leverage:
    reference_id: str
    delta_tightness: float
    rank: int


@dataclass(frozen=True, slots=True)
class BoardStats:
    tightness: Tightness
    leverage: tuple[Leverage, ...]
    flags: tuple[str, ...]


# ---------------------------------------------------------------------------
# Assets: the discriminated union
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoredAsset:
    """An asset the engine was willing to score.

    There is no ``reason`` field to leave None. ``score`` and ``interval`` are both required,
    which is what makes it impossible to represent a score that is present and unusable.
    """

    state: Literal["scored"]
    asset_id: str
    source: str
    category_id: str
    n_local: int
    score: float
    interval: Interval
    rank: int
    axes: Mapping[str, float]
    exemplars: tuple[Exemplar, ...]
    flags: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state != "scored":
            raise ValueError(
                f"ScoredAsset carries state {self.state!r}; the discriminator must be 'scored'"
            )


@dataclass(frozen=True, slots=True)
class AbstainedAsset:
    """An asset the engine refused to score.

    There is no ``score`` field to leave None, which is the point: the key is absent from the
    serialised object because the attribute does not exist, not because a serialiser was told to
    omit a null. ``axes["style"]`` is None while the classical axes are still computed, since a
    designer told an asset is nothing like the references is helped by seeing its palette matched.
    """

    state: Literal["abstained"]
    asset_id: str
    source: str
    reason: Literal["resolution", "multi_modality", "far_outlier"]
    explanation: str
    measurement: Mapping[str, Any]
    category_id: str
    axes: Mapping[str, float | None]
    exemplars: tuple[Exemplar, ...]
    flags: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state != "abstained":
            raise ValueError(
                f"AbstainedAsset carries state {self.state!r}; "
                "the discriminator must be 'abstained'"
            )


Asset = ScoredAsset | AbstainedAsset


@dataclass(frozen=True, slots=True)
class ScoredAssetV1_1:
    """A scored v1.1 asset with original identity and an inline candidate preview."""

    state: Literal["scored"]
    asset_id: str
    source: str
    image: CandidateImage
    category_id: str
    n_local: int
    score: float
    interval: Interval
    rank: int
    axes: Mapping[str, float]
    exemplars: tuple[Exemplar, ...]
    flags: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state != "scored":
            raise ValueError(
                f"ScoredAssetV1_1 carries state {self.state!r}; the discriminator must be 'scored'"
            )


@dataclass(frozen=True, slots=True)
class AbstainedAssetV1_1:
    """An abstained v1.1 asset; no score-bearing attribute exists on this branch."""

    state: Literal["abstained"]
    asset_id: str
    source: str
    image: CandidateImage
    reason: Literal["resolution", "multi_modality", "far_outlier"]
    explanation: str
    measurement: Mapping[str, Any]
    category_id: str
    axes: Mapping[str, float | None]
    exemplars: tuple[Exemplar, ...]
    flags: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state != "abstained":
            raise ValueError(
                f"AbstainedAssetV1_1 carries state {self.state!r}; "
                "the discriminator must be 'abstained'"
            )


AssetV1_1 = ScoredAssetV1_1 | AbstainedAssetV1_1


# ---------------------------------------------------------------------------
# Comparisons and provenance
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Comparisons:
    """``ties`` is a list of pairs and never a partition, because the tie test is not transitive."""

    ties: tuple[tuple[str, str], ...]
    note: str


@dataclass(frozen=True, slots=True)
class ModelProvenance:
    repo: str
    revision: str
    sha256: str


@dataclass(frozen=True, slots=True)
class EngineProvenance:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class EngineSourceProvenance:
    source_repository: str
    source_revision: str
    source_dirty: bool


@dataclass(frozen=True, slots=True)
class EngineProvenanceV1_1:
    name: str
    version: str
    source: EngineSourceProvenance | None = None


@dataclass(frozen=True, slots=True)
class SchemaProvenance:
    id: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Provenance:
    engine: EngineProvenance
    model: ModelProvenance
    command: str
    seed: int
    created_at: str


@dataclass(frozen=True, slots=True)
class ProvenanceV1_1:
    engine: EngineProvenanceV1_1
    model: ModelProvenance
    command: str
    argv: tuple[str, ...]
    seed: int
    created_at: str
    schema: SchemaProvenance


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Report:
    schema_version: Literal["1.0"]
    board: Board
    board_stats: BoardStats
    references: tuple[ReferenceEntry, ...]
    assets: tuple[Asset, ...]
    comparisons: Comparisons
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class ReportV1_1:
    schema_version: Literal["1.1"]
    board: BoardV1_1
    board_stats: BoardStats
    references: tuple[ReferenceEntry, ...]
    assets: tuple[AssetV1_1, ...]
    comparisons: Comparisons
    provenance: ProvenanceV1_1


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
#
# The serialiser is written by hand rather than with dataclasses.asdict for two reasons. It has
# to control key presence per union branch exactly, and asdict preserves tuples, which
# jsonschema rejects for "type": "array" because a tuple is not a list. Numeric coercion happens
# here as well: the computing modules produce numpy scalars, json.dump cannot serialise a
# numpy float32 and jsonschema does not recognise it as a number, so the boundary that turns
# objects into a JSON document is the right place to make them primitives.


def _as_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field} must be a real number, got {type(value).__name__}")
    return float(value)


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be an integer, got bool")
    try:
        return operator.index(value)
    except TypeError:
        raise TypeError(f"{field} must be an integer, got {type(value).__name__}") from None


def _as_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string, got {type(value).__name__}")
    return value


def _jsonable(value: Any, field: str) -> Any:
    """Coerce a free-shaped value (an abstention ``measurement``) to JSON primitives.

    Fails on anything it does not recognise rather than letting an unserialisable object reach
    json.dump, where the failure would surface far from its cause.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value)
    if isinstance(value, Mapping):
        return {
            _as_str(k, f"{field} key"): _jsonable(v, f"{field}[{k!r}]") for k, v in value.items()
        }
    if isinstance(value, (bytes, bytearray)):
        # Rejected rather than treated as a Sequence, which would turn it into a list of ints.
        raise TypeError(f"{field} holds raw bytes; encode it before it reaches the report")
    if isinstance(value, Sequence):
        return [_jsonable(v, f"{field}[{i}]") for i, v in enumerate(value)]
    raise TypeError(f"{field} holds a value of type {type(value).__name__}, which is not JSON data")


def _interval_to_json(interval: Interval) -> dict[str, Any]:
    return {
        "low": _as_float(interval.low, "interval.low"),
        "high": _as_float(interval.high, "interval.high"),
        "level": _as_float(interval.level, "interval.level"),
        "method": _as_str(interval.method, "interval.method"),
    }


def _exemplars_to_json(exemplars: Sequence[Exemplar]) -> list[dict[str, Any]]:
    return [
        {
            "reference_id": _as_str(e.reference_id, "exemplar.reference_id"),
            "similarity": _as_float(e.similarity, "exemplar.similarity"),
        }
        for e in exemplars
    ]


def _thumbnail_to_json(thumbnail: Thumbnail) -> dict[str, Any]:
    return {
        "mime": _as_str(thumbnail.mime, "thumbnail.mime"),
        "width": _as_int(thumbnail.width, "thumbnail.width"),
        "height": _as_int(thumbnail.height, "thumbnail.height"),
        "data_base64": _as_str(thumbnail.data_base64, "thumbnail.data_base64"),
    }


def _candidate_image_to_json(image: CandidateImage) -> dict[str, Any]:
    return {
        "content_sha256": _as_str(image.content_sha256, "asset.image.content_sha256"),
        "mime": _as_str(image.mime, "asset.image.mime"),
        "width": _as_int(image.width, "asset.image.width"),
        "height": _as_int(image.height, "asset.image.height"),
        "thumbnail": _thumbnail_to_json(image.thumbnail),
    }


def _axis_definition_to_json(definition: AxisDefinition) -> dict[str, Any]:
    return {
        "axis_id": _as_str(definition.axis_id, "axis_definition.axis_id"),
        "label": _as_str(definition.label, "axis_definition.label"),
        "value_kind": _as_str(definition.value_kind, "axis_definition.value_kind"),
        "direction": _as_str(definition.direction, "axis_definition.direction"),
        "aggregation": _as_str(definition.aggregation, "axis_definition.aggregation"),
        "availability": _as_str(definition.availability, "axis_definition.availability"),
        "uncertainty": _as_str(definition.uncertainty, "axis_definition.uncertainty"),
        "method": {
            "name": _as_str(definition.method.name, "axis_definition.method.name"),
            "revision": _as_int(definition.method.revision, "axis_definition.method.revision"),
        },
    }


def _board_to_json(board: Board) -> dict[str, Any]:
    return {
        "id": _as_str(board.id, "board.id"),
        "name": _as_str(board.name, "board.name"),
        "n_references": _as_int(board.n_references, "board.n_references"),
        "n_eff": _as_float(board.n_eff, "board.n_eff"),
        "requested_alpha": _as_float(board.requested_alpha, "board.requested_alpha"),
        "supported_alpha": _as_float(board.supported_alpha, "board.supported_alpha"),
        "built_at": _as_str(board.built_at, "board.built_at"),
        "representation": {
            "style": {
                "model": _as_str(
                    board.representation.style.model, "board.representation.style.model"
                ),
                "revision": _as_str(
                    board.representation.style.revision, "board.representation.style.revision"
                ),
                "dim": _as_int(board.representation.style.dim, "board.representation.style.dim"),
            },
            "axes": [_as_str(a, "board.representation.axes") for a in board.representation.axes],
        },
        "fit": {
            "metric": _as_str(board.fit.metric, "board.fit.metric"),
            "k": _as_int(board.fit.k, "board.fit.k"),
            "cluster_cut": _as_float(board.fit.cluster_cut, "board.fit.cluster_cut"),
            "dup_cut": _as_float(board.fit.dup_cut, "board.fit.dup_cut"),
            "interval": {
                "method": _as_str(board.fit.interval.method, "board.fit.interval.method"),
                "replicates": None,
                "seed": _as_int(board.fit.interval.seed, "board.fit.interval.seed"),
            },
        },
        "categories": [
            {
                "category_id": _as_str(c.category_id, "category.category_id"),
                "n_local": _as_int(c.n_local, "category.n_local"),
                "member_ids": [_as_str(m, "category.member_ids") for m in c.member_ids],
            }
            for c in board.categories
        ],
    }


def _board_v1_1_to_json(board: BoardV1_1) -> dict[str, Any]:
    return {
        "id": _as_str(board.id, "board.id"),
        "name": _as_str(board.name, "board.name"),
        "n_references": _as_int(board.n_references, "board.n_references"),
        "n_eff": _as_float(board.n_eff, "board.n_eff"),
        "requested_alpha": _as_float(board.requested_alpha, "board.requested_alpha"),
        "supported_alpha": _as_float(board.supported_alpha, "board.supported_alpha"),
        "built_at": _as_str(board.built_at, "board.built_at"),
        "representation": {
            "style": {
                "model": _as_str(
                    board.representation.style.model, "board.representation.style.model"
                ),
                "revision": _as_str(
                    board.representation.style.revision, "board.representation.style.revision"
                ),
                "dim": _as_int(board.representation.style.dim, "board.representation.style.dim"),
            },
            "axes": [
                _as_str(axis, "board.representation.axes") for axis in board.representation.axes
            ],
            "axis_definitions": [
                _axis_definition_to_json(definition)
                for definition in board.representation.axis_definitions
            ],
        },
        "fit": {
            "metric": _as_str(board.fit.metric, "board.fit.metric"),
            "k": _as_int(board.fit.k, "board.fit.k"),
            "k_cap": _as_int(board.fit.k_cap, "board.fit.k_cap"),
            "cluster_cut": _as_float(board.fit.cluster_cut, "board.fit.cluster_cut"),
            "dup_cut": _as_float(board.fit.dup_cut, "board.fit.dup_cut"),
            "min_category_size": _as_int(
                board.fit.min_category_size, "board.fit.min_category_size"
            ),
            "interval_level": _as_float(board.fit.interval_level, "board.fit.interval_level"),
            "far_outlier_iqr_multiplier": _as_float(
                board.fit.far_outlier_iqr_multiplier,
                "board.fit.far_outlier_iqr_multiplier",
            ),
            "far_outlier_iqr_multiplier_source": _as_str(
                board.fit.far_outlier_iqr_multiplier_source,
                "board.fit.far_outlier_iqr_multiplier_source",
            ),
            "interval": {
                "method": _as_str(board.fit.interval.method, "board.fit.interval.method"),
                "replicates": None,
                "seed": _as_int(board.fit.interval.seed, "board.fit.interval.seed"),
            },
        },
        "categories": [
            {
                "category_id": _as_str(category.category_id, "category.category_id"),
                "n_local": _as_int(category.n_local, "category.n_local"),
                "member_ids": [
                    _as_str(member, "category.member_ids") for member in category.member_ids
                ],
            }
            for category in board.categories
        ],
    }


def _board_stats_to_json(stats: BoardStats) -> dict[str, Any]:
    return {
        "tightness": {
            "loo_mean": _as_float(stats.tightness.loo_mean, "tightness.loo_mean"),
            "loo_sd": _as_float(stats.tightness.loo_sd, "tightness.loo_sd"),
            "loo_quantiles": {
                _as_str(k, "tightness.loo_quantiles key"): _as_float(
                    v, f"tightness.loo_quantiles[{k!r}]"
                )
                for k, v in stats.tightness.loo_quantiles.items()
            },
        },
        "leverage": [
            {
                "reference_id": _as_str(lev.reference_id, "leverage.reference_id"),
                "delta_tightness": _as_float(lev.delta_tightness, "leverage.delta_tightness"),
                "rank": _as_int(lev.rank, "leverage.rank"),
            }
            for lev in stats.leverage
        ],
        "flags": [_as_str(f, "board_stats.flags") for f in stats.flags],
    }


def _reference_to_json(reference: ReferenceEntry) -> dict[str, Any]:
    return {
        "reference_id": _as_str(reference.reference_id, "reference.reference_id"),
        "content_sha256": _as_str(reference.content_sha256, "reference.content_sha256"),
        "mime": _as_str(reference.mime, "reference.mime"),
        "width": _as_int(reference.width, "reference.width"),
        "height": _as_int(reference.height, "reference.height"),
        "thumbnail": _thumbnail_to_json(reference.thumbnail),
    }


def _asset_to_json(asset: Asset) -> dict[str, Any]:
    if isinstance(asset, ScoredAsset):
        return {
            "state": "scored",
            "asset_id": _as_str(asset.asset_id, "asset.asset_id"),
            "source": _as_str(asset.source, "asset.source"),
            "category_id": _as_str(asset.category_id, "asset.category_id"),
            "n_local": _as_int(asset.n_local, "asset.n_local"),
            "score": _as_float(asset.score, "asset.score"),
            "interval": _interval_to_json(asset.interval),
            "rank": _as_int(asset.rank, "asset.rank"),
            "axes": {
                _as_str(k, "asset.axes key"): _as_float(v, f"asset.axes[{k!r}]")
                for k, v in asset.axes.items()
            },
            "exemplars": _exemplars_to_json(asset.exemplars),
            "flags": [_as_str(f, "asset.flags") for f in asset.flags],
        }
    if isinstance(asset, AbstainedAsset):
        return {
            "state": "abstained",
            "asset_id": _as_str(asset.asset_id, "asset.asset_id"),
            "source": _as_str(asset.source, "asset.source"),
            "reason": _as_str(asset.reason, "asset.reason"),
            "explanation": _as_str(asset.explanation, "asset.explanation"),
            "measurement": _jsonable(dict(asset.measurement), "asset.measurement"),
            "category_id": _as_str(asset.category_id, "asset.category_id"),
            "axes": {
                _as_str(k, "asset.axes key"): (
                    None if v is None else _as_float(v, f"asset.axes[{k!r}]")
                )
                for k, v in asset.axes.items()
            },
            "exemplars": _exemplars_to_json(asset.exemplars),
            "flags": [_as_str(f, "asset.flags") for f in asset.flags],
        }
    raise TypeError(f"assets must be ScoredAsset or AbstainedAsset, got {type(asset).__name__}")


def _asset_v1_1_to_json(asset: AssetV1_1) -> dict[str, Any]:
    shared = {
        "asset_id": _as_str(asset.asset_id, "asset.asset_id"),
        "source": _as_str(asset.source, "asset.source"),
        "image": _candidate_image_to_json(asset.image),
        "category_id": _as_str(asset.category_id, "asset.category_id"),
        "exemplars": _exemplars_to_json(asset.exemplars),
        "flags": [_as_str(flag, "asset.flags") for flag in asset.flags],
    }
    if isinstance(asset, ScoredAssetV1_1):
        return {
            "state": "scored",
            **shared,
            "n_local": _as_int(asset.n_local, "asset.n_local"),
            "score": _as_float(asset.score, "asset.score"),
            "interval": _interval_to_json(asset.interval),
            "rank": _as_int(asset.rank, "asset.rank"),
            "axes": {
                _as_str(key, "asset.axes key"): _as_float(value, f"asset.axes[{key!r}]")
                for key, value in asset.axes.items()
            },
        }
    if isinstance(asset, AbstainedAssetV1_1):
        return {
            "state": "abstained",
            **shared,
            "reason": _as_str(asset.reason, "asset.reason"),
            "explanation": _as_str(asset.explanation, "asset.explanation"),
            "measurement": _jsonable(dict(asset.measurement), "asset.measurement"),
            "axes": {
                _as_str(key, "asset.axes key"): (
                    None if value is None else _as_float(value, f"asset.axes[{key!r}]")
                )
                for key, value in asset.axes.items()
            },
        }
    raise TypeError(
        "report v1.1 assets must be ScoredAssetV1_1 or AbstainedAssetV1_1, "
        f"got {type(asset).__name__}"
    )


def _comparisons_to_json(comparisons: Comparisons) -> dict[str, Any]:
    return {
        "ties": [
            [_as_str(first, "comparisons.ties"), _as_str(second, "comparisons.ties")]
            for first, second in comparisons.ties
        ],
        "note": _as_str(comparisons.note, "comparisons.note"),
    }


def _provenance_v1_0_to_json(provenance: Provenance) -> dict[str, Any]:
    return {
        "engine": {
            "name": _as_str(provenance.engine.name, "provenance.engine.name"),
            "version": _as_str(provenance.engine.version, "provenance.engine.version"),
        },
        "model": {
            "repo": _as_str(provenance.model.repo, "provenance.model.repo"),
            "revision": _as_str(provenance.model.revision, "provenance.model.revision"),
            "sha256": _as_str(provenance.model.sha256, "provenance.model.sha256"),
        },
        "command": _as_str(provenance.command, "provenance.command"),
        "seed": _as_int(provenance.seed, "provenance.seed"),
        "created_at": _as_str(provenance.created_at, "provenance.created_at"),
    }


def _provenance_v1_1_to_json(provenance: ProvenanceV1_1) -> dict[str, Any]:
    engine = {
        "name": _as_str(provenance.engine.name, "provenance.engine.name"),
        "version": _as_str(provenance.engine.version, "provenance.engine.version"),
    }
    if provenance.engine.source is not None:
        engine.update(
            {
                "source_repository": _as_str(
                    provenance.engine.source.source_repository,
                    "provenance.engine.source_repository",
                ),
                "source_revision": _as_str(
                    provenance.engine.source.source_revision,
                    "provenance.engine.source_revision",
                ),
                "source_dirty": provenance.engine.source.source_dirty,
            }
        )
    return {
        "engine": engine,
        "model": {
            "repo": _as_str(provenance.model.repo, "provenance.model.repo"),
            "revision": _as_str(provenance.model.revision, "provenance.model.revision"),
            "sha256": _as_str(provenance.model.sha256, "provenance.model.sha256"),
        },
        "command": _as_str(provenance.command, "provenance.command"),
        "argv": [_as_str(argument, "provenance.argv") for argument in provenance.argv],
        "seed": _as_int(provenance.seed, "provenance.seed"),
        "created_at": _as_str(provenance.created_at, "provenance.created_at"),
        "schema": {
            "id": _as_str(provenance.schema.id, "provenance.schema.id"),
            "sha256": _as_str(provenance.schema.sha256, "provenance.schema.sha256"),
        },
    }


def to_json_dict(report: Report | ReportV1_1) -> dict[str, Any]:
    """Serialise either complete report version without projecting between versions.

    The frozen :class:`Report` type always emits 1.0. :class:`ReportV1_1` always emits every
    additive field required by ADR-0008. Neither path strips fields or relabels the other version.
    """
    if isinstance(report, ReportV1_1):
        return {
            "schema_version": _as_str(report.schema_version, "schema_version"),
            "board": _board_v1_1_to_json(report.board),
            "board_stats": _board_stats_to_json(report.board_stats),
            "references": [_reference_to_json(reference) for reference in report.references],
            "assets": [_asset_v1_1_to_json(asset) for asset in report.assets],
            "comparisons": _comparisons_to_json(report.comparisons),
            "provenance": _provenance_v1_1_to_json(report.provenance),
        }
    if isinstance(report, Report):
        return {
            "schema_version": _as_str(report.schema_version, "schema_version"),
            "board": _board_to_json(report.board),
            "board_stats": _board_stats_to_json(report.board_stats),
            "references": [_reference_to_json(reference) for reference in report.references],
            "assets": [_asset_to_json(asset) for asset in report.assets],
            "comparisons": _comparisons_to_json(report.comparisons),
            "provenance": _provenance_v1_0_to_json(report.provenance),
        }
    raise TypeError(f"report must be Report or ReportV1_1, got {type(report).__name__}")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _asset_from_json(data: Mapping[str, Any]) -> Asset:
    state = data["state"]
    exemplars = tuple(
        Exemplar(reference_id=e["reference_id"], similarity=float(e["similarity"]))
        for e in data["exemplars"]
    )
    flags = tuple(data["flags"])
    if state == "scored":
        interval = data["interval"]
        return ScoredAsset(
            state="scored",
            asset_id=data["asset_id"],
            source=data["source"],
            category_id=data["category_id"],
            n_local=int(data["n_local"]),
            score=float(data["score"]),
            interval=Interval(
                low=float(interval["low"]),
                high=float(interval["high"]),
                level=float(interval["level"]),
                method=interval["method"],
            ),
            rank=int(data["rank"]),
            axes={k: float(v) for k, v in data["axes"].items()},
            exemplars=exemplars,
            flags=flags,
        )
    if state == "abstained":
        return AbstainedAsset(
            state="abstained",
            asset_id=data["asset_id"],
            source=data["source"],
            reason=data["reason"],
            explanation=data["explanation"],
            measurement=dict(data["measurement"]),
            category_id=data["category_id"],
            axes={k: (None if v is None else float(v)) for k, v in data["axes"].items()},
            exemplars=exemplars,
            flags=flags,
        )
    raise ValueError(f"asset carries an unknown state {state!r}; expected 'scored' or 'abstained'")


def _report_v1_0_from_json(data: Mapping[str, Any]) -> Report:
    """Parse a document already validated against the frozen v1.0 schema."""

    board = data["board"]
    representation = board["representation"]
    fit = board["fit"]
    stats = data["board_stats"]
    tightness = stats["tightness"]
    provenance = data["provenance"]
    return Report(
        schema_version=data["schema_version"],
        board=Board(
            id=board["id"],
            name=board["name"],
            n_references=int(board["n_references"]),
            n_eff=float(board["n_eff"]),
            requested_alpha=float(board["requested_alpha"]),
            supported_alpha=float(board["supported_alpha"]),
            built_at=board["built_at"],
            representation=Representation(
                style=StyleModelInfo(
                    model=representation["style"]["model"],
                    revision=representation["style"]["revision"],
                    dim=int(representation["style"]["dim"]),
                ),
                axes=tuple(representation["axes"]),
            ),
            fit=BoardFit(
                metric=fit["metric"],
                k=int(fit["k"]),
                cluster_cut=float(fit["cluster_cut"]),
                dup_cut=float(fit["dup_cut"]),
                interval=IntervalMethod(
                    method=fit["interval"]["method"],
                    replicates=None,
                    seed=int(fit["interval"]["seed"]),
                ),
            ),
            categories=tuple(
                Category(
                    category_id=c["category_id"],
                    n_local=int(c["n_local"]),
                    member_ids=tuple(c["member_ids"]),
                )
                for c in board["categories"]
            ),
        ),
        board_stats=BoardStats(
            tightness=Tightness(
                loo_mean=float(tightness["loo_mean"]),
                loo_sd=float(tightness["loo_sd"]),
                loo_quantiles={k: float(v) for k, v in tightness["loo_quantiles"].items()},
            ),
            leverage=tuple(
                Leverage(
                    reference_id=lev["reference_id"],
                    delta_tightness=float(lev["delta_tightness"]),
                    rank=int(lev["rank"]),
                )
                for lev in stats["leverage"]
            ),
            flags=tuple(stats["flags"]),
        ),
        references=tuple(
            ReferenceEntry(
                reference_id=r["reference_id"],
                content_sha256=r["content_sha256"],
                mime=r["mime"],
                width=int(r["width"]),
                height=int(r["height"]),
                thumbnail=Thumbnail(
                    mime=r["thumbnail"]["mime"],
                    width=int(r["thumbnail"]["width"]),
                    height=int(r["thumbnail"]["height"]),
                    data_base64=r["thumbnail"]["data_base64"],
                ),
            )
            for r in data["references"]
        ),
        assets=tuple(_asset_from_json(a) for a in data["assets"]),
        comparisons=Comparisons(
            ties=tuple((pair[0], pair[1]) for pair in data["comparisons"]["ties"]),
            note=data["comparisons"]["note"],
        ),
        provenance=Provenance(
            engine=EngineProvenance(
                name=provenance["engine"]["name"],
                version=provenance["engine"]["version"],
            ),
            model=ModelProvenance(
                repo=provenance["model"]["repo"],
                revision=provenance["model"]["revision"],
                sha256=provenance["model"]["sha256"],
            ),
            command=provenance["command"],
            seed=int(provenance["seed"]),
            created_at=provenance["created_at"],
        ),
    )


def _axis_definition_from_json(data: Mapping[str, Any]) -> AxisDefinition:
    method = data["method"]
    return AxisDefinition(
        axis_id=data["axis_id"],
        label=data["label"],
        value_kind=data["value_kind"],
        direction=data["direction"],
        aggregation=data["aggregation"],
        availability=data["availability"],
        uncertainty=data["uncertainty"],
        method=AxisMethod(name=method["name"], revision=int(method["revision"])),
    )


def _thumbnail_from_json(data: Mapping[str, Any]) -> Thumbnail:
    return Thumbnail(
        mime=data["mime"],
        width=int(data["width"]),
        height=int(data["height"]),
        data_base64=data["data_base64"],
    )


def _candidate_image_from_json(data: Mapping[str, Any]) -> CandidateImage:
    return CandidateImage(
        content_sha256=data["content_sha256"],
        mime=data["mime"],
        width=int(data["width"]),
        height=int(data["height"]),
        thumbnail=_thumbnail_from_json(data["thumbnail"]),
    )


def _asset_v1_1_from_json(data: Mapping[str, Any]) -> AssetV1_1:
    exemplars = tuple(
        Exemplar(reference_id=entry["reference_id"], similarity=float(entry["similarity"]))
        for entry in data["exemplars"]
    )
    shared = {
        "asset_id": data["asset_id"],
        "source": data["source"],
        "image": _candidate_image_from_json(data["image"]),
        "category_id": data["category_id"],
        "exemplars": exemplars,
        "flags": tuple(data["flags"]),
    }
    if data["state"] == "scored":
        interval = data["interval"]
        return ScoredAssetV1_1(
            state="scored",
            **shared,
            n_local=int(data["n_local"]),
            score=float(data["score"]),
            interval=Interval(
                low=float(interval["low"]),
                high=float(interval["high"]),
                level=float(interval["level"]),
                method=interval["method"],
            ),
            rank=int(data["rank"]),
            axes={key: float(value) for key, value in data["axes"].items()},
        )
    if data["state"] == "abstained":
        return AbstainedAssetV1_1(
            state="abstained",
            **shared,
            reason=data["reason"],
            explanation=data["explanation"],
            measurement=dict(data["measurement"]),
            axes={
                key: (None if value is None else float(value))
                for key, value in data["axes"].items()
            },
        )
    raise ValueError(
        f"asset carries an unknown state {data['state']!r}; expected 'scored' or 'abstained'"
    )


def _report_v1_1_from_json(data: Mapping[str, Any]) -> ReportV1_1:
    board = data["board"]
    representation = board["representation"]
    fit = board["fit"]
    stats = data["board_stats"]
    tightness = stats["tightness"]
    provenance = data["provenance"]
    engine = provenance["engine"]
    source = None
    if "source_repository" in engine:
        source = EngineSourceProvenance(
            source_repository=engine["source_repository"],
            source_revision=engine["source_revision"],
            source_dirty=engine["source_dirty"],
        )
    return ReportV1_1(
        schema_version="1.1",
        board=BoardV1_1(
            id=board["id"],
            name=board["name"],
            n_references=int(board["n_references"]),
            n_eff=float(board["n_eff"]),
            requested_alpha=float(board["requested_alpha"]),
            supported_alpha=float(board["supported_alpha"]),
            built_at=board["built_at"],
            representation=RepresentationV1_1(
                style=StyleModelInfo(
                    model=representation["style"]["model"],
                    revision=representation["style"]["revision"],
                    dim=int(representation["style"]["dim"]),
                ),
                axes=tuple(representation["axes"]),
                axis_definitions=tuple(
                    _axis_definition_from_json(definition)
                    for definition in representation["axis_definitions"]
                ),
            ),
            fit=BoardFitV1_1(
                metric=fit["metric"],
                k=int(fit["k"]),
                k_cap=int(fit["k_cap"]),
                cluster_cut=float(fit["cluster_cut"]),
                dup_cut=float(fit["dup_cut"]),
                min_category_size=int(fit["min_category_size"]),
                interval_level=float(fit["interval_level"]),
                far_outlier_iqr_multiplier=float(fit["far_outlier_iqr_multiplier"]),
                far_outlier_iqr_multiplier_source=fit["far_outlier_iqr_multiplier_source"],
                interval=IntervalMethod(
                    method=fit["interval"]["method"],
                    replicates=None,
                    seed=int(fit["interval"]["seed"]),
                ),
            ),
            categories=tuple(
                Category(
                    category_id=category["category_id"],
                    n_local=int(category["n_local"]),
                    member_ids=tuple(category["member_ids"]),
                )
                for category in board["categories"]
            ),
        ),
        board_stats=BoardStats(
            tightness=Tightness(
                loo_mean=float(tightness["loo_mean"]),
                loo_sd=float(tightness["loo_sd"]),
                loo_quantiles={
                    key: float(value) for key, value in tightness["loo_quantiles"].items()
                },
            ),
            leverage=tuple(
                Leverage(
                    reference_id=entry["reference_id"],
                    delta_tightness=float(entry["delta_tightness"]),
                    rank=int(entry["rank"]),
                )
                for entry in stats["leverage"]
            ),
            flags=tuple(stats["flags"]),
        ),
        references=tuple(
            ReferenceEntry(
                reference_id=entry["reference_id"],
                content_sha256=entry["content_sha256"],
                mime=entry["mime"],
                width=int(entry["width"]),
                height=int(entry["height"]),
                thumbnail=_thumbnail_from_json(entry["thumbnail"]),
            )
            for entry in data["references"]
        ),
        assets=tuple(_asset_v1_1_from_json(asset) for asset in data["assets"]),
        comparisons=Comparisons(
            ties=tuple((pair[0], pair[1]) for pair in data["comparisons"]["ties"]),
            note=data["comparisons"]["note"],
        ),
        provenance=ProvenanceV1_1(
            engine=EngineProvenanceV1_1(
                name=engine["name"], version=engine["version"], source=source
            ),
            model=ModelProvenance(
                repo=provenance["model"]["repo"],
                revision=provenance["model"]["revision"],
                sha256=provenance["model"]["sha256"],
            ),
            command=provenance["command"],
            argv=tuple(provenance["argv"]),
            seed=int(provenance["seed"]),
            created_at=provenance["created_at"],
            schema=SchemaProvenance(
                id=provenance["schema"]["id"], sha256=provenance["schema"]["sha256"]
            ),
        ),
    )


def _schema_path_for_version(version: Any) -> Path:
    if version == SCHEMA_VERSION_V1_0:
        return SCHEMA_PATH_V1_0
    if version == SCHEMA_VERSION_V1_1:
        return SCHEMA_PATH_V1_1
    raise UnsupportedSchemaVersionError(
        f"unsupported_schema_version: expected one of ('1.0', '1.1'), got {version!r}"
    )


def from_json_dict(data: dict[str, Any]) -> Report | ReportV1_1:
    """Version-dispatch, validate, and parse a report without projecting between minors.

    The complete version token is inspected before any payload field. Unknown, malformed, or
    missing versions therefore always surface as ``unsupported_schema_version`` even if a later
    field is also malformed.
    """

    if not isinstance(data, dict):
        raise UnsupportedSchemaVersionError(
            "unsupported_schema_version: report root must be an object carrying schema_version"
        )
    schema_path = _schema_path_for_version(data.get("schema_version"))
    jsonschema.validate(
        instance=data,
        schema=_load_schema(schema_path),
        cls=jsonschema.Draft202012Validator,
    )
    report: Report | ReportV1_1
    if data["schema_version"] == SCHEMA_VERSION_V1_0:
        report = _report_v1_0_from_json(data)
    else:
        report = _report_v1_1_from_json(data)
    _validate_cross_fields(report, schema_path=schema_path)
    return report


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_axis_vocabulary(report: Report | ReportV1_1) -> None:
    """Raise ValueError, listing every offending asset_id, unless

        set(asset.axes.keys()) == {"style"} | set(report.board.representation.axes)

    holds for every asset in report.assets, in both states. A null value in ``axes["style"]`` on
    an abstained asset satisfies the invariant and a missing "style" key does not, which is the
    intended asymmetry: the vocabulary is fixed even where a number is unavailable.

    Today ``report.board.representation.axes`` is exactly {"palette", "tone", "composition"}, so
    today this reduces to the exact set equality {"style", "palette", "tone", "composition"} for
    every asset. The check is written against ``board.representation.axes`` rather than against
    the AXES constant because that field, and not the constant, is what a report with a dropped
    axis actually carries, and checking the constant would hold two enumerations of one
    vocabulary in two places for them to drift apart.
    """
    expected = {"style"} | set(report.board.representation.axes)
    offenders: list[str] = []
    for asset in report.assets:
        actual = set(asset.axes.keys())
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            offenders.append(f"  {asset.asset_id}: missing {missing}, unexpected {unexpected}")
    if offenders:
        raise ValueError(
            "axis vocabulary invariant violated. Every asset's axes keys must equal "
            f"{sorted(expected)}:\n" + "\n".join(offenders)
        )


@cache
def _load_schema(schema_path: Path) -> dict[str, Any]:
    return json.loads(schema_path.read_text(encoding="utf-8"))


def report_schema_sha256(schema_path: Path = SCHEMA_PATH_V1_1) -> str:
    """Hash the exact schema bytes named in v1.1 provenance."""

    return hashlib.sha256(schema_path.read_bytes()).hexdigest()


def _validate_thumbnail_limits(
    *, width: object, height: object, encoded_length: int, field: str
) -> int:
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, Integral)
        or not isinstance(height, Integral)
        or width < 1
        or height < 1
    ):
        raise ValueError(f"{field} thumbnail dimensions must be positive integers")
    if width > THUMBNAIL_MAX_SIDE or height > THUMBNAIL_MAX_SIDE:
        raise ValueError(f"{field} thumbnail exceeds the {THUMBNAIL_MAX_SIDE}-pixel side limit")
    pixels = int(width) * int(height)
    if pixels > THUMBNAIL_MAX_PIXELS:
        raise ValueError(f"{field} thumbnail exceeds the {THUMBNAIL_MAX_PIXELS}-pixel decode limit")
    if pixels * 4 > THUMBNAIL_MAX_DECODED_BYTES:
        raise ValueError(
            f"{field} thumbnail exceeds the {THUMBNAIL_MAX_DECODED_BYTES}-byte raster limit"
        )
    estimated_bytes = (encoded_length * 3 + 3) // 4
    if estimated_bytes > THUMBNAIL_MAX_COMPRESSED_BYTES:
        raise ValueError(
            f"{field} thumbnail exceeds the {THUMBNAIL_MAX_COMPRESSED_BYTES}-byte compressed limit"
        )
    return pixels * 4


def _validate_thumbnail(thumbnail: Thumbnail, *, field: str) -> int:
    if thumbnail.mime not in frozenset(_THUMBNAIL_MIME_BY_FORMAT.values()):
        raise ValueError(
            f"{field} thumbnail MIME must be image/png, image/jpeg, or image/webp; "
            f"got {thumbnail.mime!r}"
        )
    if not isinstance(thumbnail.data_base64, str):
        raise ValueError(f"{field} thumbnail base64 must be a string")
    declared_raster_bytes = _validate_thumbnail_limits(
        width=thumbnail.width,
        height=thumbnail.height,
        encoded_length=len(thumbnail.data_base64),
        field=field,
    )
    try:
        payload = base64.b64decode(thumbnail.data_base64, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{field} thumbnail is not strict base64: {error}") from error
    if base64.b64encode(payload).decode("ascii") != thumbnail.data_base64:
        raise ValueError(f"{field} thumbnail base64 is not in canonical padded form")
    if len(payload) > THUMBNAIL_MAX_COMPRESSED_BYTES:
        raise ValueError(
            f"{field} thumbnail exceeds the {THUMBNAIL_MAX_COMPRESSED_BYTES}-byte compressed limit"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                actual_format = image.format
                actual_size = image.size
                actual_raster_bytes = _validate_thumbnail_limits(
                    width=actual_size[0],
                    height=actual_size[1],
                    encoded_length=len(thumbnail.data_base64),
                    field=field,
                )
                image.verify()
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError(
            f"{field} thumbnail bytes are not a decodable inert image: {error}"
        ) from error
    actual_mime = _THUMBNAIL_MIME_BY_FORMAT.get(actual_format or "")
    if actual_mime != thumbnail.mime:
        raise ValueError(
            f"{field} thumbnail declares MIME {thumbnail.mime!r} but bytes decode as "
            f"{actual_mime or actual_format!r}"
        )
    if actual_size != (thumbnail.width, thumbnail.height):
        raise ValueError(
            f"{field} thumbnail declares {thumbnail.width}x{thumbnail.height} but bytes decode "
            f"as {actual_size[0]}x{actual_size[1]}"
        )
    return max(declared_raster_bytes, actual_raster_bytes)


def _validate_legacy_thumbnail_resources(thumbnail: Thumbnail, *, field: str) -> int:
    """Enforce resource limits while preserving v1.0's diagnostic decode semantics."""

    if not isinstance(thumbnail.data_base64, str):
        raise ValueError(f"{field} thumbnail base64 must be a string")
    declared_raster_bytes = _validate_thumbnail_limits(
        width=thumbnail.width,
        height=thumbnail.height,
        encoded_length=len(thumbnail.data_base64),
        field=field,
    )
    try:
        payload = base64.b64decode(thumbnail.data_base64, validate=True)
    except (ValueError, TypeError):
        return declared_raster_bytes
    if len(payload) > THUMBNAIL_MAX_COMPRESSED_BYTES:
        raise ValueError(
            f"{field} thumbnail exceeds the {THUMBNAIL_MAX_COMPRESSED_BYTES}-byte compressed limit"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                actual_size = image.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError(f"{field} thumbnail header exceeds the decode safety limit") from error
    except (OSError, SyntaxError, UnidentifiedImageError):
        return declared_raster_bytes
    actual_raster_bytes = _validate_thumbnail_limits(
        width=actual_size[0],
        height=actual_size[1],
        encoded_length=len(thumbnail.data_base64),
        field=field,
    )
    return max(declared_raster_bytes, actual_raster_bytes)


def _validate_unique_exemplars(report: Report | ReportV1_1) -> None:
    for asset in report.assets:
        identifiers = [exemplar.reference_id for exemplar in asset.exemplars]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError(f"{asset.asset_id} has a duplicate exemplar reference_id")


def _validate_v1_1_cross_fields(report: ReportV1_1, schema_path: Path) -> None:
    references = tuple(reference.reference_id for reference in report.references)
    if len(set(references)) != len(references):
        raise ValueError("report v1.1 reference_id values must be unique")
    if report.board.n_references != len(references):
        raise ValueError("report v1.1 board.n_references must equal the reference catalogue length")

    expected_definitions = axis_definitions_for(report.board.representation.axes)
    if report.board.representation.axis_definitions != expected_definitions:
        raise ValueError(
            "report v1.1 axis_definitions must be style followed by the exact governed "
            "definitions for board.representation.axes"
        )

    expected_k = min(report.board.fit.k_cap, len(references) - 1)
    if report.board.fit.k != expected_k:
        raise ValueError(
            f"report v1.1 fit.k must equal min(k_cap, n_references - 1) = {expected_k}"
        )

    position = {reference_id: index for index, reference_id in enumerate(references)}
    expected_count = min(3, len(references))
    total_raster_bytes = 0
    for index, reference in enumerate(report.references):
        total_raster_bytes += _validate_thumbnail(reference.thumbnail, field=f"references[{index}]")

    asset_ids: set[str] = set()
    for index, asset in enumerate(report.assets):
        if asset.asset_id in asset_ids:
            raise ValueError(f"report v1.1 asset_id {asset.asset_id!r} is duplicated")
        asset_ids.add(asset.asset_id)
        total_raster_bytes += _validate_thumbnail(
            asset.image.thumbnail, field=f"assets[{index}].image"
        )
        if len(asset.exemplars) != expected_count:
            raise ValueError(
                f"{asset.asset_id} has {len(asset.exemplars)} exemplars; report v1.1 requires "
                f"exactly min(3, references.length) = {expected_count}"
            )
        for exemplar in asset.exemplars:
            if exemplar.reference_id not in position:
                raise ValueError(
                    f"{asset.asset_id} exemplar {exemplar.reference_id!r} does not resolve "
                    "into references"
                )
        for previous, current in zip(asset.exemplars, asset.exemplars[1:], strict=False):
            if previous.similarity < current.similarity:
                raise ValueError(
                    f"{asset.asset_id} exemplars are not ordered by descending similarity"
                )
            if (
                previous.similarity == current.similarity
                and position[previous.reference_id] > position[current.reference_id]
            ):
                raise ValueError(
                    f"{asset.asset_id} equal-similarity exemplars do not follow reference "
                    "catalogue order"
                )

        if isinstance(asset, ScoredAssetV1_1):
            if asset.axes.get("style") != asset.score:
                raise ValueError(
                    f"{asset.asset_id} axes.style must exactly equal its repeated score value"
                )
            if asset.interval.level != report.board.fit.interval_level:
                raise ValueError(
                    f"{asset.asset_id} interval level differs from the immutable board fit policy"
                )
        else:
            if asset.axes.get("style") is not None:
                raise ValueError(f"{asset.asset_id} abstained style axis must be null")
        for axis in report.board.representation.axes:
            value = asset.axes.get(axis)
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
            ):
                raise ValueError(
                    f"{asset.asset_id} classical axis {axis!r} must be a finite numeric value"
                )

    if total_raster_bytes > THUMBNAIL_TOTAL_DECODED_BYTES:
        raise ValueError(
            "report thumbnails exceed the aggregate "
            f"{THUMBNAIL_TOTAL_DECODED_BYTES}-byte raster limit"
        )

    if report.provenance.command != shlex.join(report.provenance.argv):
        raise ValueError("provenance.command must equal shlex.join(provenance.argv)")
    if report.provenance.schema.id != _SCHEMA_ID_V1_1:
        raise ValueError(f"provenance.schema.id must equal {_SCHEMA_ID_V1_1!r}")
    if _load_schema(schema_path).get("$id") != report.provenance.schema.id:
        raise ValueError(
            "provenance.schema.id must equal the $id of the exact schema file used to validate"
        )
    measured_schema_hash = report_schema_sha256(schema_path)
    if report.provenance.schema.sha256 != measured_schema_hash:
        raise ValueError(
            "provenance.schema.sha256 does not match the exact schema bytes used to validate"
        )
    source = report.provenance.engine.source
    if source is not None:
        parsed = urlsplit(source.source_repository)
        if not parsed.scheme or not parsed.path and not parsed.netloc:
            raise ValueError("provenance.engine.source_repository must be an absolute URI")


def _validate_cross_fields(
    report: Report | ReportV1_1,
    *,
    schema_path: Path,
) -> None:
    validate_axis_vocabulary(report)
    _validate_unique_exemplars(report)
    thumbnails: list[tuple[Thumbnail, str]] = [
        (reference.thumbnail, f"references[{index}]")
        for index, reference in enumerate(report.references)
    ]
    if isinstance(report, ReportV1_1):
        thumbnails.extend(
            (asset.image.thumbnail, f"assets[{index}].image")
            for index, asset in enumerate(report.assets)
        )
    if len(thumbnails) > THUMBNAIL_MAX_COUNT:
        raise ValueError(
            f"report carries {len(thumbnails)} thumbnails and exceeds the "
            f"{THUMBNAIL_MAX_COUNT}-thumbnail decode limit"
        )
    # Count and declared geometry are available without opening an image. Refuse aggregate work
    # here so an over-budget document reaches neither Pillow header processing nor native decode.
    declared_raster_bytes = sum(
        _validate_thumbnail_limits(
            width=thumbnail.width,
            height=thumbnail.height,
            encoded_length=len(thumbnail.data_base64)
            if isinstance(thumbnail.data_base64, str)
            else 0,
            field=field,
        )
        for thumbnail, field in thumbnails
    )
    if declared_raster_bytes > THUMBNAIL_TOTAL_DECODED_BYTES:
        raise ValueError(
            "report thumbnails exceed the aggregate "
            f"{THUMBNAIL_TOTAL_DECODED_BYTES}-byte raster limit"
        )
    if isinstance(report, ReportV1_1):
        _validate_v1_1_cross_fields(report, schema_path)
    else:
        total_raster_bytes = sum(
            _validate_legacy_thumbnail_resources(reference.thumbnail, field=f"references[{index}]")
            for index, reference in enumerate(report.references)
        )
        if total_raster_bytes > THUMBNAIL_TOTAL_DECODED_BYTES:
            raise ValueError(
                "report thumbnails exceed the aggregate "
                f"{THUMBNAIL_TOTAL_DECODED_BYTES}-byte raster limit"
            )


def _validate_candidate_inputs(
    report: ReportV1_1, candidate_inputs: Sequence[CandidateImageInput]
) -> None:
    evidence_by_id: dict[str, CandidateImageInput] = {}
    for evidence in candidate_inputs:
        if evidence.asset_id in evidence_by_id:
            raise ValueError(f"candidate input evidence duplicates asset_id {evidence.asset_id!r}")
        evidence_by_id[evidence.asset_id] = evidence
    report_ids = {asset.asset_id for asset in report.assets}
    if set(evidence_by_id) != report_ids:
        raise ValueError(
            "candidate input evidence must contain exactly one entry for every report asset"
        )
    for asset in report.assets:
        evidence = evidence_by_id[asset.asset_id]
        for field in ("content_sha256", "mime", "width", "height"):
            expected = getattr(evidence, field)
            actual = getattr(asset.image, field)
            if actual != expected:
                raise ValueError(
                    f"candidate {asset.asset_id} image.{field} is {actual!r}; original "
                    f"candidate input evidence records {expected!r}"
                )


def validate_report(
    report: Report | ReportV1_1,
    schema_path: Path | None = None,
) -> None:
    """Validate one exact writer version and its version-specific cross-field rules.

    This is the function the engine calls on every report before writing it. A failure here is an
    error the caller must not catch and continue past: a report that fails its own schema is an
    error and not a warning. The axis-vocabulary invariant runs as a second explicit step because
    it is an equality between two different parts of the same document, which JSON Schema cannot
    express.
    """
    if schema_path is None:
        schema_path = SCHEMA_PATH_V1_1 if isinstance(report, ReportV1_1) else SCHEMA_PATH_V1_0
    document = to_json_dict(report)
    schema = _load_schema(schema_path)
    jsonschema.validate(instance=document, schema=schema, cls=jsonschema.Draft202012Validator)
    _validate_cross_fields(report, schema_path=schema_path)


def write_report(
    report: Report | ReportV1_1,
    path: Path,
    schema_path: Path | None = None,
    *,
    candidate_inputs: Sequence[CandidateImageInput] | None = None,
) -> None:
    """Validate the report and then write it as indented JSON.

    There is no path through this module that writes a report which has not just passed
    ``validate_report`` in the same call.
    """
    validate_report(report, schema_path)
    if isinstance(report, ReportV1_1):
        if candidate_inputs is None:
            raise ValueError(
                "report v1.1 writer requires candidate input evidence for original byte identity"
            )
        _validate_candidate_inputs(report, candidate_inputs)
    document = to_json_dict(report)
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )

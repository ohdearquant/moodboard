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

import json
import operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from numbers import Integral, Real
from pathlib import Path
from typing import Any, Literal

import jsonschema

__all__ = [
    "AXES",
    "AXIS_ORDER",
    "INTERVAL_METHOD",
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "AbstainedAsset",
    "Asset",
    "Board",
    "BoardFit",
    "BoardStats",
    "Category",
    "Comparisons",
    "EngineProvenance",
    "Exemplar",
    "Interval",
    "IntervalMethod",
    "Leverage",
    "ModelProvenance",
    "Provenance",
    "ReferenceEntry",
    "Report",
    "Representation",
    "ScoredAsset",
    "StyleModelInfo",
    "Thumbnail",
    "Tightness",
    "from_json_dict",
    "to_json_dict",
    "validate_axis_vocabulary",
    "validate_report",
    "write_report",
]

SCHEMA_VERSION: Literal["1.0"] = "1.0"

INTERVAL_METHOD: Literal["loo-jackknife-plus"] = "loo-jackknife-plus"

# The classical axis vocabulary, in the order a report lists it. ADR-0003 states that an axis
# failing its intervention test "loses its name in the report and appears as an unlabelled
# component, or comes out", so the vocabulary is data that can shrink at runtime rather than a
# fixed set of struct fields. AXIS_ORDER exists because INTERFACES.md pins the constant as a
# frozenset and also says a board carries the three names "in that order"; a frozenset cannot
# carry an order, so the ordered tuple is what a caller passes and AXES is derived from it.
AXIS_ORDER: tuple[str, ...] = ("palette", "tone", "composition")
AXES: frozenset[str] = frozenset(AXIS_ORDER)

SCHEMA_PATH: Path = Path(__file__).parent / "schema" / "report_v1_0.schema.json"


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
class Representation:
    style: StyleModelInfo
    axes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntervalMethod:
    """``replicates`` is always None: loo-jackknife-plus has no inner replicates."""

    method: Literal["loo-jackknife-plus"]
    replicates: None
    seed: int


@dataclass(frozen=True, slots=True)
class BoardFit:
    """Every parameter that can move a score, which is also what the board hash covers."""

    metric: Literal["cosine"]
    k: int
    cluster_cut: float
    dup_cut: float
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
class Provenance:
    engine: EngineProvenance
    model: ModelProvenance
    command: str
    seed: int
    created_at: str


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
        "thumbnail": {
            "mime": _as_str(reference.thumbnail.mime, "thumbnail.mime"),
            "width": _as_int(reference.thumbnail.width, "thumbnail.width"),
            "height": _as_int(reference.thumbnail.height, "thumbnail.height"),
            "data_base64": _as_str(reference.thumbnail.data_base64, "thumbnail.data_base64"),
        },
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


def to_json_dict(report: Report) -> dict[str, Any]:
    """Serialise a report to a plain JSON-compatible dict matching schema version 1.0.

    Field names and nesting are as in ADR-0002, tuples become JSON arrays, and no key is present
    for a field the dataclass in question does not have: score, interval and rank are absent on
    an abstained asset, and reason, explanation and measurement are absent on a scored one.
    """
    return {
        "schema_version": _as_str(report.schema_version, "schema_version"),
        "board": _board_to_json(report.board),
        "board_stats": _board_stats_to_json(report.board_stats),
        "references": [_reference_to_json(r) for r in report.references],
        "assets": [_asset_to_json(a) for a in report.assets],
        "comparisons": {
            "ties": [
                [_as_str(a, "comparisons.ties"), _as_str(b, "comparisons.ties")]
                for a, b in report.comparisons.ties
            ],
            "note": _as_str(report.comparisons.note, "comparisons.note"),
        },
        "provenance": {
            "engine": {
                "name": _as_str(report.provenance.engine.name, "provenance.engine.name"),
                "version": _as_str(report.provenance.engine.version, "provenance.engine.version"),
            },
            "model": {
                "repo": _as_str(report.provenance.model.repo, "provenance.model.repo"),
                "revision": _as_str(report.provenance.model.revision, "provenance.model.revision"),
                "sha256": _as_str(report.provenance.model.sha256, "provenance.model.sha256"),
            },
            "command": _as_str(report.provenance.command, "provenance.command"),
            "seed": _as_int(report.provenance.seed, "provenance.seed"),
            "created_at": _as_str(report.provenance.created_at, "provenance.created_at"),
        },
    }


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


def from_json_dict(data: dict[str, Any]) -> Report:
    """Parse an already schema-valid dict into a typed Report.

    Each entry of ``data["assets"]`` is dispatched to ScoredAsset or AbstainedAsset by its
    ``state`` key.
    """
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


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_axis_vocabulary(report: Report) -> None:
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


def validate_report(report: Report, schema_path: Path = SCHEMA_PATH) -> None:
    """Serialise the report, validate it against the committed JSON Schema, then check the axes.

    This is the function the engine calls on every report before writing it. A failure here is an
    error the caller must not catch and continue past: a report that fails its own schema is an
    error and not a warning. The axis-vocabulary invariant runs as a second explicit step because
    it is an equality between two different parts of the same document, which JSON Schema cannot
    express.
    """
    document = to_json_dict(report)
    schema = _load_schema(schema_path)
    jsonschema.validate(instance=document, schema=schema, cls=jsonschema.Draft202012Validator)
    validate_axis_vocabulary(report)


def write_report(report: Report, path: Path, schema_path: Path = SCHEMA_PATH) -> None:
    """Validate the report and then write it as indented JSON.

    There is no path through this module that writes a report which has not just passed
    ``validate_report`` in the same call.
    """
    validate_report(report, schema_path)
    document = to_json_dict(report)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

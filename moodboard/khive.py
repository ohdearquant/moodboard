"""Fail-closed process adapter for Moodboard's small Khive verb surface.

This is intentionally not a general Khive SDK.  It knows how to submit a sequence of JSON
operations through ``kkernel exec`` and how to prove that the saved JSONL has exactly one
successful, ordered result per operation. It owns the typed visual-retrieval result boundary;
embedding-array interpretation remains in :mod:`moodboard.encoders`.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from moodboard.preference import FEATURE_PRODUCER_ID, FEATURE_PRODUCER_REVISION

if TYPE_CHECKING:
    from moodboard.encoders import VisualDescriptor

__all__ = [
    "KhiveClient",
    "KhiveJudgmentRequest",
    "KhiveJudgmentResult",
    "KhiveMoodboardEntity",
    "KhivePreferenceRequest",
    "KhivePreferencePrediction",
    "KhiveProtocolError",
    "KhiveSearchHit",
    "KhiveSearchRequest",
    "KhiveSearchResult",
    "KhiveServeRequest",
    "KhiveServeOccurrence",
    "KhiveServeResult",
    "KhiveTrainedPreferenceModel",
]

_HEX = frozenset("0123456789abcdef")
_SEARCH_RESULT_KEYS = frozenset({"query_asset_id", "descriptor", "experimental", "hits"})
_SEARCH_HIT_KEYS = frozenset({"asset_id", "score", "rank", "name", "content_ref"})
_MODEL_RESULT_KEYS = frozenset({"descriptor", "experimental"})
_NAMESPACED_STORAGE_TOOLS = frozenset(
    {
        "kg.create",
        "moodboard.model",
        "moodboard.ingest",
        "moodboard.search",
        "moodboard.serve",
        "moodboard.judge",
        "moodboard.train_preference",
        "moodboard.preference",
    }
)
_DEFAULT_SEARCH_TOP_K = 20
_MAX_SEARCH_TOP_K = 100
_FEATURE_SCHEMA_ID = "f691fc73bf9a50d72157e21601fa579caa707bf2c448df546c63e915b4e42175"
_FEATURE_NAMES = (
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
_PREFERENCE_BOARD_SCHEMA_VERSION = "moodboard.preference-board.v1"
_PREFERENCE_BOARD_DESCRIPTION = "Immutable Moodboard preference-learning scope"
_PREFERENCE_BOARD_TAGS = ("moodboard", "preference-learning")
_PREFERENCE_BOARD_PROPERTIES = frozenset(
    {
        "schema_version",
        "board_id",
        "model_key",
        "descriptor_fingerprint",
        "source_report_sha256",
        "feature_schema_id",
        "feature_producer_revision",
        "feature_producer_id",
    }
)
_KG_ENTITY_KEYS = frozenset(
    {
        "id",
        "namespace",
        "created_at",
        "updated_at",
        "kind",
        "entity_type",
        "name",
        "description",
        "properties",
        "tags",
        "deleted_at",
        "merged_into",
        "merge_event_id",
        "content_ref",
    }
)


class KhiveProtocolError(ValueError):
    """Khive completed (or partially completed) without satisfying the wire contract."""


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = frozenset(value)
    if actual == expected:
        return
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    details = []
    if unknown:
        details.append(f"unknown keys {unknown}")
    if missing:
        details.append(f"missing keys {missing}")
    raise KhiveProtocolError(f"{field} has " + " and ".join(details))


def _canonical_uuid(value: Any, field: str, error_type: type[ValueError]) -> str:
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except ValueError as error:
        raise error_type(f"{field} must be a bare canonical UUID") from error
    if parsed is None or str(parsed) != value:
        raise error_type(f"{field} must be a bare canonical UUID")
    return value


def _is_hex_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _parse_visual_descriptor(value: Any) -> VisualDescriptor:
    """Reuse the encoder's one frozen descriptor parser without a second wire schema.

    The import is deliberately deferred. ``encoders`` imports this process adapter, so a
    module-level reverse import would create a cycle; every public client call happens after
    both modules are initialized. Keeping one parser is what makes model, ingest, and search
    agree on the exact closed descriptor identity.
    """
    from moodboard.encoders import VisualDescriptor

    return VisualDescriptor.parse(value)


def _parse_model_descriptor(value: Any) -> VisualDescriptor:
    if not isinstance(value, dict):
        raise KhiveProtocolError("moodboard.model result must be an object")
    _require_exact_keys(value, _MODEL_RESULT_KEYS, "moodboard.model result")
    if value.get("experimental") is not True:
        raise KhiveProtocolError("moodboard.model must explicitly report experimental=true")
    return _parse_visual_descriptor(value.get("descriptor"))


@dataclass(frozen=True, slots=True)
class KhiveSearchRequest:
    """The complete argument surface of ``moodboard.search`` v1."""

    asset_id: str
    top_k: int | None = None

    def __post_init__(self) -> None:
        _canonical_uuid(self.asset_id, "moodboard.search asset_id", ValueError)
        if self.top_k is not None and (
            not _plain_int(self.top_k) or not 1 <= self.top_k <= _MAX_SEARCH_TOP_K
        ):
            raise ValueError(
                f"moodboard.search top_k must be an integer from 1 through {_MAX_SEARCH_TOP_K}"
            )

    @property
    def effective_top_k(self) -> int:
        return _DEFAULT_SEARCH_TOP_K if self.top_k is None else self.top_k

    def to_arguments(self) -> dict[str, Any]:
        arguments: dict[str, Any] = {"asset_id": self.asset_id}
        if self.top_k is not None:
            arguments["top_k"] = self.top_k
        return arguments


@dataclass(frozen=True, slots=True)
class KhiveSearchHit:
    """One exact-cosine visual neighbour and its immutable Khive locator."""

    asset_id: str
    score: float
    rank: int
    name: str
    content_ref: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "score": self.score,
            "rank": self.rank,
            "name": self.name,
            "content_ref": self.content_ref,
        }


@dataclass(frozen=True, slots=True)
class KhiveSearchResult:
    """Validated ``moodboard.search`` result in the discovered descriptor space."""

    query_asset_id: str
    descriptor: VisualDescriptor
    experimental: Literal[True]
    hits: tuple[KhiveSearchHit, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "query_asset_id": self.query_asset_id,
            "descriptor": self.descriptor.to_json_dict(),
            "experimental": self.experimental,
            "hits": [hit.to_json_dict() for hit in self.hits],
        }


@dataclass(frozen=True, slots=True)
class KhiveMoodboardEntity:
    """Validated live ``artifact/moodboard`` used as a preference scope anchor."""

    entity_id: str
    namespace: str
    board_id: str
    model_key: str
    descriptor_fingerprint: str
    source_report_sha256: str
    feature_schema_id: str
    feature_producer_revision: str
    feature_producer_id: str


@dataclass(frozen=True, slots=True)
class KhiveServeOccurrence:
    result_occurrence_id: str
    asset_id: str
    content_ref: str
    source_rank: int | None


@dataclass(frozen=True, slots=True)
class KhiveServeRequest:
    """One typed pair presentation inside a narrow Moodboard serve batch."""

    candidates: Sequence[Mapping[str, Any]]
    candidate_pool_sha256: str
    policy_revision: str = "moodboard-demo-pairs-v1"
    pair_propensity: float | None = None


@dataclass(frozen=True, slots=True)
class KhiveServeResult:
    serve_id: str
    feature_schema_id: str
    left: KhiveServeOccurrence
    right: KhiveServeOccurrence
    swap_applied: bool


@dataclass(frozen=True, slots=True)
class KhiveJudgmentRequest:
    """One occurrence-bound judgment inside a narrow Moodboard judgment batch."""

    serve_id: str
    left_result_occurrence_id: str
    right_result_occurrence_id: str
    choice: Literal["left", "right", "tie", "abstain"]
    reason_code: str | None = None
    response_ms: int | None = None


@dataclass(frozen=True, slots=True)
class KhiveJudgmentResult:
    judgment_id: str
    serve_id: str
    choice: Literal["left", "right", "tie", "abstain"]
    reason_code: str | None
    created: bool


@dataclass(frozen=True, slots=True)
class KhivePreferenceRequest:
    """One scored occurrence pair inside a narrow Moodboard inference batch."""

    left: Mapping[str, Any]
    right: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class KhiveTrainedPreferenceModel:
    preference_model_id: str
    model_fingerprint: str
    content_ref: str
    network_sha256: str
    network_content_ref: str
    fann_inference_verified: bool
    training: Mapping[str, Any]
    calibration: Mapping[str, Any]
    test_metrics: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class KhivePreferencePrediction:
    preference_model_id: str
    model_fingerprint: str
    probability_left_given_decisive: float
    probability_right_given_decisive: float
    raw_fann_logit: float
    calibrated_temperature: float
    indifference_state: str
    conformal_state: Literal["not_computed_by_this_verb"]


@dataclass(frozen=True, slots=True)
class _KhiveOperation:
    """One operation in the JSON form accepted by ``kkernel exec --ops-file``."""

    tool: str
    args: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.tool or not isinstance(self.tool, str):
            raise ValueError("a Khive operation needs a non-empty tool name")

    def to_json_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": dict(self.args)}


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token!r}")


def _json_loads(text: str, *, source: str) -> Any:
    try:
        return json.loads(text, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise KhiveProtocolError(f"{source} is not strict JSON: {error}") from error


def _plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_hex_64(value: Any, field: str, error_type: type[ValueError]) -> str:
    if not _is_hex_digest(value):
        raise error_type(f"{field} must be 64 lowercase hexadecimal characters")
    return value


def _finite_range(
    value: Any,
    field: str,
    minimum: float,
    maximum: float,
    *,
    open_low: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a plain finite number")
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a plain finite number") from error
    lower_ok = numeric > minimum if open_low else numeric >= minimum
    if not math.isfinite(numeric) or not lower_ok or numeric > maximum:
        raise ValueError(f"{field} must be a plain finite number in the allowed range")
    return numeric


def _validate_model_identity(model_key: Any, fingerprint: Any, context: str) -> None:
    _require_hex_64(fingerprint, f"{context} descriptor_fingerprint", ValueError)
    prefix = f"moodboard_{fingerprint}_"
    dimension_text = model_key.removeprefix(prefix) if isinstance(model_key, str) else ""
    if (
        not isinstance(model_key, str)
        or not model_key.isascii()
        or not model_key.startswith(prefix)
        or not dimension_text.isdigit()
        or dimension_text.startswith("0")
        or not 1 <= int(dimension_text) <= 8192
    ):
        raise ValueError(f"{context} model_key must bind the supplied descriptor fingerprint")


def _preference_board_properties(
    *,
    board_id: str,
    model_key: str,
    descriptor_fingerprint: str,
    source_report_sha256: str,
) -> dict[str, str]:
    return {
        "schema_version": _PREFERENCE_BOARD_SCHEMA_VERSION,
        "board_id": board_id,
        "model_key": model_key,
        "descriptor_fingerprint": descriptor_fingerprint,
        "source_report_sha256": source_report_sha256,
        "feature_schema_id": _FEATURE_SCHEMA_ID,
        "feature_producer_revision": FEATURE_PRODUCER_REVISION,
        "feature_producer_id": FEATURE_PRODUCER_ID,
    }


def _require_rfc3339(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise KhiveProtocolError(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise KhiveProtocolError(f"{field} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KhiveProtocolError(f"{field} must include an explicit UTC offset")


def _parse_published_board(
    value: Any,
    *,
    namespace: str,
    name: str,
    expected_properties: Mapping[str, str],
) -> KhiveMoodboardEntity:
    if not isinstance(value, dict):
        raise KhiveProtocolError("kg.create moodboard result must be an object")
    _require_exact_keys(value, _KG_ENTITY_KEYS, "kg.create moodboard result")
    entity_id = _canonical_uuid(
        value.get("id"), "kg.create moodboard result id", KhiveProtocolError
    )
    if value.get("namespace") != namespace:
        raise KhiveProtocolError("kg.create moodboard result namespace does not match the request")
    _require_rfc3339(value.get("created_at"), "kg.create moodboard result created_at")
    _require_rfc3339(value.get("updated_at"), "kg.create moodboard result updated_at")
    if value.get("kind") != "artifact" or value.get("entity_type") != "moodboard":
        raise KhiveProtocolError("kg.create moodboard result is not artifact/moodboard")
    if value.get("name") != name:
        raise KhiveProtocolError("kg.create moodboard result name does not match the request")
    if value.get("description") != _PREFERENCE_BOARD_DESCRIPTION:
        raise KhiveProtocolError(
            "kg.create moodboard result description does not match the request"
        )
    properties = value.get("properties")
    if not isinstance(properties, dict):
        raise KhiveProtocolError("kg.create moodboard result properties must be an object")
    _require_exact_keys(properties, _PREFERENCE_BOARD_PROPERTIES, "kg.create moodboard properties")
    if properties != expected_properties:
        raise KhiveProtocolError("kg.create moodboard result properties do not match the request")
    if value.get("tags") != list(_PREFERENCE_BOARD_TAGS):
        raise KhiveProtocolError("kg.create moodboard result tags do not match the request")
    if any(
        value.get(field) is not None
        for field in ("deleted_at", "merged_into", "merge_event_id", "content_ref")
    ):
        raise KhiveProtocolError(
            "kg.create moodboard result lifecycle and content fields must all be null"
        )
    return KhiveMoodboardEntity(
        entity_id=entity_id,
        namespace=namespace,
        board_id=properties["board_id"],
        model_key=properties["model_key"],
        descriptor_fingerprint=properties["descriptor_fingerprint"],
        source_report_sha256=properties["source_report_sha256"],
        feature_schema_id=properties["feature_schema_id"],
        feature_producer_revision=properties["feature_producer_revision"],
        feature_producer_id=properties["feature_producer_id"],
    )


def _validate_preference_candidate(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"moodboard preference {field} must be an object")
    expected = frozenset({"state", "asset_id", "content_ref", "source_rank", "features"})
    if frozenset(value) != expected:
        raise ValueError(f"moodboard preference {field} must have the exact candidate keys")
    if value.get("state") != "scored":
        raise ValueError(f"moodboard preference {field} state must be scored")
    asset_id = _canonical_uuid(value.get("asset_id"), f"{field}.asset_id", ValueError)
    content_ref = _require_hex_64(value.get("content_ref"), f"{field}.content_ref", ValueError)
    source_rank = value.get("source_rank")
    if not _plain_int(source_rank) or not 1 <= source_rank <= 2**32 - 1:
        raise ValueError(f"moodboard preference {field}.source_rank must be a positive u32")
    raw_features = value.get("features")
    if not isinstance(raw_features, (list, tuple)) or len(raw_features) != len(_FEATURE_NAMES):
        raise ValueError(f"moodboard preference {field}.features must have length 10")
    features = [
        _finite_range(coordinate, f"{field}.features[{index}]", 0.0, 1.0)
        for index, coordinate in enumerate(raw_features)
    ]
    return {
        "state": "scored",
        "asset_id": asset_id,
        "content_ref": content_ref,
        "source_rank": source_rank,
        "features": features,
    }


def _parse_occurrence(value: Any, field: str) -> KhiveServeOccurrence:
    if not isinstance(value, dict):
        raise KhiveProtocolError(f"{field} must be an object")
    _require_exact_keys(
        value,
        frozenset({"result_occurrence_id", "asset_id", "content_ref", "source_rank"}),
        field,
    )
    occurrence_id = _canonical_uuid(
        value.get("result_occurrence_id"), f"{field}.result_occurrence_id", KhiveProtocolError
    )
    asset_id = _canonical_uuid(value.get("asset_id"), f"{field}.asset_id", KhiveProtocolError)
    content_ref = _require_hex_64(
        value.get("content_ref"), f"{field}.content_ref", KhiveProtocolError
    )
    source_rank = value.get("source_rank")
    if source_rank is not None and (
        not _plain_int(source_rank) or not 1 <= source_rank <= 2**32 - 1
    ):
        raise KhiveProtocolError(f"{field}.source_rank must be null or a positive u32")
    return KhiveServeOccurrence(occurrence_id, asset_id, content_ref, source_rank)


def _parse_scope(
    value: Any,
    *,
    namespace: str,
    actor: str,
    board_entity_id: str,
    board_id: str,
    model_key: str,
    descriptor_fingerprint: str,
) -> None:
    if not isinstance(value, dict):
        raise KhiveProtocolError("moodboard preference scope must be an object")
    expected = frozenset(
        {
            "namespace",
            "actor_kind",
            "actor_id",
            "board_entity_id",
            "board_id",
            "model_key",
            "descriptor_fingerprint",
            "feature_schema_id",
        }
    )
    _require_exact_keys(value, expected, "moodboard preference scope")
    for field in ("namespace", "actor_kind", "actor_id"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise KhiveProtocolError(f"moodboard preference scope {field} must be non-empty")
    required = {
        "namespace": namespace,
        "board_entity_id": board_entity_id,
        "board_id": board_id,
        "model_key": model_key,
        "descriptor_fingerprint": descriptor_fingerprint,
        "feature_schema_id": _FEATURE_SCHEMA_ID,
    }
    if f"{value.get('actor_kind')}:{value.get('actor_id')}" != actor:
        raise KhiveProtocolError("moodboard preference result scope actor does not match request")
    if any(value.get(field) != expected_value for field, expected_value in required.items()):
        raise KhiveProtocolError("moodboard preference result scope does not match the request")


def _parse_serve_result(
    value: Any,
    namespace: str,
    actor: str,
    board_entity_id: str,
    board_id: str,
    model_key: str,
    descriptor_fingerprint: str,
) -> KhiveServeResult:
    if not isinstance(value, dict):
        raise KhiveProtocolError("moodboard.serve result must be an object")
    expected = frozenset(
        {
            "schema_version",
            "serve_id",
            "scope",
            "feature_schema",
            "left",
            "right",
            "randomization",
            "experimental",
        }
    )
    _require_exact_keys(value, expected, "moodboard.serve result")
    if (
        value.get("schema_version") != "moodboard.preference-serve.v1"
        or value.get("experimental") is not True
    ):
        raise KhiveProtocolError("moodboard.serve result has unsupported identity")
    serve_id = _canonical_uuid(
        value.get("serve_id"), "moodboard.serve serve_id", KhiveProtocolError
    )
    _parse_scope(
        value.get("scope"),
        namespace=namespace,
        actor=actor,
        board_entity_id=board_entity_id,
        board_id=board_id,
        model_key=model_key,
        descriptor_fingerprint=descriptor_fingerprint,
    )
    schema = value.get("feature_schema")
    if not isinstance(schema, dict):
        raise KhiveProtocolError("moodboard.serve feature_schema must be an object")
    _require_exact_keys(
        schema,
        frozenset(
            {"schema_version", "feature_schema_id", "dtype", "bounds", "pair_transform", "features"}
        ),
        "moodboard.serve feature_schema",
    )
    if (
        schema.get("schema_version") != "moodboard.preference-features.v1"
        or schema.get("feature_schema_id") != _FEATURE_SCHEMA_ID
        or schema.get("dtype") != "float32"
        or schema.get("bounds") != [0.0, 1.0]
        or schema.get("pair_transform") != "left_minus_right"
        or schema.get("features") != list(_FEATURE_NAMES)
    ):
        raise KhiveProtocolError("moodboard.serve feature_schema does not match the client")
    left = _parse_occurrence(value.get("left"), "moodboard.serve left")
    right = _parse_occurrence(value.get("right"), "moodboard.serve right")
    if left.asset_id == right.asset_id or left.content_ref == right.content_ref:
        raise KhiveProtocolError("moodboard.serve result occurrences are not distinct")
    randomization = value.get("randomization")
    if not isinstance(randomization, dict):
        raise KhiveProtocolError("moodboard.serve randomization must be an object")
    _require_exact_keys(
        randomization,
        frozenset({"revision", "sha256", "swap_applied"}),
        "moodboard.serve randomization",
    )
    if randomization.get("revision") != "moodboard-side-v1" or not isinstance(
        randomization.get("swap_applied"), bool
    ):
        raise KhiveProtocolError("moodboard.serve randomization has unsupported identity")
    _require_hex_64(
        randomization.get("sha256"), "moodboard.serve randomization.sha256", KhiveProtocolError
    )
    return KhiveServeResult(
        serve_id=serve_id,
        feature_schema_id=_FEATURE_SCHEMA_ID,
        left=left,
        right=right,
        swap_applied=randomization["swap_applied"],
    )


def _parse_judgment_result(
    value: Any, serve_id: str, choice: str, reason_code: str | None
) -> KhiveJudgmentResult:
    if not isinstance(value, dict):
        raise KhiveProtocolError("moodboard.judge result must be an object")
    _require_exact_keys(
        value,
        frozenset(
            {
                "schema_version",
                "judgment_id",
                "serve_id",
                "choice",
                "reason_code",
                "created",
                "experimental",
            }
        ),
        "moodboard.judge result",
    )
    if (
        value.get("schema_version") != "moodboard.preference-judgment.v1"
        or value.get("serve_id") != serve_id
        or value.get("choice") != choice
        or value.get("reason_code") != reason_code
        or not isinstance(value.get("created"), bool)
        or value.get("experimental") is not True
    ):
        raise KhiveProtocolError("moodboard.judge result does not match the request")
    judgment_id = _canonical_uuid(
        value.get("judgment_id"), "moodboard.judge judgment_id", KhiveProtocolError
    )
    return KhiveJudgmentResult(judgment_id, serve_id, choice, reason_code, value["created"])


def _parse_trained_model(
    value: Any,
    *,
    namespace: str,
    actor: str,
    board_entity_id: str,
    board_id: str,
    model_key: str,
    descriptor_fingerprint: str,
) -> KhiveTrainedPreferenceModel:
    if not isinstance(value, dict):
        raise KhiveProtocolError("moodboard.train_preference result must be an object")
    expected = frozenset(
        {
            "schema_version",
            "preference_model_id",
            "content_ref",
            "model_fingerprint",
            "network_content_ref",
            "network_sha256",
            "created",
            "scope",
            "training",
            "calibration",
            "test_metrics",
            "fann_inference_verified",
            "experimental",
        }
    )
    _require_exact_keys(value, expected, "moodboard.train_preference result")
    if (
        value.get("schema_version") != "moodboard.preference-model.v1"
        or value.get("fann_inference_verified") is not True
        or value.get("experimental") is not True
        or not isinstance(value.get("created"), bool)
    ):
        raise KhiveProtocolError("moodboard.train_preference result has unsupported identity")
    model_id = _canonical_uuid(
        value.get("preference_model_id"),
        "moodboard.train_preference preference_model_id",
        KhiveProtocolError,
    )
    for field in ("content_ref", "model_fingerprint", "network_content_ref", "network_sha256"):
        _require_hex_64(value.get(field), f"moodboard.train_preference {field}", KhiveProtocolError)
    for field in ("training", "calibration", "test_metrics", "scope"):
        if not isinstance(value.get(field), dict):
            raise KhiveProtocolError(f"moodboard.train_preference {field} must be an object")
    _parse_scope(
        value["scope"],
        namespace=namespace,
        actor=actor,
        board_entity_id=board_entity_id,
        board_id=board_id,
        model_key=model_key,
        descriptor_fingerprint=descriptor_fingerprint,
    )
    return KhiveTrainedPreferenceModel(
        model_id,
        value["model_fingerprint"],
        value["content_ref"],
        value["network_sha256"],
        value["network_content_ref"],
        True,
        value["training"],
        value["calibration"],
        value["test_metrics"],
    )


def _parse_preference_prediction(
    value: Any,
    preference_model_id: str,
    source_report_sha256: str,
    *,
    namespace: str,
    actor: str,
    board_entity_id: str,
    board_id: str,
    model_key: str,
    descriptor_fingerprint: str,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> KhivePreferencePrediction:
    if not isinstance(value, dict):
        raise KhiveProtocolError("moodboard.preference result must be an object")
    expected = frozenset(
        {
            "schema_version",
            "prediction_kind",
            "conditional_on",
            "probability_left_given_decisive",
            "probability_right_given_decisive",
            "raw_fann_logit",
            "calibrated_temperature",
            "indifference",
            "conformal_evidence",
            "preference_model_id",
            "model_content_ref",
            "model_fingerprint",
            "source_report_sha256",
            "scope",
            "left",
            "right",
            "experimental",
        }
    )
    _require_exact_keys(value, expected, "moodboard.preference result")
    if (
        value.get("schema_version") != "moodboard.preference.v1"
        or value.get("prediction_kind") != "learned_pairwise_preference"
        or value.get("conditional_on") != "decisive_judgment"
        or value.get("preference_model_id") != preference_model_id
        or value.get("source_report_sha256") != source_report_sha256
        or value.get("experimental") is not True
    ):
        raise KhiveProtocolError("moodboard.preference result does not match the request")
    left_probability = _finite_range(
        value.get("probability_left_given_decisive"), "probability_left", 0.0, 1.0
    )
    right_probability = _finite_range(
        value.get("probability_right_given_decisive"), "probability_right", 0.0, 1.0
    )
    if not math.isclose(left_probability + right_probability, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise KhiveProtocolError("moodboard.preference probabilities must sum to one")
    raw_logit = _finite_range(value.get("raw_fann_logit"), "raw_fann_logit", -1e30, 1e30)
    temperature = _finite_range(
        value.get("calibrated_temperature"), "calibrated_temperature", 0.0, 1e30, open_low=True
    )
    indifference = value.get("indifference")
    conformal = value.get("conformal_evidence")
    if not isinstance(indifference, dict) or indifference.get("state") not in {
        "inside_calibrated_band",
        "outside_calibrated_band",
    }:
        raise KhiveProtocolError("moodboard.preference indifference is invalid")
    if not isinstance(conformal, dict) or conformal.get("state") != "not_computed_by_this_verb":
        raise KhiveProtocolError("moodboard.preference must keep conformal evidence separate")
    for field in ("model_content_ref", "model_fingerprint"):
        _require_hex_64(value.get(field), f"moodboard.preference {field}", KhiveProtocolError)
    _parse_scope(
        value.get("scope"),
        namespace=namespace,
        actor=actor,
        board_entity_id=board_entity_id,
        board_id=board_id,
        model_key=model_key,
        descriptor_fingerprint=descriptor_fingerprint,
    )
    for field, requested in (("left", left), ("right", right)):
        occurrence = value.get(field)
        if not isinstance(occurrence, dict):
            raise KhiveProtocolError(f"moodboard.preference result {field} must be an object")
        _require_exact_keys(
            occurrence,
            frozenset({"asset_id", "content_ref"}),
            f"moodboard.preference result {field}",
        )
        if (
            occurrence.get("asset_id") != requested["asset_id"]
            or occurrence.get("content_ref") != requested["content_ref"]
        ):
            raise KhiveProtocolError(
                f"moodboard.preference result {field} does not match requested identity"
            )
    return KhivePreferencePrediction(
        preference_model_id,
        value["model_fingerprint"],
        left_probability,
        right_probability,
        raw_logit,
        temperature,
        indifference["state"],
        "not_computed_by_this_verb",
    )


class KhiveClient:
    """Invoke the Khive Moodboard pack with pinned attribution and namespace.

    The executable may be a real ``kkernel`` or a contract-compatible test double.  No
    operation payload is placed in argv: both input and output travel through private temporary
    files, which avoids platform argument limits for base64-encoded images.
    """

    def __init__(
        self,
        *,
        executable: str | Path,
        actor: str,
        namespace: str,
        config: str | Path | None = None,
    ) -> None:
        self.executable = str(executable)
        self.actor = actor
        self.namespace = namespace
        self.config = None if config is None else str(config)
        self._model_descriptor: VisualDescriptor | None = None
        for field, value in (
            ("executable", self.executable),
            ("actor", self.actor),
            ("namespace", self.namespace),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Khive {field} must be a non-empty string")
        if self.config is not None and not self.config.strip():
            raise ValueError("Khive config must be absent or a non-empty path")

    def model(self) -> Any:
        """Return the raw model result after binding its closed descriptor identity."""
        result = self._execute((_KhiveOperation("moodboard.model", {}),))[0]
        descriptor = _parse_model_descriptor(result)
        if (
            self._model_descriptor is not None
            and descriptor.canonical_json != self._model_descriptor.canonical_json
        ):
            raise KhiveProtocolError(
                "moodboard.model descriptor drifted within one Khive client session"
            )
        self._model_descriptor = descriptor
        return result

    def publish_board(
        self,
        *,
        name: str,
        board_id: str,
        model_key: str,
        descriptor_fingerprint: str,
        source_report_sha256: str,
    ) -> KhiveMoodboardEntity:
        """Create the one live ``artifact/moodboard`` required by ADR-149.

        This remains a narrow application operation: callers cannot use the client to dispatch
        arbitrary KG verbs or choose a different entity shape.
        """

        if (
            not isinstance(name, str)
            or not name.strip()
            or name.strip() != name
            or len(name.encode("utf-8")) > 512
        ):
            raise ValueError("kg.create moodboard name must be a trimmed non-empty UTF-8 string")
        _require_hex_64(board_id, "kg.create moodboard board_id", ValueError)
        _validate_model_identity(model_key, descriptor_fingerprint, "kg.create moodboard")
        _require_hex_64(
            source_report_sha256, "kg.create moodboard source_report_sha256", ValueError
        )
        properties = _preference_board_properties(
            board_id=board_id,
            model_key=model_key,
            descriptor_fingerprint=descriptor_fingerprint,
            source_report_sha256=source_report_sha256,
        )
        value = self._execute(
            (
                _KhiveOperation(
                    "kg.create",
                    {
                        "kind": "entity",
                        "entity_kind": "artifact",
                        "entity_type": "moodboard",
                        "name": name,
                        "description": _PREFERENCE_BOARD_DESCRIPTION,
                        "properties": properties,
                        "tags": list(_PREFERENCE_BOARD_TAGS),
                        "skip_dedup_check": True,
                    },
                ),
            )
        )[0]
        return _parse_published_board(
            value,
            namespace=self.namespace,
            name=name,
            expected_properties=properties,
        )

    def ingest(self, arguments: Sequence[Mapping[str, Any]]) -> tuple[Any, ...]:
        """Submit one ordered `moodboard.ingest` batch and return its raw results."""
        return self._execute(
            tuple(_KhiveOperation("moodboard.ingest", argument) for argument in arguments)
        )

    def search(self, asset_id: str, top_k: int | None = None) -> KhiveSearchResult:
        """Return exact visual neighbours without assigning them coherence semantics.

        Discovery happens before the first search and the returned descriptor must match it
        byte-for-canonical-byte. The method exposes only the Moodboard pack's one retrieval
        request rather than a generic Khive verb executor.
        """
        request = KhiveSearchRequest(asset_id=asset_id, top_k=top_k)
        if self._model_descriptor is None:
            self.model()
        assert self._model_descriptor is not None  # established by model() or an earlier call
        value = self._execute((_KhiveOperation("moodboard.search", request.to_arguments()),))[0]
        return self._parse_search_result(value, request, self._model_descriptor)

    def serve(
        self,
        *,
        board_entity_id: str,
        board_id: str,
        model_key: str,
        descriptor_fingerprint: str,
        source_report_sha256: str,
        candidates: Sequence[Mapping[str, Any]],
        candidate_pool_sha256: str,
        policy_revision: str = "moodboard-demo-pairs-v1",
        pair_propensity: float | None = None,
    ) -> KhiveServeResult:
        """Persist one randomized, occurrence-bound comparison presentation."""

        return self.batch_serve(
            board_entity_id=board_entity_id,
            board_id=board_id,
            model_key=model_key,
            descriptor_fingerprint=descriptor_fingerprint,
            source_report_sha256=source_report_sha256,
            requests=(
                KhiveServeRequest(
                    candidates=candidates,
                    candidate_pool_sha256=candidate_pool_sha256,
                    policy_revision=policy_revision,
                    pair_propensity=pair_propensity,
                ),
            ),
        )[0]

    def batch_serve(
        self,
        *,
        board_entity_id: str,
        board_id: str,
        model_key: str,
        descriptor_fingerprint: str,
        source_report_sha256: str,
        requests: Sequence[KhiveServeRequest],
    ) -> tuple[KhiveServeResult, ...]:
        """Persist an ordered batch of independent randomized pair presentations."""

        _canonical_uuid(board_entity_id, "moodboard.serve board_entity_id", ValueError)
        _require_hex_64(board_id, "moodboard.serve board_id", ValueError)
        _validate_model_identity(model_key, descriptor_fingerprint, "moodboard.serve")
        _require_hex_64(source_report_sha256, "moodboard.serve source_report_sha256", ValueError)
        request_rows = tuple(requests)
        if not request_rows:
            raise ValueError("moodboard.batch_serve requests must not be empty")
        operations: list[_KhiveOperation] = []
        for request_index, request in enumerate(request_rows):
            if not isinstance(request, KhiveServeRequest):
                raise ValueError(
                    f"moodboard.batch_serve requests[{request_index}] must be KhiveServeRequest"
                )
            _require_hex_64(
                request.candidate_pool_sha256,
                f"moodboard.serve requests[{request_index}].candidate_pool_sha256",
                ValueError,
            )
            if len(request.candidates) != 2:
                raise ValueError(
                    f"moodboard.serve requests[{request_index}].candidates must contain exactly two"
                )
            validated = [
                _validate_preference_candidate(
                    candidate,
                    f"requests[{request_index}].candidates[{candidate_index}]",
                )
                for candidate_index, candidate in enumerate(request.candidates)
            ]
            if (
                validated[0]["asset_id"] == validated[1]["asset_id"]
                or validated[0]["content_ref"] == validated[1]["content_ref"]
            ):
                raise ValueError(
                    f"moodboard.serve requests[{request_index}].candidates must have distinct "
                    "asset and content IDs"
                )
            if (
                not isinstance(request.policy_revision, str)
                or not request.policy_revision.strip()
                or request.policy_revision.strip() != request.policy_revision
                or len(request.policy_revision.encode("utf-8")) > 128
            ):
                raise ValueError(
                    f"moodboard.serve requests[{request_index}].policy_revision must be a "
                    "trimmed non-empty string"
                )
            if request.pair_propensity is not None:
                _finite_range(
                    request.pair_propensity,
                    f"moodboard.serve requests[{request_index}].pair_propensity",
                    0.0,
                    1.0,
                    open_low=True,
                )
            selection: dict[str, Any] = {
                "policy_revision": request.policy_revision,
                "candidate_pool_sha256": request.candidate_pool_sha256,
            }
            if request.pair_propensity is not None:
                selection["pair_propensity"] = request.pair_propensity
            operations.append(
                _KhiveOperation(
                    "moodboard.serve",
                    {
                        "board_entity_id": board_entity_id,
                        "board_id": board_id,
                        "descriptor": {
                            "model_key": model_key,
                            "descriptor_fingerprint": descriptor_fingerprint,
                        },
                        "feature_schema_id": _FEATURE_SCHEMA_ID,
                        "source_report_sha256": source_report_sha256,
                        "candidates": validated,
                        "selection": selection,
                        "presentation": {
                            "preference_probability_shown": False,
                            "source_rank_shown": True,
                        },
                    },
                )
            )
        values = self._execute(tuple(operations))
        results = tuple(
            _parse_serve_result(
                value,
                self.namespace,
                self.actor,
                board_entity_id,
                board_id,
                model_key,
                descriptor_fingerprint,
            )
            for value in values
        )
        serve_ids = [result.serve_id for result in results]
        occurrence_ids = [
            occurrence.result_occurrence_id
            for result in results
            for occurrence in (result.left, result.right)
        ]
        if len(set(serve_ids)) != len(serve_ids) or len(set(occurrence_ids)) != len(occurrence_ids):
            raise KhiveProtocolError(
                "moodboard.batch_serve returned duplicate serve or occurrence identities"
            )
        return results

    def judge(
        self,
        *,
        serve_id: str,
        left_result_occurrence_id: str,
        right_result_occurrence_id: str,
        choice: Literal["left", "right", "tie", "abstain"],
        reason_code: str | None = None,
        response_ms: int | None = None,
    ) -> KhiveJudgmentResult:
        """Append one exact immutable judgment; retries remain server-idempotent."""

        return self.batch_judge(
            requests=(
                KhiveJudgmentRequest(
                    serve_id=serve_id,
                    left_result_occurrence_id=left_result_occurrence_id,
                    right_result_occurrence_id=right_result_occurrence_id,
                    choice=choice,
                    reason_code=reason_code,
                    response_ms=response_ms,
                ),
            )
        )[0]

    def batch_judge(
        self, *, requests: Sequence[KhiveJudgmentRequest]
    ) -> tuple[KhiveJudgmentResult, ...]:
        """Append an ordered batch of exact occurrence-bound judgments."""

        request_rows = tuple(requests)
        if not request_rows:
            raise ValueError("moodboard.batch_judge requests must not be empty")
        allowed = {
            "left": {None, "style", "palette", "tone", "composition", "other"},
            "right": {None, "style", "palette", "tone", "composition", "other"},
            "tie": {None, "equally_good", "equally_bad", "other"},
            "abstain": {
                "insufficient_context",
                "both_unacceptable",
                "render_failure",
                "other",
            },
        }
        operations: list[_KhiveOperation] = []
        serve_ids: set[str] = set()
        for request_index, request in enumerate(request_rows):
            if not isinstance(request, KhiveJudgmentRequest):
                raise ValueError(
                    f"moodboard.batch_judge requests[{request_index}] must be KhiveJudgmentRequest"
                )
            for field, value in (
                ("serve_id", request.serve_id),
                ("left_result_occurrence_id", request.left_result_occurrence_id),
                ("right_result_occurrence_id", request.right_result_occurrence_id),
            ):
                _canonical_uuid(
                    value,
                    f"moodboard.judge requests[{request_index}].{field}",
                    ValueError,
                )
            if request.serve_id in serve_ids:
                raise ValueError("moodboard.batch_judge cannot submit one serve more than once")
            serve_ids.add(request.serve_id)
            if request.choice not in allowed:
                raise ValueError("moodboard.judge choice must be left, right, tie, or abstain")
            if request.reason_code not in allowed[request.choice]:
                raise ValueError("moodboard.judge reason_code is incompatible with choice")
            if request.response_ms is not None and (
                not _plain_int(request.response_ms) or not 0 <= request.response_ms <= 3_600_000
            ):
                raise ValueError("moodboard.judge response_ms must be an integer from 0 to 3600000")
            arguments: dict[str, Any] = {
                "serve_id": request.serve_id,
                "left_result_occurrence_id": request.left_result_occurrence_id,
                "right_result_occurrence_id": request.right_result_occurrence_id,
                "choice": request.choice,
            }
            if request.reason_code is not None:
                arguments["reason_code"] = request.reason_code
            if request.response_ms is not None:
                arguments["response_ms"] = request.response_ms
            operations.append(_KhiveOperation("moodboard.judge", arguments))
        values = self._execute(tuple(operations))
        results = tuple(
            _parse_judgment_result(
                value,
                request.serve_id,
                request.choice,
                request.reason_code,
            )
            for value, request in zip(values, request_rows, strict=True)
        )
        judgment_ids = [result.judgment_id for result in results]
        if len(set(judgment_ids)) != len(judgment_ids):
            raise KhiveProtocolError("moodboard.batch_judge returned duplicate judgment identities")
        return results

    def train_preference(
        self,
        *,
        board_entity_id: str,
        board_id: str,
        model_key: str,
        descriptor_fingerprint: str,
    ) -> KhiveTrainedPreferenceModel:
        """Train and publish the actor/board scoped immutable FANN model."""

        _canonical_uuid(board_entity_id, "moodboard.train_preference board_entity_id", ValueError)
        _require_hex_64(board_id, "moodboard.train_preference board_id", ValueError)
        _validate_model_identity(model_key, descriptor_fingerprint, "moodboard.train_preference")
        value = self._execute(
            (
                _KhiveOperation(
                    "moodboard.train_preference",
                    {
                        "board_entity_id": board_entity_id,
                        "board_id": board_id,
                        "descriptor": {
                            "model_key": model_key,
                            "descriptor_fingerprint": descriptor_fingerprint,
                        },
                        "feature_schema_id": _FEATURE_SCHEMA_ID,
                    },
                ),
            )
        )[0]
        return _parse_trained_model(
            value,
            namespace=self.namespace,
            actor=self.actor,
            board_entity_id=board_entity_id,
            board_id=board_id,
            model_key=model_key,
            descriptor_fingerprint=descriptor_fingerprint,
        )

    def preference(
        self,
        *,
        preference_model_id: str,
        board_entity_id: str,
        board_id: str,
        model_key: str,
        descriptor_fingerprint: str,
        source_report_sha256: str,
        left: Mapping[str, Any],
        right: Mapping[str, Any],
    ) -> KhivePreferencePrediction:
        """Run the loaded FANN head without merging it into coherence evidence."""

        return self.batch_preference(
            preference_model_id=preference_model_id,
            board_entity_id=board_entity_id,
            board_id=board_id,
            model_key=model_key,
            descriptor_fingerprint=descriptor_fingerprint,
            source_report_sha256=source_report_sha256,
            requests=(KhivePreferenceRequest(left=left, right=right),),
        )[0]

    def batch_preference(
        self,
        *,
        preference_model_id: str,
        board_entity_id: str,
        board_id: str,
        model_key: str,
        descriptor_fingerprint: str,
        source_report_sha256: str,
        requests: Sequence[KhivePreferenceRequest],
    ) -> tuple[KhivePreferencePrediction, ...]:
        """Run one immutable FANN model over an ordered batch of scored pairs."""

        _canonical_uuid(preference_model_id, "moodboard.preference preference_model_id", ValueError)
        _canonical_uuid(board_entity_id, "moodboard.preference board_entity_id", ValueError)
        _require_hex_64(board_id, "moodboard.preference board_id", ValueError)
        _validate_model_identity(model_key, descriptor_fingerprint, "moodboard.preference")
        _require_hex_64(
            source_report_sha256, "moodboard.preference source_report_sha256", ValueError
        )
        request_rows = tuple(requests)
        if not request_rows:
            raise ValueError("moodboard.batch_preference requests must not be empty")
        operations: list[_KhiveOperation] = []
        validated_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for request_index, request in enumerate(request_rows):
            if not isinstance(request, KhivePreferenceRequest):
                raise ValueError(
                    f"moodboard.batch_preference requests[{request_index}] must be "
                    "KhivePreferenceRequest"
                )
            left_value = _validate_preference_candidate(
                request.left, f"requests[{request_index}].left"
            )
            right_value = _validate_preference_candidate(
                request.right, f"requests[{request_index}].right"
            )
            if (
                left_value["asset_id"] == right_value["asset_id"]
                or left_value["content_ref"] == right_value["content_ref"]
            ):
                raise ValueError(
                    f"moodboard.preference requests[{request_index}] candidates must have "
                    "distinct identities"
                )
            validated_pairs.append((left_value, right_value))
            operations.append(
                _KhiveOperation(
                    "moodboard.preference",
                    {
                        "preference_model_id": preference_model_id,
                        "board_entity_id": board_entity_id,
                        "board_id": board_id,
                        "descriptor": {
                            "model_key": model_key,
                            "descriptor_fingerprint": descriptor_fingerprint,
                        },
                        "feature_schema_id": _FEATURE_SCHEMA_ID,
                        "source_report_sha256": source_report_sha256,
                        "left": left_value,
                        "right": right_value,
                    },
                )
            )
        values = self._execute(tuple(operations))
        return tuple(
            _parse_preference_prediction(
                value,
                preference_model_id,
                source_report_sha256,
                namespace=self.namespace,
                actor=self.actor,
                board_entity_id=board_entity_id,
                board_id=board_id,
                model_key=model_key,
                descriptor_fingerprint=descriptor_fingerprint,
                left=left_value,
                right=right_value,
            )
            for value, (left_value, right_value) in zip(values, validated_pairs, strict=True)
        )

    @staticmethod
    def _parse_search_result(
        value: Any,
        request: KhiveSearchRequest,
        expected_descriptor: VisualDescriptor,
    ) -> KhiveSearchResult:
        if not isinstance(value, dict):
            raise KhiveProtocolError("moodboard.search result must be an object")
        _require_exact_keys(value, _SEARCH_RESULT_KEYS, "moodboard.search result")
        query_asset_id = _canonical_uuid(
            value.get("query_asset_id"),
            "moodboard.search result query_asset_id",
            KhiveProtocolError,
        )
        if query_asset_id != request.asset_id:
            raise KhiveProtocolError(
                "moodboard.search result query_asset_id does not match the requested asset"
            )
        if value.get("experimental") is not True:
            raise KhiveProtocolError(
                "moodboard.search result must explicitly report experimental=true"
            )
        descriptor = _parse_visual_descriptor(value.get("descriptor"))
        if descriptor.canonical_json != expected_descriptor.canonical_json:
            raise KhiveProtocolError(
                "moodboard.search result has descriptor drift from moodboard.model"
            )

        raw_hits = value.get("hits")
        if not isinstance(raw_hits, list):
            raise KhiveProtocolError("moodboard.search result hits must be an array")
        if len(raw_hits) > request.effective_top_k:
            raise KhiveProtocolError(
                f"moodboard.search returned {len(raw_hits)} hits, more hits than requested "
                f"({request.effective_top_k})"
            )

        hits: list[KhiveSearchHit] = []
        seen_asset_ids: set[str] = set()
        previous_score = math.inf
        for index, raw_hit in enumerate(raw_hits):
            if not isinstance(raw_hit, dict):
                raise KhiveProtocolError(f"moodboard.search hit {index} must be an object")
            _require_exact_keys(raw_hit, _SEARCH_HIT_KEYS, f"moodboard.search hit {index}")
            hit_asset_id = _canonical_uuid(
                raw_hit.get("asset_id"),
                f"moodboard.search hit {index} asset_id",
                KhiveProtocolError,
            )
            if hit_asset_id == query_asset_id:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} must exclude the query asset"
                )
            if hit_asset_id in seen_asset_ids:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} has duplicate asset_id {hit_asset_id}"
                )
            seen_asset_ids.add(hit_asset_id)

            raw_score = raw_hit.get("score")
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} score must be a plain JSON number"
                )
            try:
                score = float(raw_score)
            except (OverflowError, TypeError, ValueError) as error:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} score must be a finite cosine in [-1,1]"
                ) from error
            if not math.isfinite(score) or not -1.0 <= score <= 1.0:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} score must be a finite cosine in [-1,1]"
                )
            if score > previous_score:
                raise KhiveProtocolError("moodboard.search hits must be in descending cosine order")
            previous_score = score

            rank = raw_hit.get("rank")
            if not _plain_int(rank) or rank != index + 1:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} must carry one-based contiguous rank {index + 1}"
                )
            name = raw_hit.get("name")
            if not isinstance(name, str) or not name.strip() or len(name.encode("utf-8")) > 512:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} name must be a non-empty UTF-8 string of "
                    "at most 512 bytes"
                )
            content_ref = raw_hit.get("content_ref")
            if not _is_hex_digest(content_ref):
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} content_ref must be 64 lowercase "
                    "hexadecimal characters"
                )
            hits.append(
                KhiveSearchHit(
                    asset_id=hit_asset_id,
                    score=score,
                    rank=rank,
                    name=name,
                    content_ref=content_ref,
                )
            )

        return KhiveSearchResult(
            query_asset_id=query_asset_id,
            descriptor=descriptor,
            experimental=True,
            hits=tuple(hits),
        )

    def _execute(self, operations: Sequence[_KhiveOperation]) -> tuple[Any, ...]:
        """Return one result per operation, or expose no result and raise.

        ``--serial`` makes the physical execution order match the submitted order and prevents
        concurrent readers from contending on shared pack state. ``--strict`` makes Khive signal
        a failed row in its process status. The checks below are still required: a truncated
        result file, a mismatched manifest, or an executable that does not honour strict mode must
        not be accepted just because its status is zero.
        """
        submitted = tuple(self._bind_storage_namespace(operation) for operation in operations)
        if not submitted:
            return ()

        with tempfile.TemporaryDirectory(prefix="moodboard-khive-") as directory:
            root = Path(directory)
            ops_path = root / "ops.jsonl"
            save_path = root / "results.jsonl"
            try:
                with ops_path.open("w", encoding="utf-8", newline="\n") as stream:
                    for operation in submitted:
                        json.dump(
                            operation.to_json_dict(),
                            stream,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                            allow_nan=False,
                        )
                        stream.write("\n")
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Khive operation arguments are not strict JSON: {error}"
                ) from error

            command = [
                self.executable,
                "exec",
            ]
            if self.config is not None:
                command.extend(["--config", self.config])
            command.extend(
                [
                    "--ops-file",
                    str(ops_path),
                    "--save-file",
                    str(save_path),
                    "--namespace",
                    self.namespace,
                    "--actor",
                    self.actor,
                    "--expect-actor",
                    self.actor,
                    "--presentation",
                    "verbose",
                    "--output-format",
                    "json",
                    "--serial",
                    "--strict",
                ]
            )
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                detail = completed.stderr.strip()
                suffix = f": {detail}" if detail else ""
                raise KhiveProtocolError(
                    f"kkernel exec returned exit status {completed.returncode}{suffix}"
                )

            manifest_lines = [line for line in completed.stdout.splitlines() if line.strip()]
            if len(manifest_lines) != 1:
                raise KhiveProtocolError(
                    "kkernel exec must print exactly one JSON manifest line when --save-file "
                    f"is used; received {len(manifest_lines)} non-blank lines"
                )
            manifest = _json_loads(manifest_lines[0], source="kkernel save manifest")
            if not isinstance(manifest, dict):
                raise KhiveProtocolError("kkernel save manifest must be a JSON object")
            if not save_path.is_file():
                raise KhiveProtocolError("kkernel reported success but wrote no result JSONL file")

            payload = save_path.read_bytes()
            self._validate_manifest(manifest, save_path, payload, len(submitted))
            rows = self._parse_rows(payload, submitted)
            return tuple(row["result"] for row in rows)

    def _bind_storage_namespace(self, operation: _KhiveOperation) -> _KhiveOperation:
        """Bind a Moodboard pack operation to this client's durable storage namespace.

        ``kkernel --namespace`` remains the actor/gate attribution namespace. The Moodboard
        pack also requires the same value inside ``args`` to select durable asset, vector, and
        retrieval state. Low-level callers may repeat the configured value, but cannot replace
        it with a conflicting storage namespace.
        """
        if operation.tool not in _NAMESPACED_STORAGE_TOOLS:
            return operation
        arguments = dict(operation.args)
        missing = object()
        supplied = arguments.get("namespace", missing)
        if supplied is not missing and supplied != self.namespace:
            raise ValueError(
                f"{operation.tool} namespace {supplied!r} conflicts with the configured "
                f"Khive namespace {self.namespace!r}"
            )
        arguments["namespace"] = self.namespace
        return _KhiveOperation(operation.tool, arguments)

    @staticmethod
    def _validate_manifest(
        manifest: Mapping[str, Any], save_path: Path, payload: bytes, expected_rows: int
    ) -> None:
        manifest_path = manifest.get("path")
        if not isinstance(manifest_path, str) or not Path(manifest_path).is_absolute():
            raise KhiveProtocolError(
                "kkernel save manifest path must be an absolute path to the result file"
            )
        try:
            returned_target = Path(manifest_path).resolve(strict=True)
            requested_target = save_path.resolve(strict=True)
        except OSError as error:
            raise KhiveProtocolError(
                f"kkernel save manifest path cannot be resolved: {error}"
            ) from error
        if returned_target != requested_target:
            raise KhiveProtocolError(
                "kkernel save manifest path does not resolve to the requested --save-file"
            )
        rows = manifest.get("rows")
        if not _plain_int(rows) or rows != expected_rows:
            raise KhiveProtocolError(
                f"kkernel manifest describes {rows!r} result rows; expected {expected_rows}"
            )
        checksum = manifest.get("checksum")
        measured = hashlib.sha256(payload).hexdigest()
        if checksum != measured:
            raise KhiveProtocolError(
                f"kkernel result checksum mismatch: manifest {checksum!r}, measured {measured!r}"
            )
        summary = manifest.get("summary")
        if not isinstance(summary, dict):
            raise KhiveProtocolError("kkernel save manifest has no result summary object")
        expected_summary = {
            "aborted": 0,
            "failed": 0,
            "succeeded": expected_rows,
            "total": expected_rows,
        }
        for key, expected in expected_summary.items():
            value = summary.get(key)
            if not _plain_int(value) or value != expected:
                if key in {"failed", "aborted"} and value:
                    raise KhiveProtocolError(f"kkernel manifest reported failure: {key}={value!r}")
                raise KhiveProtocolError(
                    f"kkernel manifest summary has {key}={value!r}; expected {expected}"
                )

    @staticmethod
    def _parse_rows(payload: bytes, submitted: Sequence[_KhiveOperation]) -> list[dict[str, Any]]:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise KhiveProtocolError(f"kkernel result JSONL is not UTF-8: {error}") from error
        lines = text.splitlines()
        if len(lines) != len(submitted) or any(not line.strip() for line in lines):
            raise KhiveProtocolError(
                f"kkernel result JSONL has {len(lines)} rows; expected {len(submitted)}"
            )

        parsed: list[dict[str, Any]] = []
        for index, (line, operation) in enumerate(zip(lines, submitted, strict=True)):
            row = _json_loads(line, source=f"kkernel result JSONL row {index}")
            if not isinstance(row, dict):
                raise KhiveProtocolError(f"kkernel result JSONL row {index} is not an object")
            if row.get("tool") != operation.tool:
                raise KhiveProtocolError(
                    f"kkernel result row {index} is for {row.get('tool')!r}, expected "
                    f"{operation.tool!r}; batch order is not trustworthy"
                )
            if row.get("ok") is not True:
                detail = row.get("error", "no error detail")
                raise KhiveProtocolError(
                    f"kkernel operation {index} ({operation.tool}) reported failure: {detail}"
                )
            if "error" in row:
                raise KhiveProtocolError(
                    f"kkernel operation {index} ({operation.tool}) reports ok=true but also "
                    "carries an error field"
                )
            if "aborted" in row and row.get("aborted") is not False:
                raise KhiveProtocolError(
                    f"kkernel operation {index} ({operation.tool}) reports ok=true but also "
                    f"carries aborted={row.get('aborted')!r}"
                )
            if "result" not in row:
                raise KhiveProtocolError(
                    f"kkernel operation {index} ({operation.tool}) has no result field"
                )
            parsed.append(row)
        return parsed

"""Closed typed judgments for the executable aesthetic judge.

The committed Draft 2020-12 schema is the structural authority.  This module validates an
unmodified JSON data model first, checks the kind-specific immutable identity second, applies the
few cross-field rules JSON Schema cannot express clearly, and only then projects into frozen
Python values.  It never coerces, reranks, or combines evidence from different judgment kinds.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeAlias

import jsonschema

from moodboard.contracts import (
    ContractIdentityError,
    canonical_json_bytes,
    verify_document_identity,
)

__all__ = [
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "BoardCompatibilityJudgment",
    "ConstraintVerificationJudgment",
    "HumanComparisonJudgment",
    "IntentEligibilityJudgment",
    "Judgment",
    "JudgmentError",
    "PreferencePredictionJudgment",
    "SourceSimilarityJudgment",
    "from_json_dict",
    "to_json_dict",
    "validate_locality_blocking_pair",
    "validate_judgment",
]

SCHEMA_VERSION: Literal["moodboard.judgment.v1"] = "moodboard.judgment.v1"
SCHEMA_PATH = Path(__file__).parent / "schema" / "judgment_v1.schema.json"

_MACHINE_KINDS = frozenset(
    {
        "intent_eligibility",
        "source_similarity",
        "board_compatibility",
        "constraint_verification",
        "preference_prediction",
    }
)
_HUMAN_REASONS: dict[str, frozenset[str | None]] = {
    "left": frozenset({None, "style", "palette", "tone", "composition", "other"}),
    "right": frozenset({None, "style", "palette", "tone", "composition", "other"}),
    "tie": frozenset({None, "equally_good", "equally_bad", "other"}),
    "abstain": frozenset(
        {"insufficient_context", "both_unacceptable", "render_failure", "other"}
    ),
}


class JudgmentError(ValueError):
    """A document cannot be admitted as one exact v1 judgment."""


FrozenJson: TypeAlias = (
    None | bool | int | float | str | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]
)


@dataclass(frozen=True, slots=True)
class _JudgmentBase:
    schema_version: str
    evidence_id: str
    kind: str
    subject: Mapping[str, FrozenJson]
    result: Mapping[str, FrozenJson]
    authority: Mapping[str, FrozenJson]
    evidence_ref: Mapping[str, FrozenJson]


@dataclass(frozen=True, slots=True)
class IntentEligibilityJudgment(_JudgmentBase):
    """One route-owned eligibility decision for one asset occurrence."""


@dataclass(frozen=True, slots=True)
class SourceSimilarityJudgment(_JudgmentBase):
    """One ordered, stable-filtered source-image cosine result."""


@dataclass(frozen=True, slots=True)
class BoardCompatibilityJudgment(_JudgmentBase):
    """One board-relative conformal result or explicit abstention/non-computation."""


@dataclass(frozen=True, slots=True)
class ConstraintVerificationJudgment(_JudgmentBase):
    """One named verifier's pass, fail, or not-run result."""


@dataclass(frozen=True, slots=True)
class HumanComparisonJudgment(_JudgmentBase):
    """One explicit, serve-bound human pairwise event."""


@dataclass(frozen=True, slots=True)
class PreferencePredictionJudgment(_JudgmentBase):
    """One immutable-snapshot prediction for one declared pair."""


Judgment: TypeAlias = (
    IntentEligibilityJudgment
    | SourceSimilarityJudgment
    | BoardCompatibilityJudgment
    | ConstraintVerificationJudgment
    | HumanComparisonJudgment
    | PreferencePredictionJudgment
)

_KIND_TYPES: dict[str, type[_JudgmentBase]] = {
    "intent_eligibility": IntentEligibilityJudgment,
    "source_similarity": SourceSimilarityJudgment,
    "board_compatibility": BoardCompatibilityJudgment,
    "constraint_verification": ConstraintVerificationJudgment,
    "human_comparison": HumanComparisonJudgment,
    "preference_prediction": PreferencePredictionJudgment,
}


@cache
def _schema() -> dict[str, Any]:
    try:
        value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, jsonschema.SchemaError) as error:
        raise JudgmentError(f"judgment schema is unavailable or invalid: {error}") from error
    return value


@cache
def _validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(_schema(), format_checker=jsonschema.FormatChecker())


def _json_path(error: jsonschema.ValidationError) -> str:
    if not error.absolute_path:
        return "$"
    return "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
    )


def _error_sort_key(error: jsonschema.ValidationError) -> tuple[tuple[int, str], ...]:
    """Make validation-error selection deterministic across mixed object/array paths."""

    return tuple(
        (0, f"{part:020d}") if isinstance(part, int) else (1, part)
        for part in error.absolute_path
    )


def _freeze_json(value: Any) -> FrozenJson:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: FrozenJson) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise JudgmentError(f"{field} must be a canonical UUID string")
    try:
        measured = str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise JudgmentError(f"{field} must be a canonical UUID string") from error
    if measured != value:
        raise JudgmentError(f"{field} must use canonical lowercase UUID spelling")
    return value


def _validate_similarity(document: Mapping[str, Any]) -> None:
    authority = document["authority"]
    _validate_model_key(
        authority["model_key"],
        authority["descriptor_fingerprint"],
        "source similarity authority",
    )
    result = document["result"]
    if result["state"] != "computed":
        return
    rows = result["rows"]
    routed = [row["routed_rank"] for row in rows]
    if routed != list(range(1, len(rows) + 1)):
        raise JudgmentError("source_similarity routed_rank must be contiguous array order")
    source = [row["source_search_rank"] for row in rows]
    if source != sorted(source) or len(source) != len(set(source)):
        raise JudgmentError(
            "source_similarity source_search_rank must be a strictly increasing stable filter"
        )
    scores = [row["source_similarity"] for row in rows]
    if any(left < right for left, right in zip(scores, scores[1:], strict=False)):
        raise JudgmentError(
            "source_similarity scores must be non-increasing in preserved source-rank order"
        )
    assets = [row["asset_id"] for row in rows]
    refs = [row["content_ref"] for row in rows]
    if len(assets) != len(set(assets)) or len(refs) != len(set(refs)):
        raise JudgmentError("source_similarity rows must have unique asset and content identities")


def _validate_intent(document: Mapping[str, Any]) -> None:
    result = document["result"]
    authority = document["authority"]
    if result["state"] == "eligible" and result["collection"] != authority["value"]:
        raise JudgmentError("eligible intent result must name the declared collection value")


def _validate_board(document: Mapping[str, Any]) -> None:
    authority = document["authority"]
    if authority["input_digest"] != authority["source_report_sha256"]:
        raise JudgmentError(
            "board authority input_digest must equal its exact source_report_sha256"
        )
    result = document["result"]
    if result["state"] == "scored" and result["interval"]["low"] > result["interval"]["high"]:
        raise JudgmentError("board compatibility interval low must not exceed high")
    if result["state"] != "abstained":
        return
    measurement = result["measurement"]
    if result["reason"] in {"resolution", "multi_modality"}:
        if result["category_id"] != measurement["category_id"]:
            raise JudgmentError("board abstention category must match its measurement")
        if measurement["n_eff_local"] > measurement["n_local"] + 1e-12:
            raise JudgmentError("board abstention n_eff_local cannot exceed n_local")
        if measurement["n_local"] > measurement["n_references"]:
            raise JudgmentError("board abstention n_local cannot exceed board reference count")
        is_whole_board = measurement["n_local"] == measurement["n_references"]
        expected_reason = "resolution" if is_whole_board else "multi_modality"
        if result["reason"] != expected_reason:
            raise JudgmentError("board abstention reason does not match its category population")
        resolution = 1.0 / (measurement["n_local"] + 1)
        if not math.isclose(
            measurement["resolution_alpha"], resolution, rel_tol=0.0, abs_tol=1e-15
        ):
            raise JudgmentError("board abstention resolution_alpha does not match n_local")
        supported = 1.0 / (measurement["n_eff_local"] + 1)
        if not math.isclose(
            measurement["supported_alpha"], supported, rel_tol=0.0, abs_tol=1e-15
        ):
            raise JudgmentError("board abstention supported_alpha does not match n_eff_local")
        binding = "effective" if supported > resolution else "achievability"
        if measurement["binding_floor"] != binding:
            raise JudgmentError("board abstention binding_floor does not match its measured floors")
        if measurement["requested_alpha"] >= measurement["supported_alpha"]:
            raise JudgmentError(
                "board resolution abstention requires requested alpha below support"
            )
    else:
        threshold = measurement["reference_max"] + (
            measurement["iqr_multiplier"] * measurement["reference_iqr"]
        )
        if not math.isclose(measurement["threshold"], threshold, rel_tol=0.0, abs_tol=1e-15):
            raise JudgmentError("board far-outlier threshold does not match its registered formula")
        if measurement["candidate_alpha"] <= measurement["threshold"]:
            raise JudgmentError(
                "board far-outlier abstention requires candidate alpha above threshold"
            )


def _validate_constraint(document: Mapping[str, Any]) -> None:
    result = document["result"]
    authority = document["authority"]
    if authority["schema_version"] == "moodboard.verifier.raster-structure.v1":
        _validate_structural_constraint(result, authority)
        return
    if authority["schema_version"] != "moodboard.verifier.outside-mask-rgb-exact.v1":
        return
    if result["state"] == "not_run":
        return
    measurements = result["measurements"]
    protected = measurements["protected_pixel_count"]
    changed = measurements["changed_pixel_count"]
    maximum = measurements["max_abs_channel_error"]
    if protected <= 0:
        raise JudgmentError("exact locality requires at least one protected pixel")
    if changed > protected:
        raise JudgmentError("changed protected pixels cannot exceed protected pixel count")
    if (changed == 0) != (maximum == 0):
        raise JudgmentError(
            "changed_pixel_count and max_abs_channel_error must agree on whether a change exists"
        )
    if result["state"] == "pass" and (changed != 0 or maximum != 0):
        raise JudgmentError("exact locality passes only with zero changed pixels and channel error")
    if result["state"] == "fail" and changed == 0 and maximum == 0:
        raise JudgmentError("exact locality failure must carry a measured protected difference")


def _validate_structural_constraint(
    result: Mapping[str, Any], authority: Mapping[str, Any]
) -> None:
    measurements = result["measurements"]
    container_decoded = measurements["container_decoded"]
    canonical_compiled = measurements["canonical_raster_compiled"]
    inspected_fields = (
        measurements["frame_count"],
        measurements["output_width"],
        measurements["output_height"],
        measurements["output_mode"],
        measurements["opaque"],
    )
    if container_decoded and any(value is None for value in inspected_fields):
        raise JudgmentError("decoded structural evidence requires complete inspection measurements")
    if not container_decoded and any(value is not None for value in inspected_fields):
        raise JudgmentError("undecoded structural evidence cannot claim inspection measurements")
    if canonical_compiled != (authority["output_raster_sha256"] is not None):
        raise JudgmentError(
            "canonical_raster_compiled must agree with output_raster_sha256 presence"
        )
    if canonical_compiled and (
        not container_decoded
        or measurements["frame_count"] != 1
        or measurements["output_mode"] != "RGB"
        or measurements["opaque"] is not True
    ):
        raise JudgmentError("canonical structural raster requires one opaque RGB frame")
    if result["state"] == "pass":
        if (
            not canonical_compiled
            or measurements["frame_count"] != 1
            or measurements["output_width"] != measurements["source_width"]
            or measurements["output_height"] != measurements["source_height"]
            or measurements["output_mode"] != "RGB"
            or measurements["opaque"] is not True
        ):
            raise JudgmentError("structural pass requires one opaque source-sized RGB frame")
        return
    reason = result["reason"]
    if reason in {"decode_failed", "decode_limit_exceeded"} and container_decoded:
        raise JudgmentError(f"{reason} structural evidence cannot claim a decoded container")
    if reason != "dimension_mismatch" and canonical_compiled:
        raise JudgmentError(
            "only structural pass or dimension_mismatch may bind a canonical output raster"
        )
    if reason == "unsafe_decoder_warning" and not container_decoded:
        raise JudgmentError("unsafe_decoder_warning requires decoded inspection measurements")
    if reason == "dimension_mismatch" and (
        not canonical_compiled
        or (
            measurements["output_width"] == measurements["source_width"]
            and measurements["output_height"] == measurements["source_height"]
        )
    ):
        raise JudgmentError("dimension_mismatch requires decoded unequal source/output dimensions")
    if reason == "unsupported_frame_count" and measurements["frame_count"] == 1:
        raise JudgmentError("unsupported_frame_count cannot report exactly one frame")
    if reason == "non_opaque" and measurements["opaque"] is not False:
        raise JudgmentError("non_opaque structural failure must report opaque false")
    if reason in {"unsupported_frame_count", "non_opaque", "unsupported_color_contract"} and (
        not container_decoded or canonical_compiled
    ):
        raise JudgmentError(
            "inspected structural incompatibility must be decoded without a canonical raster"
        )


def _validate_human(document: Mapping[str, Any]) -> None:
    evidence_id = _validate_uuid(document["evidence_id"], "evidence_id")
    authority = document["authority"]
    judgment_id = _validate_uuid(authority["judgment_id"], "authority.judgment_id")
    if evidence_id != judgment_id:
        raise JudgmentError("human comparison evidence_id must equal authority.judgment_id")
    subject = document["subject"]
    if subject["left_output_occurrence_id"] == subject["right_output_occurrence_id"]:
        raise JudgmentError("human comparison output occurrences must be distinct")
    if subject["left_result_occurrence_id"] == subject["right_result_occurrence_id"]:
        raise JudgmentError("human comparison Khive result occurrences must be distinct")
    result = document["result"]
    if result.get("reason_code") not in _HUMAN_REASONS[result["choice"]]:
        raise JudgmentError("human comparison reason_code is incompatible with choice")
    presentation = authority["presentation"]
    if presentation["preference_probability_shown"] or presentation["source_rank_shown"]:
        raise JudgmentError("human comparison v1 must bind a blind pre-judgment presentation")
    scope = authority["scope"]
    _validate_model_key(
        scope["model_key"], scope["descriptor_fingerprint"], "human comparison scope"
    )


def _validate_prediction(document: Mapping[str, Any]) -> None:
    subject = document["subject"]
    if subject["left_output_occurrence_id"] == subject["right_output_occurrence_id"]:
        raise JudgmentError("preference prediction output occurrences must be distinct")
    scope = document["authority"]["scope"]
    _validate_model_key(
        scope["model_key"], scope["descriptor_fingerprint"], "preference prediction scope"
    )
    result = document["result"]
    if result["state"] != "predicted":
        return
    left = result["probability_left_given_decisive"]
    right = result["probability_right_given_decisive"]
    if not math.isclose(left + right, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise JudgmentError("preference probabilities must sum to one within 1e-12")


def _validate_cross_fields(document: Mapping[str, Any]) -> None:
    kind = document["kind"]
    if kind == "intent_eligibility":
        _validate_intent(document)
    elif kind == "source_similarity":
        _validate_similarity(document)
    elif kind == "board_compatibility":
        _validate_board(document)
    elif kind == "constraint_verification":
        _validate_constraint(document)
    elif kind == "preference_prediction":
        _validate_prediction(document)


def _validate_model_key(model_key: Any, fingerprint: Any, owner: str) -> None:
    if not isinstance(model_key, str) or not isinstance(fingerprint, str):
        raise JudgmentError(f"{owner} has invalid model identity fields")
    prefix = "moodboard_"
    if not model_key.startswith(prefix):
        raise JudgmentError(f"{owner} model_key must use the Moodboard descriptor format")
    encoded = model_key[len(prefix) :]
    measured_fingerprint, separator, dimension_text = encoded.rpartition("_")
    if (
        not separator
        or measured_fingerprint != fingerprint
        or not dimension_text.isascii()
        or not dimension_text.isdigit()
        or not 1 <= int(dimension_text) <= 8192
    ):
        raise JudgmentError(f"{owner} model_key does not bind its descriptor fingerprint")


def validate_judgment(document: dict[str, Any]) -> None:
    """Validate one raw v1 judgment without coercion or mutation."""

    if not isinstance(document, dict):
        raise JudgmentError("judgment root must be a JSON object")
    try:
        canonical_json_bytes(document)
    except ContractIdentityError as error:
        raise JudgmentError(str(error)) from error
    if document.get("schema_version") != SCHEMA_VERSION:
        raise JudgmentError(
            f"unsupported_schema_version: expected {SCHEMA_VERSION!r}, "
            f"got {document.get('schema_version')!r}"
        )
    errors = sorted(_validator().iter_errors(document), key=_error_sort_key)
    if errors:
        first = errors[0]
        raise JudgmentError(f"invalid judgment at {_json_path(first)}: {first.message}")
    kind = document["kind"]
    try:
        if kind in _MACHINE_KINDS:
            verify_document_identity(
                document,
                schema_version=SCHEMA_VERSION,
                identity_field="evidence_id",
            )
        else:
            _validate_human(document)
    except ContractIdentityError as error:
        raise JudgmentError(str(error)) from error
    _validate_cross_fields(document)


def validate_locality_blocking_pair(
    structural: dict[str, Any], locality: dict[str, Any]
) -> None:
    """Validate the two-receipt structural-fail/locality-not-run relationship."""

    validate_judgment(structural)
    validate_judgment(locality)
    if (
        structural["kind"] != "constraint_verification"
        or structural["authority"]["schema_version"]
        != "moodboard.verifier.raster-structure.v1"
        or structural["result"]["state"] != "fail"
    ):
        raise JudgmentError("blocking evidence must be one failed raster-structure judgment")
    if (
        locality["kind"] != "constraint_verification"
        or locality["authority"]["schema_version"]
        != "moodboard.verifier.outside-mask-rgb-exact.v1"
        or locality["result"]["state"] != "not_run"
    ):
        raise JudgmentError("blocked locality evidence must be one exact-locality not_run judgment")
    if structural["subject"] != locality["subject"]:
        raise JudgmentError(
            "structural and locality judgments must name the same output occurrence"
        )
    if locality["authority"]["blocking_structural_evidence_id"] != structural["evidence_id"]:
        raise JudgmentError("locality not_run must bind the exact structural-failure evidence id")
    if (
        locality["authority"]["source_raster_sha256"]
        != structural["authority"]["source_raster_sha256"]
    ):
        raise JudgmentError("structural and locality judgments must bind the same source raster")


def from_json_dict(document: dict[str, Any]) -> Judgment:
    """Validate and freeze one exact judgment document."""

    validate_judgment(document)
    judgment_type = _KIND_TYPES[document["kind"]]
    return judgment_type(
        schema_version=document["schema_version"],
        evidence_id=document["evidence_id"],
        kind=document["kind"],
        subject=_freeze_json(document["subject"]),  # type: ignore[arg-type]
        result=_freeze_json(document["result"]),  # type: ignore[arg-type]
        authority=_freeze_json(document["authority"]),  # type: ignore[arg-type]
        evidence_ref=_freeze_json(document["evidence_ref"]),  # type: ignore[arg-type]
    )


def to_json_dict(judgment: Judgment) -> dict[str, Any]:
    """Return a fresh JSON object for one frozen typed judgment."""

    if not isinstance(judgment, _JudgmentBase):
        raise JudgmentError("value is not a typed Moodboard judgment")
    if _KIND_TYPES.get(judgment.kind) is not type(judgment):
        raise JudgmentError("typed judgment class does not match its kind discriminator")
    document = {
        "schema_version": judgment.schema_version,
        "evidence_id": judgment.evidence_id,
        "kind": judgment.kind,
        "subject": _thaw_json(judgment.subject),
        "result": _thaw_json(judgment.result),
        "authority": _thaw_json(judgment.authority),
        "evidence_ref": _thaw_json(judgment.evidence_ref),
    }
    validate_judgment(document)
    return document

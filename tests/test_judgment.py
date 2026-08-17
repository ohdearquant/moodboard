"""Executable contract tests for the closed typed-judgment vocabulary in ADR-0012."""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError, is_dataclass, replace
from typing import Any

import jsonschema
import pytest

from moodboard.abstain import check_far_outlier, check_resolution
from moodboard.conformal import CategoryPartition
from moodboard.contracts import compute_document_identity
from moodboard.judgment import (
    SCHEMA_PATH,
    SCHEMA_VERSION,
    JudgmentError,
    from_json_dict,
    to_json_dict,
    validate_judgment,
    validate_locality_blocking_pair,
)

JsonObject = dict[str, Any]
JsonPath = tuple[str | int, ...]

_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "evidence_id",
        "kind",
        "subject",
        "result",
        "authority",
        "evidence_ref",
    }
)

_ASSET_A = "00000000-0000-4000-8000-000000000001"
_ASSET_B = "00000000-0000-4000-8000-000000000002"
_SERVE_ID = "00000000-0000-4000-8000-000000000003"
_LEFT_RESULT_ID = "00000000-0000-4000-8000-000000000004"
_RIGHT_RESULT_ID = "00000000-0000-4000-8000-000000000005"
_JUDGMENT_ID = "00000000-0000-4000-8000-000000000006"
_PREFERENCE_MODEL_ID = "00000000-0000-4000-8000-000000000007"
_BOARD_ENTITY_ID = "00000000-0000-4000-8000-000000000008"


def _digest(character: str) -> str:
    assert len(character) == 1 and character in "0123456789abcdef"
    return character * 64


def _artifact_ref(character: str) -> JsonObject:
    return {"kind": "artifact", "artifact_id": _digest(character)}


def _content_ref(character: str) -> JsonObject:
    return {"kind": "content_ref", "content_ref": _digest(character)}


def _with_computed_identity(document: JsonObject) -> JsonObject:
    document["evidence_id"] = _digest("0")
    document["evidence_id"] = compute_document_identity(
        document,
        schema_version="moodboard.judgment.v1",
        identity_field="evidence_id",
    )
    return document


def _valid_documents() -> dict[str, JsonObject]:
    intent = _with_computed_identity(
        {
            "schema_version": "moodboard.judgment.v1",
            "kind": "intent_eligibility",
            "subject": {
                "kind": "asset_occurrence",
                "asset_id": _ASSET_A,
                "content_ref": _digest("1"),
                "route_query_occurrence_id": _digest("2"),
            },
            "result": {
                "state": "eligible",
                "route_reason": "declared_collection_match",
                "collection": "fruit-lemon",
            },
            "authority": {
                "schema_version": "moodboard.intent-route.collection-gate.v1",
                "input_digest": _digest("3"),
                "route_policy_id": _digest("4"),
                "eligible_corpus_sha256": _digest("5"),
                "field": "collection",
                "operator": "equals",
                "value": "fruit-lemon",
                "empty_result_policy": "no_ungated_fallback",
                "interpretation": "structural_routing_control_not_learned_retrieval_quality",
            },
            "evidence_ref": _artifact_ref("4"),
        }
    )
    similarity = _with_computed_identity(
        {
            "schema_version": "moodboard.judgment.v1",
            "kind": "source_similarity",
            "subject": {
                "kind": "retrieval_result",
                "query_occurrence_id": _digest("5"),
                "ordered_result_artifact_id": _digest("6"),
            },
            "result": {
                "state": "computed",
                "rows": [
                    {
                        "routed_rank": 1,
                        "source_search_rank": 4,
                        "asset_id": _ASSET_B,
                        "content_ref": _digest("7"),
                        "source_similarity": 0.8432995826005936,
                    },
                    {
                        "routed_rank": 2,
                        "source_search_rank": 7,
                        "asset_id": _ASSET_A,
                        "content_ref": _digest("8"),
                        "source_similarity": 0.7482006549835205,
                    },
                ],
            },
            "authority": {
                "schema_version": "moodboard.source-similarity.v1",
                "input_digest": _digest("9"),
                "descriptor_fingerprint": _digest("a"),
                "model_key": f"moodboard_{_digest('a')}_96",
                "metric": "source_image_cosine",
                "method": "khive_exact_cosine_over_lattice_visual_embeddings",
                "preference_applied": False,
                "reranker": None,
            },
            "evidence_ref": _content_ref("b"),
        }
    )
    board = _with_computed_identity(
        {
            "schema_version": "moodboard.judgment.v1",
            "kind": "board_compatibility",
            "subject": {
                "kind": "selectable_output_occurrence",
                "output_occurrence_id": _digest("c"),
            },
            "result": {
                "state": "scored",
                "score": 0.75,
                "interval": {
                    "low": 0.5,
                    "high": 1.0,
                    "level": 0.9,
                    "method": "loo-jackknife-plus",
                },
                "rank": 1,
            },
            "authority": {
                "schema_version": "moodboard.board-compatibility.v1",
                "input_digest": _digest("d"),
                "source_report_schema_version": "1.1",
                "source_report_sha256": _digest("d"),
                "board_id": _digest("e"),
                "report_asset_id": "candidate-01",
            },
            "evidence_ref": _artifact_ref("f"),
        }
    )
    constraint = _with_computed_identity(
        {
            "schema_version": "moodboard.judgment.v1",
            "kind": "constraint_verification",
            "subject": {
                "kind": "selectable_output_occurrence",
                "output_occurrence_id": _digest("1"),
            },
            "result": {
                "state": "pass",
                "measurements": {
                    "protected_pixel_count": 921600,
                    "changed_pixel_count": 0,
                    "max_abs_channel_error": 0,
                },
            },
            "authority": {
                "schema_version": "moodboard.verifier.outside-mask-rgb-exact.v1",
                "input_digest": _digest("2"),
                "source_raster_sha256": _digest("4"),
                "output_raster_sha256": _digest("5"),
                "mask_sha256": _digest("6"),
            },
            "evidence_ref": _artifact_ref("3"),
        }
    )
    human = {
        "schema_version": "moodboard.judgment.v1",
        "evidence_id": _JUDGMENT_ID,
        "kind": "human_comparison",
        "subject": {
            "kind": "comparison_pair",
            "serve_id": _SERVE_ID,
            "left_output_occurrence_id": _digest("4"),
            "right_output_occurrence_id": _digest("5"),
            "left_result_occurrence_id": _LEFT_RESULT_ID,
            "right_result_occurrence_id": _RIGHT_RESULT_ID,
        },
        "result": {
            "state": "recorded",
            "choice": "left",
            "reason_code": "style",
            "response_ms": 1200,
        },
        "authority": {
            "schema_version": "moodboard.preference-judgment.v1",
            "judgment_id": _JUDGMENT_ID,
            "principal_id": "00000000-0000-4000-8000-000000000009",
            "evidence_class": "human_explicit",
            "presentation": {
                "revision": "moodboard.studio.blind-comparison.v1",
                "preference_probability_shown": False,
                "source_rank_shown": False,
            },
            "scope": {
                "namespace": "studio:human-fixture",
                "actor_kind": "actor",
                "actor_id": "person:fixture",
                "board_entity_id": _BOARD_ENTITY_ID,
                "board_id": _digest("e"),
                "model_key": f"moodboard_{_digest('7')}_96",
                "descriptor_fingerprint": _digest("7"),
                "feature_schema_id": _digest("f"),
            },
        },
        "evidence_ref": _artifact_ref("6"),
    }
    fingerprint = _digest("7")
    prediction = _with_computed_identity(
        {
            "schema_version": "moodboard.judgment.v1",
            "kind": "preference_prediction",
            "subject": {
                "kind": "comparison_pair",
                "pair_id": _digest("8"),
                "left_output_occurrence_id": _digest("9"),
                "right_output_occurrence_id": _digest("a"),
            },
            "result": {
                "state": "predicted",
                "prediction_kind": "learned_pairwise_preference",
                "conditional_on": "decisive_judgment",
                "probability_left_given_decisive": 0.75,
                "probability_right_given_decisive": 0.25,
                "raw_fann_logit": 0.8,
                "calibrated_temperature": 1.25,
                "indifference": {"state": "outside_calibrated_band"},
                "conformal_evidence": {"state": "not_computed_by_this_verb"},
            },
            "authority": {
                "schema_version": "moodboard.preference.v1",
                "input_digest": _digest("2"),
                "preference_model_id": _PREFERENCE_MODEL_ID,
                "model_content_ref": _digest("b"),
                "model_fingerprint": _digest("c"),
                "source_report_sha256": _digest("d"),
                "principal_id": "00000000-0000-4000-8000-000000000009",
                "evidence_class": "human_explicit",
                "scope": {
                    "namespace": "studio:human-fixture",
                    "actor_kind": "actor",
                    "actor_id": "person:fixture",
                    "board_entity_id": _BOARD_ENTITY_ID,
                    "board_id": _digest("e"),
                    "model_key": f"moodboard_{fingerprint}_96",
                    "descriptor_fingerprint": fingerprint,
                    "feature_schema_id": _digest("f"),
                },
            },
            "evidence_ref": _artifact_ref("1"),
        }
    )
    return {
        "intent_eligibility": intent,
        "source_similarity": similarity,
        "board_compatibility": board,
        "constraint_verification": constraint,
        "human_comparison": human,
        "preference_prediction": prediction,
    }


def _valid_state_documents() -> dict[str, JsonObject]:
    """Exercise every closed non-success state and every explicit human choice."""

    documents = _valid_documents()
    variants: dict[str, JsonObject] = {}

    for state, reason in (
        ("excluded", "declared_collection_mismatch"),
        ("not_computed", "route_not_run"),
    ):
        document = copy.deepcopy(documents["intent_eligibility"])
        document["result"] = {"state": state, "route_reason": reason}
        _refresh_machine_identity(document)
        variants[f"intent_{state}"] = document

    for state in ("empty", "not_computed", "refused"):
        document = copy.deepcopy(documents["source_similarity"])
        document["result"] = {"state": state}
        _refresh_machine_identity(document)
        variants[f"similarity_{state}"] = document

    for reason in ("resolution", "multi_modality"):
        document = copy.deepcopy(documents["board_compatibility"])
        document["result"] = {
            "state": "abstained",
            "category_id": "look-01",
            "reason": reason,
            "measurement": {
                "n_local": 8,
                "n_eff_local": 4.0,
                "n_eff_local_source": "duplicate_groups",
                "n_references": 8 if reason == "resolution" else 12,
                "n_categories": 1 if reason == "resolution" else 2,
                "resolution_alpha": 1 / 9,
                "supported_alpha": 0.2,
                "binding_floor": "effective",
                "requested_alpha": 0.05,
                "category_id": "look-01",
            },
            "explanation": "The requested distinction is finer than this local board supports.",
        }
        _refresh_machine_identity(document)
        variants[f"board_{reason}"] = document

    far_outlier = copy.deepcopy(documents["board_compatibility"])
    far_outlier["result"] = {
        "state": "abstained",
        "category_id": "look-01",
        "reason": "far_outlier",
        "measurement": {
            "candidate_alpha": 1.2,
            "reference_max": 0.8,
            "reference_iqr": 0.1,
            "threshold": 0.95,
            "iqr_multiplier": 1.5,
            "iqr_multiplier_source": "board.fit.far_outlier_iqr_multiplier",
        },
        "explanation": "The occurrence is a far outlier relative to these references.",
    }
    _refresh_machine_identity(far_outlier)
    variants["board_far_outlier"] = far_outlier

    board_not_computed = copy.deepcopy(documents["board_compatibility"])
    board_not_computed["result"] = {"state": "not_computed"}
    _refresh_machine_identity(board_not_computed)
    variants["board_not_computed"] = board_not_computed

    failed = copy.deepcopy(documents["constraint_verification"])
    failed["result"] = {
        "state": "fail",
        "measurements": {
            "protected_pixel_count": 100,
            "changed_pixel_count": 1,
            "max_abs_channel_error": 12,
        },
    }
    _refresh_machine_identity(failed)
    variants["constraint_fail"] = failed

    structural_pass = copy.deepcopy(documents["constraint_verification"])
    structural_pass["result"] = {
        "state": "pass",
        "measurements": {
            "source_width": 1280,
            "source_height": 960,
            "container_decoded": True,
            "canonical_raster_compiled": True,
            "frame_count": 1,
            "output_width": 1280,
            "output_height": 960,
            "output_mode": "RGB",
            "opaque": True,
        },
    }
    structural_pass["authority"] = {
        "schema_version": "moodboard.verifier.raster-structure.v1",
        "input_digest": _digest("2"),
        "source_raster_sha256": _digest("4"),
        "output_content_sha256": _digest("8"),
        "output_raster_sha256": _digest("9"),
        "decoder_revision": "moodboard.raster.srgb-u8.fixture.v1",
    }
    _refresh_machine_identity(structural_pass)
    variants["constraint_structural_pass"] = structural_pass

    structural_fail = copy.deepcopy(structural_pass)
    structural_fail["result"] = {
        "state": "fail",
        "reason": "dimension_mismatch",
        "measurements": {
            "source_width": 1280,
            "source_height": 960,
            "container_decoded": True,
            "canonical_raster_compiled": True,
            "frame_count": 1,
            "output_width": 1184,
            "output_height": 864,
            "output_mode": "RGB",
            "opaque": True,
        },
    }
    _refresh_machine_identity(structural_fail)
    variants["constraint_structural_fail"] = structural_fail

    structural_failures = {
        "decode_failed": (False, None, None, None, None, None),
        "decode_limit_exceeded": (False, None, None, None, None, None),
        "unsafe_decoder_warning": (True, 1, 1280, 960, "RGB", True),
        "unsupported_frame_count": (True, 2, 1280, 960, "RGB", True),
        "non_opaque": (True, 1, 1280, 960, "RGBA", False),
        # RGB is a channel-mode observation, not proof that an ICC/profile contract is supported.
        "unsupported_color_contract": (True, 1, 1280, 960, "RGB", True),
    }
    for reason, (
        container_decoded,
        frame_count,
        output_width,
        output_height,
        output_mode,
        opaque,
    ) in structural_failures.items():
        document = copy.deepcopy(structural_pass)
        document["authority"]["output_raster_sha256"] = None
        document["result"] = {
            "state": "fail",
            "reason": reason,
            "measurements": {
                "source_width": 1280,
                "source_height": 960,
                "container_decoded": container_decoded,
                "canonical_raster_compiled": False,
                "frame_count": frame_count,
                "output_width": output_width,
                "output_height": output_height,
                "output_mode": output_mode,
                "opaque": opaque,
            },
        }
        _refresh_machine_identity(document)
        variants[f"constraint_structural_{reason}"] = document

    not_run = copy.deepcopy(documents["constraint_verification"])
    not_run["result"] = {
        "state": "not_run",
        "reason": "structural_verification_failed",
    }
    not_run["authority"] = {
        "schema_version": "moodboard.verifier.outside-mask-rgb-exact.v1",
        "input_digest": _digest("2"),
        "source_raster_sha256": _digest("4"),
        "mask_sha256": _digest("6"),
        "blocking_structural_evidence_id": structural_fail["evidence_id"],
    }
    _refresh_machine_identity(not_run)
    variants["constraint_not_run"] = not_run

    for choice, reason in (
        ("right", "palette"),
        ("tie", "equally_good"),
        ("abstain", "insufficient_context"),
    ):
        document = copy.deepcopy(documents["human_comparison"])
        document["result"] = {
            "state": "recorded",
            "choice": choice,
            "reason_code": reason,
            "response_ms": 800,
        }
        variants[f"human_{choice}"] = document

    unannotated = copy.deepcopy(documents["human_comparison"])
    unannotated["result"] = {"state": "recorded", "choice": "left"}
    variants["human_left_unannotated"] = unannotated

    unavailable = copy.deepcopy(documents["preference_prediction"])
    unavailable["result"] = {"state": "unavailable", "refusal": "no_active_snapshot"}
    authority = unavailable["authority"]
    unavailable["authority"] = {
        "schema_version": "moodboard.preference-availability.v1",
        "input_digest": authority["input_digest"],
        "source_report_sha256": authority["source_report_sha256"],
        "principal_id": authority["principal_id"],
        "evidence_class": authority["evidence_class"],
        "scope": authority["scope"],
    }
    _refresh_machine_identity(unavailable)
    variants["prediction_unavailable"] = unavailable
    return variants


def _schema_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def _refresh_machine_identity(document: JsonObject) -> None:
    if document["kind"] == "human_comparison":
        return
    document["evidence_id"] = compute_document_identity(
        document,
        schema_version="moodboard.judgment.v1",
        identity_field="evidence_id",
    )


def _object_paths(value: Any, path: JsonPath = ()) -> list[JsonPath]:
    paths: list[JsonPath] = []
    if isinstance(value, dict):
        paths.append(path)
        for key, nested in value.items():
            paths.extend(_object_paths(nested, (*path, key)))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            paths.extend(_object_paths(nested, (*path, index)))
    return paths


def _at_path(document: JsonObject, path: JsonPath) -> Any:
    value: Any = document
    for segment in path:
        value = value[segment]
    return value


def test_schema_is_closed_draft_2020_12_and_exposes_the_stable_version() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert SCHEMA_VERSION == "moodboard.judgment.v1"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("kind", tuple(_valid_documents()))
def test_each_kind_has_exact_root_keys_and_round_trips_to_an_immutable_typed_branch(
    kind: str,
) -> None:
    document = _valid_documents()[kind]

    assert frozenset(document) == _ROOT_KEYS
    assert validate_judgment(document) is None
    _schema_validator().validate(document)

    value = from_json_dict(document)
    assert is_dataclass(value)
    assert value.kind == kind
    assert to_json_dict(value) == document
    with pytest.raises(FrozenInstanceError):
        value.kind = "tampered"  # type: ignore[misc]

    emitted = to_json_dict(value)
    emitted["kind"] = "tampered"
    assert to_json_dict(value) == document


def test_the_six_parser_results_are_distinct_typed_branches() -> None:
    values = [from_json_dict(document) for document in _valid_documents().values()]

    assert len({type(value) for value in values}) == 6


@pytest.mark.parametrize("kind", tuple(_valid_documents()))
def test_kind_subject_result_and_authority_cannot_be_cross_wired(kind: str) -> None:
    documents = _valid_documents()
    names = tuple(documents)
    other = documents[names[(names.index(kind) + 1) % len(names)]]

    mutations = [
        lambda value: value.__setitem__("kind", other["kind"]),
        lambda value: value.__setitem__("result", copy.deepcopy(other["result"])),
        lambda value: value.__setitem__("authority", copy.deepcopy(other["authority"])),
    ]
    if documents[kind]["subject"]["kind"] != other["subject"]["kind"]:
        mutations.append(
            lambda value: value.__setitem__("subject", copy.deepcopy(other["subject"]))
        )
    for mutate in mutations:
        tampered = copy.deepcopy(documents[kind])
        mutate(tampered)
        _refresh_machine_identity(tampered)

        with pytest.raises(JudgmentError):
            validate_judgment(tampered)
        with pytest.raises(JudgmentError):
            from_json_dict(tampered)


@pytest.mark.parametrize("kind", tuple(_valid_documents()))
def test_every_object_is_closed_at_every_nesting_level(kind: str) -> None:
    document = _valid_documents()[kind]
    validator = _schema_validator()

    for path in _object_paths(document):
        tampered = copy.deepcopy(document)
        target = _at_path(tampered, path)
        target["unexpected"] = "must fail closed"
        _refresh_machine_identity(tampered)

        assert not validator.is_valid(tampered), f"schema left object {path!r} open"
        with pytest.raises(JudgmentError):
            validate_judgment(tampered)


@pytest.mark.parametrize(
    "kind",
    (
        "intent_eligibility",
        "source_similarity",
        "board_compatibility",
        "constraint_verification",
        "preference_prediction",
    ),
)
def test_machine_judgment_identity_covers_the_complete_document(kind: str) -> None:
    document = _valid_documents()[kind]
    expected = compute_document_identity(
        document,
        schema_version="moodboard.judgment.v1",
        identity_field="evidence_id",
    )
    assert document["evidence_id"] == expected

    tampered = copy.deepcopy(document)
    evidence_ref = tampered["evidence_ref"]
    digest_field = "artifact_id" if evidence_ref["kind"] == "artifact" else "content_ref"
    evidence_ref[digest_field] = _digest("0")
    assert _schema_validator().is_valid(tampered)

    with pytest.raises(JudgmentError):
        validate_judgment(tampered)
    with pytest.raises(JudgmentError):
        from_json_dict(tampered)


def test_human_judgment_uses_its_canonical_authority_uuid_not_a_document_digest() -> None:
    document = _valid_documents()["human_comparison"]

    assert document["evidence_id"] == _JUDGMENT_ID
    assert document["authority"]["judgment_id"] == _JUDGMENT_ID
    validate_judgment(document)

    for field in ("evidence_id", "authority.judgment_id"):
        tampered = copy.deepcopy(document)
        if field == "evidence_id":
            tampered["evidence_id"] = "00000000-0000-4000-8000-000000000009"
        else:
            tampered["authority"]["judgment_id"] = (
                "00000000-0000-4000-8000-000000000009"
            )
        with pytest.raises(JudgmentError):
            validate_judgment(tampered)


def test_source_similarity_preserves_routed_order_and_full_float_values() -> None:
    document = _valid_documents()["source_similarity"]
    emitted = to_json_dict(from_json_dict(document))
    rows = emitted["result"]["rows"]

    assert [row["asset_id"] for row in rows] == [_ASSET_B, _ASSET_A]
    assert [row["routed_rank"] for row in rows] == [1, 2]
    assert [row["source_search_rank"] for row in rows] == [4, 7]
    assert [row["source_similarity"] for row in rows] == [
        0.8432995826005936,
        0.7482006549835205,
    ]
    encoded = json.dumps(emitted, sort_keys=True, separators=(",", ":"))
    assert "0.8432995826005936" in encoded
    assert "0.7482006549835205" in encoded


def test_schema_rejects_combined_taste_score_and_boolean_numeric_fields() -> None:
    board = _valid_documents()["board_compatibility"]
    board["result"]["taste_score"] = 0.9
    _refresh_machine_identity(board)
    with pytest.raises(JudgmentError):
        validate_judgment(board)

    similarity = _valid_documents()["source_similarity"]
    similarity["result"]["rows"][0]["routed_rank"] = True
    _refresh_machine_identity(similarity)
    with pytest.raises(JudgmentError):
        validate_judgment(similarity)


def test_source_similarity_cross_fields_pin_stable_filter_order() -> None:
    for field, value in (("routed_rank", 3), ("source_search_rank", 3)):
        document = _valid_documents()["source_similarity"]
        document["result"]["rows"][1][field] = value
        _refresh_machine_identity(document)
        assert _schema_validator().is_valid(document)
        with pytest.raises(JudgmentError):
            validate_judgment(document)


def test_unknown_authority_revision_fails_closed() -> None:
    document = _valid_documents()["constraint_verification"]
    document["authority"]["schema_version"] = "moodboard.verifier.unknown.v1"
    _refresh_machine_identity(document)
    with pytest.raises(JudgmentError):
        validate_judgment(document)


@pytest.mark.parametrize("case", tuple(_valid_state_documents()))
def test_every_registered_non_success_state_and_human_choice_is_closed_and_round_trips(
    case: str,
) -> None:
    document = _valid_state_documents()[case]

    validate_judgment(document)
    assert to_json_dict(from_json_dict(document)) == document
    for path in _object_paths(document):
        tampered = copy.deepcopy(document)
        _at_path(tampered, path)["unexpected"] = "closed means closed"
        _refresh_machine_identity(tampered)
        with pytest.raises(JudgmentError):
            validate_judgment(tampered)


def test_registered_state_coverage_is_exact() -> None:
    documents = {**_valid_documents(), **_valid_state_documents()}
    states: dict[str, set[str]] = {}
    choices: set[str] = set()
    for document in documents.values():
        states.setdefault(document["kind"], set()).add(document["result"]["state"])
        if document["kind"] == "human_comparison":
            choices.add(document["result"]["choice"])

    assert states == {
        "intent_eligibility": {"eligible", "excluded", "not_computed"},
        "source_similarity": {"computed", "empty", "not_computed", "refused"},
        "board_compatibility": {"scored", "abstained", "not_computed"},
        "constraint_verification": {"pass", "fail", "not_run"},
        "human_comparison": {"recorded"},
        "preference_prediction": {"predicted", "unavailable"},
    }
    assert choices == {"left", "right", "tie", "abstain"}


@pytest.mark.parametrize(
    ("kind", "path", "unknown"),
    (
        ("intent_eligibility", ("result", "state"), "allowed"),
        ("source_similarity", ("result", "state"), "ranked"),
        ("board_compatibility", ("result", "state"), "failed"),
        ("constraint_verification", ("result", "state"), "skipped"),
        ("human_comparison", ("result", "choice"), "none"),
        ("preference_prediction", ("result", "state"), "ranked"),
    ),
)
def test_unknown_result_states_and_choices_fail_closed(
    kind: str, path: JsonPath, unknown: str
) -> None:
    document = _valid_documents()[kind]
    _at_path(document, path[:-1])[path[-1]] = unknown
    _refresh_machine_identity(document)

    with pytest.raises(JudgmentError):
        validate_judgment(document)


@pytest.mark.parametrize("kind", tuple(_valid_documents()))
def test_unknown_schema_kind_subject_and_authority_tokens_fail_closed(kind: str) -> None:
    original = _valid_documents()[kind]
    mutations = (
        (("schema_version",), "moodboard.judgment.v2"),
        (("kind",), "taste_score"),
        (("subject", "kind"), "unknown_subject"),
        (("authority", "schema_version"), "moodboard.unknown.v1"),
    )
    for path, value in mutations:
        document = copy.deepcopy(original)
        _at_path(document, path[:-1])[path[-1]] = value
        if path != ("schema_version",):
            _refresh_machine_identity(document)
        with pytest.raises(JudgmentError):
            validate_judgment(document)


@pytest.mark.parametrize(
    ("state", "protected", "changed", "maximum"),
    (
        ("pass", 100, 1, 1),
        ("fail", 100, 0, 1),
        ("fail", 100, 1, 0),
        ("fail", 100, 101, 1),
        ("fail", 0, 1, 1),
    ),
)
def test_exact_locality_rejects_impossible_measurement_triples(
    state: str, protected: int, changed: int, maximum: int
) -> None:
    document = _valid_documents()["constraint_verification"]
    document["result"] = {
        "state": state,
        "measurements": {
            "protected_pixel_count": protected,
            "changed_pixel_count": changed,
            "max_abs_channel_error": maximum,
        },
    }
    _refresh_machine_identity(document)

    with pytest.raises(JudgmentError):
        validate_judgment(document)


def test_structural_failure_is_the_exact_authority_for_locality_not_run() -> None:
    documents = _valid_state_documents()
    structural = documents["constraint_structural_fail"]
    locality = documents["constraint_not_run"]

    validate_locality_blocking_pair(structural, locality)
    locality["authority"]["blocking_structural_evidence_id"] = _digest("0")
    _refresh_machine_identity(locality)
    with pytest.raises(JudgmentError):
        validate_locality_blocking_pair(structural, locality)


def test_structural_verifier_state_matches_its_measured_raster() -> None:
    passed = _valid_state_documents()["constraint_structural_pass"]
    passed["result"]["measurements"]["output_width"] = 1184
    _refresh_machine_identity(passed)
    with pytest.raises(JudgmentError):
        validate_judgment(passed)

    failed = _valid_state_documents()["constraint_structural_fail"]
    failed["result"]["measurements"]["output_width"] = 1280
    failed["result"]["measurements"]["output_height"] = 960
    _refresh_machine_identity(failed)
    with pytest.raises(JudgmentError):
        validate_judgment(failed)


@pytest.mark.parametrize(
    "case",
    (
        "constraint_structural_unsafe_decoder_warning",
        "constraint_structural_unsupported_frame_count",
        "constraint_structural_non_opaque",
        "constraint_structural_unsupported_color_contract",
    ),
)
def test_inspected_structural_failures_cannot_claim_a_canonical_output_raster(
    case: str,
) -> None:
    document = _valid_state_documents()[case]
    document["result"]["measurements"]["canonical_raster_compiled"] = True
    document["authority"]["output_raster_sha256"] = _digest("9")
    _refresh_machine_identity(document)

    with pytest.raises(JudgmentError):
        validate_judgment(document)


def test_dimension_mismatch_requires_a_canonical_comparable_output_raster() -> None:
    document = _valid_state_documents()["constraint_structural_fail"]
    document["result"]["measurements"]["canonical_raster_compiled"] = False
    document["authority"]["output_raster_sha256"] = None
    _refresh_machine_identity(document)

    with pytest.raises(JudgmentError):
        validate_judgment(document)


@pytest.mark.parametrize("case", ("decode_failed", "decode_limit_exceeded"))
def test_undecodable_structural_failures_cannot_claim_inspection_measurements(
    case: str,
) -> None:
    document = _valid_state_documents()[f"constraint_structural_{case}"]
    measurements = document["result"]["measurements"]
    measurements.update(
        {
            "container_decoded": True,
            "frame_count": 1,
            "output_width": 1280,
            "output_height": 960,
            "output_mode": "RGB",
            "opaque": True,
        }
    )
    _refresh_machine_identity(document)

    with pytest.raises(JudgmentError):
        validate_judgment(document)


@pytest.mark.parametrize(
    ("case", "field", "value"),
    (
        ("board_resolution", "category_id", "different-look"),
        ("board_resolution", "supported_alpha", 0.5),
        ("board_resolution", "requested_alpha", 0.2),
        ("board_far_outlier", "candidate_alpha", 0.9),
    ),
)
def test_board_abstention_measurements_retain_the_report_invariants(
    case: str, field: str, value: str | float
) -> None:
    document = _valid_state_documents()[case]
    document["result"]["measurement"][field] = value
    _refresh_machine_identity(document)

    with pytest.raises(JudgmentError):
        validate_judgment(document)


def test_board_judgment_accepts_exact_measurements_from_both_real_abstention_producers() -> None:
    partition = CategoryPartition(
        category_id="look-01",
        candidate_category_members=tuple(range(8)),
        all_categories={"look-01": tuple(range(8))},
    )
    resolution = check_resolution(
        partition,
        0.05,
        ((0, 1), (2, 3), (4, 5), (6, 7)),
    )
    assert resolution is not None

    outlier = check_far_outlier(
        1.2,
        (0.5, 0.6, 0.7, 0.8),
        far_outlier_iqr_multiplier=1.5,
        far_outlier_iqr_multiplier_source="board.fit.far_outlier_iqr_multiplier",
    )
    assert outlier is not None

    for verdict in (resolution, outlier):
        document = _valid_documents()["board_compatibility"]
        document["result"] = {
            "state": "abstained",
            "category_id": "look-01",
            "reason": verdict.reason,
            "measurement": dict(verdict.measurement),
            "explanation": verdict.explanation,
        }
        _refresh_machine_identity(document)
        validate_judgment(document)


@pytest.mark.parametrize(
    "kind", ("source_similarity", "human_comparison", "preference_prediction")
)
def test_descriptor_model_key_must_bind_the_exact_fingerprint(kind: str) -> None:
    document = _valid_documents()[kind]
    scope = document["authority"] if kind == "source_similarity" else document["authority"]["scope"]
    scope["descriptor_fingerprint"] = _digest("6")
    _refresh_machine_identity(document)

    with pytest.raises(JudgmentError):
        validate_judgment(document)


def test_human_comparison_bounds_metadata_and_remains_blind_and_pair_bound() -> None:
    too_slow = _valid_documents()["human_comparison"]
    too_slow["result"]["response_ms"] = 3_600_001
    with pytest.raises(JudgmentError):
        validate_judgment(too_slow)

    exposed = _valid_documents()["human_comparison"]
    exposed["authority"]["presentation"]["source_rank_shown"] = True
    with pytest.raises(JudgmentError):
        validate_judgment(exposed)

    same_side = _valid_documents()["human_comparison"]
    same_side["subject"]["right_output_occurrence_id"] = same_side["subject"][
        "left_output_occurrence_id"
    ]
    with pytest.raises(JudgmentError):
        validate_judgment(same_side)

    incompatible_reason = _valid_documents()["human_comparison"]
    incompatible_reason["result"]["choice"] = "tie"
    with pytest.raises(JudgmentError):
        validate_judgment(incompatible_reason)


def test_source_board_and_prediction_cross_field_invariants_fail_closed() -> None:
    intent = _valid_documents()["intent_eligibility"]
    intent["result"]["collection"] = "different-collection"
    _refresh_machine_identity(intent)
    with pytest.raises(JudgmentError):
        validate_judgment(intent)

    duplicate_source = _valid_documents()["source_similarity"]
    duplicate_source["result"]["rows"][1]["content_ref"] = duplicate_source["result"][
        "rows"
    ][0]["content_ref"]
    _refresh_machine_identity(duplicate_source)
    with pytest.raises(JudgmentError):
        validate_judgment(duplicate_source)

    board_digest = _valid_documents()["board_compatibility"]
    board_digest["authority"]["input_digest"] = _digest("0")
    _refresh_machine_identity(board_digest)
    with pytest.raises(JudgmentError):
        validate_judgment(board_digest)

    interval = _valid_documents()["board_compatibility"]
    interval["result"]["interval"]["low"] = 1.0
    interval["result"]["interval"]["high"] = 0.5
    _refresh_machine_identity(interval)
    with pytest.raises(JudgmentError):
        validate_judgment(interval)

    prediction = _valid_documents()["preference_prediction"]
    prediction["result"]["probability_right_given_decisive"] = 0.3
    _refresh_machine_identity(prediction)
    with pytest.raises(JudgmentError):
        validate_judgment(prediction)


def test_source_similarity_rejects_score_inversion_but_accepts_ties() -> None:
    inverted = _valid_documents()["source_similarity"]
    inverted["result"]["rows"][1]["source_similarity"] = 0.9
    _refresh_machine_identity(inverted)
    with pytest.raises(JudgmentError):
        validate_judgment(inverted)

    tied = _valid_documents()["source_similarity"]
    tied["result"]["rows"][1]["source_similarity"] = tied["result"]["rows"][0][
        "source_similarity"
    ]
    _refresh_machine_identity(tied)
    validate_judgment(tied)


def test_forged_typed_values_cannot_emit_wire_documents() -> None:
    value = from_json_dict(_valid_documents()["board_compatibility"])
    with pytest.raises(JudgmentError):
        to_json_dict(replace(value, kind="human_comparison"))
    with pytest.raises(JudgmentError):
        to_json_dict(replace(value, result={"state": "scored", "taste_score": 1.0}))


def test_nested_typed_values_are_immutable_and_detached_from_the_input() -> None:
    document = _valid_documents()["source_similarity"]
    value = from_json_dict(document)
    document["result"]["rows"][0]["source_similarity"] = -1.0

    assert to_json_dict(value)["result"]["rows"][0]["source_similarity"] == 0.8432995826005936
    with pytest.raises(TypeError):
        value.result["state"] = "empty"  # type: ignore[index]


def test_non_i_json_and_recursive_inputs_fail_as_judgment_errors() -> None:
    non_finite = _valid_documents()["source_similarity"]
    non_finite["result"]["rows"][0]["source_similarity"] = float("nan")
    with pytest.raises(JudgmentError):
        validate_judgment(non_finite)

    recursive = _valid_documents()["intent_eligibility"]
    recursive["subject"]["cycle"] = recursive
    with pytest.raises(JudgmentError):
        validate_judgment(recursive)

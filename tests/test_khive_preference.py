from __future__ import annotations

import stat
import uuid
from pathlib import Path

import pytest

from moodboard.khive import (
    KhiveClient,
    KhiveJudgmentRequest,
    KhivePreferenceRequest,
    KhiveProtocolError,
    KhiveServeRequest,
)
from moodboard.preference import (
    FEATURE_PRODUCER_ID,
    FEATURE_PRODUCER_REVISION,
    FEATURE_SCHEMA_ID,
)

FAKE_PREFERENCE_KKERNEL = r"""#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sys


def value(flag):
    return sys.argv[sys.argv.index(flag) + 1]


if "--serial" not in sys.argv:
    raise SystemExit(92)
if "--strict" not in sys.argv:
    raise SystemExit(93)
if value("--presentation") != "verbose":
    raise SystemExit(95)
if value("--output-format") != "json":
    raise SystemExit(97)


ops = [json.loads(line) for line in pathlib.Path(value("--ops-file")).read_text().splitlines()]
save_path = pathlib.Path(value("--save-file"))
rows = []
for operation_index, operation in enumerate(ops, start=1):
    tool = operation["tool"]
    args = operation["args"]
    identity_base = 100 + (operation_index - 1) * 10
    if args.get("namespace") != value("--namespace"):
        raise SystemExit(91)
    if "presentation" in args or "presentation_per_op" in args:
        raise SystemExit(96)
    if tool == "create":
        expected_properties = {
            "schema_version": "moodboard.preference-board.v1",
            "board_id": "a" * 64,
            "model_key": "moodboard_" + "b" * 64 + "_1024",
            "descriptor_fingerprint": "b" * 64,
            "source_report_sha256": "c" * 64,
            "feature_schema_id": "f691fc73bf9a50d72157e21601fa579caa707bf2c448df546c63e915b4e42175",
            "feature_producer_revision": "moodboard.preference-producer.v1",
            "feature_producer_id": (
                "3fd22977f9f3686429cdb6569580b70573396efe0562095f43ed44e0a0ff3f22"
            ),
        }
        if args != {
            "kind": "entity",
            "entity_kind": "artifact",
            "entity_type": "moodboard",
            "name": "Adobe lemon study",
            "description": "Immutable Moodboard preference-learning scope",
            "properties": expected_properties,
            "tags": ["moodboard", "preference-learning"],
            "skip_dedup_check": True,
            "namespace": value("--namespace"),
        }:
            raise SystemExit(93)
        result = {
            "id": "00000000-0000-4000-8000-000000000010",
            "namespace": args["namespace"],
            "created_at": "2026-08-12T16:00:00+00:00",
            "updated_at": "2026-08-12T16:00:00+00:00",
            "kind": "artifact",
            "entity_type": "moodboard",
            "name": args["name"],
            "description": args["description"],
            "properties": args["properties"],
            "tags": args["tags"],
            "deleted_at": None,
            "merged_into": None,
            "merge_event_id": None,
            "content_ref": None,
        }
    elif tool == "moodboard.serve":
        if args.get("exposure") != {
            "preference_probability_shown": False,
            "source_rank_shown": True,
        }:
            raise SystemExit(94)
        result = {
            "schema_version": "moodboard.preference-serve.v1",
            "serve_id": f"00000000-0000-4000-8000-{identity_base + 1:012d}",
            "scope": {
                "namespace": args["namespace"],
                "actor_kind": "lambda",
                "actor_id": "adobe-demo",
                "board_entity_id": args["board_entity_id"],
                "board_id": args["board_id"],
                "model_key": args["descriptor"]["model_key"],
                "descriptor_fingerprint": args["descriptor"]["descriptor_fingerprint"],
                "feature_schema_id": args["feature_schema_id"],
            },
            "feature_schema": {
                "schema_version": "moodboard.preference-features.v1",
                "feature_schema_id": args["feature_schema_id"],
                "dtype": "float32",
                "bounds": [0.0, 1.0],
                "pair_transform": "left_minus_right",
                "features": [
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
                ],
            },
            "left": {
                "result_occurrence_id": f"00000000-0000-4000-8000-{identity_base + 2:012d}",
                "asset_id": args["candidates"][0]["asset_id"],
                "content_ref": args["candidates"][0]["content_ref"],
                "source_rank": args["candidates"][0]["source_rank"],
            },
            "right": {
                "result_occurrence_id": f"00000000-0000-4000-8000-{identity_base + 3:012d}",
                "asset_id": args["candidates"][1]["asset_id"],
                "content_ref": args["candidates"][1]["content_ref"],
                "source_rank": args["candidates"][1]["source_rank"],
            },
            "randomization": {
                "revision": "moodboard-side-v1",
                "sha256": "a" * 64,
                "swap_applied": False,
            },
            "experimental": True,
        }
    elif tool == "moodboard.judge":
        result = {
            "schema_version": "moodboard.preference-judgment.v1",
            "judgment_id": f"00000000-0000-5000-8000-{identity_base + 4:012d}",
            "serve_id": args["serve_id"],
            "choice": args["choice"],
            "reason_code": args.get("reason_code"),
            "created": True,
            "experimental": True,
        }
    elif tool == "moodboard.train_preference":
        result = {
            "schema_version": "moodboard.preference-model.v1",
            "preference_model_id": "00000000-0000-4000-8000-000000000105",
            "content_ref": "b" * 64,
            "model_fingerprint": "c" * 64,
            "network_content_ref": "d" * 64,
            "network_sha256": "e" * 64,
            "created": True,
            "scope": {
                "namespace": args["namespace"],
                "actor_kind": "lambda",
                "actor_id": "adobe-demo",
                "board_entity_id": args["board_entity_id"],
                "board_id": args["board_id"],
                "model_key": args["descriptor"]["model_key"],
                "descriptor_fingerprint": args["descriptor"]["descriptor_fingerprint"],
                "feature_schema_id": args["feature_schema_id"],
            },
            "training": {"snapshot_sha256": "f" * 64},
            "calibration": {"temperature": 1.25, "tie_band_half_width": 0.08},
            "test_metrics": {"accuracy": 0.8125, "brier": 0.19, "log_loss": 0.55},
            "fann_inference_verified": True,
            "experimental": True,
        }
    elif tool == "moodboard.preference":
        result = {
            "schema_version": "moodboard.preference.v1",
            "prediction_kind": "learned_pairwise_preference",
            "conditional_on": "decisive_judgment",
            "probability_left_given_decisive": 0.75,
            "probability_right_given_decisive": 0.25,
            "raw_fann_logit": 0.8,
            "calibrated_temperature": 1.25,
            "indifference": {
                "state": "outside_calibrated_band",
                "probability_margin_from_half": 0.25,
                "calibrated_half_width": 0.08,
            },
            "conformal_evidence": {
                "state": "not_computed_by_this_verb",
                "note": "learned preference is not a conformal p-value or coherence statistic",
            },
            "preference_model_id": args["preference_model_id"],
            "model_content_ref": "b" * 64,
            "model_fingerprint": "c" * 64,
            "source_report_sha256": args["source_report_sha256"],
            "scope": {
                "namespace": args["namespace"],
                "actor_kind": "lambda",
                "actor_id": "adobe-demo",
                "board_entity_id": args["board_entity_id"],
                "board_id": args["board_id"],
                "model_key": args["descriptor"]["model_key"],
                "descriptor_fingerprint": args["descriptor"]["descriptor_fingerprint"],
                "feature_schema_id": args["feature_schema_id"],
            },
            "left": {
                "asset_id": args["left"]["asset_id"],
                "content_ref": args["left"]["content_ref"],
            },
            "right": {
                "asset_id": args["right"]["asset_id"],
                "content_ref": args["right"]["content_ref"],
            },
            "experimental": True,
        }
    else:
        raise SystemExit(92)
    rows.append({"ok": True, "result": result, "tool": tool, "usage": {}})

payload = b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode() for row in rows)
save_path.write_bytes(payload)
print(json.dumps({
    "path": str(save_path.resolve()),
    "rows": len(rows),
    "checksum": hashlib.sha256(payload).hexdigest(),
    "summary": {"total": len(rows), "succeeded": len(rows), "failed": 0, "aborted": 0},
}, separators=(",", ":")))
"""


@pytest.fixture
def preference_client(tmp_path: Path) -> tuple[KhiveClient, Path]:
    executable = tmp_path / "kkernel-preference-fake"
    executable.write_text(FAKE_PREFERENCE_KKERNEL, encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return (
        KhiveClient(
            executable=executable,
            actor="lambda:adobe-demo",
            namespace="adobe-demo",
        ),
        executable,
    )


def _candidate(index: int) -> dict[str, object]:
    return {
        "state": "scored",
        "asset_id": str(uuid.UUID(int=index)),
        "content_ref": f"{index:x}" * 64,
        "source_rank": index,
        "features": [float(index) / 10.0] * 10,
    }


def _scope() -> dict[str, str]:
    return {
        "board_entity_id": "00000000-0000-4000-8000-000000000010",
        "board_id": "a" * 64,
        "model_key": "moodboard_" + "b" * 64 + "_1024",
        "descriptor_fingerprint": "b" * 64,
        "source_report_sha256": "c" * 64,
    }


def test_publish_board_creates_exact_identity_bound_artifact(preference_client) -> None:
    client, _ = preference_client
    entity = client.publish_board(
        name="Adobe lemon study",
        board_id="a" * 64,
        model_key="moodboard_" + "b" * 64 + "_1024",
        descriptor_fingerprint="b" * 64,
        source_report_sha256="c" * 64,
    )

    assert entity.entity_id == "00000000-0000-4000-8000-000000000010"
    assert entity.namespace == "adobe-demo"
    assert entity.board_id == "a" * 64
    assert entity.feature_schema_id == FEATURE_SCHEMA_ID
    assert entity.feature_producer_revision == FEATURE_PRODUCER_REVISION
    assert entity.feature_producer_id == FEATURE_PRODUCER_ID


def test_publish_board_rejects_invalid_scope_before_process(preference_client) -> None:
    client, executable = preference_client
    before = executable.stat().st_atime_ns

    with pytest.raises(ValueError, match="model_key"):
        client.publish_board(
            name="Adobe lemon study",
            board_id="a" * 64,
            model_key="moodboard_bad_1024",
            descriptor_fingerprint="b" * 64,
            source_report_sha256="c" * 64,
        )

    assert executable.stat().st_atime_ns == before


def test_publish_board_parser_rejects_property_drift(monkeypatch, preference_client) -> None:
    client, _ = preference_client
    original = client._execute

    def broken(operations):
        result = original(operations)[0]
        result["properties"]["feature_producer_id"] = "0" * 64
        return (result,)

    monkeypatch.setattr(client, "_execute", broken)
    with pytest.raises(KhiveProtocolError, match="properties"):
        client.publish_board(
            name="Adobe lemon study",
            board_id="a" * 64,
            model_key="moodboard_" + "b" * 64 + "_1024",
            descriptor_fingerprint="b" * 64,
            source_report_sha256="c" * 64,
        )


def test_publish_board_parser_rejects_non_null_entity_lifecycle_fields(
    monkeypatch, preference_client
) -> None:
    client, _ = preference_client
    original = client._execute

    def broken(operations):
        result = original(operations)[0]
        result["content_ref"] = "d" * 64
        return (result,)

    monkeypatch.setattr(client, "_execute", broken)
    with pytest.raises(KhiveProtocolError, match="lifecycle"):
        client.publish_board(
            name="Adobe lemon study",
            board_id="a" * 64,
            model_key="moodboard_" + "b" * 64 + "_1024",
            descriptor_fingerprint="b" * 64,
            source_report_sha256="c" * 64,
        )


def test_preference_client_typed_full_loop(preference_client) -> None:
    client, _ = preference_client
    scope = _scope()
    served = client.serve(
        board_entity_id=scope["board_entity_id"],
        board_id=scope["board_id"],
        model_key=scope["model_key"],
        descriptor_fingerprint=scope["descriptor_fingerprint"],
        source_report_sha256=scope["source_report_sha256"],
        candidates=(_candidate(1), _candidate(2)),
        candidate_pool_sha256="d" * 64,
    )
    assert served.serve_id == "00000000-0000-4000-8000-000000000101"
    assert served.left.asset_id == _candidate(1)["asset_id"]
    assert served.feature_schema_id == FEATURE_SCHEMA_ID

    judged = client.judge(
        serve_id=served.serve_id,
        left_result_occurrence_id=served.left.result_occurrence_id,
        right_result_occurrence_id=served.right.result_occurrence_id,
        choice="left",
        reason_code="style",
        response_ms=1250,
    )
    assert judged.choice == "left" and judged.created is True

    trained = client.train_preference(
        board_entity_id=scope["board_entity_id"],
        board_id=scope["board_id"],
        model_key=scope["model_key"],
        descriptor_fingerprint=scope["descriptor_fingerprint"],
    )
    assert trained.preference_model_id == "00000000-0000-4000-8000-000000000105"
    assert trained.fann_inference_verified is True

    prediction = client.preference(
        preference_model_id=trained.preference_model_id,
        board_entity_id=scope["board_entity_id"],
        board_id=scope["board_id"],
        model_key=scope["model_key"],
        descriptor_fingerprint=scope["descriptor_fingerprint"],
        source_report_sha256=scope["source_report_sha256"],
        left=_candidate(1),
        right=_candidate(2),
    )
    assert prediction.probability_left_given_decisive == 0.75
    assert prediction.probability_right_given_decisive == 0.25
    assert prediction.conformal_state == "not_computed_by_this_verb"


def test_preference_batch_methods_use_one_ordered_process_per_batch(
    monkeypatch, preference_client
) -> None:
    client, _ = preference_client
    scope = _scope()
    execute_calls: list[tuple[str, ...]] = []
    original = client._execute

    def counted(operations):
        execute_calls.append(tuple(operation.tool for operation in operations))
        return original(operations)

    monkeypatch.setattr(client, "_execute", counted)
    served = client.batch_serve(
        board_entity_id=scope["board_entity_id"],
        board_id=scope["board_id"],
        model_key=scope["model_key"],
        descriptor_fingerprint=scope["descriptor_fingerprint"],
        source_report_sha256=scope["source_report_sha256"],
        requests=(
            KhiveServeRequest(
                candidates=(_candidate(1), _candidate(2)),
                candidate_pool_sha256="d" * 64,
                policy_revision="policy-a/first",
            ),
            KhiveServeRequest(
                candidates=(_candidate(3), _candidate(4)),
                candidate_pool_sha256="d" * 64,
                policy_revision="policy-a/second",
            ),
        ),
    )
    assert [row.left.asset_id for row in served] == [
        _candidate(1)["asset_id"],
        _candidate(3)["asset_id"],
    ]
    assert len({row.serve_id for row in served}) == 2
    assert (
        len(
            {
                occurrence.result_occurrence_id
                for row in served
                for occurrence in (row.left, row.right)
            }
        )
        == 4
    )

    judged = client.batch_judge(
        requests=tuple(
            KhiveJudgmentRequest(
                serve_id=row.serve_id,
                left_result_occurrence_id=row.left.result_occurrence_id,
                right_result_occurrence_id=row.right.result_occurrence_id,
                choice="left",
                reason_code="other",
            )
            for row in served
        )
    )
    assert [row.serve_id for row in judged] == [row.serve_id for row in served]

    predicted = client.batch_preference(
        preference_model_id="00000000-0000-4000-8000-000000000105",
        board_entity_id=scope["board_entity_id"],
        board_id=scope["board_id"],
        model_key=scope["model_key"],
        descriptor_fingerprint=scope["descriptor_fingerprint"],
        source_report_sha256=scope["source_report_sha256"],
        requests=(
            KhivePreferenceRequest(left=_candidate(1), right=_candidate(2)),
            KhivePreferenceRequest(left=_candidate(3), right=_candidate(4)),
        ),
    )
    assert [row.probability_left_given_decisive for row in predicted] == [0.75, 0.75]
    assert execute_calls == [
        ("moodboard.serve", "moodboard.serve"),
        ("moodboard.judge", "moodboard.judge"),
        ("moodboard.preference", "moodboard.preference"),
    ]


def test_preference_batch_validates_every_item_before_process(preference_client) -> None:
    client, executable = preference_client
    before = executable.stat().st_atime_ns
    scope = _scope()

    with pytest.raises(ValueError, match="candidates"):
        client.batch_serve(
            board_entity_id=scope["board_entity_id"],
            board_id=scope["board_id"],
            model_key=scope["model_key"],
            descriptor_fingerprint=scope["descriptor_fingerprint"],
            source_report_sha256=scope["source_report_sha256"],
            requests=(
                KhiveServeRequest(
                    candidates=(_candidate(1), _candidate(2)),
                    candidate_pool_sha256="d" * 64,
                ),
                KhiveServeRequest(
                    candidates=(_candidate(3),),
                    candidate_pool_sha256="d" * 64,
                ),
            ),
        )

    assert executable.stat().st_atime_ns == before


def test_preference_batches_reject_empty_before_process(monkeypatch, preference_client) -> None:
    client, _ = preference_client
    scope = _scope()
    execute_calls = 0

    def unexpected_execute(_operations):
        nonlocal execute_calls
        execute_calls += 1
        raise AssertionError("invalid batch reached the process boundary")

    monkeypatch.setattr(client, "_execute", unexpected_execute)
    common = {
        "board_entity_id": scope["board_entity_id"],
        "board_id": scope["board_id"],
        "model_key": scope["model_key"],
        "descriptor_fingerprint": scope["descriptor_fingerprint"],
        "source_report_sha256": scope["source_report_sha256"],
    }
    with pytest.raises(ValueError, match="must not be empty"):
        client.batch_serve(**common, requests=())
    with pytest.raises(ValueError, match="must not be empty"):
        client.batch_judge(requests=())
    with pytest.raises(ValueError, match="must not be empty"):
        client.batch_preference(
            **common,
            preference_model_id="00000000-0000-4000-8000-000000000105",
            requests=(),
        )

    assert execute_calls == 0


def test_preference_client_accepts_any_descriptor_bound_positive_dimension(
    preference_client,
) -> None:
    client, _ = preference_client
    scope = _scope()
    fingerprint = scope["descriptor_fingerprint"]
    served = client.serve(
        board_entity_id=scope["board_entity_id"],
        board_id=scope["board_id"],
        model_key=f"moodboard_{fingerprint}_4",
        descriptor_fingerprint=fingerprint,
        source_report_sha256=scope["source_report_sha256"],
        candidates=(_candidate(1), _candidate(2)),
        candidate_pool_sha256="d" * 64,
    )
    assert served.feature_schema_id == FEATURE_SCHEMA_ID


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        (
            "serve",
            {
                "board_entity_id": "bad",
                "board_id": "a" * 64,
                "model_key": "model",
                "descriptor_fingerprint": "b" * 64,
                "source_report_sha256": "c" * 64,
                "candidates": (_candidate(1), _candidate(2)),
                "candidate_pool_sha256": "d" * 64,
            },
        ),
        (
            "judge",
            {
                "serve_id": "00000000-0000-4000-8000-000000000101",
                "left_result_occurrence_id": "00000000-0000-4000-8000-000000000102",
                "right_result_occurrence_id": "00000000-0000-4000-8000-000000000103",
                "choice": "maybe",
            },
        ),
    ],
)
def test_preference_client_rejects_invalid_inputs_before_process(
    preference_client, method: str, arguments: dict[str, object]
) -> None:
    client, executable = preference_client
    before = executable.stat().st_atime_ns
    with pytest.raises(ValueError):
        getattr(client, method)(**arguments)
    assert executable.stat().st_atime_ns == before


def test_preference_parser_rejects_probability_noncomplement(
    monkeypatch, preference_client
) -> None:
    client, _ = preference_client
    original = client._execute

    def broken(operations):
        result = original(operations)[0]
        result["probability_right_given_decisive"] = 0.30
        return (result,)

    monkeypatch.setattr(client, "_execute", broken)
    scope = _scope()
    with pytest.raises(KhiveProtocolError, match="sum to one"):
        client.preference(
            preference_model_id="00000000-0000-4000-8000-000000000105",
            board_entity_id=scope["board_entity_id"],
            board_id=scope["board_id"],
            model_key=scope["model_key"],
            descriptor_fingerprint=scope["descriptor_fingerprint"],
            source_report_sha256=scope["source_report_sha256"],
            left=_candidate(1),
            right=_candidate(2),
        )


def test_serve_parser_rejects_scope_attribution_drift(monkeypatch, preference_client) -> None:
    client, _ = preference_client
    original = client._execute

    def broken(operations):
        result = original(operations)[0]
        result["scope"]["namespace"] = "foreign"
        return (result,)

    monkeypatch.setattr(client, "_execute", broken)
    scope = _scope()
    with pytest.raises(KhiveProtocolError, match="scope"):
        client.serve(
            board_entity_id=scope["board_entity_id"],
            board_id=scope["board_id"],
            model_key=scope["model_key"],
            descriptor_fingerprint=scope["descriptor_fingerprint"],
            source_report_sha256=scope["source_report_sha256"],
            candidates=(_candidate(1), _candidate(2)),
            candidate_pool_sha256="d" * 64,
        )


def test_trained_model_parser_rejects_scope_drift(monkeypatch, preference_client) -> None:
    client, _ = preference_client
    original = client._execute

    def broken(operations):
        result = original(operations)[0]
        result["scope"]["board_id"] = "0" * 64
        return (result,)

    monkeypatch.setattr(client, "_execute", broken)
    scope = _scope()
    with pytest.raises(KhiveProtocolError, match="scope"):
        client.train_preference(
            board_entity_id=scope["board_entity_id"],
            board_id=scope["board_id"],
            model_key=scope["model_key"],
            descriptor_fingerprint=scope["descriptor_fingerprint"],
        )


def test_prediction_parser_rejects_candidate_identity_drift(monkeypatch, preference_client) -> None:
    client, _ = preference_client
    original = client._execute

    def broken(operations):
        result = original(operations)[0]
        result["left"]["asset_id"] = "00000000-0000-4000-8000-000000000099"
        return (result,)

    monkeypatch.setattr(client, "_execute", broken)
    scope = _scope()
    with pytest.raises(KhiveProtocolError, match="left"):
        client.preference(
            preference_model_id="00000000-0000-4000-8000-000000000105",
            board_entity_id=scope["board_entity_id"],
            board_id=scope["board_id"],
            model_key=scope["model_key"],
            descriptor_fingerprint=scope["descriptor_fingerprint"],
            source_report_sha256=scope["source_report_sha256"],
            left=_candidate(1),
            right=_candidate(2),
        )

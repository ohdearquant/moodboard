"""RED contracts for ADR-0016 locality schemas and identities.

This contract-only slice covers closed raster/mask descriptors, domain-separated identities,
verifier-input identities, and the structural/exact receipt branches already owned by
``moodboard.judgment.v1``.  It deliberately performs no image decoding or pixel comparison.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest
import rfc8785

from moodboard.contracts import compute_document_identity
from moodboard.judgment import JudgmentError, validate_judgment, validate_locality_blocking_pair

JsonObject = dict[str, Any]

RASTER_VERSION = "moodboard.raster.srgb-u8.v1"
MASK_VERSION = "moodboard.mask.u8.v1"
STRUCTURAL_VERSION = "moodboard.verifier.raster-structure.v1"
EXACT_VERSION = "moodboard.verifier.outside-mask-rgb-exact.v1"
RASTER_COMPILER_REVISION = "pillow-12.3-srgb.v1"
MASK_COMPILER_REVISION = "moodboard.rect-mask.v1"

_RASTER_BYTES = bytes([0, 1, 2, 10, 11, 12, 20, 21, 22, 250, 251, 252])
_RASTER_ID = "8a6d40e9b3f6e8cd11dcddafb0867489ca89797c121f589bb2ae3168aa183ad9"
_MASK_BYTES = b"\x00\x01\x00\x00"
_MASK_ID = "c17475e0b8d213c4af6af002a194caefae4e621a963b87aab4726308916d83e9"
_STRUCTURAL_INPUT_ID = "d082c430b458f2647ed43cc0853303166fb963e7c2fb77bbb03873bc48ba7112"
_EXACT_INPUT_ID = "6e0f3df774b695dd0774a82f10066a3ca976d5710cfc8f6dfd89d5a5108f42d7"
_EXACT_NOT_RUN_INPUT_ID = "fb2ef3a8331119f7880983897086193f8b5967c99bff7ca93dce04b749433b70"

_ATTEMPT_ID = "20000000-0000-4000-8000-000000000002"


def _contracts() -> ModuleType:
    return importlib.import_module("moodboard.locality_contracts")


def _digest(character: str) -> str:
    assert len(character) == 1 and character in "0123456789abcdef"
    return character * 64


def _reference_binary_identity(*, schema_version: str, metadata: JsonObject, payload: bytes) -> str:
    return hashlib.sha256(
        schema_version.encode("utf-8") + b"\x00" + rfc8785.dumps(metadata) + b"\x00" + payload
    ).hexdigest()


def _raster_document() -> JsonObject:
    return {
        "schema_version": RASTER_VERSION,
        "compiler_revision": RASTER_COMPILER_REVISION,
        "width": 2,
        "height": 2,
        "mode": "RGB",
        "byte_count": 12,
        "source_content_sha256": _digest("1"),
        "raster_sha256": _RASTER_ID,
    }


def _mask_document() -> JsonObject:
    return {
        "schema_version": MASK_VERSION,
        "compiler_revision": MASK_COMPILER_REVISION,
        "width": 2,
        "height": 2,
        "byte_count": 4,
        "editable_count": 1,
        "protected_count": 3,
        "source_raster_sha256": _RASTER_ID,
        "mask_sha256": _MASK_ID,
    }


def _reference_raster_id(document: JsonObject, raster_bytes: bytes) -> str:
    metadata = {
        key: document[key]
        for key in (
            "compiler_revision",
            "width",
            "height",
            "mode",
            "byte_count",
            "source_content_sha256",
        )
    }
    return _reference_binary_identity(
        schema_version=RASTER_VERSION,
        metadata=metadata,
        payload=raster_bytes,
    )


def _raster_projection(document: JsonObject) -> JsonObject:
    return {
        key: value
        for key, value in document.items()
        if key not in {"schema_version", "raster_sha256"}
    }


def _mask_projection(document: JsonObject) -> JsonObject:
    return {
        key: value
        for key, value in document.items()
        if key not in {"schema_version", "mask_sha256"}
    }


def _reference_mask_id(document: JsonObject, mask_bytes: bytes) -> str:
    metadata = {
        key: document[key]
        for key in (
            "compiler_revision",
            "width",
            "height",
            "byte_count",
            "editable_count",
            "protected_count",
            "source_raster_sha256",
        )
    }
    return _reference_binary_identity(
        schema_version=MASK_VERSION,
        metadata=metadata,
        payload=mask_bytes,
    )


def _schema_validator(path: Path) -> jsonschema.Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _with_evidence_id(document: JsonObject) -> JsonObject:
    document["evidence_id"] = _digest("0")
    document["evidence_id"] = compute_document_identity(
        document,
        schema_version="moodboard.judgment.v1",
        identity_field="evidence_id",
    )
    return document


def _provider_payload_subject() -> JsonObject:
    return {
        "kind": "provider_output_payload",
        "attempt_id": _ATTEMPT_ID,
        "output_index": 0,
        "provider_receipt_id": _digest("7"),
        "content_ref": _digest("8"),
        "content_sha256": _digest("2"),
    }


def _structural_judgment(*, reason: str = "malformed_orientation") -> JsonObject:
    decoded = True
    return _with_evidence_id(
        {
            "schema_version": "moodboard.judgment.v1",
            "kind": "constraint_verification",
            "subject": _provider_payload_subject(),
            "result": {
                "state": "fail",
                "reason": reason,
                "measurements": {
                    "source_width": 2,
                    "source_height": 2,
                    "container_decoded": decoded,
                    "canonical_raster_compiled": False,
                    "frame_count": 1 if decoded else None,
                    "output_width": 2 if decoded else None,
                    "output_height": 2 if decoded else None,
                    "output_mode": "RGB" if decoded else None,
                    "opaque": True if decoded else None,
                },
            },
            "authority": {
                "schema_version": STRUCTURAL_VERSION,
                "input_digest": _STRUCTURAL_INPUT_ID,
                "source_raster_sha256": _RASTER_ID,
                "output_content_sha256": _digest("2"),
                "output_raster_sha256": None,
                "decoder_revision": RASTER_COMPILER_REVISION,
            },
            "evidence_ref": {"kind": "artifact", "artifact_id": _digest("6")},
        }
    )


def _structural_pass_judgment(output_mode: str) -> JsonObject:
    document = _structural_judgment()
    document["subject"] = {
        "kind": "selectable_output_occurrence",
        "output_occurrence_id": _digest("5"),
    }
    document["result"] = {
        "state": "pass",
        "measurements": {
            "source_width": 2,
            "source_height": 2,
            "container_decoded": True,
            "canonical_raster_compiled": True,
            "frame_count": 1,
            "output_width": 2,
            "output_height": 2,
            "output_mode": output_mode,
            "opaque": True,
        },
    }
    document["authority"]["output_raster_sha256"] = _digest("3")
    return _with_evidence_id(document)


def _locality_not_run_judgment(structural: JsonObject) -> JsonObject:
    return _with_evidence_id(
        {
            "schema_version": "moodboard.judgment.v1",
            "kind": "constraint_verification",
            "subject": copy.deepcopy(structural["subject"]),
            "result": {
                "state": "not_run",
                "reason": "structural_verification_failed",
            },
            "authority": {
                "schema_version": EXACT_VERSION,
                "input_digest": _contracts().compute_exact_locality_not_run_input_digest(
                    source_raster_sha256=_RASTER_ID,
                    mask_sha256=_MASK_ID,
                    blocking_structural_evidence_id=structural["evidence_id"],
                ),
                "source_raster_sha256": _RASTER_ID,
                "mask_sha256": _MASK_ID,
                "blocking_structural_evidence_id": structural["evidence_id"],
            },
            "evidence_ref": {"kind": "artifact", "artifact_id": _digest("6")},
        }
    )


def _exact_judgment() -> JsonObject:
    return _with_evidence_id(
        {
            "schema_version": "moodboard.judgment.v1",
            "kind": "constraint_verification",
            "subject": {
                "kind": "selectable_output_occurrence",
                "output_occurrence_id": _digest("5"),
            },
            "result": {
                "state": "pass",
                "measurements": {
                    "protected_pixel_count": 3,
                    "changed_pixel_count": 0,
                    "max_abs_channel_error": 0,
                },
            },
            "authority": {
                "schema_version": EXACT_VERSION,
                "input_digest": _EXACT_INPUT_ID,
                "source_raster_sha256": _RASTER_ID,
                "output_raster_sha256": _digest("3"),
                "mask_sha256": _MASK_ID,
            },
            "evidence_ref": {"kind": "artifact", "artifact_id": _digest("6")},
        }
    )


class TestLocalityContractSchemasAndIdentities:
    def test_versions_and_standalone_descriptor_schemas_are_closed(self) -> None:
        contracts = _contracts()
        assert contracts.RASTER_SCHEMA_VERSION == RASTER_VERSION
        assert contracts.MASK_SCHEMA_VERSION == MASK_VERSION
        assert contracts.STRUCTURAL_VERIFIER_VERSION == STRUCTURAL_VERSION
        assert set(contracts.SCHEMA_PATHS) == {RASTER_VERSION, MASK_VERSION}

        documents = {
            RASTER_VERSION: _raster_document(),
            MASK_VERSION: _mask_document(),
        }
        for version, document in documents.items():
            path = contracts.SCHEMA_PATHS[version]
            assert path.name in {
                "raster_srgb_u8_v1.schema.json",
                "mask_u8_v1.schema.json",
            }
            validator = _schema_validator(path)
            assert not list(validator.iter_errors(document))
            extended = {**document, "undeclared": True}
            assert list(validator.iter_errors(extended))

    def test_raster_identity_matches_independent_golden_and_freezes_value(self) -> None:
        contracts = _contracts()
        document = _raster_document()
        projection = _raster_projection(document)
        assert contracts.compute_raster_sha256(projection, _RASTER_BYTES) == _RASTER_ID

        artifact = contracts.validate_raster_artifact(document, _RASTER_BYTES)
        assert is_dataclass(type(artifact))
        assert artifact.raster_sha256 == _RASTER_ID
        assert artifact.rgb_bytes == _RASTER_BYTES
        with pytest.raises(FrozenInstanceError):
            artifact.width = 3

    @pytest.mark.parametrize("field", ["compiler_revision", "source_content_sha256"])
    def test_raster_identity_detects_metadata_drift(self, field: str) -> None:
        contracts = _contracts()
        document = _raster_document()
        projection = _raster_projection(document)
        projection[field] = "pillow-12.3-srgb.v2" if field == "compiler_revision" else _digest("2")
        assert contracts.compute_raster_sha256(projection, _RASTER_BYTES) != _RASTER_ID

    def test_raster_identity_detects_shape_and_byte_drift(self) -> None:
        contracts = _contracts()
        document = _raster_document()
        projection = _raster_projection(document)
        wider_bytes = _RASTER_BYTES + b"\x01\x02\x03\x04\x05\x06"
        projection.update(width=3, byte_count=len(wider_bytes))
        assert contracts.compute_raster_sha256(projection, wider_bytes) != _RASTER_ID
        assert (
            contracts.compute_raster_sha256(
                _raster_projection(_raster_document()),
                _RASTER_BYTES[:-1] + b"\xfd",
            )
            != _RASTER_ID
        )

    @pytest.mark.parametrize(
        ("mutation", "payload", "code"),
        [
            (lambda value: value.update(extra=True), _RASTER_BYTES, "schema_invalid"),
            (lambda value: value.update(mode="L"), _RASTER_BYTES, "schema_invalid"),
            (lambda value: value.update(byte_count=11), _RASTER_BYTES, "byte_count_mismatch"),
            (lambda value: value.update(width=3), _RASTER_BYTES, "shape_mismatch"),
            (
                lambda value: value.update(raster_sha256=_digest("f")),
                _RASTER_BYTES,
                "identity_mismatch",
            ),
            (lambda value: None, _RASTER_BYTES[:-1], "byte_count_mismatch"),
        ],
    )
    def test_raster_artifact_rejects_noncanonical_publication(
        self,
        mutation: Any,
        payload: bytes,
        code: str,
    ) -> None:
        contracts = _contracts()
        document = _raster_document()
        mutation(document)
        if document.get("raster_sha256") == _RASTER_ID and payload == _RASTER_BYTES:
            # Re-seal malformed but identity-consistent metadata so the semantic error wins.
            document["raster_sha256"] = _reference_raster_id(document, payload)
        with pytest.raises(contracts.LocalityContractError) as captured:
            contracts.validate_raster_artifact(document, payload)
        assert captured.value.code == code

    def test_mask_identity_matches_independent_golden_and_freezes_value(self) -> None:
        contracts = _contracts()
        document = _mask_document()
        projection = _mask_projection(document)
        assert contracts.compute_mask_sha256(projection, _MASK_BYTES) == _MASK_ID

        artifact = contracts.validate_mask_artifact(document, _MASK_BYTES)
        assert is_dataclass(type(artifact))
        assert artifact.mask_sha256 == _MASK_ID
        assert artifact.mask_bytes == _MASK_BYTES
        with pytest.raises(FrozenInstanceError):
            artifact.editable_count = 2

    @pytest.mark.parametrize("field", ["compiler_revision", "source_raster_sha256"])
    def test_mask_identity_detects_metadata_drift(self, field: str) -> None:
        contracts = _contracts()
        document = _mask_document()
        projection = _mask_projection(document)
        projection[field] = (
            "moodboard.rect-mask.v2" if field == "compiler_revision" else _digest("9")
        )
        assert contracts.compute_mask_sha256(projection, _MASK_BYTES) != _MASK_ID

    def test_mask_identity_detects_shape_count_and_byte_drift(self) -> None:
        contracts = _contracts()
        projection = _mask_projection(_mask_document())
        wider = b"\x00\x01\x00\x00\x01\x00"
        projection.update(width=3, byte_count=6, editable_count=2, protected_count=4)
        assert contracts.compute_mask_sha256(projection, wider) != _MASK_ID
        original = _mask_projection(_mask_document())
        assert contracts.compute_mask_sha256(original, b"\x01\x00\x00\x00") != _MASK_ID

    @pytest.mark.parametrize(
        ("document_patch", "payload", "code"),
        [
            ({"extra": True}, _MASK_BYTES, "schema_invalid"),
            ({"byte_count": 3}, _MASK_BYTES, "byte_count_mismatch"),
            ({"width": 3}, _MASK_BYTES, "shape_mismatch"),
            ({"editable_count": 2}, _MASK_BYTES, "count_mismatch"),
            ({"editable_count": False}, _MASK_BYTES, "schema_invalid"),
            ({"mask_sha256": "f" * 64}, _MASK_BYTES, "identity_mismatch"),
            ({}, b"\x00\x02\x00\x00", "mask_not_binary"),
            ({"editable_count": 0, "protected_count": 4}, b"\x00" * 4, "empty_editable_set"),
            ({"editable_count": 4, "protected_count": 0}, b"\x01" * 4, "empty_protected_set"),
        ],
    )
    def test_mask_artifact_rejects_noncanonical_publication(
        self,
        document_patch: JsonObject,
        payload: bytes,
        code: str,
    ) -> None:
        contracts = _contracts()
        document = _mask_document()
        document.update(document_patch)
        if document.get("mask_sha256") == _MASK_ID and (document_patch or payload != _MASK_BYTES):
            document["mask_sha256"] = _reference_mask_id(document, payload)
        with pytest.raises(contracts.LocalityContractError) as captured:
            contracts.validate_mask_artifact(document, payload)
        assert captured.value.code == code

    def test_verifier_input_identity_vectors_are_domain_separated(self) -> None:
        contracts = _contracts()
        assert (
            contracts.compute_structural_input_digest(
                source_raster_sha256=_RASTER_ID,
                output_content_sha256=_digest("2"),
            )
            == _STRUCTURAL_INPUT_ID
        )
        assert (
            contracts.compute_exact_locality_input_digest(
                source_raster_sha256=_RASTER_ID,
                output_raster_sha256=_digest("3"),
                mask_sha256=_MASK_ID,
            )
            == _EXACT_INPUT_ID
        )
        assert (
            contracts.compute_exact_locality_not_run_input_digest(
                source_raster_sha256=_RASTER_ID,
                mask_sha256=_MASK_ID,
                blocking_structural_evidence_id=_digest("4"),
            )
            == _EXACT_NOT_RUN_INPUT_ID
        )
        assert len({_STRUCTURAL_INPUT_ID, _EXACT_INPUT_ID, _EXACT_NOT_RUN_INPUT_ID}) == 3

    @pytest.mark.parametrize("case", ["structural", "exact", "not_run"])
    def test_judgment_rejects_a_resealed_false_verifier_input_digest(self, case: str) -> None:
        structural = _structural_judgment()
        if case == "structural":
            document = structural
        elif case == "exact":
            document = _exact_judgment()
        else:
            document = _locality_not_run_judgment(structural)
        document["authority"]["input_digest"] = _digest("f")
        document = _with_evidence_id(document)

        with pytest.raises(JudgmentError, match="input_digest"):
            validate_judgment(document)

    @pytest.mark.parametrize("reason", ["malformed_orientation", "unsupported_format"])
    def test_source_rejection_has_an_explicit_structural_receipt_reason(self, reason: str) -> None:
        document = _structural_judgment(reason=reason)
        validate_judgment(document)
        assert document["result"]["reason"] == reason
        assert document["subject"] == _provider_payload_subject()

    def test_provider_payload_subject_rejects_a_non_versioned_attempt_uuid(self) -> None:
        document = _structural_judgment()
        document["subject"]["attempt_id"] = "20000000-0000-0000-8000-000000000002"
        document = _with_evidence_id(document)

        with pytest.raises(JudgmentError):
            validate_judgment(document)

    @pytest.mark.parametrize("decoded_mode", ["L", "LA", "RGB", "RGBA"])
    def test_structural_pass_records_decoded_mode_then_binds_canonical_rgb(
        self, decoded_mode: str
    ) -> None:
        document = _structural_pass_judgment(decoded_mode)
        validate_judgment(document)
        assert document["result"]["measurements"]["output_mode"] == decoded_mode
        assert document["result"]["measurements"]["canonical_raster_compiled"] is True
        assert document["authority"]["output_raster_sha256"] == _digest("3")

    def test_structural_pass_rejects_an_unregistered_decoded_mode(self) -> None:
        document = _structural_pass_judgment("P")
        with pytest.raises(JudgmentError):
            validate_judgment(document)

    def test_structural_failure_and_locality_not_run_name_the_same_provider_payload(
        self,
    ) -> None:
        structural = _structural_judgment(reason="unsupported_format")
        locality = _locality_not_run_judgment(structural)
        validate_locality_blocking_pair(structural, locality)
        assert locality["subject"] == structural["subject"] == _provider_payload_subject()

    def test_exact_receipt_uses_existing_judgment_authority_and_identity(self) -> None:
        document = _exact_judgment()
        validate_judgment(document)
        assert document["authority"] == {
            "schema_version": EXACT_VERSION,
            "input_digest": _EXACT_INPUT_ID,
            "source_raster_sha256": _RASTER_ID,
            "output_raster_sha256": _digest("3"),
            "mask_sha256": _MASK_ID,
        }
        changed = copy.deepcopy(document)
        changed["result"]["measurements"]["changed_pixel_count"] = 1
        changed["result"]["measurements"]["max_abs_channel_error"] = 1
        changed["result"]["state"] = "fail"
        with pytest.raises(JudgmentError, match="identity"):
            validate_judgment(changed)

    def test_measured_exact_receipt_cannot_target_an_unvalidated_provider_payload(self) -> None:
        document = _exact_judgment()
        document["subject"] = _provider_payload_subject()
        document = _with_evidence_id(document)
        with pytest.raises(JudgmentError):
            validate_judgment(document)

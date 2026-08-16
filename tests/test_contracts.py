from __future__ import annotations

import copy
from types import MappingProxyType

import pytest

from moodboard.contracts import (
    ContractIdentityError,
    canonical_json_bytes,
    compute_document_identity,
    compute_projection_identity,
    verify_document_identity,
)


def test_rfc8785_golden_vector_pins_numbers_unicode_and_key_order() -> None:
    document = {
        "é": "€",
        "z": -0.0,
        "a": [1e30, 4.5, 0.002, 1e-27],
    }

    assert canonical_json_bytes(document) == (
        b'{"a":[1e+30,4.5,0.002,1e-27],"z":0,'
        b'"\xc3\xa9":"\xe2\x82\xac"}'
    )


def test_rfc8785_orders_object_keys_by_utf16_code_units() -> None:
    # U+10000 is encoded by the UTF-16 pair D800 DC00, so JCS sorts it before
    # the single BMP code unit E000 even though Python code-point order does not.
    assert canonical_json_bytes({"\ue000": 1, "\U00010000": 2}) == (
        b'{"\xf0\x90\x80\x80":2,"\xee\x80\x80":1}'
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -(2**53), 2**53])
def test_rfc8785_rejects_values_outside_ijson(value: float | int) -> None:
    with pytest.raises(ContractIdentityError):
        canonical_json_bytes({"value": value})


def test_computed_identity_pins_schema_domain_and_omitted_field() -> None:
    document: dict[str, object] = {
        "schema_version": "moodboard.judgment.v1",
        "evidence_id": "0" * 64,
        "kind": "constraint_verification",
    }
    before = copy.deepcopy(document)

    measured = compute_document_identity(
        document,
        schema_version="moodboard.judgment.v1",
        identity_field="evidence_id",
    )

    assert measured == "7aedb4e446841bb48800839a92ca1cec2b4d0af9e4e9f380691d913a01d9cf6c"
    assert document == before


def test_explicit_projection_identity_does_not_hash_the_whole_artifact() -> None:
    """ADR-0014 output ids bind only attempt_id and output_index."""

    projection = {
        "attempt_id": "run-01-attempt-01",
        "output_index": 0,
    }
    occurrence = projection | {
        "schema_version": "moodboard.output-occurrence.v1",
        "admission": "eligible",
        "byte_sha256": "f" * 64,
    }

    assert compute_projection_identity(
        projection,
        domain_tag="moodboard.output-occurrence.v1",
    ) == "9ea4f67cf25a605df3b14977eced80ec6138d2c4258ecfd7b5051b15a7acb581"
    assert compute_projection_identity(
        occurrence,
        domain_tag="moodboard.output-occurrence.v1",
    ) != compute_projection_identity(
        projection,
        domain_tag="moodboard.output-occurrence.v1",
    )


def test_projection_identity_snapshots_any_mapping_without_mutation() -> None:
    projection = MappingProxyType({"attempt_id": "attempt-01", "output_index": 0})

    assert compute_projection_identity(
        projection,
        domain_tag="moodboard.output-occurrence.v1",
    ) == compute_projection_identity(
        dict(projection),
        domain_tag="moodboard.output-occurrence.v1",
    )


def test_contract_errors_normalize_cycles_and_invalid_domain_unicode() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(ContractIdentityError):
        canonical_json_bytes(cyclic)
    with pytest.raises(ContractIdentityError):
        compute_projection_identity({"value": 1}, domain_tag="bad-\ud800-domain")


def test_identity_verification_rejects_payload_or_domain_drift() -> None:
    document: dict[str, object] = {
        "schema_version": "moodboard.judgment.v1",
        "kind": "constraint_verification",
        "evidence_id": "7aedb4e446841bb48800839a92ca1cec2b4d0af9e4e9f380691d913a01d9cf6c",
    }
    verify_document_identity(
        document,
        schema_version="moodboard.judgment.v1",
        identity_field="evidence_id",
    )

    tampered = document | {"kind": "board_compatibility"}
    with pytest.raises(ContractIdentityError, match="identity mismatch"):
        verify_document_identity(
            tampered,
            schema_version="moodboard.judgment.v1",
            identity_field="evidence_id",
        )

    with pytest.raises(ContractIdentityError, match="identity mismatch"):
        verify_document_identity(
            document,
            schema_version="moodboard.evidence.v1",
            identity_field="evidence_id",
        )


@pytest.mark.parametrize(
    ("document", "identity_field"),
    [
        ({"schema_version": "moodboard.judgment.v1"}, "evidence_id"),
        (
            {
                "schema_version": "moodboard.judgment.v1",
                "evidence_id": "not-a-digest",
            },
            "evidence_id",
        ),
    ],
)
def test_identity_contract_rejects_missing_or_malformed_identity(
    document: dict[str, object], identity_field: str
) -> None:
    with pytest.raises(ContractIdentityError):
        verify_document_identity(
            document,
            schema_version="moodboard.judgment.v1",
            identity_field=identity_field,
        )

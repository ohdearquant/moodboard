from __future__ import annotations

import base64
import copy
import json
from dataclasses import replace

import jsonschema
import pytest

from moodboard.compositor import (
    COMPOSITOR_OCCURRENCE_VERSION,
    COMPOSITOR_REVISION,
    INSERT_COMPILER_REVISION,
    INSERT_CONFIRMATION_VERSION,
    INSERT_VERSION,
    PNG_ENCODER_REVISION,
    PNG_VERSION,
    SCHEMA_PATHS,
    CompositorError,
    CompositorOutputOccurrence,
    InsertCompileConfirmation,
    _nearest_source_offset,
    compile_raw_crop_nearest,
    compose_source_backed_rect_replace,
    compute_compositor_occurrence_id,
    compute_compositor_replay_key,
    encode_canonical_png,
    resolve_compositor_replay,
    resolve_insert_confirmation_replay,
    seal_compositor_output_occurrence,
    seal_insert_compile_confirmation,
    verify_compositor_output_structure,
)
from moodboard.compositor import (
    from_json_dict as compositor_from_json,
)
from moodboard.compositor import (
    to_json_dict as compositor_to_json,
)
from moodboard.contracts import compute_document_identity
from moodboard.intent_packet import from_json_dict as intent_packet_from_json
from moodboard.judgment import SCHEMA_VERSION as JUDGMENT_VERSION
from moodboard.judgment import ConstraintVerificationJudgment
from moodboard.judgment import from_json_dict as judgment_from_json
from moodboard.judgment import to_json_dict as judgment_to_json
from moodboard.locality import (
    COMPILER_REVISION,
    build_locality_not_run,
    compile_canonical_raster,
    compile_rectangle_mask,
    verify_output_structure,
    verify_outside_mask_rgb_exact,
)
from moodboard.locality_contracts import compute_mask_sha256, compute_raster_sha256
from moodboard.provider_artifacts import OutputOccurrence
from moodboard.provider_artifacts import from_json_dict as provider_from_json
from tests.test_intent_packet import (
    _refresh_packet_identity,
    _sync_confirmation,
    _valid_packet,
)
from tests.test_provider_artifacts import (
    _refresh_document_id,
    _valid_attempt,
    _valid_capability,
    _valid_events,
    _valid_normalized_request,
    _valid_output,
    _valid_receipt,
    _valid_run,
)


class _ExplodingMapping(dict):
    def items(self):
        raise RuntimeError("sk-live-forged-dataclass-must-not-leak")


def _raster_document(raster) -> dict[str, object]:
    return {
        "schema_version": raster.schema_version,
        "compiler_revision": raster.compiler_revision,
        "width": raster.width,
        "height": raster.height,
        "mode": raster.mode,
        "byte_count": raster.byte_count,
        "source_content_sha256": raster.source_content_sha256,
        "raster_sha256": raster.raster_sha256,
    }


def _mask_document(mask) -> dict[str, object]:
    return {
        "schema_version": mask.schema_version,
        "compiler_revision": mask.compiler_revision,
        "width": mask.width,
        "height": mask.height,
        "byte_count": mask.byte_count,
        "editable_count": mask.editable_count,
        "protected_count": mask.protected_count,
        "source_raster_sha256": mask.source_raster_sha256,
        "mask_sha256": mask.mask_sha256,
    }


def _compile_rgb(width: int, height: int, rgb: bytes):
    png = encode_canonical_png(width=width, height=height, rgb_bytes=rgb)
    raster = compile_canonical_raster(
        png.png_bytes,
        source_content_sha256=png.content_sha256,
    )
    assert raster.rgb_bytes == rgb
    return png, raster


def _provider_chain(
    *,
    source_png,
    source_raster,
    mask,
    raw_png,
    raw_raster,
    rejected: bool = False,
    region: dict[str, int] | None = None,
):
    packet = _valid_packet()
    packet["source"].update(
        {
            "content_ref": source_png.content_ref,
            "content_sha256": source_png.content_sha256,
            "width": source_raster.width,
            "height": source_raster.height,
        }
    )
    payload = packet["operation"]["payload"]
    payload["source_raster"] = _raster_document(source_raster)
    region_bounds = region or {"left": 1, "top": 1, "right": 3, "bottom": 2}
    payload["region"] = {
        "selection_tool_revision": "studio.rectangle.v1",
        **region_bounds,
    }
    payload["mask"] = _mask_document(mask)
    source_input, mask_input = packet["generation_request"]["operation_inputs"]
    source_input["original_artifact"].update(
        {
            "content_ref": source_png.content_ref,
            "content_sha256": source_png.content_sha256,
        }
    )
    source_input["delivered_artifact"].update(
        {
            "content_ref": source_png.content_ref,
            "content_sha256": source_png.content_sha256,
            "byte_count": source_png.byte_count,
            "width": source_raster.width,
            "height": source_raster.height,
        }
    )
    mask_input["original_artifact"]["mask_sha256"] = mask.mask_sha256
    _sync_confirmation(packet)
    _refresh_packet_identity(packet)

    capability = _valid_capability(packet)
    if rejected:
        capability["actual_model_disclosure"] = "attested"
        _refresh_document_id(capability)
    packet["generation_request"]["capability_snapshot_id"] = capability["capability_snapshot_id"]
    _sync_confirmation(packet)
    _refresh_packet_identity(packet)
    normalized = _valid_normalized_request(packet, capability)
    run = _valid_run(packet)
    attempt = _valid_attempt(packet, run, capability, normalized)
    receipt = _valid_receipt(packet, attempt, normalized)
    if rejected:
        receipt["actual_model"] = {
            "state": "attested",
            "model": "vendor/substituted-model",
            "source_field": "$.model",
        }
    receipt["outputs"][0].update(
        {
            "content_ref": raw_png.content_ref,
            "content_sha256": raw_png.content_sha256,
            "byte_count": raw_png.byte_count,
            "media_type_claim": "image/png",
        }
    )
    _refresh_document_id(receipt)
    output = _valid_output(packet, run, attempt, normalized, receipt)
    output["original"].update(
        {
            "content_ref": raw_png.content_ref,
            "content_sha256": raw_png.content_sha256,
            "mime": "image/png",
            "byte_count": raw_png.byte_count,
            "width": raw_raster.width,
            "height": raw_raster.height,
        }
    )
    output["media_validation"].update(
        {
            "decoder_revision": COMPILER_REVISION,
            "measured_content_sha256": raw_png.content_sha256,
            "measured_content_ref": raw_png.content_ref,
            "measured_byte_count": raw_png.byte_count,
            "measured_mime": "image/png",
            "measured_width": raw_raster.width,
            "measured_height": raw_raster.height,
            "measured_mode": "RGB",
        }
    )
    if rejected:
        output["admission"] = {
            "state": "rejected",
            "rejection_reasons": ["actual_model_conflict"],
        }
    events = _valid_events(attempt, capability, normalized, receipt, output)
    if rejected:
        terminal = next(event for event in events if event["state"] == "succeeded")
        terminal["state"] = "failed"
        terminal["detail"] = {
            "kind": "failed",
            "failure_stage": "provenance",
            "failure_code": "actual_model_conflict",
        }
        _refresh_document_id(terminal)
    artifacts = [run, capability, normalized, attempt, *events, receipt, output]
    return packet, artifacts, receipt, output


def _fixture(*, same_size_raw: bool = False, nonrectangular_mask: bool = False):
    source_rgb = bytes((10, 20, 30)) * 12
    source_png, source = _compile_rgb(4, 3, source_rgb)
    mask = compile_rectangle_mask(source, left=1, top=1, right=3, bottom=2)
    if nonrectangular_mask:
        mask_bytes = bytearray(mask.mask_bytes)
        mask_bytes[6] = 0
        mask_bytes[7] = 1
        projection = _mask_document(mask)
        del projection["schema_version"]
        del projection["mask_sha256"]
        mask = replace(
            mask,
            mask_sha256=compute_mask_sha256(projection, bytes(mask_bytes)),
            mask_bytes=bytes(mask_bytes),
        )
    if same_size_raw:
        raw_bytes = bytearray(source_rgb)
        raw_bytes[15:21] = bytes((91, 92, 93, 101, 102, 103))
        raw_png, raw = _compile_rgb(4, 3, bytes(raw_bytes))
        crop = {"left": 1, "top": 1, "right": 3, "bottom": 2}
    else:
        raw_rgb = b"".join(bytes((value, value + 1, value + 2)) for value in range(0, 18, 3))
        raw_png, raw = _compile_rgb(3, 2, raw_rgb)
        crop = {"left": 0, "top": 0, "right": 3, "bottom": 2}
    packet, artifacts, receipt, occurrence = _provider_chain(
        source_png=source_png,
        source_raster=source,
        mask=mask,
        raw_png=raw_png,
        raw_raster=raw,
    )
    structural = verify_output_structure(
        source,
        provider_receipt=receipt,
        output_index=0,
        output_bytes=raw_png.png_bytes,
        output_occurrence=occurrence,
    )
    assert structural.output_raster == raw
    if same_size_raw:
        locality = verify_outside_mask_rgb_exact(
            source,
            raw,
            mask,
            output_occurrence=occurrence,
            structural_pass=structural.judgment,
        )
    else:
        locality = build_locality_not_run(structural.judgment, mask)
    confirmation = seal_insert_compile_confirmation(
        {
            "schema_version": INSERT_CONFIRMATION_VERSION,
            "principal_id": packet["confirmation"]["principal_id"],
            "studio_session_id": packet["confirmation"]["studio_session_id"],
            "intent_packet_id": packet["intent_packet_id"],
            "raw_output_occurrence_id": occurrence["output_occurrence_id"],
            "raw_output_raster_sha256": raw.raster_sha256,
            "raw_structural_evidence_id": structural.judgment.evidence_id,
            "raw_locality_evidence_id": locality.evidence_id,
            "crop": crop,
            "target_region": {
                "schema_version": "moodboard.target-region.v1",
                "source_raster_sha256": source.raster_sha256,
                "mask_sha256": mask.mask_sha256,
                "left": 1,
                "top": 1,
                "right": 3,
                "bottom": 2,
                "width": 2,
                "height": 1,
            },
            "compiler_policy": {
                "policy_id": "raw_crop_nearest.v1",
                "compiler_revision": INSERT_COMPILER_REVISION,
            },
            "confirmed_at": "2026-08-16T20:31:00Z",
        }
    )
    insert = compile_raw_crop_nearest(
        raw,
        raw_output_occurrence=occurrence,
        raw_structural_judgment=structural.judgment,
        raw_locality_judgment=locality,
        confirmation=confirmation,
    )
    return {
        "source": source,
        "source_png": source_png,
        "raw": raw,
        "raw_png": raw_png,
        "mask": mask,
        "packet": packet,
        "artifacts": artifacts,
        "occurrence": occurrence,
        "structural": structural,
        "raw_locality": locality,
        "confirmation": confirmation,
        "insert": insert,
    }


def _compile_mapping_case(
    *, source_span: int, target_span: int, crop_left: int, vertical: bool = False
) -> list[int]:
    if vertical:
        source_png, source = _compile_rgb(3, 6, bytes((10, 20, 30)) * 18)
        raw_height = crop_left + source_span
        raw_rgb = b"".join(bytes((y, y + 20, y + 40)) for y in range(raw_height))
        raw_png, raw = _compile_rgb(1, raw_height, raw_rgb)
        region = {"left": 1, "top": 1, "right": 2, "bottom": 1 + target_span}
        crop = {
            "left": 0,
            "top": crop_left,
            "right": 1,
            "bottom": crop_left + source_span,
        }
        target_width, target_height = 1, target_span
    else:
        source_png, source = _compile_rgb(6, 3, bytes((10, 20, 30)) * 18)
        raw_width = crop_left + source_span
        raw_rgb = b"".join(bytes((x, x + 20, x + 40)) for x in range(raw_width))
        raw_png, raw = _compile_rgb(raw_width, 1, raw_rgb)
        region = {"left": 1, "top": 1, "right": 1 + target_span, "bottom": 2}
        crop = {
            "left": crop_left,
            "top": 0,
            "right": crop_left + source_span,
            "bottom": 1,
        }
        target_width, target_height = target_span, 1
    mask = compile_rectangle_mask(source, **region)
    packet, _, receipt, occurrence = _provider_chain(
        source_png=source_png,
        source_raster=source,
        mask=mask,
        raw_png=raw_png,
        raw_raster=raw,
        region=region,
    )
    structural = verify_output_structure(
        source,
        provider_receipt=receipt,
        output_index=0,
        output_bytes=raw_png.png_bytes,
        output_occurrence=occurrence,
    )
    locality = build_locality_not_run(structural.judgment, mask)
    confirmation = seal_insert_compile_confirmation(
        {
            "schema_version": INSERT_CONFIRMATION_VERSION,
            "principal_id": packet["confirmation"]["principal_id"],
            "studio_session_id": packet["confirmation"]["studio_session_id"],
            "intent_packet_id": packet["intent_packet_id"],
            "raw_output_occurrence_id": occurrence["output_occurrence_id"],
            "raw_output_raster_sha256": raw.raster_sha256,
            "raw_structural_evidence_id": structural.judgment.evidence_id,
            "raw_locality_evidence_id": locality.evidence_id,
            "crop": crop,
            "target_region": {
                "schema_version": "moodboard.target-region.v1",
                "source_raster_sha256": source.raster_sha256,
                "mask_sha256": mask.mask_sha256,
                **region,
                "width": target_width,
                "height": target_height,
            },
            "compiler_policy": {
                "policy_id": "raw_crop_nearest.v1",
                "compiler_revision": INSERT_COMPILER_REVISION,
            },
            "confirmed_at": "2026-08-16T20:31:00Z",
        }
    )
    insert = compile_raw_crop_nearest(
        raw,
        raw_output_occurrence=occurrence,
        raw_structural_judgment=structural.judgment,
        raw_locality_judgment=locality,
        confirmation=confirmation,
    )
    return [insert.rgb_bytes[offset] for offset in range(0, len(insert.rgb_bytes), 3)]


def test_registered_compositor_versions_are_explicit() -> None:
    assert INSERT_CONFIRMATION_VERSION == "moodboard.insert-compile-confirmation.v1"
    assert INSERT_VERSION == "moodboard.insert.rgb-u8.v1"
    assert PNG_VERSION == "moodboard.canonical-png.v1"
    assert COMPOSITOR_OCCURRENCE_VERSION == "moodboard.compositor-output-occurrence.v1"
    assert INSERT_COMPILER_REVISION == "moodboard.insert-compiler.raw-crop-nearest.v1"
    assert PNG_ENCODER_REVISION == "moodboard.png-encoder.rgb8-filter0-deflate-stored.v1"
    assert COMPOSITOR_REVISION == "moodboard.compositor.source-backed-rect-replace.v1"


@pytest.mark.parametrize("schema_path", SCHEMA_PATHS.values(), ids=lambda path: path.name)
def test_compositor_schemas_are_valid_and_recursively_closed(schema_path) -> None:
    document = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(document)

    object_schemas: list[dict[str, object]] = []

    def visit(value) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                object_schemas.append(value)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(document)
    assert object_schemas
    assert all(schema.get("additionalProperties") is False for schema in object_schemas)


def test_canonical_png_matches_the_adr_golden() -> None:
    artifact = encode_canonical_png(width=2, height=1, rgb_bytes=bytes(range(1, 7)))

    assert artifact.byte_count == 75
    assert artifact.content_sha256 == (
        "6c51b0237c26c450f73907fe73a1d48f68d6564bc5cbe8f4fce188194234d5b3"
    )
    assert artifact.content_ref == (
        "5b212fe105c55c86ade3e67d05650e22e6a34c67c2f8a2c9712ec816bf1632e7"
    )
    assert base64.b64encode(artifact.png_bytes).decode("ascii") == (
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAAEklEQVR4AQEHAPj/"
        "AAECAwQFBgA/ABZoQV+NAAAAAElFTkSuQmCC"
    )


def test_canonical_png_is_deterministic_and_rejects_shape_drift() -> None:
    pixels = bytes(range(1, 7))
    assert encode_canonical_png(width=2, height=1, rgb_bytes=pixels) == encode_canonical_png(
        width=2,
        height=1,
        rgb_bytes=pixels,
    )

    boundary_rgb = bytes(range(256)) * 255 + bytes(range(255))
    boundary = encode_canonical_png(width=21_845, height=1, rgb_bytes=boundary_rgb)
    assert boundary.byte_count == 65_609
    assert boundary.content_sha256 == (
        "ccfba72687774c5e44e728e8f77bcf9a6056637f0d97ba5e3aa0e003683fa758"
    )
    assert boundary.content_ref == (
        "093d876f6f77820b06b8aedf0990ac949ff1cfb8316fd5cf6ffca9d62e0b2efe"
    )
    assert boundary.png_bytes[65_583:65_589] == bytes.fromhex("010100fefffe")


def test_provider_raw_occurrence_version_remains_disjoint() -> None:
    from moodboard.provider_artifacts import OUTPUT_VERSION

    assert OUTPUT_VERSION == "moodboard.output-occurrence.v1"
    assert OUTPUT_VERSION != COMPOSITOR_OCCURRENCE_VERSION


def test_confirmation_and_nearest_crop_bind_both_raw_verdicts() -> None:
    fixture = _fixture()
    confirmation = fixture["confirmation"]
    insert = fixture["insert"]

    assert fixture["structural"].judgment.result["state"] == "fail"
    assert fixture["structural"].judgment.result["reason"] == "dimension_mismatch"
    assert fixture["raw_locality"].result["state"] == "not_run"
    assert insert.rgb_bytes == bytes((9, 10, 11, 15, 16, 17))
    assert (insert.width, insert.height, insert.mode) == (2, 1, "RGB")
    assert insert.raw_structural_evidence_id == fixture["structural"].judgment.evidence_id
    assert insert.raw_locality_evidence_id == fixture["raw_locality"].evidence_id
    assert confirmation.preview_projection["raw_structural_evidence_id"] == (
        insert.raw_structural_evidence_id
    )
    assert confirmation.preview_projection["raw_locality_evidence_id"] == (
        insert.raw_locality_evidence_id
    )


@pytest.mark.parametrize(
    ("source_span", "target_span", "crop_left", "expected"),
    [
        (3, 2, 0, [0, 2]),
        (2, 3, 2, [2, 3, 3]),
        (5, 3, 1, [1, 3, 5]),
        (1, 4, 4, [4, 4, 4, 4]),
    ],
)
def test_nearest_mapping_pins_integer_center_goldens(
    source_span: int,
    target_span: int,
    crop_left: int,
    expected: list[int],
) -> None:
    measured = [
        crop_left + _nearest_source_offset(index, source_span, target_span)
        for index in range(target_span)
    ]
    assert measured == expected
    assert (
        _compile_mapping_case(
            source_span=source_span,
            target_span=target_span,
            crop_left=crop_left,
        )
        == expected
    )


def test_nearest_compiler_pins_nonzero_top_and_bottom_edge() -> None:
    assert _compile_mapping_case(
        source_span=2,
        target_span=3,
        crop_left=2,
        vertical=True,
    ) == [2, 3, 3]


def test_confirmation_time_changes_identity_but_not_logical_key() -> None:
    fixture = _fixture()
    first = fixture["confirmation"]
    draft = compositor_to_json(first)
    del draft["insert_compile_confirmation_id"]
    del draft["confirmation_key"]
    draft["confirmed_at"] = "2026-08-16T20:32:00Z"

    second = seal_insert_compile_confirmation(draft)

    assert second.confirmation_key == first.confirmation_key
    assert second.insert_compile_confirmation_id != first.insert_compile_confirmation_id
    assert resolve_insert_confirmation_replay(first, second) is first

    principal_draft = compositor_to_json(first)
    del principal_draft["insert_compile_confirmation_id"]
    del principal_draft["confirmation_key"]
    principal_draft["principal_id"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    principal = seal_insert_compile_confirmation(principal_draft)
    assert principal.confirmation_key != first.confirmation_key
    assert principal.insert_compile_confirmation_id != first.insert_compile_confirmation_id

    preview_drift = compositor_to_json(first)
    preview_drift["preview_projection"]["target_width"] = 3
    preview_drift["confirmation_key"] = "0" * 64
    preview_drift["insert_compile_confirmation_id"] = "0" * 64
    with pytest.raises(CompositorError) as preview_error:
        compositor_from_json(preview_drift)
    assert preview_error.value.code == "preview_mismatch"


def test_composition_validates_full_provider_ancestry_and_exact_locality() -> None:
    fixture = _fixture()
    result = compose_source_backed_rect_replace(
        fixture["source"],
        fixture["insert"],
        fixture["mask"],
        intent_packet=intent_packet_from_json(copy.deepcopy(fixture["packet"])),
        provider_artifacts=copy.deepcopy(fixture["artifacts"]),
        raw_output_occurrence=copy.deepcopy(fixture["occurrence"]),
        raw_output_bytes=fixture["raw_png"].png_bytes,
        raw_output_raster=fixture["raw"],
        raw_structural_judgment=fixture["structural"].judgment,
        raw_locality_judgment=fixture["raw_locality"],
        confirmation=fixture["confirmation"],
    )

    assert result.output_raster.width == fixture["source"].width
    assert result.output_raster.height == fixture["source"].height
    assert result.output_raster.raster_sha256 == (
        "6978841038f4ad348576eec69e1059f3c63bd2348fd5352de3e56cbb2d5504bb"
    )
    assert result.png.content_sha256 == (
        "42aab01b75863e9c1ba4737db156bbb82e853b537a47ba455bb765d7da77dd08"
    )
    assert result.occurrence.compositor_replay_key == (
        "b6d6798ba7522006b3a443ab28f96a704f28f638ad26421a933a83cad7de08cd"
    )
    assert result.occurrence.output_occurrence_id == (
        "e302f759b09ff47bf47cc423236527590e97128ad34c27e9676fcbed185e79f6"
    )
    assert result.output_raster.rgb_bytes == (
        bytes((10, 20, 30)) * 5
        + bytes((9, 10, 11, 15, 16, 17))
        + bytes((10, 20, 30))
        + bytes((10, 20, 30)) * 4
    )
    assert result.structural.judgment.result["state"] == "pass"
    assert result.locality.result == {
        "state": "pass",
        "measurements": {
            "protected_pixel_count": 10,
            "changed_pixel_count": 0,
            "max_abs_channel_error": 0,
        },
    }
    assert result.occurrence.lineage["raw_structural_evidence_id"] == (
        fixture["structural"].judgment.evidence_id
    )
    assert result.occurrence.lineage["raw_locality_evidence_id"] == (
        fixture["raw_locality"].evidence_id
    )
    assert fixture["structural"].judgment.result["reason"] == "dimension_mismatch"
    assert fixture["raw_locality"].result["state"] == "not_run"

    _, wrong_source = _compile_rgb(4, 3, bytes((99, 98, 97)) * 12)
    with pytest.raises(CompositorError) as wrong_source_error:
        verify_compositor_output_structure(
            wrong_source,
            compositor_occurrence=result.occurrence,
            output_raster=result.output_raster,
            output_bytes=result.png.png_bytes,
        )
    assert wrong_source_error.value.code == "output_binding_mismatch"


def test_composition_rejects_tampered_provider_ancestry() -> None:
    fixture = _fixture()
    tampered = copy.deepcopy(fixture["artifacts"])
    output = next(
        artifact
        for artifact in tampered
        if artifact["schema_version"] == "moodboard.output-occurrence.v1"
    )
    output["intent_packet_id"] = "f" * 64

    with pytest.raises(CompositorError) as error:
        compose_source_backed_rect_replace(
            fixture["source"],
            fixture["insert"],
            fixture["mask"],
            intent_packet=copy.deepcopy(fixture["packet"]),
            provider_artifacts=tampered,
            raw_output_occurrence=copy.deepcopy(fixture["occurrence"]),
            raw_output_bytes=fixture["raw_png"].png_bytes,
            raw_output_raster=fixture["raw"],
            raw_structural_judgment=fixture["structural"].judgment,
            raw_locality_judgment=fixture["raw_locality"],
            confirmation=fixture["confirmation"],
        )

    assert error.value.code == "provider_bundle_invalid"

    decoder_drift = copy.deepcopy(fixture["artifacts"])
    bundled_occurrence = next(
        artifact
        for artifact in decoder_drift
        if artifact["schema_version"] == "moodboard.output-occurrence.v1"
    )
    bundled_occurrence["media_validation"]["decoder_revision"] = "evil.decoder.v1"
    with pytest.raises(CompositorError) as decoder_error:
        compose_source_backed_rect_replace(
            fixture["source"],
            fixture["insert"],
            fixture["mask"],
            intent_packet=copy.deepcopy(fixture["packet"]),
            provider_artifacts=decoder_drift,
            raw_output_occurrence=copy.deepcopy(bundled_occurrence),
            raw_output_bytes=fixture["raw_png"].png_bytes,
            raw_output_raster=fixture["raw"],
            raw_structural_judgment=fixture["structural"].judgment,
            raw_locality_judgment=fixture["raw_locality"],
            confirmation=fixture["confirmation"],
        )
    assert decoder_error.value.code == "judgment_mismatch"

    rejected_packet, rejected_artifacts, _, rejected_occurrence = _provider_chain(
        source_png=fixture["source_png"],
        source_raster=fixture["source"],
        mask=fixture["mask"],
        raw_png=fixture["raw_png"],
        raw_raster=fixture["raw"],
        rejected=True,
    )
    with pytest.raises(CompositorError) as rejected_error:
        compose_source_backed_rect_replace(
            fixture["source"],
            fixture["insert"],
            fixture["mask"],
            intent_packet=rejected_packet,
            provider_artifacts=rejected_artifacts,
            raw_output_occurrence=rejected_occurrence,
            raw_output_bytes=fixture["raw_png"].png_bytes,
            raw_output_raster=fixture["raw"],
            raw_structural_judgment=fixture["structural"].judgment,
            raw_locality_judgment=fixture["raw_locality"],
            confirmation=fixture["confirmation"],
        )
    assert rejected_error.value.code == "raw_output_ineligible"


def test_composition_replays_raw_exact_measurements_before_publication() -> None:
    fixture = _fixture(same_size_raw=True)
    assert fixture["structural"].judgment.result["state"] == "pass"
    assert fixture["raw_locality"].result["state"] == "pass"

    forged_document = judgment_to_json(fixture["raw_locality"])
    forged_document["result"]["measurements"]["protected_pixel_count"] = 1
    forged_document["evidence_id"] = compute_document_identity(
        forged_document,
        schema_version=JUDGMENT_VERSION,
        identity_field="evidence_id",
    )
    forged = judgment_from_json(forged_document)
    assert isinstance(forged, ConstraintVerificationJudgment)

    with pytest.raises(CompositorError) as error:
        compose_source_backed_rect_replace(
            fixture["source"],
            fixture["insert"],
            fixture["mask"],
            intent_packet=copy.deepcopy(fixture["packet"]),
            provider_artifacts=copy.deepcopy(fixture["artifacts"]),
            raw_output_occurrence=copy.deepcopy(fixture["occurrence"]),
            raw_output_bytes=fixture["raw_png"].png_bytes,
            raw_output_raster=fixture["raw"],
            raw_structural_judgment=fixture["structural"].judgment,
            raw_locality_judgment=forged,
            confirmation=fixture["confirmation"],
        )

    assert error.value.code == "judgment_mismatch"

    forged_rgb = bytes((200, 201, 202)) * (fixture["raw"].width * fixture["raw"].height)
    forged_projection = {
        "compiler_revision": fixture["raw"].compiler_revision,
        "width": fixture["raw"].width,
        "height": fixture["raw"].height,
        "mode": fixture["raw"].mode,
        "byte_count": len(forged_rgb),
        "source_content_sha256": fixture["raw"].source_content_sha256,
    }
    forged_raster = replace(
        fixture["raw"],
        raster_sha256=compute_raster_sha256(forged_projection, forged_rgb),
        rgb_bytes=forged_rgb,
    )
    with pytest.raises(CompositorError) as byte_replay_error:
        compose_source_backed_rect_replace(
            fixture["source"],
            fixture["insert"],
            fixture["mask"],
            intent_packet=copy.deepcopy(fixture["packet"]),
            provider_artifacts=copy.deepcopy(fixture["artifacts"]),
            raw_output_occurrence=copy.deepcopy(fixture["occurrence"]),
            raw_output_bytes=fixture["raw_png"].png_bytes,
            raw_output_raster=forged_raster,
            raw_structural_judgment=fixture["structural"].judgment,
            raw_locality_judgment=fixture["raw_locality"],
            confirmation=fixture["confirmation"],
        )
    assert byte_replay_error.value.code == "judgment_mismatch"


def test_composition_rejects_resealed_structural_measurement_drift() -> None:
    fixture = _fixture()
    structural_document = judgment_to_json(fixture["structural"].judgment)
    structural_document["result"]["measurements"]["source_width"] = 999
    structural_document["evidence_id"] = compute_document_identity(
        structural_document,
        schema_version=JUDGMENT_VERSION,
        identity_field="evidence_id",
    )
    forged_structural = judgment_from_json(structural_document)
    assert isinstance(forged_structural, ConstraintVerificationJudgment)
    forged_locality = build_locality_not_run(forged_structural, fixture["mask"])

    confirmation_draft = compositor_to_json(fixture["confirmation"])
    for field in (
        "insert_compile_confirmation_id",
        "confirmation_key",
        "preview_projection",
    ):
        del confirmation_draft[field]
    confirmation_draft["raw_structural_evidence_id"] = forged_structural.evidence_id
    confirmation_draft["raw_locality_evidence_id"] = forged_locality.evidence_id
    forged_confirmation = seal_insert_compile_confirmation(confirmation_draft)
    forged_insert = compile_raw_crop_nearest(
        fixture["raw"],
        raw_output_occurrence=fixture["occurrence"],
        raw_structural_judgment=forged_structural,
        raw_locality_judgment=forged_locality,
        confirmation=forged_confirmation,
    )

    with pytest.raises(CompositorError) as error:
        compose_source_backed_rect_replace(
            fixture["source"],
            forged_insert,
            fixture["mask"],
            intent_packet=copy.deepcopy(fixture["packet"]),
            provider_artifacts=copy.deepcopy(fixture["artifacts"]),
            raw_output_occurrence=copy.deepcopy(fixture["occurrence"]),
            raw_output_bytes=fixture["raw_png"].png_bytes,
            raw_output_raster=fixture["raw"],
            raw_structural_judgment=forged_structural,
            raw_locality_judgment=forged_locality,
            confirmation=forged_confirmation,
        )

    assert error.value.code == "judgment_mismatch"


def test_insert_rejects_resealed_locality_blocker_reference() -> None:
    fixture = _fixture()
    locality_document = judgment_to_json(fixture["raw_locality"])
    locality_document["evidence_ref"]["artifact_id"] = "d" * 64
    locality_document["evidence_id"] = compute_document_identity(
        locality_document,
        schema_version=JUDGMENT_VERSION,
        identity_field="evidence_id",
    )
    forged_locality = judgment_from_json(locality_document)
    assert isinstance(forged_locality, ConstraintVerificationJudgment)

    confirmation_draft = compositor_to_json(fixture["confirmation"])
    for field in (
        "insert_compile_confirmation_id",
        "confirmation_key",
        "preview_projection",
    ):
        del confirmation_draft[field]
    confirmation_draft["raw_locality_evidence_id"] = forged_locality.evidence_id
    confirmation = seal_insert_compile_confirmation(confirmation_draft)

    with pytest.raises(CompositorError) as error:
        compile_raw_crop_nearest(
            fixture["raw"],
            raw_output_occurrence=fixture["occurrence"],
            raw_structural_judgment=fixture["structural"].judgment,
            raw_locality_judgment=forged_locality,
            confirmation=confirmation,
        )
    assert error.value.code == "judgment_mismatch"


def test_compositor_artifacts_are_closed_and_round_trip_immutably() -> None:
    fixture = _fixture()
    document = compositor_to_json(fixture["confirmation"])
    restored = compositor_from_json(copy.deepcopy(document))
    document["preview_projection"]["target_width"] = 999

    assert isinstance(restored, InsertCompileConfirmation)
    assert restored == fixture["confirmation"]
    assert restored.preview_projection["target_width"] == 2
    invalid = compositor_to_json(restored)
    invalid["unexpected"] = "secret-value-must-not-leak"
    with pytest.raises(CompositorError) as error:
        compositor_from_json(invalid)
    assert error.value.code == "schema_invalid"
    assert "secret-value-must-not-leak" not in str(error.value)

    forged = replace(restored, crop=_ExplodingMapping())
    with pytest.raises(CompositorError) as forged_error:
        compositor_to_json(forged)
    assert forged_error.value.code == "contract_mismatch"
    assert "sk-live-forged-dataclass-must-not-leak" not in str(forged_error.value)


def test_imported_typed_inputs_normalize_adversarial_projection_failures() -> None:
    fixture = _fixture()
    typed_occurrence = provider_from_json(copy.deepcopy(fixture["occurrence"]))
    assert isinstance(typed_occurrence, OutputOccurrence)

    forged_occurrence = replace(typed_occurrence, original=_ExplodingMapping())
    with pytest.raises(CompositorError) as occurrence_error:
        compile_raw_crop_nearest(
            fixture["raw"],
            raw_output_occurrence=forged_occurrence,
            raw_structural_judgment=fixture["structural"].judgment,
            raw_locality_judgment=fixture["raw_locality"],
            confirmation=fixture["confirmation"],
        )
    assert occurrence_error.value.code == "provider_contract_mismatch"
    assert "sk-live-forged-dataclass-must-not-leak" not in str(occurrence_error.value)

    forged_judgment = replace(
        fixture["structural"].judgment,
        authority=_ExplodingMapping(),
    )
    with pytest.raises(CompositorError) as judgment_error:
        compile_raw_crop_nearest(
            fixture["raw"],
            raw_output_occurrence=typed_occurrence,
            raw_structural_judgment=forged_judgment,
            raw_locality_judgment=fixture["raw_locality"],
            confirmation=fixture["confirmation"],
        )
    assert judgment_error.value.code == "judgment_mismatch"
    assert "sk-live-forged-dataclass-must-not-leak" not in str(judgment_error.value)

    typed_packet = intent_packet_from_json(copy.deepcopy(fixture["packet"]))
    forged_packet = replace(typed_packet, operation=_ExplodingMapping())
    with pytest.raises(CompositorError) as packet_error:
        compose_source_backed_rect_replace(
            fixture["source"],
            fixture["insert"],
            fixture["mask"],
            intent_packet=forged_packet,
            provider_artifacts=copy.deepcopy(fixture["artifacts"]),
            raw_output_occurrence=typed_occurrence,
            raw_output_bytes=fixture["raw_png"].png_bytes,
            raw_output_raster=fixture["raw"],
            raw_structural_judgment=fixture["structural"].judgment,
            raw_locality_judgment=fixture["raw_locality"],
            confirmation=fixture["confirmation"],
        )
    assert packet_error.value.code == "intent_packet_mismatch"
    assert "sk-live-forged-dataclass-must-not-leak" not in str(packet_error.value)


def test_png_shape_validation_rejects_bool_and_length_drift() -> None:
    with pytest.raises(CompositorError) as bool_error:
        encode_canonical_png(width=True, height=1, rgb_bytes=b"\x00\x00\x00")
    assert bool_error.value.code == "shape_mismatch"

    with pytest.raises(CompositorError) as length_error:
        encode_canonical_png(width=2, height=1, rgb_bytes=b"\x00" * 5)
    assert length_error.value.code == "shape_mismatch"


def test_target_region_and_bundle_inputs_fail_before_unbounded_work() -> None:
    fixture = _fixture()
    draft = compositor_to_json(fixture["confirmation"])
    del draft["insert_compile_confirmation_id"]
    del draft["confirmation_key"]
    del draft["preview_projection"]
    draft["target_region"].update(
        {
            "left": 0,
            "top": 0,
            "right": 32_768,
            "bottom": 32_768,
            "width": 32_768,
            "height": 32_768,
        }
    )
    del draft["target_region"]["target_region_id"]
    with pytest.raises(CompositorError) as target_error:
        seal_insert_compile_confirmation(draft)
    assert target_error.value.code == "target_region_mismatch"

    oversized_bundle = [*copy.deepcopy(fixture["artifacts"])]
    oversized_bundle.extend(copy.deepcopy(fixture["artifacts"][0]) for _ in range(65))
    with pytest.raises(CompositorError) as bundle_error:
        compose_source_backed_rect_replace(
            fixture["source"],
            fixture["insert"],
            fixture["mask"],
            intent_packet=copy.deepcopy(fixture["packet"]),
            provider_artifacts=oversized_bundle,
            raw_output_occurrence=copy.deepcopy(fixture["occurrence"]),
            raw_output_bytes=fixture["raw_png"].png_bytes,
            raw_output_raster=fixture["raw"],
            raw_structural_judgment=fixture["structural"].judgment,
            raw_locality_judgment=fixture["raw_locality"],
            confirmation=fixture["confirmation"],
        )
    assert bundle_error.value.code == "provider_bundle_invalid"


def test_composition_rejects_same_count_nonrectangular_mask() -> None:
    fixture = _fixture(nonrectangular_mask=True)

    with pytest.raises(CompositorError) as error:
        compose_source_backed_rect_replace(
            fixture["source"],
            fixture["insert"],
            fixture["mask"],
            intent_packet=copy.deepcopy(fixture["packet"]),
            provider_artifacts=copy.deepcopy(fixture["artifacts"]),
            raw_output_occurrence=copy.deepcopy(fixture["occurrence"]),
            raw_output_bytes=fixture["raw_png"].png_bytes,
            raw_output_raster=fixture["raw"],
            raw_structural_judgment=fixture["structural"].judgment,
            raw_locality_judgment=fixture["raw_locality"],
            confirmation=fixture["confirmation"],
        )

    assert error.value.code == "mask_mismatch"


def test_compositor_replay_is_exact_and_conflicts_fail_closed() -> None:
    fixture = _fixture()
    result = compose_source_backed_rect_replace(
        fixture["source"],
        fixture["insert"],
        fixture["mask"],
        intent_packet=copy.deepcopy(fixture["packet"]),
        provider_artifacts=copy.deepcopy(fixture["artifacts"]),
        raw_output_occurrence=copy.deepcopy(fixture["occurrence"]),
        raw_output_bytes=fixture["raw_png"].png_bytes,
        raw_output_raster=fixture["raw"],
        raw_structural_judgment=fixture["structural"].judgment,
        raw_locality_judgment=fixture["raw_locality"],
        confirmation=fixture["confirmation"],
    )
    independent = compose_source_backed_rect_replace(
        fixture["source"],
        fixture["insert"],
        fixture["mask"],
        intent_packet=copy.deepcopy(fixture["packet"]),
        provider_artifacts=copy.deepcopy(fixture["artifacts"]),
        raw_output_occurrence=copy.deepcopy(fixture["occurrence"]),
        raw_output_bytes=bytes(fixture["raw_png"].png_bytes),
        raw_output_raster=fixture["raw"],
        raw_structural_judgment=fixture["structural"].judgment,
        raw_locality_judgment=fixture["raw_locality"],
        confirmation=fixture["confirmation"],
    )
    assert independent.png == result.png
    assert independent.output_raster == result.output_raster
    assert independent.occurrence == result.occurrence

    assert (
        resolve_compositor_replay(
            result.occurrence,
            result.occurrence,
            existing_png_bytes=result.png.png_bytes,
            candidate_png_bytes=result.png.png_bytes,
        )
        is result.occurrence
    )
    with pytest.raises(CompositorError) as conflict:
        resolve_compositor_replay(
            result.occurrence,
            result.occurrence,
            existing_png_bytes=result.png.png_bytes,
            candidate_png_bytes=result.png.png_bytes[:-1] + b"x",
        )
    assert conflict.value.code in {"identity_mismatch", "png_profile_mismatch"}

    altered_rgb = bytearray(result.output_raster.rgb_bytes)
    altered_rgb[15] ^= 1
    altered_png, altered_raster = _compile_rgb(4, 3, bytes(altered_rgb))
    altered_draft = compositor_to_json(result.occurrence)
    del altered_draft["output_occurrence_id"]
    del altered_draft["compositor_replay_key"]
    altered_draft["original"] = compositor_to_json(altered_png)
    altered_draft["output_raster"] = _raster_document(altered_raster)
    altered_occurrence = seal_compositor_output_occurrence(
        altered_draft,
        png_bytes=altered_png.png_bytes,
        output_raster=altered_raster,
    )
    assert altered_occurrence.compositor_replay_key == result.occurrence.compositor_replay_key
    assert altered_occurrence.output_occurrence_id != result.occurrence.output_occurrence_id
    with pytest.raises(CompositorError) as semantic_conflict:
        resolve_compositor_replay(
            result.occurrence,
            altered_occurrence,
            existing_png_bytes=result.png.png_bytes,
            candidate_png_bytes=altered_png.png_bytes,
        )
    assert semantic_conflict.value.code == "replay_conflict"

    impossible_descriptor = compositor_to_json(result.occurrence)
    impossible_descriptor["original"]["byte_count"] = 1
    impossible_descriptor["output_raster"]["byte_count"] = 1
    impossible_descriptor["output_occurrence_id"] = compute_compositor_occurrence_id(
        impossible_descriptor
    )
    with pytest.raises(CompositorError) as descriptor_error:
        compositor_from_json(impossible_descriptor)
    assert descriptor_error.value.code == "output_binding_mismatch"

    forged_document = compositor_to_json(result.occurrence)
    forged_document["lineage"]["raw_structural_evidence_id"] = "e" * 64
    forged_document["output_raster"]["raster_sha256"] = "f" * 64
    forged_document["compositor_replay_key"] = compute_compositor_replay_key(forged_document)
    forged_document["output_occurrence_id"] = compute_compositor_occurrence_id(forged_document)
    forged_candidate = compositor_from_json(forged_document)
    assert isinstance(forged_candidate, CompositorOutputOccurrence)
    with pytest.raises(CompositorError) as forged_raster_error:
        resolve_compositor_replay(
            result.occurrence,
            forged_candidate,
            existing_png_bytes=result.png.png_bytes,
            candidate_png_bytes=result.png.png_bytes,
        )
    assert forged_raster_error.value.code == "output_binding_mismatch"

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from moodboard.firefly_viewer import (
    FireflyViewerBridgeError,
    compile_viewer_firefly_bridge,
    read_viewer_firefly_bridge,
    validate_viewer_firefly_bridge,
)

ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "viewer" / "src" / "generated" / "firefly-bridge.json"
FIREFLY = ROOT / ".cache" / "showcase-firefly-v1"
KHIVE = ROOT / ".cache" / "showcase-firefly-khive-v1" / "evidence"


def _bridge() -> dict:
    return read_viewer_firefly_bridge(BRIDGE)


def _compile_kwargs() -> dict:
    projection = ROOT / "eval" / "showcase_firefly_projection.py"
    return {
        "replace_evidence": FIREFLY / "evidence.json",
        "restyle_evidence": FIREFLY / "restyle-evidence.json",
        "khive_evidence": KHIVE / "04-firefly-verification.summary.json",
        "raw_output": FIREFLY / "replace-gemini25-iteration-02.png",
        "selected_output": FIREFLY / "replace-gemini25-iteration-02-cutout-composite-v5.png",
        "restyle_output": FIREFLY / "restyle-gemini25-iteration-01.png",
        "ingest_command": KHIVE / "01-firefly-ingest.command.json",
        "ingest_results": KHIVE / "01-firefly-ingest.results.jsonl",
        "search_command": KHIVE / "02-firefly-search.command.json",
        "search_results": KHIVE / "02-firefly-search.results.jsonl",
        "restart_command": KHIVE / "03-firefly-restart-search.command.json",
        "restart_results": KHIVE / "03-firefly-restart-search.results.jsonl",
        "projection_revision": "moodboard.showcase-firefly-frozen-projection.v1",
        "projection_sha256": hashlib.sha256(projection.read_bytes()).hexdigest(),
    }


def test_frozen_firefly_bridge_preserves_measured_loop_and_khive_restart() -> None:
    bridge = _bridge()

    assert bridge["state"] == "projected"
    assert bridge["format_version"] == "moodboard.viewer-firefly-measured-loop-bridge.v2"
    assert bridge["generator_revision"] == "moodboard.firefly-viewer-bridge.v2"
    assert bridge["inputs"] == {
        "khive_evidence": {
            "byte_size": 4761,
            "schema_version": "moodboard.firefly-khive-evidence.v1",
            "sha256": "86c31855c9c2107d2688a54173434ad17292eb24a86484a613031602d9453abb",
        },
        "replace_evidence": {
            "byte_size": 18528,
            "schema_version": "moodboard.firefly-iteration-evidence.v1",
            "sha256": "a55e262a0d2752f7946028248359842864ec26932d66f4e6b76eb2233bf3fce5",
        },
        "restyle_evidence": {
            "byte_size": 2799,
            "schema_version": "moodboard.firefly-restyle-evidence.v1",
            "sha256": "9d7f75fdc63de6147a97d326dc01b85602e45474fb40c662f8352891bf0129c7",
        },
    }
    evidence = bridge["evidence"]
    assert evidence["source"] == {
        "asset_id": "fruit_apple_garden",
        "byte_size": 645201,
        "content_ref": "d9c1a0e3e6a5a72a9da252a0ea9fb4616c9099dd20cdc65ea00ffc29d14f23a8",
        "height": 960,
        "mime": "image/jpeg",
        "sha256": "3bda38b4304152f813f6bea37dc236f95670fbea5da4731903d9ce8cfaa8ae23",
        "width": 1280,
    }
    assert evidence["capture"] == {
        "authenticated_session": True,
        "cost_display": "Uses 0 credits",
        "model": "Gemini 2.5 (Nano Banana)",
        "native_firefly_api": False,
        "provider_boundary": "Google partner model served through Adobe Firefly",
        "surface": "Adobe Firefly web Edit > Prompt",
    }
    assert evidence["khive"]["assets"] == [
        {
            "content_ref": "4c052e6cff3913a4949fe2bef13331c56ac32f34d14b31e27e4e295f7884c052",
            "embedding_dimensions": 1024,
            "output_sha256": "8e33e4e6485ab776d5794cfa8ebba2f20687dfc391de1cd97d587bb4e3632f27",
            "record_id": "d49dd28a-fdb7-47f6-9e7a-2730c4ac3892",
            "role": "raw",
        },
        {
            "content_ref": "7ad97f3c2cf9e7f7b5236369d57f922f2897dd0bad55122d26bc4eb8f6e7cc47",
            "embedding_dimensions": 1024,
            "output_sha256": "53b601c226fa9997fcce2e7e8bfeb80f4a1e6322d25e7d5293ea4436c2c9d35d",
            "record_id": "5b8ce0e1-d2ff-4164-be8c-fa0eff456521",
            "role": "selected",
        },
        {
            "content_ref": "00a768f43e854cafc0af2df67ebf70f5e439531951cc591f21432860ba1f07cb",
            "embedding_dimensions": 1024,
            "output_sha256": "930dd8ddfb4fafcf724027fbeee652f19fe56233994926dc0f7a44186510b45a",
            "record_id": "532ed912-0811-4c3e-b6bf-aa6cc3979348",
            "role": "restyle",
        },
    ]
    assert evidence["replacement"]["retrieved_reference"] == {
        "asset_id": "fruit_lemon_santa_clara",
        "content_ref": "cf72f06b425eb52039d6926e057f7f5720f16435341625ce2fc9b92f5b52069d",
        "direct_generator_reference": False,
        "license_id": "CC0-1.0",
        "raw_cosine": 0.843299582601,
        "sha256": "d53ca28eb2d59727fc577118d2d23dd0a16af8f0b8670d54fec6993428d71429",
    }
    assert evidence["replacement"]["compositor_exact_outside_mask"] == {
        "changed_pixel_count": 0,
        "comparison": "decoded_rgb_u8_outside_mask",
        "mask": {
            "bounds_half_open_source_pixels": {
                "x0_inclusive": 230,
                "x1_exclusive": 1152,
                "y0_inclusive": 48,
                "y1_exclusive": 912,
            },
            "encoding": "u8_row_major_1_inside_0_outside",
            "height": 960,
            "inside_pixel_count": 796608,
            "outside_pixel_count": 432192,
            "sha256": "09f9072f646ef8d99af30736210a57f2de448e8ca90fbff07a07edd7bd5eef4b",
            "width": 1280,
        },
        "max_abs_channel_error": 0,
        "result": "pass",
        "selected_output_sha256": (
            "53b601c226fa9997fcce2e7e8bfeb80f4a1e6322d25e7d5293ea4436c2c9d35d"
        ),
        "semantics": "deterministic_compositor_invariant_not_generator_locality",
        "source_sha256": ("3bda38b4304152f813f6bea37dc236f95670fbea5da4731903d9ce8cfaa8ae23"),
    }
    assert evidence["replacement"]["timeline"][0]["output_sha256"] == (
        "76abe16ec31fdfa4448094fc27e9b62debf933c566973264719722efc7f9acef"
    )
    raw, selected = evidence["replacement"]["timeline"][-2:]
    assert (raw["output_sha256"], raw["decision"], raw["outside_mask_ssim"]) == (
        "8e33e4e6485ab776d5794cfa8ebba2f20687dfc391de1cd97d587bb4e3632f27",
        "fail",
        0.174819482254,
    )
    assert (
        selected["output_sha256"],
        selected["decision"],
        selected["outside_mask_ssim"],
        selected["pass_semantics"],
    ) == (
        "53b601c226fa9997fcce2e7e8bfeb80f4a1e6322d25e7d5293ea4436c2c9d35d",
        "pass",
        1.0,
        "deterministic_preservation_constraint_not_intrinsic_generator_locality",
    )
    assert evidence["restyle"]["output_sha256"] == (
        "930dd8ddfb4fafcf724027fbeee652f19fe56233994926dc0f7a44186510b45a"
    )
    assert evidence["restyle"]["acceptance_decision"] == "not_computed"
    assert evidence["restyle"]["diagnostics"] == {
        "aligned_pixel_rgb_cosine": 0.899155132364,
        "horizontal_luma_gradient_cosine": 0.003698292898,
        "vertical_luma_gradient_cosine": 0.003281997953,
    }
    assert (
        "The premium Gemini 3.1 model was not used in this measured capture."
        in evidence["nonclaims"]
    )
    assert evidence["khive"]["descriptor"]["inference"] == {
        "provider": "lattice-embed",
        "version": "0.9.0",
    }
    assert evidence["khive"]["restart"] == {
        "canonical_search_byte_exact": True,
        "first_search_sha256": "18f9f1b4cd289834ee6aaa50d4f5076c1bd048edea3b0e3d94ff0c99fedd1b48",
        "restart_search_sha256": "18f9f1b4cd289834ee6aaa50d4f5076c1bd048edea3b0e3d94ff0c99fedd1b48",
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update({"future": True}), "closed"),
        (
            lambda value: value["evidence"]["capture"].update({"native_firefly_api": True}),
            "native Firefly API",
        ),
        (
            lambda value: value["evidence"]["replacement"]["timeline"][-2].update(
                {"decision": "pass"}
            ),
            "raw replacement",
        ),
        (
            lambda value: value["evidence"]["replacement"]["timeline"][-1].update(
                {"pass_semantics": "intrinsic_generator_locality"}
            ),
            "deterministic preservation",
        ),
        (
            lambda value: value["evidence"]["restyle"].update({"acceptance_decision": "pass"}),
            "not_computed",
        ),
        (
            lambda value: value["evidence"]["khive"]["restart"].update(
                {"canonical_search_byte_exact": False}
            ),
            "restart",
        ),
        (
            lambda value: value["evidence"]["replacement"]["timeline"][-1].update(
                {"content_ref": "f" * 64}
            ),
            "Khive asset",
        ),
        (
            lambda value: value["evidence"]["replacement"]["timeline"][0].update(
                {"output_sha256": "f" * 64}
            ),
            "structural replacement",
        ),
        (
            lambda value: value["evidence"]["replacement"]["retrieved_reference"].update(
                {"raw_cosine": 0.1}
            ),
            "retrieved reference",
        ),
        (
            lambda value: value["evidence"]["restyle"]["diagnostics"].update(
                {"aligned_pixel_rgb_cosine": 0.1}
            ),
            "restyle diagnostics",
        ),
        (
            lambda value: value["evidence"]["source"].update({"sha256": "f" * 64}),
            "source identity",
        ),
        (
            lambda value: value["evidence"]["source"].update({"content_ref": "f" * 64}),
            "source identity",
        ),
        (
            lambda value: value["evidence"]["source"].update({"width": 1279}),
            "source identity",
        ),
        (
            lambda value: value["evidence"]["source"].update({"width": 1280.0}),
            "source identity",
        ),
        (
            lambda value: value["evidence"]["replacement"]["compositor_exact_outside_mask"].update(
                {"changed_pixel_count": 1}
            ),
            "exact outside-mask",
        ),
        (
            lambda value: value["evidence"]["replacement"]["compositor_exact_outside_mask"].update(
                {"max_abs_channel_error": 1}
            ),
            "exact outside-mask",
        ),
        (
            lambda value: value["evidence"]["replacement"]["compositor_exact_outside_mask"].update(
                {"source_sha256": "f" * 64}
            ),
            "exact outside-mask",
        ),
        (
            lambda value: value["evidence"]["replacement"]["compositor_exact_outside_mask"][
                "mask"
            ].update({"sha256": "f" * 64}),
            "exact outside-mask",
        ),
    ],
)
def test_firefly_bridge_rejects_claim_identity_and_restart_drift(mutate, message: str) -> None:
    drifted = copy.deepcopy(_bridge())
    mutate(drifted)

    with pytest.raises(FireflyViewerBridgeError, match=message):
        validate_viewer_firefly_bridge(drifted)


def test_firefly_bridge_rejects_preview_and_bridge_identity_drift() -> None:
    drifted_preview = copy.deepcopy(_bridge())
    drifted_preview["evidence"]["replacement"]["timeline"][-2]["preview"]["sha256"] = "f" * 64
    with pytest.raises(FireflyViewerBridgeError, match="preview"):
        validate_viewer_firefly_bridge(drifted_preview)

    drifted_id = copy.deepcopy(_bridge())
    drifted_id["bridge_id"] = "f" * 64
    with pytest.raises(FireflyViewerBridgeError, match="bridge_id"):
        validate_viewer_firefly_bridge(drifted_id)


def test_firefly_bridge_is_canonical_and_contains_no_local_paths() -> None:
    raw = BRIDGE.read_bytes()
    bridge = json.loads(raw)

    assert raw == (
        json.dumps(bridge, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    assert b"/Users/" not in raw
    assert b"local_file" not in raw


def test_firefly_projector_fails_closed_when_exact_source_bytes_are_unavailable(
    tmp_path: Path,
) -> None:
    missing_source = tmp_path / "missing-source.jpg"

    with pytest.raises(FireflyViewerBridgeError, match="exact locality source is unavailable"):
        compile_viewer_firefly_bridge(**_compile_kwargs(), source_image=missing_source)

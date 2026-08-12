"""Contract tests for the intent-scoped Pixel RAG artifact compiler."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from blake3 import blake3
from PIL import Image

from moodboard.pixel_rag import (
    ExternalOutput,
    PixelRagError,
    compile_pixel_rag_artifact,
    read_pixel_rag_artifact,
    write_pixel_rag_artifact,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _record_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://moodboard.test/pixel-rag/{name}"))


def _descriptor() -> dict:
    identity = {
        "schema_version": "moodboard.visual-descriptor.v1",
        "model_name": "qwen3.5-vlm-pooled-visual",
        "model_revision": "fixture-qwen3.5-0.8b",
        "checkpoint_sha256": "1" * 64,
        "inference": {"provider": "lattice-embed", "version": "0.9.0"},
        "preprocessing": {
            "revision": "moodboard-qwen35-srgb-pad32-max448-v1",
            "max_side": 448,
            "alignment": 32,
            "matte_rgb": [128, 128, 128],
            "resample": "lanczos3",
        },
        "prompt": {
            "revision": "moodboard-style-retrieval-v1",
            "sha256": "a67ae9b539c243f498c75f1ea9f19e7018860948087728d6f8e65b34eef6a66e",
        },
        "pooling": "mean_visual_tokens",
        "dimensions": 4,
        "normalization": "l2",
    }
    fingerprint = hashlib.sha256(_canonical(identity).encode()).hexdigest()
    return {
        **identity,
        "model_key": f"moodboard_{fingerprint}_4",
        "fingerprint": fingerprint,
    }


def _image(path: Path, color: tuple[int, int, int], *, size=(12, 9)) -> bytes:
    Image.new("RGB", size, color).save(path, format="PNG", compress_level=9)
    return path.read_bytes()


def _manifest(tmp_path: Path) -> tuple[Path, dict[str, dict]]:
    run = tmp_path / "run"
    assets_dir = run / "assets"
    assets_dir.mkdir(parents=True)
    specifications = [
        ("fruit_apple_garden", "fruit-apple", (55, 120, 30), "PDM-1.0"),
        ("fruit_apple_meadow", "fruit-apple", (75, 145, 55), "CC0-1.0"),
        ("fruit_lemon_argos", "fruit-lemon", (190, 185, 40), "CC0-1.0"),
        ("fruit_lemon_detail", "fruit-lemon", (220, 205, 35), "PDM-1.0"),
        ("fruit_lemon_menton", "fruit-lemon", (175, 170, 25), "PDM-1.0"),
        ("style_claude_ford", "style-claude-lorrain", (145, 110, 70), "CC0-1.0"),
        ("style_claude_pastoral", "style-claude-lorrain", (170, 135, 90), "CC0-1.0"),
        ("style_claude_sunrise", "style-claude-lorrain", (210, 155, 80), "CC0-1.0"),
        ("style_vangogh_cypresses", "style-vincent-van-gogh", (25, 60, 90), "CC0-1.0"),
    ]
    assets = []
    by_id: dict[str, dict] = {}
    for asset_id, collection, color, license_id in specifications:
        relative = f"assets/{asset_id}.png"
        data = _image(run / relative, color)
        license_name = "Public domain" if license_id == "PDM-1.0" else "CC0"
        row = {
            "artist": "Fixture artist",
            "asset_id": asset_id,
            "byte_size": len(data),
            "collection": collection,
            "download_url": f"https://example.test/download/{asset_id}.png",
            "http": {
                "content_type": "image/png",
                "etag": None,
                "final_url": f"https://example.test/media/{asset_id}.png",
                "last_modified": None,
            },
            "image": {"format": "PNG", "height": 9, "width": 12},
            "khive_content_ref": blake3(data).hexdigest(),
            "license": {
                "id": license_id,
                "public_domain": True,
                "short_name": license_name,
                "source_url": None,
                "url": (
                    "https://creativecommons.org/publicdomain/mark/1.0/"
                    if license_id == "PDM-1.0"
                    else "https://creativecommons.org/publicdomain/zero/1.0/"
                ),
            },
            "local_path": relative,
            "metadata": {
                "etag": None,
                "evidence_sha256": hashlib.sha256(f"metadata:{asset_id}".encode()).hexdigest(),
                "url": f"https://example.test/metadata/{asset_id}",
            },
            "object_id": None,
            "provider": "wikimedia_commons",
            "retrieved_at": "2026-08-12T17:00:00Z",
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_page_url": f"https://example.test/source/{asset_id}",
            "title": asset_id.replace("_", " ").title(),
        }
        assets.append(row)
        by_id[asset_id] = row
    manifest = {
        "asset_count": len(assets),
        "assets": assets,
        "catalog_sha256": "2" * 64,
        "dataset_id": "adobe-interview-public-domain-v1",
        "retrieved_at": "2026-08-12T17:00:00Z",
        "schema_version": "moodboard.demo-manifest.v1",
    }
    path = run / "manifest.json"
    path.write_text(_canonical(manifest) + "\n", encoding="utf-8")
    (run / "manifest.sha256").write_text(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  manifest.json\n", encoding="utf-8"
    )
    return path, by_id


def _hits(rows: list[dict], scores: dict[str, float]) -> list[dict]:
    ordered = sorted(rows, key=lambda row: (-scores[row["asset_id"]], row["asset_id"]))
    return [
        {
            "record_id": _record_id(row["asset_id"]),
            "content_ref": row["khive_content_ref"],
            "rank": index,
            "score": scores[row["asset_id"]],
        }
        for index, row in enumerate(ordered, 1)
    ]


def _measurements(path: Path, by_id: dict[str, dict], *, status="contract_fixture") -> Path:
    all_rows = list(by_id.values())
    replace_scores = {
        row["asset_id"]: score
        for row, score in zip(
            all_rows,
            [0.99, 0.98, 0.91, 0.82, 0.87, 0.30, 0.28, 0.25, 0.20],
            strict=True,
        )
    }
    restyle_scores = {
        row["asset_id"]: score
        for row, score in zip(
            all_rows,
            [0.95, 0.70, 0.25, 0.22, 0.20, 0.84, 0.92, 0.88, 0.86],
            strict=True,
        )
    }
    source = by_id["fruit_apple_garden"]
    region_bytes = b"fixture-derived-tree-region"
    payload = {
        "schema_version": "moodboard.pixel-rag-measurements.v1",
        "evidence_status": status,
        "generated_at": "2026-08-12T19:00:00Z",
        "khive_revision": "5e7e73c0e7d8868c6a7aabbde3124ecb42289acc",
        "descriptor": _descriptor(),
        "source_record": {
            "record_id": _record_id("fruit_apple_garden"),
            "content_ref": source["khive_content_ref"],
        },
        "intents": [
            {
                "id": "local_replace",
                "namespace": "adobe-demo:pixel-rag:replace",
                "designer_prompt": (
                    "Replace only the selected apple tree with a mature lemon tree; preserve "
                    "the water, ground, camera, lighting, and every pixel outside the selection."
                ),
                "query_record": {
                    "record_id": _record_id("apple-tree-region"),
                    "content_ref": blake3(region_bytes).hexdigest(),
                    "sha256": hashlib.sha256(region_bytes).hexdigest(),
                },
                "region": {
                    "kind": "normalized_rectangle",
                    "x": 0.04,
                    "y": 0.02,
                    "width": 0.93,
                    "height": 0.94,
                    "label": "primary apple tree canopy and trunk",
                    "confirmation": {
                        "method": "human_confirmed",
                        "actor": "operator:adobe-demo",
                        "confirmed_at": "2026-08-12T18:55:00Z",
                    },
                },
                "hits": _hits(all_rows, replace_scores),
                "relevance_judgments": [
                    {"content_ref": by_id["fruit_lemon_argos"]["khive_content_ref"], "gain": 3},
                    {"content_ref": by_id["fruit_lemon_detail"]["khive_content_ref"], "gain": 1},
                    {"content_ref": by_id["fruit_lemon_menton"]["khive_content_ref"], "gain": 2},
                ],
                "verification_metrics": [],
                "generator": {
                    "provider": "openai-imagegen",
                    "service": "Codex built-in ImageGen",
                    "execution_mode": "precomputed_external",
                },
            },
            {
                "id": "global_restyle",
                "namespace": "adobe-demo:pixel-rag:restyle",
                "designer_prompt": (
                    "Restyle the whole frame as a luminous Claude Lorrain pastoral oil "
                    "painting while preserving the source composition and subject layout."
                ),
                "query_record": {
                    "record_id": _record_id("fruit_apple_garden"),
                    "content_ref": source["khive_content_ref"],
                    "sha256": source["sha256"],
                },
                "region": None,
                "hits": _hits(all_rows, restyle_scores),
                "relevance_judgments": [
                    {"content_ref": by_id["style_claude_ford"]["khive_content_ref"], "gain": 2},
                    {
                        "content_ref": by_id["style_claude_pastoral"]["khive_content_ref"],
                        "gain": 3,
                    },
                    {"content_ref": by_id["style_claude_sunrise"]["khive_content_ref"], "gain": 2},
                ],
                "verification_metrics": [],
                "generator": {
                    "provider": "openai-imagegen",
                    "service": "Codex built-in ImageGen",
                    "execution_mode": "precomputed_external",
                },
            },
        ],
    }
    path.write_text(_canonical(payload) + "\n", encoding="utf-8")
    return path


def _outputs(tmp_path: Path) -> dict[str, ExternalOutput]:
    local = tmp_path / "apple-to-lemon.png"
    global_style = tmp_path / "apple-classical-restyle.png"
    _image(local, (175, 180, 35), size=(16, 12))
    _image(global_style, (160, 115, 70), size=(16, 12))
    return {
        "local_replace": ExternalOutput(path=local),
        "global_restyle": ExternalOutput(path=global_style),
    }


def _metric(intent: dict, metric_id: str) -> dict:
    return next(metric for metric in intent["retrieval"]["metrics"] if metric["id"] == metric_id)


def test_compile_routes_one_source_through_two_grounded_intent_plans(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
        external_outputs=_outputs(tmp_path),
    )

    assert artifact["schema_version"] == "moodboard.pixel-rag-artifact.v1"
    assert all(len(digest) == 64 for digest in artifact["contracts"].values())
    assert artifact["evidence_status"] == "contract_fixture"
    assert artifact["source"]["asset_id"] == "fruit_apple_garden"
    assert artifact["source"]["khive"]["content_ref"] == by_id["fruit_apple_garden"][
        "khive_content_ref"
    ]
    assert artifact["descriptor"]["inference"] == {
        "provider": "lattice-embed",
        "version": "0.9.0",
    }

    local, restyle = artifact["intents"]
    assert [local["id"], restyle["id"]] == ["local_replace", "global_restyle"]
    assert local["route"]["query_granularity"] == "human_confirmed_region"
    assert local["route"]["region"]["confirmation"]["method"] == "human_confirmed"
    assert local["route"]["hard_filter"] == {
        "field": "collection",
        "operator": "equals",
        "value": "fruit-lemon",
    }
    assert restyle["route"]["query_granularity"] == "whole_frame"
    assert restyle["route"]["region"] is None
    assert restyle["route"]["hard_filter"]["value"] == "style-claude-lorrain"
    assert {hit["collection"] for hit in local["retrieval"]["ranked_evidence"]} == {
        "fruit-lemon"
    }
    assert {hit["collection"] for hit in restyle["retrieval"]["ranked_evidence"]} == {
        "style-claude-lorrain"
    }
    assert [row["asset_id"] for row in local["retrieval"]["exact_score_order"]] == [
        "fruit_lemon_argos",
        "fruit_lemon_menton",
        "fruit_lemon_detail",
    ]
    assert _metric(local, "precision_at_3")["value"] == pytest.approx(1.0)
    assert _metric(local, "ndcg_at_5")["state"] == "computed"
    assert all(metric["source"] == "contract_fixture" for metric in local["retrieval"]["metrics"])
    assert artifact["cross_intent_metrics"] == [
        {
            "id": "intent_top3_jaccard",
            "intersection_count": 0,
            "union_count": 6,
            "value": 0.0,
            "source": "contract_fixture",
        }
    ]
    expected_stages = [
        "retrieval",
        "region",
        "conditioning",
        "external_generation",
        "verification",
        "immutable_output",
        "rollback",
    ]
    assert [stage["id"] for stage in local["plan"]["stages"]] == expected_stages
    assert local["plan"]["stages"][3]["executor"] == "external_generator"
    assert local["verification"] == {"metrics": [], "status": "not_run"}
    assert local["output"]["state"] == "precomputed_external_output"
    assert local["output"]["blob_store_registration"] == {"state": "not_registered"}
    assert local["output"]["rollback"]["content_ref"] == artifact["source"]["khive"][
        "content_ref"
    ]
    assert len(artifact["artifact_id"]) == 64


def test_measured_run_does_not_invent_relevance_or_edit_verification(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(
        tmp_path / "measurements.json", by_id, status="measured_run"
    )
    measurements = json.loads(measurements_path.read_text())
    for intent in measurements["intents"]:
        intent["relevance_judgments"] = None
        intent["verification_metrics"] = []
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    assert artifact["evidence_status"] == "measured_run"
    for intent in artifact["intents"]:
        assert intent["output"] is None
        assert intent["verification"] == {"metrics": [], "status": "not_run"}
        assert intent["retrieval"]["metrics"] == [
            {
                "id": "precision_at_3",
                "reason": "explicit relevance judgments were not supplied",
                "source": "measured_run",
                "state": "not_computed",
                "value": None,
            },
            {
                "id": "ndcg_at_5",
                "reason": "explicit relevance judgments were not supplied",
                "source": "measured_run",
                "state": "not_computed",
                "value": None,
            },
        ]


def test_compiler_rejects_unclosed_inputs_descriptor_drift_and_unknown_assets(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    measurements = json.loads(measurements_path.read_text())
    measurements["surprise"] = True
    measurements_path.write_text(_canonical(measurements), encoding="utf-8")
    with pytest.raises(PixelRagError, match="unknown|Additional properties"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path, measurements_path=measurements_path
        )

    measurements.pop("surprise")
    measurements["descriptor"]["inference"]["version"] = "0.8.0"
    measurements_path.write_text(_canonical(measurements), encoding="utf-8")
    with pytest.raises(PixelRagError, match="0.9.0"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path, measurements_path=measurements_path
        )

    measurements["descriptor"] = _descriptor()
    measurements["intents"][0]["hits"][0]["content_ref"] = "f" * 64
    measurements_path.write_text(_canonical(measurements), encoding="utf-8")
    with pytest.raises(PixelRagError, match="not present in the governed manifest"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path, measurements_path=measurements_path
        )


def test_compiler_rejects_score_reordering_and_manifest_byte_tampering(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    measurements = json.loads(measurements_path.read_text())
    measurements["intents"][0]["hits"][0]["score"] = -0.9
    measurements_path.write_text(_canonical(measurements), encoding="utf-8")
    with pytest.raises(PixelRagError, match="score order"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path, measurements_path=measurements_path
        )

    _measurements(measurements_path, by_id)
    source_path = manifest_path.parent / by_id["fruit_apple_garden"]["local_path"]
    source_path.write_bytes(source_path.read_bytes() + b"tamper")
    with pytest.raises(PixelRagError, match="SHA-256 mismatch"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path, measurements_path=measurements_path
        )


def test_explicit_verification_is_derived_and_output_registration_is_bound(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    measurements = json.loads(measurements_path.read_text())
    measurements["intents"][0]["verification_metrics"] = [
        {
            "id": "outside_mask_ssim",
            "method_revision": "moodboard.outside-mask-ssim.v1",
            "operator": "greater_than_or_equal",
            "threshold": 0.95,
            "value": 0.94,
        }
    ]
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")
    outputs = _outputs(tmp_path)
    local_bytes = outputs["local_replace"].path.read_bytes()
    outputs["local_replace"] = ExternalOutput(
        path=outputs["local_replace"].path,
        khive_record_id=_record_id("generated-lemon"),
        expected_content_ref=blake3(local_bytes).hexdigest(),
    )

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
        external_outputs=outputs,
    )

    metric = artifact["intents"][0]["verification"]["metrics"][0]
    assert metric["passed"] is False
    assert artifact["intents"][0]["verification"]["status"] == "failed"
    assert artifact["intents"][0]["output"]["blob_store_registration"] == {
        "content_ref": blake3(local_bytes).hexdigest(),
        "record_id": _record_id("generated-lemon"),
        "state": "registered",
    }

    outputs["local_replace"] = ExternalOutput(
        path=outputs["local_replace"].path,
        khive_record_id=_record_id("generated-lemon"),
        expected_content_ref="a" * 64,
    )
    with pytest.raises(PixelRagError, match="expected Khive content_ref"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path,
            measurements_path=measurements_path,
            external_outputs=outputs,
        )


def test_frozen_artifact_round_trip_is_atomic_closed_and_self_identifying(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
        external_outputs=_outputs(tmp_path),
    )
    destination = tmp_path / "pixel-rag.json"

    write_pixel_rag_artifact(artifact, destination)
    assert read_pixel_rag_artifact(destination) == artifact
    with pytest.raises(FileExistsError):
        write_pixel_rag_artifact(artifact, destination)

    document = json.loads(destination.read_text())
    document["unknown"] = True
    destination.write_text(_canonical(document), encoding="utf-8")
    with pytest.raises(PixelRagError, match="unknown|Additional properties"):
        read_pixel_rag_artifact(destination)

    document.pop("unknown")
    document["source"]["title"] = "tampered"
    destination.write_text(_canonical(document), encoding="utf-8")
    with pytest.raises(PixelRagError, match="artifact_id"):
        read_pixel_rag_artifact(destination)


def test_viewer_bridge_embeds_one_validated_canonical_artifact_with_input_pins(
    tmp_path: Path,
) -> None:
    from moodboard.pixel_rag_viewer import (
        compile_viewer_pixel_rag_bridge,
        validate_viewer_pixel_rag_bridge,
    )

    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
        external_outputs=_outputs(tmp_path),
    )
    source = tmp_path / "pixel-rag.json"
    write_pixel_rag_artifact(artifact, source)

    bridge = compile_viewer_pixel_rag_bridge(source)

    expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    assert bridge["format_version"] == "moodboard.viewer-pixel-rag-bridge.v1"
    assert bridge["generator_revision"] == "moodboard.pixel-rag-viewer-bridge.v1"
    assert bridge["state"] == "projected"
    assert bridge["input"] == {
        "artifact_id": artifact["artifact_id"],
        "byte_size": source.stat().st_size,
        "canonical_sha256": expected_sha256,
        "schema_version": "moodboard.pixel-rag-artifact.v1",
        "sha256": expected_sha256,
    }
    assert bridge["artifact"] == artifact
    validate_viewer_pixel_rag_bridge(bridge)


def test_public_artifact_validator_is_the_cross_module_contract(tmp_path: Path) -> None:
    from moodboard.pixel_rag import validate_pixel_rag_artifact

    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    validate_pixel_rag_artifact(artifact)

    drifted = json.loads(json.dumps(artifact))
    drifted["source"]["title"] = "drifted after compile"
    with pytest.raises(PixelRagError, match="artifact_id"):
        validate_pixel_rag_artifact(drifted)


def test_viewer_bridge_fails_closed_on_noncanonical_input_and_generated_drift(
    tmp_path: Path,
) -> None:
    from moodboard.pixel_rag_viewer import (
        PixelRagViewerBridgeError,
        compile_viewer_pixel_rag_bridge,
        validate_viewer_pixel_rag_bridge,
    )

    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )
    pretty = tmp_path / "pretty-pixel-rag.json"
    pretty.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    with pytest.raises(PixelRagViewerBridgeError, match="canonical"):
        compile_viewer_pixel_rag_bridge(pretty)

    source = tmp_path / "pixel-rag.json"
    write_pixel_rag_artifact(artifact, source)
    bridge = compile_viewer_pixel_rag_bridge(source)
    bridge["input"]["canonical_sha256"] = "f" * 64
    with pytest.raises(PixelRagViewerBridgeError, match="canonical_sha256"):
        validate_viewer_pixel_rag_bridge(bridge)

    bridge = compile_viewer_pixel_rag_bridge(source)
    bridge["artifact"]["surprise"] = True
    with pytest.raises(PixelRagError, match="unknown|Additional properties"):
        validate_viewer_pixel_rag_bridge(bridge)

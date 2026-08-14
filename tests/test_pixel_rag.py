"""Contract tests for the intent-scoped Pixel RAG artifact compiler."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from io import BytesIO
from pathlib import Path

import jsonschema
import pytest
from blake3 import blake3
from PIL import Image

from moodboard.pixel_rag import (
    ExternalOutput,
    PixelRagError,
    compile_pixel_rag_artifact,
    read_pixel_rag_artifact,
    validate_pixel_rag_artifact,
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
        "dataset_id": "showcase-public-domain-v1",
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
                "namespace": "showcase:pixel-rag:replace",
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
                        "actor": "operator:showcase",
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
                "namespace": "showcase:pixel-rag:restyle",
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


def _reidentify(artifact: dict) -> None:
    identity = {key: value for key, value in artifact.items() if key != "artifact_id"}
    artifact["artifact_id"] = hashlib.sha256(
        (_canonical(identity) + "\n").encode("utf-8")
    ).hexdigest()


def _legacy_run_fingerprint(artifact: dict) -> str:
    outputs = {
        intent["id"]: {
            "content_ref": intent["output"]["output_content_ref"],
            "sha256": intent["output"]["output_sha256"],
        }
        for intent in artifact["intents"]
        if intent["output"] is not None
    }
    return hashlib.sha256(
        (
            _canonical(
                {
                    "compiler_revision": "moodboard.pixel-rag-compiler.v1",
                    "manifest_sha256": artifact["source_manifest"]["manifest_sha256"],
                    "measurements_sha256": artifact["provenance"]["measurements_sha256"],
                    "outputs": outputs,
                }
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


def _current_run_fingerprint(artifact: dict) -> str:
    outputs = {
        intent["id"]: {
            "negative": [
                {
                    "content_ref": evidence["output"]["output_content_ref"],
                    "evidence_id": evidence["evidence_id"],
                    "sha256": evidence["output"]["output_sha256"],
                }
                for evidence in intent.get("negative_output_evidence", [])
            ],
            "selected": (
                {
                    "content_ref": intent["output"]["output_content_ref"],
                    "sha256": intent["output"]["output_sha256"],
                }
                if intent["output"] is not None
                else None
            ),
        }
        for intent in artifact["intents"]
    }
    return hashlib.sha256(
        (
            _canonical(
                {
                    "compiler_revision": artifact["provenance"]["compiler_revision"],
                    "contracts": artifact["contracts"],
                    "manifest_sha256": artifact["source_manifest"]["manifest_sha256"],
                    "measurements_sha256": artifact["provenance"]["measurements_sha256"],
                    "outputs": outputs,
                }
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


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
    assert (
        artifact["source"]["khive"]["content_ref"]
        == by_id["fruit_apple_garden"]["khive_content_ref"]
    )
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
    assert {hit["collection"] for hit in local["retrieval"]["ranked_evidence"]} == {"fruit-lemon"}
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
            "interpretation": ("structural_routing_control_not_learned_retrieval_quality"),
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
    assert local["output"]["rollback"]["content_ref"] == artifact["source"]["khive"]["content_ref"]
    assert len(artifact["artifact_id"]) == 64


def test_measured_run_does_not_invent_relevance_or_edit_verification(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id, status="measured_run")
    measurements = json.loads(measurements_path.read_text())
    for intent in measurements["intents"]:
        intent["relevance_judgments"] = None
        intent["verification_metrics"] = []
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")
    _add_evidence_bindings(measurements_path, tmp_path)

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    assert artifact["evidence_status"] == "measured_run"
    for intent in artifact["intents"]:
        assert intent["output"] is None
        assert intent["verification"] == {"metrics": [], "status": "not_run"}
        assert [metric["id"] for metric in intent["retrieval"]["metrics"]] == [
            "precision_at_3",
            "ndcg_at_5",
            "mrr",
            "recall_at_5",
        ]
        assert all(
            metric
            == {
                "id": metric["id"],
                "reason": "explicit relevance judgments were not supplied",
                "source": "measured_run",
                "state": "not_computed",
                "value": None,
            }
            for metric in intent["retrieval"]["metrics"]
        )


def test_retrieval_diagnostics_separate_raw_geometry_from_structural_routing(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    for intent in artifact["intents"]:
        retrieval = intent["retrieval"]
        assert retrieval["metrics_interpretation"] == (
            "structural_routing_control_not_learned_retrieval_quality"
        )
        raw = retrieval["raw_diagnostics"]
        assert raw["gate"] == "ungated"
        assert raw["interpretation"] == (
            "experimental_qwen_visual_embedding_geometry_not_probability"
        )
        assert len(raw["exact_score_order"]) == len(
            next(
                measured
                for measured in json.loads(measurements_path.read_text())["intents"]
                if measured["id"] == intent["id"]
            )["hits"]
        )
        assert [metric["id"] for metric in raw["metrics"]] == [
            "precision_at_3",
            "ndcg_at_5",
            "mrr",
            "recall_at_5",
        ]

    assert artifact["cross_intent_metrics"][0]["interpretation"] == (
        "structural_routing_control_not_learned_retrieval_quality"
    )


def _add_evidence_bindings(measurements_path: Path, tmp_path: Path) -> dict[str, Path]:
    measurements = json.loads(measurements_path.read_text())
    paths: dict[str, Path] = {}
    bindings = []
    for kind in (
        "evaluation_preregistration",
        "intent_freeze",
        "verification_summary",
    ):
        path = tmp_path / f"{kind}.json"
        path.write_text(_canonical({"evidence_kind": kind}) + "\n", encoding="utf-8")
        paths[kind] = path
        bindings.append(
            {
                "kind": kind,
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    measurements["evidence_bindings"] = bindings
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")
    return paths


def _experimental_diagnostics() -> dict:
    return {
        "contract": {
            "interpretation": (
                "descriptive diagnostic only; no calibrated semantic or style claim"
            ),
            "kind": "experimental_qwen_visual_embedding_cosine",
            "raw_cosine_is_probability": False,
            "validated_csd_or_style_probability": False,
        },
        "local_output_region_intent_alignment": {
            "mean_apple_cosine": 0.8773538152747281,
            "mean_lemon_cosine": 0.8791791857434634,
            "mean_lemon_minus_apple_margin": 0.0018253704687353212,
            "output_role": "local_output_region_diagnostic",
        },
        "restyle_content_retention": {
            "cosine": 0.8286733941440402,
            "output_role": "external_precomputed_global_restyle",
        },
        "restyle_style_affinity": {
            "claude_centroid_cosine": 0.847032431041006,
            "claude_minus_vangogh_margin": 0.000005733906261906618,
            "claude_reference_count": 4,
            "output_role": "external_precomputed_global_restyle",
            "vangogh_centroid_cosine": 0.8470266971347441,
            "vangogh_reference_count": 4,
        },
    }


def test_evidence_bindings_are_rehashed_and_frozen_into_provenance(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    paths = _add_evidence_bindings(measurements_path, tmp_path)

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    assert artifact["provenance"]["evidence_sha256"] == {
        kind: hashlib.sha256(path.read_bytes()).hexdigest() for kind, path in paths.items()
    }


def test_evidence_bound_region_confirmation_does_not_invent_actor_or_time(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    paths = _add_evidence_bindings(measurements_path, tmp_path)
    measurements = json.loads(measurements_path.read_text())
    measurements["intents"][0]["region"]["confirmation"] = {
        "evidence_kind": "evaluation_preregistration",
        "evidence_sha256": hashlib.sha256(
            paths["evaluation_preregistration"].read_bytes()
        ).hexdigest(),
        "json_pointer": "/intent_routes/0/query",
        "method": "evidence_bound_human_confirmation",
    }
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    assert artifact["intents"][0]["route"]["region"]["confirmation"] == {
        "evidence_kind": "evaluation_preregistration",
        "evidence_sha256": hashlib.sha256(
            paths["evaluation_preregistration"].read_bytes()
        ).hexdigest(),
        "json_pointer": "/intent_routes/0/query",
        "method": "evidence_bound_human_confirmation",
    }


def test_verification_can_bind_a_frozen_evidence_field_without_inventing_revision(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    paths = _add_evidence_bindings(measurements_path, tmp_path)
    measurements = json.loads(measurements_path.read_text())
    verification_sha = hashlib.sha256(paths["verification_summary"].read_bytes()).hexdigest()
    measurements["intents"][0]["verification_metrics"] = [
        {
            "evidence_field": {
                "evidence_kind": "verification_summary",
                "evidence_sha256": verification_sha,
                "json_pointer": (
                    "/experimental_visual_embedding_diagnostics/locality_pixel_evidence/"
                    "passing_mask_enforced_v3/outside_mask_ssim_luma_safe_window_mean"
                ),
            },
            "id": "outside_mask_ssim",
            "operator": "greater_than_or_equal",
            "threshold": 0.95,
            "value": 0.9996823620965922,
        }
    ]
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    metric = artifact["intents"][0]["verification"]["metrics"][0]
    assert "method_revision" not in metric
    assert metric["evidence_field"]["evidence_sha256"] == verification_sha


def test_projection_identity_is_preserved_in_artifact_provenance(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    measurements = json.loads(measurements_path.read_text())
    measurements["projection"] = {
        "revision": "moodboard.showcase-frozen-evidence-projection.v1",
        "sha256": "7" * 64,
    }
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    assert artifact["provenance"]["projection"] == measurements["projection"]


def test_evidence_binding_rejects_bytes_that_drift_after_measurement(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    paths = _add_evidence_bindings(measurements_path, tmp_path)
    paths["intent_freeze"].write_text('{"tampered":true}\n', encoding="utf-8")

    with pytest.raises(PixelRagError, match="evidence binding.*SHA-256"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path,
            measurements_path=measurements_path,
        )


def test_measured_run_requires_the_complete_frozen_evidence_chain(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id, status="measured_run")

    with pytest.raises(PixelRagError, match="evidence_bindings.*required"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path,
            measurements_path=measurements_path,
        )


def test_measured_json_schemas_require_the_frozen_evidence_chain(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    measurements = json.loads(measurements_path.read_text())
    measurements["evidence_status"] = "measured_run"
    measurement_schema = json.loads(
        (
            Path(__file__).parents[1] / "moodboard/schema/pixel_rag_measurements_v1.schema.json"
        ).read_text()
    )

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            measurements,
            measurement_schema,
            cls=jsonschema.Draft202012Validator,
        )

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )
    artifact["evidence_status"] = "measured_run"
    artifact["provenance"].pop("evidence_sha256", None)
    artifact_schema = json.loads(
        (
            Path(__file__).parents[1] / "moodboard/schema/pixel_rag_artifact_v1.schema.json"
        ).read_text()
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            artifact,
            artifact_schema,
            cls=jsonschema.Draft202012Validator,
        )


def test_all_raw_and_routed_metrics_are_preserved_as_nonprobabilistic_diagnostics(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    for intent in artifact["intents"]:
        retrieval = intent["retrieval"]
        assert [metric["id"] for metric in retrieval["metrics"]] == [
            "precision_at_3",
            "ndcg_at_5",
            "mrr",
            "recall_at_5",
        ]
        assert [metric["id"] for metric in retrieval["raw_diagnostics"]["metrics"]] == [
            "precision_at_3",
            "ndcg_at_5",
            "mrr",
            "recall_at_5",
        ]
        assert retrieval["raw_diagnostics"]["probabilistic_interpretation"] is False


def test_experimental_qwen_output_diagnostics_are_closed_and_arithmetically_bound(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    measurements = json.loads(measurements_path.read_text())
    measurements["experimental_visual_embedding_diagnostics"] = _experimental_diagnostics()
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    diagnostics = artifact["experimental_visual_embedding_diagnostics"]
    assert diagnostics == _experimental_diagnostics()
    assert diagnostics["contract"]["raw_cosine_is_probability"] is False
    assert diagnostics["contract"]["validated_csd_or_style_probability"] is False

    measurements["experimental_visual_embedding_diagnostics"]["restyle_style_affinity"][
        "claude_minus_vangogh_margin"
    ] = 0.25
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")
    with pytest.raises(PixelRagError, match="Claude-minus-Van-Gogh margin"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path,
            measurements_path=measurements_path,
        )


def test_reader_keeps_accepting_a_canonical_pre_extension_v1_artifact(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    current = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )
    legacy = json.loads(json.dumps(current))
    legacy["provenance"].pop("evidence_sha256", None)
    legacy.pop("experimental_visual_embedding_diagnostics", None)
    for intent in legacy["intents"]:
        intent.pop("negative_output_evidence")
        intent["retrieval"].pop("metrics_interpretation")
        intent["retrieval"].pop("raw_diagnostics")
        intent["retrieval"]["metrics"] = intent["retrieval"]["metrics"][:2]
        if intent["output"] is not None:
            intent["output"]["external_location"] = {
                "kind": "local_file",
                "path": f"/legacy/{intent['id']}.png",
            }
    legacy["cross_intent_metrics"][0].pop("interpretation")
    legacy["contracts"] = {
        "artifact_schema_sha256": (
            "a317962da489b7471866286dc5bfab9429fe5f9caed1a9e3c2e92259a3a7fbd5"
        ),
        "measurements_schema_sha256": (
            "475d95a387765167f1e7109c9bc8cc549abc3e4bb12c4446fbf292eb4106965d"
        ),
        "source_manifest_schema_sha256": (
            "212d49d8a18a42ab95566417cb2b943efd62436177cd55c91a60cb6051f545c5"
        ),
    }
    legacy["provenance"]["compiler_revision"] = "moodboard.pixel-rag-compiler.v1"
    legacy["provenance"]["run_fingerprint"] = _legacy_run_fingerprint(legacy)
    _reidentify(legacy)

    validate_pixel_rag_artifact(legacy)


def test_reader_requires_current_structural_routing_interpretations(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    missing_retrieval_interpretation = json.loads(json.dumps(artifact))
    missing_retrieval_interpretation["intents"][0]["retrieval"].pop("metrics_interpretation")
    _reidentify(missing_retrieval_interpretation)
    with pytest.raises(PixelRagError, match="metrics_interpretation|required property"):
        validate_pixel_rag_artifact(missing_retrieval_interpretation)

    missing_cross_interpretation = json.loads(json.dumps(artifact))
    missing_cross_interpretation["cross_intent_metrics"][0].pop("interpretation")
    _reidentify(missing_cross_interpretation)
    with pytest.raises(PixelRagError, match="interpretation|required property"):
        validate_pixel_rag_artifact(missing_cross_interpretation)


def test_reader_binds_compiler_run_fingerprint_and_whole_frame_source_query(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    compiler_drift = json.loads(json.dumps(artifact))
    compiler_drift["provenance"]["compiler_revision"] = "moodboard.pixel-rag-compiler.v1"
    _reidentify(compiler_drift)
    with pytest.raises(PixelRagError, match="compiler/schema identity"):
        validate_pixel_rag_artifact(compiler_drift)

    fingerprint_drift = json.loads(json.dumps(artifact))
    fingerprint_drift["provenance"]["run_fingerprint"] = "f" * 64
    _reidentify(fingerprint_drift)
    with pytest.raises(PixelRagError, match="run fingerprint"):
        validate_pixel_rag_artifact(fingerprint_drift)

    query_drift = json.loads(json.dumps(artifact))
    global_query = query_drift["intents"][1]["route"]["query"]
    global_query["content_ref"] = "e" * 64
    global_query["sha256"] = "d" * 64
    _reidentify(query_drift)
    with pytest.raises(PixelRagError, match="whole-frame query.*immutable source"):
        validate_pixel_rag_artifact(query_drift)


def test_reader_rejects_extended_metric_sequence_and_verifier_status_drift(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )

    incomplete_metrics = json.loads(json.dumps(artifact))
    incomplete_metrics["intents"][0]["retrieval"]["metrics"].pop()
    _reidentify(incomplete_metrics)
    with pytest.raises(PixelRagError, match="retrieval metric sequence|too short"):
        validate_pixel_rag_artifact(incomplete_metrics)

    extension_downgrade = json.loads(json.dumps(artifact))
    for intent in extension_downgrade["intents"]:
        intent["retrieval"].pop("metrics_interpretation")
        intent["retrieval"].pop("raw_diagnostics")
        intent["retrieval"]["metrics"] = intent["retrieval"]["metrics"][:2]
    _reidentify(extension_downgrade)
    with pytest.raises(PixelRagError, match="extended retrieval evidence|required property"):
        validate_pixel_rag_artifact(extension_downgrade)

    routed_projection_drift = json.loads(json.dumps(artifact))
    routed = routed_projection_drift["intents"][0]["retrieval"]
    routed["exact_score_order"][0]["score"] -= 0.01
    routed["ranked_evidence"][0]["score"]["value"] -= 0.01
    _reidentify(routed_projection_drift)
    with pytest.raises(PixelRagError, match="stable subsequence of raw retrieval"):
        validate_pixel_rag_artifact(routed_projection_drift)

    card_score_drift = json.loads(json.dumps(artifact))
    card_score_drift["intents"][0]["retrieval"]["ranked_evidence"][0]["score"]["value"] -= 0.01
    _reidentify(card_score_drift)
    with pytest.raises(PixelRagError, match="ranked card fields"):
        validate_pixel_rag_artifact(card_score_drift)

    card_fingerprint_drift = json.loads(json.dumps(artifact))
    card_fingerprint_drift["intents"][0]["retrieval"]["ranked_evidence"][0]["score"][
        "descriptor_fingerprint"
    ] = "f" * 64
    _reidentify(card_fingerprint_drift)
    with pytest.raises(PixelRagError, match="ranked card fields"):
        validate_pixel_rag_artifact(card_fingerprint_drift)

    inconsistent_verifier = json.loads(json.dumps(artifact))
    inconsistent_verifier["intents"][0]["verification"]["status"] = "passed"
    _reidentify(inconsistent_verifier)
    with pytest.raises(PixelRagError, match="verification status"):
        validate_pixel_rag_artifact(inconsistent_verifier)


def test_measured_local_output_rejects_a_compositor_method_that_overclaims_pixels(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    measurements = json.loads(measurements_path.read_text())
    measurements["intents"][0]["generator"]["deterministic_postprocess"] = {
        "method": "restore_source_pixels_outside_confirmed_region",
        "provenance_sha256": "6" * 64,
        "revision": "ffmpeg-7.1.1-mask-overlay-v1",
    }
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")

    with pytest.raises(PixelRagError, match="overclaims exact source pixels"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path,
            measurements_path=measurements_path,
        )


def test_measured_metrics_require_complete_raw_relevance_coverage(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id, status="measured_run")
    _add_evidence_bindings(measurements_path, tmp_path)

    with pytest.raises(PixelRagError, match="relevance judgments for every raw hit"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path,
            measurements_path=measurements_path,
        )


def test_compiler_rejects_unclosed_inputs_descriptor_drift_and_unknown_assets(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    measurements = json.loads(measurements_path.read_text())
    measurements["surprise"] = True
    measurements_path.write_text(_canonical(measurements), encoding="utf-8")
    with pytest.raises(PixelRagError, match="unknown|Additional properties"):
        compile_pixel_rag_artifact(manifest_path=manifest_path, measurements_path=measurements_path)

    measurements.pop("surprise")
    measurements["descriptor"]["inference"]["version"] = "0.8.0"
    measurements_path.write_text(_canonical(measurements), encoding="utf-8")
    with pytest.raises(PixelRagError, match="0.9.0"):
        compile_pixel_rag_artifact(manifest_path=manifest_path, measurements_path=measurements_path)

    measurements["descriptor"] = _descriptor()
    measurements["intents"][0]["hits"][0]["content_ref"] = "f" * 64
    measurements_path.write_text(_canonical(measurements), encoding="utf-8")
    with pytest.raises(PixelRagError, match="not present in the governed manifest"):
        compile_pixel_rag_artifact(manifest_path=manifest_path, measurements_path=measurements_path)


def test_compiler_rejects_score_reordering_and_manifest_byte_tampering(tmp_path: Path) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    measurements = json.loads(measurements_path.read_text())
    measurements["intents"][0]["hits"][0]["score"] = -0.9
    measurements_path.write_text(_canonical(measurements), encoding="utf-8")
    with pytest.raises(PixelRagError, match="score order"):
        compile_pixel_rag_artifact(manifest_path=manifest_path, measurements_path=measurements_path)

    _measurements(measurements_path, by_id)
    source_path = manifest_path.parent / by_id["fruit_apple_garden"]["local_path"]
    source_path.write_bytes(source_path.read_bytes() + b"tamper")
    with pytest.raises(PixelRagError, match="SHA-256 mismatch"):
        compile_pixel_rag_artifact(manifest_path=manifest_path, measurements_path=measurements_path)


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


def test_selected_mask_composite_retains_rejected_predecessor_evidence(
    tmp_path: Path,
) -> None:
    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    measurements = json.loads(measurements_path.read_text())
    local = measurements["intents"][0]
    local["generator"]["deterministic_postprocess"] = {
        "method": "source_backed_region_overlay",
        "provenance_sha256": "6" * 64,
        "revision": "ffmpeg-7.1.1-mask-overlay-v1",
    }
    local["verification_metrics"] = [
        {
            "id": "outside_mask_ssim",
            "method_revision": "moodboard.outside-mask-ssim.v1",
            "operator": "greater_than_or_equal",
            "threshold": 0.95,
            "value": 0.9996,
        }
    ]
    local["negative_output_evidence"] = [
        {
            "disposition": "rejected",
            "evidence_id": "external_precomputed_failed_locality_v1",
            "generator": {
                "execution_mode": "precomputed_external",
                "provider": "openai-imagegen",
                "service": "Codex built-in ImageGen",
            },
            "verification_metrics": [
                {
                    "id": "outside_mask_ssim",
                    "method_revision": "moodboard.outside-mask-ssim.v1",
                    "operator": "greater_than_or_equal",
                    "threshold": 0.95,
                    "value": 0.3849,
                }
            ],
        }
    ]
    measurements_path.write_text(_canonical(measurements) + "\n", encoding="utf-8")

    selected_path = tmp_path / "selected-v3.png"
    selected_bytes = _image(selected_path, (210, 210, 45), size=(16, 12))
    rejected_path = tmp_path / "rejected-v1.png"
    rejected_bytes = _image(rejected_path, (160, 90, 25), size=(16, 12))
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
        external_outputs={
            "local_replace": ExternalOutput(
                path=selected_path,
                khive_record_id=_record_id("selected-v3"),
                expected_content_ref=blake3(selected_bytes).hexdigest(),
            )
        },
        historical_external_outputs={
            "local_replace": [
                ExternalOutput(
                    path=rejected_path,
                    khive_record_id=_record_id("rejected-v1"),
                    expected_content_ref=blake3(rejected_bytes).hexdigest(),
                )
            ]
        },
    )

    result = artifact["intents"][0]
    assert result["verification"]["status"] == "passed"
    assert result["output"]["output_content_ref"] == blake3(selected_bytes).hexdigest()
    assert result["output"]["generator"]["deterministic_postprocess"] == {
        "method": "source_backed_region_overlay",
        "provenance_sha256": "6" * 64,
        "revision": "ffmpeg-7.1.1-mask-overlay-v1",
    }
    assert result["negative_output_evidence"] == [
        {
            "disposition": "rejected",
            "evidence_id": "external_precomputed_failed_locality_v1",
            "output": {
                **result["negative_output_evidence"][0]["output"],
                "output_content_ref": blake3(rejected_bytes).hexdigest(),
            },
            "verification": {
                "metrics": [
                    {
                        "id": "outside_mask_ssim",
                        "method_revision": "moodboard.outside-mask-ssim.v1",
                        "operator": "greater_than_or_equal",
                        "passed": False,
                        "source": "contract_fixture",
                        "threshold": 0.95,
                        "value": 0.3849,
                    }
                ],
                "status": "failed",
            },
        }
    ]
    assert result["output"]["external_location"] == {"kind": "identity_only"}
    assert result["negative_output_evidence"][0]["output"]["external_location"] == {
        "kind": "identity_only"
    }

    with pytest.raises(PixelRagError, match="byte/identity-distinct"):
        compile_pixel_rag_artifact(
            manifest_path=manifest_path,
            measurements_path=measurements_path,
            external_outputs={
                "local_replace": ExternalOutput(
                    path=selected_path,
                    khive_record_id=_record_id("selected-v3"),
                    expected_content_ref=blake3(selected_bytes).hexdigest(),
                )
            },
            historical_external_outputs={
                "local_replace": [
                    ExternalOutput(
                        path=selected_path,
                        khive_record_id=_record_id("rejected-same-bytes"),
                        expected_content_ref=blake3(selected_bytes).hexdigest(),
                    )
                ]
            },
        )

    selected_location_drift = json.loads(json.dumps(artifact))
    selected_location_drift["intents"][0]["output"]["external_location"] = {
        "kind": "local_file",
        "path": "/tmp/private-selected.png",
    }
    _reidentify(selected_location_drift)
    with pytest.raises(PixelRagError, match=r"intents\.0\.output|identity_only"):
        validate_pixel_rag_artifact(selected_location_drift)

    negative_location_drift = json.loads(json.dumps(artifact))
    negative_location_drift["intents"][0]["negative_output_evidence"][0]["output"][
        "external_location"
    ] = {
        "kind": "local_file",
        "path": "/tmp/private-rejected.png",
    }
    _reidentify(negative_location_drift)
    with pytest.raises(
        PixelRagError,
        match=r"negative_output_evidence|identity_only",
    ):
        validate_pixel_rag_artifact(negative_location_drift)

    for identity_field in ("output_sha256", "output_content_ref"):
        reused_identity = json.loads(json.dumps(artifact))
        selected_output = reused_identity["intents"][0]["output"]
        rejected_output = reused_identity["intents"][0]["negative_output_evidence"][0]["output"]
        rejected_output[identity_field] = selected_output[identity_field]
        if identity_field == "output_content_ref":
            rejected_output["blob_store_registration"]["content_ref"] = selected_output[
                identity_field
            ]
        reused_identity["provenance"]["run_fingerprint"] = _current_run_fingerprint(reused_identity)
        _reidentify(reused_identity)
        with pytest.raises(PixelRagError, match="byte/identity-distinct"):
            validate_pixel_rag_artifact(reused_identity)

    registration_drift = json.loads(json.dumps(artifact))
    registration_drift["intents"][0]["output"]["blob_store_registration"]["content_ref"] = "f" * 64
    _reidentify(registration_drift)
    with pytest.raises(PixelRagError, match="registration.*ContentRef"):
        validate_pixel_rag_artifact(registration_drift)

    rollback_drift = json.loads(json.dumps(artifact))
    rollback_drift["intents"][0]["negative_output_evidence"][0]["output"]["rollback"][
        "content_ref"
    ] = "f" * 64
    _reidentify(rollback_drift)
    with pytest.raises(PixelRagError, match="rollback.*immutable source"):
        validate_pixel_rag_artifact(rollback_drift)

    arithmetic_drift = json.loads(json.dumps(artifact))
    arithmetic_drift["intents"][0]["negative_output_evidence"][0]["verification"]["metrics"][0][
        "passed"
    ] = True
    arithmetic_drift["intents"][0]["negative_output_evidence"][0]["verification"]["status"] = (
        "passed"
    )
    _reidentify(arithmetic_drift)
    with pytest.raises(PixelRagError, match="verifier arithmetic"):
        validate_pixel_rag_artifact(arithmetic_drift)

    negative_pass = json.loads(json.dumps(artifact))
    metric = negative_pass["intents"][0]["negative_output_evidence"][0]["verification"]["metrics"][
        0
    ]
    metric["value"] = 0.99
    metric["passed"] = True
    negative_pass["intents"][0]["negative_output_evidence"][0]["verification"]["status"] = "passed"
    _reidentify(negative_pass)
    with pytest.raises(PixelRagError, match="negative output evidence.*failed verifier"):
        validate_pixel_rag_artifact(negative_pass)

    unregistered_negative = json.loads(json.dumps(artifact))
    unregistered_negative["intents"][0]["negative_output_evidence"][0]["output"][
        "blob_store_registration"
    ] = {"state": "not_registered"}
    _reidentify(unregistered_negative)
    with pytest.raises(PixelRagError, match=r"blob_store_registration\.state"):
        validate_pixel_rag_artifact(unregistered_negative)


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

    bridge = compile_viewer_pixel_rag_bridge(source, manifest_path)

    expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    assert bridge["format_version"] == "moodboard.viewer-pixel-rag-bridge.v2"
    assert bridge["generator_revision"] == "moodboard.pixel-rag-viewer-bridge.v2"
    assert bridge["state"] == "projected"
    assert bridge["input"] == {
        "artifact_id": artifact["artifact_id"],
        "byte_size": source.stat().st_size,
        "canonical_sha256": expected_sha256,
        "schema_version": "moodboard.pixel-rag-artifact.v1",
        "sha256": expected_sha256,
    }
    assert bridge["artifact"] == artifact
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert bridge["media"]["manifest_sha256"] == manifest_sha256
    expected_records = [
        artifact["source"],
        *(hit for intent in artifact["intents"] for hit in intent["retrieval"]["ranked_evidence"]),
    ]
    expected_by_id = {record["asset_id"]: record for record in expected_records}
    assert len(expected_by_id) == 7
    assert [row["asset_id"] for row in bridge["media"]["assets"]] == sorted(expected_by_id)
    for row in bridge["media"]["assets"]:
        record = expected_by_id[row["asset_id"]]
        manifest_row = by_id[row["asset_id"]]
        original = (manifest_path.parent / manifest_row["local_path"]).read_bytes()
        assert set(row) == {
            "asset_id",
            "content_ref",
            "content_sha256",
            "original_byte_size",
            "original_height",
            "original_width",
            "preview",
        }
        assert row | {"preview": None} == {
            "asset_id": record["asset_id"],
            "content_ref": record["khive"]["content_ref"],
            "content_sha256": record["sha256"],
            "original_byte_size": len(original),
            "original_height": record["image"]["height"],
            "original_width": record["image"]["width"],
            "preview": None,
        }
        preview = row["preview"]
        assert set(preview) == {
            "byte_size",
            "data_base64",
            "height",
            "mime",
            "sha256",
            "width",
        }
        preview_bytes = base64.b64decode(preview["data_base64"], validate=True)
        assert preview["mime"] == "image/jpeg"
        assert preview["byte_size"] == len(preview_bytes)
        assert preview["sha256"] == hashlib.sha256(preview_bytes).hexdigest()
        assert preview_bytes != original
        with Image.open(BytesIO(preview_bytes)) as decoded:
            assert decoded.format == "JPEG"
            assert decoded.size == (preview["width"], preview["height"])
            assert max(decoded.size) <= 480
            assert not decoded.getexif()
            assert not {"exif", "icc_profile", "xmp", "comment"} & set(decoded.info)
            decoded.verify()
    assert compile_viewer_pixel_rag_bridge(source, manifest_path) == bridge
    assert len((_canonical(bridge) + "\n").encode()) < 256 * 1024
    validate_viewer_pixel_rag_bridge(bridge)


def test_viewer_bridge_media_fails_closed_on_missing_tampered_or_noncanonical_bytes(
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
    source = tmp_path / "pixel-rag.json"
    write_pixel_rag_artifact(artifact, source)
    bridge = compile_viewer_pixel_rag_bridge(source, manifest_path)

    missing = json.loads(json.dumps(bridge))
    missing["media"]["assets"].pop()
    with pytest.raises(PixelRagViewerBridgeError, match="exact.*media|media.*exact"):
        validate_viewer_pixel_rag_bridge(missing)

    extra = json.loads(json.dumps(bridge))
    extra_row = json.loads(json.dumps(extra["media"]["assets"][0]))
    extra_row["asset_id"] = "unexpected_media"
    extra["media"]["assets"].append(extra_row)
    with pytest.raises(PixelRagViewerBridgeError, match="exact.*media|media.*exact"):
        validate_viewer_pixel_rag_bridge(extra)

    tampered = json.loads(json.dumps(bridge))
    tampered["media"]["assets"][0]["preview"]["data_base64"] = base64.b64encode(
        b"tampered"
    ).decode()
    with pytest.raises(PixelRagViewerBridgeError, match="byte size|SHA-256|ContentRef"):
        validate_viewer_pixel_rag_bridge(tampered)

    noncanonical = json.loads(json.dumps(bridge))
    noncanonical["media"]["assets"][0]["preview"]["data_base64"] += "\n"
    with pytest.raises(PixelRagViewerBridgeError, match="base64"):
        validate_viewer_pixel_rag_bridge(noncanonical)

    wrong_dimensions = json.loads(json.dumps(bridge))
    wrong_dimensions["media"]["assets"][0]["preview"]["width"] += 1
    with pytest.raises(PixelRagViewerBridgeError, match="dimensions"):
        validate_viewer_pixel_rag_bridge(wrong_dimensions)

    original_identity = json.loads(json.dumps(bridge))
    original_identity["media"]["assets"][0]["content_sha256"] = "f" * 64
    with pytest.raises(PixelRagViewerBridgeError, match="identity"):
        validate_viewer_pixel_rag_bridge(original_identity)

    preview_identity = json.loads(json.dumps(bridge))
    preview_identity["media"]["assets"][0]["preview"]["sha256"] = "f" * 64
    with pytest.raises(PixelRagViewerBridgeError, match="SHA-256"):
        validate_viewer_pixel_rag_bridge(preview_identity)


def test_viewer_bridge_rejects_media_aliases_across_distinct_asset_ids(tmp_path: Path) -> None:
    from moodboard.pixel_rag_viewer import (
        PixelRagViewerBridgeError,
        _required_media_records,
    )

    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )
    first = artifact["intents"][0]["retrieval"]["ranked_evidence"][0]
    second = artifact["intents"][0]["retrieval"]["ranked_evidence"][1]
    second["sha256"] = first["sha256"]
    second["khive"]["content_ref"] = first["khive"]["content_ref"]
    with pytest.raises(PixelRagViewerBridgeError, match="one-to-one"):
        _required_media_records(artifact)


def test_viewer_bridge_compile_binds_manifest_and_rejects_symlink_media(tmp_path: Path) -> None:
    from moodboard.pixel_rag_viewer import (
        PixelRagViewerBridgeError,
        compile_viewer_pixel_rag_bridge,
    )

    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )
    source = tmp_path / "pixel-rag.json"
    write_pixel_rag_artifact(artifact, source)

    mismatched_manifest = tmp_path / "mismatched-manifest.json"
    mismatched_manifest.write_bytes(manifest_path.read_bytes() + b" ")
    with pytest.raises(PixelRagViewerBridgeError, match="manifest SHA-256"):
        compile_viewer_pixel_rag_bridge(source, mismatched_manifest)

    source_asset = manifest_path.parent / by_id["fruit_apple_garden"]["local_path"]
    replacement = tmp_path / "source-copy.png"
    replacement.write_bytes(source_asset.read_bytes())
    source_asset.unlink()
    source_asset.symlink_to(replacement)
    with pytest.raises(PixelRagViewerBridgeError, match="symlink"):
        compile_viewer_pixel_rag_bridge(source, manifest_path)


def test_viewer_bridge_compile_rejects_symlink_media_directory(tmp_path: Path) -> None:
    from moodboard.pixel_rag_viewer import (
        PixelRagViewerBridgeError,
        compile_viewer_pixel_rag_bridge,
    )

    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )
    source = tmp_path / "pixel-rag.json"
    write_pixel_rag_artifact(artifact, source)

    assets = manifest_path.parent / "assets"
    moved_assets = manifest_path.parent / "real-assets"
    assets.rename(moved_assets)
    assets.symlink_to(moved_assets, target_is_directory=True)
    with pytest.raises(PixelRagViewerBridgeError, match="symlink"):
        compile_viewer_pixel_rag_bridge(source, manifest_path)


def test_viewer_bridge_fallback_is_closed_and_cli_requires_manifest(tmp_path: Path) -> None:
    from moodboard.pixel_rag_viewer import (
        PixelRagViewerBridgeError,
        fallback_viewer_pixel_rag_bridge,
        main,
        validate_viewer_pixel_rag_bridge,
    )

    fallback = fallback_viewer_pixel_rag_bridge()
    assert fallback == {
        "artifact": None,
        "format_version": "moodboard.viewer-pixel-rag-bridge.v2",
        "generator_revision": "moodboard.pixel-rag-viewer-bridge.v2",
        "input": None,
        "media": None,
        "state": "fallback",
    }
    validate_viewer_pixel_rag_bridge(fallback)
    drifted = {**fallback, "media": {"assets": [], "manifest_sha256": "f" * 64}}
    with pytest.raises(PixelRagViewerBridgeError, match="fallback.*media"):
        validate_viewer_pixel_rag_bridge(drifted)

    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )
    source = tmp_path / "pixel-rag.json"
    write_pixel_rag_artifact(artifact, source)
    destination = tmp_path / "bridge.json"

    with pytest.raises(SystemExit, match="--input requires --manifest and --write"):
        main(["--input", str(source), "--write", str(destination)])

    assert (
        main(
            [
                "--input",
                str(source),
                "--manifest",
                str(manifest_path),
                "--write",
                str(destination),
            ]
        )
        == 0
    )

    with pytest.raises(SystemExit, match="--check requires --input and --manifest"):
        main(["--check", str(destination)])

    assert (
        main(
            [
                "--input",
                str(source),
                "--manifest",
                str(manifest_path),
                "--check",
                str(destination),
            ]
        )
        == 0
    )


def test_viewer_bridge_cli_deep_check_rejects_self_consistent_preview_substitution(
    tmp_path: Path,
) -> None:
    from moodboard.pixel_rag_viewer import (
        compile_viewer_pixel_rag_bridge,
        main,
        validate_viewer_pixel_rag_bridge,
        write_viewer_pixel_rag_bridge,
    )

    manifest_path, by_id = _manifest(tmp_path)
    measurements_path = _measurements(tmp_path / "measurements.json", by_id)
    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
    )
    source = tmp_path / "pixel-rag.json"
    write_pixel_rag_artifact(artifact, source)
    bridge = compile_viewer_pixel_rag_bridge(source, manifest_path)

    preview = bridge["media"]["assets"][0]["preview"]
    raw = base64.b64decode(preview["data_base64"], validate=True)
    with Image.open(BytesIO(raw)) as decoded:
        replacement = decoded.convert("RGB")
    replacement.paste((255, 0, 255), (0, 0, 6, 6))
    encoded = BytesIO()
    replacement.save(
        encoded,
        format="JPEG",
        quality=45,
        subsampling=2,
        optimize=True,
        progressive=True,
        exif=b"",
    )
    replacement_bytes = encoded.getvalue()
    preview.update(
        {
            "byte_size": len(replacement_bytes),
            "data_base64": base64.b64encode(replacement_bytes).decode("ascii"),
            "sha256": hashlib.sha256(replacement_bytes).hexdigest(),
        }
    )
    validate_viewer_pixel_rag_bridge(bridge)
    destination = tmp_path / "substituted-bridge.json"
    write_viewer_pixel_rag_bridge(bridge, destination)
    before = destination.read_bytes()

    with pytest.raises(SystemExit, match="deterministic recompilation"):
        main(
            [
                "--input",
                str(source),
                "--manifest",
                str(manifest_path),
                "--check",
                str(destination),
            ]
        )
    assert destination.read_bytes() == before


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
        compile_viewer_pixel_rag_bridge(pretty, manifest_path)

    source = tmp_path / "pixel-rag.json"
    write_pixel_rag_artifact(artifact, source)
    bridge = compile_viewer_pixel_rag_bridge(source, manifest_path)
    bridge["input"]["canonical_sha256"] = "f" * 64
    with pytest.raises(PixelRagViewerBridgeError, match="canonical_sha256"):
        validate_viewer_pixel_rag_bridge(bridge)

    bridge = compile_viewer_pixel_rag_bridge(source, manifest_path)
    bridge["artifact"]["surprise"] = True
    with pytest.raises(PixelRagError, match="unknown|Additional properties"):
        validate_viewer_pixel_rag_bridge(bridge)

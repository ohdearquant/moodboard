"""Project frozen governed evidence into the closed Pixel RAG contract.

This is a deterministic attestation boundary, not a second evaluation. It performs no
inference and refuses to overwrite a published run. Large source/evidence/output bytes remain
ignored; this tracked projector makes every equality assertion reviewable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from moodboard.pixel_rag import ExternalOutput, compile_pixel_rag_artifact, write_pixel_rag_artifact

EXPECTED_SHA256 = {
    "manifest": "f3ade3346f7887e05e65c3f41b02e875f995f53f8cd7660621294a3f80149ad1",
    "preregistration": "07597460d66962a71c5d3816e97ec1bba73fd933f9940c6e293fbbf7abcb9cda",
    "intent_freeze": "605bb3bb01f905895f809e294472eb697560badffc2c738070212938f0acfa16",
    "verification": "72d5ed8a50a4a5715554369b266bc90f685590bfd56e2f5b67625e435ebd56eb",
    "compositor_provenance": ("58a9b71d42c3c3900edf85a49308b2d2eef667e0d67ebc46817df75eea9af377"),
}


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _load(path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    raw = path.read_bytes()
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError(f"frozen evidence SHA-256 drifted: {path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError(f"frozen evidence must be an object: {path}")
    return value


def _metric_values(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {row["id"]: row["value"] for row in rows}


def project(*, root: Path, output_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    evidence_dir = root / ".cache/showcase-khive-state/evidence"
    manifest_path = (
        root / ".cache/showcase-public-domain-v1/run-20260812-final-current/manifest.json"
    )
    prereg_path = evidence_dir / "00-evaluation-prereg.json"
    intent_path = evidence_dir / "07-intent-freeze.summary.json"
    registration_path = evidence_dir / "08-output-ingest.summary.json"
    verification_path = evidence_dir / "09-verification.summary.json"
    compositor_path = (
        root / ".cache/showcase-generated-v1/provenance/apple-to-lemon-mask-enforced-v3.json"
    )
    measurements_path = output_dir / "pixel-rag-measurements.json"
    artifact_path = output_dir / "pixel-rag-artifact.json"

    manifest = _load(manifest_path, EXPECTED_SHA256["manifest"])
    prereg = _load(prereg_path, EXPECTED_SHA256["preregistration"])
    frozen = _load(intent_path, EXPECTED_SHA256["intent_freeze"])
    registration = _load(registration_path)
    verification = _load(verification_path, EXPECTED_SHA256["verification"])
    compositor = _load(compositor_path, EXPECTED_SHA256["compositor_provenance"])

    if verification["pixel_rag"]["frozen_evaluation"] != frozen:
        raise ValueError("verification summary no longer embeds the frozen evaluation")
    if verification["external_precomputed_outputs"] != registration:
        raise ValueError("verification summary no longer embeds the output registrations")
    if prereg["manifest_sha256"] != EXPECTED_SHA256["manifest"]:
        raise ValueError("preregistration does not bind the frozen manifest")
    if frozen["preregistration"]["sha256"] != EXPECTED_SHA256["preregistration"]:
        raise ValueError("intent freeze does not bind the preregistration")
    if verification["khive_commit"] != "985f5c245d94d413a018525c4a0ed25a53dec671":
        raise ValueError("Khive revision drifted")
    if not verification["descriptor_restart_stable"]:
        raise ValueError("descriptor did not survive restart")
    if not verification["search_restart_byte_equivalent"]:
        raise ValueError("search results did not survive restart")

    descriptor = verification["experimental_visual_embedding_diagnostics"]["contract"][
        "model_descriptor"
    ]
    if descriptor != registration["descriptor"]:
        raise ValueError("registration descriptor drifted")
    if descriptor["inference"] != {"provider": "lattice-embed", "version": "0.9.0"}:
        raise ValueError("descriptor is not the Lattice 0.9.0 contract")

    manifest_by_ref = {row["khive_content_ref"]: row for row in manifest["assets"]}
    if len(manifest_by_ref) != manifest["asset_count"] or len(manifest_by_ref) != 15:
        raise ValueError("governed corpus identity drifted")
    source = next(row for row in manifest["assets"] if row["asset_id"] == "fruit_apple_garden")
    source_ref = source["khive_content_ref"]
    route_by_intent = {row["intent"]: row for row in prereg["intent_routes"]}
    replace = frozen["replace"]
    restyle = frozen["restyle"]
    replace_crop = verification["pixel_rag"]["replace"]["crop"]
    restyle_query = verification["pixel_rag"]["restyle"]["query"]
    if replace_crop["normalized_rectangle"] != {"h": 0.9, "w": 0.72, "x": 0.18, "y": 0.05}:
        raise ValueError("confirmed replacement rectangle drifted")
    if (
        restyle_query["content_ref"] != source_ref
        or restyle_query["source_sha256"] != source["sha256"]
    ):
        raise ValueError("restyle query no longer binds the immutable source")

    def hits(block: dict[str, Any]) -> list[dict[str, Any]]:
        rows = block["raw_ranking"]
        if [row["rank"] for row in rows] != list(range(1, len(rows) + 1)):
            raise ValueError("raw ranks are not contiguous")
        if [row["score"] for row in rows] != sorted((row["score"] for row in rows), reverse=True):
            raise ValueError("raw scores are not in exact descending order")
        if any(row["content_ref"] not in manifest_by_ref for row in rows):
            raise ValueError("raw hit escaped the governed manifest")
        return [
            {
                "content_ref": row["content_ref"],
                "rank": row["rank"],
                "record_id": row["asset_id"],
                "score": row["score"],
            }
            for row in rows
        ]

    def relevance(route: dict[str, Any], block: dict[str, Any]) -> list[dict[str, Any]]:
        gains = route["relevance"]
        if set(gains) != {row["content_ref"] for row in block["raw_ranking"]}:
            raise ValueError("frozen relevance judgments do not cover the complete raw corpus")
        return [
            {"content_ref": row["content_ref"], "gain": gains[row["content_ref"]]}
            for row in block["raw_ranking"]
        ]

    locality = verification["experimental_visual_embedding_diagnostics"]["locality_pixel_evidence"]
    v1 = locality["failed_external_precomputed_v1"]
    v3 = locality["passing_mask_enforced_v3"]
    if v1["passes_threshold"] is not False or v3["passes_threshold"] is not True:
        raise ValueError("frozen locality fail/pass history drifted")
    if locality["preregistered_gate"] != {
        "metric": "outside_mask_ssim_luma_safe_window_mean",
        "minimum": 0.95,
        "v1_expected_and_observed_fail": True,
        "v3_expected_and_observed_pass": True,
    }:
        raise ValueError("preregistered locality gate drifted")

    registered_by_role = {row["role"]: row for row in registration["outputs"]}
    registered_v1 = registered_by_role["external_precomputed_failed_locality_v1"]
    registered_v3 = registered_by_role["mask_enforced_local_output_v3"]
    registered_restyle = registered_by_role["external_precomputed_global_restyle"]
    if registered_v1["source_sha256"] != v1["candidate"]["sha256"]:
        raise ValueError("failed v1 registration drifted")
    if registered_v3["source_sha256"] != v3["candidate"]["sha256"]:
        raise ValueError("selected v3 registration drifted")
    if not registration["negative_locality_evidence_preserved"]:
        raise ValueError("negative locality evidence was not preserved")

    compositor_region = compositor["edit_region"]["normalized"]
    if compositor_region != {"height": 0.9, "width": 0.72, "x": 0.18, "y": 0.05}:
        raise ValueError("compositor region does not match the confirmed rectangle")
    if compositor["compositor"]["revision"] != "ffmpeg-7.1.1-mask-overlay-v1":
        raise ValueError("compositor revision drifted")
    if compositor["source"]["sha256"] != source["sha256"]:
        raise ValueError("compositor source drifted")
    if compositor["output"]["sha256"] != registered_v3["source_sha256"]:
        raise ValueError("compositor output drifted")
    if compositor["output"]["blake3"] != registered_v3["content_ref"]:
        raise ValueError("compositor output ContentRef drifted")

    diagnostics = verification["experimental_visual_embedding_diagnostics"]
    local_alignment = diagnostics["local_output_region_intent_alignment"]
    restyle_retention = diagnostics["restyle_content_retention"]
    style_affinity = diagnostics["restyle_style_affinity"]
    projection_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    verification_sha = EXPECTED_SHA256["verification"]
    evidence_field = {
        "evidence_kind": "verification_summary",
        "evidence_sha256": verification_sha,
    }
    generator = {
        "execution_mode": "precomputed_external",
        "provider": "OpenAI",
        "service": "ImageGen through Codex built-in",
    }
    measurements: dict[str, Any] = {
        "descriptor": descriptor,
        "evidence_bindings": [
            {
                "kind": "evaluation_preregistration",
                "path": str(prereg_path),
                "sha256": EXPECTED_SHA256["preregistration"],
            },
            {
                "kind": "intent_freeze",
                "path": str(intent_path),
                "sha256": EXPECTED_SHA256["intent_freeze"],
            },
            {
                "kind": "verification_summary",
                "path": str(verification_path),
                "sha256": verification_sha,
            },
        ],
        "evidence_status": "measured_run",
        "experimental_visual_embedding_diagnostics": {
            "contract": {
                key: diagnostics["contract"][key]
                for key in (
                    "interpretation",
                    "kind",
                    "raw_cosine_is_probability",
                    "validated_csd_or_style_probability",
                )
            },
            "local_output_region_intent_alignment": {
                key: local_alignment[key]
                for key in (
                    "mean_apple_cosine",
                    "mean_lemon_cosine",
                    "mean_lemon_minus_apple_margin",
                    "output_role",
                )
            },
            "restyle_content_retention": {
                "cosine": restyle_retention["cosine"],
                "output_role": "external_precomputed_global_restyle",
            },
            "restyle_style_affinity": {
                **{
                    key: style_affinity[key]
                    for key in (
                        "claude_centroid_cosine",
                        "claude_minus_vangogh_margin",
                        "claude_reference_count",
                        "vangogh_centroid_cosine",
                        "vangogh_reference_count",
                    )
                },
                "output_role": "external_precomputed_global_restyle",
            },
        },
        "generated_at": frozen["frozen_at"].replace("+00:00", "Z"),
        "intents": [
            {
                "designer_prompt": (
                    "Replace only the selected apple tree with a mature lemon tree. Preserve "
                    "the scene outside the confirmed rectangle, camera geometry, and lighting."
                ),
                "generator": {
                    **generator,
                    "deterministic_postprocess": {
                        "method": "source_backed_region_overlay",
                        "provenance_sha256": EXPECTED_SHA256["compositor_provenance"],
                        "revision": compositor["compositor"]["revision"],
                    },
                },
                "hits": hits(replace),
                "id": "local_replace",
                "namespace": replace["namespace"],
                "negative_output_evidence": [
                    {
                        "disposition": "rejected",
                        "evidence_id": "external_precomputed_failed_locality_v1",
                        "generator": generator,
                        "verification_metrics": [
                            {
                                "evidence_field": {
                                    **evidence_field,
                                    "json_pointer": (
                                        "/experimental_visual_embedding_diagnostics/"
                                        "locality_pixel_evidence/"
                                        "failed_external_precomputed_v1/"
                                        "outside_mask_ssim_luma_safe_window_mean"
                                    ),
                                },
                                "id": "outside_mask_ssim",
                                "operator": "greater_than_or_equal",
                                "threshold": v1["threshold"],
                                "value": v1["outside_mask_ssim_luma_safe_window_mean"],
                            }
                        ],
                    }
                ],
                "query_record": {
                    "content_ref": replace_crop["content_ref"],
                    "record_id": replace_crop["asset_id"],
                    "sha256": replace_crop["canonical_png_sha256"],
                },
                "region": {
                    "confirmation": {
                        "evidence_kind": "evaluation_preregistration",
                        "evidence_sha256": EXPECTED_SHA256["preregistration"],
                        "json_pointer": "/intent_routes/0/query",
                        "method": "evidence_bound_human_confirmation",
                    },
                    "height": 0.9,
                    "kind": "normalized_rectangle",
                    "label": "primary apple tree canopy and trunk",
                    "width": 0.72,
                    "x": 0.18,
                    "y": 0.05,
                },
                "relevance_judgments": relevance(route_by_intent["local_replace"], replace),
                "verification_metrics": [
                    {
                        "evidence_field": {
                            **evidence_field,
                            "json_pointer": (
                                "/experimental_visual_embedding_diagnostics/"
                                "locality_pixel_evidence/passing_mask_enforced_v3/"
                                "outside_mask_ssim_luma_safe_window_mean"
                            ),
                        },
                        "id": "outside_mask_ssim",
                        "operator": "greater_than_or_equal",
                        "threshold": v3["threshold"],
                        "value": v3["outside_mask_ssim_luma_safe_window_mean"],
                    }
                ],
            },
            {
                "designer_prompt": (
                    "Restyle the complete scene as a luminous Claude Lorrain pastoral painting "
                    "while preserving the source composition and subject relationships."
                ),
                "generator": generator,
                "hits": hits(restyle),
                "id": "global_restyle",
                "namespace": restyle["namespace"],
                "query_record": {
                    "content_ref": restyle_query["content_ref"],
                    "record_id": restyle_query["asset_id"],
                    "sha256": restyle_query["source_sha256"],
                },
                "region": None,
                "relevance_judgments": relevance(route_by_intent["global_restyle"], restyle),
                "verification_metrics": [],
            },
        ],
        "khive_revision": verification["khive_commit"],
        "projection": {
            "revision": "moodboard.showcase-frozen-evidence-projection.v1",
            "sha256": projection_sha,
        },
        "schema_version": "moodboard.pixel-rag-measurements.v1",
        "source_record": {
            "content_ref": source_ref,
            "record_id": verification["first_search"]["query_asset_id"],
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    if measurements_path.exists() or artifact_path.exists():
        raise FileExistsError("refusing to overwrite an immutable measured Pixel RAG run")
    measurements_path.write_bytes(_canonical_bytes(measurements))

    artifact = compile_pixel_rag_artifact(
        manifest_path=manifest_path,
        measurements_path=measurements_path,
        external_outputs={
            "local_replace": ExternalOutput(
                path=Path(registered_v3["source_path"]),
                khive_record_id=registered_v3["asset_id"],
                expected_content_ref=registered_v3["content_ref"],
            ),
            "global_restyle": ExternalOutput(
                path=Path(registered_restyle["source_path"]),
                khive_record_id=registered_restyle["asset_id"],
                expected_content_ref=registered_restyle["content_ref"],
            ),
        },
        historical_external_outputs={
            "local_replace": [
                ExternalOutput(
                    path=Path(registered_v1["source_path"]),
                    khive_record_id=registered_v1["asset_id"],
                    expected_content_ref=registered_v1["content_ref"],
                )
            ]
        },
    )
    for intent_id, frozen_block in (("local_replace", replace), ("global_restyle", restyle)):
        intent = next(row for row in artifact["intents"] if row["id"] == intent_id)
        if (
            _metric_values(intent["retrieval"]["raw_diagnostics"]["metrics"])
            != frozen_block["raw_metrics"]
        ):
            raise ValueError(f"{intent_id} raw metrics drifted")
        if _metric_values(intent["retrieval"]["metrics"]) != frozen_block["routed_metrics"]:
            raise ValueError(f"{intent_id} routed metrics drifted")
    if artifact["cross_intent_metrics"][0]["value"] != frozen["top3_cross_intent_jaccard"]:
        raise ValueError("cross-intent route separation drifted")
    if artifact["intents"][0]["verification"]["status"] != "passed":
        raise ValueError("selected v3 must preserve its recorded locality pass")
    if artifact["intents"][0]["negative_output_evidence"][0]["verification"]["status"] != "failed":
        raise ValueError("historical v1 must preserve its recorded locality failure")
    if artifact["provenance"]["projection"]["sha256"] != projection_sha:
        raise ValueError("projector identity drifted")
    write_pixel_rag_artifact(artifact, artifact_path)
    return {
        "artifact": str(artifact_path),
        "artifact_id": artifact["artifact_id"],
        "measurements": str(measurements_path),
        "projection_sha256": projection_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(project(root=arguments.root, output_dir=arguments.output_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

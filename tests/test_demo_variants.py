from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from moodboard.demo_variants import build_preference_demo_pool


def _write_source_manifest(root: Path) -> Path:
    assets = root / "assets"
    assets.mkdir()
    rows = []
    for index in range(2):
        image = Image.new("RGB", (12 + index, 10 + index), (30 + index, 70, 110))
        for x in range(image.width):
            image.putpixel((x, index), (x * 10, 25 + index, 200 - x))
        path = assets / f"style_{index}.png"
        image.save(path, format="PNG")
        rows.append(
            {
                "artist": f"artist-{index}",
                "asset_id": f"style_{index}",
                "byte_size": len(path.read_bytes()),
                "collection": ("style-claude-lorrain" if index == 0 else "style-vincent-van-gogh"),
                "download_url": f"https://images.example.test/{index}.png",
                "http": {
                    "content_type": "image/png",
                    "etag": None,
                    "final_url": f"https://images.example.test/{index}.png",
                    "last_modified": None,
                },
                "image": {"format": "PNG", "height": image.height, "width": image.width},
                "khive_content_ref": __import__("blake3").blake3(path.read_bytes()).hexdigest(),
                "license": {
                    "id": "CC0-1.0",
                    "short_name": "CC0",
                    "url": "https://creativecommons.org/publicdomain/zero/1.0/",
                    "source_url": None,
                    "public_domain": True,
                },
                "local_path": f"assets/{path.name}",
                "metadata": {
                    "etag": None,
                    "evidence_sha256": str(index) * 64,
                    "url": f"https://collectionapi.example.test/{index}",
                },
                "object_id": index + 1,
                "provider": "met",
                "retrieved_at": "2026-08-12T17:00:00Z",
                "sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
                "source_page_url": f"https://example.test/{index}",
                "title": f"title-{index}",
            }
        )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "moodboard.demo-manifest.v1",
                "dataset_id": "adobe-interview-public-domain-v1",
                "retrieved_at": "2026-08-12T17:00:00Z",
                "catalog_sha256": "a" * 64,
                "asset_count": len(rows),
                "assets": rows,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return manifest


def test_preference_demo_pool_is_deterministic_unique_and_lineage_bound(tmp_path: Path) -> None:
    manifest = _write_source_manifest(tmp_path)

    first = build_preference_demo_pool(manifest, tmp_path / "first")
    second = build_preference_demo_pool(manifest, tmp_path / "second")

    assert first.read_bytes() == second.read_bytes()
    document = json.loads(first.read_text())
    assert document["schema_version"] == "moodboard.preference-demo-pool.v1"
    assert (
        document["source_manifest_sha256"]
        == __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    )
    assert document["asset_count"] == 6
    assert [row["transform"] for row in document["assets"]] == [
        "original",
        "center_crop_90pct",
        "horizontal_mirror",
    ] * 2
    assert len({row["sha256"] for row in document["assets"]}) == 6
    assert len({row["khive_content_ref"] for row in document["assets"]}) == 6
    for row in document["assets"]:
        assert row["source_asset_id"] in {"style_0", "style_1"}
        assert row["license"]["public_domain"] is True
        artifact = first.parent / row["local_path"]
        assert artifact.is_file()
        assert __import__("hashlib").sha256(artifact.read_bytes()).hexdigest() == row["sha256"]
        assert (
            __import__("blake3").blake3(artifact.read_bytes()).hexdigest()
            == row["khive_content_ref"]
        )


def test_preference_demo_pool_refuses_non_style_and_tampered_source(tmp_path: Path) -> None:
    manifest = _write_source_manifest(tmp_path)
    document = json.loads(manifest.read_text())
    document["assets"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(document))

    try:
        build_preference_demo_pool(manifest, tmp_path / "out")
    except ValueError as error:
        assert "source sha256" in str(error)
    else:
        raise AssertionError("tampered source must fail closed")
    assert not (tmp_path / "out").exists()

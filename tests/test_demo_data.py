"""Contract tests for the public-domain Adobe demo corpus acquisition."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from blake3 import blake3
from PIL import Image

from moodboard.demo_data import (
    AcquisitionError,
    HttpResponse,
    acquire_dataset,
    load_catalog,
)

FIXED_TIME = "2026-08-12T17:00:00Z"
REVIEWED_CATALOG = Path(__file__).resolve().parents[1] / "moodboard" / "demo_sources_v1.json"


def _jpeg_bytes(*, size: tuple[int, int] = (4, 3), color=(30, 120, 60)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="JPEG", quality=90)
    return output.getvalue()


def _commons_metadata(*, license_name: str = "Public domain") -> bytes:
    payload = {
        "batchcomplete": "",
        "query": {
            "pages": [
                {
                    "pageid": 123,
                    "ns": 6,
                    "title": "File:Apple tree.jpg",
                    "imageinfo": [
                        {
                            "canonicaltitle": "File:Apple tree.jpg",
                            "descriptionurl": (
                                "https://commons.wikimedia.org/wiki/File:Apple_tree.jpg"
                            ),
                            "extmetadata": {
                                "Artist": {"value": "Example photographer"},
                                "Copyrighted": {"value": "False"},
                                "Credit": {"value": "Own work"},
                                "LicenseShortName": {"value": license_name},
                                "LicenseUrl": {
                                    "value": ("https://creativecommons.org/publicdomain/mark/1.0/")
                                },
                                "ObjectName": {"value": "Apple tree"},
                                "UsageTerms": {"value": "Public domain"},
                            },
                        }
                    ],
                }
            ]
        },
    }
    return json.dumps(payload, sort_keys=True).encode()


def _met_metadata(*, public_domain: bool = True) -> bytes:
    payload = {
        "objectID": 435906,
        "isPublicDomain": public_domain,
        "primaryImage": "https://images.metmuseum.org/CRDImages/ep/original/DP-17628-001.jpg",
        "primaryImageSmall": (
            "https://images.metmuseum.org/CRDImages/ep/web-large/DP-17628-001.jpg"
        ),
        "objectURL": "https://www.metmuseum.org/art/collection/search/435906",
        "title": "Pastoral Landscape",
        "artistDisplayName": "Claude Lorrain",
    }
    return json.dumps(payload, sort_keys=True).encode()


def _catalog(tmp_path: Path) -> Path:
    payload = {
        "schema_version": "moodboard.demo-sources.v1",
        "dataset_id": "adobe-interview-public-domain-v1",
        "assets": [
            {
                "asset_id": "apple_tree",
                "collection": "fruit-apple",
                "provider": "wikimedia_commons",
                "file_title": "File:Apple tree.jpg",
                "metadata_url": "https://commons.wikimedia.org/w/api.php?apple",
                "page_url": "https://commons.wikimedia.org/wiki/File:Apple_tree.jpg",
                "download_url": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Apple_tree.jpg?width=1280",
                "expected": {
                    "artist": "Example photographer",
                    "copyrighted": "False",
                    "license_id": "PDM-1.0",
                    "license_short_name": "Public domain",
                    "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
                    "object_name": "Apple tree",
                    "page_id": 123,
                    "public_domain": True,
                    "source_license_url": ("https://creativecommons.org/publicdomain/mark/1.0/"),
                },
            },
            {
                "asset_id": "claude_pastoral",
                "collection": "style-claude-lorrain",
                "provider": "met",
                "object_id": 435906,
                "metadata_url": (
                    "https://collectionapi.metmuseum.org/public/collection/v1/objects/435906"
                ),
                "page_url": "https://www.metmuseum.org/art/collection/search/435906",
                "download_url": (
                    "https://images.metmuseum.org/CRDImages/ep/web-large/DP-17628-001.jpg"
                ),
                "expected": {
                    "artist": "Claude Lorrain",
                    "public_domain": True,
                    "title": "Pastoral Landscape",
                },
            },
        ],
    }
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FakeTransport:
    def __init__(self, responses: dict[str, HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url: str, *, max_bytes: int) -> HttpResponse:
        self.calls.append((url, max_bytes))
        response = self.responses[url]
        if len(response.body) > max_bytes:
            raise AcquisitionError(f"response exceeds {max_bytes} bytes")
        return response


def _responses(catalog_path: Path, image: bytes) -> dict[str, HttpResponse]:
    apple, claude = load_catalog(catalog_path).assets
    met_image = _jpeg_bytes(color=(90, 70, 140))
    return {
        apple.metadata_url: HttpResponse(
            status=200,
            final_url=apple.metadata_url,
            headers={"content-type": "application/json", "etag": '"commons-meta"'},
            body=_commons_metadata(),
        ),
        apple.download_url: HttpResponse(
            status=200,
            final_url="https://upload.wikimedia.org/apple.jpg",
            headers={
                "content-type": "image/jpeg",
                "content-length": str(len(image)),
                "etag": '"commons-image"',
                "last-modified": "Tue, 12 Aug 2025 00:00:00 GMT",
            },
            body=image,
        ),
        claude.metadata_url: HttpResponse(
            status=200,
            final_url=claude.metadata_url,
            headers={"content-type": "application/json"},
            body=_met_metadata(),
        ),
        claude.download_url: HttpResponse(
            status=200,
            final_url=claude.download_url,
            headers={
                "content-type": "image/jpeg",
                "content-length": str(len(met_image)),
                "etag": '"met-image"',
            },
            body=met_image,
        ),
    }


def test_reviewed_catalog_is_exactly_the_15_item_public_domain_demo_set():
    catalog = load_catalog(REVIEWED_CATALOG)

    assert catalog.dataset_id == "adobe-interview-public-domain-v1"
    assert len(catalog.assets) == 15
    assert {asset.provider for asset in catalog.assets} == {"met", "wikimedia_commons"}
    assert {asset.collection for asset in catalog.assets} == {
        "fruit-apple",
        "fruit-lemon",
        "style-claude-lorrain",
        "style-vincent-van-gogh",
    }
    assert all(asset.expected["public_domain"] is True for asset in catalog.assets)


def test_success_publishes_one_canonical_immutable_run(tmp_path):
    catalog_path = _catalog(tmp_path)
    image = _jpeg_bytes()
    transport = FakeTransport(_responses(catalog_path, image))
    destination = tmp_path / "run"

    manifest = acquire_dataset(
        catalog_path=catalog_path,
        destination=destination,
        retrieved_at=FIXED_TIME,
        transport=transport,
    )

    assert manifest["schema_version"] == "moodboard.demo-manifest.v1"
    assert manifest["dataset_id"] == "adobe-interview-public-domain-v1"
    assert manifest["retrieved_at"] == FIXED_TIME
    assert manifest["asset_count"] == 2
    assert [row["asset_id"] for row in manifest["assets"]] == [
        "apple_tree",
        "claude_pastoral",
    ]
    apple = manifest["assets"][0]
    assert apple["sha256"] == hashlib.sha256(image).hexdigest()
    assert apple["khive_content_ref"] == blake3(image).hexdigest()
    assert apple["byte_size"] == len(image)
    assert apple["image"] == {"format": "JPEG", "height": 3, "width": 4}
    assert apple["http"]["etag"] == '"commons-image"'
    assert apple["http"]["last_modified"] == "Tue, 12 Aug 2025 00:00:00 GMT"
    assert apple["license"]["public_domain"] is True
    assert apple["local_path"] == "assets/apple_tree.jpg"
    assert (destination / apple["local_path"]).read_bytes() == image

    manifest_bytes = (destination / "manifest.json").read_bytes()
    assert (
        manifest_bytes
        == (
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    )
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    assert (destination / "manifest.sha256").read_text() == f"{digest}  manifest.json\n"
    assert not list(tmp_path.glob(".run.staging-*"))

    with pytest.raises(FileExistsError):
        acquire_dataset(
            catalog_path=catalog_path,
            destination=destination,
            retrieved_at=FIXED_TIME,
            transport=transport,
        )


def test_ungoverned_provider_array_order_cannot_move_the_locked_manifest(tmp_path):
    catalog_path = _catalog(tmp_path)
    image = _jpeg_bytes()
    first_responses = _responses(catalog_path, image)
    second_responses = _responses(catalog_path, image)
    met = load_catalog(catalog_path).assets[1]
    first_metadata = json.loads(first_responses[met.metadata_url].body)
    second_metadata = json.loads(second_responses[met.metadata_url].body)
    first_metadata["measurements"] = [{"name": "framed"}, {"name": "overall"}]
    second_metadata["measurements"] = list(reversed(first_metadata["measurements"]))
    first_responses[met.metadata_url] = HttpResponse(
        status=200,
        final_url=met.metadata_url,
        headers={"content-type": "application/json"},
        body=json.dumps(first_metadata).encode(),
    )
    second_responses[met.metadata_url] = HttpResponse(
        status=200,
        final_url=met.metadata_url,
        headers={"content-type": "application/json"},
        body=json.dumps(second_metadata).encode(),
    )

    acquire_dataset(
        catalog_path=catalog_path,
        destination=tmp_path / "first",
        retrieved_at=FIXED_TIME,
        transport=FakeTransport(first_responses),
    )
    acquire_dataset(
        catalog_path=catalog_path,
        destination=tmp_path / "second",
        retrieved_at=FIXED_TIME,
        transport=FakeTransport(second_responses),
    )

    assert (tmp_path / "first" / "manifest.json").read_bytes() == (
        tmp_path / "second" / "manifest.json"
    ).read_bytes()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("commons-license", "license"),
        ("met-public-domain", "public domain"),
    ],
)
def test_license_or_public_domain_drift_fails_closed_without_partial_output(
    tmp_path, mutation, message
):
    catalog_path = _catalog(tmp_path)
    image = _jpeg_bytes()
    responses = _responses(catalog_path, image)
    apple, claude = load_catalog(catalog_path).assets
    if mutation == "commons-license":
        responses[apple.metadata_url] = HttpResponse(
            status=200,
            final_url=apple.metadata_url,
            headers={"content-type": "application/json"},
            body=_commons_metadata(license_name="CC BY-SA 4.0"),
        )
    else:
        responses[claude.metadata_url] = HttpResponse(
            status=200,
            final_url=claude.metadata_url,
            headers={"content-type": "application/json"},
            body=_met_metadata(public_domain=False),
        )

    destination = tmp_path / "failed-run"
    with pytest.raises(AcquisitionError, match=message):
        acquire_dataset(
            catalog_path=catalog_path,
            destination=destination,
            retrieved_at=FIXED_TIME,
            transport=FakeTransport(responses),
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".failed-run.staging-*"))


def test_oversized_or_non_image_payload_fails_before_publication(tmp_path):
    catalog_path = _catalog(tmp_path)
    image = _jpeg_bytes()
    responses = _responses(catalog_path, image)
    apple = load_catalog(catalog_path).assets[0]
    responses[apple.download_url] = HttpResponse(
        status=200,
        final_url="https://upload.wikimedia.org/not-an-image.jpg",
        headers={"content-type": "image/jpeg"},
        body=b"<html>not an image</html>",
    )

    destination = tmp_path / "bad-run"
    with pytest.raises(AcquisitionError, match="valid image"):
        acquire_dataset(
            catalog_path=catalog_path,
            destination=destination,
            retrieved_at=FIXED_TIME,
            transport=FakeTransport(responses),
        )
    assert not destination.exists()


def test_duplicate_source_bytes_fail_the_unique_asset_gate(tmp_path):
    catalog_path = _catalog(tmp_path)
    image = _jpeg_bytes()
    responses = _responses(catalog_path, image)
    met = load_catalog(catalog_path).assets[1]
    responses[met.download_url] = HttpResponse(
        status=200,
        final_url=met.download_url,
        headers={"content-type": "image/jpeg", "content-length": str(len(image))},
        body=image,
    )

    with pytest.raises(AcquisitionError, match="duplicate image bytes"):
        acquire_dataset(
            catalog_path=catalog_path,
            destination=tmp_path / "duplicate-run",
            retrieved_at=FIXED_TIME,
            transport=FakeTransport(responses),
        )


def test_metadata_redirect_outside_the_official_provider_fails_closed(tmp_path):
    catalog_path = _catalog(tmp_path)
    image = _jpeg_bytes()
    responses = _responses(catalog_path, image)
    apple = load_catalog(catalog_path).assets[0]
    responses[apple.metadata_url] = HttpResponse(
        status=200,
        final_url="https://example.test/forged-metadata.json",
        headers={"content-type": "application/json"},
        body=_commons_metadata(),
    )

    with pytest.raises(AcquisitionError, match="metadata redirected outside"):
        acquire_dataset(
            catalog_path=catalog_path,
            destination=tmp_path / "redirected-run",
            retrieved_at=FIXED_TIME,
            transport=FakeTransport(responses),
        )


def test_catalog_rejects_unknown_fields_and_non_https_urls(tmp_path):
    catalog_path = _catalog(tmp_path)
    payload = json.loads(catalog_path.read_text())
    payload["assets"][0]["surprise"] = "not governed"
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AcquisitionError, match="unknown fields"):
        load_catalog(catalog_path)

    catalog_path = _catalog(tmp_path)
    payload = json.loads(catalog_path.read_text())
    payload["assets"][0]["download_url"] = "http://commons.wikimedia.org/insecure.jpg"
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AcquisitionError, match="HTTPS"):
        load_catalog(catalog_path)

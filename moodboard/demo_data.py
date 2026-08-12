"""Acquire the small, public-domain corpus used by the Adobe interview demo.

This is deliberately a governed downloader, not a general image scraper.  A reviewed catalogue
names every allowed object and the exact licence facts expected from the provider's official API.
The command refuses a metadata mismatch, validates every image before publishing anything, and
renames one complete staging directory into place so a failed run cannot look complete.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from blake3 import blake3
from jsonschema import Draft202012Validator
from PIL import Image, UnidentifiedImageError

CATALOG_SCHEMA = "moodboard.demo-sources.v1"
MANIFEST_SCHEMA = "moodboard.demo-manifest.v1"
MANIFEST_SCHEMA_PATH = Path(__file__).with_name("schema") / "demo_manifest_v1.schema.json"
METADATA_MAX_BYTES = 2 * 1024 * 1024
IMAGE_MAX_BYTES = 24 * 1024 * 1024
IMAGE_MAX_SIDE = 8192
IMAGE_MAX_PIXELS = 40_000_000
_ASSET_ID = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}\Z")
_HTML_TAG = re.compile(r"<[^>]*>")
_IMAGE_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


class AcquisitionError(RuntimeError):
    """The source no longer satisfies the reviewed acquisition contract."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """One bounded HTTP response, kept small enough to fake in contract tests."""

    status: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class SourceSpec:
    asset_id: str
    collection: str
    provider: str
    metadata_url: str
    page_url: str
    download_url: str
    expected: Mapping[str, Any]
    file_title: str | None = None
    object_id: int | None = None


@dataclass(frozen=True, slots=True)
class SourceCatalog:
    schema_version: str
    dataset_id: str
    assets: tuple[SourceSpec, ...]
    sha256: str


Transport = Callable[..., HttpResponse]


def _only_keys(value: Mapping[str, Any], allowed: set[str], source: str) -> None:
    unknown = set(value) - allowed
    missing = allowed - set(value)
    if unknown:
        raise AcquisitionError(f"{source} has unknown fields: {sorted(unknown)}")
    if missing:
        raise AcquisitionError(f"{source} is missing fields: {sorted(missing)}")


def _https_url(value: Any, source: str, *, hosts: set[str]) -> str:
    if not isinstance(value, str):
        raise AcquisitionError(f"{source} must be a string HTTPS URL")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise AcquisitionError(f"{source} must be an absolute HTTPS URL")
    hostname = (parsed.hostname or "").lower()
    if hostname not in hosts:
        raise AcquisitionError(f"{source} host {hostname!r} is not an approved provider host")
    return value


def _text(value: Any, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AcquisitionError(f"{source} must be a non-empty string")
    return value


def _bool(value: Any, source: str) -> bool:
    if not isinstance(value, bool):
        raise AcquisitionError(f"{source} must be a boolean")
    return value


def load_catalog(path: Path) -> SourceCatalog:
    """Load the closed catalogue and reject anything outside its provider-specific schema."""

    catalog_path = Path(path)
    raw = catalog_path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcquisitionError(f"catalog is not valid UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise AcquisitionError("catalog must be a JSON object")
    _only_keys(value, {"schema_version", "dataset_id", "assets"}, "catalog")
    if value["schema_version"] != CATALOG_SCHEMA:
        raise AcquisitionError(f"catalog schema must be {CATALOG_SCHEMA!r}")
    dataset_id = _text(value["dataset_id"], "catalog.dataset_id")
    raw_assets = value["assets"]
    if not isinstance(raw_assets, list) or not raw_assets:
        raise AcquisitionError("catalog.assets must be a non-empty array")

    assets: list[SourceSpec] = []
    for index, raw_asset in enumerate(raw_assets):
        source = f"catalog.assets[{index}]"
        if not isinstance(raw_asset, dict):
            raise AcquisitionError(f"{source} must be an object")
        provider = raw_asset.get("provider")
        common = {
            "asset_id",
            "collection",
            "provider",
            "metadata_url",
            "page_url",
            "download_url",
            "expected",
        }
        if provider == "wikimedia_commons":
            _only_keys(raw_asset, common | {"file_title"}, source)
            metadata_hosts = {"commons.wikimedia.org"}
            page_hosts = {"commons.wikimedia.org"}
            download_hosts = {"commons.wikimedia.org"}
            expected_keys = {
                "artist",
                "copyrighted",
                "license_id",
                "license_short_name",
                "license_url",
                "object_name",
                "page_id",
                "public_domain",
                "source_license_url",
            }
            file_title = _text(raw_asset["file_title"], f"{source}.file_title")
            if not file_title.startswith("File:"):
                raise AcquisitionError(f"{source}.file_title must start with 'File:'")
            object_id = None
        elif provider == "met":
            _only_keys(raw_asset, common | {"object_id"}, source)
            metadata_hosts = {"collectionapi.metmuseum.org"}
            page_hosts = {"www.metmuseum.org"}
            download_hosts = {"images.metmuseum.org"}
            expected_keys = {"artist", "public_domain", "title"}
            object_id = raw_asset["object_id"]
            if isinstance(object_id, bool) or not isinstance(object_id, int) or object_id <= 0:
                raise AcquisitionError(f"{source}.object_id must be a positive integer")
            file_title = None
        else:
            raise AcquisitionError(f"{source}.provider is not supported")

        expected = raw_asset["expected"]
        if not isinstance(expected, dict):
            raise AcquisitionError(f"{source}.expected must be an object")
        _only_keys(expected, expected_keys, f"{source}.expected")
        if not _bool(expected["public_domain"], f"{source}.expected.public_domain"):
            raise AcquisitionError(f"{source} is not declared public domain")
        if provider == "wikimedia_commons":
            _text(expected["license_id"], f"{source}.expected.license_id")
            _text(expected["license_short_name"], f"{source}.expected.license_short_name")
            _text(expected["artist"], f"{source}.expected.artist")
            _text(expected["object_name"], f"{source}.expected.object_name")
            _https_url(
                expected["license_url"],
                f"{source}.expected.license_url",
                hosts={"creativecommons.org"},
            )
            source_license_url = expected["source_license_url"]
            if source_license_url is not None:
                if not isinstance(source_license_url, str):
                    raise AcquisitionError(
                        f"{source}.expected.source_license_url must be a string or null"
                    )
                parsed_source_license = urllib.parse.urlsplit(source_license_url)
                if (
                    parsed_source_license.scheme not in {"http", "https"}
                    or (parsed_source_license.hostname or "").lower() != "creativecommons.org"
                ):
                    raise AcquisitionError(
                        f"{source}.expected.source_license_url must name Creative Commons"
                    )
            if expected["copyrighted"] not in {"True", "False"}:
                raise AcquisitionError(
                    f"{source}.expected.copyrighted must be the exact Commons boolean string"
                )
            page_id = expected["page_id"]
            if isinstance(page_id, bool) or not isinstance(page_id, int) or page_id <= 0:
                raise AcquisitionError(f"{source}.expected.page_id must be a positive integer")
        else:
            _text(expected["artist"], f"{source}.expected.artist")
            _text(expected["title"], f"{source}.expected.title")

        asset_id = _text(raw_asset["asset_id"], f"{source}.asset_id")
        if not _ASSET_ID.fullmatch(asset_id):
            raise AcquisitionError(f"{source}.asset_id is not a safe stable identifier")
        assets.append(
            SourceSpec(
                asset_id=asset_id,
                collection=_text(raw_asset["collection"], f"{source}.collection"),
                provider=provider,
                metadata_url=_https_url(
                    raw_asset["metadata_url"], f"{source}.metadata_url", hosts=metadata_hosts
                ),
                page_url=_https_url(raw_asset["page_url"], f"{source}.page_url", hosts=page_hosts),
                download_url=_https_url(
                    raw_asset["download_url"],
                    f"{source}.download_url",
                    hosts=download_hosts,
                ),
                expected=expected,
                file_title=file_title,
                object_id=object_id,
            )
        )

    asset_ids = [asset.asset_id for asset in assets]
    if len(set(asset_ids)) != len(asset_ids):
        raise AcquisitionError("catalog asset_id values must be unique")
    if asset_ids != sorted(asset_ids):
        raise AcquisitionError("catalog assets must be sorted by asset_id")
    return SourceCatalog(
        schema_version=CATALOG_SCHEMA,
        dataset_id=dataset_id,
        assets=tuple(assets),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def fetch_url(url: str, *, max_bytes: int) -> HttpResponse:
    """Fetch one response without ever buffering more than the declared byte ceiling."""

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,image/jpeg,image/png,image/webp;q=0.9,*/*;q=0.1",
            "User-Agent": "moodboard-public-domain-demo/1.0 (+https://github.com/ohdearquant/moodboard)",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 - closed host allowlist
            headers = {key.lower(): value for key, value in response.headers.items()}
            declared = headers.get("content-length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as error:
                    raise AcquisitionError(f"{url} returned an invalid Content-Length") from error
                if declared_size < 0 or declared_size > max_bytes:
                    raise AcquisitionError(f"{url} response exceeds {max_bytes} bytes")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise AcquisitionError(f"{url} response exceeds {max_bytes} bytes")
            status = int(getattr(response, "status", response.getcode()))
            return HttpResponse(
                status=status,
                final_url=response.geturl(),
                headers=headers,
                body=body,
            )
    except AcquisitionError:
        raise
    except (OSError, urllib.error.URLError) as error:
        raise AcquisitionError(f"failed to fetch {url}: {error}") from error


def _header(response: HttpResponse, name: str) -> str | None:
    wanted = name.lower()
    for key, value in response.headers.items():
        if key.lower() == wanted:
            return value
    return None


def _json_response(response: HttpResponse, *, source: str) -> dict[str, Any]:
    if response.status != 200:
        raise AcquisitionError(f"{source} returned HTTP {response.status}")
    content_type = (_header(response, "content-type") or "").split(";", 1)[0].lower()
    if content_type not in {"application/json", "application/ld+json"}:
        raise AcquisitionError(f"{source} did not return JSON")
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcquisitionError(f"{source} returned invalid JSON") from error
    if not isinstance(value, dict):
        raise AcquisitionError(f"{source} JSON must be an object")
    return value


def _validate_metadata_location(spec: SourceSpec, response: HttpResponse) -> None:
    parsed = urllib.parse.urlsplit(response.final_url)
    allowed_host = (
        "commons.wikimedia.org"
        if spec.provider == "wikimedia_commons"
        else "collectionapi.metmuseum.org"
    )
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != allowed_host:
        raise AcquisitionError(
            f"{spec.asset_id}: metadata redirected outside its official provider"
        )


def _plain_metadata(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(html.unescape(_HTML_TAG.sub(" ", value)).split())


def _commons_provenance(spec: SourceSpec, metadata: dict[str, Any]) -> dict[str, Any]:
    pages = (
        metadata.get("query", {}).get("pages") if isinstance(metadata.get("query"), dict) else None
    )
    if not isinstance(pages, list) or len(pages) != 1:
        raise AcquisitionError(f"{spec.asset_id}: Commons metadata must resolve exactly one page")
    page = pages[0]
    if not isinstance(page, dict) or "missing" in page:
        raise AcquisitionError(f"{spec.asset_id}: Commons source page is missing")
    if page.get("pageid") != spec.expected["page_id"]:
        raise AcquisitionError(f"{spec.asset_id}: Commons page identity drifted")
    expected_title = (spec.file_title or "").replace("_", " ")
    actual_title = str(page.get("title", "")).replace("_", " ")
    if actual_title != expected_title:
        raise AcquisitionError(f"{spec.asset_id}: Commons file title drifted")
    image_info = page.get("imageinfo")
    if (
        not isinstance(image_info, list)
        or len(image_info) != 1
        or not isinstance(image_info[0], dict)
    ):
        raise AcquisitionError(f"{spec.asset_id}: Commons imageinfo is missing")
    info = image_info[0]
    description_url = info.get("descriptionurl")
    if description_url != spec.page_url:
        raise AcquisitionError(f"{spec.asset_id}: Commons source page URL drifted")
    ext = info.get("extmetadata")
    if not isinstance(ext, dict):
        raise AcquisitionError(f"{spec.asset_id}: Commons extmetadata is missing")

    def field(name: str) -> str:
        item = ext.get(name)
        if not isinstance(item, dict) or not isinstance(item.get("value"), str):
            raise AcquisitionError(f"{spec.asset_id}: Commons {name} is missing")
        return item["value"]

    def optional_field(name: str) -> str | None:
        item = ext.get(name)
        if item is None:
            return None
        if not isinstance(item, dict) or not isinstance(item.get("value"), str):
            raise AcquisitionError(f"{spec.asset_id}: Commons {name} is malformed")
        return item["value"]

    license_name = field("LicenseShortName")
    source_license_url = optional_field("LicenseUrl")
    copyrighted = field("Copyrighted")
    if license_name != spec.expected["license_short_name"]:
        raise AcquisitionError(f"{spec.asset_id}: Commons license short name drifted")
    if source_license_url != spec.expected["source_license_url"]:
        raise AcquisitionError(f"{spec.asset_id}: Commons license URL drifted")
    if copyrighted != spec.expected["copyrighted"]:
        raise AcquisitionError(f"{spec.asset_id}: Commons copyright status drifted")
    artist = _plain_metadata(field("Artist"))
    object_name = _plain_metadata(field("ObjectName"))
    if artist != spec.expected["artist"]:
        raise AcquisitionError(f"{spec.asset_id}: Commons artist attribution drifted")
    if object_name != spec.expected["object_name"]:
        raise AcquisitionError(f"{spec.asset_id}: Commons object name drifted")
    evidence = {
        "artist": artist,
        "copyrighted": copyrighted,
        "description_url": description_url,
        "file_title": actual_title,
        "license_short_name": license_name,
        "page_id": page["pageid"],
        "source_license_url": source_license_url,
        "title": object_name,
    }
    return {
        "artist": artist,
        "evidence": evidence,
        "license": {
            "id": spec.expected["license_id"],
            "public_domain": True,
            "short_name": license_name,
            "source_url": source_license_url,
            "url": spec.expected["license_url"],
        },
        "object_id": None,
        "title": object_name,
    }


def _met_provenance(spec: SourceSpec, metadata: dict[str, Any]) -> dict[str, Any]:
    if metadata.get("objectID") != spec.object_id:
        raise AcquisitionError(f"{spec.asset_id}: Met object identity drifted")
    if metadata.get("isPublicDomain") is not True:
        raise AcquisitionError(f"{spec.asset_id}: Met object is no longer marked public domain")
    artist = metadata.get("artistDisplayName")
    title = metadata.get("title")
    if artist != spec.expected["artist"]:
        raise AcquisitionError(f"{spec.asset_id}: Met artist attribution drifted")
    if title != spec.expected["title"]:
        raise AcquisitionError(f"{spec.asset_id}: Met object title drifted")
    if metadata.get("objectURL") != spec.page_url:
        raise AcquisitionError(f"{spec.asset_id}: Met object page URL drifted")
    if spec.download_url not in {metadata.get("primaryImage"), metadata.get("primaryImageSmall")}:
        raise AcquisitionError(f"{spec.asset_id}: Met image URL drifted")
    evidence = {
        "artist": artist,
        "download_url": spec.download_url,
        "object_id": spec.object_id,
        "page_url": spec.page_url,
        "public_domain": True,
        "title": title,
    }
    return {
        "artist": artist,
        "evidence": evidence,
        "license": {
            "id": "CC0-1.0",
            "public_domain": True,
            "short_name": "CC0 1.0 / Met Open Access",
            "source_url": None,
            "url": "https://creativecommons.org/publicdomain/zero/1.0/",
        },
        "object_id": spec.object_id,
        "title": title,
    }


def _validated_image(spec: SourceSpec, response: HttpResponse) -> tuple[str, int, int]:
    if response.status != 200:
        raise AcquisitionError(f"{spec.asset_id}: image returned HTTP {response.status}")
    final = urllib.parse.urlsplit(response.final_url)
    final_host = (final.hostname or "").lower()
    allowed = (
        {"commons.wikimedia.org", "upload.wikimedia.org"}
        if spec.provider == "wikimedia_commons"
        else {"images.metmuseum.org"}
    )
    if final.scheme != "https" or final_host not in allowed:
        raise AcquisitionError(f"{spec.asset_id}: image redirected outside its approved provider")
    content_type = (_header(response, "content-type") or "").split(";", 1)[0].lower()
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise AcquisitionError(f"{spec.asset_id}: response is not an approved image media type")
    if not response.body or len(response.body) > IMAGE_MAX_BYTES:
        raise AcquisitionError(f"{spec.asset_id}: image violates the byte ceiling")
    declared = _header(response, "content-length")
    if declared is not None:
        try:
            if int(declared) != len(response.body):
                raise AcquisitionError(
                    f"{spec.asset_id}: Content-Length does not match image bytes"
                )
        except ValueError as error:
            raise AcquisitionError(f"{spec.asset_id}: invalid image Content-Length") from error
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(response.body)) as image:
                image_format = image.format
                width, height = image.size
                if image_format not in _IMAGE_EXTENSIONS:
                    raise AcquisitionError(f"{spec.asset_id}: unsupported image format")
                if (
                    width <= 0
                    or height <= 0
                    or max(width, height) > IMAGE_MAX_SIDE
                    or width * height > IMAGE_MAX_PIXELS
                ):
                    raise AcquisitionError(f"{spec.asset_id}: image violates the dimension ceiling")
                image.verify()
    except (AcquisitionError, Image.DecompressionBombError):
        raise
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombWarning) as error:
        raise AcquisitionError(f"{spec.asset_id}: payload is not a valid image") from error
    return image_format, width, height


def _rfc3339_utc(value: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AcquisitionError("retrieved_at must be an RFC 3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise AcquisitionError("retrieved_at must be a valid RFC 3339 timestamp") from error
    if parsed.tzinfo != UTC:
        raise AcquisitionError("retrieved_at must be UTC")
    return value


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    schema = json.loads(MANIFEST_SCHEMA_PATH.read_bytes())
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda error: tuple(error.absolute_path))
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise AcquisitionError(
            f"generated demo manifest violates its v1 contract at {location}: {errors[0].message}"
        )


def acquire_dataset(
    *,
    catalog_path: Path,
    destination: Path,
    retrieved_at: str,
    transport: Transport = fetch_url,
) -> dict[str, Any]:
    """Acquire and atomically publish one immutable run directory."""

    catalog = load_catalog(catalog_path)
    timestamp = _rfc3339_utc(retrieved_at)
    output = Path(destination)
    if os.path.lexists(output):
        raise FileExistsError(f"refusing to overwrite immutable acquisition run: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        assets_dir = stage / "assets"
        assets_dir.mkdir()
        rows: list[dict[str, Any]] = []
        for spec in catalog.assets:
            metadata_response = transport(spec.metadata_url, max_bytes=METADATA_MAX_BYTES)
            _validate_metadata_location(spec, metadata_response)
            metadata = _json_response(metadata_response, source=f"{spec.asset_id}: metadata")
            provenance = (
                _commons_provenance(spec, metadata)
                if spec.provider == "wikimedia_commons"
                else _met_provenance(spec, metadata)
            )
            image_response = transport(spec.download_url, max_bytes=IMAGE_MAX_BYTES)
            image_format, width, height = _validated_image(spec, image_response)
            extension = _IMAGE_EXTENSIONS[image_format]
            local_path = Path("assets") / f"{spec.asset_id}{extension}"
            (stage / local_path).write_bytes(image_response.body)
            rows.append(
                {
                    "artist": provenance["artist"],
                    "asset_id": spec.asset_id,
                    "byte_size": len(image_response.body),
                    "collection": spec.collection,
                    "download_url": spec.download_url,
                    "http": {
                        "content_type": (_header(image_response, "content-type") or "").split(
                            ";", 1
                        )[0],
                        "etag": _header(image_response, "etag"),
                        "final_url": image_response.final_url,
                        "last_modified": _header(image_response, "last-modified"),
                    },
                    "image": {"format": image_format, "height": height, "width": width},
                    "khive_content_ref": blake3(image_response.body).hexdigest(),
                    "license": provenance["license"],
                    "local_path": local_path.as_posix(),
                    "metadata": {
                        "evidence_sha256": hashlib.sha256(
                            _canonical_json(provenance["evidence"])
                        ).hexdigest(),
                        "etag": _header(metadata_response, "etag"),
                        "url": spec.metadata_url,
                    },
                    "object_id": provenance["object_id"],
                    "provider": spec.provider,
                    "retrieved_at": timestamp,
                    "sha256": hashlib.sha256(image_response.body).hexdigest(),
                    "source_page_url": spec.page_url,
                    "title": provenance["title"],
                }
            )
        asset_by_sha256: dict[str, str] = {}
        for row in rows:
            previous = asset_by_sha256.setdefault(row["sha256"], row["asset_id"])
            if previous != row["asset_id"]:
                raise AcquisitionError(
                    f"duplicate image bytes for {previous!r} and {row['asset_id']!r}"
                )
        manifest: dict[str, Any] = {
            "asset_count": len(rows),
            "assets": rows,
            "catalog_sha256": catalog.sha256,
            "dataset_id": catalog.dataset_id,
            "retrieved_at": timestamp,
            "schema_version": MANIFEST_SCHEMA,
        }
        _validate_manifest(manifest)
        manifest_bytes = _canonical_json(manifest)
        (stage / "manifest.json").write_bytes(manifest_bytes)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        (stage / "manifest.sha256").write_text(
            f"{manifest_sha256}  manifest.json\n", encoding="utf-8"
        )
        os.replace(stage, output)
        return manifest
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m moodboard.demo_data",
        description="Fetch the reviewed public-domain corpus into one immutable run directory.",
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--retrieved-at",
        help="RFC 3339 UTC time to bind into the run; defaults to the current UTC second.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    retrieved_at = arguments.retrieved_at or datetime.now(UTC).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    try:
        manifest = acquire_dataset(
            catalog_path=arguments.catalog,
            destination=arguments.output,
            retrieved_at=retrieved_at,
        )
    except (AcquisitionError, FileExistsError, OSError) as error:
        raise SystemExit(f"BLOCKED: {error}") from error
    digest = hashlib.sha256((arguments.output / "manifest.json").read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "asset_count": manifest["asset_count"],
                "dataset_id": manifest["dataset_id"],
                "manifest": str((arguments.output / "manifest.json").resolve()),
                "manifest_sha256": digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a command in the real smoke run
    raise SystemExit(main())

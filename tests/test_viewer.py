"""The offline viewer package is one closed, atomic file boundary."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from moodboard import report as report_module
from moodboard import viewer as viewer_module
from moodboard.viewer import (
    ViewerPackagingError,
    inline_report,
    validate_viewer_package,
)

REPOSITORY = Path(__file__).resolve().parents[1]
PACKAGE_DATA = REPOSITORY / "moodboard" / "viewer_dist"
SHOWCASE_REPORT = REPOSITORY / "viewer" / "tests" / "fixtures" / "showcase" / "report.json"
PAYLOAD = re.compile(
    rb'<script type="application/octet-stream" id="moodboard-report">'
    rb"([A-Za-z0-9+/]+={0,2})</script>"
)
STANDALONE_CSP = (
    "default-src 'none'; script-src data:; style-src data:; img-src data:; "
    "connect-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
)
CSP_META = f'<meta http-equiv="Content-Security-Policy" content="{STANDALONE_CSP}" />'.encode()


@pytest.fixture(scope="module")
def viewer_package():
    if not PACKAGE_DATA.is_dir():
        pytest.skip("run `npm --prefix viewer run build` to stage verified viewer package data")
    return validate_viewer_package(PACKAGE_DATA)


def test_viewer_package_is_closed_and_hash_verified(viewer_package):
    expected = {
        "artifact-manifest.json",
        "artifact-manifest.schema.json",
        "consumer-contract.json",
        "index.html",
        "schemas/report_v1_0.schema.json",
        "schemas/report_v1_1.schema.json",
        "standalone-template.html",
        "verification-toolchain.json",
        *(entry["path"] for entry in viewer_package.manifest["assets"]),
    }
    actual = {
        path.relative_to(viewer_package.root).as_posix()
        for path in viewer_package.root.rglob("*")
        if path.is_file()
    }
    assert actual == expected


def test_real_engine_showcase_inlines_as_one_inert_offline_payload(tmp_path, viewer_package):
    destination = tmp_path / "showcase.html"
    inline_report(SHOWCASE_REPORT, destination, package_root=viewer_package.root)
    html = destination.read_bytes()
    payloads = PAYLOAD.findall(html)
    assert len(payloads) == 1
    assert base64.b64decode(payloads[0], validate=True) == SHOWCASE_REPORT.read_bytes()
    assert re.search(rb'\b(?:src|href)=["\'](?!data:)', html, flags=re.IGNORECASE) is None
    assert html.count(CSP_META) == 1
    assert b"connect-src 'none'" in html
    assert b"__MOODBOARD_REPORT_BASE64__" not in html


def test_report_text_cannot_terminate_the_inert_payload(tmp_path, viewer_package):
    report = json.loads(SHOWCASE_REPORT.read_text(encoding="utf-8"))
    dangerous = '</script><script data-unsafe="true">globalThis.pwned=true</script>'
    report["board"]["name"] = dangerous
    report["assets"][0]["source"] = dangerous
    report_path = tmp_path / "literal-text.json"
    report_bytes = (json.dumps(report, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    report_path.write_bytes(report_bytes)

    destination = tmp_path / "literal-text.html"
    inline_report(report_path, destination, package_root=viewer_package.root)
    html = destination.read_bytes()
    assert dangerous.encode() not in html
    payload = PAYLOAD.findall(html)
    assert len(payload) == 1
    assert base64.b64decode(payload[0], validate=True) == report_bytes
    assert b'<script data-unsafe="true">' not in html


def test_non_finite_json_constant_is_rejected_before_publication(tmp_path, viewer_package):
    report = json.loads(SHOWCASE_REPORT.read_text(encoding="utf-8"))
    report["board"]["n_eff"] = float("nan")
    report_path = tmp_path / "non-finite.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    destination = tmp_path / "existing.html"
    destination.write_bytes(b"sentinel")

    with pytest.raises(ViewerPackagingError, match="non-finite JSON constant NaN"):
        inline_report(report_path, destination, package_root=viewer_package.root)

    assert destination.read_bytes() == b"sentinel"


def test_invalid_report_preserves_existing_destination_and_leaves_no_temporary(
    tmp_path, viewer_package
):
    report = json.loads(SHOWCASE_REPORT.read_text(encoding="utf-8"))
    report["assets"][0]["exemplars"][1] = report["assets"][0]["exemplars"][0]
    report_path = tmp_path / "duplicate-exemplar.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    destination = tmp_path / "existing.html"
    destination.write_bytes(b"sentinel")

    with pytest.raises(ViewerPackagingError, match="duplicate exemplar"):
        inline_report(report_path, destination, package_root=viewer_package.root)

    assert destination.read_bytes() == b"sentinel"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def test_package_mutation_fails_before_output_is_touched(tmp_path, viewer_package):
    corrupted = tmp_path / "viewer-dist"
    shutil.copytree(viewer_package.root, corrupted)
    template = corrupted / "standalone-template.html"
    template.write_bytes(template.read_bytes() + b"\n")
    destination = tmp_path / "existing.html"
    destination.write_bytes(b"sentinel")

    with pytest.raises(ViewerPackagingError, match="hash mismatch"):
        inline_report(SHOWCASE_REPORT, destination, package_root=corrupted)

    assert destination.read_bytes() == b"sentinel"


def test_rehashed_template_cannot_weaken_the_standalone_csp(tmp_path, viewer_package):
    corrupted = tmp_path / "viewer-dist"
    shutil.copytree(viewer_package.root, corrupted)
    template = corrupted / "standalone-template.html"
    weakened = template.read_bytes().replace(b"connect-src 'none'", b"connect-src https:")
    assert weakened != template.read_bytes()
    template.write_bytes(weakened)

    manifest_path = corrupted / "artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["template"]["sha256"] = hashlib.sha256(weakened).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ViewerPackagingError, match="Content Security Policy"):
        validate_viewer_package(corrupted)


def test_output_cannot_replace_the_input_report(tmp_path, viewer_package):
    report = tmp_path / "report.json"
    report.write_bytes(SHOWCASE_REPORT.read_bytes())

    with pytest.raises(ViewerPackagingError, match="cannot replace the input report"):
        inline_report(report, report, package_root=viewer_package.root)

    assert report.read_bytes() == SHOWCASE_REPORT.read_bytes()


def test_shared_report_limit_preserves_existing_output(tmp_path, viewer_package, monkeypatch):
    destination = tmp_path / "existing.html"
    destination.write_bytes(b"sentinel")
    monkeypatch.setattr(report_module, "REPORT_MAX_BYTES", 1)

    with pytest.raises(ViewerPackagingError, match="report limit"):
        inline_report(SHOWCASE_REPORT, destination, package_root=viewer_package.root)

    assert destination.read_bytes() == b"sentinel"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def _solid_rgb_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    compressor = zlib.compressobj(level=9)
    compressed = bytearray()
    row = b"\0" + (b"\0" * (width * 3))
    for _ in range(height):
        compressed.extend(compressor.compress(row))
    compressed.extend(compressor.flush())
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", bytes(compressed))
        + chunk(b"IEND", b"")
    )


def test_compressed_bomb_thumbnail_is_rejected_before_decode(tmp_path, viewer_package):
    report = json.loads(SHOWCASE_REPORT.read_text(encoding="utf-8"))
    side = 4_097
    bomb = _solid_rgb_png(side, side)
    assert len(bomb) < 128 * 1024
    thumbnail = report["references"][0]["thumbnail"]
    thumbnail.update(
        {
            "mime": "image/png",
            "width": side,
            "height": side,
            "data_base64": base64.b64encode(bomb).decode("ascii"),
        }
    )
    report_path = tmp_path / "compressed-bomb.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    destination = tmp_path / "existing.html"
    destination.write_bytes(b"sentinel")

    with pytest.raises(ViewerPackagingError, match="pixel decode limit"):
        inline_report(report_path, destination, package_root=viewer_package.root)

    assert destination.read_bytes() == b"sentinel"


def test_thumbnail_count_preflight_rejects_before_any_pillow_work(
    tmp_path, viewer_package, monkeypatch
):
    report = json.loads(SHOWCASE_REPORT.read_text(encoding="utf-8"))
    prototype = report["references"][0]
    target_references = report_module.THUMBNAIL_MAX_COUNT - len(report["assets"]) + 1
    for index in range(len(report["references"]), target_references):
        reference = copy.deepcopy(prototype)
        reference["reference_id"] = f"preflight-{index:04d}"
        report["references"].append(reference)
    report["board"]["n_references"] = len(report["references"])
    report_path = tmp_path / "too-many-thumbnails.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    destination = tmp_path / "existing.html"
    destination.write_bytes(b"sentinel")
    pillow_calls = 0

    def forbidden_pillow(*args, **kwargs):
        nonlocal pillow_calls
        pillow_calls += 1
        raise AssertionError("Pillow must not run before the count preflight")

    monkeypatch.setattr(viewer_module.Image, "open", forbidden_pillow)

    with pytest.raises(ViewerPackagingError, match="512-thumbnail decode limit"):
        inline_report(report_path, destination, package_root=viewer_package.root)

    assert pillow_calls == 0
    assert destination.read_bytes() == b"sentinel"


def test_declared_aggregate_preflight_rejects_before_any_pillow_work(
    tmp_path, viewer_package, monkeypatch
):
    report_path = tmp_path / "aggregate.json"
    report_path.write_bytes(SHOWCASE_REPORT.read_bytes())
    destination = tmp_path / "existing.html"
    destination.write_bytes(b"sentinel")
    monkeypatch.setattr(viewer_module, "_MAX_TOTAL_THUMBNAIL_DECODED_BYTES", 1)
    pillow_calls = 0

    def forbidden_pillow(*args, **kwargs):
        nonlocal pillow_calls
        pillow_calls += 1
        raise AssertionError("Pillow must not run before the aggregate preflight")

    monkeypatch.setattr(viewer_module.Image, "open", forbidden_pillow)

    with pytest.raises(ViewerPackagingError, match="aggregate 1-byte raster limit"):
        inline_report(report_path, destination, package_root=viewer_package.root)

    assert pillow_calls == 0
    assert destination.read_bytes() == b"sentinel"


def test_shared_report_aggregate_preflight_rejects_before_pillow(monkeypatch):
    document = json.loads(SHOWCASE_REPORT.read_text(encoding="utf-8"))
    monkeypatch.setattr(report_module, "THUMBNAIL_TOTAL_DECODED_BYTES", 1)
    pillow_calls = 0

    def forbidden_pillow(*args, **kwargs):
        nonlocal pillow_calls
        pillow_calls += 1
        raise AssertionError("Pillow must not run before the shared aggregate preflight")

    monkeypatch.setattr(report_module.Image, "open", forbidden_pillow)

    with pytest.raises(ValueError, match="aggregate 1-byte raster limit"):
        report_module.from_json_dict(document)

    assert pillow_calls == 0

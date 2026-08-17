from __future__ import annotations

import base64
import copy
import hashlib
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from threading import Event, Lock
from typing import Any

import pytest
from blake3 import blake3
from PIL import Image, ImageCms

import moodboard.locality as locality_module
from moodboard.judgment import to_json_dict as judgment_to_json
from moodboard.locality import (
    COMPILER_REVISION,
    DEFAULT_COMPILER_MANIFEST,
    MASK_COMPILER_REVISION,
    LocalityError,
    build_locality_not_run,
    compile_canonical_raster,
    compile_rectangle_mask,
    verify_output_structure,
    verify_outside_mask_rgb_exact,
)
from moodboard.provider_artifacts import (
    OUTPUT_VERSION,
    RECEIPT_VERSION,
    OutputOccurrence,
    ProviderReceipt,
    seal_provider_artifact,
)
from moodboard.provider_artifacts import (
    to_json_dict as provider_to_json,
)
from tests.test_provider_artifacts import _artifact, _valid_artifact_chain

JsonObject = dict[str, Any]

_PNG_GOLDEN = (
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjd"
    "AAAAD0lEQVR4nGNkZGJmZmYGAAA8ABHVfkbmAAAAAElFTkSuQmCC"
)
_JPEG_GOLDEN = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYF"
    "BgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/wAALCAABAAIBAREA/8QAHwAAAQUBAQEB"
    "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1Fh"
    "ByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZ"
    "WmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APsj9gD/AJMO+Cf/AGSP"
    "w3/6a7ev/9k="
)
_JPEG_RGB_PROGRESSIVE_ICC_ORIENTED_GOLDEN = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/4QAiRXhpZgAATU0AKgAAAAgAAQESAAMAAAABAAYAAAAAAAD/4gJcSUNDX1BS"
    "T0ZJTEUAAQEAAAJMbGNtcwRAAABtbnRyUkdCIFhZWiAH0AABAAEAAAAAAABhY3NwQVBQTAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAA9tYAAQAAAADTLWxjbXMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAtkZXNjAAABCAAAADZjcHJ0AAABQAAAAEx3dHB0AAABjAAAABRjaGFkAAABoAAAACxyWFlaAAABzAAA"
    "ABRiWFlaAAAB4AAAABRnWFlaAAAB9AAAABRyVFJDAAACCAAAACBnVFJDAAACCAAAACBiVFJDAAACCAAAACBjaHJt"
    "AAACKAAAACRtbHVjAAAAAAAAAAEAAAAMZW5VUwAAABoAAAAcAHMAUgBHAEIAIABiAHUAaQBsAHQALQBpAG4AAG1s"
    "dWMAAAAAAAAAAQAAAAxlblVTAAAAMAAAABwATgBvACAAYwBvAHAAeQByAGkAZwBoAHQALAAgAHUAcwBlACAAZgBy"
    "AGUAZQBsAHlYWVogAAAAAAAA9tYAAQAAAADTLXNmMzIAAAAAAAEMQgAABd7///MlAAAHkwAA/ZD///uh///9ogAA"
    "A9wAAMBuWFlaIAAAAAAAAG+gAAA49QAAA5BYWVogAAAAAAAAJJ8AAA+EAAC2w1hZWiAAAAAAAABilwAAt4cAABjZ"
    "cGFyYQAAAAAAAwAAAAJmZgAA8qcAAA1ZAAAT0AAACltjaHJtAAAAAAADAAAAAKPXAABUewAATM0AAJmaAAAmZgAA"
    "D1z/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsK"
    "DAkKCgr/2wBDAQICAgICAgUDAwUKBwYHCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoK"
    "CgoKCgoKCgr/wgARCAACAAMDAREAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAAAf/EABUBAQEAAAAAAAAAAAAA"
    "AAAAAAQH/9oADAMBAAIQAxAAAAEJdP/EABYQAQEBAAAAAAAAAAAAAAAAAAQFBv/aAAgBAQABBQLTELOsf//EAB4R"
    "AAIBAwUAAAAAAAAAAAAAAAECAwQFBgASITJB/9oACAEDAQE/AcIs1nq8Yglnp0Zju5KKT3b0jX//xAAYEQEBAAMA"
    "AAAAAAAAAAAAAAABAwACBP/aAAgBAgEBPwHqrTW6Dn//xAAcEAACAgIDAAAAAAAAAAAAAAACAwEEAAUSI0H/2gAI"
    "AQEABj8CKnr6y0JBKuCkhAjHWPkZ/8QAGBABAQADAAAAAAAAAAAAAAAAAREAIXH/2gAIAQEAAT8h3b0QV2QCqvXP"
    "/9oADAMBAAIAAwAAABA//8QAGBEBAQEBAQAAAAAAAAAAAAAAAREhADH/2gAIAQMBAT8Q1M9cIRULAArgB53/xAAX"
    "EQEBAQEAAAAAAAAAAAAAAAABIQBR/9oACAECAQE/EGthILw3/8QAGBABAAMBAAAAAAAAAAAAAAAAAQARITH/2gAI"
    "AQEAAT8Q5gA3PfsIo1F1Z//Z"
)
_PNG_ICC_GOLDEN = (
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAABdGlDQ1BJQ0MgUHJvZmlsZQAAeJx1kT1Lw1AUht+2"
    "SkUrHXQQcchQxaEFURBHjUOXIqVWsOqS3CatkKThJkGKq+DiUHAQXfwa/Ae6Cq4KBUERRPwNfi1S4rlNoUXac7k5"
    "D2/Oezg5AcIZg5lO3yJgWi7PpWVpvbAhResI0WmGwhx7KZvNoGf8PAW1jynRq3dd1xgqag4DQgPE88zmLjFNg8yO"
    "aws+IB5lZaVIfEac5DQg8b3Q1YDfBZcC/hLM87llICx6SqUOVjuYlblJPE2cMA2PteYRXxLTrLVVyuN0J+AghzRk"
    "SFDhYRsGXKQoW7Sz7r6Zpm8FFfIwetqogpOjhDJ5k6R61FWjrJOu0TFQFXv/v09Hn5sNusdkoP/N9z8ngegh0Kj5"
    "/u+57zcugMgrcGu1/RXa08I36bW2ljgF4nvA9V1bU4+Am31g7MVWuNKUInTDug58XAHDBWCkDgxuBrtqvcflM5Df"
    "pV/0AByfAFNUH9/6AwT9Z5bLPogwAAAAD0lEQVR4nGMUrP7PwMAAAAbXAY2HsDMGAAAAAElFTkSuQmCC"
)


def _image_bytes(
    mode: str = "RGB",
    *,
    size: tuple[int, int] = (4, 3),
    pixels: list[Any] | None = None,
    image_format: str = "PNG",
    orientation: int | None = None,
) -> bytes:
    value: Any
    if mode == "L":
        value = 31
    elif mode == "LA":
        value = (31, 255)
    elif mode == "RGBA":
        value = (11, 22, 33, 255)
    elif mode == "P":
        value = 1
    else:
        value = (11, 22, 33)
    image = Image.new(mode, size, value)
    if pixels is not None:
        image.putdata(pixels)
    kwargs: dict[str, Any] = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        kwargs["exif"] = exif
    buffer = BytesIO()
    image.save(buffer, format=image_format, **kwargs)
    return buffer.getvalue()


def _compile(payload: bytes):
    return compile_canonical_raster(
        payload,
        source_content_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _pinned_srgb_profile() -> bytes:
    profile = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    profile[24:36] = struct.pack(">6H", 2000, 1, 1, 0, 0, 0)
    assert hashlib.sha256(profile).hexdigest() == (
        "6f6fe5cc53cd24ceeb7997fb24ce2889fdfb88d88ce4fdc5f8e25e0481294953"
    )
    return bytes(profile)


def _insert_png_chunk(payload: bytes, chunk_type: bytes, data: bytes) -> bytes:
    position = payload.index(b"IDAT") - 4
    chunk = _encoded_png_chunk(chunk_type, data)
    return payload[:position] + chunk + payload[position:]


def _encoded_png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        len(data).to_bytes(4, "big")
        + chunk_type
        + data
        + (zlib.crc32(chunk_type + data) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def _png_chunk(payload: bytes, chunk_type: bytes) -> bytes:
    marker = payload.index(chunk_type)
    start = marker - 4
    length = int.from_bytes(payload[start:marker], "big")
    return payload[start : marker + 4 + length + 4]


def _jpeg_app1(payload: bytes) -> bytes:
    marker = payload.index(b"\xff\xe1")
    length = int.from_bytes(payload[marker + 2 : marker + 4], "big")
    return payload[marker : marker + 2 + length]


def _rewrite_png_dimensions(payload: bytes, *, width: int, height: int) -> bytes:
    rewritten = bytearray(payload)
    rewritten[16:20] = width.to_bytes(4, "big")
    rewritten[20:24] = height.to_bytes(4, "big")
    rewritten[29:33] = (zlib.crc32(b"IHDR" + rewritten[16:29]) & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(rewritten)


def _provider_evidence(
    payload: bytes,
    *,
    width: int,
    height: int,
    measured_mode: str,
    mime: str = "image/png",
) -> tuple[ProviderReceipt, OutputOccurrence]:
    _, artifacts = _valid_artifact_chain()
    receipt = copy.deepcopy(_artifact(artifacts, RECEIPT_VERSION))
    row = receipt["outputs"][0]
    row.update(
        {
            "content_ref": blake3(payload).hexdigest(),
            "content_sha256": hashlib.sha256(payload).hexdigest(),
            "byte_count": len(payload),
            "media_type_claim": mime,
        }
    )
    receipt.pop("provider_receipt_id")
    sealed_receipt = seal_provider_artifact(receipt)
    assert isinstance(sealed_receipt, ProviderReceipt)

    output = copy.deepcopy(_artifact(artifacts, OUTPUT_VERSION))
    output.pop("output_occurrence_id")
    output["provider_receipt_id"] = sealed_receipt.provider_receipt_id
    output["original"].update(
        {
            "content_ref": row["content_ref"],
            "content_sha256": row["content_sha256"],
            "mime": mime,
            "byte_count": len(payload),
            "width": width,
            "height": height,
        }
    )
    output["media_validation"].update(
        {
            "decoder_revision": COMPILER_REVISION,
            "measured_content_sha256": row["content_sha256"],
            "measured_content_ref": row["content_ref"],
            "measured_byte_count": len(payload),
            "measured_mime": mime,
            "measured_width": width,
            "measured_height": height,
            "measured_mode": measured_mode,
        }
    )
    sealed_output = seal_provider_artifact(output)
    assert isinstance(sealed_output, OutputOccurrence)
    return sealed_receipt, sealed_output


def _structural(source, payload: bytes):
    decoded = _compile(payload)
    receipt, occurrence = _provider_evidence(
        payload,
        width=decoded.width,
        height=decoded.height,
        measured_mode="RGB",
    )
    result = verify_output_structure(
        source,
        provider_receipt=receipt,
        output_index=0,
        output_bytes=payload,
        output_occurrence=occurrence,
    )
    return result, occurrence


def test_manifest_and_dependency_are_exactly_pinned() -> None:
    assert COMPILER_REVISION == "moodboard.raster-compiler.pillow-12.3.0-pngjpeg8-icc1.v1"
    assert DEFAULT_COMPILER_MANIFEST.pillow_version == "12.3.0"
    assert DEFAULT_COMPILER_MANIFEST.pinned_srgb_profile_sha256 == (
        "6f6fe5cc53cd24ceeb7997fb24ce2889fdfb88d88ce4fdc5f8e25e0481294953"
    )


@pytest.mark.parametrize(
    ("encoded_b64", "payload_sha256", "rgb_bytes", "raster_sha256"),
    (
        (
            _PNG_GOLDEN,
            "0999c85bac983f785dbc678837371110ac90295a345e460a6e9e947b469831d7",
            bytes((1, 2, 3, 4, 5, 6)),
            "956bae7aeef512a8adcbdebeea8802bcc00c92eb708bb3e7ca609b7abb645798",
        ),
        (
            _JPEG_GOLDEN,
            "3b14696f02358f17faec779175fe5b446f934b103a938be3278d26140917247e",
            bytes((17, 17, 17, 201, 201, 201)),
            "5efcae766b977b944a9cd4dc0bbdd777216c695934d3eb88b20ee00e9e35e258",
        ),
        (
            _JPEG_RGB_PROGRESSIVE_ICC_ORIENTED_GOLDEN,
            "753470d35ee5df49164d5c01372cfb3804e0a88aa6436b0f0161b78f077758ba",
            bytes.fromhex("9faaae09161fd4e1e73b4254f32f5d6e7975"),
            "b75babb2b0deb163bf9a25ee85cb915e5a9b5e202fec4ea2f2da850770159e18",
        ),
        (
            _PNG_ICC_GOLDEN,
            "cbdc792d0ef70fce829c84036c3bfcb6b57be96b9b1a4d83422654e355895a3c",
            bytes.fromhex("117bff117bff"),
            "2884a5ebe2113f2bf7e2fde1b003d693e789a44e766f8c7bf9470133258b65e8",
        ),
    ),
)
def test_static_source_format_goldens_pin_payload_pixels_and_raster_identity(
    encoded_b64: str,
    payload_sha256: str,
    rgb_bytes: bytes,
    raster_sha256: str,
) -> None:
    payload = base64.b64decode(encoded_b64, validate=True)
    assert hashlib.sha256(payload).hexdigest() == payload_sha256
    raster = compile_canonical_raster(payload, source_content_sha256=payload_sha256)
    assert raster.rgb_bytes == rgb_bytes
    assert raster.raster_sha256 == raster_sha256


def test_manifest_drift_fails_before_compilation() -> None:
    payload = _image_bytes()
    drifted = replace(DEFAULT_COMPILER_MANIFEST, pillow_version="12.3.1")
    with pytest.raises(LocalityError, match="manifest") as captured:
        compile_canonical_raster(
            payload,
            source_content_sha256=hashlib.sha256(payload).hexdigest(),
            compiler_manifest=drifted,
        )
    assert captured.value.code == "compiler_manifest_mismatch"


def test_registered_linux_zlib_compatibility_label_uses_the_same_zlib_ng_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = locality_module._runtime_manifest()
    monkeypatch.setattr(
        locality_module,
        "_runtime_manifest",
        lambda: replace(runtime, zlib_version="1.3"),
    )
    payload = base64.b64decode(_PNG_GOLDEN, validate=True)
    raster = compile_canonical_raster(
        payload,
        source_content_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert raster.rgb_bytes == bytes((1, 2, 3, 4, 5, 6))


def test_unregistered_zlib_compatibility_label_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = locality_module._runtime_manifest()
    monkeypatch.setattr(
        locality_module,
        "_runtime_manifest",
        lambda: replace(runtime, zlib_version="1.2.13"),
    )
    payload = base64.b64decode(_PNG_GOLDEN, validate=True)
    with pytest.raises(LocalityError, match="manifest") as captured:
        compile_canonical_raster(
            payload,
            source_content_sha256=hashlib.sha256(payload).hexdigest(),
        )
    assert captured.value.code == "compiler_manifest_mismatch"


def test_registered_compiler_calls_are_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _image_bytes()
    _compile(payload)  # Populate the immutable runtime certificate before instrumenting preflight.
    original = locality_module._preflight
    first_entered = Event()
    second_entered = Event()
    release_first = Event()
    counter_lock = Lock()
    calls = 0

    def _instrumented_preflight(encoded: bytes, manifest):
        nonlocal calls
        with counter_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(2)
        else:
            second_entered.set()
        return original(encoded, manifest)

    monkeypatch.setattr(locality_module, "_preflight", _instrumented_preflight)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_compile, payload)
        assert first_entered.wait(2)
        second = executor.submit(_compile, payload)
        assert not second_entered.wait(0.05)
        release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)
    assert second_entered.is_set()


def test_any_decoder_warning_rejects_the_raster(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _image_bytes()
    _compile(payload)
    original = locality_module._convert_color

    def _warning_color_convert(image: Image.Image, *, opaque: bool) -> Image.Image:
        import warnings

        warnings.warn("synthetic decoder warning", UserWarning, stacklevel=1)
        return original(image, opaque=opaque)

    monkeypatch.setattr(locality_module, "_convert_color", _warning_color_convert)
    with pytest.raises(LocalityError) as captured:
        _compile(payload)
    assert captured.value.code == "unsafe_decoder_warning"


@pytest.mark.parametrize("mode", ("L", "LA", "RGB", "RGBA"))
def test_png_registered_opaque_modes_compile_to_frozen_rgb(mode: str) -> None:
    payload = _image_bytes(mode)
    raster = _compile(payload)
    assert raster.mode == "RGB"
    assert raster.byte_count == raster.width * raster.height * 3
    assert len(raster.rgb_bytes) == raster.byte_count


def test_jpeg_orientation_is_applied_once_before_dimensions() -> None:
    payload = _image_bytes(size=(3, 2), image_format="JPEG", orientation=6)
    raster = _compile(payload)
    assert (raster.width, raster.height) == (2, 3)


@pytest.mark.parametrize(
    ("orientation", "width", "height", "red_channels"),
    (
        (1, 3, 2, (10, 20, 30, 40, 50, 60)),
        (2, 3, 2, (30, 20, 10, 60, 50, 40)),
        (3, 3, 2, (60, 50, 40, 30, 20, 10)),
        (4, 3, 2, (40, 50, 60, 10, 20, 30)),
        (5, 2, 3, (10, 40, 20, 50, 30, 60)),
        (6, 2, 3, (40, 10, 50, 20, 60, 30)),
        (7, 2, 3, (60, 30, 50, 20, 40, 10)),
        (8, 2, 3, (30, 60, 20, 50, 10, 40)),
    ),
)
def test_all_exif_orientations_have_independent_pixel_goldens(
    orientation: int,
    width: int,
    height: int,
    red_channels: tuple[int, ...],
) -> None:
    pixels = [(value, 0, 0) for value in (10, 20, 30, 40, 50, 60)]
    payload = _image_bytes(pixels=pixels, size=(3, 2), orientation=orientation)
    raster = _compile(payload)
    assert (raster.width, raster.height) == (width, height)
    assert tuple(raster.rgb_bytes[::3]) == red_channels


@pytest.mark.parametrize("orientation", (0, 9))
def test_malformed_exif_orientation_fails_closed(orientation: int) -> None:
    payload = _image_bytes(orientation=orientation)
    with pytest.raises(LocalityError) as captured:
        _compile(payload)
    assert captured.value.code == "malformed_orientation"


@pytest.mark.parametrize("image_format", ("PNG", "JPEG"))
def test_conflicting_duplicate_exif_blocks_fail_closed(image_format: str) -> None:
    first = _image_bytes(image_format=image_format, orientation=2)
    second = _image_bytes(image_format=image_format, orientation=6)
    if image_format == "PNG":
        duplicate = _png_chunk(second, b"eXIf")
        insertion = first.index(b"IDAT") - 4
    else:
        duplicate = _jpeg_app1(second)
        insertion = first.index(b"\xff\xda")
    candidate = first[:insertion] + duplicate + first[insertion:]
    with pytest.raises(LocalityError) as captured:
        _compile(candidate)
    assert captured.value.code == "malformed_orientation"


@pytest.mark.parametrize(
    ("segment", "code"),
    (
        (_jpeg_app1(_image_bytes(image_format="JPEG", orientation=6)), "malformed_orientation"),
        (b"\xff\xe2\x00\x12ICC_PROFILE\x00\x01\x01bad", "unsupported_color_contract"),
        (b"\xff\xe2\x00\x08MPF\x00xx", "unsupported_format"),
    ),
)
def test_jpeg_rejects_all_post_scan_application_metadata(segment: bytes, code: str) -> None:
    payload = _image_bytes(image_format="JPEG", orientation=1)
    candidate = payload[:-2] + segment + payload[-2:]
    with pytest.raises(LocalityError) as captured:
        _compile(candidate)
    assert captured.value.code == code


def test_pre_scan_mpf_and_apng_are_rejected_as_multiple_frames() -> None:
    jpeg = _image_bytes(image_format="JPEG")
    insertion = jpeg.index(b"\xff\xda")
    mpf = jpeg[:insertion] + b"\xff\xe2\x00\x08MPF\x00xx" + jpeg[insertion:]
    with pytest.raises(LocalityError) as captured:
        _compile(mpf)
    assert captured.value.code == "unsupported_format"

    png = _insert_png_chunk(_image_bytes(), b"acTL", struct.pack(">II", 2, 0))
    with pytest.raises(LocalityError) as captured:
        _compile(png)
    assert captured.value.code == "unsupported_format"


@pytest.mark.parametrize("dimensions", ((8_193, 1), (4_097, 4_097)))
def test_png_dimensions_fail_before_pixel_allocation(dimensions: tuple[int, int]) -> None:
    payload = _rewrite_png_dimensions(
        _image_bytes(size=(1, 1)), width=dimensions[0], height=dimensions[1]
    )
    with pytest.raises(LocalityError) as captured:
        _compile(payload)
    assert captured.value.code == "decode_limit_exceeded"


def test_jpeg_marker_count_is_bounded() -> None:
    payload = _image_bytes(image_format="JPEG")
    insertion = payload.index(b"\xff\xda")
    comments = b"\xff\xfe\x00\x02" * (DEFAULT_COMPILER_MANIFEST.max_jpeg_segments + 1)
    candidate = payload[:insertion] + comments + payload[insertion:]
    with pytest.raises(LocalityError) as captured:
        _compile(candidate)
    assert captured.value.code == "decode_limit_exceeded"


def test_pinned_icc_profile_is_converted_and_any_profile_drift_rejects() -> None:
    image = Image.new("RGB", (2, 1), (17, 123, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG", icc_profile=_pinned_srgb_profile())
    accepted = _compile(buffer.getvalue())
    assert accepted.rgb_bytes == bytes((17, 123, 255, 17, 123, 255))

    drifted_profile = bytearray(_pinned_srgb_profile())
    drifted_profile[-1] ^= 1
    buffer = BytesIO()
    image.save(buffer, format="PNG", icc_profile=bytes(drifted_profile))
    with pytest.raises(LocalityError) as captured:
        _compile(buffer.getvalue())
    assert captured.value.code == "unsupported_color_contract"


def test_color_engine_failures_are_normalized_to_structural_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _compile(_image_bytes())  # Certify the real runtime before injecting a post-gate CMS failure.
    image = Image.new("RGB", (2, 1), (17, 123, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG", icc_profile=_pinned_srgb_profile())

    def _fail_color_transform(*_args: Any, **_kwargs: Any) -> None:
        raise ImageCms.PyCMSError("synthetic color engine failure")

    monkeypatch.setattr(ImageCms, "profileToProfile", _fail_color_transform)
    with pytest.raises(LocalityError) as captured:
        _compile(buffer.getvalue())
    assert captured.value.code == "unsupported_color_contract"


def test_grayscale_with_embedded_icc_is_not_silently_reinterpreted() -> None:
    image = Image.new("L", (2, 1), 17)
    buffer = BytesIO()
    image.save(buffer, format="PNG", icc_profile=_pinned_srgb_profile())
    with pytest.raises(LocalityError) as captured:
        _compile(buffer.getvalue())
    assert captured.value.code == "unsupported_color_contract"


@pytest.mark.parametrize("mode", ("L", "RGB"))
def test_registered_jpeg_modes_compile(mode: str) -> None:
    assert _compile(_image_bytes(mode, image_format="JPEG")).mode == "RGB"


def test_jpeg_cmyk_and_trailing_bytes_are_rejected() -> None:
    buffer = BytesIO()
    Image.new("CMYK", (2, 1), (0, 10, 20, 30)).save(buffer, format="JPEG")
    with pytest.raises(LocalityError) as captured:
        _compile(buffer.getvalue())
    assert captured.value.code == "unsupported_color_contract"

    with pytest.raises(LocalityError) as captured:
        _compile(_image_bytes(image_format="JPEG") + b"trailing")
    assert captured.value.code == "decode_failed"


def test_jpeg_rejects_bytes_after_first_eoi_and_concatenated_images() -> None:
    payload = _image_bytes(image_format="JPEG")
    for candidate in (payload + b"JUNK\xff\xd9", payload + payload):
        with pytest.raises(LocalityError) as captured:
            _compile(candidate)
        assert captured.value.code == "decode_failed"


def test_jpeg_rejects_incomplete_icc_chunk_sets_even_when_pillow_drops_them() -> None:
    image = Image.new("RGB", (2, 1), (17, 123, 255))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", icc_profile=_pinned_srgb_profile())
    payload = bytearray(buffer.getvalue())
    marker = payload.index(b"ICC_PROFILE\x00")
    payload[marker + 13] = 2
    with pytest.raises(LocalityError) as captured:
        _compile(bytes(payload))
    assert captured.value.code == "unsupported_color_contract"


@pytest.mark.parametrize(
    ("chunk_type", "chunk_data"),
    (
        (b"sRGB", b"\x00"),
        (b"gAMA", (45_455).to_bytes(4, "big")),
        (b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)),
        (b"iCCP", b"sRGB\x00\x00" + zlib.compress(_pinned_srgb_profile())),
    ),
)
def test_png_rejects_duplicate_color_authorities(chunk_type: bytes, chunk_data: bytes) -> None:
    payload = _image_bytes()
    insertion = payload.index(b"IDAT") - 4
    prefix = b""
    if chunk_type in {b"gAMA", b"cHRM"}:
        prefix = _encoded_png_chunk(b"sRGB", b"\x00")
    duplicate = _encoded_png_chunk(chunk_type, chunk_data) * 2
    candidate = payload[:insertion] + prefix + duplicate + payload[insertion:]
    with pytest.raises(LocalityError) as captured:
        _compile(candidate)
    assert captured.value.code == "unsupported_color_contract"


@pytest.mark.parametrize(
    ("chunk_type", "chunk_data"),
    (
        (b"cICP", bytes((9, 16, 0, 1))),
        (b"sBIT", bytes((5, 5, 5))),
        (b"mDCv", bytes(24)),
        (b"cLLi", bytes(8)),
    ),
)
def test_png_rejects_unregistered_color_and_hdr_declarations(
    chunk_type: bytes, chunk_data: bytes
) -> None:
    payload = _insert_png_chunk(_image_bytes(), chunk_type, chunk_data)
    with pytest.raises(LocalityError) as captured:
        _compile(payload)
    assert captured.value.code == "unsupported_color_contract"


def test_jpeg_rejects_aggregate_icc_bytes_above_the_registered_bound() -> None:
    payload = _image_bytes(image_format="JPEG")
    insertion = payload.index(b"\xff\xda")
    parts: list[bytes] = []
    chunk_payload = b"x" * 65_500
    total = 17
    for sequence in range(1, total + 1):
        data = b"ICC_PROFILE\x00" + bytes((sequence, total)) + chunk_payload
        parts.append(b"\xff\xe2" + (len(data) + 2).to_bytes(2, "big") + data)
    oversized = payload[:insertion] + b"".join(parts) + payload[insertion:]
    with pytest.raises(LocalityError) as captured:
        _compile(oversized)
    assert captured.value.code == "decode_limit_exceeded"


def test_png_crc_truncation_and_trailing_polyglot_are_rejected() -> None:
    payload = _image_bytes()
    idat = payload.index(b"IDAT")
    corrupted = bytearray(payload)
    corrupted[idat + 4] ^= 1
    for candidate in (bytes(corrupted), payload[:-1], payload + b"trailing"):
        with pytest.raises(LocalityError) as captured:
            _compile(candidate)
        assert captured.value.code == "decode_failed"


def test_malformed_png_color_metadata_still_produces_a_structural_failure() -> None:
    source = _compile(_image_bytes())
    payload = _insert_png_chunk(_image_bytes(), b"gAMA", b"\x00\x00\x01")
    receipt, _ = _provider_evidence(payload, width=4, height=3, measured_mode="RGB")
    structural = verify_output_structure(
        source,
        provider_receipt=receipt,
        output_index=0,
        output_bytes=payload,
    )
    result = judgment_to_json(structural.judgment)["result"]
    assert result["state"] == "fail"
    assert result["reason"] == "decode_failed"


def test_encoded_bytes_must_match_their_declared_source_sha256() -> None:
    payload = _image_bytes()
    with pytest.raises(LocalityError) as captured:
        compile_canonical_raster(payload, source_content_sha256="f" * 64)
    assert captured.value.code == "content_sha256_mismatch"


@pytest.mark.parametrize(
    ("payload", "code"),
    (
        (_image_bytes("P"), "unsupported_color_contract"),
        (_image_bytes("RGBA", pixels=[(1, 2, 3, 254)] * 12), "non_opaque"),
        (b"not an image", "unsupported_format"),
    ),
)
def test_compiler_rejects_unsupported_or_nonopaque_inputs(payload: bytes, code: str) -> None:
    with pytest.raises(LocalityError) as captured:
        _compile(payload)
    assert captured.value.code == code


def test_rectangle_mask_is_integer_half_open_and_row_major() -> None:
    source = _compile(_image_bytes())
    mask = compile_rectangle_mask(source, left=1, top=1, right=3, bottom=2)
    assert mask.compiler_revision == MASK_COMPILER_REVISION
    assert mask.mask_bytes == bytes((0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0))
    assert (mask.editable_count, mask.protected_count) == (2, 10)


@pytest.mark.parametrize(
    "bounds",
    (
        {"left": True, "top": 0, "right": 2, "bottom": 2},
        {"left": 0, "top": 0, "right": 4, "bottom": 3},
        {"left": 2, "top": 1, "right": 2, "bottom": 2},
    ),
)
def test_rectangle_mask_rejects_bool_full_frame_and_empty_bounds(bounds: JsonObject) -> None:
    source = _compile(_image_bytes())
    with pytest.raises(LocalityError):
        compile_rectangle_mask(source, **bounds)


def test_structural_pass_requires_and_cross_binds_full_eligible_occurrence() -> None:
    payload = _image_bytes()
    source = _compile(payload)
    receipt, occurrence = _provider_evidence(payload, width=4, height=3, measured_mode="RGB")

    with pytest.raises(LocalityError, match="occurrence"):
        verify_output_structure(
            source,
            provider_receipt=receipt,
            output_index=0,
            output_bytes=payload,
        )

    result = verify_output_structure(
        source,
        provider_receipt=receipt,
        output_index=0,
        output_bytes=payload,
        output_occurrence=occurrence,
    )
    document = judgment_to_json(result.judgment)
    assert document["result"]["state"] == "pass"
    assert document["subject"] == {
        "kind": "selectable_output_occurrence",
        "output_occurrence_id": occurrence.output_occurrence_id,
    }
    assert result.output_raster is not None


@pytest.mark.parametrize("mode", ("L", "LA", "RGB", "RGBA"))
def test_structural_pass_preserves_registered_observed_mode(mode: str) -> None:
    payload = _image_bytes(mode)
    source = _compile(_image_bytes())
    receipt, occurrence = _provider_evidence(payload, width=4, height=3, measured_mode=mode)
    result = verify_output_structure(
        source,
        provider_receipt=receipt,
        output_index=0,
        output_bytes=payload,
        output_occurrence=occurrence,
    )
    document = judgment_to_json(result.judgment)
    assert document["result"]["state"] == "pass"
    assert document["result"]["measurements"]["output_mode"] == mode


def test_structural_dimension_mismatch_retains_raster_but_is_not_a_pass() -> None:
    source = _compile(_image_bytes(size=(4, 3)))
    payload = _image_bytes(size=(2, 2))
    receipt, occurrence = _provider_evidence(payload, width=2, height=2, measured_mode="RGB")
    result = verify_output_structure(
        source,
        provider_receipt=receipt,
        output_index=0,
        output_bytes=payload,
        output_occurrence=occurrence,
    )
    document = judgment_to_json(result.judgment)
    assert document["result"]["state"] == "fail"
    assert document["result"]["reason"] == "dimension_mismatch"
    assert result.output_raster is not None
    mask = compile_rectangle_mask(source, left=1, top=1, right=3, bottom=2)
    assert (
        judgment_to_json(build_locality_not_run(result.judgment, mask))["result"]["state"]
        == "not_run"
    )


def test_structural_verifier_rejects_provider_mime_signature_drift() -> None:
    payload = _image_bytes()
    source = _compile(payload)
    receipt, occurrence = _provider_evidence(
        payload,
        width=4,
        height=3,
        measured_mode="RGB",
        mime="image/jpeg",
    )
    with pytest.raises(LocalityError) as captured:
        verify_output_structure(
            source,
            provider_receipt=receipt,
            output_index=0,
            output_bytes=payload,
            output_occurrence=occurrence,
        )
    assert captured.value.code == "provider_payload_mismatch"


def test_invalid_provider_bytes_emit_structural_fail_and_locality_not_run() -> None:
    source = _compile(_image_bytes())
    payload = b"not an image"
    receipt, _ = _provider_evidence(
        payload,
        width=4,
        height=3,
        measured_mode="RGB",
        mime="image/png",
    )
    receipt_document = provider_to_json(receipt)
    receipt_document["outputs"][0]["media_type_claim"] = None
    receipt_document.pop("provider_receipt_id")
    receipt = seal_provider_artifact(receipt_document)
    assert isinstance(receipt, ProviderReceipt)

    structural = verify_output_structure(
        source,
        provider_receipt=receipt,
        output_index=0,
        output_bytes=payload,
    )
    structural_document = judgment_to_json(structural.judgment)
    assert structural_document["result"]["state"] == "fail"
    assert structural_document["subject"]["kind"] == "provider_output_payload"
    assert structural.output_raster is None

    mask = compile_rectangle_mask(source, left=1, top=1, right=3, bottom=2)
    blocked = build_locality_not_run(structural.judgment, mask)
    blocked_document = judgment_to_json(blocked)
    assert blocked_document["result"] == {
        "state": "not_run",
        "reason": "structural_verification_failed",
    }
    assert blocked_document["subject"] == structural_document["subject"]


@pytest.mark.parametrize(
    ("payload", "reason", "mode", "opaque"),
    (
        (_image_bytes("P"), "unsupported_color_contract", "P", True),
        (
            _image_bytes("RGBA", pixels=[(1, 2, 3, 254)] * 12),
            "non_opaque",
            "RGBA",
            False,
        ),
    ),
)
def test_decoded_structural_rejections_emit_complete_honest_measurements(
    payload: bytes,
    reason: str,
    mode: str,
    opaque: bool,
) -> None:
    source = _compile(_image_bytes())
    receipt, _ = _provider_evidence(payload, width=4, height=3, measured_mode="RGB")
    structural = verify_output_structure(
        source,
        provider_receipt=receipt,
        output_index=0,
        output_bytes=payload,
    )
    result = judgment_to_json(structural.judgment)["result"]
    assert result["reason"] == reason
    assert result["measurements"] == {
        "source_width": 4,
        "source_height": 3,
        "container_decoded": True,
        "canonical_raster_compiled": False,
        "frame_count": 1,
        "output_width": 4,
        "output_height": 3,
        "output_mode": mode,
        "opaque": opaque,
    }


def test_structural_verifier_checks_receipt_bytes_before_decoder() -> None:
    payload = _image_bytes()
    source = _compile(payload)
    receipt, occurrence = _provider_evidence(payload, width=4, height=3, measured_mode="RGB")
    with pytest.raises(LocalityError, match="receipt|bytes") as captured:
        verify_output_structure(
            source,
            provider_receipt=receipt,
            output_index=0,
            output_bytes=payload + b"drift",
            output_occurrence=occurrence,
        )
    assert captured.value.code == "provider_payload_mismatch"


def test_typed_provider_artifact_validation_errors_are_normalized() -> None:
    payload = _image_bytes()
    source = _compile(payload)
    receipt, occurrence = _provider_evidence(payload, width=4, height=3, measured_mode="RGB")
    forged = replace(receipt, provider_receipt_id="not-a-digest")
    with pytest.raises(LocalityError) as captured:
        verify_output_structure(
            source,
            provider_receipt=forged,
            output_index=0,
            output_bytes=payload,
            output_occurrence=occurrence,
        )
    assert captured.value.code == "contract_mismatch"


@pytest.mark.parametrize("editable_change", (True, False))
def test_exact_verifier_ignores_editable_pixels_and_detects_one_protected_channel(
    editable_change: bool,
) -> None:
    source_payload = _image_bytes(pixels=[(10, 20, 30)] * 12)
    source = _compile(source_payload)
    pixels = [(10, 20, 30)] * 12
    index = 5 if editable_change else 0
    pixels[index] = (17, 20, 30)
    output_payload = _image_bytes(pixels=pixels)
    structural, occurrence = _structural(source, output_payload)
    assert structural.output_raster is not None
    mask = compile_rectangle_mask(source, left=1, top=1, right=3, bottom=2)

    judgment = verify_outside_mask_rgb_exact(
        source,
        structural.output_raster,
        mask,
        output_occurrence=occurrence,
        structural_pass=structural.judgment,
    )
    result = judgment_to_json(judgment)["result"]
    if editable_change:
        assert result == {
            "state": "pass",
            "measurements": {
                "protected_pixel_count": 10,
                "changed_pixel_count": 0,
                "max_abs_channel_error": 0,
            },
        }
    else:
        assert result == {
            "state": "fail",
            "measurements": {
                "protected_pixel_count": 10,
                "changed_pixel_count": 1,
                "max_abs_channel_error": 7,
            },
        }


def test_exact_verifier_rejects_substituted_occurrence_and_mask_lineage() -> None:
    source = _compile(_image_bytes(pixels=[(10, 20, 30)] * 12))
    output_payload = _image_bytes(pixels=[(11, 20, 30)] * 12)
    structural, occurrence = _structural(source, output_payload)
    assert structural.output_raster is not None
    mask = compile_rectangle_mask(source, left=1, top=1, right=3, bottom=2)

    other_payload = _image_bytes(pixels=[(12, 20, 30)] * 12)
    _, substituted_occurrence = _provider_evidence(
        other_payload,
        width=4,
        height=3,
        measured_mode="RGB",
    )
    with pytest.raises(LocalityError) as captured:
        verify_outside_mask_rgb_exact(
            source,
            structural.output_raster,
            mask,
            output_occurrence=substituted_occurrence,
            structural_pass=structural.judgment,
        )
    assert captured.value.code == "contract_mismatch"

    other_source = _compile(_image_bytes(pixels=[(9, 20, 30)] * 12))
    other_mask = compile_rectangle_mask(other_source, left=1, top=1, right=3, bottom=2)
    with pytest.raises(LocalityError) as captured:
        verify_outside_mask_rgb_exact(
            source,
            structural.output_raster,
            other_mask,
            output_occurrence=occurrence,
            structural_pass=structural.judgment,
        )
    assert captured.value.code == "contract_mismatch"

"""Pinned raster compilation and exact outside-mask verification for ADR-0016.

The provider is allowed to produce arbitrary bytes.  This module first binds those bytes to an
immutable provider receipt, then compiles only the narrow PNG/JPEG profile registered here.  A
structurally invalid payload never becomes a selectable output occurrence.  Exact locality is a
separate judgment over already validated canonical RGB rasters and a canonical binary mask.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import struct
import threading
import warnings
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from io import BytesIO
from typing import Any

import PIL
from blake3 import blake3
from PIL import Image, ImageCms, ImageFile, UnidentifiedImageError, features

from moodboard.contracts import ContractIdentityError, compute_document_identity
from moodboard.judgment import (
    SCHEMA_VERSION as JUDGMENT_VERSION,
)
from moodboard.judgment import (
    ConstraintVerificationJudgment,
    JudgmentError,
    validate_locality_blocking_pair,
)
from moodboard.judgment import (
    from_json_dict as judgment_from_json,
)
from moodboard.judgment import (
    to_json_dict as judgment_to_json,
)
from moodboard.locality_contracts import (
    EXACT_LOCALITY_VERIFIER_VERSION,
    MASK_SCHEMA_VERSION,
    RASTER_SCHEMA_VERSION,
    STRUCTURAL_VERIFIER_VERSION,
    CanonicalMaskArtifact,
    CanonicalRasterArtifact,
    LocalityContractError,
    compute_exact_locality_input_digest,
    compute_exact_locality_not_run_input_digest,
    compute_mask_sha256,
    compute_raster_sha256,
    compute_structural_input_digest,
    validate_mask_artifact,
    validate_raster_artifact,
)
from moodboard.provider_artifacts import (
    OUTPUT_VERSION,
    RECEIPT_VERSION,
    OutputOccurrence,
    ProviderArtifactError,
    ProviderReceipt,
    validate_provider_artifact,
)
from moodboard.provider_artifacts import (
    to_json_dict as provider_to_json,
)

__all__ = [
    "COMPILER_REVISION",
    "DEFAULT_COMPILER_MANIFEST",
    "MASK_COMPILER_REVISION",
    "LocalityError",
    "RasterCompilerManifest",
    "StructuralVerification",
    "build_locality_not_run",
    "compile_canonical_raster",
    "compile_rectangle_mask",
    "verify_outside_mask_rgb_exact",
    "verify_output_structure",
]

COMPILER_REVISION = "moodboard.raster-compiler.pillow-12.3.0-pngjpeg8-icc1.v1"
MASK_COMPILER_REVISION = "moodboard.rect-mask.v1"
_MANIFEST_VERSION = "moodboard.raster-compiler-manifest.v1"
_PINNED_SRGB_PROFILE_SHA256 = "6f6fe5cc53cd24ceeb7997fb24ce2889fdfb88d88ce4fdc5f8e25e0481294953"
# Generated once by LittleCMS 2.19's MIT-licensed built-in sRGB constructor, with only the ICC
# creation timestamp normalized to 2000-01-01T00:00:00. Its embedded cprt tag is
# "No copyright, use freely". Runtime creation is forbidden because that timestamp would drift.
_PINNED_SRGB_PROFILE_BASE64 = (
    "AAACTGxjbXMEQAAAbW50clJHQiBYWVogB9AAAQABAAAAAAAAYWNzcEFQUEwAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAPbWAAEAAAAA0y1sY21zAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAALZGVzYwAAAQgAAAA2Y3BydAAAAUAAAABMd3RwdAAAAYwAAAAUY2hh"
    "ZAAAAaAAAAAsclhZWgAAAcwAAAAUYlhZWgAAAeAAAAAUZ1hZWgAAAfQAAAAUclRSQwAAAggAAAAg"
    "Z1RSQwAAAggAAAAgYlRSQwAAAggAAAAgY2hybQAAAigAAAAkbWx1YwAAAAAAAAABAAAADGVuVVMA"
    "AAAaAAAAHABzAFIARwBCACAAYgB1AGkAbAB0AC0AaQBuAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAA"
    "ADAAAAAcAE4AbwAgAGMAbwBwAHkAcgBpAGcAaAB0ACwAIAB1AHMAZQAgAGYAcgBlAGUAbAB5WFla"
    "IAAAAAAAAPbWAAEAAAAA0y1zZjMyAAAAAAABDEIAAAXe///zJQAAB5MAAP2Q///7of///aIAAAPc"
    "AADAblhZWiAAAAAAAABvoAAAOPUAAAOQWFlaIAAAAAAAACSfAAAPhAAAtsNYWVogAAAAAAAAYpcA"
    "ALeHAAAY2XBhcmEAAAAAAAMAAAACZmYAAPKnAAANWQAAE9AAAApbY2hybQAAAAAAAwAAAACj1wAA"
    "VHsAAEzNAACZmgAAJmYAAA9c"
)
_RUNTIME_CERTIFICATE_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjd"
    "AAAAD0lEQVR4nGNkZGJmZmYGAAA8ABHVfkbmAAAAAElFTkSuQmCC"
)
_RUNTIME_CERTIFICATE_PNG_ICC_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAABdGlDQ1BJQ0MgUHJvZmlsZQAAeJx1kT1Lw1AUht+2"
    "SkUrHXQQcchQxaEFURBHjUOXIqVWsOqS3CatkKThJkGKq+DiUHAQXfwa/Ae6Cq4KBUERRPwNfi1S4rlNoUXac7k5"
    "D2/Oezg5AcIZg5lO3yJgWi7PpWVpvbAhResI0WmGwhx7KZvNoGf8PAW1jynRq3dd1xgqag4DQgPE88zmLjFNg8yO"
    "aws+IB5lZaVIfEac5DQg8b3Q1YDfBZcC/hLM87llICx6SqUOVjuYlblJPE2cMA2PteYRXxLTrLVVyuN0J+AghzRk"
    "SFDhYRsGXKQoW7Sz7r6Zpm8FFfIwetqogpOjhDJ5k6R61FWjrJOu0TFQFXv/v09Hn5sNusdkoP/N9z8ngegh0Kj5"
    "/u+57zcugMgrcGu1/RXa08I36bW2ljgF4nvA9V1bU4+Am31g7MVWuNKUInTDug58XAHDBWCkDgxuBrtqvcflM5Df"
    "pV/0AByfAFNUH9/6AwT9Z5bLPogwAAAAD0lEQVR4nGMUrP7PwMAAAAbXAY2HsDMGAAAAAElFTkSuQmCC"
)
_RUNTIME_CERTIFICATE_JPEG_BASE64 = (
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

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8"
_DIGEST_CHARS = frozenset("0123456789abcdef")
_COMPILER_LOCK = threading.RLock()
_PNG_ALLOWED_CRITICAL_CHUNKS = frozenset({b"IHDR", b"PLTE", b"IDAT", b"IEND"})
_PNG_UNSUPPORTED_COLOR_CHUNKS = frozenset({b"sBIT", b"cICP", b"mDCv", b"cLLi"})
_PNG_SRGB_CHROMATICITIES = (31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
_STRUCTURAL_FAILURE_CODES = frozenset(
    {
        "decode_failed",
        "decode_limit_exceeded",
        "malformed_orientation",
        "unsupported_format",
        "unsafe_decoder_warning",
        "unsupported_frame_count",
        "non_opaque",
        "unsupported_color_contract",
    }
)


class LocalityError(ValueError):
    """A locality operation failed closed under one stable error code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        inspection: _RasterInspection | None = None,
    ) -> None:
        self.code = code
        self.inspection = inspection
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RasterCompilerManifest:
    schema_version: str
    compiler_revision: str
    pillow_version: str
    pillow_core_version: str
    jpeg_abi_version: str
    libjpeg_turbo_version: str
    zlib_version: str
    zlib_ng_version: str
    littlecms_version: str
    load_truncated_images: bool
    pillow_max_image_pixels: int
    pinned_srgb_profile_sha256: str
    max_encoded_bytes: int
    max_dimension: int
    max_pixels: int
    max_rgb_bytes: int
    max_png_chunks: int
    max_jpeg_segments: int
    max_exif_bytes: int
    max_icc_bytes: int


DEFAULT_COMPILER_MANIFEST = RasterCompilerManifest(
    schema_version=_MANIFEST_VERSION,
    compiler_revision=COMPILER_REVISION,
    pillow_version="12.3.0",
    pillow_core_version="12.3.0",
    jpeg_abi_version="6.2",
    libjpeg_turbo_version="3.1.4.1",
    zlib_version="1.3.1.zlib-ng",
    zlib_ng_version="2.3.3",
    littlecms_version="2.19",
    load_truncated_images=False,
    pillow_max_image_pixels=89_478_485,
    pinned_srgb_profile_sha256=_PINNED_SRGB_PROFILE_SHA256,
    max_encoded_bytes=16_777_216,
    max_dimension=8_192,
    max_pixels=16_777_216,
    max_rgb_bytes=50_331_648,
    max_png_chunks=4_096,
    max_jpeg_segments=4_096,
    max_exif_bytes=1_048_576,
    max_icc_bytes=1_048_576,
)


@dataclass(frozen=True, slots=True)
class StructuralVerification:
    judgment: ConstraintVerificationJudgment
    output_raster: CanonicalRasterArtifact | None


@dataclass(frozen=True, slots=True)
class _ContainerInfo:
    format: str
    mime: str
    width: int
    height: int
    color_contract_supported: bool = True
    png_has_srgb: bool = False
    png_has_iccp: bool = False
    png_gamma: int | None = None
    png_chromaticities: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class _RasterInspection:
    container_decoded: bool
    frame_count: int | None
    width: int | None
    height: int | None
    mode: str | None
    opaque: bool | None
    mime: str | None


@dataclass(frozen=True, slots=True)
class _CompiledRaster:
    artifact: CanonicalRasterArtifact
    inspection: _RasterInspection


def _runtime_manifest() -> RasterCompilerManifest:
    try:
        turbo = features.version_feature("libjpeg_turbo")
        zlib_ng = features.version_feature("zlib_ng")
        littlecms = features.version_module("littlecms2")
    except (ValueError, AttributeError) as error:
        raise LocalityError(
            "compiler_manifest_mismatch", "required Pillow codec features are unavailable"
        ) from error
    return RasterCompilerManifest(
        schema_version=_MANIFEST_VERSION,
        compiler_revision=COMPILER_REVISION,
        pillow_version=PIL.__version__,
        pillow_core_version=str(getattr(Image.core, "PILLOW_VERSION", "")),
        jpeg_abi_version=str(getattr(Image.core, "jpeglib_version", "")),
        libjpeg_turbo_version=str(turbo),
        zlib_version=str(getattr(Image.core, "zlib_version", "")),
        zlib_ng_version=str(zlib_ng),
        littlecms_version=str(littlecms),
        load_truncated_images=bool(ImageFile.LOAD_TRUNCATED_IMAGES),
        pillow_max_image_pixels=int(Image.MAX_IMAGE_PIXELS or 0),
        pinned_srgb_profile_sha256=_PINNED_SRGB_PROFILE_SHA256,
        max_encoded_bytes=DEFAULT_COMPILER_MANIFEST.max_encoded_bytes,
        max_dimension=DEFAULT_COMPILER_MANIFEST.max_dimension,
        max_pixels=DEFAULT_COMPILER_MANIFEST.max_pixels,
        max_rgb_bytes=DEFAULT_COMPILER_MANIFEST.max_rgb_bytes,
        max_png_chunks=DEFAULT_COMPILER_MANIFEST.max_png_chunks,
        max_jpeg_segments=DEFAULT_COMPILER_MANIFEST.max_jpeg_segments,
        max_exif_bytes=DEFAULT_COMPILER_MANIFEST.max_exif_bytes,
        max_icc_bytes=DEFAULT_COMPILER_MANIFEST.max_icc_bytes,
    )


def _require_manifest(manifest: RasterCompilerManifest) -> None:
    if not isinstance(manifest, RasterCompilerManifest) or manifest != DEFAULT_COMPILER_MANIFEST:
        raise LocalityError(
            "compiler_manifest_mismatch",
            "compiler manifest does not equal the registered raster compiler manifest",
        )
    if _runtime_manifest() != DEFAULT_COMPILER_MANIFEST:
        raise LocalityError(
            "compiler_manifest_mismatch",
            "runtime Pillow codec build does not equal the registered compiler manifest",
        )
    profile = _pinned_srgb_profile_bytes()
    if not hmac.compare_digest(
        hashlib.sha256(profile).hexdigest(), manifest.pinned_srgb_profile_sha256
    ):
        raise LocalityError(
            "compiler_manifest_mismatch", "pinned sRGB profile does not match its manifest"
        )
    _certify_runtime()


def _pinned_srgb_profile_bytes() -> bytes:
    try:
        profile = base64.b64decode(_PINNED_SRGB_PROFILE_BASE64, validate=True)
    except ValueError as error:
        raise LocalityError(
            "compiler_manifest_mismatch", "pinned sRGB profile is not valid base64"
        ) from error
    if len(profile) != 588:
        raise LocalityError(
            "compiler_manifest_mismatch", "pinned sRGB profile has the wrong byte length"
        )
    return profile


def _require_digest(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _DIGEST_CHARS for character in value)
    ):
        raise LocalityError("contract_mismatch", f"{field} must be one lowercase SHA-256 digest")
    return value


def _require_payload(payload: object) -> bytes:
    if not isinstance(payload, bytes):
        raise LocalityError("payload_invalid", "encoded image payload must be immutable bytes")
    return payload


def _check_dimensions(width: int, height: int, manifest: RasterCompilerManifest) -> None:
    if width <= 0 or height <= 0:
        raise LocalityError("decode_failed", "encoded image has invalid dimensions")
    if width > manifest.max_dimension or height > manifest.max_dimension:
        raise LocalityError("decode_limit_exceeded", "encoded image exceeds the side bound")
    pixels = width * height
    if pixels > manifest.max_pixels or pixels * 3 > manifest.max_rgb_bytes:
        raise LocalityError("decode_limit_exceeded", "encoded image exceeds the raster bound")


def _bounded_icc_decompress(compressed: bytes, maximum: int) -> bytes | None:
    """Return one bounded zlib stream, or ``None`` when its framing is malformed."""

    try:
        decoder = zlib.decompressobj()
        profile = decoder.decompress(compressed, maximum + 1)
        if len(profile) > maximum or decoder.unconsumed_tail:
            raise LocalityError("decode_limit_exceeded", "embedded ICC profile exceeds its bound")
        profile += decoder.flush()
    except zlib.error:
        return None
    if len(profile) > maximum:
        raise LocalityError("decode_limit_exceeded", "embedded ICC profile exceeds its bound")
    if not decoder.eof or decoder.unused_data:
        return None
    return profile


def _preflight_png(payload: bytes, manifest: RasterCompilerManifest) -> _ContainerInfo:
    position = len(_PNG_SIGNATURE)
    chunk_count = 0
    width = height = 0
    saw_ihdr = False
    saw_iend = False
    color_contract_supported = True
    has_srgb = False
    has_iccp = False
    gamma: int | None = None
    chromaticities: tuple[int, ...] | None = None
    saw_exif = False
    while position < len(payload):
        chunk_count += 1
        if chunk_count > manifest.max_png_chunks:
            raise LocalityError("decode_limit_exceeded", "PNG exceeds the chunk-count bound")
        if position + 12 > len(payload):
            raise LocalityError("decode_failed", "PNG chunk framing is truncated")
        length = int.from_bytes(payload[position : position + 4], "big")
        chunk_type = payload[position + 4 : position + 8]
        data_start = position + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(payload):
            raise LocalityError("decode_failed", "PNG chunk payload is truncated")
        chunk_data = payload[data_start:data_end]
        expected_crc = int.from_bytes(payload[data_end:crc_end], "big")
        measured_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if expected_crc != measured_crc:
            raise LocalityError("decode_failed", "PNG chunk CRC verification failed")
        if len(chunk_type) != 4 or not all(
            65 <= character <= 90 or 97 <= character <= 122 for character in chunk_type
        ):
            raise LocalityError("decode_failed", "PNG chunk type is malformed")
        if not (chunk_type[0] & 0x20) and chunk_type not in _PNG_ALLOWED_CRITICAL_CHUNKS:
            raise LocalityError("unsupported_format", "PNG has an unknown critical chunk")
        if chunk_type in {b"acTL", b"fcTL", b"fdAT"}:
            raise LocalityError("unsupported_format", "animated PNG is outside the v1 profile")
        if chunk_type in _PNG_UNSUPPORTED_COLOR_CHUNKS:
            color_contract_supported = False
        if chunk_type == b"IHDR":
            if saw_ihdr or chunk_count != 1 or length != 13:
                raise LocalityError("decode_failed", "PNG IHDR is missing or duplicated")
            saw_ihdr = True
            width, height, bit_depth, color_type, compression, filter_method, interlace = (
                struct.unpack(">IIBBBBB", chunk_data)
            )
            _check_dimensions(width, height, manifest)
            if bit_depth != 8 or color_type not in {0, 2, 4, 6}:
                color_contract_supported = False
            if compression != 0 or filter_method != 0 or interlace not in {0, 1}:
                raise LocalityError("unsupported_format", "PNG coding method is unsupported")
        elif chunk_type == b"sRGB":
            if has_srgb:
                color_contract_supported = False
            if length != 1 or chunk_data[0] > 3:
                color_contract_supported = False
            else:
                has_srgb = True
        elif chunk_type == b"iCCP":
            if has_iccp:
                color_contract_supported = False
            has_iccp = True
            separator = chunk_data.find(b"\0")
            if (
                not 1 <= separator <= 79
                or separator + 2 > len(chunk_data)
                or chunk_data[separator + 1] != 0
            ):
                color_contract_supported = False
            else:
                profile = _bounded_icc_decompress(
                    chunk_data[separator + 2 :], manifest.max_icc_bytes
                )
                if profile is None or not hmac.compare_digest(
                    hashlib.sha256(profile).hexdigest(), _PINNED_SRGB_PROFILE_SHA256
                ):
                    color_contract_supported = False
        elif chunk_type == b"gAMA":
            if gamma is not None:
                color_contract_supported = False
            if length != 4:
                color_contract_supported = False
            else:
                gamma = int.from_bytes(chunk_data, "big")
        elif chunk_type == b"cHRM":
            if chromaticities is not None:
                color_contract_supported = False
            if length != 32:
                color_contract_supported = False
            else:
                chromaticities = struct.unpack(">8I", chunk_data)
        elif chunk_type == b"eXIf":
            if saw_exif:
                raise LocalityError("malformed_orientation", "PNG carries more than one EXIF block")
            saw_exif = True
            if length > manifest.max_exif_bytes:
                raise LocalityError("decode_limit_exceeded", "PNG EXIF exceeds the metadata bound")
        elif chunk_type == b"IEND":
            if length != 0 or crc_end != len(payload):
                raise LocalityError("decode_failed", "PNG IEND is not the final exact chunk")
            saw_iend = True
        position = crc_end
        if saw_iend:
            break
    if not saw_ihdr or not saw_iend:
        raise LocalityError("decode_failed", "PNG is missing a required terminal chunk")
    if (gamma is not None or chromaticities is not None) and not has_srgb:
        color_contract_supported = False
    if has_srgb and gamma is not None and gamma != 45_455:
        color_contract_supported = False
    if has_srgb and chromaticities is not None and chromaticities != _PNG_SRGB_CHROMATICITIES:
        color_contract_supported = False
    if has_srgb and has_iccp:
        color_contract_supported = False
    return _ContainerInfo(
        format="PNG",
        mime="image/png",
        width=width,
        height=height,
        color_contract_supported=color_contract_supported,
        png_has_srgb=has_srgb,
        png_has_iccp=has_iccp,
        png_gamma=gamma,
        png_chromaticities=chromaticities,
    )


def _jpeg_terminal_eoi(
    payload: bytes,
    position: int,
    *,
    segment_count: int,
    manifest: RasterCompilerManifest,
) -> int:
    """Return the first syntactic EOI after SOS, skipping stuffed bytes and marker payloads."""

    while position < len(payload):
        marker_start = payload.find(b"\xff", position)
        if marker_start < 0:
            raise LocalityError("decode_failed", "JPEG entropy stream has no EOI marker")
        position = marker_start + 1
        while position < len(payload) and payload[position] == 0xFF:
            position += 1
        if position >= len(payload):
            raise LocalityError("decode_failed", "JPEG entropy marker is truncated")
        marker = payload[position]
        position += 1
        if marker == 0x00 or marker in range(0xD0, 0xD8):
            if marker != 0x00:
                segment_count += 1
                if segment_count > manifest.max_jpeg_segments:
                    raise LocalityError(
                        "decode_limit_exceeded", "JPEG exceeds the marker-count bound"
                    )
            continue
        segment_count += 1
        if segment_count > manifest.max_jpeg_segments:
            raise LocalityError("decode_limit_exceeded", "JPEG exceeds the marker-count bound")
        if marker == 0xD9:
            return position
        if marker in {0xD8, 0x01}:
            continue
        if position + 2 > len(payload):
            raise LocalityError("decode_failed", "JPEG scan marker length is truncated")
        segment_length = int.from_bytes(payload[position : position + 2], "big")
        if segment_length < 2 or position + segment_length > len(payload):
            raise LocalityError("decode_failed", "JPEG scan marker payload is truncated")
        segment = payload[position + 2 : position + segment_length]
        if 0xE0 <= marker <= 0xEF:
            if marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
                raise LocalityError(
                    "malformed_orientation", "JPEG EXIF metadata is forbidden after scan start"
                )
            if marker == 0xE2 and segment.startswith(b"ICC_PROFILE\x00"):
                raise LocalityError(
                    "unsupported_color_contract",
                    "JPEG ICC metadata is forbidden after scan start",
                )
            if marker == 0xE2 and segment.startswith(b"MPF\x00"):
                raise LocalityError(
                    "unsupported_format", "JPEG MPF metadata is forbidden after scan start"
                )
            raise LocalityError(
                "unsupported_format", "JPEG application metadata is forbidden after scan start"
            )
        position += segment_length
    raise LocalityError("decode_failed", "JPEG entropy stream has no terminal EOI")


def _preflight_jpeg(payload: bytes, manifest: RasterCompilerManifest) -> _ContainerInfo:
    if not payload.endswith(b"\xff\xd9"):
        raise LocalityError("decode_failed", "JPEG does not end at its EOI marker")
    position = 2
    width = height = 0
    precision: int | None = None
    entropy_start: int | None = None
    icc_parts: dict[int, bytes] = {}
    icc_total: int | None = None
    icc_byte_count = 0
    saw_exif = False
    segment_count = 0
    color_contract_supported = True
    while position < len(payload) - 2:
        if payload[position] != 0xFF:
            raise LocalityError("decode_failed", "JPEG marker framing is malformed")
        while position < len(payload) and payload[position] == 0xFF:
            position += 1
        if position >= len(payload):
            raise LocalityError("decode_failed", "JPEG marker is truncated")
        marker = payload[position]
        position += 1
        segment_count += 1
        if segment_count > manifest.max_jpeg_segments:
            raise LocalityError("decode_limit_exceeded", "JPEG exceeds the marker-count bound")
        if marker in {0xD8, 0xD9, 0x01, *range(0xD0, 0xD8)}:
            continue
        if position + 2 > len(payload):
            raise LocalityError("decode_failed", "JPEG segment length is truncated")
        segment_length = int.from_bytes(payload[position : position + 2], "big")
        if segment_length < 2 or position + segment_length > len(payload):
            raise LocalityError("decode_failed", "JPEG segment payload is truncated")
        segment = payload[position + 2 : position + segment_length]
        if marker in _JPEG_SOF_MARKERS:
            if len(segment) < 6:
                raise LocalityError("decode_failed", "JPEG frame header is truncated")
            precision = segment[0]
            height = int.from_bytes(segment[1:3], "big")
            width = int.from_bytes(segment[3:5], "big")
            _check_dimensions(width, height, manifest)
        if marker == 0xE2 and segment.startswith(b"MPF\x00"):
            raise LocalityError("unsupported_format", "multi-picture JPEG is outside v1")
        if marker == 0xE2 and segment.startswith(b"ICC_PROFILE\x00"):
            if len(segment) < 14:
                color_contract_supported = False
            else:
                sequence = segment[12]
                total = segment[13]
                if (
                    sequence == 0
                    or total == 0
                    or sequence > total
                    or sequence in icc_parts
                    or (icc_total is not None and total != icc_total)
                ):
                    color_contract_supported = False
                else:
                    icc_total = total
                    part = segment[14:]
                    icc_byte_count += len(part)
                    if icc_byte_count > manifest.max_icc_bytes:
                        raise LocalityError(
                            "decode_limit_exceeded",
                            "JPEG ICC profile exceeds the metadata bound",
                        )
                    icc_parts[sequence] = part
        if marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
            if saw_exif:
                raise LocalityError(
                    "malformed_orientation", "JPEG carries more than one EXIF block"
                )
            saw_exif = True
            if len(segment) > manifest.max_exif_bytes:
                raise LocalityError("decode_limit_exceeded", "JPEG EXIF exceeds the metadata bound")
        position += segment_length
        if marker == 0xDA:
            entropy_start = position
            break
    if entropy_start is None:
        raise LocalityError("decode_failed", "JPEG has no scan payload")
    if _jpeg_terminal_eoi(
        payload,
        entropy_start,
        segment_count=segment_count,
        manifest=manifest,
    ) != len(payload):
        raise LocalityError("decode_failed", "JPEG carries bytes after its first EOI marker")
    if not width or not height or precision is None:
        raise LocalityError("decode_failed", "JPEG has no supported frame header")
    if precision != 8:
        color_contract_supported = False
    if icc_total is not None:
        if set(icc_parts) != set(range(1, icc_total + 1)):
            color_contract_supported = False
        else:
            profile = b"".join(icc_parts[index] for index in range(1, icc_total + 1))
            if len(profile) > manifest.max_icc_bytes:
                raise LocalityError(
                    "decode_limit_exceeded", "JPEG ICC profile exceeds the metadata bound"
                )
            if not hmac.compare_digest(
                hashlib.sha256(profile).hexdigest(), _PINNED_SRGB_PROFILE_SHA256
            ):
                color_contract_supported = False
    return _ContainerInfo(
        "JPEG",
        "image/jpeg",
        width,
        height,
        color_contract_supported=color_contract_supported,
    )


def _preflight(payload: bytes, manifest: RasterCompilerManifest) -> _ContainerInfo:
    if len(payload) == 0:
        raise LocalityError("unsupported_format", "empty payload has no supported image format")
    if len(payload) > manifest.max_encoded_bytes:
        raise LocalityError("decode_limit_exceeded", "encoded image exceeds the byte bound")
    if payload.startswith(_PNG_SIGNATURE):
        return _preflight_png(payload, manifest)
    if payload.startswith(_JPEG_SIGNATURE):
        return _preflight_jpeg(payload, manifest)
    raise LocalityError("unsupported_format", "payload is not registered PNG or JPEG")


def _sniff_mime(payload: bytes) -> str | None:
    if payload.startswith(_PNG_SIGNATURE):
        return "image/png"
    if payload.startswith(_JPEG_SIGNATURE):
        return "image/jpeg"
    return None


def _inspection(
    *,
    frame_count: int,
    image: Image.Image,
    opaque: bool,
    mime: str,
) -> _RasterInspection:
    return _RasterInspection(
        container_decoded=True,
        frame_count=frame_count,
        width=image.width,
        height=image.height,
        mode=image.mode if image.mode in {"L", "LA", "RGB", "RGBA", "P", "CMYK"} else "other",
        opaque=opaque,
        mime=mime,
    )


def _is_opaque(image: Image.Image) -> bool:
    if image.mode in {"LA", "RGBA"}:
        return image.getchannel("A").getextrema() == (255, 255)
    if image.mode in {"L", "RGB", "P"} and "transparency" in image.info:
        return image.convert("RGBA").getchannel("A").getextrema() == (255, 255)
    return True


def _orientation(image: Image.Image, manifest: RasterCompilerManifest) -> int:
    exif_bytes = image.info.get("exif")
    if exif_bytes is None:
        return 1
    if not isinstance(exif_bytes, bytes) or len(exif_bytes) > manifest.max_exif_bytes:
        raise LocalityError("malformed_orientation", "EXIF orientation metadata is malformed")
    try:
        exif = Image.Exif()
        exif.load(exif_bytes)
        value = exif.get(274, 1)
    except (KeyError, SyntaxError, TypeError, ValueError):
        raise LocalityError(
            "malformed_orientation", "EXIF orientation metadata is malformed"
        ) from None
    if type(value) is not int or not 1 <= value <= 8:
        raise LocalityError("malformed_orientation", "EXIF orientation must be an integer 1..8")
    return value


def _apply_orientation(image: Image.Image, orientation: int) -> Image.Image:
    operation = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }.get(orientation)
    return image.copy() if operation is None else image.transpose(operation)


def _convert_color(image: Image.Image, *, opaque: bool) -> Image.Image:
    if not opaque:
        raise LocalityError("non_opaque", "decoded image contains a non-opaque pixel")
    if image.mode not in {"L", "LA", "RGB", "RGBA"}:
        raise LocalityError(
            "unsupported_color_contract", "decoded mode is outside the registered color contract"
        )
    profile_bytes = image.info.get("icc_profile")
    if profile_bytes is not None:
        if not isinstance(profile_bytes, bytes) or image.mode not in {"RGB", "RGBA"}:
            raise LocalityError(
                "unsupported_color_contract", "embedded ICC profile is not registered for this mode"
            )
        if not hmac.compare_digest(
            hashlib.sha256(profile_bytes).hexdigest(), _PINNED_SRGB_PROFILE_SHA256
        ):
            raise LocalityError(
                "unsupported_color_contract", "embedded ICC profile is not the pinned sRGB profile"
            )
        source = image.convert("RGB")
        try:
            input_profile = ImageCms.ImageCmsProfile(BytesIO(profile_bytes))
            output_profile = ImageCms.ImageCmsProfile(BytesIO(_pinned_srgb_profile_bytes()))
            transformed = ImageCms.profileToProfile(
                source,
                input_profile,
                output_profile,
                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
                outputMode="RGB",
                inPlace=False,
                flags=ImageCms.Flags.NONE,
            )
            if transformed is None:
                raise ValueError("non-in-place ICC transform returned no raster")
            return transformed
        except (ImageCms.PyCMSError, OSError, TypeError, ValueError):
            raise LocalityError(
                "unsupported_color_contract", "embedded ICC profile cannot be transformed"
            ) from None
    if image.mode in {"LA", "RGBA"}:
        image = image.getchannel("L") if image.mode == "LA" else image.convert("RGB")
    return image.convert("RGB")


def _build_raster(
    rgb_bytes: bytes,
    *,
    width: int,
    height: int,
    source_content_sha256: str,
) -> CanonicalRasterArtifact:
    document: dict[str, Any] = {
        "schema_version": RASTER_SCHEMA_VERSION,
        "compiler_revision": COMPILER_REVISION,
        "width": width,
        "height": height,
        "mode": "RGB",
        "byte_count": len(rgb_bytes),
        "source_content_sha256": source_content_sha256,
        "raster_sha256": "0" * 64,
    }
    document["raster_sha256"] = compute_raster_sha256(
        {
            key: document[key]
            for key in (
                "compiler_revision",
                "width",
                "height",
                "mode",
                "byte_count",
                "source_content_sha256",
            )
        },
        rgb_bytes,
    )
    return validate_raster_artifact(document, rgb_bytes)


def _compile_locked(
    payload: bytes,
    *,
    source_content_sha256: str,
    compiler_manifest: RasterCompilerManifest,
    verify_manifest: bool,
) -> _CompiledRaster:
    if verify_manifest:
        _require_manifest(compiler_manifest)
    encoded = _require_payload(payload)
    claimed_sha256 = _require_digest(source_content_sha256, "source_content_sha256")
    if not hmac.compare_digest(hashlib.sha256(encoded).hexdigest(), claimed_sha256):
        raise LocalityError(
            "content_sha256_mismatch", "encoded image bytes do not match source_content_sha256"
        )
    container = _preflight(encoded, compiler_manifest)
    caught: list[warnings.WarningMessage]
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with Image.open(BytesIO(encoded), formats=("PNG", "JPEG")) as verifier:
                if verifier.format != container.format:
                    raise LocalityError(
                        "decode_failed", "decoded container disagrees with its byte signature"
                    )
                verifier.verify()
            with Image.open(BytesIO(encoded), formats=("PNG", "JPEG")) as decoded:
                if decoded.format != container.format:
                    raise LocalityError(
                        "decode_failed", "decoded container disagrees with its byte signature"
                    )
                decoded.load()
                if (decoded.width, decoded.height) != (container.width, container.height):
                    raise LocalityError(
                        "decode_failed", "decoded dimensions disagree with the container header"
                    )
                frame_count = int(getattr(decoded, "n_frames", 1))
                opaque = _is_opaque(decoded)
                initial = _inspection(
                    frame_count=frame_count,
                    image=decoded,
                    opaque=opaque,
                    mime=container.mime,
                )
                if frame_count != 1:
                    raise LocalityError(
                        "unsupported_frame_count",
                        "decoded image must contain exactly one frame",
                        inspection=initial,
                    )
                try:
                    orientation = _orientation(decoded, compiler_manifest)
                except LocalityError as error:
                    raise LocalityError(error.code, str(error), inspection=initial) from None
                oriented = _apply_orientation(decoded, orientation)
                oriented_opaque = _is_opaque(oriented)
                observed = _inspection(
                    frame_count=frame_count,
                    image=oriented,
                    opaque=oriented_opaque,
                    mime=container.mime,
                )
                if not oriented_opaque:
                    raise LocalityError(
                        "non_opaque",
                        "decoded image contains a non-opaque pixel",
                        inspection=observed,
                    )
                if not container.color_contract_supported:
                    raise LocalityError(
                        "unsupported_color_contract",
                        "container color declaration is outside the registered contract",
                        inspection=observed,
                    )
                try:
                    rgb = _convert_color(oriented, opaque=oriented_opaque)
                except LocalityError as error:
                    raise LocalityError(error.code, str(error), inspection=observed) from None
                rgb_bytes = rgb.tobytes("raw", "RGB")
                if len(rgb_bytes) > compiler_manifest.max_rgb_bytes:
                    raise LocalityError("decode_limit_exceeded", "canonical RGB exceeds its bound")
    except LocalityError:
        raise
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError):
        raise LocalityError("decode_failed", "image payload could not be fully decoded") from None
    if caught:
        if any(issubclass(item.category, Image.DecompressionBombWarning) for item in caught):
            raise LocalityError("decode_limit_exceeded", "image decoder reported a size limit")
        raise LocalityError(
            "unsafe_decoder_warning",
            "image decoder emitted a warning classified as unsafe",
            inspection=observed,
        )
    artifact = _build_raster(
        rgb_bytes,
        width=rgb.width,
        height=rgb.height,
        source_content_sha256=claimed_sha256,
    )
    return _CompiledRaster(artifact=artifact, inspection=observed)


def _compile(
    payload: bytes,
    *,
    source_content_sha256: str,
    compiler_manifest: RasterCompilerManifest,
    _verify_manifest: bool = True,
) -> _CompiledRaster:
    # Python builds without context-aware warnings use process-global warning capture.  All
    # registered Pillow work is serialized so concurrent compiler calls cannot steal one
    # another's warnings or observe the mutable Pillow decode-policy globals between checks.
    with _COMPILER_LOCK:
        return _compile_locked(
            payload,
            source_content_sha256=source_content_sha256,
            compiler_manifest=compiler_manifest,
            verify_manifest=_verify_manifest,
        )


@cache
def _certify_runtime() -> None:
    fixtures = (
        (
            _RUNTIME_CERTIFICATE_PNG_BASE64,
            "0999c85bac983f785dbc678837371110ac90295a345e460a6e9e947b469831d7",
            2,
            1,
            bytes((1, 2, 3, 4, 5, 6)),
            "956bae7aeef512a8adcbdebeea8802bcc00c92eb708bb3e7ca609b7abb645798",
        ),
        (
            _RUNTIME_CERTIFICATE_JPEG_BASE64,
            "753470d35ee5df49164d5c01372cfb3804e0a88aa6436b0f0161b78f077758ba",
            2,
            3,
            bytes.fromhex("9faaae09161fd4e1e73b4254f32f5d6e7975"),
            "b75babb2b0deb163bf9a25ee85cb915e5a9b5e202fec4ea2f2da850770159e18",
        ),
        (
            _RUNTIME_CERTIFICATE_PNG_ICC_BASE64,
            "cbdc792d0ef70fce829c84036c3bfcb6b57be96b9b1a4d83422654e355895a3c",
            2,
            1,
            bytes.fromhex("117bff117bff"),
            "2884a5ebe2113f2bf7e2fde1b003d693e789a44e766f8c7bf9470133258b65e8",
        ),
    )
    try:
        for encoded_b64, source_sha256, width, height, rgb_bytes, raster_sha256 in fixtures:
            encoded = base64.b64decode(encoded_b64, validate=True)
            if hashlib.sha256(encoded).hexdigest() != source_sha256:
                raise ValueError("compiler certificate payload identity drifted")
            compiled = _compile(
                encoded,
                source_content_sha256=source_sha256,
                compiler_manifest=DEFAULT_COMPILER_MANIFEST,
                _verify_manifest=False,
            ).artifact
            if (
                compiled.width != width
                or compiled.height != height
                or compiled.rgb_bytes != rgb_bytes
                or compiled.raster_sha256 != raster_sha256
            ):
                raise ValueError("compiler certificate output drifted")
    except Exception as error:
        raise LocalityError(
            "compiler_manifest_mismatch",
            "runtime decoder build failed its registered byte-level certificate",
        ) from error


def compile_canonical_raster(
    payload: bytes,
    *,
    source_content_sha256: str,
    compiler_manifest: RasterCompilerManifest = DEFAULT_COMPILER_MANIFEST,
) -> CanonicalRasterArtifact:
    """Compile one exact PNG/JPEG payload to the registered row-major RGB artifact."""

    return _compile(
        payload,
        source_content_sha256=source_content_sha256,
        compiler_manifest=compiler_manifest,
    ).artifact


def _raster_document(raster: CanonicalRasterArtifact) -> dict[str, Any]:
    if not isinstance(raster, CanonicalRasterArtifact):
        raise LocalityError("contract_mismatch", "raster must be one frozen canonical artifact")
    document = {
        "schema_version": raster.schema_version,
        "compiler_revision": raster.compiler_revision,
        "width": raster.width,
        "height": raster.height,
        "mode": raster.mode,
        "byte_count": raster.byte_count,
        "source_content_sha256": raster.source_content_sha256,
        "raster_sha256": raster.raster_sha256,
    }
    try:
        validate_raster_artifact(document, raster.rgb_bytes)
    except LocalityContractError as error:
        raise LocalityError("contract_mismatch", str(error)) from error
    return document


def _mask_document(mask: CanonicalMaskArtifact) -> dict[str, Any]:
    if not isinstance(mask, CanonicalMaskArtifact):
        raise LocalityError("contract_mismatch", "mask must be one frozen canonical artifact")
    document = {
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
    try:
        validate_mask_artifact(document, mask.mask_bytes)
    except LocalityContractError as error:
        raise LocalityError("contract_mismatch", str(error)) from error
    return document


def compile_rectangle_mask(
    source_raster: CanonicalRasterArtifact,
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> CanonicalMaskArtifact:
    """Compile authoritative integer half-open bounds into one row-major 0/1 mask."""

    source = _raster_document(source_raster)
    coordinates = (left, top, right, bottom)
    if any(type(value) is not int for value in coordinates):
        raise LocalityError("invalid_bounds", "mask bounds must be plain integers")
    if not (0 <= left < right <= source["width"] and 0 <= top < bottom <= source["height"]):
        raise LocalityError("invalid_bounds", "mask bounds are outside the canonical source")
    editable = (right - left) * (bottom - top)
    total = source["width"] * source["height"]
    protected = total - editable
    if editable == 0 or protected == 0:
        raise LocalityError("invalid_bounds", "mask requires nonempty editable and protected sets")
    payload = bytearray(total)
    for y in range(top, bottom):
        start = y * source["width"] + left
        payload[start : start + (right - left)] = b"\x01" * (right - left)
    mask_bytes = bytes(payload)
    document: dict[str, Any] = {
        "schema_version": MASK_SCHEMA_VERSION,
        "compiler_revision": MASK_COMPILER_REVISION,
        "width": source["width"],
        "height": source["height"],
        "byte_count": total,
        "editable_count": editable,
        "protected_count": protected,
        "source_raster_sha256": source["raster_sha256"],
        "mask_sha256": "0" * 64,
    }
    document["mask_sha256"] = compute_mask_sha256(
        {
            key: document[key]
            for key in (
                "compiler_revision",
                "width",
                "height",
                "byte_count",
                "editable_count",
                "protected_count",
                "source_raster_sha256",
            )
        },
        mask_bytes,
    )
    try:
        return validate_mask_artifact(document, mask_bytes)
    except LocalityContractError as error:
        raise LocalityError("contract_mismatch", str(error)) from error


def _provider_document(
    value: ProviderReceipt | OutputOccurrence | Mapping[str, Any], *, version: str
) -> dict[str, Any]:
    if isinstance(value, (ProviderReceipt, OutputOccurrence)):
        try:
            document = provider_to_json(value)
        except (ProviderArtifactError, RecursionError, TypeError, ValueError) as error:
            raise LocalityError(
                "contract_mismatch", "provider artifact validation failed"
            ) from error
    elif isinstance(value, Mapping):
        try:
            document = copy.deepcopy(dict(value))
        except (RecursionError, TypeError, ValueError) as error:
            raise LocalityError(
                "contract_mismatch", "provider artifact is not finite JSON"
            ) from error
        try:
            validate_provider_artifact(document)
        except ProviderArtifactError as error:
            raise LocalityError(
                "contract_mismatch", "provider artifact validation failed"
            ) from error
    else:
        raise LocalityError("contract_mismatch", "provider artifact has the wrong Python type")
    if document.get("schema_version") != version:
        raise LocalityError("contract_mismatch", f"provider artifact must use {version}")
    return document


def _receipt_row(
    receipt: Mapping[str, Any], output_index: int, output_bytes: bytes
) -> dict[str, Any]:
    if type(output_index) is not int or not 0 <= output_index <= 7:
        raise LocalityError("contract_mismatch", "output_index must be an integer 0..7")
    rows = [row for row in receipt["outputs"] if row["output_index"] == output_index]
    if len(rows) != 1:
        raise LocalityError("contract_mismatch", "provider receipt has no unique output index")
    row = rows[0]
    expected = {
        "byte_count": len(output_bytes),
        "content_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "content_ref": blake3(output_bytes).hexdigest(),
    }
    if any(row[field] != value for field, value in expected.items()):
        raise LocalityError(
            "provider_payload_mismatch", "provider receipt does not bind the supplied output bytes"
        )
    return row


def _occurrence_binding(
    occurrence: OutputOccurrence | Mapping[str, Any] | None,
    *,
    receipt: Mapping[str, Any],
    row: Mapping[str, Any],
    compiled: _CompiledRaster,
) -> dict[str, Any]:
    if occurrence is None:
        raise LocalityError(
            "output_occurrence_required",
            "compiled provider output requires a full eligible output occurrence",
        )
    document = _provider_document(occurrence, version=OUTPUT_VERSION)
    media = document["media_validation"]
    original = document["original"]
    expected = {
        "producer_kind": "generator_raw",
        "attempt_id": receipt["attempt_id"],
        "output_index": row["output_index"],
        "role": row["role"],
        "provider_receipt_id": receipt["provider_receipt_id"],
        "normalized_request_id": receipt["normalized_request_id"],
    }
    for field, value in expected.items():
        if document[field] != value:
            raise LocalityError("output_occurrence_mismatch", f"output occurrence {field} drifted")
    if document["admission"] != {"state": "eligible", "rejection_reasons": []}:
        raise LocalityError(
            "output_occurrence_ineligible", "structural output occurrence must be eligible"
        )
    expected_original = {
        "content_ref": row["content_ref"],
        "content_sha256": row["content_sha256"],
        "mime": compiled.inspection.mime,
        "byte_count": row["byte_count"],
        "width": compiled.artifact.width,
        "height": compiled.artifact.height,
    }
    if original != expected_original:
        raise LocalityError(
            "output_occurrence_mismatch", "output occurrence original media facts drifted"
        )
    expected_media = {
        "schema_version": "moodboard.media-validation.v1",
        "state": "pass",
        "decoder_revision": COMPILER_REVISION,
        "measured_content_sha256": row["content_sha256"],
        "measured_content_ref": row["content_ref"],
        "measured_byte_count": row["byte_count"],
        "measured_mime": compiled.inspection.mime,
        "measured_width": compiled.artifact.width,
        "measured_height": compiled.artifact.height,
        "measured_mode": compiled.inspection.mode,
        "frame_count": 1,
        "active_content": False,
        "bounded": True,
    }
    if media != expected_media:
        raise LocalityError(
            "output_occurrence_mismatch", "output occurrence measured media facts drifted"
        )
    return document


def _measurements(
    source: CanonicalRasterArtifact,
    inspection: _RasterInspection | None,
    *,
    compiled: bool,
) -> dict[str, Any]:
    if inspection is None or not inspection.container_decoded:
        return {
            "source_width": source.width,
            "source_height": source.height,
            "container_decoded": False,
            "canonical_raster_compiled": False,
            "frame_count": None,
            "output_width": None,
            "output_height": None,
            "output_mode": None,
            "opaque": None,
        }
    return {
        "source_width": source.width,
        "source_height": source.height,
        "container_decoded": True,
        "canonical_raster_compiled": compiled,
        "frame_count": inspection.frame_count,
        "output_width": inspection.width,
        "output_height": inspection.height,
        "output_mode": inspection.mode,
        "opaque": inspection.opaque,
    }


def _seal_judgment(draft: dict[str, Any]) -> ConstraintVerificationJudgment:
    document = copy.deepcopy(draft)
    document["evidence_id"] = "0" * 64
    try:
        document["evidence_id"] = compute_document_identity(
            document,
            schema_version=JUDGMENT_VERSION,
            identity_field="evidence_id",
        )
        judgment = judgment_from_json(document)
    except (ContractIdentityError, JudgmentError) as error:
        raise LocalityError("contract_mismatch", str(error)) from error
    if not isinstance(judgment, ConstraintVerificationJudgment):
        raise LocalityError("contract_mismatch", "locality runtime minted another judgment kind")
    return judgment


def verify_output_structure(
    source_raster: CanonicalRasterArtifact,
    *,
    provider_receipt: ProviderReceipt | Mapping[str, Any],
    output_index: int,
    output_bytes: bytes,
    output_occurrence: OutputOccurrence | Mapping[str, Any] | None = None,
    compiler_manifest: RasterCompilerManifest = DEFAULT_COMPILER_MANIFEST,
) -> StructuralVerification:
    """Verify one receipt-bound provider payload and mint its structural judgment."""

    _raster_document(source_raster)
    payload = _require_payload(output_bytes)
    receipt = _provider_document(provider_receipt, version=RECEIPT_VERSION)
    row = _receipt_row(receipt, output_index, payload)
    sniffed_mime = _sniff_mime(payload)
    if (
        row["media_type_claim"] is not None
        and sniffed_mime is not None
        and row["media_type_claim"] != sniffed_mime
    ):
        raise LocalityError(
            "provider_payload_mismatch", "provider MIME claim conflicts with payload signature"
        )
    try:
        compiled = _compile(
            payload,
            source_content_sha256=row["content_sha256"],
            compiler_manifest=compiler_manifest,
        )
    except LocalityError as error:
        if error.code not in _STRUCTURAL_FAILURE_CODES:
            raise
        if output_occurrence is not None:
            raise LocalityError(
                "invalid_payload_has_occurrence",
                "structurally invalid provider bytes cannot name an output occurrence",
            ) from None
        result = {
            "state": "fail",
            "reason": error.code,
            "measurements": _measurements(source_raster, error.inspection, compiled=False),
        }
        subject = {
            "kind": "provider_output_payload",
            "attempt_id": receipt["attempt_id"],
            "output_index": output_index,
            "provider_receipt_id": receipt["provider_receipt_id"],
            "content_ref": row["content_ref"],
            "content_sha256": row["content_sha256"],
        }
        evidence_ref = {"kind": "artifact", "artifact_id": receipt["provider_receipt_id"]}
        output_raster = None
    else:
        claim = row["media_type_claim"]
        if claim is not None and claim != compiled.inspection.mime:
            raise LocalityError(
                "provider_payload_mismatch", "provider MIME claim conflicts with decoded bytes"
            )
        occurrence = _occurrence_binding(
            output_occurrence,
            receipt=receipt,
            row=row,
            compiled=compiled,
        )
        same_dimensions = (
            compiled.artifact.width == source_raster.width
            and compiled.artifact.height == source_raster.height
        )
        result = {
            "state": "pass" if same_dimensions else "fail",
            "measurements": _measurements(source_raster, compiled.inspection, compiled=True),
        }
        if not same_dimensions:
            result["reason"] = "dimension_mismatch"
        subject = {
            "kind": "selectable_output_occurrence",
            "output_occurrence_id": occurrence["output_occurrence_id"],
        }
        evidence_ref = {
            "kind": "artifact",
            "artifact_id": occurrence["output_occurrence_id"],
        }
        output_raster = compiled.artifact
    authority = {
        "schema_version": STRUCTURAL_VERIFIER_VERSION,
        "input_digest": compute_structural_input_digest(
            source_raster_sha256=source_raster.raster_sha256,
            output_content_sha256=row["content_sha256"],
        ),
        "source_raster_sha256": source_raster.raster_sha256,
        "output_content_sha256": row["content_sha256"],
        "output_raster_sha256": (
            output_raster.raster_sha256 if output_raster is not None else None
        ),
        "decoder_revision": COMPILER_REVISION,
    }
    judgment = _seal_judgment(
        {
            "schema_version": JUDGMENT_VERSION,
            "kind": "constraint_verification",
            "subject": subject,
            "result": result,
            "authority": authority,
            "evidence_ref": evidence_ref,
        }
    )
    return StructuralVerification(judgment=judgment, output_raster=output_raster)


def build_locality_not_run(
    structural_judgment: ConstraintVerificationJudgment,
    mask: CanonicalMaskArtifact,
) -> ConstraintVerificationJudgment:
    """Mint the exact-locality not-run receipt paired to one structural failure."""

    _mask_document(mask)
    try:
        structural = judgment_to_json(structural_judgment)
    except JudgmentError as error:
        raise LocalityError("contract_mismatch", str(error)) from error
    if (
        structural["authority"].get("schema_version") != STRUCTURAL_VERIFIER_VERSION
        or structural["result"].get("state") != "fail"
    ):
        raise LocalityError(
            "contract_mismatch", "locality not_run requires one structural-failure judgment"
        )
    if structural["authority"]["source_raster_sha256"] != mask.source_raster_sha256:
        raise LocalityError(
            "contract_mismatch", "blocking structural source does not match the canonical mask"
        )
    authority = {
        "schema_version": EXACT_LOCALITY_VERIFIER_VERSION,
        "input_digest": compute_exact_locality_not_run_input_digest(
            source_raster_sha256=mask.source_raster_sha256,
            mask_sha256=mask.mask_sha256,
            blocking_structural_evidence_id=structural["evidence_id"],
        ),
        "source_raster_sha256": mask.source_raster_sha256,
        "mask_sha256": mask.mask_sha256,
        "blocking_structural_evidence_id": structural["evidence_id"],
    }
    blocked = _seal_judgment(
        {
            "schema_version": JUDGMENT_VERSION,
            "kind": "constraint_verification",
            "subject": structural["subject"],
            "result": {
                "state": "not_run",
                "reason": "structural_verification_failed",
            },
            "authority": authority,
            "evidence_ref": {
                "kind": "artifact",
                "artifact_id": structural["evidence_id"],
            },
        }
    )
    try:
        validate_locality_blocking_pair(structural, judgment_to_json(blocked))
    except JudgmentError as error:
        raise LocalityError("contract_mismatch", str(error)) from error
    return blocked


def verify_outside_mask_rgb_exact(
    source_raster: CanonicalRasterArtifact,
    output_raster: CanonicalRasterArtifact,
    mask: CanonicalMaskArtifact,
    *,
    output_occurrence: OutputOccurrence | Mapping[str, Any],
    structural_pass: ConstraintVerificationJudgment,
) -> ConstraintVerificationJudgment:
    """Compare every protected RGB channel and mint one exact pass/fail judgment."""

    _raster_document(source_raster)
    _raster_document(output_raster)
    _mask_document(mask)
    occurrence = _provider_document(output_occurrence, version=OUTPUT_VERSION)
    if occurrence["admission"] != {"state": "eligible", "rejection_reasons": []}:
        raise LocalityError("contract_mismatch", "exact locality requires an eligible occurrence")
    try:
        structural = judgment_to_json(structural_pass)
    except JudgmentError as error:
        raise LocalityError("contract_mismatch", str(error)) from error
    expected_subject = {
        "kind": "selectable_output_occurrence",
        "output_occurrence_id": occurrence["output_occurrence_id"],
    }
    if (
        structural["authority"].get("schema_version") != STRUCTURAL_VERIFIER_VERSION
        or structural["result"].get("state") != "pass"
        or structural["subject"] != expected_subject
        or structural["authority"]["source_raster_sha256"] != source_raster.raster_sha256
        or structural["authority"]["output_raster_sha256"] != output_raster.raster_sha256
        or structural["authority"]["output_content_sha256"]
        != occurrence["original"]["content_sha256"]
        or output_raster.source_content_sha256 != occurrence["original"]["content_sha256"]
    ):
        raise LocalityError(
            "contract_mismatch", "exact locality inputs do not match their structural pass"
        )
    if (
        source_raster.width != output_raster.width
        or source_raster.height != output_raster.height
        or mask.width != source_raster.width
        or mask.height != source_raster.height
        or mask.source_raster_sha256 != source_raster.raster_sha256
    ):
        raise LocalityError(
            "contract_mismatch", "source, output, and mask raster identities do not align"
        )
    changed = 0
    maximum = 0
    source_bytes = source_raster.rgb_bytes
    output_bytes = output_raster.rgb_bytes
    for pixel_index, mask_value in enumerate(mask.mask_bytes):
        if mask_value != 0:
            continue
        channel_index = pixel_index * 3
        pixel_changed = False
        for offset in range(3):
            difference = abs(
                source_bytes[channel_index + offset] - output_bytes[channel_index + offset]
            )
            if difference:
                pixel_changed = True
                maximum = max(maximum, difference)
        if pixel_changed:
            changed += 1
    state = "pass" if changed == 0 and maximum == 0 else "fail"
    authority = {
        "schema_version": EXACT_LOCALITY_VERIFIER_VERSION,
        "input_digest": compute_exact_locality_input_digest(
            source_raster_sha256=source_raster.raster_sha256,
            output_raster_sha256=output_raster.raster_sha256,
            mask_sha256=mask.mask_sha256,
        ),
        "source_raster_sha256": source_raster.raster_sha256,
        "output_raster_sha256": output_raster.raster_sha256,
        "mask_sha256": mask.mask_sha256,
    }
    return _seal_judgment(
        {
            "schema_version": JUDGMENT_VERSION,
            "kind": "constraint_verification",
            "subject": expected_subject,
            "result": {
                "state": state,
                "measurements": {
                    "protected_pixel_count": mask.protected_count,
                    "changed_pixel_count": changed,
                    "max_abs_channel_error": maximum,
                },
            },
            "authority": authority,
            "evidence_ref": {
                "kind": "artifact",
                "artifact_id": occurrence["output_occurrence_id"],
            },
        }
    )

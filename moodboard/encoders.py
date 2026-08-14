"""The `Encoder` Protocol and its offline and Khive/Lattice implementations.

ADR-0003 pins CSD as the style axis, with CLIP ViT-L/14 and DINOv2 ViT-L/14 as the baselines
that give the acceptance measurement its meaning. All three load published checkpoints. This
package still loads none of those checkpoints directly. `ClassicalEncoder` runs from the
classical axes in `axes.py` and needs nothing fetched. The opt-in `KhiveLatticeEncoder` uses
ADR-0011's fail-closed Khive process boundary; Khive owns Lattice inference, BlobStore asset
location, and the governed visual vector space.

The boundary is deliberately narrow. An encoder is three plain attributes and one method.
`conformal.py` needs L2-normalised rows so that cosine distance is one minus a dot product,
`board.py` needs `name` and `revision` as the two model-identity strings that enter the board
hash, and `report.py` needs `dim` for the representation block. Nothing else about a
representation is visible to the rest of the engine, so adding a real CSD, CLIP or DINOv2
encoder later is a new class in this file and no change anywhere else.

`ClassicalEncoder` is not a stand-in for the learned model and its output should not be read as
an estimate of what one will do. It is a real, deterministic, fully computed representation of
the three properties `axes.py` measures, and it keeps the conformal machinery, board artifact,
ordinary tests, and default command line fully offline. See the note on axis weighting below
for the one property of it a caller has to know.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
import uuid
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import numpy as np
from blake3 import blake3

from moodboard import axes
from moodboard.khive import KhiveClient, KhiveProtocolError

__all__ = [
    "Encoder",
    "ClassicalEncoder",
    "KhiveAsset",
    "KHIVE_ADAPTER_REVISION",
    "KHIVE_REQUEST_MAX_BYTES",
    "KHIVE_REQUEST_MAX_IMAGES",
    "KHIVE_VISUAL_MATTE_RGB",
    "KhiveLatticeEncoder",
    "VisualDescriptor",
]


@runtime_checkable
class Encoder(Protocol):
    """A representation the engine can fit a board on and score a candidate against.

    `name`, `revision` and `dim` are plain attributes rather than properties with hidden
    computation, because a report reads them directly when it records provenance. `name`
    identifies the representation and `revision` identifies the exact weights or the exact
    version of the computation behind it. Both enter the board hash, so two encoders that
    produce different numbers must not share a `(name, revision)` pair.
    """

    name: str
    revision: str
    dim: int

    def embed(self, images: Sequence[np.ndarray]) -> np.ndarray:
        """Return an (len(images), self.dim) float32 array, one L2-normalised row per input
        image, in input order. Deterministic for a fixed (name, revision) pair and identical
        input arrays. Must not mutate any array in images."""
        ...


def _unit(part: np.ndarray) -> np.ndarray:
    """Scale one descriptor block to unit length, leaving an all-zero block alone.

    An all-zero block has no direction to normalise. It is left as zeros rather than raising,
    because a block can legitimately be empty (a flat image has no salient region) while the
    other two carry the image; `embed` raises only if the whole concatenation is zero, which
    is the case that genuinely has no direction.
    """
    norm = float(np.linalg.norm(part))
    return part if norm == 0.0 else part / norm


def _feature_parts(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The three fixed-length descriptors `ClassicalEncoder` concatenates, in pinned order.

    Kept as one function so the order is written down once. `axes.py` owns what each of these
    measures; this module owns only how they are joined.
    """
    return (
        axes.palette_feature_vector(image),
        axes.tone_feature_vector(image),
        axes.composition_feature_vector(image),
    )


# A fixed, tiny, non-degenerate image used once at import time to read off the three feature
# lengths. `dim` is a property of `axes.py`'s bin counts and grid size and is not a parameter
# this module gets to choose, so it is measured from `axes.py` rather than restated here, which
# would be a second copy of a number that can drift. The probe is a deterministic gradient so
# no code path depends on random data at import.
_DIM_PROBE = np.linspace(0, 255, 8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)


def _classical_dim() -> int:
    return int(sum(part.size for part in _feature_parts(_DIM_PROBE)))


class ClassicalEncoder:
    """Palette, tone and composition descriptors concatenated and L2-normalised.

    Fully classical: histograms, linear filters, an FFT-based saliency estimate. No learned
    weights, no download, deterministic on identical input.

    **Axis weighting, measured, because it decides how to read this encoder's distances.**
    INTERFACES.md revision 2 pins the construction as **each descriptor L2-normalised to unit
    length, then concatenated, then the whole normalised once**, so the three axes contribute
    exactly one third of the squared energy each. Revision 1 concatenated the parts raw and
    normalised only the whole, and that is the defect this construction exists to fix.

    The three descriptors arrive on very different scales: the palette and tone histograms are
    pixel counts over a 128-pixel canonical grid, carrying squared magnitudes of order 1e7 to
    1e8, while the composition descriptor is a saliency distribution summing to one plus a
    ratio in [0, 1], carrying a squared magnitude of order 1. Under revision 1 the composition
    block therefore held a share of the squared energy of order 1e-9, which is below float32
    resolution in a dot product between two unit rows. The axis was present in the vector and
    absent from the answer.

    Regenerate with `uv run python eval/encoder_revision_figures.py`, which reports the share on
    three synthetic subjects (uniform noise, gradient, flat colour) at a pinned seed: 4.97e-10,
    4.40e-09 and 2.60e-09. An earlier version of this docstring cited 3.31e-10, 4.17e-09 and
    2.72e-09 from an uncommitted measurement whose subjects were never recorded; those are
    withdrawn as unreproducible rather than defended, and the order of magnitude, which is the
    whole of the argument, is unchanged.

    The decisive measurement was not the energy share. Two images with identical pixel content,
    a bright square moved from one corner to the other, differ by 0.2318 under
    `axes.composition_distance` and by 5.96e-08 in revision 1 cosine distance, which is below
    float32 epsilon. `tests/test_encoders.py` keeps that probe as a must-pass characterisation
    test with an explicit floor, so this defect class cannot return silently, and that test IS
    the reproducing artifact for these two figures — they are not regenerated by the eval
    script above, which runs an independent second subject rather than restating this one.

    Per-block normalisation also removes a second consequence of the same scale gap, which is
    why no separate fix is needed for it: the palette histogram totalled the pixel count of the
    downsampled image and so depended on source aspect ratio, while the tone histogram always
    totals the fixed canonical grid, so the palette-to-tone balance used to shift with input
    shape. Neither block's raw total survives unit-normalisation. One test arm is kept on that
    property for the same reason.

    Neither the old defect nor this fix affects the conformal guarantee, which ADR-0003 states
    does not depend on the embedding being well behaved, and neither affects the `*_distance`
    functions in `axes.py`, which are separate computations and are what ADR-0003's
    axis-intervention criterion measures.

    `dim` is fixed by `axes.py`'s bin counts and grid size, not chosen at construction. A
    change to any of those changes every embedding this class produces, so it requires a
    `revision` bump; `tests/test_encoders.py` pins the three part lengths so such a change
    fails loudly rather than silently invalidating board hashes.

    Revision 2 is a semantic break: every embedding and therefore every board hash changes.
    The set of `brand.mb` artifacts invalidated by it was empty when it landed, which is why it
    landed then rather than later.
    """

    name: str = "classical-v1"
    revision: str = "2"
    dim: int = _classical_dim()

    def embed(self, images: Sequence[np.ndarray]) -> np.ndarray:
        """Return an (len(images), self.dim) float32 array, one L2-normalised row per input
        image, in input order. Deterministic for a fixed (name, revision) pair and identical
        input arrays. Must not mutate any array in images.

        Normalisation happens in float64 and the cast to float32 follows it, so each row is
        unit norm to within float32 resolution rather than to within whatever the accumulated
        error of a float32 reduction would have been.
        """
        rows = np.empty((len(images), self.dim), dtype=np.float64)
        for index, image in enumerate(images):
            _validate_image(image, index)
            parts = _feature_parts(image)
            vector = np.concatenate([_unit(part) for part in parts])
            if vector.size != self.dim:
                raise RuntimeError(
                    f"image at index {index} produced a {vector.size}-length descriptor, "
                    f"but this encoder is fixed at {self.dim}. The feature-vector functions "
                    f"in axes.py must return the same lengths for every image."
                )
            rows[index] = vector

        norms = np.linalg.norm(rows, axis=1)
        degenerate = np.flatnonzero(norms == 0.0)
        if degenerate.size:
            raise ValueError(
                f"image at index {int(degenerate[0])} produced an all-zero descriptor, which "
                f"has no direction to normalise. This means every palette, tone and "
                f"composition feature was empty, which a valid image cannot produce."
            )
        return (rows / norms[:, None]).astype(np.float32)


def _validate_image(image: np.ndarray, index: int) -> None:
    """Reject shapes `axes.py` cannot work on, naming the offending position.

    Without this an empty or one-dimensional array reaches a resize deep inside an axis
    function and fails with a message that names neither the image nor the caller's argument.
    """
    array = np.asarray(image)
    if array.ndim not in (2, 3):
        raise ValueError(
            f"image at index {index} has {array.ndim} dimensions; an image must be (H, W) or "
            f"(H, W, C)."
        )
    if array.ndim == 3 and array.shape[-1] not in (1, 3, 4):
        raise ValueError(
            f"image at index {index} has {array.shape[-1]} channels; expected 1, 3 or 4."
        )
    if min(array.shape[:2]) < 1:
        raise ValueError(f"image at index {index} has zero extent: shape {array.shape}.")


_HEX = frozenset("0123456789abcdef")
_DESCRIPTOR_SCHEMA = "moodboard.visual-descriptor.v1"
_DESCRIPTOR_MODEL_NAME = "qwen3.5-vlm-pooled-visual"
_DESCRIPTOR_PROMPT_SHA256 = "a67ae9b539c243f498c75f1ea9f19e7018860948087728d6f8e65b34eef6a66e"
_UNIT_NORM_ATOL = 1e-5
_KHIVE_SOURCE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
KHIVE_ADAPTER_REVISION = "moodboard-khive-adapter-v3"
"""Pinned rendition, source-ingest, storage-namespace, and process-partition contract revision."""
KHIVE_VISUAL_MATTE_RGB = (128, 128, 128)
"""The frozen v1 alpha-compositing matte shared with the Khive Moodboard pack."""
KHIVE_REQUEST_MAX_IMAGES = 64
"""Maximum total asset occurrences admitted by one logical encoder call."""
KHIVE_REQUEST_MAX_BYTES = 32 * 1024 * 1024
"""Maximum decoded payload bytes across all occurrences before byte deduplication."""
_KHIVE_INGEST_PROCESS_MAX_UNIQUE = 8
"""Maximum unique ingests sharing one bounded Khive request-read deadline."""
_DESCRIPTOR_KEYS = frozenset(
    {
        "schema_version",
        "model_key",
        "model_name",
        "model_revision",
        "checkpoint_sha256",
        "inference",
        "preprocessing",
        "prompt",
        "pooling",
        "dimensions",
        "normalization",
        "fingerprint",
    }
)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise KhiveProtocolError(f"visual descriptor is not strict JSON: {error}") from error


def _is_hex_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise KhiveProtocolError(f"visual descriptor {field} must be an object")
    return value


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise KhiveProtocolError(f"visual descriptor {field} must be a non-empty string")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        detail = []
        if unknown:
            detail.append(f"unknown keys {unknown}")
        if missing:
            detail.append(f"missing keys {missing}")
        raise KhiveProtocolError(f"visual descriptor {field} has " + " and ".join(detail))


def _require_result_keys(value: Mapping[str, Any], expected: frozenset[str], result: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        details = []
        if unknown:
            details.append(f"unknown keys {unknown}")
        if missing:
            details.append(f"missing keys {missing}")
        raise KhiveProtocolError(f"{result} has " + " and ".join(details))


@dataclass(frozen=True, slots=True)
class VisualDescriptor:
    """Validated, immutable identity for one Lattice visual vector space.

    Only canonical JSON is retained.  ``to_json_dict`` returns a fresh object, so a caller
    cannot mutate the identity held by an encoder after its board hash has been computed.
    """

    canonical_json: str
    model_key: str
    model_name: str
    model_revision: str
    checkpoint_sha256: str
    dimensions: int
    fingerprint: str

    @classmethod
    def parse(cls, value: Any) -> VisualDescriptor:
        document = _require_mapping(value, "root")
        _require_exact_keys(document, _DESCRIPTOR_KEYS, "root")
        if document.get("schema_version") != _DESCRIPTOR_SCHEMA:
            raise KhiveProtocolError(
                f"visual descriptor schema_version must be {_DESCRIPTOR_SCHEMA!r}"
            )
        model_name = _require_text(document.get("model_name"), "model_name")
        if model_name != _DESCRIPTOR_MODEL_NAME:
            raise KhiveProtocolError(
                f"visual descriptor model_name must be {_DESCRIPTOR_MODEL_NAME!r}"
            )
        model_revision = _require_text(document.get("model_revision"), "model_revision")
        checkpoint = document.get("checkpoint_sha256")
        if not _is_hex_digest(checkpoint):
            raise KhiveProtocolError(
                "visual descriptor checkpoint_sha256 must be 64 lowercase hex characters"
            )

        inference = _require_mapping(document.get("inference"), "inference")
        _require_exact_keys(inference, frozenset({"provider", "version"}), "inference")
        if inference.get("provider") != "lattice-embed" or inference.get("version") != "0.9.0":
            raise KhiveProtocolError(
                "visual descriptor inference must identify lattice-embed version 0.9.0"
            )
        preprocessing = _require_mapping(document.get("preprocessing"), "preprocessing")
        _require_exact_keys(
            preprocessing,
            frozenset({"revision", "max_side", "alignment", "matte_rgb", "resample"}),
            "preprocessing",
        )
        expected_preprocessing = {
            "revision": "moodboard-qwen35-srgb-pad32-max448-v1",
            "max_side": 448,
            "alignment": 32,
            "matte_rgb": list(KHIVE_VISUAL_MATTE_RGB),
            "resample": "lanczos3",
        }
        for field, expected in expected_preprocessing.items():
            if preprocessing.get(field) != expected:
                raise KhiveProtocolError(
                    f"visual descriptor preprocessing.{field} is "
                    f"{preprocessing.get(field)!r}; expected {expected!r}"
                )
        prompt = _require_mapping(document.get("prompt"), "prompt")
        _require_exact_keys(prompt, frozenset({"revision", "sha256"}), "prompt")
        if (
            prompt.get("revision") != "moodboard-style-retrieval-v1"
            or prompt.get("sha256") != _DESCRIPTOR_PROMPT_SHA256
        ):
            raise KhiveProtocolError(
                "visual descriptor prompt must carry revision moodboard-style-retrieval-v1 "
                f"and sha256 {_DESCRIPTOR_PROMPT_SHA256}"
            )
        if document.get("pooling") != "mean_visual_tokens":
            raise KhiveProtocolError("visual descriptor pooling must be 'mean_visual_tokens'")
        dimensions = document.get("dimensions")
        if (
            not isinstance(dimensions, int)
            or isinstance(dimensions, bool)
            or not 1 <= dimensions <= 8192
        ):
            raise KhiveProtocolError(
                "visual descriptor dimensions must be an integer from 1 through 8192"
            )
        if document.get("normalization") != "l2":
            raise KhiveProtocolError("visual descriptor normalization must be 'l2'")
        fingerprint = document.get("fingerprint")
        if not _is_hex_digest(fingerprint):
            raise KhiveProtocolError(
                "visual descriptor fingerprint must be 64 lowercase hex characters"
            )

        identity = {
            key: nested
            for key, nested in document.items()
            if key not in {"fingerprint", "model_key"}
        }
        measured_fingerprint = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
        if fingerprint != measured_fingerprint:
            raise KhiveProtocolError(
                "visual descriptor fingerprint does not match its canonical identity"
            )
        model_key = _require_text(document.get("model_key"), "model_key")
        expected_key = f"moodboard_{fingerprint}_{dimensions}"
        if model_key != expected_key:
            raise KhiveProtocolError(
                f"visual descriptor model_key is {model_key!r}; expected {expected_key!r}"
            )

        return cls(
            canonical_json=_canonical_json(document),
            model_key=model_key,
            model_name=model_name,
            model_revision=model_revision,
            checkpoint_sha256=checkpoint,
            dimensions=dimensions,
            fingerprint=fingerprint,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return json.loads(self.canonical_json)


@dataclass(frozen=True, slots=True)
class KhiveAsset:
    """Durable location and outcome metadata returned for one ingested image."""

    asset_id: str
    content_ref: str
    created: bool
    indexed: bool
    byte_identity: Literal["source-bytes", "canonical-png-rendition"]


def _model_result(value: Any) -> VisualDescriptor:
    if not isinstance(value, dict):
        raise KhiveProtocolError("moodboard.model result must be an object")
    _require_result_keys(value, frozenset({"descriptor", "experimental"}), "moodboard.model result")
    if value.get("experimental") is not True:
        raise KhiveProtocolError("moodboard.model must explicitly report experimental=true")
    return VisualDescriptor.parse(value.get("descriptor"))


def _canonical_png_size(image: np.ndarray, index: int) -> int:
    """Return the exact byte length produced by ``_png_bytes`` without encoding pixels."""
    _validate_image(image, index)
    array = np.asarray(image)
    channels = 4 if array.ndim == 3 and array.shape[-1] == 4 else 3
    height, width = array.shape[:2]
    filtered_bytes = height * (1 + width * channels)
    stored_blocks = (filtered_bytes + 65_534) // 65_535
    zlib_bytes = 2 + filtered_bytes + 5 * stored_blocks + 4
    # Signature; IHDR, IDAT and IEND chunk framing; and the 13-byte IHDR payload.
    return 8 + (12 + 13) + (12 + zlib_bytes) + 12


def _png_bytes(image: np.ndarray, index: int) -> bytes:
    _validate_image(image, index)
    array = np.asarray(image)
    if array.dtype == np.uint8:
        pixels = array.astype(np.float64) / 255.0
    elif np.issubdtype(array.dtype, np.floating):
        pixels = array.astype(np.float64)
        if not np.isfinite(pixels).all():
            raise ValueError(f"image at index {index} has a non-finite pixel value")
        minimum = float(np.min(pixels))
        maximum = float(np.max(pixels))
        if minimum < 0.0 or maximum > 1.0:
            raise ValueError(
                f"image at index {index} has floating pixels outside [0,1]: "
                f"min={minimum}, max={maximum}"
            )
    else:
        raise ValueError(
            f"image at index {index} has dtype {array.dtype}; Khive arrays must be uint8 "
            "in [0,255] or floating point in [0,1]"
        )
    if pixels.ndim == 2:
        pixels = np.repeat(pixels[..., None], 3, axis=-1)
    elif pixels.shape[-1] == 1:
        pixels = np.repeat(pixels, 3, axis=-1)
    # Preserve RGBA. The descriptor pins alpha compositing to gray in the Khive pack; dropping
    # alpha here would expose hidden RGB instead of the visual a viewer sees.
    encoded_pixels = np.rint(pixels * 255.0).astype(np.uint8)
    height, width, channels = encoded_pixels.shape
    scanlines = np.empty((height, 1 + width * channels), dtype=np.uint8)
    scanlines[:, 0] = 0  # PNG filter type None, frozen for every row.
    scanlines[:, 1:] = encoded_pixels.reshape(height, width * channels)
    filtered = scanlines.tobytes(order="C")

    # A byte-frozen zlib stream: fixed 0x78/0x01 wrapper and DEFLATE stored blocks. No Pillow,
    # compressor, platform, or heuristic can change the canonical rendition ContentRef.
    compressed = bytearray(b"\x78\x01")
    for offset in range(0, len(filtered), 65_535):
        block = filtered[offset : offset + 65_535]
        final = offset + len(block) == len(filtered)
        compressed.append(1 if final else 0)  # BFINAL plus BTYPE=00, then byte alignment.
        compressed.extend(struct.pack("<H", len(block)))
        compressed.extend(struct.pack("<H", len(block) ^ 0xFFFF))
        compressed.extend(block)
    compressed.extend(struct.pack(">I", zlib.adler32(filtered) & 0xFFFFFFFF))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    color_type = 6 if channels == 4 else 2
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", bytes(compressed))
        + chunk(b"IEND", b"")
    )


class _RequestBudget:
    """Admit payloads incrementally so limits apply before a full request is materialised."""

    def __init__(self) -> None:
        self._payload_by_content_ref: dict[str, bytes] = {}
        self.decoded_bytes = 0

    @property
    def remaining_bytes(self) -> int:
        return KHIVE_REQUEST_MAX_BYTES - self.decoded_bytes

    def admit(self, data: bytes, index: int) -> bytes:
        content_ref = blake3(data).hexdigest()
        total = self.decoded_bytes + len(data)
        if total > KHIVE_REQUEST_MAX_BYTES:
            raise ValueError(
                f"Khive ingest would have {total} decoded bytes at asset index {index}; "
                f"the in-process request budget is {KHIVE_REQUEST_MAX_BYTES}"
            )
        self.decoded_bytes = total
        existing = self._payload_by_content_ref.get(content_ref)
        if existing is not None:
            if existing != data:
                raise ValueError(
                    f"asset payload at index {index} has a BLAKE3 collision with an earlier "
                    "payload; refusing to deduplicate distinct bytes"
                )
            return existing
        self._payload_by_content_ref[content_ref] = data
        return data


def _read_source_with_limit(path: Path, limit: int, index: int) -> bytes:
    """Read at most ``limit + 1`` bytes so an oversized file is never materialised."""
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError(
            f"Khive ingest would exceed its decoded-byte budget while reading source image "
            f"at index {index}; at most {limit} bytes remain"
        )
    return data


class KhiveLatticeEncoder:
    """Lattice visual inference and Khive asset publication behind ``Encoder``.

    Constructing the encoder discovers and validates the active descriptor once. Every ingest
    repeats that descriptor and is compared byte-for-canonical-byte, so a hot model reload
    cannot mix vector spaces within a board.
    """

    def __init__(self, client: KhiveClient) -> None:
        self._client = client
        result = client.model()
        self.descriptor = _model_result(result)
        self.name = f"khive:{self.descriptor.model_name}"
        self.revision = f"{self.descriptor.fingerprint}+{KHIVE_ADAPTER_REVISION}"
        self.dim = self.descriptor.dimensions
        self.last_assets: tuple[KhiveAsset, ...] = ()

    def embed(self, images: Sequence[np.ndarray]) -> np.ndarray:
        return self.embed_assets(images)

    def embed_assets(
        self,
        images: Sequence[np.ndarray],
        *,
        names: Sequence[str | None] | None = None,
        captions: Sequence[str | None] | None = None,
    ) -> np.ndarray:
        count = len(images)
        names_tuple = tuple(names) if names is not None else (None,) * count
        captions_tuple = tuple(captions) if captions is not None else (None,) * count
        if len(names_tuple) != count:
            raise ValueError(f"names has {len(names_tuple)} entries for {count} images")
        if len(captions_tuple) != count:
            raise ValueError(f"captions has {len(captions_tuple)} entries for {count} images")
        self.last_assets = ()
        if count > KHIVE_REQUEST_MAX_IMAGES:
            raise ValueError(
                f"Khive ingest has {count} images; the in-process request budget allows at "
                f"most {KHIVE_REQUEST_MAX_IMAGES}"
            )
        if count == 0:
            return np.empty((0, self.dim), dtype=np.float32)

        payloads: list[tuple[bytes, str, str, str | None]] = []
        budget = _RequestBudget()
        for index, (image, name, caption) in enumerate(
            zip(images, names_tuple, captions_tuple, strict=True)
        ):
            rendition_size = _canonical_png_size(image, index)
            if rendition_size > budget.remaining_bytes:
                total = budget.decoded_bytes + rendition_size
                raise ValueError(
                    f"Khive ingest would have {total} decoded bytes at asset index {index}; "
                    f"the in-process request budget is {KHIVE_REQUEST_MAX_BYTES}"
                )
            png = budget.admit(_png_bytes(image, index), index)
            payloads.append(
                (png, name if name is not None else f"image-{index:06d}.png", "image/png", caption)
            )
        return self._ingest_payloads(payloads, byte_identity="canonical-png-rendition")

    def embed_source_assets(
        self,
        paths: Sequence[Path],
        *,
        expected_sha256: Sequence[str],
        media_types: Sequence[str],
        names: Sequence[str | None] | None = None,
        captions: Sequence[str | None] | None = None,
    ) -> np.ndarray:
        """Ingest exact file bytes after binding them to the caller's prior SHA-256 read.

        The ordinary ``Encoder.embed`` boundary has arrays and therefore can only publish a
        canonical PNG rendition. The CLI has paths and the hashes already used by
        ``board_hash``; this narrow method preserves the source bytes while detecting a file
        changed between load and ingest.
        """
        count = len(paths)
        expected_tuple = tuple(expected_sha256)
        media_tuple = tuple(media_types)
        names_tuple = tuple(names) if names is not None else (None,) * count
        captions_tuple = tuple(captions) if captions is not None else (None,) * count
        lengths = {
            "expected_sha256": len(expected_tuple),
            "media_types": len(media_tuple),
            "names": len(names_tuple),
            "captions": len(captions_tuple),
        }
        mismatch = next((field for field, length in lengths.items() if length != count), None)
        if mismatch is not None:
            raise ValueError(f"{mismatch} has {lengths[mismatch]} entries for {count} paths")
        self.last_assets = ()
        if count > KHIVE_REQUEST_MAX_IMAGES:
            raise ValueError(
                f"Khive ingest has {count} images; the in-process request budget allows at "
                f"most {KHIVE_REQUEST_MAX_IMAGES}"
            )
        if count == 0:
            return np.empty((0, self.dim), dtype=np.float32)

        payloads: list[tuple[bytes, str, str, str | None]] = []
        budget = _RequestBudget()
        source_by_sha256: dict[str, bytes] = {}
        for index, (path, expected, media_type, name, caption) in enumerate(
            zip(
                paths,
                expected_tuple,
                media_tuple,
                names_tuple,
                captions_tuple,
                strict=True,
            )
        ):
            if media_type not in _KHIVE_SOURCE_MEDIA_TYPES:
                raise ValueError(
                    f"media_type at index {index} is {media_type!r}; Khive source-byte ingest "
                    "supports image/png, image/jpeg, and image/webp"
                )
            prior = source_by_sha256.get(expected)
            if prior is None:
                if budget.remaining_bytes == 0:
                    raise ValueError(
                        f"Khive ingest would exceed its decoded-byte budget at source image "
                        f"index {index}; no bytes remain"
                    )
                data = _read_source_with_limit(Path(path), budget.remaining_bytes, index)
            else:
                # Re-read duplicate paths to preserve the same TOCTOU guarantee, but cap the
                # read at the already admitted byte length and reuse the first immutable bytes.
                if len(prior) > budget.remaining_bytes:
                    raise ValueError(
                        f"Khive ingest would exceed its decoded-byte budget before duplicate "
                        f"source image at index {index}; {budget.remaining_bytes} bytes remain"
                    )
                data = _read_source_with_limit(Path(path), len(prior), index)
            measured = hashlib.sha256(data).hexdigest()
            if measured != expected:
                raise ValueError(
                    f"source image at index {index} changed after it was loaded: expected "
                    f"sha256 {expected!r}, read {measured!r}"
                )
            if prior is not None and data != prior:
                raise ValueError(
                    f"source image at index {index} has the same expected SHA-256 as an "
                    "earlier path but different bytes"
                )
            data = budget.admit(data, index)
            source_by_sha256.setdefault(expected, data)
            source_name = name if name is not None else Path(path).name
            payloads.append((data, source_name, media_type, caption))
        return self._ingest_payloads(payloads, byte_identity="source-bytes")

    def _ingest_payloads(
        self,
        payloads: Sequence[tuple[bytes, str, str, str | None]],
        *,
        byte_identity: Literal["source-bytes", "canonical-png-rendition"],
    ) -> np.ndarray:
        unique_payloads: list[tuple[bytes, str, str, str | None]] = []
        expected_content_refs: list[str] = []
        occurrence_to_unique: list[int] = []
        unique_by_content_ref: dict[str, int] = {}
        first_occurrence_by_unique: list[int] = []
        for index, (data, name, media_type, caption) in enumerate(payloads):
            if not isinstance(data, bytes):
                raise ValueError(f"asset payload at index {index} must be bytes")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"asset name at index {index} must be a non-empty string")
            if not isinstance(media_type, str) or not media_type.strip():
                raise ValueError(f"asset media_type at index {index} must be a non-empty string")
            if caption is not None and (not isinstance(caption, str) or not caption.strip()):
                raise ValueError(
                    f"asset caption at index {index} must be absent or a non-empty string"
                )
            expected_content_ref = blake3(data).hexdigest()
            existing = unique_by_content_ref.get(expected_content_ref)
            if existing is not None:
                if unique_payloads[existing][2] != media_type:
                    raise ValueError(
                        f"asset payload at index {index} duplicates index "
                        f"{first_occurrence_by_unique[existing]} byte-for-byte but declares a "
                        "different media_type"
                    )
                occurrence_to_unique.append(existing)
                continue
            unique_index = len(unique_payloads)
            unique_by_content_ref[expected_content_ref] = unique_index
            occurrence_to_unique.append(unique_index)
            unique_payloads.append((data, name, media_type, caption))
            first_occurrence_by_unique.append(index)
            expected_content_refs.append(expected_content_ref)

        if len(payloads) > KHIVE_REQUEST_MAX_IMAGES:
            raise ValueError(
                f"Khive ingest has {len(payloads)} images; the in-process request budget "
                f"allows at most {KHIVE_REQUEST_MAX_IMAGES}"
            )
        request_bytes = sum(len(payload[0]) for payload in payloads)
        if request_bytes > KHIVE_REQUEST_MAX_BYTES:
            raise ValueError(
                f"Khive ingest has {request_bytes} decoded bytes; the in-process request "
                f"budget is {KHIVE_REQUEST_MAX_BYTES}"
            )

        arguments: list[dict[str, Any]] = []
        for data, name, media_type, caption in unique_payloads:
            args: dict[str, Any] = {
                "image_base64": base64.b64encode(data).decode("ascii"),
                "name": name,
                "media_type": media_type,
            }
            if caption is not None:
                args["caption"] = caption
            arguments.append(args)

        unique_vectors: list[np.ndarray] = []
        unique_assets: list[KhiveAsset] = []
        for start in range(0, len(arguments), _KHIVE_INGEST_PROCESS_MAX_UNIQUE):
            stop = min(start + _KHIVE_INGEST_PROCESS_MAX_UNIQUE, len(arguments))
            results = self._client.ingest(arguments[start:stop])
            for index, (result, expected_content_ref) in enumerate(
                zip(results, expected_content_refs[start:stop], strict=True),
                start=start,
            ):
                asset, vector = self._validate_ingest_result(
                    result, index, byte_identity, expected_content_ref
                )
                unique_assets.append(asset)
                unique_vectors.append(vector)

        matrix = np.empty((len(payloads), self.dim), dtype=np.float64)
        assets: list[KhiveAsset] = []
        seen_unique: set[int] = set()
        for occurrence, unique_index in enumerate(occurrence_to_unique):
            asset = unique_assets[unique_index]
            if unique_index in seen_unique and asset.created:
                asset = KhiveAsset(
                    asset_id=asset.asset_id,
                    content_ref=asset.content_ref,
                    created=False,
                    indexed=asset.indexed,
                    byte_identity=asset.byte_identity,
                )
            seen_unique.add(unique_index)
            assets.append(asset)
            matrix[occurrence] = unique_vectors[unique_index]
        self.last_assets = tuple(assets)
        return matrix.astype(np.float32)

    def _validate_ingest_result(
        self,
        value: Any,
        index: int,
        byte_identity: Literal["source-bytes", "canonical-png-rendition"],
        expected_content_ref: str,
    ) -> tuple[KhiveAsset, np.ndarray]:
        if not isinstance(value, dict):
            raise KhiveProtocolError(f"moodboard.ingest result {index} must be an object")
        _require_result_keys(
            value,
            frozenset(
                {
                    "asset_id",
                    "content_ref",
                    "created",
                    "indexed",
                    "descriptor",
                    "experimental",
                    "embedding",
                }
            ),
            f"moodboard.ingest result {index}",
        )
        if value.get("experimental") is not True:
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} must explicitly report experimental=true"
            )
        returned_descriptor = VisualDescriptor.parse(value.get("descriptor"))
        if returned_descriptor.canonical_json != self.descriptor.canonical_json:
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} has descriptor drift from moodboard.model"
            )

        asset_id = value.get("asset_id")
        try:
            parsed_asset_id = uuid.UUID(asset_id) if isinstance(asset_id, str) else None
        except ValueError as error:
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} asset_id is not a UUID"
            ) from error
        if parsed_asset_id is None or str(parsed_asset_id) != asset_id:
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} asset_id is not a canonical UUID"
            )
        content_ref = value.get("content_ref")
        if not _is_hex_digest(content_ref):
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} content_ref must be 64 lowercase hex characters"
            )
        if content_ref != expected_content_ref:
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} content_ref does not match its submitted "
                "bytes; the batch may be reordered or cross-wired"
            )
        created = value.get("created")
        indexed = value.get("indexed")
        if not isinstance(created, bool) or not isinstance(indexed, bool):
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} created/indexed flags must be booleans"
            )
        if not indexed:
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} returned an embedding without indexing it"
            )

        raw_vector = value.get("embedding")
        if not isinstance(raw_vector, list) or len(raw_vector) != self.dim:
            length = len(raw_vector) if isinstance(raw_vector, list) else None
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} embedding dimension is {length!r}; "
                f"expected {self.dim}"
            )
        if any(
            isinstance(coordinate, bool) or not isinstance(coordinate, (int, float))
            for coordinate in raw_vector
        ):
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} embedding coordinates must be plain JSON numbers"
            )
        try:
            vector = np.asarray(raw_vector, dtype=np.float64)
        except (TypeError, ValueError, OverflowError) as error:
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} embedding is not numeric"
            ) from error
        if vector.shape != (self.dim,):
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} embedding shape is {vector.shape}; "
                f"expected ({self.dim},)"
            )
        if not np.isfinite(vector).all():
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} embedding has a non-finite value"
            )
        norm = float(np.linalg.norm(vector))
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=_UNIT_NORM_ATOL):
            raise KhiveProtocolError(
                f"moodboard.ingest result {index} embedding is not unit-normalized: norm={norm}"
            )
        return KhiveAsset(asset_id, content_ref, created, indexed, byte_identity), vector

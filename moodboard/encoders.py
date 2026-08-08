"""The `Encoder` Protocol every representation implements, and the one encoder that needs no
downloaded weights.

ADR-0003 pins CSD as the style axis, with CLIP ViT-L/14 and DINOv2 ViT-L/14 as the baselines
that give the acceptance measurement its meaning. All three load published checkpoints. This
pass builds none of them. What it builds is the boundary they will sit behind, plus one
concrete encoder that runs from the classical axes in `axes.py` and therefore needs nothing
fetched: `ClassicalEncoder`.

The boundary is deliberately narrow. An encoder is three plain attributes and one method.
`conformal.py` needs L2-normalised rows so that cosine distance is one minus a dot product,
`board.py` needs `name` and `revision` as the two model-identity strings that enter the board
hash, and `report.py` needs `dim` for the representation block. Nothing else about a
representation is visible to the rest of the engine, so adding a real CSD, CLIP or DINOv2
encoder later is a new class in this file and no change anywhere else.

`ClassicalEncoder` is not a stand-in for those models and its output should not be read as an
estimate of what they will do. It is a real, deterministic, fully computed representation of
the three properties `axes.py` measures, and it exists so the conformal machinery, the board
artifact and the command line can be exercised end to end on synthetic arrays with no network
access. See the note on axis weighting below for the one property of it a caller has to know.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np

from moodboard import axes

__all__ = ["Encoder", "ClassicalEncoder"]


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

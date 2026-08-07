"""Properties of the encoder boundary and of the one encoder that needs no weights.

Every fixture here is a synthetic array. No dataset download, no network access, and no
learned checkpoint, per IMPLEMENTATION_CONTRACT.md deliverable 8.

The properties tested are the ones INTERFACES.md states as the Protocol's contract: the return
shape and dtype, L2-normalised rows, input order preserved, determinism, and no mutation of the
caller's arrays. Two further tests pin numbers that are not free parameters. The first pins the
three feature lengths, so a change to `axes.py`'s bin counts fails here rather than silently
changing every embedding produced under `revision = "1"`. The second characterises the axis
weighting the concatenation produces, so that a future change to it is a deliberate, visible
act rather than a quiet one.
"""

import numpy as np
import pytest

from moodboard import axes
from moodboard.encoders import ClassicalEncoder, Encoder

# The three descriptor lengths as `axes.py` currently defines them: a 4x4x4 CIELAB histogram,
# two 16-bin tone histograms, and a 4x4 saliency grid plus one negative-space ratio.
EXPECTED_PALETTE_LENGTH = 64
EXPECTED_TONE_LENGTH = 32
EXPECTED_COMPOSITION_LENGTH = 17
EXPECTED_DIM = EXPECTED_PALETTE_LENGTH + EXPECTED_TONE_LENGTH + EXPECTED_COMPOSITION_LENGTH


def noise_image(seed: int, height: int = 40, width: int = 50) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (height, width, 3), dtype=np.uint8)


def flat_image(rgb: tuple[int, int, int], side: int = 32) -> np.ndarray:
    return np.tile(np.array(rgb, dtype=np.uint8), (side, side, 1))


def gradient_image(side: int = 48) -> np.ndarray:
    ramp = np.linspace(0, 255, side, dtype=np.uint8)
    return np.repeat(np.tile(ramp[None, :], (side, 1))[..., None], 3, axis=-1)


class TestTheProtocol:
    def test_classical_encoder_satisfies_the_encoder_protocol(self):
        assert isinstance(ClassicalEncoder(), Encoder)

    def test_an_object_missing_embed_does_not_satisfy_it(self):
        class NotAnEncoder:
            name = "x"
            revision = "1"
            dim = 3

        assert not isinstance(NotAnEncoder(), Encoder)

    def test_identity_fields_are_the_pinned_strings(self):
        encoder = ClassicalEncoder()
        assert encoder.name == "classical-v1"
        assert encoder.revision == "1"

    def test_identity_fields_are_plain_attributes_not_properties(self):
        # board.py and report.py read these off the class without calling anything.
        for field in ("name", "revision", "dim"):
            assert not isinstance(getattr(ClassicalEncoder, field), property)


class TestDimIsFixedByAxesNotChosenHere:
    def test_dim_is_the_sum_of_the_three_feature_lengths(self):
        assert ClassicalEncoder.dim == EXPECTED_DIM

    def test_each_feature_vector_has_its_pinned_length(self):
        image = noise_image(0)
        assert axes.palette_feature_vector(image).size == EXPECTED_PALETTE_LENGTH
        assert axes.tone_feature_vector(image).size == EXPECTED_TONE_LENGTH
        assert axes.composition_feature_vector(image).size == EXPECTED_COMPOSITION_LENGTH

    def test_dim_does_not_vary_with_image_size_or_shape(self):
        encoder = ClassicalEncoder()
        images = [
            noise_image(1, height=17, width=200),
            noise_image(2, height=200, width=17),
            flat_image((10, 200, 90), side=8),
        ]
        assert encoder.embed(images).shape == (3, EXPECTED_DIM)

    def test_dim_is_not_a_constructor_parameter(self):
        with pytest.raises(TypeError):
            ClassicalEncoder(dim=7)


class TestEmbedReturnContract:
    def test_shape_and_dtype(self):
        encoder = ClassicalEncoder()
        result = encoder.embed([noise_image(3), noise_image(4)])
        assert result.shape == (2, encoder.dim)
        assert result.dtype == np.float32

    def test_rows_are_l2_normalised(self):
        encoder = ClassicalEncoder()
        images = [noise_image(5), gradient_image(), flat_image((240, 240, 230))]
        norms = np.linalg.norm(encoder.embed(images).astype(np.float64), axis=1)
        assert np.allclose(norms, 1.0, atol=1e-6)

    def test_rows_come_back_in_input_order(self):
        encoder = ClassicalEncoder()
        images = [noise_image(6), gradient_image(), flat_image((20, 180, 60))]
        together = encoder.embed(images)
        for position, image in enumerate(images):
            alone = encoder.embed([image])[0]
            assert np.array_equal(together[position], alone)

    def test_an_empty_sequence_returns_an_empty_matrix_of_the_right_width(self):
        encoder = ClassicalEncoder()
        result = encoder.embed([])
        assert result.shape == (0, encoder.dim)
        assert result.dtype == np.float32

    def test_distinct_images_get_distinct_embeddings(self):
        encoder = ClassicalEncoder()
        rows = encoder.embed([flat_image((220, 20, 20)), flat_image((20, 20, 220))])
        assert not np.allclose(rows[0], rows[1])


class TestDeterminismAndPurity:
    def test_repeated_calls_are_bit_identical(self):
        encoder = ClassicalEncoder()
        images = [noise_image(7), gradient_image()]
        assert np.array_equal(encoder.embed(images), encoder.embed(images))

    def test_two_instances_agree(self):
        images = [noise_image(8)]
        assert np.array_equal(ClassicalEncoder().embed(images), ClassicalEncoder().embed(images))

    def test_the_callers_arrays_are_not_mutated(self):
        images = [noise_image(9), gradient_image(), flat_image((5, 5, 5))]
        before = [image.copy() for image in images]
        ClassicalEncoder().embed(images)
        for original, after in zip(before, images, strict=True):
            assert np.array_equal(original, after)


class TestAcceptedInputForms:
    @pytest.mark.parametrize(
        "image",
        [
            pytest.param(noise_image(10), id="uint8-rgb"),
            pytest.param(noise_image(11).astype(np.float64) / 255.0, id="float-rgb"),
            pytest.param(noise_image(12)[..., 0], id="grayscale-2d"),
            pytest.param(noise_image(13)[..., :1], id="single-channel-3d"),
            pytest.param(
                np.concatenate(
                    [noise_image(14), np.full((40, 50, 1), 255, dtype=np.uint8)], axis=-1
                ),
                id="rgba",
            ),
        ],
    )
    def test_accepted_forms_embed_to_a_unit_row(self, image):
        row = ClassicalEncoder().embed([image])
        assert row.shape == (1, EXPECTED_DIM)
        assert np.isclose(np.linalg.norm(row.astype(np.float64)), 1.0, atol=1e-6)

    def test_uint8_and_its_float_equivalent_agree(self):
        image = noise_image(15)
        as_float = image.astype(np.float64) / 255.0
        encoder = ClassicalEncoder()
        assert np.allclose(encoder.embed([image]), encoder.embed([as_float]), atol=1e-6)


class TestRejectedInputNamesTheOffendingPosition:
    def test_a_one_dimensional_array_is_rejected(self):
        with pytest.raises(ValueError, match="index 1"):
            ClassicalEncoder().embed([noise_image(16), np.zeros(10, dtype=np.uint8)])

    def test_an_unexpected_channel_count_is_rejected(self):
        with pytest.raises(ValueError, match="channels"):
            ClassicalEncoder().embed([np.zeros((8, 8, 5), dtype=np.uint8)])

    def test_a_zero_extent_image_is_rejected(self):
        with pytest.raises(ValueError, match="zero extent"):
            ClassicalEncoder().embed([np.zeros((0, 8, 3), dtype=np.uint8)])

    def test_a_wrong_length_descriptor_is_caught(self, monkeypatch):
        # The guard exists because `dim` is fixed at import from one probe image. Point the
        # encoder at a palette function that disagrees with that probe and it must say so,
        # rather than raise a broadcast error from deep inside numpy or silently truncate.
        monkeypatch.setattr(
            "moodboard.encoders.axes.palette_feature_vector", lambda image: np.zeros(3)
        )
        with pytest.raises(RuntimeError, match="fixed at"):
            ClassicalEncoder().embed([noise_image(20)])


class TestTheAxisWeightingThisConstructionProduces:
    """Characterisation, not aspiration.

    INTERFACES.md pins the raw concatenation with a single L2 pass over the whole vector and
    states that the parts are not normalised individually. The three descriptors are on very
    different scales, so that construction leaves the composition block numerically negligible.
    This is recorded in `ClassicalEncoder`'s docstring and pinned here so that changing the
    construction breaks a test and forces both the docstring and the encoder revision to move
    with it.
    """

    @staticmethod
    def _energy_shares(image: np.ndarray) -> tuple[float, float, float]:
        row = ClassicalEncoder().embed([image]).astype(np.float64)[0]
        squared = row**2
        palette_end = EXPECTED_PALETTE_LENGTH
        tone_end = palette_end + EXPECTED_TONE_LENGTH
        return (
            float(squared[:palette_end].sum()),
            float(squared[palette_end:tone_end].sum()),
            float(squared[tone_end:].sum()),
        )

    @pytest.mark.parametrize("seed", [17, 18, 19])
    def test_composition_holds_a_negligible_share_of_the_energy(self, seed):
        _, _, composition = self._energy_shares(noise_image(seed))
        assert composition < 1e-6

    def test_palette_and_tone_hold_essentially_all_of_it(self):
        palette, tone, _ = self._energy_shares(gradient_image())
        assert palette + tone > 1.0 - 1e-6

    def test_the_palette_to_tone_balance_shifts_with_the_input_shape(self):
        # The palette histogram totals the pixel count of the downsampled image, which depends
        # on the source shape; the tone histogram always totals the fixed canonical grid.
        square_palette, square_tone, _ = self._energy_shares(flat_image((120, 60, 30), side=64))
        wide = np.tile(np.array([120, 60, 30], dtype=np.uint8), (16, 256, 1))
        wide_palette, wide_tone, _ = self._energy_shares(wide)
        assert not np.isclose(square_palette / square_tone, wide_palette / wide_tone, rtol=1e-3)

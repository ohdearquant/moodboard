"""Properties of the encoder boundary and of the one encoder that needs no weights.

Every fixture here is a synthetic array. No dataset download, no network access, and no
learned checkpoint: the suite reaches nothing outside this repository.

The properties tested are the ones INTERFACES.md states as the Protocol's contract: the return
shape and dtype, L2-normalised rows, input order preserved, determinism, and no mutation of the
caller's arrays. Two further tests pin numbers that are not free parameters. The first pins the
three feature lengths, so a change to `axes.py`'s bin counts fails here rather than silently
changing every embedding the encoder produces. The second characterises the axis
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
        """The revision moved to "2" with the per-block normalisation, and this test failing on
        that change is the revision field working rather than a test needing maintenance.

        Revision 1 concatenated the three descriptors raw, which weighted them by whatever
        magnitudes `axes.py` happened to produce: measured over a pool of 250, tone carried
        92.0% of the vector's energy, palette 8.0% and composition 0.0%. Every distance the
        engine computed under revision 1 was therefore a tone distance to three significant
        figures, and any board built or scored under it is not comparable to one built under
        revision 2. That is what a revision string is for, so it is bumped rather than the
        change being made quietly under the old one.
        """
        encoder = ClassicalEncoder()
        assert encoder.name == "classical-v1"
        assert encoder.revision == "2"

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


# The floor the moved-square probe must clear. Chosen from the two measurements it sits
# between rather than picked for roundness: revision 1 scored 5.96e-08 on this pair, revision 2
# scores 0.03979, and 0.01 is comfortably under the working value while remaining five orders
# of magnitude above the broken one. A floor set just under 0.03979 would fail on ordinary
# numerical drift; a floor near 1e-07 would pass the defect it exists to catch.
MIN_COMPOSITION_ONLY_EMBEDDING_MOVEMENT = 0.01


class TestEachAxisCarriesEqualWeightInTheEmbedding:
    """The construction INTERFACES.md revision 2 pins: each descriptor normalised to unit
    length, then concatenated, then the whole normalised once.

    These were characterisation tests of a defect. Revision 1 concatenated the parts raw and
    normalised only the whole, which left the composition block at a share of squared energy
    of order 1e-9, below float32 resolution in a dot product. They asserted that share stayed
    negligible. The construction was ruled changed, so they now assert what replaced it.
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
    def test_every_axis_holds_one_third_of_the_squared_energy(self, seed):
        shares = self._energy_shares(noise_image(seed))
        for share in shares:
            assert share == pytest.approx(1.0 / 3.0, abs=1e-6)

    def test_composition_is_no_longer_negligible(self):
        """The specific failure revision 2 exists to fix, asserted as its own arm.

        Stated separately from the one-third test because it is the claim a reader cares about
        and because it fails for a reason the reader can act on: under the old construction this
        number was 1e-9 and a composition-sensitive result read out of this encoder was noise.
        """
        _, _, composition = self._energy_shares(gradient_image())
        assert composition > 0.3

    def test_a_composition_only_change_moves_the_embedding(self):
        """The decisive probe, kept permanently and required to pass.

        Two images with identical pixel content, a bright square moved corner to corner, so
        only composition differs. `axes.composition_distance` calls them 0.2318 apart. Under
        revision 1 their embeddings were 5.96e-08 apart, below float32 epsilon: the axis was
        present in the vector and absent from the answer. An energy-share assertion alone would
        not have caught that, because a block can hold a third of the energy and still be
        dominated by whatever it shares the vector with; this measures the thing that matters,
        which is whether the encoder's own distances can see the axis at all.
        """
        side = 128
        corner = np.zeros((side, side, 3), dtype=np.uint8)
        corner[:20, :20] = 255
        opposite = np.zeros((side, side, 3), dtype=np.uint8)
        opposite[-20:, -20:] = 255

        assert corner.sum() == opposite.sum(), (
            "the two probe images must carry identical pixel mass, or this measures a "
            "brightness change rather than a composition change"
        )
        assert axes.composition_distance(corner, opposite) > 0.1, (
            "the composition axis itself must see this pair, or the probe says nothing about "
            "the encoder"
        )

        first, second = ClassicalEncoder().embed([corner, opposite])
        movement = 1.0 - float(np.dot(first.astype(np.float64), second.astype(np.float64)))
        assert movement > MIN_COMPOSITION_ONLY_EMBEDDING_MOVEMENT, (
            f"a composition-only change moved the embedding by {movement:.3e}, under the "
            f"{MIN_COMPOSITION_ONLY_EMBEDDING_MOVEMENT} floor; revision 1 scored 5.96e-08 here"
        )

    def test_the_palette_to_tone_balance_no_longer_shifts_with_the_input_shape(self):
        """The second consequence of the same scale gap, which the fix removes for free.

        The palette histogram totalled the pixel count of the downsampled image, which depends
        on source shape, while the tone histogram always totals the fixed canonical grid, so
        the balance between them used to move with aspect ratio. Neither raw total survives
        unit-normalisation. Kept as an arm because it is a distinct property from the energy
        shares and would be the first thing to break if per-block normalisation were applied to
        only some of the blocks.
        """
        square_palette, square_tone, _ = self._energy_shares(flat_image((120, 60, 30), side=64))
        wide = np.tile(np.array([120, 60, 30], dtype=np.uint8), (16, 256, 1))
        wide_palette, wide_tone, _ = self._energy_shares(wide)
        assert square_palette / square_tone == pytest.approx(wide_palette / wide_tone, rel=1e-6)


# A population wide enough that the normalisation property is tested as a property rather than
# on the handful of images the classes above happen to use. It mixes aspect ratios, dtypes,
# channel counts, degenerate extents and uniform colours, since the normalisation step divides
# by a norm and a descriptor that came out near zero is where that goes wrong.
def varied_encoder_population() -> list[np.ndarray]:
    return [noise_image(seed) for seed in range(40, 48)] + [
        gradient_image(),
        gradient_image(side=17),
        flat_image((0, 0, 0)),
        flat_image((255, 255, 255)),
        flat_image((7, 190, 120), side=5),
        np.array([[[3, 250, 40]]], dtype=np.uint8),
        noise_image(50, height=1, width=90),
        noise_image(51, height=90, width=1),
        noise_image(52, height=2, width=2),
        noise_image(53, height=220, width=9),
        noise_image(54, height=9, width=220),
        noise_image(55)[..., 0],
        noise_image(56)[..., :1],
        np.concatenate([noise_image(57), np.full((40, 50, 1), 128, dtype=np.uint8)], axis=-1),
        noise_image(58).astype(np.float64) / 255.0,
    ]


class TestEmbeddingsAreL2NormalisedAsAProperty:
    """The Protocol's central numeric promise, over a wide population rather than a few images.

    `conformal.py` reads cosine distance as one minus a dot product, which is only the cosine
    if every row is a unit vector. A row that is not normalised does not raise anywhere; it
    quietly changes every distance computed from it, so this is worth testing broadly.
    """

    def test_every_row_of_a_wide_population_is_a_unit_vector(self):
        # The bound is float32 epsilon rather than a hand-picked tolerance, because that is
        # what `embed`'s docstring promises: unit norm to within float32 resolution. Measured
        # across this population the largest deviation is about 2.6e-8, so the bound sits a
        # little over four times clear of the observation.
        #
        # What this bound deliberately does NOT do: it does not distinguish normalising in
        # float64 before the cast from normalising in float32 after it. That was checked by
        # mutation, and the float32-after variant reaches about 6.9e-8, which is still inside
        # float32 resolution and so still satisfies the contract. The float64-first ordering
        # buys a factor of roughly 2.7 on an already negligible error. Tightening this number
        # until it separated the two would be testing an implementation detail through a
        # tolerance tuned to one platform's arithmetic.
        population = varied_encoder_population()
        assert len(population) > 20
        rows = ClassicalEncoder().embed(population).astype(np.float64)
        norms = np.linalg.norm(rows, axis=1)
        assert np.isfinite(norms).all()
        assert np.abs(norms - 1.0).max() < float(np.finfo(np.float32).eps)

    def test_normalisation_holds_when_each_image_is_embedded_alone(self):
        # Batching must not be what makes the property true.
        for image in varied_encoder_population():
            row = ClassicalEncoder().embed([image]).astype(np.float64)[0]
            assert np.isclose(np.linalg.norm(row), 1.0, atol=1e-6)

    def test_no_row_carries_a_non_finite_component(self):
        rows = ClassicalEncoder().embed(varied_encoder_population())
        assert np.isfinite(rows).all()

    def test_scaling_an_image_up_does_not_change_its_direction_much(self):
        # A unit vector encodes direction only, so an image and a larger copy of the same
        # content should land close together rather than at different magnitudes.
        encoder = ClassicalEncoder()
        small = gradient_image(side=48)
        large = np.repeat(np.repeat(small, 2, axis=0), 2, axis=1)
        rows = encoder.embed([small, large]).astype(np.float64)
        assert float(rows[0] @ rows[1]) > 0.9


class TestWhatProtocolConformanceDoesAndDoesNotCheck:
    """`runtime_checkable` tests for the presence of members and for nothing else.

    Worth pinning because `isinstance(x, Encoder)` reads like validation. It is not: it cannot
    see an attribute's type and it cannot see `embed`'s signature or its return value. Anything
    that relies on those holding has to check them itself, and the tests below are that check
    for the one concrete encoder in this file.
    """

    def test_the_concrete_encoder_carries_the_declared_attribute_types(self):
        encoder = ClassicalEncoder()
        assert isinstance(encoder.name, str) and encoder.name
        assert isinstance(encoder.revision, str) and encoder.revision
        assert isinstance(encoder.dim, int) and encoder.dim > 0

    def test_embed_returns_the_width_dim_advertises(self):
        # `dim` is read by report.py for the representation block, so it has to be the width
        # the array actually has rather than an independently declared number.
        encoder = ClassicalEncoder()
        assert encoder.embed([noise_image(60), noise_image(61)]).shape[1] == encoder.dim

    def test_an_object_with_wrongly_typed_members_still_satisfies_the_protocol(self):
        # Characterisation of the boundary's real strength, so that a later reader does not
        # mistake the isinstance check above for a guarantee about types.
        class WronglyTyped:
            name = 42
            revision = None
            dim = "not an integer"

            def embed(self, images):
                return "not an array"

        assert isinstance(WronglyTyped(), Encoder)

    @pytest.mark.parametrize("missing", ["name", "revision", "dim", "embed"])
    def test_dropping_any_single_member_breaks_conformance(self, missing):
        # The must-not-match arm. Without it, a Protocol that accepted everything would pass
        # the positive check in TestTheProtocol and nothing would notice.
        members = {
            "name": "x",
            "revision": "1",
            "dim": 3,
            "embed": lambda self, images: np.zeros((len(images), 3), dtype=np.float32),
        }
        del members[missing]
        incomplete = type("Incomplete", (), members)
        assert not isinstance(incomplete(), Encoder)

"""Properties of the three classical style axes.

Every image here is a synthetic array built from a seeded generator. No dataset download, no
network access and no learned checkpoint: the suite reaches nothing outside this repository.

The central property is the one each axis is specified to have: it returns a scalar distance
in [0, 1] and it must be genuinely computed. "Genuinely computed" is not directly assertable, so
it is approached from four sides that together make a constant or a placeholder fail. Each axis
must produce a wide spread of distinct values over a varied population rather than a fixed
number. The three axes must not be strongly correlated with one another, since ADR-0003
criterion 4 warns that an axis responding to everything is one number printed three times under
different headings. Each axis must respond to a change in the property its own label names, and
must stay comparatively quiet under a change to a different axis's property. And each must
behave like a distance, returning zero against itself and the same answer in either argument
order.

Two scoping notes, because a green test here could otherwise be read as more than it is.

First, ADR-0003 criterion 4 registers its acceptance protocol in eval/thresholds.json as 200
images sampled across four PACS domains with a bootstrap over images. That measurement needs a
dataset this suite deliberately does not download, so it cannot run here and this file does not
claim to run it. What runs here is the same comparison on a small seeded synthetic population,
which is a much weaker instrument. It can catch an axis that is grossly mislabelled and it
cannot certify the criterion.

Second, the interventions below are applied in CIELAB so that each one moves exactly the
quantity it is named for. A recolour rotates the a and b channels with L held fixed, and a
luminance shift adds to L with a and b held fixed. Applying either by scaling sRGB channels
instead moves both quantities at once, which makes an axis look less specific than it is and
would attribute a defect in the intervention to the axis under test.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from skimage.color import lab2rgb, rgb2lab
from skimage.transform import resize

from moodboard import axes

THRESHOLDS_PATH = Path(__file__).resolve().parents[1] / "eval" / "thresholds.json"

AXIS_FUNCTIONS = {
    "palette": axes.palette_distance,
    "tone": axes.tone_distance,
    "composition": axes.composition_distance,
}

FEATURE_FUNCTIONS = {
    "palette": axes.palette_feature_vector,
    "tone": axes.tone_feature_vector,
    "composition": axes.composition_feature_vector,
}


@pytest.fixture(scope="session")
def axis_intervention_thresholds() -> dict:
    """The pre-registered constants, read from the registry at runtime.

    An acceptance-relevant number is never copied into Python source, because a second copy
    of a pre-registered number can drift from the registry without anything noticing.
    """
    with THRESHOLDS_PATH.open() as handle:
        return json.load(handle)["axis_intervention"]


# --- Synthetic image generators ------------------------------------------------------------
#
# All seeded. `lab_texture` is the workhorse: it builds a patch whose mean lightness and mean
# chroma are set independently, with enough variation that the image has several dominant
# colours. That matters because a single-colour image puts palette_distance into a degenerate
# regime, characterised at the end of this file.


def lab_texture(seed: int, lightness: float, a_value: float, b_value: float, side: int = 64):
    """A textured patch with a controlled mean CIELAB colour."""
    rng = np.random.default_rng(seed)
    lab = np.empty((side, side, 3))
    lab[..., 0] = np.clip(lightness + rng.normal(0.0, 12.0, (side, side)), 1.0, 99.0)
    lab[..., 1] = a_value + rng.normal(0.0, 6.0, (side, side))
    lab[..., 2] = b_value + rng.normal(0.0, 6.0, (side, side))
    return (np.clip(lab2rgb(lab), 0.0, 1.0) * 255).astype(np.uint8)


def flat(rgb: tuple[int, int, int], height: int = 48, width: int = 48):
    return np.tile(np.array(rgb, dtype=np.uint8), (height, width, 1))


def flat_lab(lightness: float, a_value: float, b_value: float, side: int = 48):
    lab = np.zeros((side, side, 3))
    lab[..., 0], lab[..., 1], lab[..., 2] = lightness, a_value, b_value
    return (np.clip(lab2rgb(lab), 0.0, 1.0) * 255).astype(np.uint8)


def noise(seed: int, height: int = 64, width: int = 64):
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)


def gradient(height: int = 64, width: int = 64, low: int = 0, high: int = 255):
    ramp = np.linspace(low, high, width, dtype=np.uint8)
    return np.repeat(np.tile(ramp[None, :], (height, 1))[..., None], 3, axis=-1)


def blob(
    seed: int,
    side: int = 64,
    centre_x: float = 0.5,
    centre_y: float = 0.5,
    radius: float = 0.2,
    background: tuple[int, int, int] = (30, 30, 40),
    foreground: tuple[int, int, int] = (230, 220, 200),
):
    """A disc of one colour on a field of another, with light seeded grain.

    This is the shape that gives the composition axis something to locate: saliency needs a
    region that stands out from its surroundings, and where that region sits in the frame is
    the thing composition is supposed to measure.
    """
    rows, columns = np.mgrid[0:side, 0:side]
    mask = ((rows / side - centre_y) ** 2 + (columns / side - centre_x) ** 2) <= radius**2
    image = np.tile(np.array(background, dtype=np.uint8), (side, side, 1))
    image[mask] = np.array(foreground, dtype=np.uint8)
    grain = np.random.default_rng(seed).integers(-8, 9, image.shape)
    return np.clip(image.astype(np.int16) + grain, 0, 255).astype(np.uint8)


def stripes(seed: int, side: int = 64, period: int = 8):
    _, columns = np.mgrid[0:side, 0:side]
    base = (((columns // period) % 2) * 200 + 30).astype(np.uint8)
    image = np.repeat(base[..., None], 3, axis=-1)
    grain = np.random.default_rng(seed).integers(-5, 6, image.shape)
    return np.clip(image.astype(np.int16) + grain, 0, 255).astype(np.uint8)


def varied_population() -> list[np.ndarray]:
    """A population spanning the kinds of variation the axes are meant to separate.

    It mixes placement (discs in several positions and sizes), spatial frequency (stripes at
    four periods), lightness and chroma (textures at controlled CIELAB means), tonal range
    (gradients, including one confined to the dark end), and two degenerate cases (flat
    colours). Both flat colours are kept deliberately, because an axis that silently fails on
    a uniform image should fail a test rather than be excluded from the population.
    """
    return (
        [blob(seed) for seed in range(4)]
        + [blob(seed, centre_x=0.2, centre_y=0.75, radius=0.3) for seed in range(4, 7)]
        + [blob(7, radius=0.42, foreground=(20, 40, 90), background=(200, 200, 190))]
        + [
            stripes(seed, period=period)
            for seed, period in zip(range(8, 12), (3, 5, 8, 12), strict=True)
        ]
        + [
            lab_texture(seed, lightness, a_value, b_value)
            for seed, (lightness, a_value, b_value) in enumerate(
                [(30, 0, 0), (60, 0, 0), (85, 0, 0), (55, 40, -20), (55, -35, 40), (40, 20, 45)],
                start=20,
            )
        ]
        + [gradient(), gradient(low=0, high=90), noise(30), flat((220, 30, 30)), flat((128,) * 3)]
    )


@pytest.fixture(scope="module")
def population() -> list[np.ndarray]:
    return varied_population()


@pytest.fixture(scope="module")
def pairwise_distances(population) -> dict[str, np.ndarray]:
    """Every axis evaluated on every unordered pair, computed once and shared.

    Module-scoped because this is the most expensive thing in the file and four tests read it.
    """
    values: dict[str, list[float]] = {axis: [] for axis in AXIS_FUNCTIONS}
    for i in range(len(population)):
        for j in range(i + 1, len(population)):
            for axis, function in AXIS_FUNCTIONS.items():
                values[axis].append(function(population[i], population[j]))
    return {axis: np.array(v, dtype=np.float64) for axis, v in values.items()}


# --- Interventions, each isolated in CIELAB -------------------------------------------------


def recolour(image: np.ndarray, magnitude: float) -> np.ndarray:
    """Rotate the chroma plane by `magnitude` half-turns, holding lightness exactly fixed."""
    lab = rgb2lab(image.astype(np.float64) / 255.0)
    angle = magnitude * np.pi
    a_channel, b_channel = lab[..., 1].copy(), lab[..., 2].copy()
    lab[..., 1] = a_channel * np.cos(angle) - b_channel * np.sin(angle)
    lab[..., 2] = a_channel * np.sin(angle) + b_channel * np.cos(angle)
    return (np.clip(lab2rgb(lab), 0.0, 1.0) * 255).astype(np.uint8)


def luminance_shift(image: np.ndarray, magnitude: float) -> np.ndarray:
    """Darken by `magnitude` of the full lightness range, holding chroma exactly fixed."""
    lab = rgb2lab(image.astype(np.float64) / 255.0)
    lab[..., 0] = np.clip(lab[..., 0] - magnitude * 40.0, 0.0, 100.0)
    return (np.clip(lab2rgb(lab), 0.0, 1.0) * 255).astype(np.uint8)


def crop(image: np.ndarray, magnitude: float) -> np.ndarray:
    """Take a centred sub-rectangle and resize it back, moving where things sit in the frame."""
    height, width = image.shape[:2]
    margin_y, margin_x = int(height * magnitude * 0.5), int(width * magnitude * 0.5)
    inner = image[margin_y : height - margin_y, margin_x : width - margin_x]
    if inner.size == 0:
        inner = image
    restored = resize(inner, (height, width), anti_aliasing=True, mode="reflect")
    return (np.clip(restored, 0.0, 1.0) * 255).astype(np.uint8)


INTERVENTIONS = {"recolour": recolour, "luminance_shift": luminance_shift, "crop": crop}
INTERVENTION_MAGNITUDES = (0.15, 0.30, 0.45)

INTERVENTION_SUBJECTS = [
    blob(11),
    blob(12, centre_x=0.3, centre_y=0.7, foreground=(200, 60, 60)),
    stripes(13),
    blob(15, radius=0.35, background=(200, 200, 190), foreground=(20, 40, 90)),
    stripes(16, period=5),
    blob(17, centre_x=0.75, centre_y=0.25, radius=0.15, foreground=(240, 200, 120)),
    blob(18, radius=0.45, background=(15, 15, 25), foreground=(180, 190, 210)),
    stripes(19, period=12),
]


@pytest.fixture(scope="module")
def normalised_intervention_movements() -> dict[str, dict[str, float]]:
    """Mean movement of each axis under each intervention, normalised as the registry states.

    eval/thresholds.json pins the normalisation: each axis's movement is divided by that
    axis's median absolute movement across all interventions, so a ratio compares like with
    like rather than raw units.
    """
    raw = {name: {axis: [] for axis in AXIS_FUNCTIONS} for name in INTERVENTIONS}
    for subject in INTERVENTION_SUBJECTS:
        for name, intervene in INTERVENTIONS.items():
            for magnitude in INTERVENTION_MAGNITUDES:
                changed = intervene(subject, magnitude)
                for axis, function in AXIS_FUNCTIONS.items():
                    raw[name][axis].append(abs(function(subject, changed)))

    medians = {
        axis: float(np.median([m for name in INTERVENTIONS for m in raw[name][axis]]))
        for axis in AXIS_FUNCTIONS
    }
    return {
        name: {
            axis: float(np.mean(raw[name][axis]) / medians[axis]) if medians[axis] > 0 else 0.0
            for axis in AXIS_FUNCTIONS
        }
        for name in INTERVENTIONS
    }


class TestEachAxisReturnsAScalarInTheUnitInterval:
    """Each axis returns a scalar distance in [0, 1]."""

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_every_pair_in_a_varied_population_lands_in_the_unit_interval(
        self, axis, pairwise_distances
    ):
        values = pairwise_distances[axis]
        assert values.size > 100, "the population must be large enough for this to mean something"
        assert np.isfinite(values).all()
        assert values.min() >= 0.0
        assert values.max() <= 1.0

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_the_return_is_a_plain_python_float(self, axis):
        value = AXIS_FUNCTIONS[axis](blob(40), stripes(41))
        assert isinstance(value, float)
        assert not isinstance(value, np.ndarray)


class TestEachAxisIsGenuinelyComputedRatherThanConstant:
    """A constant, a placeholder or a stub passes a bounds check and fails everything here."""

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_the_axis_takes_many_distinct_values(self, axis, pairwise_distances):
        values = pairwise_distances[axis]
        distinct = np.unique(np.round(values, 9)).size
        assert distinct > 0.8 * values.size, (
            f"{axis} produced {distinct} distinct values over {values.size} pairs, which is too "
            f"few for a genuinely computed quantity"
        )

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_the_axis_has_real_spread_rather_than_hovering_at_one_value(
        self, axis, pairwise_distances
    ):
        values = pairwise_distances[axis]
        assert values.std() > 0.02
        assert values.max() - values.min() > 0.1
        interquartile = np.percentile(values, 75) - np.percentile(values, 25)
        assert interquartile > 0.01

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_the_axis_separates_a_near_copy_from_an_unrelated_image(self, axis):
        # A weaker image pair would let a function that returns a fixed constant survive the
        # spread tests above by accident of the population. This one is direct: near copies
        # must score below unrelated images on every axis.
        base = blob(42)
        near_copy = blob(42, radius=0.205)
        unrelated = stripes(43, period=3)
        function = AXIS_FUNCTIONS[axis]
        assert function(base, near_copy) < function(base, unrelated)


class TestTheThreeAxesAreThreeNumbersAndNotOne:
    """ADR-0003 criterion 4's stated worry, read directly.

    "An axis that responds to every change is not a decomposition. It is one number printed
    three times under different headings." Perfect correlation between two axes over a varied
    population is what that failure would look like from here.
    """

    @pytest.mark.parametrize(
        ("first", "second"),
        [("palette", "tone"), ("palette", "composition"), ("tone", "composition")],
    )
    def test_no_two_axes_are_near_perfectly_correlated(self, first, second, pairwise_distances):
        correlation = abs(
            float(np.corrcoef(pairwise_distances[first], pairwise_distances[second])[0, 1])
        )
        assert correlation < 0.95, (
            f"{first} and {second} correlate at {correlation:.3f} over the population, which is "
            f"close enough to one that they may be the same measurement under two names"
        )

    def test_no_two_axes_agree_pair_for_pair(self, pairwise_distances):
        names = list(AXIS_FUNCTIONS)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                assert not np.allclose(
                    pairwise_distances[names[i]], pairwise_distances[names[j]], atol=1e-6
                )


class TestEachAxisBehavesLikeADistance:
    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_an_image_against_itself_is_zero(self, axis, population):
        function = AXIS_FUNCTIONS[axis]
        for image in population:
            assert function(image, image) == 0.0

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_argument_order_does_not_change_the_answer(self, axis, population):
        function = AXIS_FUNCTIONS[axis]
        subset = population[:8]
        for i in range(len(subset)):
            for j in range(i + 1, len(subset)):
                forward = function(subset[i], subset[j])
                backward = function(subset[j], subset[i])
                assert forward == pytest.approx(backward, abs=1e-9)

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_repeated_calls_give_bit_identical_answers(self, axis):
        # The palette axis clusters internally, so this is the property its fixed seed buys.
        function = AXIS_FUNCTIONS[axis]
        first, second = blob(44), noise(45)
        answers = {function(first, second) for _ in range(4)}
        assert len(answers) == 1

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_the_caller_arrays_are_not_mutated(self, axis):
        first, second = blob(46), stripes(47)
        before_first, before_second = first.copy(), second.copy()
        AXIS_FUNCTIONS[axis](first, second)
        assert np.array_equal(before_first, first)
        assert np.array_equal(before_second, second)


class TestEachAxisRespondsToThePropertyItsLabelNames:
    """The interventions are isolated in CIELAB, so each moves exactly one quantity.

    These are the tests that make the axis names mean something at this scale. They are
    per-axis and direct, which is a different and stronger instrument than the aggregate
    ratio in the class below, because each one names a single quantity and a single response.
    """

    @pytest.mark.parametrize("seed", [0, 1, 2, 3])
    @pytest.mark.parametrize("lightness", [35.0, 55.0, 75.0])
    def test_tone_barely_moves_when_only_chroma_changes(self, seed, lightness):
        base = lab_texture(seed, lightness, 0.0, 0.0)
        recoloured = lab_texture(seed, lightness, 45.0, -45.0)
        assert axes.tone_distance(base, recoloured) < 0.05
        # And the palette axis is the one that should have noticed.
        assert axes.palette_distance(base, recoloured) > 10 * axes.tone_distance(base, recoloured)

    @pytest.mark.parametrize("seed", [0, 1, 2, 3])
    def test_tone_moves_when_lightness_changes(self, seed):
        dark = lab_texture(seed, 35.0, 10.0, -10.0)
        light = lab_texture(seed, 75.0, 10.0, -10.0)
        assert axes.tone_distance(dark, light) > 0.10

    @pytest.mark.parametrize("seed", [0, 1, 2, 3])
    def test_palette_moves_further_for_a_larger_chroma_change(self, seed):
        base = lab_texture(seed, 55.0, 0.0, 0.0)
        slightly_shifted = lab_texture(seed, 55.0, 4.0, 4.0)
        far_shifted = lab_texture(seed, 55.0, 45.0, -45.0)
        assert axes.palette_distance(base, far_shifted) > axes.palette_distance(
            base, slightly_shifted
        )

    @pytest.mark.parametrize("seed", [0, 1, 2, 3])
    def test_composition_moves_when_the_salient_region_moves(self, seed):
        centred = blob(seed, centre_x=0.5, centre_y=0.5)
        offset = blob(seed, centre_x=0.18, centre_y=0.18)
        assert axes.composition_distance(centred, offset) > 0.05

    @pytest.mark.parametrize("seed", [0, 1, 2, 3])
    def test_composition_barely_moves_when_only_the_colours_change(self, seed):
        centred = blob(seed)
        lab = rgb2lab(centred / 255.0)
        lab[..., 1], lab[..., 2] = -lab[..., 1], -lab[..., 2]
        recoloured = (np.clip(lab2rgb(lab), 0.0, 1.0) * 255).astype(np.uint8)

        moved = axes.composition_distance(centred, blob(seed, centre_x=0.18, centre_y=0.18))
        assert axes.composition_distance(centred, recoloured) < moved


class TestAdr0003AxisInterventionOnSyntheticImages:
    """ADR-0003 criterion 4, run on synthetic images rather than on its registered dataset.

    The registry pins this measurement at 200 images sampled across four PACS domains with a
    bootstrap over images. That dataset is not downloaded by this suite, so what runs here is
    the same comparison on eight seeded synthetic subjects at three magnitudes. It is a much
    weaker instrument and it does not certify the criterion.

    **How much weaker was measured rather than assumed, and it decides what is gated here.**
    The ratio was computed on twelve independent sets of eight synthetic subjects. Two
    separable properties came apart:

    | intervention    | intended axis ranked first | ratio at or above 2.0 | ratio range   |
    |-----------------|----------------------------|-----------------------|---------------|
    | recolour        | 12 of 12                   | 10 of 12              | 1.45 to 4.58  |
    | luminance_shift | 9 of 12                    | 0 of 12               | 0.70 to 1.90  |
    | crop            | 12 of 12                   | 12 of 12              | 6.49 to 17.40 |

    So the ranking property is robust for recolour and crop, and the registered 2.0 margin is
    robust only for crop, where the nearest observation sits more than three times clear of
    the bar. The recolour margin straddles 2.0 depending on which subjects are drawn, which
    means a synthetic population cannot decide it. That row is therefore measured and reported
    and is deliberately not gated here, because a gate whose verdict turns on an arbitrary
    choice of subject is a coin flip wearing a threshold.

    If a future change makes one of these fail, the fix is to the axis or to the registry, and
    never to the subject list below. Choosing subjects until the number clears the bar is
    fitting to the test, which is what eval/thresholds.json's own preamble forbids.
    """

    @pytest.mark.parametrize("intervention", ["recolour", "crop"])
    def test_the_intended_axis_is_the_largest_responder(
        self, intervention, axis_intervention_thresholds, normalised_intervention_movements
    ):
        intended = axis_intervention_thresholds["expected_dominant_axis"][intervention]
        movement = normalised_intervention_movements[intervention]

        # The registry says a zero response from the intended axis is a failure and never a
        # ratio to be excluded, so it is asserted before anything is compared.
        assert movement[intended] > 0.0
        largest_unintended = max(value for axis, value in movement.items() if axis != intended)
        assert movement[intended] > largest_unintended

    def test_cropping_clears_the_registered_margin(
        self, axis_intervention_thresholds, normalised_intervention_movements
    ):
        # The only row with enough headroom on synthetic images to be worth gating: the
        # closest of twelve independent measurements was 6.49 against a bar of 2.0.
        intended = axis_intervention_thresholds["expected_dominant_axis"]["crop"]
        required = axis_intervention_thresholds["min_diagonal_to_offdiagonal_ratio"]
        movement = normalised_intervention_movements["crop"]

        assert movement[intended] > 0.0
        largest_unintended = max(value for axis, value in movement.items() if axis != intended)
        assert movement[intended] >= required * largest_unintended

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "THE CAUSE THIS MARKER ORIGINALLY NAMED IS FIXED, AND THE TEST STILL FAILS FOR A "
            "DIFFERENT ONE. It used to read that palette_distance clusters over the full "
            "CIELAB vector including L, so lightness moved the palette centroids and palette "
            "absorbed response belonging to tone. That was true and is no longer: the "
            "clustering now runs on the chroma plane per ADR-0003, and at magnitude 0.15 tone "
            "clears palette and composition by 6.99x against a required 2.0, with palette at "
            "0.0035. The axis behaves exactly as the record requires wherever the intervention "
            "is faithful. "
            "What fails now is the INTERVENTION, not the axis. luminance_shift's docstring "
            "claims it holds chroma exactly fixed; it does not. Darkening pushes colours out "
            "of the sRGB gamut and the lab2rgb round trip clips them, which moves chroma for "
            "real. Measured, fraction of pixels whose chroma moves more than 1.0 Lab unit: "
            "0.07% at magnitude 0.15, 9.91% at 0.30, 25.98% at 0.45, with a maximum chroma "
            "move of 12.61 at 0.45. So at the two larger magnitudes palette is correctly "
            "reporting a chroma change the test did not intend to make, and the ratio "
            "collapses to 0.52. A competing explanation, that dropping L shrank the earth "
            "mover normaliser and inflated palette distances, was tested and REFUTED: the "
            "normaliser is 120.3 chroma-only against 124.0 full-Lab on the same pair. "
            "Kept strict so the correction still has to remove this marker deliberately. "
            "Fixing it means making the intervention gamut-safe, which changes a "
            "pre-registered acceptance instrument and is therefore not a unilateral edit."
        ),
    )
    def test_a_luminance_shift_clears_the_registered_margin(
        self, axis_intervention_thresholds, normalised_intervention_movements
    ):
        intended = axis_intervention_thresholds["expected_dominant_axis"]["luminance_shift"]
        required = axis_intervention_thresholds["min_diagonal_to_offdiagonal_ratio"]
        movement = normalised_intervention_movements["luminance_shift"]

        assert movement[intended] > 0.0
        largest_unintended = max(value for axis, value in movement.items() if axis != intended)
        assert movement[intended] >= required * largest_unintended

    def test_the_vocabulary_this_file_drives_matches_the_registry(
        self, axis_intervention_thresholds
    ):
        # If the registry ever gains or loses an intervention row, this file stops covering it
        # silently. ADR-0003 is explicit that a texture row was removed on purpose.
        assert set(axis_intervention_thresholds["interventions"]) == set(INTERVENTIONS)
        assert set(axis_intervention_thresholds["expected_dominant_axis"].values()) == set(
            AXIS_FUNCTIONS
        )
        assert (
            len(INTERVENTION_MAGNITUDES)
            == axis_intervention_thresholds["protocol"]["magnitudes_per_intervention"]
        )


class TestPaletteDistanceSelfNormalisation:
    """Characterisation of a measured limitation, pinned so a fix breaks a test.

    palette_distance normalises its transport cost by the largest pairwise distance available
    in the comparison it is currently making. When both images reduce to a single dominant
    colour the cost matrix is one by one, so the normaliser is the only cost there is and the
    ratio is exactly 1.0 however close or far apart the two colours are.

    The consequence is that palette_distance is not comparable across image pairs, which is
    what its own docstring says it is for. These tests record the behaviour as it stands
    today. They are written to fail if it changes, so that a correction is a visible act.
    """

    @pytest.mark.parametrize(
        ("lightness", "a_value", "b_value", "description"),
        [
            (51.0, 0.0, 0.0, "one lightness unit away"),
            (55.0, 0.0, 0.0, "five lightness units away"),
            (70.0, 0.0, 0.0, "twenty lightness units away"),
            (50.0, 10.0, 0.0, "a small chroma move"),
            (20.0, -50.0, 50.0, "a very large move in every channel"),
        ],
    )
    def test_any_two_distinct_uniform_images_return_exactly_one(
        self, lightness, a_value, b_value, description
    ):
        reference = flat_lab(50.0, 0.0, 0.0)
        target = flat_lab(lightness, a_value, b_value)
        assert axes.palette_distance(reference, target) == 1.0, description

    def test_two_identical_uniform_images_still_return_zero(self):
        # The control for the test above. Saturation at 1.0 is specific to distinct colours,
        # so a function that simply returned 1.0 would fail here.
        reference = flat_lab(50.0, 0.0, 0.0)
        assert axes.palette_distance(reference, flat_lab(50.0, 0.0, 0.0)) == 0.0

    def test_images_with_several_dominant_colours_do_not_saturate(self):
        # The must-not-match arm: the limitation is specific to the single-cluster regime, and
        # this test says so. Without it, a palette_distance that always returned 1.0 would
        # pass the class above completely.
        base = lab_texture(50, 55.0, 0.0, 0.0)
        near = lab_texture(50, 55.0, 4.0, 4.0)
        far = lab_texture(50, 55.0, 45.0, -45.0)
        assert 0.0 < axes.palette_distance(base, near) < 1.0
        assert 0.0 < axes.palette_distance(base, far) < 1.0
        assert axes.palette_distance(base, near) < axes.palette_distance(base, far)

    def test_palette_responds_to_lightness_with_chroma_held_fixed(self):
        # The mechanism behind the expected failure in the intervention class above, stated on
        # its own so it is legible without reading that class. Chroma is identical in both
        # images and only lightness differs.
        dark = lab_texture(51, 35.0, 20.0, -30.0)
        light = lab_texture(51, 75.0, 20.0, -30.0)
        assert axes.palette_distance(dark, light) > 0.1


class TestAwkwardAndDegenerateInput:
    """Every one of these still has to produce a finite scalar in [0, 1].

    Boundary sizes are here because the axes resize internally and a one-pixel extent is where
    that goes wrong, and because the palette clustering asks for five clusters, which a
    four-pixel image cannot supply.
    """

    AWKWARD = {
        "all-zeros": np.zeros((32, 32, 3), dtype=np.uint8),
        "all-max": np.full((32, 32, 3), 255, dtype=np.uint8),
        "single-pixel": np.array([[[120, 30, 200]]], dtype=np.uint8),
        "one-row": noise(20, height=1, width=64),
        "one-column": noise(21, height=64, width=1),
        "two-by-two": noise(22, height=2, width=2),
        "fewer-pixels-than-clusters": noise(23, height=2, width=2),
        "grayscale-2d": noise(24)[..., 0],
        "single-channel-3d": noise(25)[..., :1],
        "rgba": np.concatenate([noise(26), np.full((64, 64, 1), 200, dtype=np.uint8)], axis=-1),
        "float-in-unit-range": noise(27).astype(np.float64) / 255.0,
        "float-outside-unit-range": noise(28).astype(np.float64) * 3.0 / 255.0 - 1.0,
        "very-tall": noise(29, height=200, width=8),
        "very-wide": noise(31, height=8, width=200),
    }

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    @pytest.mark.parametrize("label", list(AWKWARD))
    def test_a_finite_scalar_in_the_unit_interval_comes_back(self, axis, label):
        value = AXIS_FUNCTIONS[axis](blob(99), self.AWKWARD[label])
        assert isinstance(value, float)
        assert np.isfinite(value)
        assert 0.0 <= value <= 1.0

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_mismatched_shapes_are_accepted(self, axis):
        # Nothing in the interface says the two images share a shape, and a moodboard holds
        # whatever the user put in it.
        value = AXIS_FUNCTIONS[axis](
            noise(32, height=200, width=17), noise(33, height=17, width=200)
        )
        assert 0.0 <= value <= 1.0

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_uint8_and_its_float_equivalent_agree(self, axis):
        first, second = blob(34), stripes(35)
        function = AXIS_FUNCTIONS[axis]
        as_integers = function(first, second)
        as_floats = function(first.astype(np.float64) / 255.0, second.astype(np.float64) / 255.0)
        assert as_integers == pytest.approx(as_floats, abs=1e-9)

    @pytest.mark.parametrize("axis", list(AXIS_FUNCTIONS))
    def test_a_uniform_image_against_itself_is_still_zero(self, axis):
        uniform = flat((77, 77, 77))
        assert AXIS_FUNCTIONS[axis](uniform, uniform) == 0.0


class TestFeatureVectors:
    """The descriptors ClassicalEncoder concatenates.

    INTERFACES.md states that the encoder's dim is fixed once these three lengths are fixed,
    so a length that varies with the input would break the encoder for a subset of images
    rather than for all of them, which is the harder kind of failure to notice.
    """

    SHAPES = [
        ("square", noise(50, height=64, width=64)),
        ("tall", noise(51, height=200, width=17)),
        ("wide", noise(52, height=17, width=200)),
        ("tiny", noise(53, height=2, width=2)),
        ("single-pixel", np.array([[[10, 200, 90]]], dtype=np.uint8)),
        ("uniform", flat((40, 90, 160))),
        ("grayscale-2d", noise(54)[..., 0]),
        ("rgba", np.concatenate([noise(55), np.full((64, 64, 1), 7, dtype=np.uint8)], axis=-1)),
    ]

    @pytest.mark.parametrize("axis", list(FEATURE_FUNCTIONS))
    def test_the_length_does_not_vary_with_the_input(self, axis):
        function = FEATURE_FUNCTIONS[axis]
        lengths = {function(image).size for _, image in self.SHAPES}
        assert len(lengths) == 1, f"{axis} returned lengths {lengths} across input shapes"

    @pytest.mark.parametrize("axis", list(FEATURE_FUNCTIONS))
    @pytest.mark.parametrize("label", [label for label, _ in SHAPES])
    def test_every_component_is_finite_and_non_negative(self, axis, label):
        image = dict(self.SHAPES)[label]
        vector = FEATURE_FUNCTIONS[axis](image)
        assert vector.ndim == 1
        assert np.isfinite(vector).all()
        assert (vector >= 0.0).all()

    @pytest.mark.parametrize("axis", list(FEATURE_FUNCTIONS))
    @pytest.mark.parametrize("label", [label for label, _ in SHAPES])
    def test_the_descriptor_is_never_all_zero(self, axis, label):
        # An all-zero concatenation has no direction to normalise, which is the one input
        # ClassicalEncoder.embed cannot serve. No valid image may produce one.
        image = dict(self.SHAPES)[label]
        assert FEATURE_FUNCTIONS[axis](image).sum() > 0.0

    @pytest.mark.parametrize("axis", list(FEATURE_FUNCTIONS))
    def test_the_descriptor_is_deterministic(self, axis):
        image = noise(56)
        function = FEATURE_FUNCTIONS[axis]
        assert np.array_equal(function(image), function(image))

    @pytest.mark.parametrize("axis", list(FEATURE_FUNCTIONS))
    def test_distinct_images_give_distinct_descriptors(self, axis):
        function = FEATURE_FUNCTIONS[axis]
        first = function(blob(57, foreground=(240, 30, 30)))
        second = function(stripes(58, period=3))
        assert not np.array_equal(first, second)

    def test_the_tone_descriptor_totals_a_fixed_count_whatever_the_input_shape(self):
        # Both tone histograms are read off a fixed canonical grid, so their total is the same
        # for every image. The palette histogram below does not share this property, and the
        # difference is what makes the two blocks scale differently inside the encoder.
        totals = {float(axes.tone_feature_vector(image).sum()) for _, image in self.SHAPES}
        assert len(totals) == 1

    def test_the_palette_descriptor_total_tracks_the_downsampled_pixel_count(self):
        # Recorded because it is the reason the palette and tone blocks of an embedding are on
        # different scales, and that scale relationship shifts with the input aspect ratio.
        square = float(axes.palette_feature_vector(noise(59, height=64, width=64)).sum())
        wide = float(axes.palette_feature_vector(noise(60, height=8, width=200)).sum())
        assert square == pytest.approx(64 * 64)
        assert wide != square

    def test_the_composition_descriptor_is_a_distribution_plus_one_ratio(self):
        # A coarse grid of saliency mass normalised to sum to one, with the negative-space
        # ratio appended. So the total sits between one and two for every image, which is what
        # keeps this block negligible against the two histogram blocks in the concatenation.
        for _, image in self.SHAPES:
            vector = axes.composition_feature_vector(image)
            assert 1.0 <= vector.sum() <= 2.0
            assert 0.0 <= vector[-1] <= 1.0

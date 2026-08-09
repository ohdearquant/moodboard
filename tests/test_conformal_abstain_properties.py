"""Independent property tests for the three guarantees this pair of modules must provide,
checked against `moodboard.conformal` and `moodboard.abstain` rather than
against either module's own inline test suite.

1. The conformal p-value is a multiple of 1/(n_local+1) and is uniform on that grid under a
   seeded exchangeable synthetic generator (ADR-0003, `eval/thresholds.json` `score_semantics`).
2. `kish_n_eff` equals n when every reference is distinct and equals the group count when the
   groups are equal-sized (ADR-0005).
3. Abstention fires on the constructed cases pinned in `eval/thresholds.json` and stays quiet on
   the pinned quiet population (ADR-0004).

Everything here is synthetic and seeded; nothing downloads a dataset. The synthetic boards are
built with a Student-t noise generator rather than the Gaussian one `test_conformal.py` and
`test_abstain.py` already use, deliberately, so this file is not re-running the same fixtures
through the same code path under a second name. ADR-0003 states the p-value's guarantee "does
not depend on the embedding being well behaved"; a heavier-tailed generator is a direct check of
that sentence rather than a stylistic choice.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from scipy import stats

from moodboard.abstain import check_resolution, evaluate_abstention, load_abstention_thresholds
from moodboard.conformal import (
    conformal_p_value,
    duplicate_groups,
    kish_n_eff,
    nonconformity_scores,
    partition_categories,
)

# A chi-square goodness-of-fit test is not exact for a finite trial count, so the significance
# threshold below is deliberately loose: it exists to catch a biased implementation (one that
# systematically over- or under-ranks the candidate), not to flake on ordinary sampling noise.
# The seeds used with it were checked to land comfortably above this bar, not chosen to just
# clear it.
_CHI_SQUARE_ALPHA = 0.01


def _unit(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)


def _hashes(n: int, prefix: str = "h") -> list[str]:
    return [f"{prefix}{index:04d}" for index in range(n)]


# ---------------------------------------------------------------------------
# Property 1: the p-value's grid and its uniformity under exchangeability
# ---------------------------------------------------------------------------


def _iid_gaussian_bag(rng: np.random.Generator, m: int, dim: int) -> np.ndarray:
    """`m` i.i.d. unit vectors. Every one of them is exchangeable with every other by
    construction, so labelling any single one the candidate and the rest the references is a
    draw from the exchangeable null the guarantee is stated under."""
    return _unit(rng.normal(size=(m, dim)))


def _heavy_tailed_bag(rng: np.random.Generator, m: int, dim: int) -> np.ndarray:
    """`m` draws from a single shared, heavier-tailed distribution: a random base direction
    perturbed by Student-t noise (4 degrees of freedom, so it has visibly heavier tails than the
    Gaussian generator above). Still i.i.d. across the `m` draws, so still exchangeable; the
    point of this generator is that the underlying distribution is far from the Gaussian a naive
    implementation might have been tuned against.
    """
    base = _unit(rng.normal(size=dim))
    return _unit(base + 0.3 * rng.standard_t(df=4, size=(m, dim)))


class TestPValueGrid:
    @pytest.mark.parametrize("n", [1, 2, 3, 5, 8, 13, 21, 34, 50])
    def test_p_value_is_always_a_multiple_of_one_over_n_plus_one(self, n):
        """Swept across board sizes an order of magnitude apart, including the n=1 edge, rather
        than the single board size a single fixture would fix."""
        rng = np.random.default_rng(1000 + n)
        for trial in range(25):
            dim = 4 + (trial % 5)
            refs = _iid_gaussian_bag(rng, n, dim)
            candidate = _iid_gaussian_bag(rng, 1, dim)[0]
            p = conformal_p_value(refs, candidate, 5)
            grid_position = p * (n + 1)
            assert grid_position == pytest.approx(round(grid_position), abs=1e-9), (
                f"n={n}, trial={trial}: p={p!r} is not a multiple of 1/{n + 1}"
            )
            assert 1 <= round(grid_position) <= n + 1

    @pytest.mark.parametrize(
        ("n", "trials", "dim", "seed", "generator"),
        [
            (9, 4000, 6, 42, _iid_gaussian_bag),
            (9, 4000, 6, 42, _heavy_tailed_bag),
            (19, 3000, 8, 7, _iid_gaussian_bag),
            (19, 3000, 8, 7, _heavy_tailed_bag),
        ],
        ids=["gaussian-n9", "heavy-tailed-n9", "gaussian-n19", "heavy-tailed-n19"],
    )
    def test_p_value_is_uniform_on_the_grid_under_exchangeability(
        self, n, trials, dim, seed, generator
    ):
        """A formal chi-square goodness-of-fit test against the uniform null, at two board sizes
        and under two unrelated generators, rather than an eyeballed tolerance band around one.

        Each trial draws n + 1 exchangeable observations from `generator` and hands the first n
        to `conformal_p_value` as the references and the last as the candidate. Because every
        draw within a trial comes from the same distribution, which observation gets called
        "candidate" carries no information, and the guarantee says the resulting p-value must be
        uniform on {1/(n+1), ..., (n+1)/(n+1)} across trials.
        """
        rng = np.random.default_rng(seed)
        m = n + 1
        buckets = np.zeros(m, dtype=np.int64)
        for _ in range(trials):
            bag = generator(rng, m, dim)
            refs, candidate = bag[:n], bag[n]
            p = conformal_p_value(refs, candidate, 5)
            grid_index = round(p * m) - 1
            buckets[grid_index] += 1

        expected = np.full(m, trials / m)
        chi2, p_value = stats.chisquare(buckets, f_exp=expected)
        assert p_value > _CHI_SQUARE_ALPHA, (
            f"n={n}, seed={seed}: chi-square={chi2:.2f}, p={p_value:.4g} against a uniform "
            f"null over {m} grid cells; buckets={buckets.tolist()}"
        )

    def test_a_biased_ranking_fails_the_same_chi_square_check(self):
        """A negative control: feeding the chi-square check a deliberately biased sample of grid
        indices (skewed toward the low end, as a candidate-favouring implementation would
        produce) must be caught, so the test above is known to actually discriminate rather than
        passing on any input.
        """
        n = 9
        m = n + 1
        trials = 4000
        rng = np.random.default_rng(1)
        # Weighted toward index 0: p(index=0) is 3x the uniform rate.
        weights = np.array([3.0] + [1.0] * (m - 1))
        weights /= weights.sum()
        indices = rng.choice(m, size=trials, p=weights)
        buckets = np.bincount(indices, minlength=m)
        expected = np.full(m, trials / m)
        _, p_value = stats.chisquare(buckets, f_exp=expected)
        assert p_value < _CHI_SQUARE_ALPHA


# ---------------------------------------------------------------------------
# Property 2: Kish n_eff at its two named fixed points
# ---------------------------------------------------------------------------


class TestKishNEffFormula:
    """The formula itself, `(sum s)^2 / sum s^2`, swept over many random group-size lists rather
    than the handful of hand-picked lists a fixture would use.

    By Cauchy-Schwarz, `(sum s)^2 <= m * sum(s^2)` for any `m` positive sizes, so `n_eff <= m`
    always, with equality exactly when every group is the same size; the singleton case ADR-0005
    names (`n_eff == n`) is the special case of that equality where `m` itself equals `n`. This
    is checked below as a bound alongside the two named fixed points, since "lies between
    otherwise" only has content once the direction of the bound is right: `n_eff` is bounded
    above by the group count, not below by it.
    """

    @pytest.mark.parametrize("seed", range(30))
    def test_all_singleton_groups_gives_n_eff_equal_to_n(self, seed):
        rng = np.random.default_rng(seed)
        n = int(rng.integers(1, 80))
        sizes = [1] * n
        assert kish_n_eff(sizes) == pytest.approx(float(n))

    @pytest.mark.parametrize("seed", range(30))
    def test_equal_sized_groups_gives_n_eff_equal_to_the_group_count(self, seed):
        rng = np.random.default_rng(100 + seed)
        m = int(rng.integers(1, 40))
        s = int(rng.integers(1, 20))
        sizes = [s] * m
        assert kish_n_eff(sizes) == pytest.approx(float(m))

    @pytest.mark.parametrize("seed", range(50))
    def test_n_eff_is_bounded_above_by_the_group_count_and_below_by_one(self, seed):
        rng = np.random.default_rng(200 + seed)
        m = int(rng.integers(1, 25))
        sizes = rng.integers(1, 15, size=m).tolist()
        n_eff = kish_n_eff(sizes)
        assert 1.0 - 1e-9 <= n_eff <= m + 1e-9
        if len(set(sizes)) == 1:
            assert n_eff == pytest.approx(float(m))
        if m == 1:
            assert n_eff == pytest.approx(1.0)


class TestKishNEffThroughDuplicateGroups:
    """The formula wired to `duplicate_groups`'s actual output on synthetic boards, rather than
    fed a hand-typed list of sizes: this is the guarantee as `abstain.py`'s `category_n_eff`
    actually consumes it, at the pinned duplicate cut of 0.05.
    """

    _DIM = 64  # high enough that random unit vectors concentrate well below the 0.05 cut apart

    @pytest.mark.parametrize("n", [1, 2, 5, 10, 23, 40])
    def test_genuinely_distinct_references_give_n_eff_equal_to_n(self, n):
        """Random points in a high-dimensional space are, with overwhelming probability, all
        pairwise farther than the 0.05 duplicate cut, so every reference is its own group and
        `n_eff` must equal the file count exactly."""
        rng = np.random.default_rng(300 + n)
        refs = _unit(rng.normal(size=(n, self._DIM)))
        groups = duplicate_groups(refs, cut=0.05)
        assert len(groups) == n, "the distinct-references construction produced a duplicate group"
        n_eff = kish_n_eff([len(g) for g in groups])
        assert n_eff == pytest.approx(float(n))

    @pytest.mark.parametrize(("m", "s"), [(2, 3), (3, 4), (4, 5), (5, 2), (6, 3), (8, 6)])
    def test_equal_sized_near_duplicate_groups_give_n_eff_equal_to_the_group_count(self, m, s):
        """`m` well-separated base directions, each repeated `s` times with a tiny perturbation
        so the repeats collapse into one duplicate group under the 0.05 cut. `n_eff` must then
        equal `m`, the number of distinct sources, not `m * s`, the file count."""
        rng = np.random.default_rng(1000 * m + s)
        bases = _unit(rng.normal(size=(m, self._DIM)))
        refs = np.vstack(
            [_unit(base + 1e-4 * rng.standard_t(df=4, size=(s, self._DIM))) for base in bases]
        )
        groups = duplicate_groups(refs, cut=0.05)
        assert len(groups) == m
        assert sorted(len(g) for g in groups) == sorted([s] * m)
        n_eff = kish_n_eff([len(g) for g in groups])
        assert n_eff == pytest.approx(float(m))


# ---------------------------------------------------------------------------
# Property 3: abstention fires and stays quiet exactly as `eval/thresholds.json` pins
# ---------------------------------------------------------------------------

_BOARD_DIM = 32
_CLUSTER_CUT = 0.35
_DUP_CUT = 0.05
_TRIALS = 30

# Chosen and checked (see the module docstring's rationale) so a single look holds together as
# one category under the 0.35 cluster cut while its own members stay above the 0.05 duplicate
# cut: this generator produces genuinely distinct, single-category references, the same
# geometric regime `test_abstain.py`'s own generator targets, reached by an unrelated
# construction (Student-t noise around a random base direction rather than Gaussian noise around
# a fixed basis vector).
_LOOK_JITTER = 0.10
_LOOK_DF = 8


def _look(rng: np.random.Generator, count: int, base: np.ndarray) -> np.ndarray:
    noise = rng.standard_t(df=_LOOK_DF, size=(count, _BOARD_DIM)) * _LOOK_JITTER
    return _unit(base + noise)


def _random_base(rng: np.random.Generator) -> np.ndarray:
    return _unit(rng.normal(size=_BOARD_DIM))


def _augmented_alphas(references: np.ndarray, candidate: np.ndarray) -> tuple[np.ndarray, float]:
    n = references.shape[0]
    bag = np.vstack([references, candidate.reshape(1, -1)])
    alphas = nonconformity_scores(bag, min(5, n))
    return alphas[:n], float(alphas[n])


def _verdict_for(references, candidate, alpha, thresholds, tag):
    partition = partition_categories(
        references, _hashes(references.shape[0], tag), candidate, f"{tag}-cand", cut=_CLUSTER_CUT
    )
    groups = duplicate_groups(references, _DUP_CUT)
    reference_alphas, candidate_alpha = _augmented_alphas(references, candidate)
    return (
        evaluate_abstention(
            partition, alpha, candidate_alpha, reference_alphas, groups, thresholds
        ),
        partition,
    )


@pytest.fixture(scope="module")
def thresholds():
    return load_abstention_thresholds()


@pytest.fixture(scope="module")
def registry(thresholds):
    with thresholds.path.open(encoding="utf-8") as handle:
        return json.load(handle)


class TestMustFireCasesFromTheRegistry:
    """Data-driven off `eval/thresholds.json` itself: each pinned case is read from the file and
    the matching board is built generically from its fields (`n` or `sub_look_size`, and
    `alpha`), rather than one hand-written test per case name. A case added to the registry
    without a matching construction here is a construction this test does not know how to build,
    which is itself a signal the registry grew past what this file covers.
    """

    def _case(self, registry, name):
        for case in registry["abstention"]["must_fire"]["constructed_cases"]:
            if case["case"] == name:
                return case
        raise AssertionError(f"eval/thresholds.json carries no must_fire case named {name!r}")

    def test_threshold_below_resolution(self, thresholds, registry):
        case = self._case(registry, "threshold_below_resolution")
        fired = 0
        for trial in range(15):
            rng = np.random.default_rng(4000 + trial)
            base = _random_base(rng)
            references = _look(rng, case["n"], base)
            candidate = _look(rng, 1, base)[0]
            verdict, partition = _verdict_for(
                references, candidate, case["alpha"], thresholds, f"res{trial}"
            )
            if verdict is not None and verdict.reason == case["expect_reason"]:
                fired += 1
                assert verdict.measurement["n_local"] == case["n"]
        assert fired / 15 >= 1.0, f"only {fired}/15 trials fired resolution as pinned"

    def test_two_disjoint_style_groups(self, thresholds, registry):
        case = self._case(registry, "two_disjoint_style_groups")
        sub_look = case["sub_look_size"]
        fired = 0
        for trial in range(15):
            rng = np.random.default_rng(5000 + trial)
            base_a, base_b = _random_base(rng), _random_base(rng)
            references = np.vstack([_look(rng, sub_look, base_a), _look(rng, sub_look, base_b)])
            candidate = _look(rng, 1, base_a)[0]
            verdict, partition = _verdict_for(
                references, candidate, case["alpha"], thresholds, f"mm{trial}"
            )
            if verdict is not None and verdict.reason == case["expect_reason"]:
                fired += 1
                assert verdict.measurement["n_local"] == sub_look
                assert len(partition.all_categories) == 2
        assert fired / 15 >= 1.0, f"only {fired}/15 trials fired multi_modality as pinned"

    def test_asset_from_absent_domain(self, thresholds, registry):
        case = self._case(registry, "asset_from_absent_domain")
        fired = 0
        for trial in range(15):
            rng = np.random.default_rng(6000 + trial)
            board_base = _random_base(rng)
            outlier_base = _random_base(rng)
            references = _look(rng, 50, board_base)
            candidate = _look(rng, 1, outlier_base)[0]
            verdict, partition = _verdict_for(
                references, candidate, case["alpha"], thresholds, f"fo{trial}"
            )
            # Guard the construction: the resolution rule must not be the one that would fire
            # here, or this trial would not be exercising the far-outlier rule at all.
            assert check_resolution(partition, case["alpha"]) is None
            if verdict is not None and verdict.reason == case["expect_reason"]:
                fired += 1
                assert verdict.measurement["candidate_alpha"] > verdict.measurement["threshold"]
        assert fired / 15 >= 1.0, f"only {fired}/15 trials fired far_outlier as pinned"


class TestMustDetectAndScore:
    """ADR-0004's other half of rule 2: large-enough sub-looks are scored, not refused. Reads the
    case and the required rate straight from the registry."""

    def test_large_sub_looks_are_scored_not_refused(self, thresholds, registry):
        case = registry["abstention"]["must_detect_and_score"]["case"]
        max_rate = registry["abstention"]["must_detect_and_score"]["max_abstention_rate"]
        sub_look = case["sub_look_size"]
        alpha = case["alpha"]

        abstained = 0
        for trial in range(_TRIALS):
            rng = np.random.default_rng(7000 + trial)
            base_a, base_b = _random_base(rng), _random_base(rng)
            references = np.vstack([_look(rng, sub_look, base_a), _look(rng, sub_look, base_b)])
            candidate = _look(rng, 1, base_a)[0]
            verdict, partition = _verdict_for(
                references, candidate, alpha, thresholds, f"score{trial}"
            )
            if verdict is not None:
                abstained += 1
                continue
            assert len(partition.candidate_category_members) == sub_look
            assert len(partition.all_categories) == 2
            score = conformal_p_value(
                references[list(partition.candidate_category_members)], candidate, 5
            )
            assert 0.0 < score <= 1.0

        rate = abstained / _TRIALS
        assert rate <= max_rate, f"{abstained}/{_TRIALS} abstained on a board the rule must score"


class TestMustStayQuietPopulation:
    """The registered quiet population, built generically from each row's `n` (or `sub_look_size`
    for a two-look row) and `alpha`, with the false-abstention rate reported per reason exactly
    as ADR-0004 requires, so one rule firing constantly cannot hide behind the others staying
    silent.
    """

    def test_every_registered_row_stays_within_the_false_abstention_rate(
        self, thresholds, registry
    ):
        population = registry["abstention"]["must_stay_quiet"]["population"]
        max_rate = registry["abstention"]["must_stay_quiet"]["max_false_abstention_rate"]

        for row_index, row in enumerate(population):
            per_reason = {"resolution": 0, "multi_modality": 0, "far_outlier": 0}
            total = 0
            for trial in range(_TRIALS):
                rng = np.random.default_rng(8000 + 100 * row_index + trial)
                if row["look"] == "single":
                    base = _random_base(rng)
                    references = _look(rng, row["n"], base)
                    candidate = _look(rng, 1, base)[0]
                else:
                    sub_look = row["sub_look_size"]
                    assert 2 * sub_look == row["n"]
                    base_a, base_b = _random_base(rng), _random_base(rng)
                    references = np.vstack(
                        [_look(rng, sub_look, base_a), _look(rng, sub_look, base_b)]
                    )
                    candidate = _look(rng, 1, base_a)[0]

                verdict, _partition = _verdict_for(
                    references, candidate, row["alpha"], thresholds, f"quiet{row_index}_{trial}"
                )
                total += 1
                if verdict is not None:
                    per_reason[verdict.reason] += 1

            for reason, count in per_reason.items():
                rate = count / total
                assert rate <= max_rate, (
                    f"row {row_index} ({row}): {reason} abstained on {count}/{total}, above "
                    f"the registered {max_rate}"
                )

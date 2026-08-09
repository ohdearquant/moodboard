"""Tests for `moodboard.conformal`: nonconformity, the symmetric full-conformal p-value, the
augmented-bag category partition (ADR-0004 rule 2), near-duplicate grouping and Kish n_eff
(ADR-0005), and the loo-jackknife-plus interval (ADR-0002).

All fixtures are synthetic, seeded numpy arrays. No dataset download, no real encoder.
"""

import numpy as np
import pytest

from moodboard.conformal import (
    conformal_p_value,
    duplicate_groups,
    kish_n_eff,
    loo_jackknife_plus_interval,
    nonconformity_scores,
    paired_score_difference_interval,
    partition_categories,
)


def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / norms


def _iid_embeddings(n: int, dim: int = 6, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return _unit(rng.normal(size=(n, dim)).astype(np.float64))


def _clustered_embeddings(
    center: np.ndarray, n: int, noise: float = 0.05, seed: int = 0
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    dim = center.shape[0]
    points = center + noise * rng.normal(size=(n, dim))
    return _unit(points)


def _orthogonal_centers(k: int, dim: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    q, _ = np.linalg.qr(rng.normal(size=(dim, dim)))
    return q[:k]


def _hashes(n: int, prefix: str = "ref") -> list[str]:
    return [f"{prefix}-{i:03d}" for i in range(n)]


class TestNonconformityScores:
    def test_shape_and_range(self):
        embeddings = _iid_embeddings(8)
        scores = nonconformity_scores(embeddings, k=3)
        assert scores.shape == (8,)
        assert np.all(scores >= 0.0)

    def test_rejects_k_out_of_bounds(self):
        embeddings = _iid_embeddings(5)
        with pytest.raises(ValueError):
            nonconformity_scores(embeddings, k=0)
        with pytest.raises(ValueError):
            nonconformity_scores(embeddings, k=5)  # n - 1 == 4

    def test_deterministic(self):
        embeddings = _iid_embeddings(10, seed=3)
        a = nonconformity_scores(embeddings, k=4)
        b = nonconformity_scores(embeddings, k=4)
        np.testing.assert_array_equal(a, b)

    def test_ties_broken_by_ascending_index(self):
        # Four unit vectors in 2D: 0 degrees, 90, 180, 270. Row 0's cosine distance to row 1
        # (90deg, sim=0) and row 3 (270deg, sim=0) is exactly tied at 1.0, and strictly nearer
        # than row 2 (180deg, sim=-1, distance 2.0). At k=1 the tie must resolve to the lower
        # index, row 1, so row 0's score is exactly that single distance.
        embeddings = np.array(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [-1.0, 0.0],
                [0.0, -1.0],
            ]
        )
        scores = nonconformity_scores(embeddings, k=1)
        assert scores[0] == pytest.approx(1.0)
        # At k=2 both tied neighbours are included, still averaging to 1.0.
        scores_k2 = nonconformity_scores(embeddings, k=2)
        assert scores_k2[0] == pytest.approx(1.0)


class TestConformalPValue:
    def test_rejects_empty_reference_set(self):
        with pytest.raises(ValueError):
            conformal_p_value(np.empty((0, 4)), np.zeros(4), 5)

    def test_single_reference(self):
        refs = _iid_embeddings(1, seed=1)
        candidate = _iid_embeddings(1, seed=2)[0]
        p = conformal_p_value(refs, candidate, 5)
        assert p in (0.5, 1.0)

    def test_p_value_is_multiple_of_grid(self):
        rng = np.random.default_rng(7)
        for _ in range(50):
            n = rng.integers(2, 30)
            refs = _iid_embeddings(n, dim=5, seed=int(rng.integers(0, 1_000_000)))
            candidate = _iid_embeddings(1, dim=5, seed=int(rng.integers(0, 1_000_000)))[0]
            p = conformal_p_value(refs, candidate, 5)
            grid_position = p * (n + 1)
            assert grid_position == pytest.approx(round(grid_position), abs=1e-9)
            assert 1 <= round(grid_position) <= n + 1

    def test_candidate_identical_to_a_reference_is_never_the_most_unusual(self):
        refs = _iid_embeddings(12, seed=11)
        candidate = refs[0].copy()
        p = conformal_p_value(refs, candidate, 5)
        # An exact duplicate of a reference is at least as unusual as that reference (distance
        # 0 to itself as a neighbour candidate), so it cannot land at the extreme high-alpha
        # end reserved for genuine outliers; the p-value must clear the finest grid value.
        assert p > 1 / (len(refs) + 1)

    def test_the_caller_supplied_k_changes_the_score_when_the_geometry_requires_it(self):
        rng = np.random.default_rng(20260808)
        for _ in range(5):
            bag = _unit(rng.normal(size=(11, 6)))

        score_at_four = conformal_p_value(bag[:10], bag[10], 4)
        score_at_five = conformal_p_value(bag[:10], bag[10], 5)

        assert score_at_four == pytest.approx(2.0 / 11.0)
        assert score_at_five == pytest.approx(3.0 / 11.0)
        assert score_at_four != score_at_five

    def test_uniform_under_exchangeability(self):
        # Under exchangeability of the references and the candidate, the symmetric full
        # conformal p-value is uniform on {1/(n+1), ..., (n+1)/(n+1)}. Draw both from the same
        # i.i.d. distribution many times and check the empirical histogram over the n+1 = 10
        # grid cells is close to flat. Seeded, so this is deterministic, not flaky.
        n = 9
        dim = 6
        trials = 4000
        rng = np.random.default_rng(42)
        buckets = np.zeros(n + 1, dtype=np.int64)

        for _ in range(trials):
            bag = _unit(rng.normal(size=(n + 1, dim)))
            refs, candidate = bag[:n], bag[n]
            p = conformal_p_value(refs, candidate, 5)
            grid_index = round(p * (n + 1)) - 1
            buckets[grid_index] += 1

        expected = trials / (n + 1)
        # 8 standard deviations under a binomial(trials, 1/(n+1)) null: generous enough to
        # never flake on a correct implementation, tight enough to catch a badly biased one
        # (e.g. one that always ranks the candidate as the most or least unusual member).
        tolerance = 8 * np.sqrt(trials * (1 / (n + 1)) * (1 - 1 / (n + 1)))
        assert np.all(np.abs(buckets - expected) < tolerance), buckets


class TestPartitionCategories:
    def test_two_well_separated_clusters_partition_apart(self):
        centers = _orthogonal_centers(2, dim=16, seed=0)
        cluster_a = _clustered_embeddings(centers[0], 6, noise=0.03, seed=1)
        cluster_b = _clustered_embeddings(centers[1], 6, noise=0.03, seed=2)
        refs = np.vstack([cluster_a, cluster_b])
        hashes = _hashes(12)
        candidate = _clustered_embeddings(centers[0], 1, noise=0.03, seed=3)[0]

        partition = partition_categories(
            refs, hashes, candidate, "candidate-hash", cut=0.35, min_category_size=5
        )

        assert len(partition.all_categories) == 2
        assert set(partition.candidate_category_members) == set(range(6))
        assert partition.category_id in partition.all_categories

    def test_all_categories_never_contains_the_candidate_index(self):
        refs = _iid_embeddings(10, seed=5)
        hashes = _hashes(10)
        candidate = _iid_embeddings(1, seed=6)[0]
        partition = partition_categories(refs, hashes, candidate, "cand", cut=0.35)
        candidate_index = 10
        for members in partition.all_categories.values():
            assert candidate_index not in members

    def test_undersized_category_is_merged(self):
        # One tight cluster of 8 plus a single far outlier reference: the outlier alone is a
        # category of size 1, below the default min_category_size of 5, so it must be folded
        # into the nearest surviving category rather than left standing on its own.
        centers = _orthogonal_centers(2, dim=16, seed=10)
        main = _clustered_embeddings(centers[0], 8, noise=0.03, seed=11)
        outlier = _clustered_embeddings(centers[1], 1, noise=0.01, seed=12)
        refs = np.vstack([main, outlier])
        hashes = _hashes(9)
        candidate = _clustered_embeddings(centers[0], 1, noise=0.03, seed=13)[0]

        partition = partition_categories(refs, hashes, candidate, "cand", cut=0.35)

        assert len(partition.all_categories) == 1
        for members in partition.all_categories.values():
            assert len(members) >= 5

    def test_deterministic(self):
        refs = _iid_embeddings(14, seed=21)
        hashes = _hashes(14)
        candidate = _iid_embeddings(1, seed=22)[0]
        p1 = partition_categories(refs, hashes, candidate, "cand", cut=0.35)
        p2 = partition_categories(refs, hashes, candidate, "cand", cut=0.35)
        assert p1.category_id == p2.category_id
        assert p1.all_categories == p2.all_categories

    def test_rejects_mismatched_hash_count(self):
        refs = _iid_embeddings(5)
        candidate = _iid_embeddings(1)[0]
        with pytest.raises(ValueError):
            partition_categories(refs, _hashes(4), candidate, "cand", cut=0.35)


class TestDuplicateGroups:
    def test_covers_every_reference_exactly_once(self):
        refs = _iid_embeddings(15, seed=30)
        groups = duplicate_groups(refs, cut=0.05)
        flattened = sorted(i for group in groups for i in group)
        assert flattened == list(range(15))

    def test_near_duplicates_share_a_group(self):
        base = _iid_embeddings(1, seed=31)[0]
        near_dupes = _unit(base + 1e-4 * np.random.default_rng(32).normal(size=(3, base.shape[0])))
        distinct = _iid_embeddings(4, seed=33)
        refs = np.vstack([near_dupes, distinct])
        groups = duplicate_groups(refs, cut=0.05)
        containing_zero = next(g for g in groups if 0 in g)
        assert set(containing_zero) == {0, 1, 2}

    def test_well_separated_points_are_singleton_groups(self):
        centers = _orthogonal_centers(5, dim=16, seed=40)
        groups = duplicate_groups(centers, cut=0.05)
        assert groups == tuple((i,) for i in range(5))


class TestKishNEff:
    def test_all_singletons_equals_n(self):
        assert kish_n_eff([1] * 10) == pytest.approx(10.0)

    def test_equal_sized_groups_equals_group_count(self):
        assert kish_n_eff([4, 4, 4, 4]) == pytest.approx(4.0)

    def test_between_singleton_and_group_count(self):
        n_eff = kish_n_eff([1, 1, 5])
        assert 1.0 < n_eff < 3.0

    def test_empty_is_zero(self):
        assert kish_n_eff([]) == 0.0


class TestLooJackknifePlusInterval:
    def test_bounds_and_shape(self):
        center = _orthogonal_centers(1, dim=10, seed=50)[0]
        category = _clustered_embeddings(center, 10, noise=0.05, seed=51)
        candidate = _clustered_embeddings(center, 1, noise=0.05, seed=52)[0]

        interval = loo_jackknife_plus_interval(category, candidate, k=5, level=0.9)

        assert interval.method == "loo-jackknife-plus"
        assert interval.level == pytest.approx(0.9)
        assert 0.0 <= interval.low <= interval.high <= 1.0

    def test_deterministic(self):
        center = _orthogonal_centers(1, dim=10, seed=60)[0]
        category = _clustered_embeddings(center, 8, noise=0.05, seed=61)
        candidate = _clustered_embeddings(center, 1, noise=0.05, seed=62)[0]

        a = loo_jackknife_plus_interval(category, candidate, k=5, level=0.9)
        b = loo_jackknife_plus_interval(category, candidate, k=5, level=0.9)
        assert (a.low, a.high) == (b.low, b.high)

    def test_rejects_empty_category(self):
        candidate = _iid_embeddings(1)[0]
        with pytest.raises(ValueError):
            loo_jackknife_plus_interval(np.empty((0, 4)), candidate, k=3, level=0.9)


class TestPairedScoreDifferenceInterval:
    def test_identical_candidates_are_tied(self):
        center = _orthogonal_centers(1, dim=10, seed=70)[0]
        category = _clustered_embeddings(center, 8, noise=0.05, seed=71)
        candidate = _clustered_embeddings(center, 1, noise=0.05, seed=72)[0]

        interval = paired_score_difference_interval(category, candidate, candidate, k=5, level=0.9)

        assert interval.low == pytest.approx(0.0)
        assert interval.high == pytest.approx(0.0)

    def test_far_apart_candidates_are_not_tied(self):
        # candidate_a sits exactly at the cluster centre, so it is the most typical possible
        # member of the category rather than a further noisy draw that could, by chance under
        # exchangeability, itself land as the category's own outlier (an earlier version of
        # this test drew candidate_a with noise and occasionally hit exactly that coincidence,
        # tying it with the genuine outlier candidate_b for reasons unrelated to what this test
        # means to check).
        centers = _orthogonal_centers(2, dim=16, seed=80)
        category = _clustered_embeddings(centers[0], 10, noise=0.03, seed=81)
        candidate_a = centers[0]
        candidate_b = centers[1]

        interval = paired_score_difference_interval(
            category, candidate_a, candidate_b, k=5, level=0.9
        )

        assert not (interval.low <= 0.0 <= interval.high)

    def test_deterministic(self):
        center = _orthogonal_centers(1, dim=10, seed=90)[0]
        category = _clustered_embeddings(center, 8, noise=0.05, seed=91)
        candidate_a = _clustered_embeddings(center, 1, noise=0.05, seed=92)[0]
        candidate_b = _clustered_embeddings(center, 1, noise=0.05, seed=93)[0]

        a = paired_score_difference_interval(category, candidate_a, candidate_b, k=5, level=0.9)
        b = paired_score_difference_interval(category, candidate_a, candidate_b, k=5, level=0.9)
        assert (a.low, a.high) == (b.low, b.high)

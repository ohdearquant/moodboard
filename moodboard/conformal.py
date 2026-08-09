"""The conformal scoring machinery: nonconformity, the symmetric full-conformal p-value, the
augmented-bag category partition, near-duplicate grouping and Kish n_eff, and the
loo-jackknife-plus interval.

This module implements ADR-0003's score construction, ADR-0004 rule 2's category partition,
and ADR-0005's near-duplicate grouping and effective sample size, exactly as INTERFACES.md
pins their signatures. It produces `Interval` values defined in `report.py` and does not
define its own; it never imports `abstain.py`, `board.py` or `cli.py`, which sit downstream of
it in the module map.

Every score-moving fitting parameter is supplied by the caller. In particular,
`conformal_p_value` receives the board's stored effective `k` and clamps it only when a
category or leave-one-out fold has fewer available neighbours. It never reloads a mutable
threshold registry or substitutes its own neighbourhood cap.

**A second gap, in the leave-one-out interval functions.** Their docstrings in INTERFACES.md
describe recomputing `partition_categories` and `duplicate_groups` inside every fold, "on the
reduced bag", so that clustering is refit rather than held fixed across folds, per ADR-0002.
Their pinned signatures, though, take only `category_embeddings`, a candidate embedding (or
two), `k` and `level`: no content hashes, no cluster cut, no duplicate cut, no
`min_category_size`, nothing `partition_categories` or `duplicate_groups` need to run. Given
only what these signatures actually receive, this module refits the one thing it can, the
nonconformity rule and the resulting p-value, on the reference set with one member held out
each fold. Refitting the whole-board partition on every fold, which ADR-0002 also asks for,
needs board-level context (the full reference set, its content hashes, the fitting cuts) that
only a caller orchestrating leave-one-out at the board level has, and belongs there rather than
inside a function whose declared inputs are already scoped to one category.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from moodboard.report import Interval

__all__ = [
    "CategoryPartition",
    "conformal_p_value",
    "duplicate_groups",
    "kish_n_eff",
    "loo_jackknife_plus_interval",
    "nonconformity_scores",
    "paired_score_difference_interval",
    "partition_categories",
]


@dataclass(frozen=True, slots=True)
class CategoryPartition:
    """The augmented-bag partition ADR-0004 rule 2 defines, and the candidate's place in it.

    `candidate_category_members` and `all_categories[category_id]` carry the same indices:
    the reference indices, into `reference_embeddings` as `partition_categories` received it,
    that share the candidate's category. `all_categories` never includes the candidate's own
    bag index, only reference indices, for every surviving category.
    """

    category_id: str
    candidate_category_members: tuple[int, ...]
    all_categories: Mapping[str, tuple[int, ...]]


def nonconformity_scores(embeddings: np.ndarray, k: int) -> np.ndarray:
    """Mean cosine distance of each row to its k nearest neighbours among the other rows.

    `embeddings` is `(n, dim)`, assumed L2-normalised, so cosine similarity is the plain dot
    product. For row `i`, ranks the other `n - 1` rows by ascending distance and averages the
    nearest `k`. Ties in that ranking are broken by ascending row index: distances are sorted
    with a stable sort, and the input order is already ascending index, so equal distances keep
    their index order without a separate tie-break key. Deterministic for a fixed array.
    """
    embeddings = np.asarray(embeddings, dtype=np.float64)
    n = embeddings.shape[0]
    if not (1 <= k <= n - 1):
        raise ValueError(f"k must satisfy 1 <= k <= n - 1 for n={n} rows; got k={k}")

    distance = 1.0 - (embeddings @ embeddings.T)
    np.fill_diagonal(distance, np.inf)

    scores = np.empty(n, dtype=np.float64)
    for i in range(n):
        order = np.argsort(distance[i], kind="stable")
        nearest = order[:k]
        scores[i] = distance[i, nearest].mean()
    return scores


def _augmented_p_value(
    reference_embeddings: np.ndarray, candidate_embedding: np.ndarray, k: int
) -> float:
    """The shared core of `conformal_p_value` and the leave-one-out folds below: form the
    augmented bag, score every member with the same nonconformity rule at a caller-supplied
    k, and count references at least as unusual as the candidate."""
    reference_embeddings = np.asarray(reference_embeddings, dtype=np.float64)
    candidate_embedding = np.asarray(candidate_embedding, dtype=np.float64)
    n = reference_embeddings.shape[0]
    bag = np.vstack([reference_embeddings, candidate_embedding.reshape(1, -1)])

    alphas = nonconformity_scores(bag, k)
    reference_alphas = alphas[:n]
    candidate_alpha = alphas[n]

    tie_inclusive_count = int(np.sum(reference_alphas >= candidate_alpha))
    return (1 + tie_inclusive_count) / (n + 1)


def conformal_p_value(
    reference_embeddings: np.ndarray, candidate_embedding: np.ndarray, k: int
) -> float:
    """ADR-0003's symmetric full-conformal p-value, exactly.

    Forms the augmented bag of `n + 1` observations (the `n` references, then the candidate)
    and computes the same nonconformity measure, `min(k, n)`, over every member of it,
    including the candidate: each reference's own alpha is computed with the candidate present
    in its neighbour pool. Returns `(1 + count(alpha_i >= alpha_cand)) / (n + 1)`, ties counted
    in the numerator. The candidate is never one of the `n` references passed in; this
    function forms the augmented bag itself.
    """
    n = np.asarray(reference_embeddings).shape[0]
    if n < 1:
        raise ValueError("conformal_p_value requires at least one reference embedding")
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise ValueError(f"k must be a positive plain integer; got {k!r}")
    return _augmented_p_value(reference_embeddings, candidate_embedding, min(k, n))


def _pairwise_cosine_distance(bag: np.ndarray) -> np.ndarray:
    distance = 1.0 - (bag @ bag.T)
    np.fill_diagonal(distance, 0.0)
    return distance


def _cluster_min_hash(members: frozenset[int], hashes: Sequence[str]) -> str:
    return min(hashes[m] for m in members)


def _average_distance(a: frozenset[int], b: frozenset[int], distance: np.ndarray) -> float:
    total = sum(distance[i, j] for i in a for j in b)
    return total / (len(a) * len(b))


def _agglomerate_average_linkage(
    distance: np.ndarray, hashes: Sequence[str], cut: float
) -> dict[int, frozenset[int]]:
    """Average-linkage HAC over every bag member, cut at `cut`, with a fully deterministic
    merge order.

    At each step, the pair of clusters with the smallest average-linkage distance merges.
    ADR-0004 breaks ties in merge order "by the pair whose members have the lexicographically
    smaller content hash". Read literally against clusters that can hold more than one member,
    this module represents each side of a candidate pair by its own smallest content hash and
    compares the two candidate pairs by the sorted pair of those two representative hashes;
    the pair whose sorted-hash-pair sorts first wins the tie. This is the one place ADR-0004's
    tie-break prose, written for pairs of points, is extended to pairs of clusters, and it is
    recorded here because a rule stated in prose is a rule every implementer completes
    differently.
    """
    n = distance.shape[0]
    clusters: dict[int, frozenset[int]] = {i: frozenset({i}) for i in range(n)}

    while len(clusters) > 1:
        ids = sorted(clusters)
        best: tuple[float, tuple[str, str], int, int] | None = None
        for a_pos in range(len(ids)):
            for b_pos in range(a_pos + 1, len(ids)):
                a, b = ids[a_pos], ids[b_pos]
                d = _average_distance(clusters[a], clusters[b], distance)
                tie_key = tuple(
                    sorted(
                        (
                            _cluster_min_hash(clusters[a], hashes),
                            _cluster_min_hash(clusters[b], hashes),
                        )
                    )
                )
                candidate = (d, tie_key, a, b)
                if best is None or candidate < best:
                    best = candidate

        d, _tie_key, a, b = best
        if d > cut:
            break
        clusters[a] = clusters[a] | clusters[b]
        del clusters[b]

    return clusters


def _merge_undersized_categories(
    clusters: dict[int, frozenset[int]],
    distance: np.ndarray,
    hashes: Sequence[str],
    min_category_size: int,
) -> dict[int, frozenset[int]]:
    """Fold every category smaller than `min_category_size` into its nearest surviving
    category, smallest (then lexicographically-least-hashed) undersized category first, so the
    result never manufactures a category too small to calibrate."""
    clusters = dict(clusters)

    while len(clusters) > 1:
        undersized = [cid for cid, members in clusters.items() if len(members) < min_category_size]
        if not undersized:
            break
        undersized.sort(
            key=lambda cid: (len(clusters[cid]), _cluster_min_hash(clusters[cid], hashes))
        )
        victim = undersized[0]

        best_target: tuple[float, str, int] | None = None
        for cid, members in clusters.items():
            if cid == victim:
                continue
            d = _average_distance(clusters[victim], members, distance)
            candidate = (d, _cluster_min_hash(members, hashes), cid)
            if best_target is None or candidate < best_target:
                best_target = candidate

        target = best_target[2]
        clusters[target] = clusters[target] | clusters[victim]
        del clusters[victim]

    return clusters


def _assign_category_ids(
    clusters: dict[int, frozenset[int]], hashes: Sequence[str]
) -> dict[str, frozenset[int]]:
    """Stable ids, assigned by ascending representative content hash so the same partition
    always names its categories the same way regardless of internal cluster-id bookkeeping."""
    ordered = sorted(clusters.values(), key=lambda members: _cluster_min_hash(members, hashes))
    return {f"c{i}": members for i, members in enumerate(ordered)}


def partition_categories(
    reference_embeddings: np.ndarray,
    reference_content_hashes: Sequence[str],
    candidate_embedding: np.ndarray,
    candidate_content_hash: str,
    cut: float,
    min_category_size: int = 5,
) -> CategoryPartition:
    """ADR-0004 rule 2's category partition: average-linkage agglomerative clustering on the
    L2-normalised augmented bag under cosine distance, cut at `cut`, undersized categories
    folded into their nearest surviving neighbour.

    The candidate is clustered as a full member of the bag from the first merge step, never
    assigned post hoc to a cluster built from the references alone, which is the permutation
    symmetry ADR-0004 requires.
    """
    reference_embeddings = np.asarray(reference_embeddings, dtype=np.float64)
    candidate_embedding = np.asarray(candidate_embedding, dtype=np.float64)
    n = reference_embeddings.shape[0]
    if len(reference_content_hashes) != n:
        raise ValueError(
            "reference_content_hashes must have exactly one entry per reference embedding"
        )

    bag = np.vstack([reference_embeddings, candidate_embedding.reshape(1, -1)])
    hashes = [*reference_content_hashes, candidate_content_hash]
    candidate_index = n

    distance = _pairwise_cosine_distance(bag)
    raw = _agglomerate_average_linkage(distance, hashes, cut)
    merged = _merge_undersized_categories(raw, distance, hashes, min_category_size)
    named = _assign_category_ids(merged, hashes)

    candidate_category_id = next(
        cid for cid, members in named.items() if candidate_index in members
    )
    all_categories = {
        cid: tuple(sorted(m for m in members if m != candidate_index))
        for cid, members in named.items()
    }

    return CategoryPartition(
        category_id=candidate_category_id,
        candidate_category_members=all_categories[candidate_category_id],
        all_categories=all_categories,
    )


def duplicate_groups(reference_embeddings: np.ndarray, cut: float) -> tuple[tuple[int, ...], ...]:
    """ADR-0005's near-duplicate grouping: single-linkage agglomerative clustering on the
    L2-normalised reference embeddings under cosine distance, cut at `cut`.

    For single linkage, cutting the dendrogram at a fixed distance is exactly the connected
    components of the graph joining every pair at or under that distance, so this is computed
    directly as connected components rather than by simulating a merge sequence: the two are
    the same partition for any cut, and the direct form has no merge-order tie-break to define,
    unlike `partition_categories`'s average linkage. Returns groups as ascending tuples of
    reference indices, in ascending order of each group's smallest index, covering every
    reference exactly once.
    """
    reference_embeddings = np.asarray(reference_embeddings, dtype=np.float64)
    n = reference_embeddings.shape[0]
    distance = _pairwise_cosine_distance(reference_embeddings)

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(n):
        for j in range(i + 1, n):
            if distance[i, j] <= cut:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    ordered = sorted(groups.values(), key=lambda members: members[0])
    return tuple(tuple(members) for members in ordered)


def kish_n_eff(group_sizes: Sequence[int]) -> float:
    """Kish's effective sample size over near-duplicate group sizes: `(sum(s))^2 / sum(s^2)`,
    returned as a real, unrounded float. Equals `len(group_sizes)` when every group is a
    singleton and equals the group count when every group is the same size.
    """
    sizes = np.asarray(list(group_sizes), dtype=np.float64)
    if sizes.size == 0:
        return 0.0
    total = sizes.sum()
    return float((total**2) / np.sum(sizes**2))


def _empirical_interval(scores: Sequence[float], level: float) -> tuple[float, float]:
    """The empirical `level` interval over a fold-score distribution, type-7 quantile
    convention (numpy's default `linear` interpolation is Hyndman and Fan's type 7)."""
    array = np.asarray(list(scores), dtype=np.float64)
    tail = (1.0 - level) / 2.0
    low = float(np.quantile(array, tail, method="linear"))
    high = float(np.quantile(array, 1.0 - tail, method="linear"))
    return low, high


def _fold_p_value(
    category_embeddings: np.ndarray, held_out_index: int, target_embedding: np.ndarray, k: int
) -> float:
    """One leave-one-out fold: drop `held_out_index` from the category and refit the
    nonconformity rule and p-value against the remainder, at `k` clamped to the number of
    references the reduced fold actually has, since `k` is fixed at the caller's original
    board fit and a fold can have fewer references than that fit assumed."""
    reduced = np.delete(category_embeddings, held_out_index, axis=0)
    n_reduced = reduced.shape[0]
    if n_reduced < 1:
        raise ValueError(
            "a leave-one-out fold needs at least one reference left in the category "
            "after holding one out"
        )
    fold_k = min(k, n_reduced)
    return _augmented_p_value(reduced, target_embedding, fold_k)


def loo_jackknife_plus_interval(
    category_embeddings: np.ndarray,
    candidate_embedding: np.ndarray,
    k: int,
    level: float,
) -> Interval:
    """ADR-0002's loo-jackknife-plus interval around the candidate's score: for every
    reference in `category_embeddings`, remove it and recompute the conformal p-value against
    the remaining category, then take the empirical `level` interval of the resulting per-fold
    distribution.

    See the module docstring for why this refits the nonconformity rule per fold but not
    `partition_categories` or `duplicate_groups`: this function's signature carries no cut,
    hash or `min_category_size` argument for either to run on.
    """
    category_embeddings = np.asarray(category_embeddings, dtype=np.float64)
    m = category_embeddings.shape[0]
    if m < 1:
        raise ValueError("loo_jackknife_plus_interval requires at least one category reference")

    fold_scores = [_fold_p_value(category_embeddings, i, candidate_embedding, k) for i in range(m)]
    low, high = _empirical_interval(fold_scores, level)
    return Interval(low=low, high=high, level=level, method="loo-jackknife-plus")


def paired_score_difference_interval(
    category_embeddings: np.ndarray,
    candidate_a_embedding: np.ndarray,
    candidate_b_embedding: np.ndarray,
    k: int,
    level: float,
) -> Interval:
    """The interval around the difference between two candidates' scores against the same
    category, sharing the same leave-one-out folds so the two scores share their randomness.

    Two assets are tied exactly when this interval contains zero; `report.py`'s
    `comparisons.ties` calls this once per pair under consideration, never comparing two
    marginal intervals, per ADR-0002.
    """
    category_embeddings = np.asarray(category_embeddings, dtype=np.float64)
    m = category_embeddings.shape[0]
    if m < 1:
        raise ValueError(
            "paired_score_difference_interval requires at least one category reference"
        )

    diffs = [
        _fold_p_value(category_embeddings, i, candidate_a_embedding, k)
        - _fold_p_value(category_embeddings, i, candidate_b_embedding, k)
        for i in range(m)
    ]
    low, high = _empirical_interval(diffs, level)
    return Interval(low=low, high=high, level=level, method="loo-jackknife-plus")

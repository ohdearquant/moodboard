"""Tests for the three abstention rules.

The constructed cases and the quiet population are read from `eval/thresholds.json` at runtime
rather than restated here, so a change to a pre-registered number moves these tests with it. Every
board is a seeded synthetic embedding array; nothing here downloads a dataset.

Scope, stated so a green run is not read as more than it is. The pre-registered fire rate and
false-abstention rate are properties of the real datasets named in DATASETS.md, measured by the
evaluation harness. What runs here is the decision rule itself against boards built to have the
geometry each case describes, at a trial count small enough for a unit suite. A pass says the rule
fires and stays quiet as specified on those constructions, and says nothing about the rates on
real images.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from moodboard.abstain import (
    AbstentionVerdict,
    category_n_eff,
    check_far_outlier,
    check_multi_modality,
    check_resolution,
    evaluate_abstention,
    load_abstention_thresholds,
    verify_resolution_table,
)
from moodboard.conformal import (
    conformal_p_value,
    duplicate_groups,
    nonconformity_scores,
    partition_categories,
)

DIM = 32
CLUSTER_CUT = 0.35
DUP_CUT = 0.05
TRIALS = 20


# ---------------------------------------------------------------------------
# Synthetic boards
# ---------------------------------------------------------------------------


def _unit(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)


def _look(rng: np.random.Generator, count: int, axis: int, jitter: float = 0.10) -> np.ndarray:
    """`count` L2-normalised embeddings scattered around basis direction `axis`.

    Two looks on different axes are orthogonal, so their cosine distance is about 1.0 and they
    survive the 0.35 category cut as separate categories.

    The default jitter is chosen against BOTH cuts, and that is the point of stating it here.
    Within a look the pairwise cosine distance runs from about 0.09 to 0.44 with a mean near 0.24,
    which is above the 0.05 duplicate cut, so the references are genuinely distinct and n_eff
    equals the file count, and below the 0.35 average-linkage cut, so the look stays one category.
    A tighter scatter makes every reference a near-duplicate of every other, which is a real board
    but not the board these cases describe, and it silently turns a resolution test into a
    duplicate test.
    """
    base = np.zeros(DIM)
    base[axis] = 1.0
    return _unit(base + jitter * rng.normal(size=(count, DIM)))


def _duplicated_look(
    rng: np.random.Generator,
    group_sizes: list[int],
    axis: int,
    base_jitter: float = 0.10,
    dup_jitter: float = 1e-4,
) -> np.ndarray:
    """`len(group_sizes)` distinct sources on `axis`, each repeated to its listed size.

    The distinct sources are scattered exactly as `_look` scatters a whole board, so they survive
    as one category; each source is then repeated and perturbed by `dup_jitter`, far below the
    0.05 duplicate cut, so the repeats are near-duplicates of their own source rather than exact
    copies or copies of a different source. This is `eval/thresholds.json`'s `group_sizes`
    histogram, expanded to references, for the two cases whose point is n_eff diverging from the
    file count.
    """
    bases = _look(rng, len(group_sizes), axis, jitter=base_jitter)
    groups = [
        _unit(
            np.repeat(base.reshape(1, -1), size, axis=0) + dup_jitter * rng.normal(size=(size, DIM))
        )
        for base, size in zip(bases, group_sizes, strict=True)
    ]
    return np.vstack(groups)


def _expand_group_sizes(histogram: dict) -> list[int]:
    """`{"3": 4, "4": 2}` (4 groups of 3, 2 groups of 4) -> `[3, 3, 3, 3, 4, 4]`."""
    sizes: list[int] = []
    for size, count in histogram.items():
        sizes.extend([int(size)] * count)
    return sizes


def _hashes(count: int, prefix: str = "r") -> list[str]:
    return [f"{prefix}{index:04d}" for index in range(count)]


def _partition(references: np.ndarray, candidate: np.ndarray, candidate_hash: str = "zcand"):
    return partition_categories(
        references,
        _hashes(references.shape[0]),
        candidate,
        candidate_hash,
        cut=CLUSTER_CUT,
    )


def _augmented_alphas(references: np.ndarray, candidate: np.ndarray) -> tuple[np.ndarray, float]:
    """The reference and candidate nonconformity values from one augmented-bag computation.

    This is the same bag `conformal_p_value` forms, which is what rule 3 requires: the far-outlier
    check reads values already computed for this candidate rather than starting a second pass.
    """
    n = references.shape[0]
    bag = np.vstack([references, candidate.reshape(1, -1)])
    alphas = nonconformity_scores(bag, min(5, n))
    return alphas[:n], float(alphas[n])


@pytest.fixture(scope="module")
def thresholds():
    return load_abstention_thresholds()


@pytest.fixture(scope="module")
def registry():
    loaded = load_abstention_thresholds()
    with loaded.path.open(encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------


def test_thresholds_are_read_from_the_committed_registry(thresholds):
    assert thresholds.path.name == "thresholds.json"
    assert thresholds.path.parent.name == "eval"
    assert thresholds.max_false_abstention_rate == 0.05
    assert thresholds.required_fire_rate == 1.0
    assert len(thresholds.must_fire_cases) == 4


def test_env_var_overrides_the_registry_location(tmp_path, monkeypatch, registry):
    elsewhere = tmp_path / "thresholds.json"
    elsewhere.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setenv("MOODBOARD_THRESHOLDS", str(elsewhere))
    assert load_abstention_thresholds().path == elsewhere

    monkeypatch.setenv("MOODBOARD_THRESHOLDS", str(tmp_path / "absent.json"))
    with pytest.raises(FileNotFoundError):
        load_abstention_thresholds()


def test_the_computed_resolution_floor_agrees_with_the_registered_table(thresholds):
    """1/(N+1) computed exactly must match `finest_expressible_by_board_size` at its own precision.

    The registered values are rounded and the trigger is not: 1/11 is 0.090909..., so a request at
    exactly the registered 0.0909 is below the true floor and must be refused. This checks the two
    representations agree without letting the rounded one become the trigger.
    """
    verify_resolution_table(thresholds)
    assert thresholds.finest_expressible_by_board_size[10] == 0.0909

    rng = np.random.default_rng(11)
    references = _look(rng, 10, 0)
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)
    assert check_resolution(partition, 0.0909) is not None
    assert check_resolution(partition, 1.0 / 11.0) is None


def test_the_far_outlier_multiplier_names_its_own_source(thresholds):
    """The registry carries no key for the Tukey multiplier today, so the value comes from
    ADR-0004 and says so. If the key is added later the source becomes the registry path, and
    this test moves with it rather than pinning the absence."""
    assert thresholds.far_outlier_iqr_multiplier == pytest.approx(1.5)
    assert thresholds.far_outlier_iqr_multiplier_source.endswith(
        ("0004-abstention.md", "thresholds.json")
    )


# ---------------------------------------------------------------------------
# Criterion 1: the rules fire when they should
# ---------------------------------------------------------------------------


def _must_fire_case(thresholds, name):
    for case in thresholds.must_fire_cases:
        if case["case"] == name:
            return case
    raise AssertionError(f"eval/thresholds.json carries no must_fire case named {name!r}")


def test_threshold_below_resolution_fires_with_reason_resolution(thresholds):
    case = _must_fire_case(thresholds, "threshold_below_resolution")
    rng = np.random.default_rng(1)
    references = _look(rng, case["n"], 0)
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)
    reference_alphas, candidate_alpha = _augmented_alphas(references, candidate)

    verdict = evaluate_abstention(
        partition,
        case["alpha"],
        candidate_alpha,
        reference_alphas,
        duplicate_groups(references, DUP_CUT),
        thresholds,
    )

    assert verdict is not None
    assert verdict.reason == case["expect_reason"] == "resolution"
    assert verdict.measurement["n_local"] == case["n"]
    assert verdict.measurement["n_categories"] == 1
    assert verdict.measurement["requested_alpha"] == case["alpha"]
    assert verdict.measurement["supported_alpha"] == pytest.approx(1.0 / (case["n"] + 1))


def test_two_disjoint_style_groups_fire_with_reason_multi_modality(thresholds):
    case = _must_fire_case(thresholds, "two_disjoint_style_groups")
    sub_look = case["sub_look_size"]
    rng = np.random.default_rng(2)
    references = np.vstack([_look(rng, sub_look, 0), _look(rng, sub_look, 1)])
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)
    reference_alphas, candidate_alpha = _augmented_alphas(references, candidate)

    verdict = evaluate_abstention(
        partition,
        case["alpha"],
        candidate_alpha,
        reference_alphas,
        duplicate_groups(references, DUP_CUT),
        thresholds,
    )

    assert verdict is not None
    assert verdict.reason == case["expect_reason"] == "multi_modality"
    assert verdict.measurement["n_local"] == sub_look
    assert verdict.measurement["n_references"] == 2 * sub_look
    assert verdict.measurement["n_categories"] == 2
    assert verdict.measurement["supported_alpha"] == pytest.approx(1.0 / (sub_look + 1))


def test_asset_from_an_absent_domain_fires_with_reason_far_outlier(thresholds):
    """A board large enough that the resolution rule is satisfied, so the far-outlier rule is the
    one under test rather than being shadowed by a refusal that would have happened anyway."""
    case = _must_fire_case(thresholds, "asset_from_absent_domain")
    rng = np.random.default_rng(3)
    references = _look(rng, 50, 0)
    candidate = _look(rng, 1, 7)[0]
    partition = _partition(references, candidate)
    reference_alphas, candidate_alpha = _augmented_alphas(references, candidate)

    assert check_resolution(partition, case["alpha"]) is None

    verdict = evaluate_abstention(
        partition,
        case["alpha"],
        candidate_alpha,
        reference_alphas,
        duplicate_groups(references, DUP_CUT),
        thresholds,
    )

    assert verdict is not None
    assert verdict.reason == case["expect_reason"] == "far_outlier"
    assert verdict.measurement["candidate_alpha"] > verdict.measurement["threshold"]
    assert verdict.measurement["iqr_multiplier"] == pytest.approx(1.5)


def test_resolution_effective_size_arm_fires_only_because_of_n_eff(thresholds):
    """ADR-0004's second floor on rule 1: a board whose file count alone would honour the
    request, refused only because near-duplicates lower n_eff below what alpha needs.

    This is the case that discriminates an implementation reading `n_local` (the achievability
    floor, which admits the request here) from one reading `n_eff_local` (the admissibility
    floor, which does not); only the second is a conforming rule 1.
    """
    case = _must_fire_case(thresholds, "resolution_effective_size_arm")
    group_sizes = _expand_group_sizes(case["group_sizes"])
    assert sum(group_sizes) == case["n"]

    rng = np.random.default_rng(100)
    references = _duplicated_look(rng, group_sizes, axis=0)
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)
    groups = duplicate_groups(references, DUP_CUT)
    assert sorted(len(group) for group in groups) == sorted(group_sizes)

    resolution_alpha = 1.0 / (case["n"] + 1)
    assert resolution_alpha < case["alpha"], (
        "the file-count floor must admit this request, or the case does not discriminate "
        "an implementation that ignores n_eff from one that reads it"
    )

    verdict = check_resolution(partition, case["alpha"], groups)

    assert verdict is not None
    assert verdict.reason == case["expect_reason"] == "resolution"
    assert verdict.measurement["n_local"] == case["n"]
    assert verdict.measurement["n_eff_local_source"] == "duplicate_groups"
    assert verdict.measurement["resolution_alpha"] == pytest.approx(resolution_alpha)
    assert verdict.measurement["supported_alpha"] > case["alpha"]


def test_every_must_fire_case_in_the_registry_is_covered():
    """The registry is the list of cases, so a case added to it without a test here fails loudly
    rather than being silently unmeasured."""
    covered = {
        "threshold_below_resolution",
        "two_disjoint_style_groups",
        "asset_from_absent_domain",
        "resolution_effective_size_arm",
    }
    registered = {case["case"] for case in load_abstention_thresholds().must_fire_cases}
    assert registered == covered


# ---------------------------------------------------------------------------
# Criterion 1b: multi-modality is reported without being refused
# ---------------------------------------------------------------------------


def test_large_sub_looks_are_scored_and_carry_their_category(thresholds):
    """The other half of rule 2. A stuck-on-abstain implementation passes every must-fire test and
    fails only here, which is why ADR-0004 makes this a separate arm."""
    case = thresholds.must_detect_and_score["case"]
    sub_look = case["sub_look_size"]
    alpha = case["alpha"]
    rng = np.random.default_rng(4)

    abstained = 0
    for trial in range(TRIALS):
        references = np.vstack([_look(rng, sub_look, 0), _look(rng, sub_look, 1)])
        candidate = _look(rng, 1, 0)[0]
        partition = _partition(references, candidate, candidate_hash=f"z{trial:04d}")
        reference_alphas, candidate_alpha = _augmented_alphas(references, candidate)

        verdict = evaluate_abstention(
            partition,
            alpha,
            candidate_alpha,
            reference_alphas,
            duplicate_groups(references, DUP_CUT),
            thresholds,
        )
        if verdict is not None:
            abstained += 1
            continue

        assert len(partition.candidate_category_members) == sub_look
        assert len(partition.all_categories) == 2
        assert partition.category_id in partition.all_categories
        score = conformal_p_value(references[list(partition.candidate_category_members)], candidate)
        assert 0.0 < score <= 1.0

    rate = abstained / TRIALS
    assert rate <= thresholds.must_detect_and_score["max_abstention_rate"], (
        f"{abstained}/{TRIALS} abstained on a board the decision rule requires be scored"
    )


# ---------------------------------------------------------------------------
# Criterion 2: the rules stay quiet when they should
# ---------------------------------------------------------------------------


def test_the_rules_stay_quiet_on_well_formed_boards(thresholds):
    """Every row of the registered quiet population, each at an alpha its board size can express,
    with the rate reported per reason so one rule firing constantly cannot hide behind the others.
    """
    per_reason: dict[str, int] = {"resolution": 0, "multi_modality": 0, "far_outlier": 0}
    total = 0

    for row_index, row in enumerate(thresholds.must_stay_quiet_population):
        rng = np.random.default_rng(100 + row_index)
        for trial in range(TRIALS):
            if row["look"] == "single" and "group_sizes" in row:
                group_sizes = _expand_group_sizes(row["group_sizes"])
                assert sum(group_sizes) == row["n"]
                references = _duplicated_look(rng, group_sizes, axis=0)
            elif row["look"] == "single":
                references = _look(rng, row["n"], 0)
            else:
                sub_look = row["sub_look_size"]
                assert 2 * sub_look == row["n"]
                references = np.vstack([_look(rng, sub_look, 0), _look(rng, sub_look, 1)])
            candidate = _look(rng, 1, 0)[0]

            partition = _partition(references, candidate, candidate_hash=f"z{trial:04d}")
            reference_alphas, candidate_alpha = _augmented_alphas(references, candidate)
            verdict = evaluate_abstention(
                partition,
                row["alpha"],
                candidate_alpha,
                reference_alphas,
                duplicate_groups(references, DUP_CUT),
                thresholds,
            )
            total += 1
            if verdict is not None:
                per_reason[verdict.reason] += 1

    for reason, count in per_reason.items():
        rate = count / total
        assert rate <= thresholds.max_false_abstention_rate, (
            f"{reason} abstained on {count}/{total} well-formed boards, above the registered "
            f"{thresholds.max_false_abstention_rate}"
        )


# ---------------------------------------------------------------------------
# The resolution rule in detail
# ---------------------------------------------------------------------------


def test_alpha_exactly_at_the_floor_is_honoured_and_just_below_it_is_not():
    """ADR-0004: 'The comparison is strict: alpha exactly equal to 1/(n_local+1) is honoured,
    because that value is achievable.'"""
    rng = np.random.default_rng(5)
    references = _look(rng, 20, 0)
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)
    floor = 1.0 / 21.0

    assert check_resolution(partition, floor) is None
    assert check_resolution(partition, np.nextafter(floor, 0.0)) is not None
    assert check_resolution(partition, floor * 2) is None


def test_n_local_counts_references_and_not_the_candidate():
    """A ten-reference board has floor 1/11 = 0.0909, which is what ADR-0004's own worked
    arithmetic and `score_semantics.finest_expressible_by_board_size` both say. Counting the
    candidate inside n_local as well would report 1/12 and disagree with the denominator the score
    was computed from."""
    rng = np.random.default_rng(6)
    references = _look(rng, 10, 0)
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)

    verdict = check_resolution(partition, 0.05)
    assert verdict is not None
    assert verdict.measurement["n_local"] == 10
    assert verdict.measurement["resolution_alpha"] == pytest.approx(1.0 / 11.0)


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5, float("nan")])
def test_an_alpha_outside_the_schema_range_is_rejected(alpha):
    rng = np.random.default_rng(7)
    references = _look(rng, 10, 0)
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)
    with pytest.raises(ValueError):
        check_resolution(partition, alpha)


def test_check_multi_modality_delegates_rather_than_reimplementing():
    """ADR-0004 defines rule 2's trigger as rule 1 applied to the candidate's category, so the two
    must be the same comparison. Anything else drifts on the boundary."""
    rng = np.random.default_rng(8)
    references = np.vstack([_look(rng, 8, 0), _look(rng, 8, 1)])
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)

    for alpha in (0.02, 0.05, 1.0 / 9.0, 0.2):
        assert check_multi_modality(partition, alpha) == check_resolution(partition, alpha)


def test_a_single_look_board_never_reports_multi_modality():
    rng = np.random.default_rng(9)
    references = _look(rng, 10, 0)
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)
    verdict = check_multi_modality(partition, 0.01)
    assert verdict is not None
    assert verdict.reason == "resolution"


# ---------------------------------------------------------------------------
# The n_eff admissibility floor (ADR-0005)
# ---------------------------------------------------------------------------


def test_near_duplicates_raise_the_floor_and_the_measurement_says_so():
    """ADR-0005: 'A requested alpha is honoured only when it is at least 1/(n_eff_local+1).' A
    board of twenty files built from ten near-identical pairs stops advertising twenty-file
    resolution, which is the whole mechanism that record introduces.
    """
    rng = np.random.default_rng(10)
    distinct = _look(rng, 20, 0)
    duplicated = _unit(np.repeat(_look(rng, 10, 0), 2, axis=0) + 1e-4 * rng.normal(size=(20, DIM)))
    candidate = _look(rng, 1, 0)[0]
    alpha = 0.06

    distinct_partition = _partition(distinct, candidate)
    duplicated_partition = _partition(duplicated, candidate)
    distinct_groups = duplicate_groups(distinct, DUP_CUT)
    duplicated_groups = duplicate_groups(duplicated, DUP_CUT)

    assert len(distinct_groups) == 20
    assert len(duplicated_groups) == 10
    assert category_n_eff(duplicated_partition, duplicated_groups) == pytest.approx(10.0)

    assert check_resolution(distinct_partition, alpha, distinct_groups) is None

    verdict = check_resolution(duplicated_partition, alpha, duplicated_groups)
    assert verdict is not None
    assert verdict.measurement["n_local"] == 20
    assert verdict.measurement["n_eff_local"] == pytest.approx(10.0)
    assert verdict.measurement["n_eff_local_source"] == "duplicate_groups"
    assert verdict.measurement["supported_alpha"] == pytest.approx(1.0 / 11.0)
    assert verdict.measurement["resolution_alpha"] == pytest.approx(1.0 / 21.0)
    assert "near-duplicates" in verdict.explanation


def test_omitting_the_duplicate_groups_is_recorded_as_an_assumption():
    """A caller that does not supply the groups is asserting the references are distinct. The
    floor then relaxes to the file count, which is the permissive direction, so the measurement
    says which of the two happened rather than presenting an assumed floor as a measured one."""
    rng = np.random.default_rng(12)
    references = _look(rng, 10, 0)
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)

    verdict = check_resolution(partition, 0.05)
    assert verdict is not None
    assert verdict.measurement["n_eff_local_source"] == "assumed_distinct"
    assert verdict.measurement["n_eff_local"] == pytest.approx(10.0)


def test_category_n_eff_restricts_the_board_groups_to_the_candidates_category():
    rng = np.random.default_rng(13)
    references = np.vstack([_look(rng, 8, 0), _look(rng, 8, 1)])
    candidate = _look(rng, 1, 0)[0]
    partition = _partition(references, candidate)
    groups = duplicate_groups(references, DUP_CUT)

    assert category_n_eff(partition, groups) == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# The far-outlier rule in detail
# ---------------------------------------------------------------------------


def test_far_outlier_uses_the_tukey_line_over_the_board_maximum(thresholds):
    alphas = [0.10, 0.12, 0.14, 0.16, 0.20]
    q1, q3 = np.quantile(alphas, [0.25, 0.75], method="linear")
    line = max(alphas) + 1.5 * (q3 - q1)

    assert check_far_outlier(line, alphas, thresholds) is None
    verdict = check_far_outlier(np.nextafter(line, 1.0), alphas, thresholds)
    assert verdict is not None
    assert verdict.measurement["threshold"] == pytest.approx(line)
    assert verdict.measurement["reference_max"] == pytest.approx(0.20)
    assert verdict.measurement["reference_iqr"] == pytest.approx(q3 - q1)


def test_a_merely_unusual_in_distribution_asset_does_not_fire(thresholds):
    """ADR-0004: 'It will not fire on a merely unusual in-medium asset, which is correct, because
    that case is what a low p-value is for.' The candidate here is the most unusual member of its
    own board and still scores."""
    rng = np.random.default_rng(14)
    references = _look(rng, 30, 0)
    candidate = _look(rng, 1, 0, jitter=0.15)[0]
    reference_alphas, candidate_alpha = _augmented_alphas(references, candidate)

    assert candidate_alpha > float(np.median(reference_alphas))
    assert check_far_outlier(candidate_alpha, reference_alphas, thresholds) is None


def test_the_far_outlier_explanation_never_names_a_medium(thresholds):
    """ADR-0004 withdrew the medium framing explicitly: the machinery observes distance and has no
    medium taxonomy, classifier or margin. An explanation that says 'this looks like a vector
    logo' would be claiming something nothing here measured."""
    verdict = check_far_outlier(5.0, [0.1, 0.11, 0.12, 0.13], thresholds)
    assert verdict is not None
    forbidden = (
        "logo",
        "vector",
        "screenshot",
        "photograph",
        "photo",
        "illustration",
        "medium",
        "file type",
        "format",
        "png",
        "jpeg",
        "svg",
        "video",
    )
    lowered = verdict.explanation.lower()
    for word in forbidden:
        assert word not in lowered, f"the far-outlier explanation names a medium: {word!r}"


def test_far_outlier_refuses_an_empty_board(thresholds):
    with pytest.raises(ValueError):
        check_far_outlier(1.0, [], thresholds)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_far_outlier_refuses_non_finite_inputs(bad, thresholds):
    with pytest.raises(ValueError):
        check_far_outlier(bad, [0.1, 0.2], thresholds)
    with pytest.raises(ValueError):
        check_far_outlier(0.1, [0.1, bad], thresholds)


# ---------------------------------------------------------------------------
# Ordering, and the shape every verdict carries
# ---------------------------------------------------------------------------


def test_resolution_wins_when_both_rules_would_fire(thresholds):
    """A report needs exactly one reason per asset. The resolution check is asked first, so an
    asset that is both unscorable at the requested alpha and a far outlier is reported under
    resolution and the far-outlier rule is never consulted."""
    rng = np.random.default_rng(15)
    references = _look(rng, 10, 0)
    candidate = _look(rng, 1, 7)[0]
    partition = _partition(references, candidate)
    reference_alphas, candidate_alpha = _augmented_alphas(references, candidate)

    assert check_far_outlier(candidate_alpha, reference_alphas, thresholds) is not None
    verdict = evaluate_abstention(
        partition, 0.05, candidate_alpha, reference_alphas, None, thresholds
    )
    assert verdict is not None
    assert verdict.reason == "resolution"


def test_every_verdict_carries_a_reason_a_measurement_and_a_sentence(thresholds):
    """The three parts ADR-0004 requires, on every rule. The explanation is checked for being a
    sentence rather than a code: it ends in a full stop, contains a space, and is not the reason
    string dressed up."""
    rng = np.random.default_rng(16)

    small = _look(rng, 10, 0)
    small_candidate = _look(rng, 1, 0)[0]
    resolution = check_resolution(_partition(small, small_candidate), 0.05)

    split = np.vstack([_look(rng, 8, 0), _look(rng, 8, 1)])
    split_candidate = _look(rng, 1, 0)[0]
    multi = check_resolution(_partition(split, split_candidate), 0.05)

    outlier = check_far_outlier(5.0, [0.1, 0.11, 0.12], thresholds)

    for verdict in (resolution, multi, outlier):
        assert isinstance(verdict, AbstentionVerdict)
        assert verdict.reason in {"resolution", "multi_modality", "far_outlier"}
        assert dict(verdict.measurement), "a verdict must carry the measurement that triggered it"
        assert verdict.explanation.strip().endswith(".")
        assert len(verdict.explanation.split()) >= 10
        assert verdict.reason not in verdict.explanation

    assert resolution.reason == "resolution"
    assert multi.reason == "multi_modality"
    assert outlier.reason == "far_outlier"


def test_the_resolution_sentence_states_both_numbers_the_way_adr_0004_writes_it():
    """ADR-0004's worked example: 'This board has 10 references, so the finest distinction it can
    express is about 9%, and you asked for 5%.'"""
    rng = np.random.default_rng(17)
    references = _look(rng, 10, 0)
    candidate = _look(rng, 1, 0)[0]
    verdict = check_resolution(_partition(references, candidate), 0.05)
    assert verdict is not None
    assert "10 references" in verdict.explanation
    assert "9%" in verdict.explanation
    assert "5%" in verdict.explanation


def test_every_measurement_is_json_primitive_and_schema_valid(thresholds):
    """The report schema keeps `measurement` open but requires an object with at least one key,
    and `report.py` serialises it directly. Every value here must therefore already be a JSON
    primitive rather than a numpy scalar, which serialises to something no schema describes."""
    import jsonschema

    from moodboard.report import SCHEMA_PATH

    with open(SCHEMA_PATH, encoding="utf-8") as handle:
        schema = json.load(handle)
    measurement_schema = schema["$defs"]["abstainedAsset"]["properties"]["measurement"]

    rng = np.random.default_rng(18)
    references = _look(rng, 10, 0)
    candidate = _look(rng, 1, 0)[0]
    groups = duplicate_groups(references, DUP_CUT)
    verdicts = [
        check_resolution(_partition(references, candidate), 0.05, groups),
        check_far_outlier(5.0, [0.1, 0.11, 0.12], thresholds),
    ]

    for verdict in verdicts:
        assert verdict is not None
        payload = dict(verdict.measurement)
        for key, value in payload.items():
            assert isinstance(key, str)
            assert isinstance(value, (int, float, str, bool)), (
                f"{key} is {type(value).__name__}, which report.py cannot serialise as it stands"
            )
        jsonschema.validate(json.loads(json.dumps(payload)), measurement_schema)

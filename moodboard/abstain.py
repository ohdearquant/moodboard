"""The three abstention rules of ADR-0004, each returning a structured reason, the measurement
that triggered it, and a plain sentence a designer can act on.

Abstention is an output with the same standing as a score. This module decides only whether to
abstain and why; `report.py` carries the result, and the report's abstained state has no `score`
field at all rather than a null one, for the reason ADR-0004 gives.

The rules run against real quantities produced by `conformal.py`. The resolution rule reads a
`CategoryPartition` from `partition_categories` and the near-duplicate groups from
`duplicate_groups`, and computes the category-local Kish effective size with `kish_n_eff`. The
far-outlier rule reads the nonconformity values from the same augmented-bag computation
`conformal_p_value` already ran for this candidate. No rule recomputes a quantity another module
owns, and no rule invents a second formula for one.

Rules 1 and 2 are one arithmetic test with two names
-----------------------------------------------------
ADR-0004 defines rule 2's trigger as "the candidate's own category cannot satisfy rule 1 at the
requested alpha", and rule 1 is itself stated in terms of `n_local`, "the number of references in
the candidate's own category under rule 2, and equals n on a single-look board". Both rules are
therefore the same comparison applied to the candidate's category, and what differs is only which
reason string is honest to report. They are written here as one function that chooses the reason,
because two independently written formulas drift apart on exactly the boundary cases the rules
exist for. `check_multi_modality` is a named entry point that delegates to it.

What is read from `eval/thresholds.json` at runtime, and what is not
--------------------------------------------------------------------
`load_abstention_thresholds` reads the registry file from disk on every call, so a change to a
pre-registered number takes effect without reinstalling anything, and no pre-registered number is
copied into this source file.

The registry carries, under `abstention`, the constructed cases the rules must fire on, the
population they must stay quiet on, the required fire rate and the maximum false-abstention rate.
Those are the acceptance bars, and the test suite reads them from here rather than restating them.

Two things the rules need are not in the registry, and both are named rather than quietly
invented:

* **The far-outlier IQR multiplier.** ADR-0004 pins 1.5 in its own text and says the rule "has no
  free parameter beyond the 1.5 fixed here". The registry has no key for it. This module looks for
  `abstention.far_outlier.iqr_multiplier` and uses it when present, so adding the key later moves
  the number without a code change; when the key is absent it uses ADR-0004's 1.5 and records
  `iqr_multiplier_source` in the measurement, so a report says where its own threshold came from.

* **The resolution floor itself is computed, never looked up.** The registry does carry
  `score_semantics.finest_expressible_by_board_size`, which reads `{"10": 0.0909, "20": 0.0476,
  "50": 0.0196}`. Those are rounded, and using a rounded floor as the trigger is wrong in the
  permissive direction: 1/11 is 0.090909..., so a request at exactly 0.0909 is below the true
  floor and must be refused, while a rule reading the rounded table would honour it. The floor is
  computed exactly as 1/(n+1) and the table is used only by `verify_resolution_table`, which
  checks that the computed rule and the registry agree at the registry's own precision. That
  function is a consistency check between two representations, not a source of the trigger.

Two departures from `INTERFACES.md`, recorded rather than worked around
------------------------------------------------------------------------
**`n_local` counts references, and does not include the candidate.** `INTERFACES.md` USED TO pin
`n_local = len(partition.candidate_category_members) + 1`, "the references sharing the category
plus the candidate itself", which read with the rest of the specification is off by one. **That
document was corrected on 2026-08-08 and now agrees with this module, so this is a resolved
departure and no longer a live disagreement.** The reasoning below is kept rather than deleted,
because the correction was made on the strength of it and because a reader landing here alone
would otherwise have no way to tell which form was withdrawn. ADR-0004's
own worked arithmetic is "Boards resampled to n = 10 with alpha = 0.05, where 1/(n+1) = 0.0909",
and `eval/thresholds.json` registers `finest_expressible_by_board_size` as `{"10": 0.0909}`, so a
board of ten references has floor 1/11 and the ten is a count of references. The committed schema
agrees from the other side: its `pValue` description says the p-value is `(1 + count) / (n_local +
1)`, and `conformal.py` divides by the reference count plus one. Adding the candidate into
`n_local` and then adding one again would count it twice, report a floor of 1/12 on a ten-image
board, and make the number printed in the report disagree with the number the score was actually
computed from. So `n_local` here is `len(partition.candidate_category_members)`.

**The admissibility floor reads n_eff, not n.** `INTERFACES.md` pins the trigger as
`requested_alpha < 1 / (n_local + 1)`. ADR-0005 amends ADR-0004 rule 1 explicitly: "A requested
alpha is honoured only when it is at least 1/(n_eff_local+1)", and its consequences section says
"The admissibility floor in ADR-0004's rule 1 reads n_eff; the score's denominator in ADR-0003
does not." The committed schema carries the same reading, describing `board.supported_alpha` as
"1 / (n_eff_local + 1), the finest honourable request". So the governing comparison is against
1/(n_eff_local + 1). Since n_eff is at most n, that floor is at least the file-count floor and
never lets through a request the `INTERFACES.md` form would have refused; on a board with no
near-duplicates the two are identical. The score's denominator is untouched by any of this and
stays the reference count, which is the ADR-0005 correction this module must not undo.

Neither departure changes the outcome of any case pinned in `eval/thresholds.json`. They are
recorded because a silent reinterpretation of a pinned formula is the defect these records were
corrected for.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from moodboard.conformal import CategoryPartition, kish_n_eff

__all__ = [
    "AbstentionThresholds",
    "AbstentionVerdict",
    "category_n_eff",
    "check_far_outlier",
    "check_multi_modality",
    "check_resolution",
    "evaluate_abstention",
    "load_abstention_thresholds",
    "verify_resolution_table",
]

AbstentionReason = Literal["resolution", "multi_modality", "far_outlier"]

# ADR-0004 rule 3: "abstain when alpha_cand exceeds max(alpha_1 ... alpha_n) + 1.5 x
# IQR(alpha_1 ... alpha_n), the conventional Tukey far-outlier rule ... it has no free parameter
# beyond the 1.5 fixed here." Used only when eval/thresholds.json carries no key for it; see the
# module docstring.
_ADR_0004_IQR_MULTIPLIER = 1.5
_ADR_0004_IQR_MULTIPLIER_SOURCE = "docs/adr/0004-abstention.md"

_THRESHOLDS_ENV_VAR = "MOODBOARD_THRESHOLDS"
_THRESHOLDS_RELATIVE_PATH = Path("eval") / "thresholds.json"


@dataclass(frozen=True, slots=True)
class AbstentionVerdict:
    """A refusal: which rule fired, the numbers that made it fire, and the sentence to show.

    `measurement` keys depend on `reason` and are pinned in each check's docstring. `explanation`
    is a complete sentence in the register ADR-0004 requires, built from the same numbers
    `measurement` carries, so the prose and the machine-readable fields cannot disagree.
    """

    reason: AbstentionReason
    measurement: Mapping[str, Any]
    explanation: str


@dataclass(frozen=True, slots=True)
class AbstentionThresholds:
    """The abstention-relevant contents of `eval/thresholds.json`, as read from the file.

    `far_outlier_iqr_multiplier_source` names where the multiplier came from, since the registry
    does not carry one today and a report that cannot say where its own threshold came from is
    the problem these records exist to avoid.
    """

    path: Path
    far_outlier_iqr_multiplier: float
    far_outlier_iqr_multiplier_source: str
    finest_expressible_by_board_size: Mapping[int, float]
    must_fire_cases: tuple[Mapping[str, Any], ...]
    required_fire_rate: float
    must_detect_and_score: Mapping[str, Any]
    must_stay_quiet_population: tuple[Mapping[str, Any], ...]
    max_false_abstention_rate: float


def _default_thresholds_path() -> Path:
    """Locate `eval/thresholds.json`.

    Honours `MOODBOARD_THRESHOLDS` when set, so a caller can point the engine at a different
    registry deliberately. Otherwise walks up from this file until it finds `eval/thresholds.json`.
    Raises rather than falling back to a built-in default, because an engine that cannot find its
    registry and silently proceeds on numbers compiled into its own source is the exact failure
    this arrangement exists to prevent.

    The walk covers all three layouts, and the first parent it checks is what makes the third
    work. A source checkout and an editable install both resolve to the repository's own
    `eval/thresholds.json`, since neither has a `moodboard/eval/` directory to stop at. A wheel
    install stops at the first parent, because the build force-includes the registry to
    `moodboard/eval/thresholds.json`; before that, an installed copy raised FileNotFoundError on
    every abstention call, which was the correct direction to fail in and still unusable.
    """
    override = os.environ.get(_THRESHOLDS_ENV_VAR)
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(
                f"{_THRESHOLDS_ENV_VAR} points at {path}, which is not a readable file"
            )
        return path

    for parent in Path(__file__).resolve().parents:
        candidate = parent / _THRESHOLDS_RELATIVE_PATH
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "could not locate eval/thresholds.json above "
        f"{Path(__file__).resolve()}; set {_THRESHOLDS_ENV_VAR} to its path"
    )


def load_abstention_thresholds(path: Path | str | None = None) -> AbstentionThresholds:
    """Read the pre-registered abstention thresholds from `eval/thresholds.json`.

    The file is read on every call rather than cached, so the registry stays the single live
    source of these numbers. Callers that score many candidates should load once and pass the
    result down; the checks below all accept it.
    """
    resolved = Path(path) if path is not None else _default_thresholds_path()
    with resolved.open(encoding="utf-8") as handle:
        registry = json.load(handle)

    abstention = registry["abstention"]
    far_outlier = abstention.get("far_outlier", {})
    if "iqr_multiplier" in far_outlier:
        multiplier = float(far_outlier["iqr_multiplier"])
        multiplier_source = str(resolved)
    else:
        multiplier = _ADR_0004_IQR_MULTIPLIER
        multiplier_source = _ADR_0004_IQR_MULTIPLIER_SOURCE

    finest = {
        int(size): float(value)
        for size, value in registry["score_semantics"]["finest_expressible_by_board_size"].items()
    }

    must_fire = abstention["must_fire"]
    quiet = abstention["must_stay_quiet"]

    return AbstentionThresholds(
        path=resolved,
        far_outlier_iqr_multiplier=multiplier,
        far_outlier_iqr_multiplier_source=multiplier_source,
        finest_expressible_by_board_size=finest,
        must_fire_cases=tuple(must_fire["constructed_cases"]),
        required_fire_rate=float(must_fire["required_fire_rate"]),
        must_detect_and_score=abstention["must_detect_and_score"],
        must_stay_quiet_population=tuple(quiet["population"]),
        max_false_abstention_rate=float(quiet["max_false_abstention_rate"]),
    )


def verify_resolution_table(thresholds: AbstentionThresholds) -> None:
    """Check the computed resolution floor against the registry's rounded table.

    The trigger is always the exact 1/(n+1); this compares it against
    `score_semantics.finest_expressible_by_board_size` at that table's own precision, so a
    divergence between the two representations is caught rather than silently tolerated. Raises
    `ValueError` naming both values on disagreement.
    """
    for board_size, registered in thresholds.finest_expressible_by_board_size.items():
        computed = 1.0 / (board_size + 1)
        decimals = _decimal_places(registered)
        if round(computed, decimals) != round(registered, decimals):
            raise ValueError(
                f"resolution floor for a board of {board_size} references is "
                f"{computed!r}, which does not agree with the registered "
                f"{registered!r} at {decimals} decimal places ({thresholds.path})"
            )


def _decimal_places(value: float) -> int:
    text = repr(float(value))
    if "e" in text or "E" in text or "." not in text:
        return 4
    return len(text.split(".", 1)[1])


def _validate_alpha(requested_alpha: float) -> float:
    alpha = float(requested_alpha)
    if not np.isfinite(alpha) or not (0.0 < alpha < 1.0):
        raise ValueError(
            f"requested_alpha must lie strictly between 0 and 1, matching the report schema's "
            f"alpha type; got {requested_alpha!r}"
        )
    return alpha


def _format_percent(value: float) -> str:
    """Render a proportion as the percentage a designer would say out loud.

    Rounds to a whole percent when the value is within a quarter of a point of one, which turns
    1/11 into "9%" and 1/9 into "11%", matching ADR-0004's own worked sentence. Otherwise keeps
    one decimal, so a request of 0.025 reads as "2.5%" rather than losing its meaning to rounding.
    """
    percent = value * 100.0
    if abs(percent - round(percent)) < 0.25:
        return f"{round(percent):d}%"
    return f"{percent:.1f}%"


def category_n_eff(
    partition: CategoryPartition, board_duplicate_groups: Iterable[Sequence[int]]
) -> float:
    """The Kish effective size of the candidate's own category.

    `board_duplicate_groups` is `conformal.duplicate_groups(reference_embeddings, dup_cut)` over
    the whole board, exactly as it returns it. This restricts each group to the references sharing
    the candidate's category, drops the groups left empty, and takes `kish_n_eff` over what
    remains, which is what `INTERFACES.md` defines `n_eff_local` to be. Taking the board-wide
    groups rather than a pre-restricted list is deliberate: the restriction is the step a caller
    can get wrong, and there is no reason for two callers to write it twice.
    """
    members = set(partition.candidate_category_members)
    sizes = [
        len([index for index in group if index in members]) for group in board_duplicate_groups
    ]
    return kish_n_eff([size for size in sizes if size > 0])


def check_resolution(
    partition: CategoryPartition,
    requested_alpha: float,
    board_duplicate_groups: Iterable[Sequence[int]] | None = None,
) -> AbstentionVerdict | None:
    """ADR-0004 rules 1 and 2: refuse a threshold the candidate's category cannot express.

    `n_local` is `len(partition.candidate_category_members)`, the references sharing the
    candidate's category, and equals the board's reference count on a single-look board. The
    category's finest expressible p-value is 1/(n_local + 1), and its honourable floor under
    ADR-0005 is 1/(n_eff_local + 1), which is the same number when the category holds no
    near-duplicates and is larger when it does. Abstains when `requested_alpha` is strictly below
    that floor, so a request exactly equal to it is honoured, per ADR-0004's "The comparison is
    strict".

    `board_duplicate_groups` is `conformal.duplicate_groups` over the whole board. Omitting it
    asserts that every reference in the category is distinct, which makes n_eff_local equal
    n_local; the measurement records which of the two happened under `n_eff_local_source`, so a
    report never presents an assumed floor as a measured one.

    The reason is "resolution" when the candidate's category holds every reference on the board,
    and "multi_modality" when the board was partitioned and the candidate was compared against one
    part of it. Detecting several sub-looks is not by itself a refusal, per ADR-0004: this returns
    None on a multi-look board whose relevant sub-look can express the request.

    measurement carries n_local, n_eff_local, n_eff_local_source, n_references, n_categories,
    resolution_alpha (1/(n_local + 1)), supported_alpha (the governing floor, 1/(n_eff_local + 1)),
    binding_floor, requested_alpha and category_id.

    `binding_floor` names which of ADR-0004's two floors is the larger, and so which one bound:
    "effective" when supported_alpha > resolution_alpha, "achievability" when they coincide. It
    is emitted because a refusal carrying only reason="resolution" cannot say which arm fired,
    and the two arms mean different things to a person holding the board: one asks for more
    files, the other for more distinct ones. Read it as a statement about the FLOORS and not
    about the request: "effective" says the duplicate-aware floor is the higher of the two, not
    that the file count alone would have honoured the request. Both arms refuse together
    whenever requested_alpha also falls below resolution_alpha, and this field still reads
    "effective" there, correctly, because it reports which floor is binding.
    """
    alpha = _validate_alpha(requested_alpha)

    n_local = len(partition.candidate_category_members)
    n_references = sum(len(members) for members in partition.all_categories.values())
    n_categories = len(partition.all_categories)

    if board_duplicate_groups is None:
        n_eff_local = float(n_local)
        n_eff_source = "assumed_distinct"
    else:
        n_eff_local = category_n_eff(partition, board_duplicate_groups)
        n_eff_source = "duplicate_groups"

    resolution_alpha = 1.0 / (n_local + 1)
    supported_alpha = 1.0 / (n_eff_local + 1)

    if alpha >= supported_alpha:
        return None

    is_whole_board = n_local == n_references
    reason: AbstentionReason = "resolution" if is_whole_board else "multi_modality"

    binding_floor = "effective" if supported_alpha > resolution_alpha else "achievability"

    measurement = {
        "n_local": n_local,
        "n_eff_local": n_eff_local,
        "n_eff_local_source": n_eff_source,
        "n_references": n_references,
        "n_categories": n_categories,
        "resolution_alpha": resolution_alpha,
        "supported_alpha": supported_alpha,
        "binding_floor": binding_floor,
        "requested_alpha": alpha,
        "category_id": partition.category_id,
    }

    supported_text = _format_percent(supported_alpha)
    requested_text = _format_percent(alpha)
    if is_whole_board:
        explanation = (
            f"This board has {n_local} references, so the finest distinction it can express is "
            f"about {supported_text}, and you asked for {requested_text}. Add more references, "
            f"or ask for {supported_text} or coarser."
        )
    else:
        explanation = (
            f"This board holds {n_categories} distinct looks, and the candidate belongs to the "
            f"one made of {n_local} of its {n_references} references. That look on its own can "
            f"express distinctions no finer than about {supported_text}, and you asked for "
            f"{requested_text}. Add more references in that look, or ask for {supported_text} "
            f"or coarser."
        )

    if n_eff_source == "duplicate_groups" and n_eff_local < n_local:
        explanation += (
            f" Some of those {n_local} references are near-duplicates of each other, so they "
            f"carry about as much information as {n_eff_local:.1f} distinct ones."
        )

    return AbstentionVerdict(reason=reason, measurement=measurement, explanation=explanation)


def check_multi_modality(
    partition: CategoryPartition,
    requested_alpha: float,
    board_duplicate_groups: Iterable[Sequence[int]] | None = None,
) -> AbstentionVerdict | None:
    """ADR-0004 rule 2, under its own name because the record names it separately.

    Delegates to `check_resolution` and returns exactly what it returns. It is not a second
    implementation of the test: ADR-0004 defines this rule's trigger as rule 1 applied to the
    candidate's own category, so the two are one comparison and writing it twice is how two
    formulas drift apart. On a single-look board this legitimately returns a "resolution" verdict
    or None, never a "multi_modality" one, because there is only one category to check.
    """
    return check_resolution(partition, requested_alpha, board_duplicate_groups)


def check_far_outlier(
    candidate_alpha: float,
    board_reference_alphas: Sequence[float],
    thresholds: AbstentionThresholds | None = None,
) -> AbstentionVerdict | None:
    """ADR-0004 rule 3: refuse when the candidate is a gross outlier against the whole board.

    `board_reference_alphas` are the references' own nonconformity values from the same
    augmented-bag computation `conformal_p_value` already ran for this candidate, and
    `candidate_alpha` is the candidate's value from that same computation. Each of those values is
    already leave-one-out in the sense the rule means, since `nonconformity_scores` excludes a
    point from its own neighbour pool. This is a rule rather than a mechanism, so nothing here
    starts a second pass over the board.

    Abstains when

        candidate_alpha > max(board_reference_alphas) + multiplier * IQR(board_reference_alphas)

    the conventional Tukey far-outlier rule. The multiplier is 1.5 unless `eval/thresholds.json`
    carries `abstention.far_outlier.iqr_multiplier`; see the module docstring. The interquartile
    range uses the type-7 quantile convention, which is numpy's default and the convention
    `conformal.py` already uses for its intervals.

    The explanation states that the candidate is nothing like these references, in plain language.
    It never mentions a medium, a file type or a format: ADR-0004 withdrew that framing on the
    grounds that this machinery measures distance and does not observe medium at all.

    measurement carries candidate_alpha, reference_max, reference_iqr, threshold, iqr_multiplier
    and iqr_multiplier_source.
    """
    alphas = np.asarray(list(board_reference_alphas), dtype=np.float64)
    if alphas.size == 0:
        raise ValueError(
            "check_far_outlier needs at least one reference nonconformity value; an empty board "
            "has no scale to judge an outlier against"
        )
    if not np.all(np.isfinite(alphas)):
        raise ValueError("board_reference_alphas must all be finite")
    candidate = float(candidate_alpha)
    if not np.isfinite(candidate):
        raise ValueError(f"candidate_alpha must be finite; got {candidate_alpha!r}")

    if thresholds is None:
        thresholds = load_abstention_thresholds()
    multiplier = thresholds.far_outlier_iqr_multiplier

    reference_max = float(alphas.max())
    q1 = float(np.quantile(alphas, 0.25, method="linear"))
    q3 = float(np.quantile(alphas, 0.75, method="linear"))
    reference_iqr = q3 - q1
    threshold = reference_max + multiplier * reference_iqr

    if candidate <= threshold:
        return None

    measurement = {
        "candidate_alpha": candidate,
        "reference_max": reference_max,
        "reference_iqr": reference_iqr,
        "threshold": threshold,
        "iqr_multiplier": multiplier,
        "iqr_multiplier_source": thresholds.far_outlier_iqr_multiplier_source,
    }
    explanation = (
        "This asset is nothing like the references on this board. Its distance from the board "
        f"is {candidate:.3f}, while the most unusual reference on the board sits at "
        f"{reference_max:.3f} and the line for a far outlier is {threshold:.3f}. Comparing it "
        "with these references would produce a number without a meaning, so no score is given."
    )
    return AbstentionVerdict(reason="far_outlier", measurement=measurement, explanation=explanation)


def evaluate_abstention(
    partition: CategoryPartition,
    requested_alpha: float,
    candidate_alpha: float,
    board_reference_alphas: Sequence[float],
    board_duplicate_groups: Iterable[Sequence[int]] | None = None,
    thresholds: AbstentionThresholds | None = None,
) -> AbstentionVerdict | None:
    """Run the rules in order and return the one verdict the report carries, or None to score.

    `check_resolution` runs first. If it abstains, that verdict is returned and the far-outlier
    rule is not consulted: an asset that cannot be scored at the requested resolution is reported
    under exactly one reason. Only when the resolution check returns None does `check_far_outlier`
    run, and its own None means score the asset.

    ADR-0004 does not state this ordering, and it is decided here: a report needs exactly one
    abstention reason per asset, and the resolution check is the cheaper and more specific of the
    two, so it is asked first. The choice is visible in the report, since the reason field names
    which rule fired.
    """
    resolution = check_resolution(partition, requested_alpha, board_duplicate_groups)
    if resolution is not None:
        return resolution
    return check_far_outlier(candidate_alpha, board_reference_alphas, thresholds)

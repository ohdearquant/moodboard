"""Property tests for the report contract, generated from seeded randomness rather than one
hand-written fixture per case.

``test_report.py`` already exercises the contract with hand-built examples. The tests here
instead build many structurally varied reports from a seed and check that the same three
properties hold across all of them, which is what a single example cannot show: that the
schema invariant holds on every emitted report regardless of how many assets are scored or
abstained, that the axis-vocabulary check fires on any way of corrupting an asset's axis keys
and not just the one way a hand-written test happened to pick, and that the self-validator
(``validate_report``, the function the engine calls on its own output before writing) refuses
every one of several independent kinds of schema-breaking damage. Every generator here is a
seeded ``numpy`` random generator over plain Python values; there are no images, no encoders,
and no dataset download anywhere in this file.

Each mutation test validates the unmutated report first, in the same test, and asserts the
mutated report is not equal to the original. A validator that rejects everything and a
validator that rejects the right thing look identical from a single failing case; checking the
baseline and checking that the mutation actually changed something both close off that gap.
"""

from __future__ import annotations

import dataclasses

import jsonschema
import numpy as np
import pytest

from moodboard.report import (
    AXIS_ORDER,
    AbstainedAsset,
    Board,
    BoardFit,
    BoardStats,
    Category,
    Comparisons,
    EngineProvenance,
    Exemplar,
    Interval,
    IntervalMethod,
    Leverage,
    ModelProvenance,
    Provenance,
    ReferenceEntry,
    Report,
    Representation,
    ScoredAsset,
    StyleModelInfo,
    Thumbnail,
    Tightness,
    validate_axis_vocabulary,
    validate_report,
)

# ---------------------------------------------------------------------------
# A seeded, schema-conforming report generator
# ---------------------------------------------------------------------------

_MIMES = ("image/jpeg", "image/png", "image/webp")
_REASONS = ("resolution", "multi_modality", "far_outlier")

# Board.representation.axes may shrink at runtime per ADR-0003; the generator draws from
# several vocabularies so the property is checked against more than the three-axis case
# every other test in this suite happens to use.
_AXIS_SETS = (AXIS_ORDER, ("palette", "tone"), ("tone", "composition"), ("palette",))


def _hex64(rng: np.random.Generator) -> str:
    return "".join(f"{b:02x}" for b in rng.integers(0, 256, size=32, dtype=np.uint8))


def _rfc3339(rng: np.random.Generator) -> str:
    """A valid RFC 3339 timestamp, varying precision and offset style across calls."""
    year = int(rng.integers(2020, 2031))
    month = int(rng.integers(1, 13))
    day = int(rng.integers(1, 29))
    hour = int(rng.integers(0, 24))
    minute = int(rng.integers(0, 60))
    second = int(rng.integers(0, 60))
    stamp = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"
    if rng.random() < 0.5:
        stamp += f".{int(rng.integers(0, 1000)):03d}"
    if rng.random() < 0.5:
        stamp += "Z"
    else:
        sign = "+" if rng.random() < 0.5 else "-"
        offset_minute = (0, 15, 30, 45)[int(rng.integers(0, 4))]
        stamp += f"{sign}{int(rng.integers(0, 13)):02d}:{offset_minute:02d}"
    return stamp


def _p_value(rng: np.random.Generator) -> float:
    """Strictly positive and at most 1, the ``pValue`` schema bound."""
    return float(rng.uniform(1e-6, 1.0))


def _unit_interval(rng: np.random.Generator) -> float:
    return float(rng.uniform(0.0, 1.0))


def _axes_for(axis_order: tuple[str, ...], rng: np.random.Generator, style: float | None):
    axes: dict[str, float | None] = {name: _unit_interval(rng) for name in axis_order}
    axes["style"] = style
    return axes


def _reference(rng: np.random.Generator, index: int) -> ReferenceEntry:
    return ReferenceEntry(
        reference_id=f"ref{index}",
        content_sha256=_hex64(rng),
        mime=_MIMES[int(rng.integers(0, len(_MIMES)))],
        width=int(rng.integers(1, 4000)),
        height=int(rng.integers(1, 4000)),
        thumbnail=Thumbnail(
            mime="image/webp",
            width=int(rng.integers(1, 512)),
            height=int(rng.integers(1, 512)),
            data_base64="cmFuZG9t",
        ),
    )


def _exemplars(rng: np.random.Generator, reference_ids: list[str]) -> tuple[Exemplar, ...]:
    if not reference_ids:
        return ()
    n = int(rng.integers(0, min(3, len(reference_ids)) + 1))
    chosen = [reference_ids[int(rng.integers(0, len(reference_ids)))] for _ in range(n)]
    return tuple(
        Exemplar(reference_id=rid, similarity=float(rng.uniform(-1.0, 1.0))) for rid in chosen
    )


def _scored_asset(rng, asset_id, category_id, axis_order, reference_ids, rank) -> ScoredAsset:
    low = float(rng.uniform(0.0, 0.5))
    high = float(rng.uniform(low, 1.0))
    return ScoredAsset(
        state="scored",
        asset_id=asset_id,
        source=f"assets/{asset_id}.jpg",
        category_id=category_id,
        n_local=int(rng.integers(1, 40)),
        score=_p_value(rng),
        interval=Interval(
            low=low, high=high, level=float(rng.uniform(0.01, 0.99)), method="loo-jackknife-plus"
        ),
        rank=rank,
        axes=_axes_for(axis_order, rng, _p_value(rng)),
        exemplars=_exemplars(rng, reference_ids),
        flags=() if rng.random() < 0.6 else ("low_confidence",),
    )


def _abstained_asset(rng, asset_id, category_id, axis_order, reference_ids) -> AbstainedAsset:
    return AbstainedAsset(
        state="abstained",
        asset_id=asset_id,
        source=f"assets/{asset_id}.jpg",
        reason=_REASONS[int(rng.integers(0, len(_REASONS)))],
        explanation=f"synthetic explanation for {asset_id}",
        measurement={
            "n_local": int(rng.integers(1, 12)),
            "n_eff_local": float(rng.uniform(1.0, 10.0)),
            "supported_alpha": float(rng.uniform(0.01, 0.5)),
            "requested_alpha": float(rng.uniform(0.01, 0.5)),
        },
        category_id=category_id,
        axes=_axes_for(axis_order, rng, None),
        exemplars=_exemplars(rng, reference_ids),
        flags=("abstained",),
    )


def _random_report(
    seed: int,
    *,
    axis_order: tuple[str, ...] = AXIS_ORDER,
    n_scored: int,
    n_abstained: int,
) -> Report:
    """A schema-conforming report built entirely from ``seed``, with a chosen asset mix."""
    rng = np.random.default_rng(seed)
    n_refs = int(rng.integers(1, 7))
    references = tuple(_reference(rng, i) for i in range(n_refs))
    reference_ids = [r.reference_id for r in references]
    category_id = "c0"

    assets: list[ScoredAsset | AbstainedAsset] = [
        _scored_asset(rng, f"s{i}", category_id, axis_order, reference_ids, rank=i + 1)
        for i in range(n_scored)
    ] + [
        _abstained_asset(rng, f"x{i}", category_id, axis_order, reference_ids)
        for i in range(n_abstained)
    ]
    rng.shuffle(assets)

    board = Board(
        id=_hex64(rng),
        name=f"board-{seed}",
        n_references=n_refs,
        n_eff=float(rng.uniform(1.0, max(1.0, float(n_refs)))),
        requested_alpha=float(rng.uniform(0.01, 0.4)),
        supported_alpha=float(rng.uniform(0.01, 0.4)),
        built_at=_rfc3339(rng),
        representation=Representation(
            style=StyleModelInfo(model="classical-v1", revision="1", dim=int(rng.integers(1, 512))),
            axes=tuple(axis_order),
        ),
        fit=BoardFit(
            metric="cosine",
            k=int(rng.integers(1, 6)),
            cluster_cut=float(rng.uniform(0.0, 2.0)),
            dup_cut=float(rng.uniform(0.0, 2.0)),
            interval=IntervalMethod(
                method="loo-jackknife-plus", replicates=None, seed=int(rng.integers(0, 1_000_000))
            ),
        ),
        categories=(
            Category(category_id=category_id, n_local=n_refs, member_ids=tuple(reference_ids)),
        ),
    )

    board_stats = BoardStats(
        tightness=Tightness(
            loo_mean=float(rng.uniform(-1.0, 1.0)),
            loo_sd=float(rng.uniform(0.0, 1.0)),
            loo_quantiles={
                "p10": float(rng.uniform(-1.0, 1.0)),
                "p50": float(rng.uniform(-1.0, 1.0)),
                "p90": float(rng.uniform(-1.0, 1.0)),
            },
        ),
        leverage=tuple(
            Leverage(reference_id=rid, delta_tightness=float(rng.uniform(-1.0, 1.0)), rank=i + 1)
            for i, rid in enumerate(reference_ids)
        ),
        flags=(),
    )

    scored_ids = [a.asset_id for a in assets if a.state == "scored"]
    ties: tuple[tuple[str, str], ...] = ()
    if len(scored_ids) >= 2:
        ties = ((scored_ids[0], scored_ids[1]),)

    return Report(
        schema_version="1.0",
        board=board,
        board_stats=board_stats,
        references=references,
        assets=tuple(assets),
        comparisons=Comparisons(ties=ties, note="synthetic tie note"),
        provenance=Provenance(
            engine=EngineProvenance(name="moodboard", version="0.1.0"),
            model=ModelProvenance(repo="local/classical", revision="1", sha256=_hex64(rng)),
            command="moodboard rank brand.mb assets/",
            seed=int(rng.integers(0, 1_000_000)),
            created_at=_rfc3339(rng),
        ),
    )


# ---------------------------------------------------------------------------
# Property 1: the schema invariant holds on every emitted report, both states
# ---------------------------------------------------------------------------


def test_schema_invariant_holds_on_random_reports_of_scored_assets_only():
    for seed in range(40):
        axis_order = _AXIS_SETS[seed % len(_AXIS_SETS)]
        report = _random_report(
            seed, axis_order=axis_order, n_scored=1 + seed % 5, n_abstained=0
        )
        assert {asset.state for asset in report.assets} == {"scored"}
        validate_report(report)


def test_schema_invariant_holds_on_random_reports_of_abstained_assets_only():
    for seed in range(40):
        axis_order = _AXIS_SETS[seed % len(_AXIS_SETS)]
        report = _random_report(
            seed, axis_order=axis_order, n_scored=0, n_abstained=1 + seed % 5
        )
        assert {asset.state for asset in report.assets} == {"abstained"}
        validate_report(report)


def test_schema_invariant_holds_on_random_reports_mixing_both_asset_states():
    for seed in range(60):
        axis_order = _AXIS_SETS[seed % len(_AXIS_SETS)]
        report = _random_report(
            seed,
            axis_order=axis_order,
            n_scored=1 + seed % 4,
            n_abstained=1 + (seed * 7) % 4,
        )
        assert {asset.state for asset in report.assets} == {"scored", "abstained"}
        validate_report(report)


# ---------------------------------------------------------------------------
# Property 2: the axis-vocabulary check fires on a malformed report
# ---------------------------------------------------------------------------


def test_axis_vocabulary_check_fires_on_every_kind_of_random_axis_corruption():
    mutation_kinds = ("drop_key", "add_key", "empty_axes", "rename_key")
    for seed in range(60):
        axis_order = _AXIS_SETS[seed % len(_AXIS_SETS)]
        report = _random_report(
            seed, axis_order=axis_order, n_scored=1 + seed % 3, n_abstained=1 + seed % 3
        )
        # Baseline: the unmutated report holds the invariant in the same test.
        validate_axis_vocabulary(report)

        rng = np.random.default_rng(seed + 10_000)
        target_index = int(rng.integers(0, len(report.assets)))
        target = report.assets[target_index]
        axes = dict(target.axes)
        kind = mutation_kinds[seed % len(mutation_kinds)]
        if kind == "drop_key":
            keys = list(axes.keys())
            del axes[keys[int(rng.integers(0, len(keys)))]]
        elif kind == "add_key":
            axes[f"bogus_{seed}"] = 0.5
        elif kind == "empty_axes":
            axes = {}
        else:  # rename_key: the vocabulary is on the key set, so a rename is both a removal
            # of the old name and an addition of an unrecognised one.
            keys = list(axes.keys())
            key = keys[int(rng.integers(0, len(keys)))]
            axes[f"{key}_renamed"] = axes.pop(key)

        mutated_asset = dataclasses.replace(target, axes=axes)
        assets = list(report.assets)
        assets[target_index] = mutated_asset
        mutated_report = dataclasses.replace(report, assets=tuple(assets))
        assert mutated_report != report

        with pytest.raises(ValueError, match=target.asset_id):
            validate_axis_vocabulary(mutated_report)


# ---------------------------------------------------------------------------
# Property 3: the self-validator rejects an intentionally broken report
# ---------------------------------------------------------------------------


def _first_index(report: Report, state: str) -> int:
    for i, asset in enumerate(report.assets):
        if asset.state == state:
            return i
    raise AssertionError(f"no {state!r} asset in the generated report; check the seed ranges")


def _with_asset(report: Report, index: int, asset) -> Report:
    assets = list(report.assets)
    assets[index] = asset
    return dataclasses.replace(report, assets=tuple(assets))


def _break_score_zero(report: Report) -> Report:
    """A conformal p-value cannot be exactly zero."""
    i = _first_index(report, "scored")
    return _with_asset(report, i, dataclasses.replace(report.assets[i], score=0.0))


def _break_score_above_one(report: Report) -> Report:
    i = _first_index(report, "scored")
    return _with_asset(report, i, dataclasses.replace(report.assets[i], score=1.5))


def _break_rank_zero(report: Report) -> Report:
    """Rank is 1-based; a rank of zero is not a rank ADR-0002 defines."""
    i = _first_index(report, "scored")
    return _with_asset(report, i, dataclasses.replace(report.assets[i], rank=0))


def _break_interval_level_zero(report: Report) -> Report:
    i = _first_index(report, "scored")
    asset = report.assets[i]
    broken_interval = dataclasses.replace(asset.interval, level=0.0)
    return _with_asset(report, i, dataclasses.replace(asset, interval=broken_interval))


def _break_measurement_empty(report: Report) -> Report:
    """An abstention with no measurement at all carries no evidence for its own reason."""
    i = _first_index(report, "abstained")
    return _with_asset(report, i, dataclasses.replace(report.assets[i], measurement={}))


def _break_asset_id_empty(report: Report) -> Report:
    return _with_asset(report, 0, dataclasses.replace(report.assets[0], asset_id=""))


def _break_negative_n_eff(report: Report) -> Report:
    """n_eff is an effective sample size; it cannot fall below 1."""
    return dataclasses.replace(report, board=dataclasses.replace(report.board, n_eff=0.5))


def _break_alpha_zero(report: Report) -> Report:
    board = dataclasses.replace(report.board, requested_alpha=0.0)
    return dataclasses.replace(report, board=board)


def _break_fit_k_zero(report: Report) -> Report:
    fit = dataclasses.replace(report.board.fit, k=0)
    return dataclasses.replace(report, board=dataclasses.replace(report.board, fit=fit))


def _break_bad_board_id(report: Report) -> Report:
    """board.id is a sha256 hash; this is neither hex nor 64 characters."""
    return dataclasses.replace(report, board=dataclasses.replace(report.board, id="not-a-sha256"))


def _break_bad_timestamp(report: Report) -> Report:
    provenance = dataclasses.replace(report.provenance, created_at="7 August 2026")
    return dataclasses.replace(report, provenance=provenance)


def _break_reference_width_zero(report: Report) -> Report:
    refs = list(report.references)
    refs[0] = dataclasses.replace(refs[0], width=0)
    return dataclasses.replace(report, references=tuple(refs))


_BREAKERS = (
    _break_score_zero,
    _break_score_above_one,
    _break_rank_zero,
    _break_interval_level_zero,
    _break_measurement_empty,
    _break_asset_id_empty,
    _break_negative_n_eff,
    _break_alpha_zero,
    _break_fit_k_zero,
    _break_bad_board_id,
    _break_bad_timestamp,
    _break_reference_width_zero,
)


def test_self_validator_rejects_every_kind_of_intentionally_broken_report():
    for seed in range(4 * len(_BREAKERS)):
        report = _random_report(seed, n_scored=1 + seed % 3, n_abstained=1 + (seed * 5) % 3)
        # Baseline: the unmutated report passes its own validator in the same test, so a
        # failure below is known to come from the mutation and not from the generator.
        validate_report(report)

        breaker = _BREAKERS[seed % len(_BREAKERS)]
        broken = breaker(report)
        assert broken != report, "the mutation must change the report, or it proves nothing"

        with pytest.raises(jsonschema.ValidationError):
            validate_report(broken)

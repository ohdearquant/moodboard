"""Tests for the command line surface.

Every image here is synthesised from a seeded generator. Nothing downloads a dataset and
nothing depends on a checkpoint, so the whole file runs offline and deterministically.

Three habits run through these tests.

The reference board is *constructed to a stated property* rather than drawn and hoped for.
`_reference_arrays` draws a pool and greedily keeps images whose pairwise cosine distance
sits inside a window: far enough apart that no pair falls under the near-duplicate cut, close
enough that they read as one look. It raises if it cannot reach the requested count, so a
fixture that stopped producing the board it describes fails loudly rather than quietly
testing a different board.

The acceptance numbers come from `eval/thresholds.json` at runtime. The board built here is
ten references, which is the size the registry pins in three separate places, so the alphas
in the fires-and-stays-quiet tests are read out of the registry rather than restated.

Each test states the regime it believes it is in before asserting the behaviour. A board's
resolution floor is a measured property of the images, so a test requesting an alpha above
the floor asserts that the floor really is below it. Without that, a drift in the generator
would silently turn a scored-path test into an abstained-path test that still passes.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from moodboard import cli
from moodboard.abstain import load_abstention_thresholds
from moodboard.board import board_hash, read_board
from moodboard.conformal import conformal_p_value, duplicate_groups, kish_n_eff
from moodboard.encoders import ClassicalEncoder
from moodboard.report import AXIS_ORDER, from_json_dict, validate_report

IMAGE_SIZE = (96, 128)

# The board every test in this file is built on. Ten references is the size
# eval/thresholds.json names in its must-fire case, its quiet population and its
# finest-expressible table, so this one board exercises all three.
BOARD_SIZE = 10

# The separation window the reference set is selected into. The lower bound is above the
# near-duplicate cut of 0.05, so every reference is its own duplicate group and n_eff equals
# n. The upper bound keeps the whole set inside one category under the 0.35 average-linkage
# cut, so the board is single-look and rule 1's reason is "resolution" rather than
# "multi_modality".
SEPARATION_MIN = 0.06
SEPARATION_MAX = 0.60


def _draw(rng: np.random.Generator, family: str = "warm") -> np.ndarray:
    """One synthetic image from a named look.

    A look is a hue direction plus a range of luminance levels, contrasts and spatial
    frequencies. Two images from the same look sit near each other in the classical encoder's
    space and two from different looks sit far apart.
    """
    height, width = IMAGE_SIZE
    rows = np.linspace(0, 1, height)[:, None]
    columns = np.linspace(0, 1, width)[None, :]

    hues = {"warm": np.array([0.92, 0.52, 0.26]), "cool": np.array([0.22, 0.44, 0.92])}
    hue = np.clip(hues[family] * rng.uniform(0.7, 1.15), 0.04, 1.0)

    angle = rng.uniform(0, np.pi)
    ramp = np.cos(angle) * rows + np.sin(angle) * columns
    ramp = (ramp - ramp.min()) / (ramp.max() - ramp.min())
    texture = 0.5 + 0.5 * np.sin(rng.uniform(2, 12) * np.pi * (rows + columns) + rng.uniform(0, 6))

    level = rng.uniform(0.28, 0.68)
    contrast = rng.uniform(0.15, 0.45)
    field = level + contrast * (0.6 * ramp + 0.4 * texture - 0.5)
    return (np.clip(field[..., None] * hue[None, None, :], 0, 1) * 255).astype(np.uint8)


def _reference_arrays(
    count: int, seed: int, family: str = "warm", pool: int = 500
) -> list[np.ndarray]:
    """A reference set whose pairwise distances all lie in the separation window.

    Raises rather than returning a short set: a board of eight images where ten were asked for
    is a different board, and every test below reads its alpha off the number ten.
    """
    rng = np.random.default_rng(seed)
    drawn = [_draw(rng, family) for _ in range(pool)]
    embeddings = ClassicalEncoder().embed(drawn).astype(np.float64)

    kept: list[np.ndarray] = []
    kept_vectors: list[np.ndarray] = []
    for image, vector in zip(drawn, embeddings, strict=True):
        if kept_vectors:
            distances = 1.0 - np.asarray(kept_vectors) @ vector
            if distances.min() < SEPARATION_MIN or distances.max() > SEPARATION_MAX:
                continue
        kept.append(image)
        kept_vectors.append(vector)
        if len(kept) == count:
            return kept
    raise AssertionError(
        f"the generator found only {len(kept)} of {count} references separated into "
        f"[{SEPARATION_MIN}, {SEPARATION_MAX}] out of a pool of {pool}"
    )


def _write_images(directory: Path, arrays: list[np.ndarray], prefix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for index, array in enumerate(arrays):
        Image.fromarray(array).save(directory / f"{prefix}_{index:02d}.png")
    return directory


def _candidate_dir(root: Path, count: int, seed: int, family: str = "warm") -> Path:
    rng = np.random.default_rng(seed)
    return _write_images(
        root / f"candidates_{family}_{count}_{seed}",
        [_draw(rng, family) for _ in range(count)],
        "cand",
    )


def _spread_candidate_arrays(
    reference_dir: Path, distinct: int = 4, seed: int = 99, pool: int = 200
) -> list[np.ndarray]:
    """Candidates chosen to realise several different scores, plus one exact repeat.

    A candidate drawn at random from the board's own look is usually the most typical thing in
    the augmented bag, so it scores 1.0, and a whole candidate set drawn that way scores 1.0
    for every member. That set cannot distinguish a report whose `n_local` is the reference
    count from one carrying the reference count plus one, because 1.0 is a whole multiple of
    both grids, and it cannot exercise competition ranking either, since every rank is 1.

    So the pool is scored first and one image is kept per distinct score, highest first, with
    a second image at the top score so an exact tie exists. Raises rather than returning a
    narrower set: a set with two distinct scores is a different fixture from one with four.
    """
    reference_embeddings = _reference_embeddings(reference_dir)
    rng = np.random.default_rng(seed)
    drawn = [_draw(rng) for _ in range(pool)]
    embeddings = ClassicalEncoder().embed(drawn).astype(np.float64)

    by_score: dict[float, list[np.ndarray]] = {}
    for image, vector in zip(drawn, embeddings, strict=True):
        by_score.setdefault(round(conformal_p_value(reference_embeddings, vector), 9), []).append(
            image
        )

    ordered = sorted(by_score, reverse=True)
    if len(ordered) < distinct:
        raise AssertionError(
            f"the pool of {pool} candidates realised only {len(ordered)} distinct scores, "
            f"and this fixture needs {distinct}"
        )
    if len(by_score[ordered[0]]) < 2:
        raise AssertionError("the pool holds no two candidates sharing the top score")

    chosen = [by_score[score][0] for score in ordered[:distinct]]
    chosen.insert(1, by_score[ordered[0]][1])
    return chosen


def _reference_embeddings(reference_dir: Path) -> np.ndarray:
    paths = sorted(reference_dir.glob("*.png"))
    arrays = [np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8) for path in paths]
    return ClassicalEncoder().embed(arrays).astype(np.float64)


def _measured_n_eff(reference_dir: Path) -> float:
    """Re-run the engine's own duplicate grouping over the images on disk.

    This is what lets a test state its regime: it measures rather than predicts, so it moves
    when the generator moves.
    """
    groups = duplicate_groups(_reference_embeddings(reference_dir), 0.05)
    return kish_n_eff([len(group) for group in groups])


def _registry_alpha_for_quiet_board(count: int) -> float:
    thresholds = load_abstention_thresholds()
    row = next(
        entry
        for entry in thresholds.must_stay_quiet_population
        if entry["n"] == count and entry["look"] == "single"
    )
    return float(row["alpha"])


def _registry_alpha_for_resolution_case() -> tuple[int, float, str]:
    thresholds = load_abstention_thresholds()
    case = next(
        entry
        for entry in thresholds.must_fire_cases
        if entry["case"] == "threshold_below_resolution"
    )
    return int(case["n"]), float(case["alpha"]), str(case["expect_reason"])


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Fixtures: one board and one ranking, reused by every assertion about a report
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workspace(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("moodboard_cli")


@pytest.fixture(scope="module")
def reference_dir(workspace: Path) -> Path:
    return _write_images(
        workspace / "references",
        _reference_arrays(BOARD_SIZE, seed=20260807),
        "ref",
    )


@pytest.fixture(scope="module")
def board_path(workspace: Path, reference_dir: Path) -> Path:
    path = workspace / "brand.mb"
    code, _, err = _run(["build", str(reference_dir), "-o", str(path)])
    assert code == 0, err
    return path


@pytest.fixture(scope="module")
def ranked(workspace: Path, reference_dir: Path, board_path: Path) -> dict:
    """One ranking at the alpha the registry says a ten-reference board should stay quiet at."""
    output = workspace / "report.json"
    candidates = _write_images(
        workspace / "candidates_spread", _spread_candidate_arrays(reference_dir), "cand"
    )
    code, out, err = _run(
        [
            "rank",
            str(candidates),
            "-b",
            str(board_path),
            "-r",
            str(reference_dir),
            "-o",
            str(output),
            "--alpha",
            str(_registry_alpha_for_quiet_board(BOARD_SIZE)),
        ]
    )
    assert code == 0, err
    return {
        "path": output,
        "document": json.loads(output.read_text(encoding="utf-8")),
        "stdout": out,
    }


# ---------------------------------------------------------------------------
# The fixture's own regime
# ---------------------------------------------------------------------------


def test_the_reference_board_is_the_one_the_registry_describes(reference_dir: Path):
    """Ten distinct references, so n_eff equals n and the floor is the registered 0.0909.

    This is the contract's own named property, that n_eff equals n when every reference is
    distinct, checked on the board the rest of the file is built from. It also pins the
    fixture: every alpha below is read off the registry's ten-reference rows, which only
    describe this board if the board really has ten distinct references.
    """
    n_eff = _measured_n_eff(reference_dir)
    assert n_eff == BOARD_SIZE

    registered = load_abstention_thresholds().finest_expressible_by_board_size[BOARD_SIZE]
    assert round(1.0 / (n_eff + 1.0), 4) == registered


def test_the_quiet_alpha_is_expressible_and_the_firing_alpha_is_not(reference_dir: Path):
    """The two regimes the tests below rely on, asserted once, from the registry's numbers."""
    floor = 1.0 / (_measured_n_eff(reference_dir) + 1.0)
    quiet_alpha = _registry_alpha_for_quiet_board(BOARD_SIZE)
    board_size, firing_alpha, _ = _registry_alpha_for_resolution_case()

    assert board_size == BOARD_SIZE
    assert floor <= quiet_alpha
    assert floor > firing_alpha


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def test_build_writes_a_board_whose_id_is_the_board_hash(reference_dir: Path, board_path: Path):
    board = read_board(board_path)
    assert board.board_id == board_hash(
        board.reference_content_hashes,
        board.model_repo,
        board.model_revision,
        board.metric,
        board.k,
        board.cluster_cut,
        board.dup_cut,
    )
    assert board.reference_ids == tuple(sorted(p.name for p in reference_dir.glob("*.png")))
    assert board.model_repo == ClassicalEncoder.name
    assert board.model_revision == ClassicalEncoder.revision
    assert board.model_dim == ClassicalEncoder.dim
    assert board.k == min(5, BOARD_SIZE - 1)
    assert board.n_eff == BOARD_SIZE


def test_build_names_the_files_it_skipped(tmp_path: Path):
    rng = np.random.default_rng(5)
    directory = _write_images(tmp_path / "refs", [_draw(rng) for _ in range(3)], "ref")
    (directory / "notes.txt").write_text("not an image", encoding="utf-8")

    code, _, err = _run(["build", str(directory), "-o", str(tmp_path / "b.mb")])
    assert code == 0
    assert "notes.txt" in err
    board = read_board(tmp_path / "b.mb")
    assert board.reference_ids == ("ref_00.png", "ref_01.png", "ref_02.png")


def test_build_refuses_a_board_with_one_reference(tmp_path: Path):
    rng = np.random.default_rng(6)
    directory = _write_images(tmp_path / "one", [_draw(rng)], "ref")
    code, _, err = _run(["build", str(directory), "-o", str(tmp_path / "b.mb")])
    assert code == 1
    assert "at least two references" in err


def test_build_prints_the_source_of_every_fitting_parameter(reference_dir: Path, tmp_path: Path):
    """A number a run cannot attribute is the defect this printing exists to prevent."""
    code, out, _ = _run(["build", str(reference_dir), "-o", str(tmp_path / "b.mb")])
    assert code == 0
    for label in ("k cap", "cluster cut", "duplicate cut", "min category size", "interval level"):
        assert label in out
    assert "docs/adr/0004-abstention.md" in out
    assert "thresholds.json" in out


def test_fit_parameters_prefer_the_registry_over_the_records(tmp_path: Path):
    """The records are the fallback and not the source.

    A registry carrying a `fit` object has to win, or the arrangement would be a hard-coded
    constant wearing a comment about a file.
    """
    registry_path = load_abstention_thresholds().path
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    from_records = cli.load_fit_parameters(registry_path)
    assert from_records.cluster_cut == 0.35
    assert from_records.dup_cut == 0.05
    assert from_records.sources["cluster_cut"].endswith(".md")
    assert from_records.sources["interval_level"] == str(registry_path)

    registry["fit"] = {"cluster_cut": 0.41, "dup_cut": 0.07, "k_cap": 4, "min_category_size": 3}
    override = tmp_path / "thresholds.json"
    override.write_text(json.dumps(registry), encoding="utf-8")

    from_registry = cli.load_fit_parameters(override)
    assert from_registry.cluster_cut == 0.41
    assert from_registry.dup_cut == 0.07
    assert from_registry.k_cap == 4
    assert from_registry.min_category_size == 3
    assert from_registry.sources["cluster_cut"] == str(override)


# ---------------------------------------------------------------------------
# rank: the report it emits
# ---------------------------------------------------------------------------


def test_rank_emits_a_report_that_passes_its_own_validator(ranked: dict):
    report = from_json_dict(ranked["document"])
    validate_report(report)
    assert report.schema_version == "1.0"
    assert len(report.assets) == 5


def test_rank_stays_quiet_on_assets_drawn_from_the_board_own_group(
    workspace: Path, reference_dir: Path, board_path: Path
):
    """The registry's `must_stay_quiet` direction, driven through the CLI.

    A refusal rule that fires on everything satisfies the fires-when-it-should criterion
    completely and is useless, which is why the registry pre-registers both directions. The
    population it names is assets drawn from the board's own group at an alpha the board can
    express, which is what this candidate set is.
    """
    output = workspace / "report_quiet.json"
    code, _, err = _run(
        [
            "rank",
            str(_candidate_dir(workspace, 6, seed=505)),
            "-b",
            str(board_path),
            "-r",
            str(reference_dir),
            "-o",
            str(output),
            "--alpha",
            str(_registry_alpha_for_quiet_board(BOARD_SIZE)),
        ]
    )
    assert code == 0, err
    document = json.loads(output.read_text(encoding="utf-8"))
    assert {asset["state"] for asset in document["assets"]} == {"scored"}


def test_the_scored_fixture_carries_distinct_scores_and_one_exact_tie(ranked: dict):
    """The regime the ranking and resolution assertions below depend on.

    Two candidates share the top score by construction, so competition ranking has a real tie
    to leave a gap after, and several distinct scores exist so the resolution grid is
    testable. The fifth candidate abstains, which is how this one document exercises both
    branches of the union.
    """
    assets = ranked["document"]["assets"]
    scored = [asset for asset in assets if asset["state"] == "scored"]
    assert {asset["state"] for asset in assets} == {"scored", "abstained"}
    assert len({asset["score"] for asset in scored}) >= 3
    ranks = [asset["rank"] for asset in scored]
    assert len(ranks) != len(set(ranks))


def test_the_axis_vocabulary_is_exact_on_every_asset_in_both_states(ranked: dict):
    document = ranked["document"]
    expected = {"style", *document["board"]["representation"]["axes"]}
    assert expected == {"style", *AXIS_ORDER}
    for asset in document["assets"]:
        assert set(asset["axes"]) == expected
        if asset["state"] == "abstained":
            assert asset["axes"]["style"] is None
        else:
            assert asset["axes"]["style"] == asset["score"]


def test_the_score_is_a_multiple_of_one_over_n_local_plus_one(ranked: dict):
    """ADR-0003's resolution property, read off the emitted document.

    This is also the check that the report's `n_local` is the reference count rather than the
    reference count plus one: under the other reading the score lands between grid points
    rather than on them.

    The first assertion is what makes the second one mean anything, and it was added after a
    mutation run showed it was needed. A score of exactly 1.0 is a whole multiple of every
    grid, so a candidate set that all scored 1.0 passed this test under both readings of
    `n_local`. The fixture now realises several distinct scores and the test refuses to run on
    one that does not.
    """
    scored = [asset for asset in ranked["document"]["assets"] if asset["state"] == "scored"]
    assert len({asset["score"] for asset in scored} - {1.0}) >= 2
    for asset in scored:
        grid = 1.0 / (asset["n_local"] + 1)
        multiple = asset["score"] / grid
        assert abs(multiple - round(multiple)) < 1e-9, (asset["asset_id"], asset["score"], grid)


def test_every_asset_category_id_resolves_and_agrees_with_its_own_size(ranked: dict):
    """Both branches of the union, because both carry a `category_id`.

    Each candidate is partitioned with itself in the bag, so two candidates can disagree about
    how the board divides. This is the assertion that the document survives that: an asset's
    `category_id` has to name a listed category whose size is the size the asset itself
    reports, whichever partition the asset came from.
    """
    document = ranked["document"]
    sizes = {entry["category_id"]: entry["n_local"] for entry in document["board"]["categories"]}
    assert len(sizes) == len(document["board"]["categories"])
    for asset in document["assets"]:
        assert asset["category_id"] in sizes
        own = asset["n_local"] if asset["state"] == "scored" else asset["measurement"]["n_local"]
        assert own == sizes[asset["category_id"]]


def test_the_board_lists_a_category_for_every_partition_a_candidate_induced(ranked: dict):
    """This fixture is deliberately one where the candidates disagree, so the flag fires."""
    document = ranked["document"]
    assert "category_partition_varies_by_candidate" in document["board_stats"]["flags"]
    assigned = {asset["category_id"] for asset in document["assets"]}
    assert len(assigned) > 1


def test_the_union_is_discriminated_by_key_presence_and_not_by_nulls(ranked: dict):
    for asset in ranked["document"]["assets"]:
        if asset["state"] == "scored":
            assert {"reason", "explanation", "measurement"}.isdisjoint(asset)
        else:
            assert {"score", "interval", "rank"}.isdisjoint(asset)


def test_ranks_are_competition_ranks_over_the_scored_assets(ranked: dict):
    scored = [asset for asset in ranked["document"]["assets"] if asset["state"] == "scored"]
    order = sorted(scored, key=lambda asset: (-asset["score"], asset["asset_id"]))
    for position, asset in enumerate(order):
        if position and order[position - 1]["score"] == asset["score"]:
            assert asset["rank"] == order[position - 1]["rank"]
        else:
            assert asset["rank"] == position + 1


def test_the_report_carries_the_board_and_its_whole_reference_catalogue(
    ranked: dict, board_path: Path
):
    board = read_board(board_path)
    document = ranked["document"]
    assert document["board"]["id"] == board.board_id
    assert document["board"]["n_references"] == len(board.reference_ids)
    assert document["board"]["n_eff"] == board.n_eff
    assert document["board"]["built_at"] == board.built_at
    assert [entry["reference_id"] for entry in document["references"]] == list(board.reference_ids)
    assert [entry["content_sha256"] for entry in document["references"]] == list(
        board.reference_content_hashes
    )
    for entry in document["references"]:
        assert entry["thumbnail"]["mime"] == "image/png"
        assert entry["thumbnail"]["data_base64"]
        assert entry["thumbnail"]["width"] <= cli.THUMBNAIL_MAX_SIDE
        assert entry["thumbnail"]["height"] <= cli.THUMBNAIL_MAX_SIDE

    resolvable = set(board.reference_ids)
    for asset in document["assets"]:
        assert asset["exemplars"]
        assert {exemplar["reference_id"] for exemplar in asset["exemplars"]} <= resolvable


def test_the_fit_recorded_in_the_report_is_the_fit_the_board_was_hashed_under(
    ranked: dict, board_path: Path
):
    """Two scores are comparable when their board hashes match, so the report's fit block and
    the hashed fit cannot be allowed to disagree."""
    board = read_board(board_path)
    fit = ranked["document"]["board"]["fit"]
    assert fit["metric"] == board.metric
    assert fit["k"] == board.k
    assert fit["cluster_cut"] == board.cluster_cut
    assert fit["dup_cut"] == board.dup_cut
    assert fit["interval"]["replicates"] is None
    assert (
        board_hash(
            board.reference_content_hashes,
            board.model_repo,
            board.model_revision,
            fit["metric"],
            fit["k"],
            fit["cluster_cut"],
            fit["dup_cut"],
        )
        == ranked["document"]["board"]["id"]
    )


def test_every_category_the_board_lists_names_real_references(ranked: dict, board_path: Path):
    board = read_board(board_path)
    document = ranked["document"]
    listed = document["board"]["categories"]
    assert listed
    for category in listed:
        assert category["n_local"] == len(category["member_ids"])
        assert set(category["member_ids"]) <= set(board.reference_ids)
    assigned = {asset["category_id"] for asset in document["assets"]}
    assert assigned <= {category["category_id"] for category in listed}


def test_board_statistics_are_computed_rather_than_placeholders(ranked: dict):
    stats = ranked["document"]["board_stats"]
    quantiles = stats["tightness"]["loo_quantiles"]
    assert quantiles["p10"] <= quantiles["p50"] <= quantiles["p90"]
    assert stats["tightness"]["loo_sd"] > 0.0
    assert len(stats["leverage"]) == ranked["document"]["board"]["n_references"]
    assert [entry["rank"] for entry in stats["leverage"]] == list(
        range(1, len(stats["leverage"]) + 1)
    )
    deltas = [entry["delta_tightness"] for entry in stats["leverage"]]
    assert deltas == sorted(deltas, reverse=True)
    assert any(delta != 0.0 for delta in deltas)


def test_a_board_of_distinct_references_is_not_flagged_as_duplicate_heavy(ranked: dict):
    """The negative arm of the flag, which a flag that fires on everything would fail."""
    assert "near_duplicate_references" not in ranked["document"]["board_stats"]["flags"]
    assert "degenerate_board" not in ranked["document"]["board_stats"]["flags"]


def test_provenance_records_the_moodboard_invocation(ranked: dict):
    provenance = ranked["document"]["provenance"]
    assert provenance["engine"]["name"] == "moodboard"
    assert provenance["model"]["repo"] == ClassicalEncoder.name
    assert provenance["model"]["revision"] == ClassicalEncoder.revision
    assert provenance["command"].startswith("moodboard rank ")
    assert "--alpha" in provenance["command"]


def test_the_tie_note_states_which_pairs_were_compared(ranked: dict):
    note = ranked["document"]["comparisons"]["note"]
    assert "consecutive pair" in note
    assert "not transitive" in note
    for pair in ranked["document"]["comparisons"]["ties"]:
        assert len(pair) == 2
        assert pair[0] < pair[1]


def test_tie_pairs_none_says_so_and_reports_no_ties(
    workspace: Path, reference_dir: Path, board_path: Path
):
    output = workspace / "report_no_ties.json"
    code, _, err = _run(
        [
            "rank",
            str(_candidate_dir(workspace, 3, seed=41)),
            "-b",
            str(board_path),
            "-r",
            str(reference_dir),
            "-o",
            str(output),
            "--alpha",
            str(_registry_alpha_for_quiet_board(BOARD_SIZE)),
            "--tie-pairs",
            "none",
        ]
    )
    assert code == 0, err
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["comparisons"]["ties"] == []
    assert "No pairs were compared" in document["comparisons"]["note"]


# ---------------------------------------------------------------------------
# rank: abstention, and the refusals that protect a run
# ---------------------------------------------------------------------------


def test_rank_abstains_when_the_board_cannot_express_the_requested_alpha(
    workspace: Path, reference_dir: Path, board_path: Path
):
    """The registry's `threshold_below_resolution` case, driven through the CLI.

    The board size, the alpha and the expected reason all come from `eval/thresholds.json`.
    The regime is asserted in `test_the_quiet_alpha_is_expressible_and_the_firing_alpha_is_not`.
    """
    board_size, alpha, expected_reason = _registry_alpha_for_resolution_case()
    assert board_size == BOARD_SIZE

    output = workspace / "report_abstained.json"
    code, out, err = _run(
        [
            "rank",
            str(_candidate_dir(workspace, 3, seed=77)),
            "-b",
            str(board_path),
            "-r",
            str(reference_dir),
            "-o",
            str(output),
            "--alpha",
            str(alpha),
        ]
    )
    assert code == 0, err

    document = json.loads(output.read_text(encoding="utf-8"))
    validate_report(from_json_dict(document))
    assert [asset["state"] for asset in document["assets"]] == ["abstained"] * 3
    for asset in document["assets"]:
        assert asset["reason"] == expected_reason
        assert asset["explanation"].endswith(".")
        assert asset["measurement"]["requested_alpha"] == alpha
        assert asset["measurement"]["n_local"] == BOARD_SIZE
        assert "abstained" in asset["flags"]
        assert asset["axes"]["style"] is None
        assert asset["axes"]["palette"] is not None
    assert "abstained      3" in out


def test_rank_refuses_a_reference_directory_that_does_not_match_the_board(
    tmp_path: Path, board_path: Path
):
    """The check that makes `-r` a verified input rather than an unchecked assumption."""
    other = _write_images(
        tmp_path / "other_references", _reference_arrays(BOARD_SIZE, seed=4321), "other"
    )
    code, _, err = _run(
        [
            "rank",
            str(_candidate_dir(tmp_path, 1, seed=8)),
            "-b",
            str(board_path),
            "-r",
            str(other),
            "-o",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == 1
    assert "does not match the board" in err


def test_rank_refuses_a_reference_directory_whose_contents_changed(
    tmp_path: Path, board_path: Path, reference_dir: Path
):
    """The same-names case, which a check on ids alone would pass.

    A file edited in place keeps its name and changes its content hash, so this is the arm
    that says the guard reads the bytes rather than the directory listing.
    """
    edited = tmp_path / "edited_references"
    edited.mkdir()
    for path in sorted(reference_dir.glob("*.png")):
        (edited / path.name).write_bytes(path.read_bytes())

    victim = sorted(edited.glob("*.png"))[0]
    array = np.asarray(Image.open(victim).convert("RGB"), dtype=np.uint8).copy()
    array[0, 0] = (255 - int(array[0, 0, 0]), 0, 0)
    Image.fromarray(array).save(victim)

    code, _, err = _run(
        [
            "rank",
            str(_candidate_dir(tmp_path, 1, seed=9)),
            "-b",
            str(board_path),
            "-r",
            str(edited),
            "-o",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == 1
    assert "contents have changed" in err


def test_rank_refuses_a_board_whose_recorded_n_eff_is_not_its_own(
    tmp_path: Path, board_path: Path, reference_dir: Path
):
    """`n_eff` is outside the board hash, so `read_board` cannot catch a wrong one.

    ADR-0005 hashes the reference content, the model identity and the fitting parameters.
    `n_eff` is derived from the embeddings rather than fitted, so it is not in that payload
    and a `brand.mb` carrying a hand-edited `n_eff` still verifies its own id. It would then
    set the admissibility floor for every asset scored against it. `rank` recomputes it from
    the stored embeddings for that reason, and this is the arm that says so.
    """
    tampered = tmp_path / "tampered.mb"
    with zipfile.ZipFile(board_path) as source:
        meta = json.loads(source.read("meta.json"))
        embeddings = source.read("embeddings.npy")
    meta["n_eff"] = meta["n_eff"] / 2.0
    with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as destination:
        destination.writestr("meta.json", json.dumps(meta, sort_keys=True, indent=2))
        destination.writestr("embeddings.npy", embeddings)

    assert read_board(tampered).board_id == read_board(board_path).board_id

    code, _, err = _run(
        [
            "rank",
            str(_candidate_dir(tmp_path, 1, seed=12)),
            "-b",
            str(tampered),
            "-r",
            str(reference_dir),
            "-o",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == 1
    assert "inconsistent with itself" in err


def test_thumbnails_are_bounded_and_keep_their_aspect_ratio(tmp_path: Path):
    """The bound has to bite on an image larger than it.

    A mutation run caught this: the reference images elsewhere in this file are smaller than
    the thumbnail bound, so removing the resize entirely left every assertion about thumbnail
    size passing. This one hands it an image that is four times the bound on the long edge.
    """
    tall = np.zeros((400, 600, 3), dtype=np.uint8)
    tall[:, :300] = (200, 40, 40)
    path = tmp_path / "tall.png"
    Image.fromarray(tall).save(path)

    thumbnail = cli._thumbnail(cli._load_image(path, "tall.png"))
    assert max(thumbnail.width, thumbnail.height) == cli.THUMBNAIL_MAX_SIDE
    assert thumbnail.width == cli.THUMBNAIL_MAX_SIDE
    assert thumbnail.height == round(cli.THUMBNAIL_MAX_SIDE * 400 / 600)
    assert base64.b64decode(thumbnail.data_base64)[:8] == b"\x89PNG\r\n\x1a\n"


def test_rank_refuses_a_missing_input_without_a_traceback(tmp_path: Path, board_path: Path):
    code, _, err = _run(
        [
            "rank",
            str(tmp_path / "nowhere"),
            "-b",
            str(board_path),
            "-r",
            str(tmp_path / "nowhere"),
            "-o",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == 1
    assert err.startswith("moodboard:")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_report_validates_an_emitted_report_and_summarises_it(ranked: dict):
    code, out, err = _run(["report", str(ranked["path"])])
    assert code == 0, err
    assert "is a valid schema 1.0 report" in out
    assert ranked["document"]["board"]["id"] in out
    for asset in ranked["document"]["assets"]:
        assert asset["asset_id"] in out


def test_report_rejects_a_document_that_violates_the_contract(tmp_path: Path, ranked: dict):
    """The validator is load-bearing at this entry point rather than decorative.

    Dropping one axis key from one asset leaves a document the JSON Schema still accepts,
    because the axis-vocabulary invariant is an equality between two parts of the document and
    is checked separately. If `report` skipped that second step this would pass.
    """
    document = json.loads(json.dumps(ranked["document"]))
    del document["assets"][0]["axes"]["tone"]
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(document), encoding="utf-8")

    code, _, err = _run(["report", str(broken)])
    assert code == 1
    assert document["assets"][0]["asset_id"] in err


def test_report_html_raises_not_implemented(ranked: dict, tmp_path: Path):
    """The one unimplemented surface in the package, and it refuses rather than half-writing.

    It is deliberately not caught into an exit code: a flag that is documented as not built
    should not be indistinguishable from a flag that broke.
    """
    with pytest.raises(NotImplementedError) as excinfo:
        _run(["report", str(ranked["path"]), "--html", str(tmp_path / "out.html")])
    message = str(excinfo.value)
    assert "viewer" in message
    assert "moodboard rank" in message
    assert not (tmp_path / "out.html").exists()


# ---------------------------------------------------------------------------
# The parser itself
# ---------------------------------------------------------------------------


def test_alpha_rejects_nan_at_parse_time():
    """`type=float` accepts the token `nan`, and a NaN alpha makes every later comparison
    False, so a request the engine cannot honour would read as one it could."""
    for token in ("nan", "inf", "-inf"):
        with pytest.raises(SystemExit) as excinfo:
            cli.build_parser().parse_args(
                ["rank", "c", "-b", "b", "-r", "r", "-o", "o", "--alpha", token]
            )
        assert excinfo.value.code == 2


def test_alpha_accepts_an_ordinary_value():
    args = cli.build_parser().parse_args(
        ["rank", "c", "-b", "b", "-r", "r", "-o", "o", "--alpha", "0.05"]
    )
    assert args.alpha == 0.05
    assert args.tie_pairs == "adjacent"
    assert args.exemplars == 3


def test_a_missing_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args([])
    assert excinfo.value.code == 2


def test_rank_requires_the_board_and_the_reference_directory():
    for missing in (["-r", "r", "-o", "o"], ["-b", "b", "-o", "o"], ["-b", "b", "-r", "r"]):
        with pytest.raises(SystemExit) as excinfo:
            cli.build_parser().parse_args(["rank", "c", *missing])
        assert excinfo.value.code == 2


def test_competition_ranking_leaves_the_gap_after_a_tie():
    ranks = cli._competition_ranks([("d", 0.5), ("a", 0.9), ("c", 0.5), ("b", 0.9), ("e", 0.1)])
    assert ranks == {"a": 1, "b": 1, "c": 3, "d": 3, "e": 5}


def test_the_console_entry_point_named_in_pyproject_exists():
    """`pyproject.toml` declares one console script and this is the callable it names.

    Read out of the file rather than restated here, so renaming the entry point without
    renaming the function fails in the suite instead of at a user's first invocation.
    """
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as handle:
        declared = tomllib.load(handle)["project"]["scripts"]["moodboard"]

    module_name, _, attribute = declared.partition(":")
    module = __import__(module_name, fromlist=[attribute])
    assert getattr(module, attribute) is cli.main

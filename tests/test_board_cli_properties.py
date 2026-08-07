"""Property tests for the board hash and for the command line's JSON path.

Three properties are under test here, each stated in a record rather than invented:

1. The board hash changes when any fitting parameter changes, and is stable under reordering
   the references. ADR-0005 defines the hash as sha256 over a canonical JSON serialisation of
   sorted reference content hashes, the model identity and the fitting parameters, and says
   the fitting parameters are the load-bearing part: "Any parameter that can change a score
   belongs inside this hash".
2. `moodboard build`, `moodboard rank` and `moodboard report` run end to end on synthetic
   images and produce a report that satisfies the committed JSON Schema.
3. `moodboard report --html` raises `NotImplementedError`.

Where the properties are quantified rather than exemplified
-----------------------------------------------------------

A property is a statement about every input, so the hash tests draw their inputs from a
seeded generator and check the statement over a population rather than over one hand-chosen
case. Every generator here takes an explicit seed and no test reads a clock, a network or the
process environment, so a failure reproduces exactly. Reordering is checked over many random
permutations at many board sizes rather than one reversal, and parameter sensitivity is
checked by moving one input at a time out of a randomly drawn argument tuple, which is what
distinguishes a hash that separates two boards from one that happens to separate the two
boards someone thought of.

Two habits that decide whether a green result means anything
------------------------------------------------------------

The hash tests carry an independent oracle. `test_the_digest_is_the_serialisation_ADR_0005_
defines` builds the canonical payload here, from the record's own text, and compares its
sha256 with what `board_hash` returned. A test that only compares `board_hash` against
itself under permutation would pass on a function that returned a constant, so the population
tests are paired with an oracle that pins the value.

The end-to-end test asserts the regime it believes it is in before asserting the behaviour,
and asserts that both asset states occur. A report in which every asset abstained is still a
schema-valid report, so "the JSON path produces a schema-valid report" is satisfiable without
the scoring path ever running. The fixture therefore carries candidates drawn from the
board's own look and candidates drawn from a different one, and a test fails if the run did
not exercise both branches of the discriminated union.

Schema validation here runs `jsonschema` against the committed schema file directly rather
than calling `report.validate_report`. The engine's own validator is the thing being checked,
so a test that called it would agree with it whatever it did.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import jsonschema
import numpy as np
import pytest
from PIL import Image

from moodboard import cli
from moodboard.abstain import load_abstention_thresholds
from moodboard.board import board_hash, read_board, write_board
from moodboard.conformal import duplicate_groups, kish_n_eff
from moodboard.encoders import ClassicalEncoder
from moodboard.report import SCHEMA_PATH

# ---------------------------------------------------------------------------
# Generators for the hash population
# ---------------------------------------------------------------------------

# The names of every input the record puts inside the hash. Parametrising over this list is
# what makes "changes when ANY fitting parameter changes" a claim about all of them: a new
# hashed input that nobody added here would leave a field untested, so the list is asserted
# against `board_hash`'s own signature below.
HASHED_INPUTS = (
    "reference_content_hashes",
    "model_repo",
    "model_revision",
    "metric",
    "k",
    "cluster_cut",
    "dup_cut",
)


def _content_hash(rng: np.random.Generator) -> str:
    """A plausible reference content hash: the sha256 of some bytes, as the engine produces."""
    return hashlib.sha256(rng.bytes(16)).hexdigest()


def _arguments(rng: np.random.Generator, n_references: int | None = None) -> dict:
    """One randomly drawn argument tuple for `board_hash`.

    The ranges are deliberately wider than the values the engine uses today. A hash that
    separated only the four or five configurations currently reachable would satisfy a test
    built from those configurations and still collide on the next one added.
    """
    count = n_references if n_references is not None else int(rng.integers(1, 15))
    return {
        "reference_content_hashes": [_content_hash(rng) for _ in range(count)],
        "model_repo": f"repo-{rng.integers(0, 10_000)}",
        "model_revision": f"rev-{rng.integers(0, 10_000)}",
        "metric": str(rng.choice(["cosine", "euclidean", "correlation"])),
        "k": int(rng.integers(1, 64)),
        "cluster_cut": round(float(rng.uniform(0.01, 0.99)), 6),
        "dup_cut": round(float(rng.uniform(0.001, 0.5)), 6),
    }


def _perturb(arguments: dict, field: str, rng: np.random.Generator) -> dict:
    """Return a copy of `arguments` with exactly one field moved to a different value."""
    moved = dict(arguments)
    if field == "reference_content_hashes":
        replacement = list(arguments[field])
        replacement[int(rng.integers(0, len(replacement)))] = _content_hash(rng)
        moved[field] = replacement
    elif field in {"model_repo", "model_revision", "metric"}:
        moved[field] = arguments[field] + "-moved"
    elif field == "k":
        moved[field] = arguments[field] + int(rng.integers(1, 8))
    else:
        moved[field] = round(arguments[field] + float(rng.uniform(0.001, 0.1)), 9)
    assert moved[field] != arguments[field], f"the perturbation of {field} did not move it"
    return moved


def _canonical_digest(arguments: dict) -> str:
    """ADR-0005's definition, written out here rather than called out of the package.

    This is the oracle. It is a second implementation of one paragraph of the record, so a
    disagreement between it and `board_hash` means one of the two misread the record, which is
    the disagreement worth catching. It is not a refactor of the module under test and must not
    become one.
    """
    payload = {
        "v": 1,
        "refs": sorted(arguments["reference_content_hashes"]),
        "model": {"repo": arguments["model_repo"], "revision": arguments["model_revision"]},
        "fit": {
            "metric": arguments["metric"],
            "k": arguments["k"],
            "cluster_cut": arguments["cluster_cut"],
            "dup_cut": arguments["dup_cut"],
        },
    }
    serialised = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The board hash: stability under reordering
# ---------------------------------------------------------------------------


def test_the_hashed_inputs_are_exactly_the_parameters_this_file_quantifies_over():
    """The list this file parametrises over is `board_hash`'s own parameter list.

    Without this, adding a hashed input would silently leave that input untested while every
    test below still passed, and the suite would report full coverage of a property it no
    longer covered.
    """
    import inspect

    signature = inspect.signature(board_hash)
    assert tuple(signature.parameters) == HASHED_INPUTS


@pytest.mark.parametrize("n_references", [1, 2, 3, 5, 10, 17, 50])
def test_the_digest_is_unchanged_by_every_permutation_of_the_references(n_references: int):
    """ADR-0005 hashes the references sorted, so reference order carries no information.

    Twenty-four permutations per board size, drawn from a seeded generator, rather than one
    reversal: a reversal alone passes on an implementation that hashed, say, the first and last
    entries symmetrically.
    """
    rng = np.random.default_rng(1000 + n_references)
    arguments = _arguments(rng, n_references=n_references)
    expected = board_hash(**arguments)

    for _ in range(24):
        shuffled = dict(arguments)
        order = rng.permutation(n_references)
        shuffled["reference_content_hashes"] = [
            arguments["reference_content_hashes"][index] for index in order
        ]
        assert board_hash(**shuffled) == expected, (
            f"reordering {n_references} references changed the digest under permutation "
            f"{order.tolist()}"
        )


def test_reordering_is_stable_even_when_two_references_have_the_same_content():
    """Two identical files are two references, and the digest counts both.

    Sorting a list and sorting a set both survive permutation, so a permutation test alone
    cannot tell them apart. It matters which one this is: a board of ten files where two are
    byte-identical is not the same board as one of nine distinct files, because n_eff, the
    duplicate grouping and every leave-one-out quantity read the count. This test fails on an
    implementation that deduplicated, and the permutation test above would not.
    """
    rng = np.random.default_rng(4242)
    arguments = _arguments(rng, n_references=6)
    hashes = list(arguments["reference_content_hashes"])
    hashes[3] = hashes[0]
    with_duplicate = {**arguments, "reference_content_hashes": hashes}

    reversed_order = {**arguments, "reference_content_hashes": list(reversed(hashes))}
    assert board_hash(**reversed_order) == board_hash(**with_duplicate)

    without_duplicate = {**arguments, "reference_content_hashes": [*hashes[:3], *hashes[4:]]}
    assert board_hash(**without_duplicate) != board_hash(**with_duplicate), (
        "dropping one of two identical references left the digest unchanged, so the hash is "
        "over the set of contents rather than over the references"
    )


# ---------------------------------------------------------------------------
# The board hash: sensitivity to every hashed input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", HASHED_INPUTS)
def test_the_digest_moves_when_that_one_input_moves(field: str):
    """Move one input out of a randomly drawn tuple and the digest must move with it.

    Forty independent tuples per field. The failure message names the field, so a hash that
    dropped one parameter from its payload is reported as that parameter rather than as a
    generic mismatch.
    """
    rng = np.random.default_rng(hash(field) % (2**32))
    for case in range(40):
        arguments = _arguments(rng, n_references=int(rng.integers(2, 12)))
        moved = _perturb(arguments, field, rng)
        assert board_hash(**arguments) != board_hash(**moved), (
            f"case {case}: moving {field} from {arguments[field]!r} to {moved[field]!r} left "
            "the board id unchanged, so two boards fitted under different parameters would "
            "carry the same identifier and their scores would be presented as comparable"
        )


def test_a_change_far_below_any_display_precision_still_moves_the_digest():
    """The cuts are real numbers and the hash does not round them.

    A cut of 0.35 and one of 0.3500000001 are different fits, and ADR-0005's rule is that any
    parameter able to change a score belongs inside the hash. The equal-value control in the
    same test is what makes this a sensitivity measurement rather than a statement that the
    function is not constant.
    """
    rng = np.random.default_rng(7)
    arguments = _arguments(rng, n_references=8)
    baseline = board_hash(**arguments)

    assert board_hash(**dict(arguments)) == baseline

    for field in ("cluster_cut", "dup_cut"):
        nudged = {**arguments, field: arguments[field] + 1e-10}
        assert board_hash(**nudged) != baseline, f"a 1e-10 change in {field} was rounded away"


def test_distinct_argument_tuples_have_distinct_digests():
    """Three hundred distinct tuples, three hundred distinct digests.

    This is the collision statement the per-field tests cannot make on their own. Each of those
    moves one field with the others held fixed; this one lets the whole tuple vary, which is
    the population a real deployment draws from.
    """
    rng = np.random.default_rng(20260807)
    seen: dict[str, str] = {}
    for _ in range(300):
        arguments = _arguments(rng)
        key = json.dumps(
            {name: arguments[name] for name in HASHED_INPUTS}, sort_keys=True, default=list
        )
        digest = board_hash(**arguments)
        assert seen.setdefault(digest, key) == key, (
            f"two different boards hash to {digest}:\n  {seen[digest]}\n  {key}"
        )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({"model_repo": "ab", "model_revision": "c"}, {"model_repo": "a", "model_revision": "bc"}),
        ({"metric": "cosine", "model_repo": "x"}, {"metric": "cosin", "model_repo": "ex"}),
        (
            {"reference_content_hashes": ["ab", "cd"]},
            {"reference_content_hashes": ["abc", "d"]},
        ),
        (
            {"reference_content_hashes": ["a", "b", "c"]},
            {"reference_content_hashes": ["abc"]},
        ),
    ],
)
def test_two_inputs_cannot_be_confused_for_each_other_by_running_together(left: dict, right: dict):
    """Adjacent fields keep their boundary.

    A payload assembled by concatenation rather than by structure lets a character move from
    one field to its neighbour without changing the bytes that get hashed, so two genuinely
    different boards collide. The pairs here are chosen so that a naive concatenation of the
    same fields produces identical text.
    """
    rng = np.random.default_rng(11)
    base = _arguments(rng, n_references=2)
    assert board_hash(**{**base, **left}) != board_hash(**{**base, **right})


def test_the_digest_is_the_serialisation_ADR_0005_defines():
    """The oracle: an independent construction of the record's payload, over a population.

    Every other hash test here compares `board_hash` with itself, which cannot detect a
    function that is self-consistent and wrong. This one pins the value.
    """
    rng = np.random.default_rng(99)
    for _ in range(50):
        arguments = _arguments(rng)
        digest = board_hash(**arguments)
        assert digest == _canonical_digest(arguments)
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")


# ---------------------------------------------------------------------------
# The hash as it reaches the artifact, and the limit of what it protects
# ---------------------------------------------------------------------------


def _sample_board(tmp_path: Path, seed: int = 5):
    rng = np.random.default_rng(seed)
    from moodboard.board import build_board

    return build_board(
        name="sample",
        reference_ids=[f"ref_{index}.png" for index in range(4)],
        reference_content_hashes=[_content_hash(rng) for _ in range(4)],
        reference_embeddings=rng.normal(size=(4, 6)).astype(np.float32),
        model_repo="classical-v1",
        model_revision="1",
        metric="cosine",
        k=3,
        cluster_cut=0.35,
        dup_cut=0.05,
        n_eff=4.0,
        built_at="2026-08-07T00:00:00Z",
    )


@pytest.mark.parametrize(
    ("section", "key", "replacement"),
    [
        (None, "reference_content_hashes", ["0" * 64, "1" * 64, "2" * 64, "3" * 64]),
        ("model", "repo", "someone-elses-encoder"),
        ("model", "revision", "2"),
        ("fit", "metric", "euclidean"),
        ("fit", "k", 4),
        ("fit", "cluster_cut", 0.36),
        ("fit", "dup_cut", 0.06),
    ],
)
def test_editing_any_hashed_field_in_the_artifact_is_caught_on_read(
    tmp_path: Path, section: str | None, key: str, replacement
):
    """The hash is only useful if something checks it, so check that something does.

    One case per hashed field rather than a sample of them, for the same reason the sensitivity
    test is parametrised: a reader that verified five of seven fields would pass a spot check.
    """
    path = tmp_path / "brand.mb"
    write_board(_sample_board(tmp_path), path)

    with zipfile.ZipFile(path) as archive:
        meta = json.loads(archive.read("meta.json"))
        embeddings = archive.read("embeddings.npy")
    if section is None:
        meta[key] = replacement
    else:
        meta[section][key] = replacement

    edited = tmp_path / "edited.mb"
    with zipfile.ZipFile(edited, "w") as archive:
        archive.writestr("meta.json", json.dumps(meta))
        archive.writestr("embeddings.npy", embeddings)

    with pytest.raises(ValueError, match="corrupt or was hand-edited"):
        read_board(edited)


@pytest.mark.parametrize("key", ["name", "n_eff", "built_at"])
def test_a_field_outside_the_hash_is_not_caught_on_read(tmp_path: Path, key: str):
    """The control that gives the test above its meaning, and states the hash's reach.

    These three fields are outside ADR-0005's payload, so a `brand.mb` carrying an edited one
    still verifies its own id. That is the definition working as written rather than a defect
    in this reader: none of the three can change a score. It is recorded as a test because the
    boundary is what a reader of the previous test needs in order to know what a passing read
    does and does not certify. `n_eff` is the one with consequences, since it sets the
    admissibility floor, and `rank` recomputes it from the stored embeddings for exactly that
    reason.
    """
    path = tmp_path / "brand.mb"
    board = _sample_board(tmp_path)
    write_board(board, path)

    with zipfile.ZipFile(path) as archive:
        meta = json.loads(archive.read("meta.json"))
        embeddings = archive.read("embeddings.npy")
    meta[key] = {"name": "renamed", "n_eff": 99.0, "built_at": "2000-01-01T00:00:00Z"}[key]

    edited = tmp_path / "edited.mb"
    with zipfile.ZipFile(edited, "w") as archive:
        archive.writestr("meta.json", json.dumps(meta))
        archive.writestr("embeddings.npy", embeddings)

    assert read_board(edited).board_id == board.board_id


# ---------------------------------------------------------------------------
# Synthetic images for the end-to-end path
# ---------------------------------------------------------------------------

IMAGE_SIZE = (72, 96)
BOARD_SIZE = 10

# The window the reference set is selected into, in cosine distance. The floor is above
# ADR-0005's near-duplicate cut of 0.05, so every reference is its own group and n_eff equals
# n. The ceiling is below ADR-0004's category cut of 0.35, so the board reads as one look and
# a candidate is compared against all ten references. Both ends are asserted by
# `test_the_reference_board_is_in_the_regime_these_tests_assume`.
SEPARATION_MIN = 0.08
SEPARATION_MAX = 0.30

LOOKS = {
    "sand": np.array([0.86, 0.72, 0.52]),
    "ink": np.array([0.18, 0.24, 0.40]),
}


def _swatch(rng: np.random.Generator, look: str, jitter: float = 0.40) -> np.ndarray:
    """One synthetic image: a grid of tinted panels under a tilted gradient.

    A look is a hue direction. Two images from one look sit near each other in the classical
    encoder's space and two from different looks sit far apart, which is what lets one fixture
    exercise both the scored and the abstained branch.
    """
    height, width = IMAGE_SIZE
    hue = np.clip(LOOKS[look] * rng.uniform(1.0 - jitter, 1.0 + jitter, size=3), 0.03, 1.0)

    blocks_y = int(rng.integers(3, 7))
    blocks_x = int(rng.integers(3, 8))
    panel = rng.uniform(0.45, 1.35, size=(blocks_y, blocks_x, 1))
    tile = np.repeat(
        np.repeat(panel, int(np.ceil(height / blocks_y)), axis=0),
        int(np.ceil(width / blocks_x)),
        axis=1,
    )[:height, :width]

    rows = np.linspace(0.0, 1.0, height)[:, None, None]
    columns = np.linspace(0.0, 1.0, width)[None, :, None]
    angle = float(rng.uniform(0.0, np.pi))
    ramp = np.cos(angle) * rows + np.sin(angle) * columns
    ramp = (ramp - ramp.min()) / (ramp.max() - ramp.min())

    level = float(rng.uniform(0.55, 1.05))
    field = np.clip(level * tile * (0.65 + 0.7 * ramp), 0.0, 2.0)
    return np.clip(field * hue[None, None, :] * 255.0, 0, 255).astype(np.uint8)


def _separated_references(count: int, seed: int, pool: int = 400) -> list[np.ndarray]:
    """A reference set every pair of which is separated into the window above.

    Raises rather than returning a shorter set. A board of eight where ten were asked for is a
    different board with a different resolution floor, and the alpha these tests rank at is
    read out of the registry against the number ten.
    """
    rng = np.random.default_rng(seed)
    drawn = [_swatch(rng, "sand") for _ in range(pool)]
    vectors = ClassicalEncoder().embed(drawn).astype(np.float64)

    kept: list[np.ndarray] = []
    kept_vectors: list[np.ndarray] = []
    for image, vector in zip(drawn, vectors, strict=True):
        if kept_vectors:
            distances = 1.0 - np.asarray(kept_vectors) @ vector
            if distances.min() < SEPARATION_MIN or distances.max() > SEPARATION_MAX:
                continue
        kept.append(image)
        kept_vectors.append(vector)
        if len(kept) == count:
            return kept
    raise AssertionError(
        f"the generator reached only {len(kept)} of {count} references separated into "
        f"[{SEPARATION_MIN}, {SEPARATION_MAX}] from a pool of {pool}"
    )


def _write(directory: Path, arrays: list[np.ndarray], prefix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for index, array in enumerate(arrays):
        Image.fromarray(array).save(directory / f"{prefix}_{index:02d}.png")
    return directory


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _quiet_alpha(board_size: int) -> float:
    """The alpha the registry says a single-look board of this size should stay quiet at."""
    thresholds = load_abstention_thresholds()
    row = next(
        entry
        for entry in thresholds.must_stay_quiet_population
        if entry["n"] == board_size and entry["look"] == "single"
    )
    return float(row["alpha"])


@pytest.fixture(scope="module")
def workspace(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("board_cli_properties")


@pytest.fixture(scope="module")
def reference_dir(workspace: Path) -> Path:
    return _write(
        workspace / "references", _separated_references(BOARD_SIZE, seed=20260807), "ref"
    )


@pytest.fixture(scope="module")
def board_path(workspace: Path, reference_dir: Path) -> Path:
    path = workspace / "brand.mb"
    code, _, err = _run(["build", str(reference_dir), "-o", str(path)])
    assert code == 0, err
    return path


CANDIDATE_COUNT = 9


@pytest.fixture(scope="module")
def candidate_dir(workspace: Path) -> Path:
    """Six candidates from the board's look, one exact repeat of one of them, and two others.

    Each group is there to reach a branch that the others cannot. The off-look pair is what
    makes the abstained arm of the union reachable, and a test below fails if the run produced
    no asset in one of the two states. The repeat is a byte-identical copy of the first
    candidate under a second name, which is the one candidate set that can distinguish
    competition ranking from position in a list: a set of distinct images gives every asset a
    distinct score, and under distinct scores the two rank policies agree. It is also an
    ordinary thing for a caller to do, since the same asset can arrive twice under two names.
    """
    rng = np.random.default_rng(31337)
    arrays = [_swatch(rng, "sand") for _ in range(6)] + [_swatch(rng, "ink") for _ in range(2)]
    arrays.append(arrays[0])
    return _write(workspace / "candidates", arrays, "cand")


@pytest.fixture(scope="module")
def ranked(workspace: Path, reference_dir: Path, board_path: Path, candidate_dir: Path) -> dict:
    output = workspace / "report.json"
    code, out, err = _run(
        [
            "rank",
            str(candidate_dir),
            "-b",
            str(board_path),
            "-r",
            str(reference_dir),
            "-o",
            str(output),
            "--alpha",
            str(_quiet_alpha(BOARD_SIZE)),
        ]
    )
    assert code == 0, err
    return {
        "path": output,
        "document": json.loads(output.read_text(encoding="utf-8")),
        "stdout": out,
    }


# ---------------------------------------------------------------------------
# The regime the end-to-end tests assume
# ---------------------------------------------------------------------------


def test_the_reference_board_is_in_the_regime_these_tests_assume(reference_dir: Path):
    """Measure the board rather than trusting the generator that drew it.

    Every claim below about the scored path rests on this board having ten distinct references
    in one category, so a drift in the generator that turned it into a duplicate-heavy or a
    multi-look board would otherwise turn these into tests of a different code path that still
    passed.
    """
    paths = sorted(reference_dir.glob("*.png"))
    assert len(paths) == BOARD_SIZE

    arrays = [np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8) for path in paths]
    embeddings = ClassicalEncoder().embed(arrays).astype(np.float64)
    distances = 1.0 - embeddings @ embeddings.T
    off_diagonal = distances[~np.eye(BOARD_SIZE, dtype=bool)]

    assert off_diagonal.min() > SEPARATION_MIN * 0.999
    assert off_diagonal.max() < SEPARATION_MAX * 1.001

    groups = duplicate_groups(embeddings, 0.05)
    assert len(groups) == BOARD_SIZE, "the references are not ten distinct near-duplicate groups"
    assert kish_n_eff([len(group) for group in groups]) == pytest.approx(float(BOARD_SIZE))

    floor = 1.0 / (BOARD_SIZE + 1.0)
    assert _quiet_alpha(BOARD_SIZE) >= floor, (
        "the registry's quiet alpha is finer than this board's resolution floor, so the "
        "ranking below would abstain on resolution for every candidate"
    )


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def test_build_writes_an_artifact_whose_id_is_the_hash_of_the_files_on_disk(
    reference_dir: Path, board_path: Path
):
    """The end-to-end statement of the hash property: recompute it from the inputs.

    The reference content hashes are taken here from the bytes on disk, not from the artifact,
    so this checks the whole path from file to identifier rather than checking the artifact
    against itself.
    """
    assert board_path.exists()
    with zipfile.ZipFile(board_path) as archive:
        assert sorted(archive.namelist()) == ["embeddings.npy", "meta.json"]

    board = read_board(board_path)
    encoder = ClassicalEncoder()
    digests = [
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(reference_dir.glob("*.png"))
    ]

    assert board.board_id == board_hash(
        digests,
        encoder.name,
        encoder.revision,
        "cosine",
        board.k,
        board.cluster_cut,
        board.dup_cut,
    )
    assert board.k == min(5, BOARD_SIZE - 1)


def test_building_the_same_directory_twice_gives_the_same_identifier(
    workspace: Path, reference_dir: Path, board_path: Path
):
    """A content-addressed identifier that moved between two runs would not address content."""
    second = workspace / "brand_again.mb"
    code, _, err = _run(["build", str(reference_dir), "-o", str(second)])
    assert code == 0, err

    first_board = read_board(board_path)
    second_board = read_board(second)
    assert second_board.board_id == first_board.board_id
    assert second_board.n_eff == first_board.n_eff
    assert second_board.reference_content_hashes == first_board.reference_content_hashes


def test_renaming_the_reference_files_leaves_the_board_identifier_unchanged(
    workspace: Path, reference_dir: Path, board_path: Path
):
    """Reordering the references end to end, through the command line.

    The hash is over sorted content hashes, so a directory holding the same ten images under
    names that reverse the order the scanner walks them in is the same board. The reference
    ids move and the identifier does not, which is the property distinguishing a hash over
    content from a hash over a directory listing. Without the second assertion the test would
    also pass on an engine that ignored the new names entirely, so both are checked.
    """
    renamed_dir = workspace / "references_renamed"
    renamed_dir.mkdir(parents=True, exist_ok=True)
    originals = sorted(reference_dir.glob("*.png"))
    for index, path in enumerate(originals):
        (renamed_dir / f"zz_{len(originals) - 1 - index:02d}.png").write_bytes(path.read_bytes())

    renamed_board_path = workspace / "brand_renamed.mb"
    code, _, err = _run(["build", str(renamed_dir), "-o", str(renamed_board_path)])
    assert code == 0, err

    original = read_board(board_path)
    renamed = read_board(renamed_board_path)

    assert renamed.board_id == original.board_id
    assert renamed.reference_ids != original.reference_ids
    assert sorted(renamed.reference_content_hashes) == sorted(original.reference_content_hashes)
    assert renamed.reference_content_hashes != original.reference_content_hashes, (
        "the renamed directory did not actually change the order the references were read in, "
        "so this test did not exercise reordering"
    )


@pytest.fixture(scope="module")
def repeated_reference_dir(workspace: Path, reference_dir: Path) -> Path:
    """The same ten references with the last one replaced by a byte copy of the first."""
    directory = workspace / "references_with_a_repeat"
    directory.mkdir(parents=True, exist_ok=True)
    originals = sorted(reference_dir.glob("*.png"))
    for path in originals[:-1]:
        (directory / path.name).write_bytes(path.read_bytes())
    (directory / originals[-1].name).write_bytes(originals[0].read_bytes())
    return directory


@pytest.fixture(scope="module")
def repeated_board(workspace: Path, repeated_reference_dir: Path) -> tuple[Path, str]:
    path = workspace / "brand_with_a_repeat.mb"
    code, out, err = _run(["build", str(repeated_reference_dir), "-o", str(path)])
    assert code == 0, err
    return path, out


def test_a_repeated_reference_file_changes_the_board_and_lowers_its_resolution(
    repeated_reference_dir: Path, repeated_board: tuple[Path, str], board_path: Path
):
    """The multiset property, end to end, and what it costs the board.

    `test_reordering_is_stable_even_when_two_references_have_the_same_content` states the same
    property about `board_hash` in isolation. This is the statement a user of the tool would
    make: a directory of ten files where two are byte-identical is a different board from one
    of nine distinct files and from one of ten, and it can resolve less finely, because
    ADR-0005's n_eff discounts the repeat while the file count does not.
    """
    duplicated_dir = repeated_reference_dir
    duplicated_board_path, out = repeated_board

    board = read_board(duplicated_board_path)
    original = read_board(board_path)
    encoder = ClassicalEncoder()
    digests = [
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(duplicated_dir.glob("*.png"))
    ]
    assert len(digests) == BOARD_SIZE
    assert len(set(digests)) == BOARD_SIZE - 1

    assert board.board_id == board_hash(
        digests,
        encoder.name,
        encoder.revision,
        "cosine",
        board.k,
        board.cluster_cut,
        board.dup_cut,
    )
    assert board.board_id != original.board_id

    embeddings = np.asarray(board.reference_embeddings, dtype=np.float64)
    groups = duplicate_groups(embeddings, board.dup_cut)
    assert len(groups) == BOARD_SIZE - 1
    assert board.n_eff == pytest.approx(kish_n_eff([len(group) for group in groups]))
    assert board.n_eff < BOARD_SIZE
    assert f"n_eff          {board.n_eff:.4f}" in out


def test_a_board_that_cannot_express_the_request_abstains_and_says_which_flags_apply(
    workspace: Path,
    candidate_dir: Path,
    repeated_reference_dir: Path,
    repeated_board: tuple[Path, str],
):
    """The refusal path end to end, on a board whose repeat has coarsened its floor.

    The regime is asserted from the board's own numbers before the behaviour is asserted: the
    floor 1/(n_eff + 1) is above the alpha being requested, so ADR-0004 rule 1 refuses every
    candidate rather than rounding the request. A report in which every asset abstained is a
    valid report and this checks that it is one.
    """
    duplicated_dir = repeated_reference_dir
    duplicated_board_path, _ = repeated_board
    board = read_board(duplicated_board_path)
    alpha = _quiet_alpha(BOARD_SIZE)
    floor = 1.0 / (board.n_eff + 1.0)
    assert floor > alpha, (
        f"this board's floor {floor} is not above the requested alpha {alpha}, so the refusal "
        "this test is about would not be triggered"
    )

    output = workspace / "report_with_a_repeat.json"
    code, _, err = _run(
        [
            "rank",
            str(candidate_dir),
            "-b",
            str(duplicated_board_path),
            "-r",
            str(duplicated_dir),
            "-o",
            str(output),
            "--alpha",
            str(alpha),
        ]
    )
    assert code == 0, err

    document = json.loads(output.read_text(encoding="utf-8"))
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(document)

    assert {asset["state"] for asset in document["assets"]} == {"abstained"}
    assert "near_duplicate_references" in document["board_stats"]["flags"]
    assert document["board"]["supported_alpha"] == pytest.approx(floor)
    assert all(asset["axes"]["style"] is None for asset in document["assets"])


# ---------------------------------------------------------------------------
# rank: the report the JSON path produces
# ---------------------------------------------------------------------------


def test_the_emitted_report_satisfies_the_committed_json_schema(ranked: dict):
    """Validate with `jsonschema` against the schema file, not through the engine's validator.

    The engine validating its own output with its own validator is one instrument reporting on
    itself. This loads the committed schema and runs a standard validator over the document as
    it sits on disk, which is what a consumer of the file would do.
    """
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(ranked["document"]), key=lambda error: error.path)
    assert not errors, "\n".join(
        f"{list(error.path)}: {error.message}" for error in errors[:10]
    )


def test_the_run_exercised_both_states_of_the_asset_union(ranked: dict):
    """Anti-vacuity: a report in which everything abstained is still schema-valid.

    Without this, every assertion about "the JSON path works" above and below would hold on a
    run where the scoring path never executed.
    """
    assets = ranked["document"]["assets"]
    states = [asset["state"] for asset in assets]
    assert len(assets) == CANDIDATE_COUNT
    assert states.count("scored") >= 1, "no asset was scored, so the scoring path never ran"
    assert states.count("abstained") >= 1, "no asset abstained, so the refusal path never ran"


def test_each_asset_carries_exactly_the_keys_its_state_declares(ranked: dict):
    """The union is discriminated by which keys are present, not by nulls in a wide record."""
    for asset in ranked["document"]["assets"]:
        if asset["state"] == "scored":
            assert {"score", "interval", "rank"} <= asset.keys()
            assert not {"reason", "explanation", "measurement"} & asset.keys()
            assert 0.0 < asset["score"] <= 1.0
            assert asset["interval"]["low"] <= asset["score"] <= asset["interval"]["high"]
            assert asset["axes"]["style"] == pytest.approx(asset["score"])
        else:
            assert {"reason", "explanation", "measurement"} <= asset.keys()
            assert not {"score", "interval", "rank"} & asset.keys()
            assert asset["axes"]["style"] is None
            assert asset["explanation"].endswith(".")
            assert asset["measurement"], "an abstention with no measurement explains nothing"


def test_the_axis_vocabulary_is_the_exact_set_on_every_asset(ranked: dict):
    """ADR-0002 states this as an exact set equality, in both states.

    Computed here from the report's own `board.representation.axes` rather than from a constant
    in the package, so a report that dropped an axis fails rather than being compared against
    the vocabulary the code believes in.
    """
    document = ranked["document"]
    expected = {"style", *document["board"]["representation"]["axes"]}
    assert expected == {"style", "palette", "tone", "composition"}
    for asset in document["assets"]:
        assert set(asset["axes"]) == expected, asset["asset_id"]
        for axis in document["board"]["representation"]["axes"]:
            assert 0.0 <= asset["axes"][axis] <= 1.0


def test_the_report_names_the_board_it_was_scored_against(ranked: dict, board_path: Path):
    """ADR-0002's `board.id` is ADR-0005's board hash, which is why it is computed once."""
    document = ranked["document"]
    board = read_board(board_path)
    assert document["board"]["id"] == board.board_id
    assert document["board"]["fit"]["k"] == board.k
    assert document["board"]["fit"]["cluster_cut"] == board.cluster_cut
    assert document["board"]["fit"]["dup_cut"] == board.dup_cut
    assert document["board"]["n_references"] == BOARD_SIZE
    assert len(document["references"]) == BOARD_SIZE


def test_every_reference_in_the_catalogue_carries_a_decodable_thumbnail(ranked: dict):
    """ADR-0002 requires the thumbnails inline so a viewer needs no network access.

    Decoding each one is what separates a real thumbnail from a well-formed base64 field: a
    string of the right shape satisfies the schema and shows nothing.
    """
    import base64

    for entry in ranked["document"]["references"]:
        thumbnail = entry["thumbnail"]
        raw = base64.b64decode(thumbnail["data_base64"], validate=True)
        with Image.open(io.BytesIO(raw)) as decoded:
            assert decoded.format == "PNG"
            assert decoded.size == (thumbnail["width"], thumbnail["height"])
        assert max(thumbnail["width"], thumbnail["height"]) <= 128


def test_the_scores_are_multiples_of_the_grid_their_own_n_local_defines(ranked: dict):
    """ADR-0003's conformal p-value takes values in {1, 2, ... , n+1} / (n + 1).

    A score off that grid is not a p-value from this construction whatever else it is, and the
    check reads `n_local` off each asset rather than off the board, so it stays correct on a
    report whose assets landed in categories of different sizes.
    """
    for asset in ranked["document"]["assets"]:
        if asset["state"] != "scored":
            continue
        grid = 1.0 / (asset["n_local"] + 1.0)
        steps = asset["score"] / grid
        assert steps == pytest.approx(round(steps), abs=1e-9), (
            f"{asset['asset_id']} scored {asset['score']}, which is not a multiple of "
            f"1/({asset['n_local']} + 1)"
        )


def test_the_ranking_covers_the_scored_assets_and_is_a_competition_ranking(ranked: dict):
    """ADR-0002 excludes abstained assets from the ranking and ranks the rest by competition.

    Competition ranking means two assets tied at rank 3 are followed by rank 5. A candidate set
    of distinct images cannot tell that policy apart from numbering positions from one, because
    the two agree whenever every score differs, so the fixture carries one exact tie and this
    test asserts the tie is there before asserting the policy. Without the first assertion a
    later change to the fixture would silently turn this into a test of the weaker claim.
    """
    scored = [asset for asset in ranked["document"]["assets"] if asset["state"] == "scored"]
    ordered = sorted(scored, key=lambda asset: (-asset["score"], asset["asset_id"]))
    scores = [asset["score"] for asset in ordered]
    assert len(set(scores)) < len(scores), "the fixture realised no tie, so the policy is untested"

    expected: list[int] = []
    for position, score in enumerate(scores):
        if position and score == scores[position - 1]:
            expected.append(expected[-1])
        else:
            expected.append(position + 1)
    assert [asset["rank"] for asset in ordered] == expected

    assert min(expected) == 1
    abstained = [asset for asset in ranked["document"]["assets"] if asset["state"] == "abstained"]
    assert not any("rank" in asset for asset in abstained)


# ---------------------------------------------------------------------------
# report: the JSON path closes the loop
# ---------------------------------------------------------------------------


def test_report_reads_back_what_rank_wrote_and_summarises_it(ranked: dict):
    """The third command closes the path: rank writes, report reads it back and revalidates."""
    code, out, err = _run(["report", str(ranked["path"])])
    assert code == 0, err
    assert "is a valid schema 1.0 report" in out

    document = ranked["document"]
    scored = [asset for asset in document["assets"] if asset["state"] == "scored"]
    abstained = [asset for asset in document["assets"] if asset["state"] == "abstained"]
    assert f"scored         {len(scored)}" in out
    assert f"abstained      {len(abstained)}" in out
    for asset in document["assets"]:
        assert asset["asset_id"] in out


def test_report_refuses_a_document_that_no_longer_satisfies_the_contract(
    tmp_path: Path, ranked: dict
):
    """The read-back validates rather than parsing, so a hand-edited report does not pass.

    This is the control on the test above. A `report` command that printed a summary without
    checking anything would satisfy that test and fail this one.
    """
    document = json.loads(json.dumps(ranked["document"]))
    for asset in document["assets"]:
        asset["axes"].pop("palette", None)
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(document), encoding="utf-8")

    code, _, err = _run(["report", str(broken)])
    assert code == 1
    assert "moodboard:" in err


# ---------------------------------------------------------------------------
# report --html: the one refusal in the package
# ---------------------------------------------------------------------------


def test_report_html_raises_not_implemented(ranked: dict, tmp_path: Path):
    """The viewer is a separate artifact, so this flag refuses rather than writing a part of one."""
    destination = tmp_path / "viewer.html"
    with pytest.raises(NotImplementedError) as raised:
        _run(["report", str(ranked["path"]), "--html", str(destination)])

    assert "not implemented" in str(raised.value)
    assert not destination.exists(), "the refusal still left a file behind"


def test_the_html_refusal_does_not_depend_on_the_report_being_readable(tmp_path: Path):
    """The refusal comes first, so it cannot be confused with a failure to read the input.

    A flag that raised only after parsing would report the same exception for an unimplemented
    renderer and for a renderer that worked but was handed a broken file.
    """
    destination = tmp_path / "viewer.html"
    with pytest.raises(NotImplementedError):
        _run(["report", str(tmp_path / "there-is-no-such-report.json"), "--html", str(destination)])

    assert not destination.exists()


def test_the_html_refusal_is_not_swallowed_into_an_exit_code(ranked: dict, tmp_path: Path):
    """`main` catches the errors it can describe and re-raises this one deliberately.

    An unimplemented surface returning exit code 1 would be indistinguishable from a run that
    failed, which is the distinction a caller needs in order to know that producing the JSON
    and handing it to the viewer is the supported path.
    """
    with pytest.raises(NotImplementedError):
        cli.main(["report", str(ranked["path"]), "--html", str(tmp_path / "viewer.html")])


def test_the_html_flag_is_the_only_unimplemented_surface_in_the_package():
    """`IMPLEMENTATION_CONTRACT.md` allows exactly one `NotImplementedError` in the package.

    Counting the sites rather than trusting the docstring: a stub added later would be caught
    here even if it was added with a plausible comment beside it.
    """
    import ast

    package = Path(cli.__file__).parent
    sites: list[str] = []
    for source_path in sorted(package.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(raised, ast.Name) and raised.id == "NotImplementedError":
                sites.append(f"{source_path.name}:{node.lineno}")

    assert len(sites) == 1, f"expected one NotImplementedError in the package, found {sites}"
    assert sites[0].startswith("cli.py:")

#!/usr/bin/env python3
"""Regenerate every measured figure the encoder-revision records cite.

    uv run python eval/encoder_revision_figures.py

WHY THIS EXISTS. `docs/adr/0004-abstention.md` and the `ClassicalEncoder` docstring in
`moodboard/encoders.py` both cite specific measured numbers comparing encoder revision 1
against revision 2. Until this script existed, none of those numbers had a reproducing
command anywhere in the tree. A measured number in a record either carries the command that
regenerates it or it is a claim about the past that no reader can check, and it decays
silently because nothing fails when it stops being true.

WHAT REVISION 1 WAS. Revision 1 concatenated the three descriptor blocks raw and normalised
the concatenation once, so the block with the largest raw magnitude dominated the direction.
Revision 2 normalises each block to unit length first, then concatenates, then normalises the
whole, so each of the three carries exactly one third of the squared energy. Revision 1 is not
in the shipped code, so it is reconstructed here from `_feature_parts`, which is the same
source the current encoder builds from. Reconstructing it is the only way to keep the
comparison honest; the alternative is to trust the historical numbers forever.

THE GENERATOR IS IMPORTED, NOT COPIED. The synthetic swatch families live in
`tests/test_cli.py`. Copying them here would create a second definition that drifts from the
one the suite actually exercises, and a figure measured on a drifted population is worse than
no figure. The import is the point, not a convenience.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from moodboard import axes  # noqa: E402
from moodboard.encoders import ClassicalEncoder, _feature_parts, _unit  # noqa: E402

# Imported deliberately -- see the module docstring.
from test_cli import FAMILIES, IMAGE_SIZE, _draw  # noqa: E402

# Pinned population. Every figure below is a statement about THIS population and no other,
# which is why the seed and the count are named here rather than passed in.
SEED = 20260808
PER_FAMILY = 60
SUB_LOOK_CUT = 0.35   # average-linkage sub-look cut, ADR-0004
DUPLICATE_CUT = 0.05  # single-linkage duplicate cut, ADR-0005


def revision_1(image: np.ndarray) -> np.ndarray:
    """The withdrawn construction: concatenate the raw blocks, normalise once."""
    return _unit(np.concatenate(_feature_parts(image)))


def revision_2(image: np.ndarray) -> np.ndarray:
    """The shipped construction, taken from the encoder itself rather than reimplemented."""
    return ClassicalEncoder().embed([image]).astype(np.float64)[0]


def block_energy_shares(image: np.ndarray) -> tuple[float, float, float]:
    """Share of squared energy held by each block under revision 1."""
    parts = _feature_parts(image)
    energies = np.array([float(np.dot(p, p)) for p in parts], dtype=np.float64)
    return tuple(energies / energies.sum())


def _subjects() -> dict[str, np.ndarray]:
    """The three synthetic subjects the encoder docstring names."""
    rng = np.random.default_rng(SEED)
    height, width = IMAGE_SIZE
    return {
        "uniform noise": rng.random((height, width, 3)),
        "gradient": np.repeat(
            np.linspace(0.0, 1.0, width)[None, :, None], height, axis=0
        ) * np.ones((1, 1, 3)),
        "flat colour": np.full((height, width, 3), 0.42),
    }


def _moved_square() -> tuple[np.ndarray, np.ndarray]:
    """Identical pixel content, one bright square moved corner to corner.

    This is the decisive probe: the two images differ only in WHERE the salient region sits,
    so palette and tone are identical by construction and composition is the entire signal.
    """
    height, width = IMAGE_SIZE
    side = min(height, width) // 4
    a = np.full((height, width, 3), 0.15)
    b = np.full((height, width, 3), 0.15)
    a[:side, :side] = 0.95
    b[height - side :, width - side :] = 0.95
    return a, b


def main() -> int:
    print(f"population: seed {SEED}, {PER_FAMILY} images per family, "
          f"families {sorted(FAMILIES)}")
    print()

    print("1. BLOCK ENERGY SHARE UNDER REVISION 1 (palette, tone, composition)")
    composition_shares = []
    for name, image in _subjects().items():
        pal, tone, comp = block_energy_shares(image)
        composition_shares.append(comp)
        print(f"   {name:<14} palette {pal:.6f}  tone {tone:.6f}  composition {comp:.3e}")
    worst = max(composition_shares)
    print(f"   composition share, largest of the three: {worst:.3e} "
          f"= {100 * worst:.1f}% of revision-1 energy")
    print()

    print("2. A COMPOSITION-ONLY SUBJECT, INDEPENDENT OF THE ONE THE RECORDS CITE")
    print("   The 0.2318 / 5.96e-08 pair in the encoder docstring belongs to the probe kept in")
    print("   tests/test_encoders.py, which is that figure's reproducing artifact and is a")
    print("   must-pass test with an explicit floor. This subject is a DIFFERENT square, run")
    print("   here only to show the effect is not particular to that one pair. Do not quote")
    print("   these two numbers as the record's -- they are a second witness, not a restatement.")
    a, b = _moved_square()
    comp_distance = float(axes.composition_distance(a, b))
    r1a, r1b = revision_1(a), revision_1(b)
    r2a, r2b = revision_2(a), revision_2(b)
    print(f"   axes.composition_distance      {comp_distance:.4f}")
    print(f"   revision 1 cosine distance     {1.0 - float(r1a @ r1b):.3e}")
    print(f"   revision 2 cosine distance     {1.0 - float(r2a @ r2b):.4f}")
    print()

    print("3. PAIR-DISTANCE DISTRIBUTION, REVISION 1 vs REVISION 2")
    rng = np.random.default_rng(SEED)
    images = [_draw(rng, family) for family in sorted(FAMILIES) for _ in range(PER_FAMILY)]
    m1 = np.asarray([revision_1(im) for im in images])
    m2 = np.asarray([revision_2(im) for im in images])

    iu = np.triu_indices(len(images), k=1)
    d1 = (1.0 - m1 @ m1.T)[iu]
    d2 = (1.0 - m2 @ m2.T)[iu]

    print(f"   pairs: {d1.size}")
    for label, d in (("revision 1", d1), ("revision 2", d2)):
        print(f"   {label}: median {np.median(d):.4f}  "
              f"above {SUB_LOOK_CUT} cut {100 * np.mean(d > SUB_LOOK_CUT):.1f}%  "
              f"above {DUPLICATE_CUT} cut {100 * np.mean(d > DUPLICATE_CUT):.1f}%")
    print()
    print("   The asymmetry ADR-0005 predicts: the sub-look cut moves a large share of pairs "
          "across it,")
    print("   while the duplicate cut barely moves, because near-coincident pairs stay "
          "near-coincident")
    print("   under any reweighting of the blocks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

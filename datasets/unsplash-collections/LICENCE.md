# Unsplash Lite — licence, and why this row is BLOCKED

**This dataset cannot serve the brand-photography claim as this project
intended, and the reason is the licence, not the data.** The fetch script beside
this file works and has been run. Its output stays local and is git-ignored.

## What was obtained

| | |
|---|---|
| source | `https://unsplash.com/data/lite/latest` |
| release | dated 2026-06-25, obtained 2026-08-07 |
| archive sha256 | `aa0fcb859040ed64e93817d1d878d0c6f861763283261ba1a6aa5d8d4af6aec6` |
| size | 320,024,071 bytes |
| terms read | `TERMS.md`, shipped inside the archive |

## The clauses that block it

Read from the archive's own `TERMS.md`, quoted rather than paraphrased because
the paraphrase in this project's first draft is exactly what went wrong.

**Section 2.A, the Lite grant.** A "non-exclusive, non-transferable,
non-sublicensable license to download and store" the data and "**internally** use
the Commercial Licensed Data to train machine learning models or algorithms for
**your internal business purposes**."

**Section 3, restrictions.** Without written permission you must not:

> A. disclose, deliver, disseminate, or publish any portion of the Licensed Data
> in any manner;
>
> B. sublicense, resell, relicense or redistribute the Licensed Data in whole or
> in part;
>
> F. publish or publicly disclose the results of any comparison of the Datasets
> or Licensed Data to similar datasets.

## Why that is fatal to the row as designed

This project's rule is that images are never committed and the repository
carries a manifest instead. That rule is sound for a source whose restriction is
on the images. **It does not work here, because 3.A restricts publishing any
portion of the data in any manner, and a manifest of photo ids, image URLs,
keywords, photographer usernames, dimensions and camera models is a portion of
the data.** The workaround and the restriction are aimed at different things.

Two further problems, either of which would be enough on its own:

- 2.A grants **internal** use. A public tool whose published validation rests on
  this data is not internal use, whatever is done with the manifest.
- 3.F bars publishing comparisons between datasets. The brand measurement is a
  comparison of *representations*, not of datasets, so this clause probably does
  not bite. "Probably" is not the standard to publish against.

## What is committed, and what is not

| artifact | committed | why |
|---|---|---|
| `fetch.py` | yes | a method, containing no data from the source |
| `manifest.jsonl` | **no**, git-ignored | a portion of the Licensed Data |
| `checksums.sha256` | yes | a one-way digest is not a portion of the data, and it lets anyone who runs the fetch themselves confirm they built the identical manifest |
| the archive and extracted TSVs | no, git-ignored | the data itself |

## What was measured before the licence stopped it, kept because it stands on its own

The work was not wasted: it refuted the protocol's central assumption about this
source, and that refutation is independent of the licence question. It is
recorded in `DATASETS.md` under `content-invariance-brand`. In short, collection
membership is not a human grouping of a coherent look, and photographer identity
is a far better style grouping. Any replacement source should be assessed with
the same test rather than on the same assumption.

## Routes forward, in the order they should be tried

1. **A permissively-licensed source.** Preferred. Openly licensed photography
   with per-item licence metadata, so the manifest is publishable and the
   recipe reproduces. This is the route the project's own dataset rule assumes.
2. **The Unsplash public API under the Unsplash License**, which is a different
   and far more permissive instrument than these Dataset Terms and governs the
   photos on the site. Whether it permits publishing a derived manifest is a
   separate question that would have to be answered at its own source, not
   assumed from this one.
3. **Written permission from Unsplash**, which section 3 explicitly contemplates.
   Slowest, and it makes reproduction depend on a permission the reader does not
   have.

Do not resolve this by quietly narrowing what the manifest contains until it
feels small enough to publish. The grant is for internal use; the size of the
excerpt is not the issue.

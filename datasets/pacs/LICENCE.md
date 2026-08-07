# PACS — licence and provenance

**Short version: the licence terms are unresolved, this repository redistributes
no images, and no claim here should be read as permission for you to.**

## What was obtained, and from where

| | |
|---|---|
| route used | Hugging Face hub, `flwrlabs/pacs` |
| revision pinned | `394113073258ead631f617d2e13bb377c0715c4b` |
| file | `data/train-00000-of-00001.parquet`, 191,395,900 bytes |
| sha256 | `4fc041ee92eec6043fe6e2859e8bdd138e5f958bc621afd153879812cbe65ff5` |
| obtained | 2026-08-07, route confirmed working on that date |
| licence declared by that mirror | `unknown` |

## Why a mirror, when the original release would be better provenance

The routes normally cited for PACS were all checked on 2026-08-07 and none of
them served the data:

| route | result |
|---|---|
| the DomainBed helper's Google Drive object | HTTP 404, a 1,652 byte HTML error page |
| the paper's project page at Queen Mary | HTTP 404 |
| the authors' lab download site | did not resolve |

Each was probed with a reachable control in the same call, so this records three
dead routes rather than one unreachable network.

That leaves a mirror. A mirror is weaker provenance than an original release,
for a specific reason worth stating: the licence terms travel with the original
distribution, and when the original distribution disappears, what remains is a
copy whose uploader has declared the licence `unknown`. Nobody in that chain has
asserted terms that can be relied on.

## What the upstream terms probably are, and why "probably" is the honest word

PACS was introduced in Li, Yang, Song and Hospedales, *Deeper, Broader and
Artier Domain Generalization*, ICCV 2017 (arXiv:1710.03077). Datasets released
alongside a paper in this area are conventionally offered for non-commercial
research use, and that is the assumption the wider literature has operated
under. This project has not been able to verify those terms at their source,
because the source is gone.

PACS is itself assembled from other collections — the dataset card names
Caltech256, Sketchy, TU-Berlin and Google Images as the sources its seven
classes were intersected from. So the images carry upstream terms of their own,
which differ per source and are not enumerated anywhere in the distribution.
A single licence line could not have been accurate even if one had been given.

## What this repository does about it

- **No images are committed.** `manifest.jsonl` carries an id, a content hash,
  the style group and the content group for each of the 9,991 items, and nothing
  else. It is a description of the data, not the data.
- **The fetch script pins an exact revision and an exact checksum** and refuses
  to proceed on a mismatch, so anyone reproducing a measurement gets the same
  bytes or a loud failure.
- **Use here is non-commercial research**: measuring whether a style
  representation responds to how an image looks rather than to what it depicts.
- **If you are considering commercial use, this file is not your answer.**
  Resolve the terms with the original authors first. The correct conclusion from
  an unresolved licence is that it is unresolved.

## If the terms are resolved later

Record what settled it and the date, in this file, beside the route. A licence
question that gets answered in conversation and not written down comes back.

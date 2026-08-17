# Release checklist and notes template

A release is a claim that a specific tree works as documented. This file is the checklist that
backs the claim and the template for the notes that publish it.

## Checklist

Every box is checked against the exact commit being released, in order.

- [ ] Version bumped in `pyproject.toml` and `viewer/package.json` to the same value, with
      `viewer/package-lock.json` re-synced (`npm --prefix viewer install --package-lock-only`);
      the version appears nowhere else by hand. One release version is shared by the Python
      project and the viewer package (ADR-0007)
- [ ] `uv sync --locked` succeeds from clean; `uv.lock` is current with `pyproject.toml`
- [ ] `uv run pytest -q -rA` passes in full; xfails are read, not just counted
- [ ] `uv run ruff check .` is clean
- [ ] `npm --prefix viewer ci && npm --prefix viewer run test:ci` passes
- [ ] Committed `viewer/dist-static` is byte-identical to a fresh build (CI proves this; a
      release re-checks it locally)
- [ ] A wheel builds from clean and `tests/test_packaging.py` passes against it
- [ ] The packaged viewer manifest's `viewer_version` equals the Python project version; a
      release with disagreeing versions is not shippable
- [ ] Schema versions shipped match the schemas documented; no schema file changed without a
      version change
- [ ] `CHANGELOG` / release notes drafted from the template below, and every claim in them
      names its reproducing artifact
- [ ] Tag created from the verified commit; the tag is annotated with the version

## Release notes template

```markdown
## vX.Y.Z — YYYY-MM-DD

### Highlights

<!-- Two to five bullets. What a user can now do, stated without adjectives. -->

### Contract changes

<!-- Schema versions added or amended, INTERFACES.md changes, decision records accepted or
     superseded. "none" if empty. This section is why a reader can upgrade safely. -->

### Measurements

<!-- Only measurements whose reproducing artifact is in the repository at this tag, each with
     the command that reproduces it. "none new" is the honest default. -->

### Compatibility

<!-- Supported Python range, report schema versions read and written, anything removed. -->
```

The standing rule applies to release notes with full force: a claim lands here only after the
artifact that reproduces it lands in the repository.

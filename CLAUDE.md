# CLAUDE.md

Read [`AGENTS.md`](AGENTS.md). It is the single agent guide for this repository, and it is
deliberately the only copy: duplicating its content here would create a second version that
drifts, so this file stays a pointer.

The two facts most often needed before the first edit:

- Verify with `uv run pytest -q -rA` and `uv run ruff check .`; the suite is offline and
  deterministic by design, and CI runs exactly these.
- If a change affects viewer output, rebuild with `npm --prefix viewer run build` and commit
  the resulting `viewer/dist-static` diff, or CI's byte-identity check will fail.

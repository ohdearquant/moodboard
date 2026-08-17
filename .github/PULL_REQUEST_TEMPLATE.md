<!-- One concern per pull request. Keep every section; write "none" rather than deleting one. -->

## What changed

<!-- Two or three sentences. What a reviewer will find in the diff, not a restatement of it. -->

## Why

<!-- The problem or decision this serves. Cite the decision record if one governs it. -->

## How it was verified

<!-- The exact commands run and their outcome. "CI is green" is the floor, not the answer. -->

```
uv run pytest -q -rA
uv run ruff check .
```

## Contract impact

<!-- Any change to a schema version, INTERFACES.md signature, decision record, or generated
     artifact. "none" if the diff touches no contract. -->

## Checklist

- [ ] One concern; the title follows `type: lowercase summary`
- [ ] Tests cover the change; the suite is deterministic and offline
- [ ] No generated file was hand-edited; `viewer/dist-static` rebuilt and committed if viewer
      output changed
- [ ] Documents that state what this change alters were updated in this PR

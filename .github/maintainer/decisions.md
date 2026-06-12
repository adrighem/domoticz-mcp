# Maintainer Decisions

## 2026-06-11 - Initialize maintainer state

- Decision: add repo-local maintainer memory under `.github/maintainer/`.
- Rationale: the repository had no open issues or pull requests, so the useful baseline is persistent context plus a health snapshot for future delta runs.
- Verification: GitHub open issue and PR lists were empty; `uv run --extra dev pytest -q` passed with 37 tests; `python -m build` produced sdist and wheel.

## 2026-06-11 - Implement maintenance recommendations

- Decision: add README-documented MCP resource aliases, centralize encoded Domoticz command URL construction for user-controlled query params, and correct the README HTTP lifecycle claim.
- Rationale: these changes reduce support confusion around resource URIs and prevent malformed write requests when names or values contain reserved URL characters.
- Verification: `uv run --extra dev pytest -q` passed with 40 tests; `uv run --extra dev python -m build` produced sdist and wheel.

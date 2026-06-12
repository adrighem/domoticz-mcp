# Maintainer Decisions

## 2026-06-11 - Initialize maintainer state

- Decision: add repo-local maintainer memory under `.github/maintainer/`.
- Rationale: the repository had no open issues or pull requests, so the useful baseline is persistent context plus a health snapshot for future delta runs.
- Verification: GitHub open issue and PR lists were empty; `uv run --extra dev pytest -q` passed with 37 tests; `python -m build` produced sdist and wheel.

## 2026-06-11 - Implement maintenance recommendations

- Decision: add README-documented MCP resource aliases, centralize encoded Domoticz command URL construction for user-controlled query params, and correct the README HTTP lifecycle claim.
- Rationale: these changes reduce support confusion around resource URIs and prevent malformed write requests when names or values contain reserved URL characters.
- Verification: `uv run --extra dev pytest -q` passed with 40 tests; `uv run --extra dev python -m build` produced sdist and wheel.

## 2026-06-12 - Add high-impact confirmations and localhost default

- Decision: require `confirm=True` for raw API calls, device hiding, user variable deletion, event updates, restarts, and security panel changes; default Domoticz to localhost; update OAuth refresh to replace active client headers before retrying.
- Rationale: preserve trusted local/LAN operation while reducing accidental high-impact actions and removing the project-specific external default URL.
- Verification: `uv run --extra dev pytest -q` passed with 43 tests; `uv run --extra dev python -m build` produced sdist and wheel.

## 2026-06-12 - Improve energy analytics

- Decision: normalize Domoticz energy readings into numeric power, energy, and volume fields; classify electricity consumption, generation, export, gas, water, and storage; return summary totals plus top consumer rankings.
- Rationale: the prior output was a raw row list and did not provide enough structure for useful home energy monitoring.
- Verification: `uv run --extra dev pytest -q` passed with 43 tests; `uv run --extra dev python -m build` produced sdist and wheel.

## 2026-06-12 - Add energy history tools

- Decision: expose daily, weekly, and monthly energy history as read-only MCP tools rather than primary resources.
- Rationale: history retrieval is parameterized by device `idx` or `name`, period behavior, and daily instantaneous mode. Tools fit that query shape better than static resources, while still being safe because they perform read-only Domoticz graph calls.
- Verification: `uv run --extra dev pytest -q` passed with 44 tests; `uv run --extra dev python -m build` produced sdist and wheel.

## 2026-06-12 - Align README with registered MCP surface

- Decision: rewrite README feature lists to document only registered tools, resources, and prompts; update the energy prompt text to reference actual resources/tools.
- Rationale: the README previously listed planned or internal capabilities as public MCP features, which would mislead users and clients.
- Verification: `uv run --extra dev pytest -q` passed with 44 tests; `uv run --extra dev python -m build` produced sdist and wheel.

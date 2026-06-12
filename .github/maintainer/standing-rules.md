# Standing Rules

- Do not comment, close, label, merge, release, or otherwise act publicly on GitHub without explicit human approval.
- Never merge external PRs directly; extract intent and implement the accepted change locally.
- Before shipping behavior changes, run the focused tests and the broader suite when practical.
- Prefer `uv run --extra dev pytest -q` for local test verification in this repo.
- Keep documentation and registered MCP capabilities in sync.

# Maintenance Brief - 2026-06-11

## Snapshot

- Repository: `adrighem/domoticz-mcp`
- Open issues: 0
- Open pull requests: 0
- Current branch: `main`
- Working tree before maintainer files: clean

## Verification

- `uv run --extra dev pytest -q`: 37 passed
- `python -m build`: succeeded
- `python -m pytest -q`: failed during collection because the system Python environment does not have `respx`; the system environment is externally managed, so verification used `uv`.

## Top Backlog

1. Align README resource URI claims with registered MCP resources, or add aliases for the documented forms.
   Impact: reduces client confusion and support requests.
   Status: implemented on 2026-06-11.

2. Encode user-controlled query parameters consistently in write tools such as user variable add/update.
   Impact: prevents broken requests for names/values containing spaces, `&`, `=`, or other reserved characters.
   Status: implemented on 2026-06-11.

3. Reconcile the README connection-pooling claim with `DomoticzClient` behavior.
   Impact: either the implementation should reuse a global client safely, or the docs should stop promising persistent pooling.
   Status: implemented on 2026-06-11 by updating docs to match current behavior.

## Public Actions

None. No public GitHub action was taken.

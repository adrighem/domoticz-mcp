# Maintainer Runs

## 2026-06-11T16:44:21+02:00

- Mode: Maintain
- Scope: repository queue and local health baseline
- GitHub queue: 0 open issues, 0 open pull requests
- Verification:
  - `uv run --extra dev pytest -q` - 37 passed
  - `python -m build` - succeeded
  - `python -m pytest -q` - blocked by missing `respx` in system Python
- Notes: the installed maintainer skill is missing its referenced scripts and templates, so this run used GitHub MCP tools and local inspection.
- Brief: `.github/maintainer/work/2026-06-11-maintenance-brief.md`

## 2026-06-11T16:48:06+02:00

- Mode: Ship
- Scope: implement maintenance recommendations from the 2026-06-11 brief
- Changes:
  - Added documented resource aliases for device, room, scene, user variable, and log resources.
  - Added encoded Domoticz command URL construction and applied it to write/raw tools with user-controlled query values.
  - Updated README wording for scene resources, typed device aliases, log aliases, and HTTP lifecycle behavior.
- Verification:
  - `uv run --extra dev pytest -q` - 40 passed
  - `uv run --extra dev python -m build` - succeeded

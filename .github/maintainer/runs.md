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

## 2026-06-12T09:58:34+02:00

- Mode: Ship
- Scope: implement review findings 1-3 after excluding intentional local/LAN HTTP openness
- Changes:
  - Added `confirm=True` gates for high-impact raw, destructive, event update, restart, and security panel tools.
  - Changed the default Domoticz URL to `http://127.0.0.1:8080`.
  - Updated OAuth refresh retry handling to replace active client authorization headers.
  - Updated README safety/default notes.
- Verification:
  - `uv run --extra dev pytest -q` - 43 passed
  - `uv run --extra dev python -m build` - succeeded

## 2026-06-12T10:39:13+02:00

- Mode: Ship
- Scope: improve energy monitoring output
- Changes:
  - Added parsing helpers for power, energy, and volume readings.
  - Added energy device classification for electricity, generation, storage, gas, and water.
  - Updated `analyze_energy_usage` to return summary totals, net electricity, top current consumers, top daily consumers, and normalized per-device rows.
  - Updated README energy wording.
- Verification:
  - `uv run --extra dev pytest -q` - 43 passed
  - `uv run --extra dev python -m build` - succeeded

## 2026-06-12T11:51:43+02:00

- Mode: Ship
- Scope: add daily, weekly, and monthly energy history tools
- Changes:
  - Added read-only `get_daily_energy_history`, `get_weekly_energy_history`, and `get_monthly_energy_history` tools.
  - Added Domoticz counter graph range handling, including weekly custom date ranges.
  - Added normalization and summaries for history rows with power, energy, and volume values.
  - Updated README and `energy_audit` prompt.
- Verification:
  - `uv run --extra dev pytest -q` - 44 passed
  - `uv run --extra dev python -m build` - succeeded

## 2026-06-12T14:02:59+02:00

- Mode: Grow
- Scope: align README with actual MCP registrations
- Changes:
  - Rewrote README tool categories to include only registered tools.
  - Removed unregistered prompt claims from README.
  - Updated `energy_audit` prompt to reference `domoticz://devices` and energy history tools instead of an internal helper.
- Verification:
  - `uv run --extra dev pytest -q` - 44 passed
  - `uv run --extra dev python -m build` - succeeded

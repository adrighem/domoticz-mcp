# Maintainer Patterns

## 2026-06-11

- Open GitHub queue is empty.
- The project has focused tests around Domoticz API request construction, MCP resources/prompts, auth handling, and DNS rebinding behavior.
- README/API alignment matters because clients discover MCP capabilities from docs and resource templates.
- Query-string construction is an ongoing risk area where user-controlled values should be encoded consistently.
- When adding resource aliases, prefer thin wrappers around existing internal functions so documented URI variants do not fork behavior.
- High-impact tools should use explicit `confirm=True` gates, while routine local/LAN control actions can remain ergonomic.
- Default configuration should be generic and local; avoid project-specific personal endpoints in released defaults.
- Energy analytics should preserve raw Domoticz values for traceability while adding normalized numeric fields and summary totals for agents.
- Use tools for parameterized read operations that need named arguments or query behavior; reserve resources for stable URI-addressable context.
- README feature lists should be generated or audited against `@mcp.tool`, `@mcp.resource`, and `@mcp.prompt` registrations before release.

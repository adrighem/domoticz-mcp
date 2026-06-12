# Maintainer Patterns

## 2026-06-11

- Open GitHub queue is empty.
- The project has focused tests around Domoticz API request construction, MCP resources/prompts, auth handling, and DNS rebinding behavior.
- README/API alignment matters because clients discover MCP capabilities from docs and resource templates.
- Query-string construction is an ongoing risk area where user-controlled values should be encoded consistently.
- When adding resource aliases, prefer thin wrappers around existing internal functions so documented URI variants do not fork behavior.

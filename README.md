# Domoticz MCP Server

<div align="center">
  <img src="logo.svg" alt="domoticz-mcp logo" width="200" height="200" />
</div>

[![PyPI Version](https://img.shields.io/pypi/v/domoticz-mcp.svg)](https://pypi.org/project/domoticz-mcp/)
[![Docker Image Version](https://img.shields.io/github/v/release/adrighem/domoticz-mcp?label=docker&logo=docker)](https://github.com/adrighem/domoticz-mcp/pkgs/container/domoticz-mcp)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

A Model Context Protocol (MCP) server for integrating with the [Domoticz](https://www.domoticz.com/) home automation system. This server provides tools to AI assistants (like Claude, Gemini, etc.) to view and control your smart home devices, scenes, user variables, and more.

## Features

The server exposes **Tools** (for active control and modifications), **Resources** (for read-only contextual awareness), and **Prompts** (for guided interaction templates).

### Tools (Actions)
- **Search:** `search_devices_tool` searches devices by name or current data/status text. `search_scripts_tool` searches inside Domoticz event scripts.
- **Authentication:** `start_oauth_login` starts a short-lived login and opens the authorization page locally without returning transaction data. `get_oauth_login_status` reports when the login is complete.
- **Energy History:** `get_daily_energy_history`, `get_weekly_energy_history`, and `get_monthly_energy_history` read counter graph history for energy, gas, and water meters by `idx` or `name`.
- **Device Control:** `toggle_switch`, `set_switch_state`, `set_dimmer_level`, `set_temperature_setpoint`, `control_blinds`, `set_color_brightness`, and `set_color_temperature` control supported devices by `idx` or `name`.
- **Device Management:** `rename_device`, `delete_device`, `create_virtual_sensor`, and `update_device_value` manage devices and virtual sensors.
- **Scenes and Groups:** `switch_scene` activates configured scenes/groups by `idx` or `name`.
- **User Variables:** `add_user_variable`, `update_user_variable`, and `delete_user_variable` manage Domoticz user variables.
- **Event Scripts:** `create_event` and `update_event` create or update internal Domoticz event scripts.
- **System Actions:** `restart_system`, `add_log_message`, `send_notification`, and `set_security_status` call Domoticz system, log, notification, and security APIs.
- **Advanced:** `call_domoticz_api` executes a generic Domoticz command API call and returns only its sanitized status/title.

High-impact tools require `confirm=True`: `call_domoticz_api`, `delete_device`, `delete_user_variable`, `update_event`, `restart_system`, and `set_security_status`.

### Resources (Context)
- **`domoticz://dashboard`**: Read a curated view of favorite and currently active devices (lights on, sensors active).
- **`domoticz://devices`**: Read the current state of all Domoticz devices.
- **`domoticz://device/{idx}`**, **`domoticz://device/{type}/{subtype}/{idx}`**, or **`domoticz://device/name/{name}`**: Read the current state of a specific device. In the typed form, `idx` is authoritative and `type`/`subtype` are descriptive path fields.
- **`domoticz://rooms`**: Read configured rooms (Room Plans).
- **`domoticz://room/{idx}`** or **`domoticz://room/{room_name}/{idx}`**: Read the full states of all devices within a specific room.
- **`domoticz://scenes`**: Read configured scenes.
- **`domoticz://scene/{idx}`** or **`domoticz://scene/name/{name}`**: Read a specific scene/group entry.
- **`domoticz://user-variables`**: Read the list of all Domoticz user variables.
- **`domoticz://user-variable/{idx}`** or **`domoticz://user-variable/name/{name}`**: Read a specific Domoticz user variable.
- **`domoticz://events` & `domoticz://event/{event_id}`**: Read the overview and specific source code of event scripts.
- **`domoticz://logs`** or **`domoticz://log`**: Read the current Domoticz system log.
- **`domoticz://logs/error`**: Read a filtered view containing only 'Error' level entries from the log.
- **`domoticz://security`**: Read the current status of the security panel.
- **`domoticz://settings`**: Read a reviewed, non-sensitive subset of global display/version settings.
- **`domoticz://hardware`**: Read only gateway `idx`, name, type, and enabled status. Connection details and integration configuration are omitted.
- **`domoticz://docs/dzvents_syntax`**: Cheat sheet for writing dzVents automation scripts.
- **`domoticz://docs/blockly_syntax`**: Syntax rules for Blockly XML automations.

### Prompts (Templates)
- **`agent_guidance`**: Orients an AI agent to start with `domoticz://overview`, prefer `idx`, and use health/log resources for troubleshooting.
- **`summarize_home`**: Guides a concise home-state summary from `domoticz://dashboard`.
- **`maintenance_report`**: Guides a health check using battery alerts, system health, and error logs.
- **`energy_audit`**: Guides an energy review using `domoticz://devices` and the energy history tools.

## Performance and Efficiency

- **Caching:** The server implements a 5-minute TTL cache for devices, scenes, user variables, and rooms to significantly reduce API latency and Domoticz load.
- **HTTP Lifecycle:** Reuses a shared `httpx.AsyncClient`, applies consistent timeouts, and closes it through the MCP server lifespan.
- **Bounded Authentication:** MCP requests fail fast with actionable guidance when OAuth needs attention; browser authentication only runs when explicitly requested.
- **Output Safety:** Domoticz responses are recursively sanitized before crossing the MCP boundary. Credential fields, OAuth transaction data, private endpoints, network addresses, and sensitive free-text patterns are redacted; high-risk configuration resources also use strict allowlists.

## Architecture

- **Type Safety:** Full type annotations using Python 3.10+ union syntax for improved IDE support and code clarity.
- **Error Handling:** Structured exception hierarchy (`DomoticzError`, `DeviceNotFoundError`, `AuthenticationError`, etc.) for precise error handling and debugging.
- **Shared Resolution:** Unified `_resolve_idx()` helper function for consistent device/scene/variable lookup patterns.

## Prerequisites

- Python 3.10 or higher
- A running Domoticz instance
- Network access to the Domoticz API

## Installation

### Standard Python Installation (Linux, macOS, Windows)

1. Clone or download this repository.
2. Navigate to the project directory.
3. Install the package using `pip`:

```bash
pip install .
```

This will install the `domoticz-mcp` command-line tool.

### Using `uv` (Recommended)

If you use `uv`, you can run the server directly from the source repository without installing it globally:

```bash
uv run --directory /path/to/domoticz-mcp domoticz-mcp
```

### Docker Installation

You can run the server via Docker. By default, the Docker image runs the server in `sse` (HTTP) mode on port 8000.

```bash
docker run -d \
  --name domoticz-mcp \
  -p 8000:8000 \
  -e DOMOTICZ_URL="http://192.168.1.100:8080" \
  -e DOMOTICZ_USERNAME="your_username" \
  -e DOMOTICZ_PASSWORD="your_password" \
  ghcr.io/adrighem/domoticz-mcp:latest
```

*Note: For the OAuth2 token flow to work and persist in Docker without interactive browser prompts, see the OAuth / API Token section below on how to mount the token file or use headless authentication.*

## Transport Options

The server supports three different transports for clients to connect with:

1. **`stdio` (Default):** Standard input/output. This is what most desktop applications (like Claude Desktop and Gemini CLI) use.
   ```bash
   domoticz-mcp --transport stdio
   ```
2. **`sse` (HTTP Server-Sent Events):** Starts a web server that clients can connect to over HTTP. Ideal for web-based UIs and remote connections. Includes wide-open CORS headers.
   ```bash
   domoticz-mcp --transport sse --port 8000
   ```
   **Connection URL for clients:** `http://localhost:8000/sse`
3. **`streamable-http` (Alternative HTTP):** Starts a web server using an alternative HTTP transport. Required by certain clients (like the llama.cpp WebUI) that expect a single POST endpoint instead of an SSE stream.
   ```bash
   domoticz-mcp --transport streamable-http --port 8000
   ```
   **Connection URL for clients:** `http://localhost:8000/mcp`

## Configuration

The server can be configured via environment variables, a `.env` file, or command-line arguments. **Environment variables take precedence over command-line arguments**, which in turn override the defaults. Using a `.env` file is a convenient way to provide these variables without exposing them in your shell history.

### General Options

| Option | Environment Variable | Default | Description |
|--------|----------------------|---------|-------------|
| `--transport` | `DOMOTICZ_MCP_TRANSPORT`, `TRANSPORT` | `stdio` | Transport to use (`stdio`, `sse`, or `streamable-http`) |
| `--host` | `DOMOTICZ_MCP_HOST`, `HOST` | `127.0.0.1` | Host to bind to for SSE / HTTP |
| `--port` | `DOMOTICZ_MCP_PORT`, `PORT` | `8000` | Port to bind to for SSE / HTTP |
| `--domoticz-url` | `DOMOTICZ_URL` | `http://127.0.0.1:8080` | Base URL of your Domoticz instance |
| `--token-file` | `DOMOTICZ_MCP_TOKEN_FILE`, `TOKEN_FILE` | `~/.config/domoticz-mcp/token.json` | Path to OAuth token storage file |

**Example `.env` file:**
```env
DOMOTICZ_URL=http://192.168.1.100:8080
DOMOTICZ_CLIENT_ID=your_client_id_here
DOMOTICZ_CLIENT_SECRET=your_client_secret_here
```

### Transport Options

By default, the server uses standard input/output (`stdio`) for communication with the MCP client. You can also run it as an HTTP Server-Sent Events (SSE) streaming server using the `--transport sse` argument.

```bash
domoticz-mcp --transport sse --host 127.0.0.1 --port 8000
```

### Authentication Options

You can authenticate the MCP server with Domoticz using either an **OAuth/API Token** (Recommended) or **Basic Auth**.

#### Option 1: OAuth / API Token (Recommended)
This approach uses an OAuth2 token and is generally more secure.

| Option | Environment Variable | Description |
|--------|----------------------|-------------|
| `--domoticz-client-id` | `DOMOTICZ_CLIENT_ID`, `DOMOTICZ_CLIENTID` | Your Application's Client ID |
| `--domoticz-client-secret` | `DOMOTICZ_CLIENT_SECRET`, `DOMOTICZ_CLIENTSECRET` | Your Application's Client Secret |
| `--domoticz-oauth-token` | `DOMOTICZ_OAUTH_TOKEN` | Direct OAuth2 access token (skips flow) |

1. In the Domoticz UI, go to **Setup** -> **More Options** -> **Applications**.
2. Click **Add Application** and configure:
   - **Name**: e.g., `MCP Server`
   - **isPublic**: Check this if you want to use Key-Pair, or leave unchecked for a Shared Secret.
3. Note the generated **Client ID** and **Client Secret**.

**Interactive Flow (Desktop/CLI):**
Run the explicit authentication command in an interactive terminal:

```bash
domoticz-mcp --authenticate
```

The command opens a local browser and waits up to two minutes for the callback without printing the authorization or callback URL. After approval, it stores the token with owner-only file permissions and starts the MCP server. Normal MCP requests never launch a browser; if refresh fails, they return an actionable authentication error instead of waiting indefinitely.

**Interactive Flow from Codex or another MCP client:**

1. Call `start_oauth_login`.
2. Complete approval in the browser opened on the MCP host.
3. Call `get_oauth_login_status` with the returned `flow_id` until its status is `complete`.
4. Retry the original Domoticz operation.

The start tool bounds the local browser launch to five seconds and reuses an existing pending flow, so it does not wait for browser approval. The callback listener binds only to `127.0.0.1`, expires after two minutes, validates OAuth state and PKCE, and never returns authorization URLs, tokens, or authorization codes through MCP.

This tool flow is intended for a local MCP server such as Codex STDIO. Remote containers and execution hosts should use the command-line authentication flow on a browser-capable host or a securely mounted token file.

**Headless Flow (Docker / Server Environments):**
In a Docker container, you have two options:
1. **Password Grant (Easiest):** Provide username and password *in addition to* the Client ID and Secret. The server will automatically perform a headless login to fetch the initial token.
2. **Mount Token File:** Run the server locally once to generate the token file, then mount it into the container.

#### Option 2: Basic Auth
If you prefer traditional username and password authentication:

| Option | Environment Variable | Description |
|--------|----------------------|-------------|
| `--domoticz-username` | `DOMOTICZ_USERNAME` | Your Domoticz username |
| `--domoticz-password` | `DOMOTICZ_PASSWORD` | Your Domoticz password |

1. In the Domoticz UI, go to **Setup** -> **Settings** -> **Security**.
2. Ensure "Allow Basic-Auth authentication over plain HTTP" is enabled (if you are not using HTTPS).

## MCP Client Configuration

### Gemini CLI

Add the following to your `~/.gemini/settings.json` under the `mcpServers` object:

```json
{
  "mcpServers": {
    "domoticz": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/domoticz-mcp",
        "run",
        "domoticz-mcp"
      ],
      "env": {
        "DOMOTICZ_URL": "http://192.168.1.x:8080",
        "DOMOTICZ_CLIENT_ID": "your_client_id_here",
        "DOMOTICZ_CLIENT_SECRET": "your_client_secret_here"
      }
    }
  }
}
```

### Claude Desktop

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "domoticz": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/domoticz-mcp",
        "run",
        "domoticz-mcp"
      ],
      "env": {
        "DOMOTICZ_URL": "http://192.168.1.x:8080",
        "DOMOTICZ_CLIENT_ID": "your_client_id_here",
        "DOMOTICZ_CLIENT_SECRET": "your_client_secret_here"
      }
    }
  }
}
```

If you installed it globally via pip, you can use the command directly:

```json
{
  "mcpServers": {
    "domoticz": {
      "command": "domoticz-mcp",
      "args": [],
      "env": {
        "DOMOTICZ_URL": "http://192.168.1.x:8080",
        "DOMOTICZ_CLIENT_ID": "your_client_id_here",
        "DOMOTICZ_CLIENT_SECRET": "your_client_secret_here"
      }
    }
  }
}
```

### Other MCP Clients

For other clients that support the Model Context Protocol, simply configure them to run the `domoticz-mcp` binary or the `uv run` command with the appropriate environment variables.

## Development and Testing

To develop and run tests for this project:

1. Clone the repository.
2. Install development dependencies using `uv`:
   ```bash
   uv pip install -e ".[dev]"
   ```
3. Run the test suite:
   ```bash
   uv run --extra dev pytest tests/
   ```

4. Build the package locally:
   ```bash
   uv run --extra dev python -m build
   ```

You can also pass `--directory /path/to/domoticz-mcp` to either `uv run` command when running from outside the repository.

## License

This project is licensed under the GNU General Public License v3.0 (GPLv3). See the [LICENSE](LICENSE) file for details.

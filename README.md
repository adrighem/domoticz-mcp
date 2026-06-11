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
- **Search and Discovery:** Use `get_overview` for a high-level system summary and device counts. Use `search_devices` to find devices by name or current status using substring or regex matching.
- **Maintenance:** Proactively identify sensors needing attention with `get_battery_levels` (low battery alerts) and `get_connectivity_report` (devices that haven't checked in). Use `get_system_health` for an overview of hardware gateway status. Check for system updates with `check_for_updates` and execute a system restart with `restart_system`.
- **Energy Analytics:** Use `analyze_energy_usage` to summarize daily consumption across all power reporting devices and meters.
- **Device Control:** Toggle switches, set states (On/Off), set dimmer levels (0-100), set thermostat temperatures (Celsius), control blinds, and manage RGB/color lighting (brightness, hue, color temperature). Supports lookup by `idx` or `name`.
- **Device Management:** Create virtual sensors, rename devices, delete/hide devices, and manually update sensor values. Supports lookup by `idx` or `name`.
- **Rooms and Scenes:** Control scenes/groups. Get room devices by `idx` or `room_name`.
- **User Variables:** Read, add, update, and delete Domoticz user variables. Supports lookup by `idx` or `name`.
- **History and Logs:** Access device history graphs and text/light logs by `idx` or `name`. Retrieve system logs and add custom log messages.
- **System Information:** Get Domoticz instance version, global settings, hardware, sun times, users, and internal event scripts/rules.
- **Security:** Get and set the Domoticz security panel status.
- **Notifications:** Send notifications through the Domoticz notification subsystem.
- **Event Management:** Get, create, update, and **search inside** internal event scripts (`search_scripts`). Supports Blockly, Lua, dzVents, and Python.
- **Cameras and Floorplans:** Retrieve camera configurations and defined floorplans. Get a base64 encoded snapshot using `get_camera_snapshot`.
- **Advanced:** Use `call_domoticz_api` to execute any generic Domoticz API endpoint directly.

### Resources (Context)
- **`domoticz://dashboard`**: Read a curated view of favorite and currently active devices (lights on, sensors active).
- **`domoticz://devices`**: Read the current state of all Domoticz devices.
- **`domoticz://device/{idx}`**, **`domoticz://device/{type}/{subtype}/{idx}`**, or **`domoticz://device/name/{name}`**: Read the current state of a specific device.
- **`domoticz://rooms`**: Read configured rooms (Room Plans).
- **`domoticz://room/{idx}`** or **`domoticz://room/{room_name}/{idx}`**: Read the full states of all devices within a specific room.
- **`domoticz://scenes`**: Read configured scenes.
- **`domoticz://scene/{idx}`** or **`domoticz://scene/name/{name}`**: Read the list of devices belonging to a specific scene.
- **`domoticz://user-variables`**: Read the list of all Domoticz user variables.
- **`domoticz://user-variable/{idx}`** or **`domoticz://user-variable/name/{name}`**: Read a specific Domoticz user variable.
- **`domoticz://events` & `domoticz://event/{event_id}`**: Read the overview and specific source code of event scripts.
- **`domoticz://log`**: Read the current Domoticz system log.
- **`domoticz://logs/error`**: Read a filtered view containing only 'Error' level entries from the log.
- **`domoticz://security`**: Read the current status of the security panel.
- **`domoticz://settings`**: Read global Domoticz settings and configuration.
- **`domoticz://hardware`**: Read the configured hardware gateways.
- **`domoticz://docs/dzvents_syntax`**: Cheat sheet for writing dzVents automation scripts.
- **`domoticz://docs/blockly_syntax`**: Syntax rules for Blockly XML automations.

### Prompts (Templates)
- **`agent_guidance`**: Provides the AI agent with critical knowledge about Domoticz-specific logic and best practices.
- **`summarize_home`**: Instructs the AI to provide a human-readable summary of the home's current state using the dashboard resource.
- **`maintenance_report`**: A comprehensive health check that audits batteries, checks device connectivity, and reviews system logs for errors.
- **`audit_batteries`**: Specifically audits battery levels across all sensors to find those below a certain threshold.
- **`energy_audit`**: Analyzes energy usage across the home to identify the biggest consumers today.
- **`find_devices_by_state`**: Helps find all devices in a particular state (e.g., "show me everything that is open").
- **`troubleshoot_device`**: A template that asks for a device `idx` or `name` and instructs the AI to read the device state and system logs to diagnose issues.
- **`analyze_automations`**: Instructs the AI to review your internal event scripts for logic flaws or optimizations.
- **`write_dzvents_script`**: Helps the AI write a dzVents script by providing syntax rules and device lookup instructions.
- **`write_blockly_script`**: Helps the AI construct Blockly automations using proper XML representation.
- **`system_update_check`**: Guides the AI to check for system updates and verify hardware gateway health.
- **`dashboard_organization`**: Prompts the AI to analyze favorite devices and suggest room-based groupings.

## Performance and Efficiency

- **Caching:** The server implements a 5-minute TTL cache for devices, scenes, user variables, and rooms to significantly reduce API latency and Domoticz load.
- **Connection Pooling:** Uses a persistent `httpx.AsyncClient` with proper lifecycle management for improved efficiency during long-running sessions. The server exposes `close_global_client()` for clean shutdown.

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
| `--domoticz-url` | `DOMOTICZ_URL` | `https://xmpp.vanadrighem.eu/domoticz` | Base URL of your Domoticz instance |
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
When the server runs for the first time natively on your machine, it will print an authorization URL to the console/stderr and attempt to open your browser. After you approve the request, it will save the token to the `token-file` for future use.

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

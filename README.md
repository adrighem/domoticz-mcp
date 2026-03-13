# Domoticz MCP Server

A Model Context Protocol (MCP) server for integrating with the [Domoticz](https://www.domoticz.com/) home automation system. This server provides tools to AI assistants (like Claude, Gemini, etc.) to view and control your smart home devices, scenes, user variables, and more.

## Features

- **Device Control:** Toggle switches, set states (On/Off), set dimmer levels, set thermostat temperatures, and control blinds.
- **Device Information:** Retrieve states for all devices or specific ones.
- **Rooms and Scenes:** List rooms (Room Plans), get devices within rooms, and control scenes/groups.
- **User Variables:** Read, add, update, and delete Domoticz user variables.
- **History and Logs:** Access device history graphs and text/light logs.
- **System Information:** Get Domoticz instance version, global settings, hardware, sun times, users, and internal event scripts/rules.
- **Event Management:** Get, create, and update internal event scripts (Blockly, Lua, dzVents, Python).
- **Cameras and Floorplans:** Retrieve camera configurations and defined floorplans.

## Prerequisites

- Python 3.10 or higher
- A running Domoticz instance
- Network access to the Domoticz API

## Installation

### Using `uv` (Recommended)

If you use `uv`, you can run the server directly from the source repository without installing it globally:

```bash
uv run --directory /path/to/domoticz-mcp domoticz-mcp
```

### Standard Python Installation (Linux, macOS, Windows)

1. Clone or download this repository.
2. Navigate to the project directory.
3. Install the package using `pip`:

```bash
pip install .
```

This will install the `domoticz-mcp` command-line tool.

## Configuration

The server requires configuration to connect to your Domoticz instance. These are provided as environment variables.

- `DOMOTICZ_URL`: The base URL of your Domoticz instance (e.g., `http://192.168.1.100:8080`). Defaults to `https://xmpp.vanadrighem.eu/domoticz` if not set.

### Authentication Options

You can authenticate the MCP server with Domoticz using either an **OAuth/API Token** (Recommended) or **Basic Auth**. 

#### Option 1: OAuth / API Token (Recommended)
This approach uses an OAuth2 token and is generally more secure, as you can revoke the token at any time without changing your password. The server supports an interactive authentication flow so you do not need to provide your username and password in the configuration.

1. In the Domoticz UI, go to **Setup** -> **More Options** -> **Applications**.
2. Click **Add Application** and configure:
   - **Name**: e.g., `MCP Server`
   - **isPublic**: Check this if you want to use Key-Pair, or leave unchecked for a Shared Secret.
3. Note the generated **Client ID** and **Client Secret**.
4. Configure the following environment variables:
   - `DOMOTICZ_CLIENT_ID`: Your Application's Client ID.
   - `DOMOTICZ_CLIENT_SECRET`: Your Application's Client Secret.

When the server runs for the first time, it will print an authorization URL to the console/stderr and attempt to open your browser. After you log in and approve the request, it will save the token to `~/.config/domoticz-mcp/token.json` for future use.

Alternatively, if you want to perform a headless login, you can provide `DOMOTICZ_USERNAME` and `DOMOTICZ_PASSWORD` alongside the Client ID and Secret to use the OAuth2 `password` grant, or you can provide a valid token directly via `DOMOTICZ_OAUTH_TOKEN`.

*Note: If you use a token, you can safely disable "Allow Basic-Auth authentication over plain HTTP" in the Domoticz security settings.*
#### Option 2: Basic Auth
If you prefer traditional username and password authentication without an OAuth application:

1. In the Domoticz UI, go to **Setup** -> **Settings** -> **Security**.
2. Ensure "Allow Basic-Auth authentication over plain HTTP" is enabled (if you are not using HTTPS).
3. Configure the following environment variables:
   - `DOMOTICZ_USERNAME`: Your Domoticz username.
   - `DOMOTICZ_PASSWORD`: Your Domoticz password.

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

## License

This project is licensed under the GNU General Public License v3.0 (GPLv3). See the [LICENSE](LICENSE) file for details.

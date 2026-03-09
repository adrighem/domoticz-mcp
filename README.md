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

You can authenticate the MCP server with Domoticz using either **Basic Auth** or an **OAuth/API Token** (Recommended). 

#### Option 1: OAuth / API Token (Recommended)
This approach uses a Bearer token and is generally more secure, as you can revoke the token at any time without changing your password.

1. In the Domoticz UI, go to **Setup** -> **Settings** -> **Security**.
2. Look for the API Tokens or OAuth2 section and generate a new token for the MCP Server.
3. Configure the following environment variable:
   - `DOMOTICZ_OAUTH_TOKEN`: The token string you generated.

*Note: If you use a token, you can safely disable "Allow Basic-Auth authentication over plain HTTP" in the Domoticz security settings.*

#### Option 2: Basic Auth
If you prefer traditional username and password authentication:

1. In the Domoticz UI, go to **Setup** -> **Settings** -> **Security**.
2. Ensure "Allow Basic-Auth authentication over plain HTTP" is enabled (if you are not using HTTPS).
3. Configure the following environment variables:
   - `DOMOTICZ_USERNAME`: Your Domoticz username.
   - `DOMOTICZ_PASSWORD`: Your Domoticz password.

## MCP Client Configuration

### Gemini CLI

Add the following to your `~/.gemini/settings.json` under the `mcp.servers` object:

```json
{
  "mcp": {
    "servers": {
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
          "DOMOTICZ_OAUTH_TOKEN": "your_token_here",
          "DOMOTICZ_USERNAME": "your_username_if_using_basic_auth",
          "DOMOTICZ_PASSWORD": "your_password_if_using_basic_auth"
        }
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
        "DOMOTICZ_OAUTH_TOKEN": "your_token_here",
        "DOMOTICZ_USERNAME": "your_username_if_using_basic_auth",
        "DOMOTICZ_PASSWORD": "your_password_if_using_basic_auth"
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
        "DOMOTICZ_OAUTH_TOKEN": "your_token_here",
        "DOMOTICZ_USERNAME": "your_username_if_using_basic_auth",
        "DOMOTICZ_PASSWORD": "your_password_if_using_basic_auth"
      }
    }
  }
}
```

### Other MCP Clients

For other clients that support the Model Context Protocol, simply configure them to run the `domoticz-mcp` binary or the `uv run` command with the appropriate environment variables.

## License

This project is licensed under the GNU General Public License v3.0 (GPLv3). See the [LICENSE](LICENSE) file for details.

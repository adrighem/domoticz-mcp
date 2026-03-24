# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp",
#     "httpx",
# ]
# ///

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
import httpx
import os
import sys
import json
import base64
import hashlib
import urllib.parse
import secrets
import webbrowser
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

mcp = FastMCP("Domoticz", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False), stateless_http=True)

DOMOTICZ_BASE_URL = os.environ.get("DOMOTICZ_URL", "https://xmpp.vanadrighem.eu/domoticz")
if DOMOTICZ_BASE_URL.endswith('/json.htm'):
    DOMOTICZ_BASE_URL = DOMOTICZ_BASE_URL[:-9]
    
DOMOTICZ_API_URL = f"{DOMOTICZ_BASE_URL}/json.htm"
DOMOTICZ_USERNAME = os.environ.get("DOMOTICZ_USERNAME")
DOMOTICZ_PASSWORD = os.environ.get("DOMOTICZ_PASSWORD")
DOMOTICZ_CLIENT_ID = os.environ.get("DOMOTICZ_CLIENT_ID", os.environ.get("DOMOTICZ_CLIENTID"))
DOMOTICZ_CLIENT_SECRET = os.environ.get("DOMOTICZ_CLIENT_SECRET", os.environ.get("DOMOTICZ_CLIENTSECRET"))
DOMOTICZ_OAUTH_TOKEN = os.environ.get("DOMOTICZ_OAUTH_TOKEN")

TOKEN_FILE = os.path.expanduser("~/.config/domoticz-mcp/token.json")
_oauth_token_cache: Optional[str] = DOMOTICZ_OAUTH_TOKEN

def _do_interactive_oauth_flow():
    code = None
    state_received = None
    
    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass # Suppress logging to stderr
            
        def do_GET(self):
            nonlocal code, state_received
            parsed_path = urllib.parse.urlparse(self.path)
            if parsed_path.path == '/callback':
                query = urllib.parse.parse_qs(parsed_path.query)
                if 'code' in query:
                    code = query['code'][0]
                if 'state' in query:
                    state_received = query['state'][0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authentication successful!</h1><p>You can close this window now and return to the application.</p></body></html>")
                threading.Thread(target=self.server.shutdown).start()
            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer(('127.0.0.1', 0), CallbackHandler)
    port = server.server_port
    
    state = secrets.token_urlsafe(16)
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')
    
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    auth_url = f"{DOMOTICZ_BASE_URL}/oauth2/v1/authorize?response_type=code&client_id={DOMOTICZ_CLIENT_ID}&redirect_uri={urllib.parse.quote(redirect_uri)}&state={state}&code_challenge={code_challenge}&code_challenge_method=S256"
    
    sys.stderr.write(f"\n==========================================================\n")
    sys.stderr.write(f"Authentication required for Domoticz MCP Server.\n")
    sys.stderr.write(f"Please open this URL in your browser to authenticate:\n\n")
    sys.stderr.write(f"{auth_url}\n\n")
    sys.stderr.write(f"Waiting for authentication on {redirect_uri}...\n")
    sys.stderr.write(f"==========================================================\n\n")
    sys.stderr.flush()
    
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
        
    server.serve_forever()
    
    if code and state_received == state:
        return code, code_verifier, redirect_uri
    return None, None, None

async def _fetch_oauth_token() -> Optional[str]:
    global _oauth_token_cache
    if _oauth_token_cache:
        return _oauth_token_cache
        
    # Try loading from file first
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r') as f:
                token_data = json.load(f)
                if "access_token" in token_data:
                    _oauth_token_cache = token_data["access_token"]
                    return _oauth_token_cache
        except Exception as e:
            sys.stderr.write(f"Failed to load token file: {e}\n")

    if not DOMOTICZ_CLIENT_ID:
        return None

    try:
        async with httpx.AsyncClient() as client:
            discovery_url = f"{DOMOTICZ_BASE_URL}/.well-known/openid-configuration"
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            config = resp.json()
            
            token_endpoint = config.get("token_endpoint")
            if not token_endpoint:
                return None
                
            if "127.0.0.1" in token_endpoint or "localhost" in token_endpoint:
                parsed = urllib.parse.urlparse(token_endpoint)
                token_endpoint = f"{DOMOTICZ_BASE_URL}{parsed.path}"

            if DOMOTICZ_USERNAME and DOMOTICZ_PASSWORD:
                data = {
                    "grant_type": "password",
                    "client_id": DOMOTICZ_CLIENT_ID,
                    "client_secret": DOMOTICZ_CLIENT_SECRET
                }
                token_resp = await client.post(
                    token_endpoint, 
                    auth=(DOMOTICZ_USERNAME, DOMOTICZ_PASSWORD),
                    data=data
                )
            else:
                code, code_verifier, redirect_uri = await asyncio.to_thread(_do_interactive_oauth_flow)
                if not code:
                    return None
                    
                data = {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": DOMOTICZ_CLIENT_ID,
                    "code_verifier": code_verifier
                }
                
                auth = (DOMOTICZ_CLIENT_ID, DOMOTICZ_CLIENT_SECRET) if DOMOTICZ_CLIENT_SECRET else None
                
                token_resp = await client.post(
                    token_endpoint,
                    auth=auth,
                    data=data
                )

            token_resp.raise_for_status()
            token_data = token_resp.json()
            
            _oauth_token_cache = token_data.get("access_token")
            
            # Save token to file
            os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
            with open(TOKEN_FILE, 'w') as f:
                json.dump(token_data, f)
                
            return _oauth_token_cache
    except Exception as e:
        import logging
        logging.error(f"Failed to fetch OAuth token: {e}")
        return None

# Custom AsyncClient wrapper that ensures the token is added
class DomoticzClient:
    def __init__(self):
        self.client = httpx.AsyncClient()
        
    async def __aenter__(self):
        # Determine authentication strategy
        oauth_token = None

        if DOMOTICZ_CLIENT_ID:
            oauth_token = await _fetch_oauth_token()

        if oauth_token:
            self.client.headers["Authorization"] = f"Bearer {oauth_token}"
        elif DOMOTICZ_USERNAME and DOMOTICZ_PASSWORD:
            self.client.auth = (DOMOTICZ_USERNAME, DOMOTICZ_PASSWORD)

        return await self.client.__aenter__()
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self.client.__aexit__(exc_type, exc_val, exc_tb)

def create_client():
    return DomoticzClient()

async def _resolve_device_idx(client, idx: int = None, name: str = None) -> int | None:
    if idx is not None:
        return idx
    if not name:
        return None
    response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
    response.raise_for_status()
    for dev in response.json().get("result", []):
        if dev.get("Name", "").lower() == name.lower():
            return int(dev.get("idx"))
    return None

async def _resolve_scene_idx(client, idx: int = None, name: str = None) -> int | None:
    if idx is not None:
        return idx
    if not name:
        return None
    response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenes")
    response.raise_for_status()
    for scene in response.json().get("result", []):
        if scene.get("Name", "").lower() == name.lower():
            return int(scene.get("idx"))
    return None

async def _resolve_user_variable_idx(client, idx: int = None, name: str = None) -> int | None:
    if idx is not None:
        return idx
    if not name:
        return None
    response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getuservariables")
    response.raise_for_status()
    for var in response.json().get("result", []):
        if var.get("Name", "").lower() == name.lower():
            return int(var.get("idx"))
    return None

@mcp.tool()
async def get_all_devices() -> str:
    """Get all devices and their current states from Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true&order=Name")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_device(idx: int = None, name: str = None) -> str:
    """Get a specific device state by IDX or Name from Domoticz."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid={resolved_idx}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def toggle_switch(idx: int = None, name: str = None) -> str:
    """Toggle a switch or light by IDX or Name in Domoticz."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd=Toggle")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def set_switch_state(state: str, idx: int = None, name: str = None) -> str:
    """Set a switch or light to On or Off by IDX or Name in Domoticz. state must be 'On' or 'Off'."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    if state.lower() not in ['on', 'off']:
        return '{"status": "error", "message": "state must be \'On\' or \'Off\'"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd={state.capitalize()}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def set_dimmer_level(level: int, idx: int = None, name: str = None) -> str:
    """Set the brightness level of a dimmer switch (0-100) by IDX or Name in Domoticz."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    if not (0 <= level <= 100):
        return '{"status": "error", "message": "level must be between 0 and 100"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd=Set%20Level&level={level}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def set_temperature_setpoint(setpoint: float, idx: int = None, name: str = None) -> str:
    """Set the temperature setpoint for a thermostat by IDX or Name in Domoticz."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=setsetpoint&idx={resolved_idx}&setpoint={setpoint}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def control_blinds(command: str, idx: int = None, name: str = None) -> str:
    """Control blinds by IDX or Name in Domoticz. command must be 'Open', 'Close', or 'Stop'."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    if command.capitalize() not in ['Open', 'Close', 'Stop']:
        return '{"status": "error", "message": "command must be \'Open\', \'Close\', or \'Stop\'"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd={command.capitalize()}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_scenes() -> str:
    """Get all scenes and groups from Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenes")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def switch_scene(command: str, idx: int = None, name: str = None) -> str:
    """Turn a scene or group On or Off by IDX or Name in Domoticz. command must be 'On', 'Off', or 'Toggle'. Scenes can only be turned 'On'."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    if command.capitalize() not in ['On', 'Off', 'Toggle']:
        return '{"status": "error", "message": "command must be \'On\', \'Off\', or \'Toggle\'"}'
    async with create_client() as client:
        resolved_idx = await _resolve_scene_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Scene not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=switchscene&idx={resolved_idx}&switchcmd={command.capitalize()}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_rooms() -> str:
    """Get all rooms (Room Plans) from Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_room_devices(idx: int = None, room_name: str = None) -> str:
    """Get all devices and their current states in a specific room. Provide either idx or room_name."""
    if idx is None and room_name is None:
        return '{"status": "error", "message": "Must provide either idx or room_name"}'
        
    async with create_client() as client:
        if idx is None:
            plans_resp = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true")
            plans_resp.raise_for_status()
            plans_data = plans_resp.json()
            for plan in plans_data.get("result", []):
                if plan.get("Name", "").lower() == room_name.lower():
                    idx = plan.get("idx")
                    break
            
            if idx is None:
                return f'{{"status": "error", "message": "Room \'{room_name}\' not found"}}'

        # Using plan=idx returns the full status of all devices in the room, rather than just their IDs
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&plan={idx}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_user_variables() -> str:
    """Get all user variables."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getuservariables")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def add_user_variable(name: str, vtype: int, value: str) -> str:
    """Add a new user variable. vtype: 0=Integer, 1=Float, 2=String, 3=Date, 4=Time"""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=adduservariable&vname={name}&vtype={vtype}&vvalue={value}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def update_user_variable(name: str, vtype: int, value: str) -> str:
    """Update an existing user variable. vtype: 0=Integer, 1=Float, 2=String, 3=Date, 4=Time"""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=updateuservariable&vname={name}&vtype={vtype}&vvalue={value}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def delete_user_variable(idx: int = None, name: str = None) -> str:
    """Delete a user variable by IDX or Name."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_user_variable_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "User variable not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=deleteuservariable&idx={resolved_idx}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_device_history(idx: int = None, name: str = None, sensor_type: str = "light", time_range: str = "day") -> str:
    """Get history log or graph for a device. sensor_type: 'light', 'text', 'temp', 'percentage', 'counter'. time_range (for graphs): 'day', 'month', 'year'."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
            
        url = f"{DOMOTICZ_API_URL}?type=command"
        if sensor_type == "light":
            url += f"&param=getlightlog&idx={resolved_idx}"
        elif sensor_type == "text":
            url += f"&param=gettextlog&idx={resolved_idx}"
        else:
            url += f"&param=graph&sensor={sensor_type}&idx={resolved_idx}&range={time_range}"
            
        response = await client.get(url)
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_system_status() -> str:
    """Get the status of the Domoticz instance (version, build time, etc)."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getversion")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_events() -> str:
    """Get overview of the internal event system scripts and rules."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_event(event_id: int) -> str:
    """Get the source code and details of a specific event script by ID."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=load&event={event_id}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def create_event(name: str, interpreter: str, event_type: str, xmlstatement: str, eventstatus: str = "1") -> str:
    """Create a new event script in Domoticz.
    
    Args:
        name: Name of the event script.
        interpreter: The language (e.g., 'Lua', 'Blockly', 'dzVents', 'Python').
        event_type: Trigger type (e.g., 'All', 'Device', 'Security', 'Time', 'UserVariable').
        xmlstatement: The source code (or XML for Blockly) of the script.
        eventstatus: '1' for enabled, '0' for disabled.
    """
    async with create_client() as client:
        data = {
            "evparam": "create",
            "name": name,
            "eventstatus": eventstatus,
            "interpreter": interpreter,
            "xml": xmlstatement,
            "eventtype": event_type,
            "logicarray": ""
        }
        response = await client.post(f"{DOMOTICZ_API_URL}?type=command&param=events", data=data)
        response.raise_for_status()
        return response.text

@mcp.tool()
async def update_event(event_id: int, name: str, interpreter: str, event_type: str, xmlstatement: str, eventstatus: str = "1") -> str:
    """Update an existing event script in Domoticz.
    
    Args:
        event_id: The ID of the event to update.
        name: Name of the event script.
        interpreter: The language (e.g., 'Lua', 'Blockly', 'dzVents', 'Python').
        event_type: Trigger type (e.g., 'All', 'Device', 'Security', 'Time', 'UserVariable').
        xmlstatement: The source code (or XML for Blockly) of the script.
        eventstatus: '1' for enabled, '0' for disabled.
    """
    async with create_client() as client:
        data = {
            "evparam": "create",
            "eventid": str(event_id),
            "name": name,
            "eventstatus": eventstatus,
            "interpreter": interpreter,
            "xml": xmlstatement,
            "eventtype": event_type,
            "logicarray": ""
        }
        response = await client.post(f"{DOMOTICZ_API_URL}?type=command&param=events", data=data)
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_hardware() -> str:
    """Get all hardware/gateways configured in Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=gethardware")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_settings() -> str:
    """Get global Domoticz settings and configuration."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getsettings")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_sun_times() -> str:
    """Get sun times (sunrise, sunset, twilight) based on home coordinates."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getsunrisenset")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_cameras() -> str:
    """Get all configured cameras in Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getcameras")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_floorplans() -> str:
    """Get all configured floorplans in Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=plans")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_users() -> str:
    """Get all Domoticz user accounts."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=users")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def set_color_brightness(hue: int, brightness: int, idx: int = None, name: str = None, iswhite: bool = False) -> str:
    """Set color and brightness for an RGB/color light by IDX or Name in Domoticz."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=setcolbrightnessvalue&idx={resolved_idx}&hue={hue}&brightness={brightness}&iswhite={str(iswhite).lower()}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def set_color_temperature(kelvin: int, idx: int = None, name: str = None) -> str:
    """Set color temperature (Kelvin) for a light by IDX or Name in Domoticz. kelvin: 0..100."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=setkelvinlevel&idx={resolved_idx}&kelvin={kelvin}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def rename_device(new_name: str, idx: int = None, old_name: str = None) -> str:
    """Rename a device by IDX or its old Name."""
    if idx is None and old_name is None:
        return '{"status": "error", "message": "Must provide either idx or old_name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, old_name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=renamedevice&name={urllib.parse.quote(new_name)}&idx={resolved_idx}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def delete_device(idx: int = None, name: str = None) -> str:
    """Delete (or hide) a device by IDX or Name."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=setused&used=false&idx={resolved_idx}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def create_virtual_sensor(hw_idx: int, sensorname: str, sensortype: int) -> str:
    """Create a virtual sensor. hw_idx is the IDX of the dummy hardware."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=createvirtualsensor&idx={hw_idx}&sensorname={urllib.parse.quote(sensorname)}&sensortype={sensortype}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def update_device_value(idx: int = None, name: str = None, nvalue: int = 0, svalue: str = "") -> str:
    """Update a sensor/device value manually. nvalue is integer value, svalue is string value. Provide IDX or Name."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=udevice&idx={resolved_idx}&nvalue={nvalue}&svalue={urllib.parse.quote(svalue)}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_log(lastlogtime: int = 0, loglevel: int = 268435455) -> str:
    """Retrieve the main system log. lastlogtime is seconds since epoch, loglevel is bitmask."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getlog&lastlogtime={lastlogtime}&loglevel={loglevel}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def add_log_message(message: str, level: int = 2) -> str:
    """Add a custom message to the Domoticz system log. level: 1=normal, 2=status, 4=error."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=addlogmessage&message={urllib.parse.quote(message)}&level={level}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def send_notification(subject: str, body: str) -> str:
    """Send a notification through Domoticz notification subsystems."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=sendnotification&subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_security_status() -> str:
    """Get the current status of the security panel."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getsecstatus")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def set_security_status(secstatus: int, seccode: str) -> str:
    """Set the security panel status. secstatus: 0=Disarm, 1=Arm Home, 2=Arm Away."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=setsecstatus&secstatus={secstatus}&seccode={urllib.parse.quote(seccode)}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_scene_devices(idx: int = None, name: str = None) -> str:
    """List all devices belonging to a specific scene/group by IDX or Name."""
    if idx is None and name is None:
        return '{"status": "error", "message": "Must provide either idx or name"}'
    async with create_client() as client:
        resolved_idx = await _resolve_scene_idx(client, idx, name)
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Scene not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenedevices&idx={resolved_idx}&isscene=true")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://log")
async def get_log_resource() -> str:
    """Read the current Domoticz system log."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getlog&lastlogtime=0&loglevel=268435455")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://security")
async def get_security_resource() -> str:
    """Read the current status of the security panel."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getsecstatus")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://devices")
async def get_all_devices_resource() -> str:
    """Read the current state of all Domoticz devices."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true&order=Name")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://device/{idx}")
async def get_device_resource(idx: int) -> str:
    """Read the current state of a specific Domoticz device."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid={idx}")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://device/name/{name}")
async def get_device_by_name_resource(name: str) -> str:
    """Read the current state of a specific Domoticz device by name."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, name=urllib.parse.unquote(name))
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Device not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid={resolved_idx}")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://device/{device_type}/{subtype}/{idx}")
async def get_device_resource_detailed(device_type: str, subtype: str, idx: int) -> str:
    """Read the current state of a specific Domoticz device using a descriptive URI."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid={idx}")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://rooms")
async def get_rooms_resource() -> str:
    """Read the list of all Domoticz rooms (Room Plans)."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://room/{idx}")
async def get_room_resource(idx: int) -> str:
    """Read the current states of all devices in a specific Domoticz room."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&plan={idx}")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://room/{room_name}/{idx}")
async def get_room_resource_detailed(room_name: str, idx: int) -> str:
    """Read the current states of all devices in a specific Domoticz room using a descriptive URI."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&plan={idx}")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://user-variables")
async def get_user_variables_resource() -> str:
    """Read the list of all Domoticz user variables."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getuservariables")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://user-variable/{idx}")
async def get_user_variable_resource(idx: int) -> str:
    """Read a specific Domoticz user variable."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getuservariable&idx={idx}")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://user-variable/name/{name}")
async def get_user_variable_by_name_resource(name: str) -> str:
    """Read a specific Domoticz user variable by name."""
    async with create_client() as client:
        resolved_idx = await _resolve_user_variable_idx(client, name=urllib.parse.unquote(name))
        if resolved_idx is None:
            return f'{{"status": "error", "message": "User variable not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getuservariable&idx={resolved_idx}")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://scenes")
async def get_scenes_resource() -> str:
    """Read the list of all Domoticz scenes and groups."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenes")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://scene/{idx}")
async def get_scene_resource(idx: int) -> str:
    """Read the devices belonging to a specific Domoticz scene/group."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenedevices&idx={idx}&isscene=true")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://scene/name/{name}")
async def get_scene_by_name_resource(name: str) -> str:
    """Read the devices belonging to a specific Domoticz scene/group by name."""
    async with create_client() as client:
        resolved_idx = await _resolve_scene_idx(client, name=urllib.parse.unquote(name))
        if resolved_idx is None:
            return f'{{"status": "error", "message": "Scene not found"}}'
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenedevices&idx={resolved_idx}&isscene=true")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://settings")
async def get_settings_resource() -> str:
    """Read global Domoticz settings and configuration."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getsettings")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://events")
async def get_events_resource() -> str:
    """Read the overview of the internal event system scripts and rules."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list")
        response.raise_for_status()
        return response.text

@mcp.resource("domoticz://event/{event_id}")
async def get_event_resource(event_id: int) -> str:
    """Read the source code and details of a specific event script by ID."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=load&event={event_id}")
        response.raise_for_status()
        return response.text

@mcp.prompt()
def troubleshoot_device(idx: int = None, name: str = None) -> str:
    """Prompt to help troubleshoot a specific Domoticz device by IDX or Name."""
    target = f"IDX {idx}" if idx is not None else f"name '{name}'"
    resource = f"domoticz://device/{idx}" if idx is not None else f"domoticz://device/name/{urllib.parse.quote(name)}"
    return f"Please help me troubleshoot my Domoticz device with {target}. Start by reading the device's state using the `{resource}` resource, and check the system log using the `domoticz://log` resource for any recent errors related to this device. Analyze the state and logs to tell me what might be wrong."

@mcp.prompt()
def summarize_home() -> str:
    """Prompt to summarize the current state of the smart home."""
    return "Please provide a human-readable summary of my smart home's current state. Use the `domoticz://devices` resource to check what devices are currently on, off, open, or closed. Highlight any active lights, open doors/windows, and the current temperatures."

@mcp.prompt()
def analyze_automations() -> str:
    """Prompt to analyze Domoticz event scripts for logic flaws."""
    return "Please analyze my Domoticz automations and event scripts for any logic flaws or potential optimizations. Start by reading the `domoticz://events` resource to get an overview of the scripts, then use the `domoticz://event/{event_id}` resource to read specific scripts you want to investigate."

import argparse

def main():
    parser = argparse.ArgumentParser(description="Domoticz MCP Server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"], help="Transport to use")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to for SSE / HTTP")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to for SSE / HTTP")
    args = parser.parse_args()
    
    if args.transport == "sse":
        import uvicorn
        from starlette.middleware.cors import CORSMiddleware
        
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        
        app = mcp.sse_app()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        uvicorn.run(app, host=args.host, port=args.port)
    elif args.transport == "streamable-http":
        import uvicorn
        from starlette.middleware.cors import CORSMiddleware
        
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        
        app = mcp.streamable_http_app()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        mcp.run()

if __name__ == "__main__":
    main()

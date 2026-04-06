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
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

mcp = FastMCP("Domoticz", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False), stateless_http=True)

# Configuration defaults
DOMOTICZ_BASE_URL = "https://xmpp.vanadrighem.eu/domoticz"
DOMOTICZ_API_URL = f"{DOMOTICZ_BASE_URL}/json.htm"
DOMOTICZ_USERNAME = None
DOMOTICZ_PASSWORD = None
DOMOTICZ_CLIENT_ID = None
DOMOTICZ_CLIENT_SECRET = None
DOMOTICZ_OAUTH_TOKEN = None
TOKEN_FILE = os.path.expanduser("~/.config/domoticz-mcp/token.json")
_oauth_token_cache: Optional[str] = None
_global_http_client: Optional[httpx.AsyncClient] = None

# Cache settings
CACHE_TTL = 300 # 5 minutes
_device_cache = {"data": None, "timestamp": 0}
_scene_cache = {"data": None, "timestamp": 0}
_user_variable_cache = {"data": None, "timestamp": 0}
_plans_cache = {"data": None, "timestamp": 0}

def _do_interactive_oauth_flow():
# ... (rest of the file remains the same until _resolve_device_idx)
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
        global _global_http_client
        if _global_http_client is None:
            _global_http_client = httpx.AsyncClient(timeout=30.0)
        self.client = _global_http_client
        
    async def __aenter__(self):
        # Determine authentication strategy
        oauth_token = None

        if DOMOTICZ_CLIENT_ID:
            oauth_token = await _fetch_oauth_token()

        if oauth_token:
            self.client.headers["Authorization"] = f"Bearer {oauth_token}"
        elif DOMOTICZ_USERNAME and DOMOTICZ_PASSWORD:
            self.client.auth = (DOMOTICZ_USERNAME, DOMOTICZ_PASSWORD)

        return self.client
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # We don't close the global client here
        pass

def create_client():
    return DomoticzClient()

async def _get_cached_data(client, cache_obj, api_url, key_path="result") -> List[Dict[str, Any]]:
    now = time.time()
    if cache_obj["data"] is None or (now - cache_obj["timestamp"]) > CACHE_TTL:
        response = await client.get(api_url)
        response.raise_for_status()
        cache_obj["data"] = response.json().get(key_path, [])
        cache_obj["timestamp"] = now
    return cache_obj["data"]

async def _resolve_device_idx(client, idx: int = None, name: str = None) -> int | None:
    if idx is not None:
        return idx
    if not name:
        return None
    devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
    for dev in devices:
        if dev.get("Name", "").lower() == name.lower():
            return int(dev.get("idx"))
    return None

async def _resolve_scene_idx(client, idx: int = None, name: str = None) -> int | None:
    if idx is not None:
        return idx
    if not name:
        return None
    scenes = await _get_cached_data(client, _scene_cache, f"{DOMOTICZ_API_URL}?type=command&param=getscenes")
    for scene in scenes:
        if scene.get("Name", "").lower() == name.lower():
            return int(scene.get("idx"))
    return None

async def _resolve_user_variable_idx(client, idx: int = None, name: str = None) -> int | None:
    if idx is not None:
        return idx
    if not name:
        return None
    vars = await _get_cached_data(client, _user_variable_cache, f"{DOMOTICZ_API_URL}?type=command&param=getuservariables")
    for var in vars:
        if var.get("Name", "").lower() == name.lower():
            return int(var.get("idx"))
    return None

def _simplify_device(dev: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce device dictionary to essential fields to save context space."""
    keys_to_keep = [
        "idx", "Name", "Type", "SubType", "Data", "Status", 
        "BatteryLevel", "Favorite", "HardwareName", "LastUpdate", 
        "TypeImg", "Usage", "CounterToday", "Temp", "Humidity"
    ]
    return {k: dev[k] for k in keys_to_keep if k in dev}

@mcp.tool()
async def get_all_devices() -> str:
    """Get all devices and their current states from Domoticz."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true&order=Name")
        return json.dumps({"status": "OK", "result": devices})

@mcp.tool()
async def search_devices(query: str) -> str:
    """Search for devices by name or data (status). Returns a list of matching devices."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        query_lower = query.lower()
        results = []
        for dev in devices:
            if query_lower in dev.get("Name", "").lower() or query_lower in dev.get("Data", "").lower():
                results.append(dev)
        return json.dumps({"status": "OK", "result": results})

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
        scenes = await _get_cached_data(client, _scene_cache, f"{DOMOTICZ_API_URL}?type=command&param=getscenes")
        return json.dumps({"status": "OK", "result": scenes})

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
        plans = await _get_cached_data(client, _plans_cache, f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true")
        return json.dumps({"status": "OK", "result": plans})

@mcp.tool()
async def get_room_devices(idx: int = None, room_name: str = None) -> str:
    """Get all devices and their current states in a specific room. Provide either idx or room_name."""
    if idx is None and room_name is None:
        return '{"status": "error", "message": "Must provide either idx or room_name"}'
        
    async with create_client() as client:
        if idx is None:
            plans = await _get_cached_data(client, _plans_cache, f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true")
            for plan in plans:
                if plan.get("Name", "").lower() == room_name.lower():
                    idx = plan.get("idx")
                    break
            
            if idx is None:
                return f'{{"status": "error", "message": "Room \'{room_name}\' not found"}}'

        # Using plan=idx returns the full status of all devices in the room, rather than just their IDs
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&plan={idx}")
        response.raise_for_status()
        data = response.json()
        if "result" in data:
            data["result"] = [_simplify_device(d) for d in data["result"]]
        return json.dumps(data)

@mcp.tool()
async def get_user_variables() -> str:
    """Get all user variables."""
    async with create_client() as client:
        vars = await _get_cached_data(client, _user_variable_cache, f"{DOMOTICZ_API_URL}?type=command&param=getuservariables")
        return json.dumps({"status": "OK", "result": vars})

@mcp.tool()
async def add_user_variable(name: str, vtype: int, value: str) -> str:
    """Add a new user variable. 
    
    vtype (Variable Type):
    0: Integer
    1: Float
    2: String
    3: Date (DD/MM/YYYY)
    4: Time (HH:MM)
    """
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=adduservariable&vname={name}&vtype={vtype}&vvalue={value}")
        response.raise_for_status()
        _user_variable_cache["timestamp"] = 0 # Invalidate cache
        return response.text

@mcp.tool()
async def update_user_variable(name: str, vtype: int, value: str) -> str:
    """Update an existing user variable. 
    
    vtype (Variable Type):
    0: Integer
    1: Float
    2: String
    3: Date (DD/MM/YYYY)
    4: Time (HH:MM)
    """
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=updateuservariable&vname={name}&vtype={vtype}&vvalue={value}")
        response.raise_for_status()
        _user_variable_cache["timestamp"] = 0 # Invalidate cache
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
        _user_variable_cache["timestamp"] = 0 # Invalidate cache
        return response.text

@mcp.tool()
async def get_battery_levels(threshold: int = 20) -> str:
    """Get a list of devices with battery levels at or below the specified threshold.
    
    Args:
        threshold: Battery percentage threshold (default: 20).
    """
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        results = []
        for dev in devices:
            battery = dev.get("BatteryLevel", 255)
            # BatteryLevel 255 often means no battery or not reporting
            if battery != 255 and battery <= threshold:
                results.append({
                    "idx": dev.get("idx"),
                    "Name": dev.get("Name"),
                    "BatteryLevel": battery,
                    "HardwareName": dev.get("HardwareName"),
                    "LastUpdate": dev.get("LastUpdate")
                })
        # Sort by battery level ascending
        results.sort(key=lambda x: x["BatteryLevel"])
        return json.dumps({"status": "OK", "result": results})

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
        events = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list")
        return json.dumps({"status": "OK", "result": events})

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
        _device_cache["timestamp"] = 0 # Invalidate cache
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
        _device_cache["timestamp"] = 0 # Invalidate cache
        return response.text

@mcp.tool()
async def create_virtual_sensor(hw_idx: int, sensorname: str, sensortype: int) -> str:
    """Create a virtual sensor. 
    
    hw_idx: IDX of the dummy hardware.
    
    sensortype (Sensor Type):
    1: Temperature
    2: Humidity
    3: Temp + Humidity
    4: Barometer
    5: Temp + Hum + Baro
    6: Rain
    7: UV
    8: Wind
    10: Lux
    11: Voltage
    12: Current
    13: Distance
    14: Text
    15: Alert
    17: Percentage
    19: Counter
    113: kWh (Energy)
    """
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=createvirtualsensor&idx={hw_idx}&sensorname={urllib.parse.quote(sensorname)}&sensortype={sensortype}")
        response.raise_for_status()
        _device_cache["timestamp"] = 0 # Invalidate cache
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

@mcp.tool()
async def get_connectivity_report(hours: int = 24) -> str:
    """Get a list of devices that haven't checked in/updated within the specified timeframe.
    
    Args:
        hours: Number of hours threshold for considering a device 'unresponsive' (default: 24).
    """
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        now = datetime.now()
        threshold_time = now - timedelta(hours=hours)
        results = []
        
        for dev in devices:
            last_update_str = dev.get("LastUpdate")
            if not last_update_str:
                continue
                
            try:
                # Domoticz format: YYYY-MM-DD HH:MM:SS
                last_update = datetime.strptime(last_update_str, "%Y-%m-%d %H:%M:%S")
                if last_update < threshold_time:
                    results.append({
                        "idx": dev.get("idx"),
                        "Name": dev.get("Name"),
                        "LastUpdate": last_update_str,
                        "HardwareName": dev.get("HardwareName"),
                        "Type": dev.get("Type"),
                        "Data": dev.get("Data")
                    })
            except ValueError:
                continue
                
        # Sort by oldest update first
        results.sort(key=lambda x: x["LastUpdate"])
        return json.dumps({"status": "OK", "result": results})

@mcp.tool()
async def analyze_energy_usage() -> str:
    """Analyze all energy-reporting devices and summarize their 'Today' usage."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        results = []
        
        # Look for devices with 'Usage' or 'Counter' related fields
        for dev in devices:
            usage = dev.get("Usage")
            counter_today = dev.get("CounterToday")
            
            if usage is not None or counter_today is not None:
                results.append({
                    "idx": dev.get("idx"),
                    "Name": dev.get("Name"),
                    "CurrentUsage": usage,
                    "TodayTotal": counter_today,
                    "Type": dev.get("Type"),
                    "SubType": dev.get("SubType")
                })
        
        return json.dumps({"status": "OK", "result": results})

@mcp.resource("domoticz://dashboard")
async def get_dashboard_resource() -> str:
    """Read a curated view of favorite and currently active devices."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        curated = []
        
        for dev in devices:
            is_favorite = dev.get("Favorite") == 1
            # Simple heuristic for 'active': lights not 'Off', sensors not 'Normal'/'Closed'
            status = dev.get("Status", "").lower()
            data = dev.get("Data", "").lower()
            is_active = status not in ["off", "closed", "normal", ""] or "on" in data or "open" in data
            
            if is_favorite or is_active:
                curated.append(_simplify_device(dev))
        
        return json.dumps({"status": "OK", "result": curated})

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
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true&order=Name")
        simplified = [_simplify_device(d) for d in devices]
        return json.dumps({"status": "OK", "result": simplified})

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
        plans = await _get_cached_data(client, _plans_cache, f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true")
        return json.dumps({"status": "OK", "result": plans})

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
        vars = await _get_cached_data(client, _user_variable_cache, f"{DOMOTICZ_API_URL}?type=command&param=getuservariables")
        return json.dumps({"status": "OK", "result": vars})

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
        scenes = await _get_cached_data(client, _scene_cache, f"{DOMOTICZ_API_URL}?type=command&param=getscenes")
        return json.dumps({"status": "OK", "result": scenes})

@mcp.resource("domoticz://scene/{idx}")
async def get_scene_resource(idx: int) -> str:
    """Read the devices belonging to a specific Domoticz scene/group."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenedevices&idx={idx}&isscene=true")
        response.raise_for_status()
        data = response.json()
        if "result" in data:
            data["result"] = [_simplify_device(d) for d in data["result"]]
        return json.dumps(data)

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
def audit_batteries(threshold: int = 20) -> str:
    """Prompt to audit battery levels across all sensors."""
    return f"Please check the battery levels of all my smart home devices. Use the `get_battery_levels` tool with a threshold of {threshold}% to identify any sensors that need attention soon. Provide a clear list of these devices, including their current battery percentage and which room they are in (if known)."

@mcp.prompt()
def find_devices_by_state(state: str) -> str:
    """Prompt to find all devices currently in a specific state (e.g., 'on', 'open', 'off')."""
    return f"I want to find all devices that are currently in the '{state}' state. Use the `search_devices` tool with the query '{state}' to find them. Analyze the results and provide a summary grouped by device type or room."

@mcp.prompt()
def maintenance_report() -> str:
    """Prompt to provide a comprehensive maintenance and health report for the home."""
    return "Please provide a comprehensive health report for my smart home. 1) Use `get_battery_levels` to find sensors with low power. 2) Use `get_connectivity_report` to identify devices that haven't updated in 24 hours. 3) Check the `domoticz://log` resource for any recent 'Error' or 'Hardware' entries that might indicate connectivity issues. 4) Summarize any devices that need physical attention or have stopped responding."

@mcp.prompt()
def troubleshoot_device(idx: int = None, name: str = None) -> str:
    """Prompt to help troubleshoot a specific Domoticz device by IDX or Name."""
    target = f"IDX {idx}" if idx is not None else f"name '{name}'"
    resource = f"domoticz://device/{idx}" if idx is not None else f"domoticz://device/name/{urllib.parse.quote(name)}"
    return f"Please help me troubleshoot my Domoticz device with {target}. Start by reading the device's state using the `{resource}` resource, and check the system log using the `domoticz://log` resource for any recent errors related to this device. Analyze the state and logs to tell me what might be wrong."

@mcp.prompt()
def energy_audit() -> str:
    """Prompt to analyze energy usage across the home."""
    return "Please perform an energy audit of my home. Use the `analyze_energy_usage` tool to see which devices are reporting power consumption. Summarize the 'Today' usage for these devices and highlight the biggest consumers."

@mcp.prompt()
def summarize_home() -> str:
    """Prompt to summarize the current state of the smart home."""
    return "Please provide a human-readable summary of my smart home's current state. Use the `domoticz://dashboard` resource to see your favorite and currently active devices (lights on, doors open, current temperatures). Provide a clear, concise overview of what's happening."

@mcp.prompt()
def analyze_automations() -> str:
    """Prompt to analyze Domoticz event scripts for logic flaws."""
    return "Please analyze my Domoticz automations and event scripts for any logic flaws or potential optimizations. Start by reading the `domoticz://events` resource to get an overview of the scripts, then use the `domoticz://event/{event_id}` resource to read specific scripts you want to investigate."

import argparse

def main():
    global DOMOTICZ_BASE_URL, DOMOTICZ_API_URL, DOMOTICZ_USERNAME, DOMOTICZ_PASSWORD
    global DOMOTICZ_CLIENT_ID, DOMOTICZ_CLIENT_SECRET, DOMOTICZ_OAUTH_TOKEN, TOKEN_FILE, _oauth_token_cache

    parser = argparse.ArgumentParser(description="Domoticz MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], help="Transport to use")
    parser.add_argument("--host", help="Host to bind to for SSE / HTTP")
    parser.add_argument("--port", type=int, help="Port to bind to for SSE / HTTP")
    
    parser.add_argument("--domoticz-url", help="Domoticz base URL")
    parser.add_argument("--domoticz-username", help="Domoticz username")
    parser.add_argument("--domoticz-password", help="Domoticz password")
    parser.add_argument("--domoticz-client-id", help="Domoticz OAuth client ID")
    parser.add_argument("--domoticz-client-secret", help="Domoticz OAuth client secret")
    parser.add_argument("--domoticz-oauth-token", help="Domoticz OAuth token")
    parser.add_argument("--token-file", help="Path to OAuth token storage file")

    args = parser.parse_args()
    
    # Configuration structure: key -> {value, source, env_vars}
    # env_vars is a list of environment variables to check, in order.
    # The first one that exists will be used and will override command line.
    config_spec = {
        "transport": {"default": "stdio", "env_vars": ["DOMOTICZ_MCP_TRANSPORT", "TRANSPORT"]},
        "host": {"default": "127.0.0.1", "env_vars": ["DOMOTICZ_MCP_HOST", "HOST"]},
        "port": {"default": 8000, "type": int, "env_vars": ["DOMOTICZ_MCP_PORT", "PORT"]},
        "domoticz_url": {"default": "https://xmpp.vanadrighem.eu/domoticz", "env_vars": ["DOMOTICZ_URL"]},
        "domoticz_username": {"default": None, "env_vars": ["DOMOTICZ_USERNAME"]},
        "domoticz_password": {"default": None, "env_vars": ["DOMOTICZ_PASSWORD"]},
        "domoticz_client_id": {"default": None, "env_vars": ["DOMOTICZ_CLIENT_ID", "DOMOTICZ_CLIENTID"]},
        "domoticz_client_secret": {"default": None, "env_vars": ["DOMOTICZ_CLIENT_SECRET", "DOMOTICZ_CLIENTSECRET"]},
        "domoticz_oauth_token": {"default": None, "env_vars": ["DOMOTICZ_OAUTH_TOKEN"]},
        "token_file": {"default": os.path.expanduser("~/.config/domoticz-mcp/token.json"), "env_vars": ["DOMOTICZ_MCP_TOKEN_FILE", "TOKEN_FILE"]}
    }
    
    final_config = {}
    
    for key, spec in config_spec.items():
        val = spec["default"]
        source = "default"
        
        # 1. Command Line
        arg_val = getattr(args, key.replace("_", "-"), getattr(args, key, None))
        if arg_val is not None:
            val = arg_val
            source = "command line"
            
        # 2. Environment Variables (override CLI)
        for env_var in spec.get("env_vars", []):
            env_val = os.environ.get(env_var)
            if env_val:
                env_val = env_val.strip('\'"')
                if spec.get("type") == int:
                    try:
                        val = int(env_val)
                    except ValueError:
                        continue
                else:
                    val = env_val
                source = f"env variable ({env_var})"
                break
        
        final_config[key] = {"value": val, "source": source}

    # Normalize Domoticz URL
    url = final_config["domoticz_url"]["value"]
    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url
    if url.endswith('/json.htm'):
        url = url[:-9]
    final_config["domoticz_url"]["value"] = url
    
    # Set globals
    DOMOTICZ_BASE_URL = final_config["domoticz_url"]["value"]
    DOMOTICZ_API_URL = f"{DOMOTICZ_BASE_URL}/json.htm"
    DOMOTICZ_USERNAME = final_config["domoticz_username"]["value"]
    DOMOTICZ_PASSWORD = final_config["domoticz_password"]["value"]
    DOMOTICZ_CLIENT_ID = final_config["domoticz_client_id"]["value"]
    DOMOTICZ_CLIENT_SECRET = final_config["domoticz_client_secret"]["value"]
    DOMOTICZ_OAUTH_TOKEN = final_config["domoticz_oauth_token"]["value"]
    TOKEN_FILE = final_config["token_file"]["value"]
    _oauth_token_cache = DOMOTICZ_OAUTH_TOKEN

    transport = final_config["transport"]["value"]
    host = final_config["host"]["value"]
    port = final_config["port"]["value"]

    sys.stderr.write("Settings:\n")
    for key, data in final_config.items():
        val_display = data['value']
        # Mask sensitive data
        is_sensitive = "password" in key or "secret" in key or "token" in key
        is_path = "file" in key or "path" in key
        if is_sensitive and not is_path:
            if data['value']:
                val_display = "********"
        sys.stderr.write(f"  {key}: {val_display} (from {data['source']})\n")
    sys.stderr.flush()
    
    if transport == "sse":
        import uvicorn
        from starlette.middleware.cors import CORSMiddleware
        
        mcp.settings.host = host
        mcp.settings.port = port
        
        app = mcp.sse_app()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        uvicorn.run(app, host=host, port=port)
    elif transport == "streamable-http":
        import uvicorn
        from starlette.middleware.cors import CORSMiddleware
        
        mcp.settings.host = host
        mcp.settings.port = port
        
        app = mcp.streamable_http_app()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        uvicorn.run(app, host=host, port=port)
    else:
        mcp.run()

if __name__ == "__main__":
    main()

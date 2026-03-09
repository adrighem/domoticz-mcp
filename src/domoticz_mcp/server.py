# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp",
#     "httpx",
# ]
# ///

from mcp.server.fastmcp import FastMCP
import httpx
import os
from typing import Optional

mcp = FastMCP("Domoticz")

DOMOTICZ_BASE_URL = os.environ.get("DOMOTICZ_URL", "https://xmpp.vanadrighem.eu/domoticz")
if DOMOTICZ_BASE_URL.endswith('/json.htm'):
    DOMOTICZ_BASE_URL = DOMOTICZ_BASE_URL[:-9]
    
DOMOTICZ_API_URL = f"{DOMOTICZ_BASE_URL}/json.htm"
DOMOTICZ_USERNAME = os.environ.get("DOMOTICZ_USERNAME")
DOMOTICZ_PASSWORD = os.environ.get("DOMOTICZ_PASSWORD")

def create_client() -> httpx.AsyncClient:
    oauth_token = os.environ.get("DOMOTICZ_OAUTH_TOKEN")
    if oauth_token:
        return httpx.AsyncClient(headers={"Authorization": f"Bearer {oauth_token}"})
    
    if DOMOTICZ_USERNAME and DOMOTICZ_PASSWORD:
        return httpx.AsyncClient(auth=(DOMOTICZ_USERNAME, DOMOTICZ_PASSWORD))
        
    return httpx.AsyncClient()

@mcp.tool()
async def get_all_devices() -> str:
    """Get all devices and their current states from Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true&order=Name")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_device(idx: int) -> str:
    """Get a specific device state by IDX from Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid={idx}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def toggle_switch(idx: int) -> str:
    """Toggle a switch or light by IDX in Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={idx}&switchcmd=Toggle")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def set_switch_state(idx: int, state: str) -> str:
    """Set a switch or light to On or Off by IDX in Domoticz. state must be 'On' or 'Off'."""
    if state.lower() not in ['on', 'off']:
        return "Error: state must be 'On' or 'Off'"
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={idx}&switchcmd={state.capitalize()}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def set_dimmer_level(idx: int, level: int) -> str:
    """Set the brightness level of a dimmer switch (0-100) by IDX in Domoticz."""
    if not (0 <= level <= 100):
        return "Error: level must be between 0 and 100"
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={idx}&switchcmd=Set%20Level&level={level}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def set_temperature_setpoint(idx: int, setpoint: float) -> str:
    """Set the temperature setpoint for a thermostat by IDX in Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=setsetpoint&idx={idx}&setpoint={setpoint}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def control_blinds(idx: int, command: str) -> str:
    """Control blinds by IDX in Domoticz. command must be 'Open', 'Close', or 'Stop'."""
    if command.capitalize() not in ['Open', 'Close', 'Stop']:
        return "Error: command must be 'Open', 'Close', or 'Stop'"
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={idx}&switchcmd={command.capitalize()}")
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
async def switch_scene(idx: int, command: str) -> str:
    """Turn a scene or group On or Off by IDX in Domoticz. command must be 'On', 'Off', or 'Toggle'. Scenes can only be turned 'On'."""
    if command.capitalize() not in ['On', 'Off', 'Toggle']:
        return "Error: command must be 'On', 'Off', or 'Toggle'"
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=switchscene&idx={idx}&switchcmd={command.capitalize()}")
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
async def get_room_devices(idx: int) -> str:
    """Get all devices in a specific room by room IDX from Domoticz."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=getplandevices&idx={idx}")
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
async def delete_user_variable(idx: int) -> str:
    """Delete a user variable by IDX."""
    async with create_client() as client:
        response = await client.get(f"{DOMOTICZ_API_URL}?type=command&param=deleteuservariable&idx={idx}")
        response.raise_for_status()
        return response.text

@mcp.tool()
async def get_device_history(idx: int, sensor_type: str = "light", time_range: str = "day") -> str:
    """Get history log or graph for a device. sensor_type: 'light', 'text', 'temp', 'percentage', 'counter'. time_range (for graphs): 'day', 'month', 'year'."""
    url = f"{DOMOTICZ_API_URL}?type=command"
    if sensor_type == "light":
        url += f"&param=getlightlog&idx={idx}"
    elif sensor_type == "text":
        url += f"&param=gettextlog&idx={idx}"
    else:
        url += f"&param=graph&sensor={sensor_type}&idx={idx}&range={time_range}"
        
    async with create_client() as client:
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

def main():
    mcp.run()

if __name__ == "__main__":
    main()

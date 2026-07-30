import pytest
import respx
import httpx
from httpx import Response
import base64
import hashlib
import json
import asyncio
import queue
import socket
import stat
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from mcp.server.fastmcp.exceptions import ToolError
import domoticz_mcp.server as server_module
import domoticz_mcp.server as server
from domoticz_mcp.server import (
    get_device, toggle_switch, get_room_devices, get_all_devices,
    _resolve_device_idx, _resolve_scene_idx, _resolve_user_variable_idx,
    create_client, DOMOTICZ_API_URL,
    _device_cache, _scene_cache, _user_variable_cache, _plans_cache,
    DomoticzError,
    DeviceNotFoundError, SceneNotFoundError, UserVariableNotFoundError,
    AuthenticationError, InvalidParameterError,
    search_devices, search_scripts, agent_guidance, maintenance_report, summarize_home, energy_audit,
    get_dashboard_resource, get_log_resource, get_security_resource, get_device_resource, 
    get_rooms_resource, get_events_resource
)

def setup_function():
    # Reset caches before each test
    _device_cache["data"] = None
    _device_cache["timestamp"] = 0
    _device_cache["generation"] = 0
    _scene_cache["data"] = None
    _scene_cache["timestamp"] = 0
    _scene_cache["generation"] = 0
    _user_variable_cache["data"] = None
    _user_variable_cache["timestamp"] = 0
    _user_variable_cache["generation"] = 0
    _plans_cache["data"] = None
    _plans_cache["timestamp"] = 0
    _plans_cache["generation"] = 0

# Mock response for getdevices to test name resolution
DEVICES_MOCK_RESPONSE = {
    "result": [
        {"idx": "1", "Name": "Living Room Light"},
        {"idx": "2", "Name": "Kitchen Light"},
        {"idx": "45", "Name": "Thermostat"}
    ]
}

# Mock response for getplans to test room name resolution
PLANS_MOCK_RESPONSE = {
    "result": [
        {"idx": "10", "Name": "Living Room"},
        {"idx": "11", "Name": "Kitchen"}
    ]
}

@pytest.mark.asyncio
@respx.mock
async def test_resolve_device_idx():
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=DEVICES_MOCK_RESPONSE)
    )
    
    async with create_client() as client:
        # Test exact match
        idx = await _resolve_device_idx(client, name="Kitchen Light")
        assert idx == 2
        
        # Test case insensitivity
        idx = await _resolve_device_idx(client, name="living ROOM light")
        assert idx == 1
        
        # Test not found
        idx = await _resolve_device_idx(client, name="Nonexistent")
        assert idx is None
        
        # Test idx passthrough
        idx = await _resolve_device_idx(client, idx=99)
        assert idx == 99

@pytest.mark.asyncio
@respx.mock
async def test_get_device_by_name():
    # Mock the name resolution call
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=DEVICES_MOCK_RESPONSE)
    )
    # Mock the actual get device call for idx 2
    device_data = {"result": [{"idx": "2", "Name": "Kitchen Light", "Data": "On"}]}
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid=2").mock(
        return_value=Response(200, json=device_data)
    )
    
    response = await get_device(name="Kitchen Light")
    data = json.loads(response)
    assert data["status"] == "OK"
    assert data["result"] == device_data["result"]

@pytest.mark.asyncio
@respx.mock
async def test_toggle_switch_by_idx():
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx=5&switchcmd=Toggle").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    
    response = await toggle_switch(idx=5)
    assert response.status == "OK"

@pytest.mark.asyncio
@respx.mock
async def test_get_room_devices_by_name():
    # Mock the getplans call
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true").mock(
        return_value=Response(200, json=PLANS_MOCK_RESPONSE)
    )
    # Mock the getdevices for plan 10 (Living Room)
    room_devices = {"result": [{"idx": "1", "Name": "Living Room Light"}]}
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&plan=10").mock(
        return_value=Response(200, json=room_devices)
    )
    
    response = await get_room_devices(room_name="Living Room")
    data = json.loads(response)
    assert data["status"] == "OK"
    assert data["result"] == room_devices["result"]

@pytest.mark.asyncio
@respx.mock
async def test_get_all_devices():
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true&order=Name").mock(
        return_value=Response(200, json=DEVICES_MOCK_RESPONSE)
    )
    
    response = await get_all_devices()
    data = json.loads(response)
    assert data["status"] == "OK"
    assert data["result"] == DEVICES_MOCK_RESPONSE["result"]
    assert data["total_count"] == 3

@pytest.mark.asyncio
@respx.mock
async def test_search_devices():
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=DEVICES_MOCK_RESPONSE)
    )
    
    # Search by name
    response = await search_devices(query="Kitchen")
    data = json.loads(response)
    assert len(data["result"]) == 1
    assert data["result"][0]["Name"] == "Kitchen Light"
    assert data["total_count"] == 1
    
    # Search by name (case insensitive)
    response = await search_devices(query="light")
    data = json.loads(response)
    assert len(data["result"]) == 2
    assert data["total_count"] == 2

@pytest.mark.asyncio
@respx.mock
async def test_get_battery_levels():
    mock_battery_data = {
        "result": [
            {"idx": "1", "Name": "Full Sensor", "BatteryLevel": 100},
            {"idx": "2", "Name": "Low Sensor", "BatteryLevel": 15},
            {"idx": "3", "Name": "Critical Sensor", "BatteryLevel": 5},
            {"idx": "4", "Name": "Mains Powered", "BatteryLevel": 255}
        ]
    }
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=mock_battery_data)
    )
    
    from domoticz_mcp.server import get_battery_levels
    
    response = await get_battery_levels(threshold=20)
    data = json.loads(response)
    assert len(data["result"]) == 2
    assert data["result"][0]["Name"] == "Critical Sensor" # Sorted by battery level
    assert data["result"][1]["Name"] == "Low Sensor"

@pytest.mark.asyncio
@respx.mock
async def test_resolve_scene_idx():
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenes").mock(
        return_value=Response(200, json={"result": [{"idx": "101", "Name": "Movie Mode"}]})
    )
    async with create_client() as client:
        idx = await _resolve_scene_idx(client, name="Movie Mode")
        assert idx == 101

@pytest.mark.asyncio
@respx.mock
async def test_resolve_user_variable_idx():
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getuservariables").mock(
        return_value=Response(200, json={"result": [{"idx": "50", "Name": "MyVar"}]})
    )
    async with create_client() as client:
        idx = await _resolve_user_variable_idx(client, name="MyVar")
        assert idx == 50

@pytest.mark.asyncio
@respx.mock
async def test_user_variable_tools():
    from domoticz_mcp.server import get_user_variables, add_user_variable, update_user_variable, delete_user_variable
    
    # get_user_variables
    mock_vars = {"result": [{"idx": "1", "Name": "Test", "Value": "123"}]}
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getuservariables").mock(
        return_value=Response(200, json=mock_vars)
    )
    response = await get_user_variables()
    assert json.loads(response)["result"] == mock_vars["result"]
    
    # add_user_variable
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=adduservariable&vname=NewVar&vtype=0&vvalue=10").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    await add_user_variable("NewVar", 0, "10")
    
    # update_user_variable
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=updateuservariable&vname=NewVar&vtype=0&vvalue=20").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    await update_user_variable("NewVar", 0, "20")
    
    # delete_user_variable
    # Mock resolution first
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getuservariables").mock(
        return_value=Response(200, json={"result": [{"idx": "5", "Name": "DeleteMe"}]})
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=deleteuservariable&idx=5").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    with pytest.raises(ToolError, match="Confirmation required"):
        await delete_user_variable(name="DeleteMe")
    await delete_user_variable(name="DeleteMe", confirm=True)

@pytest.mark.asyncio
@respx.mock
async def test_user_variable_tool_parameters_are_encoded():
    from domoticz_mcp.server import add_user_variable, update_user_variable

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=adduservariable&vname=Name%20With%20Space&vtype=2&vvalue=a%26b%3D1").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    await add_user_variable("Name With Space", 2, "a&b=1")

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=updateuservariable&vname=Name%20With%20Space&vtype=2&vvalue=now%3Dyes%26later%3Dno").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    await update_user_variable("Name With Space", 2, "now=yes&later=no")

@pytest.mark.asyncio
@respx.mock
async def test_get_connectivity_report():
    from domoticz_mcp.server import get_connectivity_report
    from datetime import datetime, timedelta
    
    old_time = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    new_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    mock_data = {
        "result": [
            {"idx": "1", "Name": "Dead Sensor", "LastUpdate": old_time},
            {"idx": "2", "Name": "Live Sensor", "LastUpdate": new_time}
        ]
    }
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=mock_data)
    )
    
    response = await get_connectivity_report(hours=24)
    data = json.loads(response)
    assert len(data["result"]) == 1
    assert data["result"][0]["Name"] == "Dead Sensor"

@pytest.mark.asyncio
@respx.mock
async def test_analyze_energy_usage():
    from domoticz_mcp.server import analyze_energy_usage
    mock_data = {
        "result": [
            {"idx": "1", "Name": "Heater", "Type": "Usage", "Usage": "2000W", "CounterToday": "5kWh"},
            {"idx": "2", "Name": "Light", "Data": "On"}, # No energy data
            {"idx": "3", "Name": "Solar Inverter", "Usage": "1.5 kW", "CounterToday": "3.2 kWh"},
            {"idx": "4", "Name": "Smart Meter", "Type": "P1 Smart Meter", "Usage": "500 Watt", "UsageDeliv": "100W", "CounterToday": "2.5 kWh", "CounterDelivToday": "0.4 kWh"},
            {"idx": "5", "Name": "Gas Meter", "Type": "Gas", "CounterToday": "0.183 m3"},
            {"idx": "6", "Name": "Water Meter", "Type": "Water", "CounterToday": "120 L"}
        ]
    }
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=mock_data)
    )
    
    response = await analyze_energy_usage()
    data = json.loads(response)
    assert len(data["result"]) == 5
    assert data["summary"]["devices_with_energy_data"] == 5
    assert data["summary"]["current"]["electricity_consumption_w"] == 2500
    assert data["summary"]["current"]["electricity_generation_w"] == 1500
    assert data["summary"]["current"]["electricity_export_w"] == 100
    assert data["summary"]["current"]["net_electricity_w"] == 900
    assert data["summary"]["today"]["electricity_consumption_kwh"] == 7.5
    assert data["summary"]["today"]["electricity_generation_kwh"] == 3.2
    assert data["summary"]["today"]["electricity_export_kwh"] == 0.4
    assert data["summary"]["today"]["gas_m3"] == 0.183
    assert data["summary"]["today"]["water_m3"] == 0.12
    assert data["summary"]["top_current_consumers"][0]["Name"] == "Heater"
    assert data["result"][0]["Name"] == "Heater"
    assert data["result"][0]["current_power_w"] == 2000
    assert data["result"][0]["today_kwh"] == 5
    assert data["result"][0]["TodayTotal"] == "5kWh"
    categories = {entry["Name"]: entry["category"] for entry in data["result"]}
    assert categories["Solar Inverter"] == "electricity_generation"
    assert categories["Gas Meter"] == "gas"
    assert categories["Water Meter"] == "water"

@pytest.mark.asyncio
@respx.mock
async def test_energy_history_tools(monkeypatch):
    from domoticz_mcp.server import get_daily_energy_history, get_monthly_energy_history, get_weekly_energy_history

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 12, 10, 0, 0)

    monkeypatch.setattr(server, "datetime", FixedDateTime)

    devices = {
        "result": [
            {"idx": "1", "Name": "Smart Meter", "Type": "P1 Smart Meter"},
            {"idx": "2", "Name": "Gas Meter", "Type": "Gas"},
        ]
    }
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=devices)
    )

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=graph&sensor=counter&idx=1&range=day&method=1").mock(
        return_value=Response(200, json={
            "status": "OK",
            "title": "Daily power",
            "result": [
                {"d": "2026-06-12 10:00", "u": "100 W"},
                {"d": "2026-06-12 10:05", "u": "0.2 kW"},
            ],
        })
    )
    daily = (await get_daily_energy_history(idx=1)).model_dump()
    assert daily["include_instantaneous"] is True
    assert daily["summary"]["power"]["max_w"] == 200
    assert daily["summary"]["power"]["avg_w"] == 150

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=graph&sensor=counter&idx=1&range=2026-06-06T2026-06-12").mock(
        return_value=Response(200, json={
            "status": "OK",
            "title": "Weekly energy",
            "result": [
                {"d": "2026-06-11", "v": "2.5 kWh"},
                {"d": "2026-06-12", "v": "3.0 kWh"},
            ],
        })
    )
    weekly = (await get_weekly_energy_history(name="Smart Meter")).model_dump()
    assert weekly["range"] == "2026-06-06T2026-06-12"
    assert weekly["summary"]["energy"]["total_kwh"] == 5.5

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=graph&sensor=counter&idx=2&range=month").mock(
        return_value=Response(200, json={
            "status": "OK",
            "title": "Monthly gas",
            "result": [
                {"d": "2026-06-01", "v": "0.4 m3"},
                {"d": "2026-06-02", "v": "0.6 m3"},
            ],
        })
    )
    monthly = (await get_monthly_energy_history(idx=2)).model_dump()
    assert monthly["summary"]["category"] == "gas"
    assert monthly["summary"]["volume"]["total_m3"] == 1.0

@pytest.mark.asyncio
@respx.mock
async def test_dashboard_resource():
    mock_data = {
        "result": [
            {"idx": "1", "Name": "Favorite Light", "Favorite": 1, "Status": "Off", "Data": "Off"},
            {"idx": "2", "Name": "Active Sensor", "Favorite": 0, "Status": "On", "Data": "On"},
            {"idx": "3", "Name": "Idle Sensor", "Favorite": 0, "Status": "Off", "Data": "Off"}
        ]
    }
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=mock_data)
    )
    
    response = await get_dashboard_resource()
    data = json.loads(response)
    # Filtered and simplified
    assert len(data) == 2
    names = [d["Name"] for d in data]
    assert "Favorite Light" in names
    assert "Active Sensor" in names
    assert "Idle Sensor" not in names

@pytest.mark.asyncio
@respx.mock
async def test_device_control_tools():
    from domoticz_mcp.server import (
        set_switch_state, set_dimmer_level, set_temperature_setpoint, control_blinds
    )
    
    # Mock resolution for Kitchen Light (idx 2)
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=DEVICES_MOCK_RESPONSE)
    )
    
    # set_switch_state
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx=2&switchcmd=On").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    await set_switch_state("On", name="Kitchen Light")
    
    # set_dimmer_level
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx=2&switchcmd=Set%20Level&level=50").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    await set_dimmer_level(50, name="Kitchen Light")
    
    # set_temperature_setpoint
    # Thermostat is idx 45 in DEVICES_MOCK_RESPONSE
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=setsetpoint&idx=45&setpoint=21.5").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    await set_temperature_setpoint(21.5, name="Thermostat")
    
    # control_blinds
    # Let's say Kitchen Light is actually a blind for this test
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx=2&switchcmd=Open").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    await control_blinds("Open", name="Kitchen Light")

@pytest.mark.asyncio
@respx.mock
async def test_scene_and_room_tools():
    from domoticz_mcp.server import get_scenes, switch_scene, get_rooms, get_hardware
    
    # get_scenes
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenes").mock(
        return_value=Response(200, json={"result": [{"idx": "1", "Name": "Scene1"}]})
    )
    await get_scenes()
    
    # switch_scene
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=switchscene&idx=1&switchcmd=On").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    await switch_scene("On", idx=1)
    with pytest.raises(ToolError, match="Invalid command"):
        await switch_scene("Invalid", idx=1)
    
    # get_rooms
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true").mock(
        return_value=Response(200, json=PLANS_MOCK_RESPONSE)
    )
    await get_rooms()
    
    # get_hardware
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=gethardware").mock(
        return_value=Response(200, json={"result": [{"idx": "1", "Name": "Dummy"}]})
    )
    await get_hardware()

def test_prompts():
    assert "health" in maintenance_report().lower()
    assert "dashboard" in summarize_home()
    assert "energy" in energy_audit().lower()

@pytest.mark.asyncio
@respx.mock
async def test_resource_handlers():
    # get_log_resource
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getlog&lastlogtime=0&loglevel=268435455").mock(
        return_value=Response(200, json={"result": []})
    )
    await get_log_resource()
    
    # get_security_resource
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getsecstatus").mock(
        return_value=Response(200, json={"secstatus": 0})
    )
    await get_security_resource()
    
    # get_device_resource
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid=1").mock(
        return_value=Response(200, json={"result": []})
    )
    await get_device_resource(1)
    
    # get_rooms_resource
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true").mock(
        return_value=Response(200, json=PLANS_MOCK_RESPONSE)
    )
    await get_rooms_resource()
    
    # get_events_resource
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list").mock(
        return_value=Response(200, json={"result": []})
    )
    await get_events_resource()

@pytest.mark.asyncio
@respx.mock
async def test_documented_resource_aliases():
    from domoticz_mcp.server import (
        get_device_by_name_resource,
        get_device_by_type_resource,
        get_log_alias_resource,
        get_room_by_name_resource,
        get_scene_by_name_resource,
        get_scene_resource,
        get_user_variable_by_name_resource,
        get_user_variable_resource,
    )

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=DEVICES_MOCK_RESPONSE)
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid=2").mock(
        return_value=Response(200, json={"result": [{"idx": "2", "Name": "Kitchen Light"}]})
    )
    assert "Kitchen Light" in await get_device_by_name_resource("Kitchen Light")

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid=45").mock(
        return_value=Response(200, json={"result": [{"idx": "45", "Name": "Thermostat"}]})
    )
    assert "Thermostat" in await get_device_by_type_resource("Temp", "Thermostat", 45)

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&plan=10").mock(
        return_value=Response(200, json={"result": [{"idx": "1", "Name": "Living Room Light"}]})
    )
    assert "Living Room Light" in await get_room_by_name_resource("Living Room", 10)

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenes").mock(
        return_value=Response(200, json={"result": [{"idx": "101", "Name": "Movie Mode"}]})
    )
    assert json.loads(await get_scene_resource(101))["result"]["Name"] == "Movie Mode"
    assert json.loads(await get_scene_by_name_resource("Movie Mode"))["result"]["idx"] == "101"

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getuservariables").mock(
        return_value=Response(200, json={"result": [{"idx": "50", "Name": "MyVar"}]})
    )
    assert json.loads(await get_user_variable_resource(50))["result"]["Name"] == "MyVar"
    assert json.loads(await get_user_variable_by_name_resource("MyVar"))["result"]["idx"] == "50"

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getlog&lastlogtime=0&loglevel=268435455").mock(
        return_value=Response(200, json={"result": [{"message": "Log entry"}]})
    )
    assert "Log entry" in await get_log_alias_resource()

@pytest.mark.asyncio
@respx.mock
async def test_error_cases():
    # Device not found
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json={"result": []})
    )
    with pytest.raises(ToolError, match="Device not found"):
        await get_device(name="Missing")
    
    # Invalid switch state
    from domoticz_mcp.server import set_switch_state
    with pytest.raises(ToolError, match="state must be 'On' or 'Off'"):
        await set_switch_state("Invalid", idx=1)

@pytest.mark.asyncio
@respx.mock
async def test_missing_params():
    with pytest.raises(ToolError, match="Device not found"):
        await toggle_switch()

def test_dns_rebinding_protection_disabled():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient
    
    mcp_test = FastMCP("Test1", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    app = mcp_test.streamable_http_app()
    
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/mcp", headers={"Host": "127.0.0.1", "Content-Type": "application/json"})
        assert response.status_code != 421

def test_dns_rebinding_protection_disabled_negative():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient
    
    mcp_test = FastMCP("Test2", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    app = mcp_test.streamable_http_app()

    with TestClient(app, base_url="http://192.168.100.100") as client:
        response = client.post("/mcp", headers={"Host": "192.168.100.100", "Content-Type": "application/json"})
        assert response.status_code != 421

def test_dns_rebinding_protection_enabled_rejects():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient
    
    strict_mcp = FastMCP("Test3", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=["127.0.0.1:*"]))
    app = strict_mcp.streamable_http_app()
    
    with TestClient(app, base_url="http://192.168.100.100") as client:
        response = client.post("/mcp", headers={"Host": "192.168.100.100", "Content-Type": "application/json"})
        assert response.status_code == 421

@pytest.mark.asyncio
@respx.mock
async def test_get_overview(monkeypatch):
    from domoticz_mcp.server import get_overview

    private_endpoint_marker = "private-domoticz-endpoint-marker"
    monkeypatch.setattr(
        server_module,
        "DOMOTICZ_BASE_URL",
        private_endpoint_marker,
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getversion").mock(
        return_value=Response(200, json={"version": "2024.1", "build_time": "2024-01-01"})
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=DEVICES_MOCK_RESPONSE)
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getscenes").mock(
        return_value=Response(200, json={"result": []})
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getuservariables").mock(
        return_value=Response(200, json={"result": []})
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true").mock(
        return_value=Response(200, json=PLANS_MOCK_RESPONSE)
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=gethardware").mock(
        return_value=Response(200, json={"result": [{"idx": "1"}]})
    )
    
    response = await get_overview()
    data = json.loads(response)
    assert data["result"]["system"]["version"] == "2024.1"
    assert data["result"]["counts"]["devices"] == 3
    assert data["result"]["counts"]["rooms_plans"] == 2
    assert "domoticz_url" not in data["result"]["system"]
    if private_endpoint_marker in response:
        raise AssertionError("Configured Domoticz endpoint escaped through overview")

@pytest.mark.asyncio
@respx.mock
async def test_get_system_health():
    from domoticz_mcp.server import get_system_health
    
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=gethardware").mock(
        return_value=Response(200, json={"result": [{"Name": "Z-Wave", "Type": "Z-Wave USB", "Enabled": "true"}]})
    )
    
    old_time = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    mock_data = {
        "result": [
            {"idx": "1", "Name": "Dead Sensor", "LastUpdate": old_time}
        ]
    }
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=mock_data)
    )
    
    response = await get_system_health()
    data = json.loads(response)
    assert data["result"]["hardware_health"][0]["Status"] == "Online"
    assert data["result"]["unresponsive_devices_count"] == 1

@pytest.mark.asyncio
@respx.mock
async def test_search_scripts():
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list").mock(
        return_value=Response(200, json={
            "result": [{"id": "10", "idx": "999", "name": "AutoLight", "interpreter": "python"}],
        })
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=load&event=10").mock(
        return_value=Response(200, json={"result": [{"xmlstatement": "if light_level < 50: turn_on()"}]})
    )
    
    response = await search_scripts(query="light_level")
    data = json.loads(response)
    assert len(data["result"]) == 1
    assert data["result"][0]["id"] == "10"
    assert data["result"][0]["idx"] == "10"
    assert data["result"][0]["Name"] == "AutoLight"
    assert data["count"] == 1
    assert data["has_more"] is False

@pytest.mark.asyncio
@respx.mock
async def test_error_logs_resource():
    from domoticz_mcp.server import get_error_logs_resource
    
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getlog&lastlogtime=0&loglevel=4").mock(
        return_value=Response(200, json={"result": [{"level": 4, "message": "Critical Error"}]})
    )
    
    response = await get_error_logs_resource()
    assert "Critical Error" in response

def test_agent_guidance():
    prompt = agent_guidance()
    assert "expert" in prompt.lower()
    assert "domoticz" in prompt.lower()

def test_error_types():
    err = DomoticzError("Test error", 500)
    assert err.message == "Test error"
    assert err.status_code == 500

    dev_err = DeviceNotFoundError("Kitchen Light")
    assert dev_err.identifier == "Kitchen Light"
    assert dev_err.status_code == 404

    scene_err = SceneNotFoundError("Movie Mode")
    assert scene_err.identifier == "Movie Mode"
    assert scene_err.status_code == 404

    var_err = UserVariableNotFoundError("MyVar")
    assert var_err.identifier == "MyVar"
    assert var_err.status_code == 404

    auth_err = AuthenticationError()
    assert auth_err.status_code == 401

    param_err = InvalidParameterError("Invalid value")
    assert param_err.status_code == 400

def test_client_lifecycle():
    client = create_client(own_client=True)
    assert client._owns_client is True
    assert client._own_client is True


@pytest.mark.asyncio
async def test_default_clients_reuse_shared_http_client():
    await server_module.close_global_client()
    try:
        first = create_client()
        second = create_client()

        assert first.client is second.client
        assert first._owns_client is False
        assert second._owns_client is False

        async with first as first_client:
            pass
        assert first_client.is_closed is False

        await server_module.close_global_client()
        assert first_client.is_closed is True
        assert server_module._global_http_client is None

        await server_module.close_global_client()
    finally:
        await server_module.close_global_client()


def test_default_domoticz_url_is_localhost():
    assert server_module.DOMOTICZ_BASE_URL == "http://127.0.0.1:8080"
    assert server_module.DOMOTICZ_API_URL == "http://127.0.0.1:8080/json.htm"


def test_new_oauth_token_file_uses_owner_only_permissions(tmp_path, monkeypatch):
    token_file = tmp_path / "token.json"
    monkeypatch.setattr(server_module, "TOKEN_FILE", str(token_file))

    server_module._save_token_data({"access_token": "test-token"})

    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


async def _prepare_oauth_tool_test(tmp_path, monkeypatch):
    await server_module._shutdown_oauth_login_flow()
    server_module._open_oauth_login_manager()
    await server_module.close_global_client()
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "access_token": "old-test-access-token",
        "refresh_token": "preserved-test-refresh-token",
    }))
    token_file.chmod(0o600)
    monkeypatch.setattr(server_module, "DOMOTICZ_BASE_URL", "https://domoticz.example")
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(server_module, "DOMOTICZ_USERNAME", None)
    monkeypatch.setattr(server_module, "DOMOTICZ_PASSWORD", None)
    monkeypatch.setattr(server_module, "DOMOTICZ_OAUTH_TOKEN", None)
    monkeypatch.setattr(server_module, "TOKEN_FILE", str(token_file))
    monkeypatch.setattr(server_module, "_oauth_token_cache", "old-test-access-token")
    monkeypatch.setattr(server_module, "_oauth_login_start_lock", asyncio.Lock())
    monkeypatch.setattr(server_module, "_token_refresh_lock", asyncio.Lock())
    monkeypatch.setattr(server_module, "OAUTH_CALLBACK_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(server_module.webbrowser, "open", lambda _url: True)
    return token_file


def _oauth_callback_url(authorization_url, **callback_params):
    authorization_query = urllib.parse.parse_qs(
        urllib.parse.urlparse(authorization_url).query
    )
    callback_params.setdefault("state", authorization_query["state"][0])
    return (
        authorization_query["redirect_uri"][0]
        + "?"
        + urllib.parse.urlencode(callback_params)
    )


def _request_oauth_callback(callback_url):
    try:
        with urllib.request.urlopen(callback_url, timeout=1) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code


@pytest.mark.asyncio
@respx.mock
async def test_oauth_tools_complete_login_without_exposing_credentials(
    tmp_path,
    monkeypatch,
):
    token_file = await _prepare_oauth_tool_test(tmp_path, monkeypatch)
    discovery_route = respx.get(
        "https://domoticz.example/.well-known/openid-configuration"
    ).mock(return_value=Response(200, json={
        "token_endpoint": "https://domoticz.example/oauth/token",
    }))
    token_route = respx.post("https://domoticz.example/oauth/token").mock(
        return_value=Response(200, json={"access_token": "new-test-access-token"})
    )
    shared_client = httpx.AsyncClient()
    monkeypatch.setattr(server_module, "_global_http_client", shared_client)

    try:
        started = await server_module.start_oauth_login()
        with server_module._oauth_login_flow_lock:
            flow = server_module._active_oauth_login_flow
        assert flow is not None

        serialized_start = started.model_dump_json()
        assert "test-client-secret" not in serialized_start
        assert "preserved-test-refresh-token" not in serialized_start
        assert flow.code_verifier not in serialized_start
        assert "authorization_url" not in serialized_start
        if flow.authorization_url in serialized_start:
            raise AssertionError("OAuth transaction URL escaped through MCP output")
        authorization_query = urllib.parse.parse_qs(
            urllib.parse.urlparse(flow.authorization_url).query
        )
        expected_challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(flow.code_verifier.encode("ascii")).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        assert authorization_query["code_challenge"] == [expected_challenge]

        wrong_state_status = await asyncio.to_thread(
            _request_oauth_callback,
            _oauth_callback_url(
                flow.authorization_url,
                code="ignored-test-code",
                state="wrong-state",
            ),
        )
        assert wrong_state_status == 400
        assert (
            await server_module.get_oauth_login_status(started.flow_id)
        ).status == "pending"

        accepted_status = await asyncio.to_thread(
            _request_oauth_callback,
            _oauth_callback_url(
                flow.authorization_url,
                code="accepted-test-code",
            ),
        )
        assert accepted_status == 200
        await asyncio.wait_for(flow.supervisor_task, timeout=1)

        completed = await server_module.get_oauth_login_status(started.flow_id)
        assert completed.status == "complete"
        assert "token" not in completed.model_dump_json().lower()
        saved_token_data = json.loads(token_file.read_text())
        assert saved_token_data == {
            "access_token": "new-test-access-token",
            "refresh_token": "preserved-test-refresh-token",
        }
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
        assert shared_client.headers["Authorization"] == "Bearer new-test-access-token"
        assert flow.code_verifier == ""
        assert flow.authorization_code is None
        assert discovery_route.calls.call_count == 1
        assert token_route.calls.call_count == 1
    finally:
        await server_module._shutdown_oauth_login_flow()
        await server_module.close_global_client()


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_oauth_tool_start_reuses_one_flow(tmp_path, monkeypatch):
    await _prepare_oauth_tool_test(tmp_path, monkeypatch)

    try:
        first, second = await asyncio.gather(
            server_module.start_oauth_login(),
            server_module.start_oauth_login(),
        )

        assert first.flow_id == second.flow_id
        assert "authorization_url" not in first.model_dump()
        assert "authorization_url" not in second.model_dump()
        assert len(respx.calls) == 0
        with server_module._oauth_login_flow_lock:
            flow = server_module._active_oauth_login_flow
        assert flow is not None
        assert flow.callback_thread is not None
        assert flow.callback_thread.is_alive()
    finally:
        await server_module._shutdown_oauth_login_flow()


@pytest.mark.asyncio
@respx.mock
async def test_oauth_tool_flow_expires_and_closes_listener(tmp_path, monkeypatch):
    await _prepare_oauth_tool_test(tmp_path, monkeypatch)
    monkeypatch.setattr(server_module, "OAUTH_CALLBACK_TIMEOUT_SECONDS", 0.01)

    partial_client = None
    try:
        started = await server_module.start_oauth_login()
        with server_module._oauth_login_flow_lock:
            flow = server_module._active_oauth_login_flow
        assert flow is not None
        authorization_query = urllib.parse.parse_qs(
            urllib.parse.urlparse(flow.authorization_url).query
        )
        redirect = urllib.parse.urlparse(authorization_query["redirect_uri"][0])
        partial_client = socket.create_connection(
            (redirect.hostname, redirect.port),
            timeout=1,
        )
        partial_client.sendall(b"GET /callback HTTP/1.1\r\n")
        await asyncio.wait_for(flow.supervisor_task, timeout=1)

        expired = await server_module.get_oauth_login_status(started.flow_id)
        assert expired.status == "expired"
        assert flow.callback_thread is not None
        assert flow.callback_thread.is_alive() is False
        assert flow.code_verifier == ""
    finally:
        if partial_client is not None:
            partial_client.close()
        await server_module._shutdown_oauth_login_flow()


@pytest.mark.asyncio
@respx.mock
async def test_oauth_tool_reports_provider_rejection_without_exchange(
    tmp_path,
    monkeypatch,
):
    await _prepare_oauth_tool_test(tmp_path, monkeypatch)

    try:
        started = await server_module.start_oauth_login()
        with server_module._oauth_login_flow_lock:
            flow = server_module._active_oauth_login_flow
        assert flow is not None
        callback_status = await asyncio.to_thread(
            _request_oauth_callback,
            _oauth_callback_url(
                flow.authorization_url,
                error="access_denied",
            ),
        )
        assert callback_status == 400
        await asyncio.wait_for(flow.supervisor_task, timeout=1)

        rejected = await server_module.get_oauth_login_status(started.flow_id)
        assert rejected.status == "error"
        assert "access_denied" not in rejected.model_dump_json()
        assert len(respx.calls) == 0
    finally:
        await server_module._shutdown_oauth_login_flow()


@pytest.mark.asyncio
@respx.mock
async def test_oauth_tool_sanitizes_token_exchange_failure(tmp_path, monkeypatch):
    await _prepare_oauth_tool_test(tmp_path, monkeypatch)
    respx.get(
        "https://domoticz.example/.well-known/openid-configuration"
    ).mock(return_value=Response(200, json={
        "token_endpoint": "https://domoticz.example/oauth/token",
    }))
    token_route = respx.post("https://domoticz.example/oauth/token").mock(
        return_value=Response(400, json={
            "error": "invalid_grant",
            "error_description": "test-sensitive-provider-detail",
        })
    )

    try:
        started = await server_module.start_oauth_login()
        with server_module._oauth_login_flow_lock:
            flow = server_module._active_oauth_login_flow
        assert flow is not None
        callback_status = await asyncio.to_thread(
            _request_oauth_callback,
            _oauth_callback_url(
                flow.authorization_url,
                code="rejected-test-code",
            ),
        )
        assert callback_status == 200
        await asyncio.wait_for(flow.supervisor_task, timeout=1)

        failed = await server_module.get_oauth_login_status(started.flow_id)
        serialized_status = failed.model_dump_json()
        assert failed.status == "error"
        assert "test-sensitive-provider-detail" not in serialized_status
        assert "rejected-test-code" not in serialized_status
        assert token_route.calls.call_count == 1
    finally:
        await server_module._shutdown_oauth_login_flow()


@pytest.mark.asyncio
@respx.mock
async def test_oauth_tool_start_is_immediate_and_shutdown_cancels_discovery(
    tmp_path,
    monkeypatch,
):
    await _prepare_oauth_tool_test(tmp_path, monkeypatch)
    discovery_started = asyncio.Event()
    discovery_cancelled = asyncio.Event()
    never_release = asyncio.Event()

    async def stalled_discovery(request):
        discovery_started.set()
        try:
            await never_release.wait()
        except asyncio.CancelledError:
            discovery_cancelled.set()
            raise

    respx.get(
        "https://domoticz.example/.well-known/openid-configuration"
    ).mock(side_effect=stalled_discovery)

    try:
        started = await asyncio.wait_for(
            server_module.start_oauth_login(),
            timeout=0.2,
        )
        assert discovery_started.is_set() is False
        with server_module._oauth_login_flow_lock:
            flow = server_module._active_oauth_login_flow
        assert flow is not None

        callback_status = await asyncio.to_thread(
            _request_oauth_callback,
            _oauth_callback_url(
                flow.authorization_url,
                code="accepted-test-code",
            ),
        )
        assert callback_status == 200
        await asyncio.wait_for(discovery_started.wait(), timeout=1)

        await asyncio.wait_for(
            server_module._shutdown_oauth_login_flow(),
            timeout=1,
        )
        assert discovery_cancelled.is_set()
        with pytest.raises(ToolError, match="shutting down"):
            await server_module.start_oauth_login()
    finally:
        never_release.set()
        await server_module._shutdown_oauth_login_flow()


@pytest.mark.asyncio
async def test_oauth_tool_requires_client_id_and_known_flow(tmp_path, monkeypatch):
    await _prepare_oauth_tool_test(tmp_path, monkeypatch)
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_ID", None)

    with pytest.raises(ToolError, match="DOMOTICZ_CLIENT_ID"):
        await server_module.start_oauth_login()
    with pytest.raises(ToolError, match="not found"):
        await server_module.get_oauth_login_status("A" * 32)


@pytest.mark.asyncio
async def test_oauth_tool_fails_cleanly_when_local_browser_is_unavailable(
    tmp_path,
    monkeypatch,
):
    await _prepare_oauth_tool_test(tmp_path, monkeypatch)
    monkeypatch.setattr(server_module.webbrowser, "open", lambda _url: False)

    with pytest.raises(ToolError, match="Could not open a local browser"):
        await server_module.start_oauth_login()

    with server_module._oauth_login_flow_lock:
        assert server_module._active_oauth_login_flow is None


@pytest.mark.asyncio
async def test_oauth_tool_bounds_a_stalled_local_browser_launcher(
    tmp_path,
    monkeypatch,
):
    await _prepare_oauth_tool_test(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server_module,
        "OAUTH_BROWSER_OPEN_TIMEOUT_SECONDS",
        0.005,
    )
    monkeypatch.setattr(
        server_module.webbrowser,
        "open",
        lambda _url: server_module.time.sleep(0.05) or True,
    )

    try:
        started = await asyncio.wait_for(
            server_module.start_oauth_login(),
            timeout=0.2,
        )

        assert started.status == "pending"
        assert "authorization_url" not in started.model_dump()
        with server_module._oauth_login_flow_lock:
            assert server_module._active_oauth_login_flow is not None
        await asyncio.sleep(0.06)
        assert (
            await server_module.get_oauth_login_status(started.flow_id)
        ).status == "pending"
    finally:
        await server_module._shutdown_oauth_login_flow()


@pytest.mark.asyncio
async def test_create_client_uses_direct_oauth_token(monkeypatch):
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_ID", None)
    monkeypatch.setattr(server_module, "DOMOTICZ_USERNAME", None)
    monkeypatch.setattr(server_module, "DOMOTICZ_PASSWORD", None)
    monkeypatch.setattr(server_module, "DOMOTICZ_OAUTH_TOKEN", "direct-token")
    monkeypatch.setattr(server_module, "_oauth_token_cache", "direct-token")

    async with create_client(own_client=True) as client:
        assert client.headers["Authorization"] == "Bearer direct-token"
        assert client.auth is None

@pytest.mark.asyncio
@respx.mock
async def test_concurrent_oauth_refresh_only_refreshes_once(tmp_path, monkeypatch):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "access_token": "expired-token",
        "refresh_token": "refresh-token",
    }))
    token_file.chmod(0o666)

    monkeypatch.setattr(server_module, "DOMOTICZ_BASE_URL", "https://domoticz.example")
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_ID", "client-id")
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(server_module, "TOKEN_FILE", str(token_file))
    monkeypatch.setattr(server_module, "_oauth_token_cache", "expired-token")

    discovery_route = respx.get(
        "https://domoticz.example/.well-known/openid-configuration"
    ).mock(return_value=Response(200, json={
        "token_endpoint": "https://domoticz.example/oauth/token",
    }))
    refresh_route = respx.post("https://domoticz.example/oauth/token").mock(
        return_value=Response(200, json={"access_token": "fresh-token"})
    )

    tokens = await asyncio.gather(
        server_module._fetch_oauth_token(force_refresh=True, old_token="expired-token"),
        server_module._fetch_oauth_token(force_refresh=True, old_token="expired-token"),
    )

    assert tokens == ["fresh-token", "fresh-token"]
    assert discovery_route.calls.call_count == 1
    assert refresh_route.calls.call_count == 1
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


@pytest.mark.asyncio
@respx.mock
async def test_failed_oauth_refresh_fails_fast_without_browser(tmp_path, monkeypatch):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "access_token": "expired-token",
        "refresh_token": "invalid-refresh-token",
    }))
    token_file.chmod(0o666)

    monkeypatch.setattr(server_module, "DOMOTICZ_BASE_URL", "https://domoticz.example")
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_ID", "client-id")
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(server_module, "DOMOTICZ_USERNAME", None)
    monkeypatch.setattr(server_module, "DOMOTICZ_PASSWORD", None)
    monkeypatch.setattr(server_module, "TOKEN_FILE", str(token_file))
    monkeypatch.setattr(server_module, "_oauth_token_cache", "expired-token")
    monkeypatch.setattr(server_module, "_token_refresh_lock", asyncio.Lock())

    def unexpected_interactive_flow(*args, **kwargs):
        pytest.fail("MCP requests must not start interactive OAuth")

    monkeypatch.setattr(
        server_module,
        "_do_interactive_oauth_flow",
        unexpected_interactive_flow,
    )
    respx.get(
        "https://domoticz.example/.well-known/openid-configuration"
    ).mock(return_value=Response(200, json={
        "token_endpoint": "https://domoticz.example/oauth/token",
    }))
    respx.post("https://domoticz.example/oauth/token").mock(
        return_value=Response(400, json={"error": "invalid_grant"})
    )

    with pytest.raises(ToolError, match="--authenticate"):
        await asyncio.wait_for(
            server_module._fetch_oauth_token(
                force_refresh=True,
                old_token="expired-token",
            ),
            timeout=1,
        )

    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


@pytest.mark.asyncio
@respx.mock
async def test_interactive_oauth_waits_outside_refresh_lock(tmp_path, monkeypatch):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "access_token": "expired-token",
        "refresh_token": "invalid-refresh-token",
    }))

    monkeypatch.setattr(server_module, "DOMOTICZ_BASE_URL", "https://domoticz.example")
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_ID", "client-id")
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(server_module, "DOMOTICZ_USERNAME", None)
    monkeypatch.setattr(server_module, "DOMOTICZ_PASSWORD", None)
    monkeypatch.setattr(server_module, "TOKEN_FILE", str(token_file))
    monkeypatch.setattr(server_module, "_oauth_token_cache", "expired-token")
    monkeypatch.setattr(server_module, "_token_refresh_lock", asyncio.Lock())

    def bounded_interactive_flow(*args, **kwargs):
        assert server_module._token_refresh_lock.locked() is False
        return None, None, None

    monkeypatch.setattr(
        server_module,
        "_do_interactive_oauth_flow",
        bounded_interactive_flow,
    )
    respx.get(
        "https://domoticz.example/.well-known/openid-configuration"
    ).mock(return_value=Response(200, json={
        "token_endpoint": "https://domoticz.example/oauth/token",
    }))
    respx.post("https://domoticz.example/oauth/token").mock(
        return_value=Response(400, json={"error": "invalid_grant"})
    )

    with pytest.raises(ToolError, match="timed out"):
        await server_module._fetch_oauth_token(
            force_refresh=True,
            old_token="expired-token",
            allow_interactive=True,
        )


@pytest.mark.asyncio
async def test_interactive_oauth_callback_has_deadline(monkeypatch):
    monkeypatch.setattr(server_module, "DOMOTICZ_BASE_URL", "https://domoticz.example")
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_ID", "client-id")
    monkeypatch.setattr(server_module.webbrowser, "open", lambda _url: False)

    result = await asyncio.wait_for(
        asyncio.to_thread(server_module._do_interactive_oauth_flow, 0),
        timeout=1,
    )

    assert result == (None, None, None)


@pytest.mark.asyncio
async def test_interactive_oauth_rejects_wrong_state_then_accepts_valid_callback(
    monkeypatch,
    capsys,
):
    authorization_urls = queue.Queue()
    monkeypatch.setattr(server_module, "DOMOTICZ_BASE_URL", "https://domoticz.example")
    monkeypatch.setattr(server_module, "DOMOTICZ_CLIENT_ID", "client-id")
    monkeypatch.setattr(
        server_module.webbrowser,
        "open",
        lambda url: authorization_urls.put(url) or True,
    )

    flow_task = asyncio.create_task(
        asyncio.to_thread(server_module._do_interactive_oauth_flow, 2)
    )
    authorization_url = await asyncio.wait_for(
        asyncio.to_thread(authorization_urls.get, True, 1),
        timeout=1,
    )
    authorization_query = urllib.parse.parse_qs(
        urllib.parse.urlparse(authorization_url).query
    )
    redirect_uri = authorization_query["redirect_uri"][0]
    expected_state = authorization_query["state"][0]

    async with httpx.AsyncClient() as client:
        rejected = await client.get(
            redirect_uri,
            params={"code": "wrong-code", "state": "wrong-state"},
        )
        accepted = await client.get(
            redirect_uri,
            params={"code": "valid-code", "state": expected_state},
        )

    code, code_verifier, returned_redirect_uri = await asyncio.wait_for(
        flow_task,
        timeout=1,
    )
    assert rejected.status_code == 400
    assert accepted.status_code == 200
    assert code == "valid-code"
    assert code_verifier
    assert returned_redirect_uri == redirect_uri
    stderr = capsys.readouterr().err
    if authorization_url in stderr or redirect_uri in stderr:
        raise AssertionError("OAuth transaction data was written to stderr")

@pytest.mark.asyncio
@respx.mock
async def test_do_request_updates_client_headers_after_oauth_refresh(monkeypatch):
    url = "https://domoticz.example/json.htm?type=command&param=getversion"
    route = respx.get(url).mock(side_effect=[
        Response(401, json={"status": "ERR"}),
        Response(200, json={"status": "OK"}),
    ])

    async def fake_fetch_oauth_token(force_refresh=False, old_token=None):
        server_module._oauth_token_cache = "fresh-token"
        return "fresh-token"

    monkeypatch.setattr(server_module, "_oauth_token_cache", "expired-token")
    monkeypatch.setattr(server_module, "_fetch_oauth_token", fake_fetch_oauth_token)

    async with httpx.AsyncClient(headers={"Authorization": "Bearer expired-token"}) as client:
        response = await server_module._do_request(client, "GET", url)
        assert response.json()["status"] == "OK"
        assert client.headers["Authorization"] == "Bearer fresh-token"

    assert route.calls[0].request.headers["Authorization"] == "Bearer expired-token"
    assert route.calls[1].request.headers["Authorization"] == "Bearer fresh-token"


@pytest.mark.asyncio
async def test_do_request_translates_upstream_timeout():
    async def timeout_handler(request):
        raise httpx.ReadTimeout("upstream stalled", request=request)

    transport = httpx.MockTransport(timeout_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ToolError, match="Domoticz request timed out") as exc_info:
            await server_module._do_request(
                client,
                "GET",
                "https://domoticz.example/json.htm",
            )

    assert exc_info.value.__cause__ is None


@pytest.mark.asyncio
async def test_http_errors_do_not_expose_request_endpoint_or_query_values():
    marker = "private-query-marker"

    async def error_handler(request):
        return Response(500, request=request)

    transport = httpx.MockTransport(error_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ToolError) as exc_info:
            await server_module._do_request(
                client,
                "GET",
                f"https://private.example/json.htm?value={marker}",
            )

    message = str(exc_info.value)
    assert message == "Domoticz request failed with HTTP 500."
    assert exc_info.value.__cause__ is None
    if marker in message or "private.example" in message:
        raise AssertionError("HTTP error exposed its request target")


def test_upstream_errors_do_not_echo_response_details():
    marker = "private-upstream-error-marker"
    response = Response(200, json={
        "status": "ERR",
        "message": f"password={marker}",
    })

    with pytest.raises(ToolError) as exc_info:
        server_module._tool_result(response, "Testing action")

    assert str(exc_info.value) == "Testing action failed."
    if marker in str(exc_info.value):
        raise AssertionError("Upstream error detail escaped through ToolError")


@pytest.mark.asyncio
async def test_overview_fetches_independent_data_concurrently(monkeypatch):
    class DummyClientContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc_value, traceback):
            return None

    started = 0
    active = 0
    peak_active = 0
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def synchronize(result):
        nonlocal started, active, peak_active
        started += 1
        active += 1
        peak_active = max(peak_active, active)
        if started == 6:
            all_started.set()
        await release.wait()
        active -= 1
        return result

    async def fake_request(client, method, url, **kwargs):
        payload = {"result": []} if "gethardware" in url else {"version": "test"}
        return await synchronize(Response(200, json=payload))

    async def fake_cached_data(client, cache, api_url, key_path="result"):
        return await synchronize([])

    monkeypatch.setattr(server_module, "create_client", lambda: DummyClientContext())
    monkeypatch.setattr(server_module, "_do_request", fake_request)
    monkeypatch.setattr(server_module, "_get_cached_data", fake_cached_data)

    overview_task = asyncio.create_task(server_module.get_overview())
    await asyncio.wait_for(all_started.wait(), timeout=1)
    release.set()
    result = json.loads(await asyncio.wait_for(overview_task, timeout=1))

    assert peak_active == 6
    assert result["result"]["system"]["version"] == "test"


@pytest.mark.asyncio
async def test_overview_cancels_and_drains_siblings_after_failure(monkeypatch):
    class DummyClientContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc_value, traceback):
            return None

    started = 0
    cancelled = 0
    all_started = asyncio.Event()
    block_siblings = asyncio.Event()

    async def wait_or_fail(should_fail=False):
        nonlocal started, cancelled
        started += 1
        if started == 6:
            all_started.set()
        await all_started.wait()
        if should_fail:
            raise ToolError("overview failed")
        try:
            await block_siblings.wait()
        except asyncio.CancelledError:
            cancelled += 1
            raise

    async def fake_request(client, method, url, **kwargs):
        await wait_or_fail(should_fail="getversion" in url)
        return Response(200, json={"result": []})

    async def fake_cached_data(client, cache, api_url, key_path="result"):
        await wait_or_fail()
        return []

    monkeypatch.setattr(server_module, "create_client", lambda: DummyClientContext())
    monkeypatch.setattr(server_module, "_do_request", fake_request)
    monkeypatch.setattr(server_module, "_get_cached_data", fake_cached_data)

    with pytest.raises(ToolError, match="overview failed"):
        await asyncio.wait_for(server_module.get_overview(), timeout=1)

    assert cancelled == 5


@pytest.mark.asyncio
async def test_explicit_authentication_requires_a_token(monkeypatch):
    async def no_token(**kwargs):
        return None

    monkeypatch.setattr(server_module, "_fetch_oauth_token", no_token)

    with pytest.raises(ToolError, match="No OAuth token was obtained"):
        await server_module._run_explicit_authentication()

@pytest.mark.asyncio
@respx.mock
async def test_resolve_idx_shared():
    from domoticz_mcp.server import _resolve_idx
    mock_devices = {"result": [{"idx": "42", "Name": "Test Device"}]}
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=mock_devices)
    )
    async with create_client() as client:
        idx = await _resolve_idx(client, idx=42, name=None, cache=_device_cache, api_url=f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        assert idx == 42
        idx = await _resolve_idx(client, idx=None, name="Test Device", cache=_device_cache, api_url=f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        assert idx == 42

def test_error_response_helper():
    from domoticz_mcp.server import _error_response
    with pytest.raises(ToolError, match="Test error"):
        _error_response("Test error")

def test_format_response_helper():
    from domoticz_mcp.server import _format_response
    test_data = {"status": "OK", "result": {"count": 1}}
    resp = _format_response(test_data)
    assert json.loads(resp) == test_data


def test_public_data_sanitization_is_recursive_non_mutating_and_idempotent():
    markers = [f"must-not-escape-{index}" for index in range(1, 10)]
    jwt_marker = (
        "aaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc"
    )
    pem_marker = (
        "-----BEGIN PRIVATE KEY-----\n"
        "synthetic-private-material\n"
        "-----END PRIVATE KEY-----"
    )
    original = {
        "Password": markers[0],
        "nested": [
            {
                "api-key": markers[1],
                "oauthState": markers[2],
                "callback_uri": "https://private.example/callback",
            }
        ],
        "named_value": {
            "Name": "mqtt_password",
            "Value": markers[3],
        },
        "Extra": json.dumps({"client_secret": markers[4]}),
        "message": (
            f"Bearer {markers[5]} password={markers[6]} "
            f"https://private.example/{markers[7]} 192.0.2.10"
        ),
        "jwt_text": f"received {jwt_marker}",
        "pem_text": pem_marker,
        "safe": {"Name": "Kitchen", "Value": markers[8]},
    }

    sanitized = server_module._sanitize_public_data(original)
    serialized = json.dumps(sanitized)

    for marker in markers[:8]:
        if marker in serialized:
            raise AssertionError("Sensitive synthetic marker escaped redaction")
    if jwt_marker in serialized or pem_marker in serialized:
        raise AssertionError("Unlabeled credential material escaped redaction")
    assert markers[8] in serialized
    assert original["Password"] == markers[0]
    assert sanitized["Password"] == server_module.PUBLIC_REDACTION
    assert sanitized["named_value"]["Value"] == server_module.PUBLIC_REDACTION
    assert sanitized["safe"]["Value"] == markers[8]
    assert server_module._sanitize_public_data(sanitized) == sanitized


def test_sensitive_field_detection_normalizes_common_variants():
    sensitive_fields = (
        "Password",
        "mqtt_username",
        "MQTTPassword",
        "MQTTUsername",
        "MQTTBroker",
        "mqttpass",
        "mqttuser",
        "mqttbroker",
        "mqttserver",
        "mqttport",
        "mqttaddress",
        "mqtturl",
        "brokerhost",
        "proxyurl",
        "callbackurl",
        "redirecturi",
        "authorizationurl",
        "secretkey",
        "consumersecret",
        "password1",
        "accesstoken1",
        "HTTPPassword",
        "IPAddress",
        "RemoteIPAddress",
        "somepassword",
        "api-key",
        "clientSecret",
        "access.token",
        "refresh token",
        "PairingKey",
        "link-id",
        "OAuthState",
        "code_verifier",
        "state",
        "code",
        "privateEndpoint",
        "WebLocalNetworks",
        "Mode1",
        "StrParam6",
        "Extra",
    )

    assert all(
        server_module._is_sensitive_public_field(field_name)
        for field_name in sensitive_fields
    )
    assert server_module._is_sensitive_public_field("Name") is False
    assert server_module._is_sensitive_public_field("Enabled") is False
    assert server_module._is_sensitive_public_field("BatteryLevel") is False


def test_public_text_sanitization_covers_network_and_assignment_variants():
    marker = "synthetic-redaction-marker"
    sensitive_text = (
        f"api key={marker}; pairing key='{marker}'; "
        f"code.verifier={marker}; MQTTPassword={marker}; "
        "addresses 2001:db8::1234 and [fe80::1%eth0]:8123; "
        "mapped ::ffff:192.0.2.45 and [::ffff:192.0.2.46]; "
        "domoticz:8080/json; broker.local/topic; "
        "custom+transport://internal-host/private; "
        "private.privatezone; connected to home-hub; "
        "AA:BB:CC:DD:EE:FF"
    )

    sanitized = server_module._sanitize_public_text(sensitive_text)

    if marker in sanitized:
        raise AssertionError("Credential assignment variant escaped redaction")
    for network_value in (
        "2001:db8::1234",
        "fe80::1%eth0",
        "::ffff:192.0.2.45",
        "::ffff:192.0.2.46",
        "domoticz:8080",
        "broker.local",
        "internal-host",
        "private.privatezone",
        "home-hub",
        "AA:BB:CC:DD:EE:FF",
    ):
        if network_value in sanitized:
            raise AssertionError("Network endpoint variant escaped redaction")

    safe_text = (
        "transport=sse compass=north spin=fast enduser=guest "
        "scripts/lights.lua room/device ratio a/b device:123"
    )
    assert server_module._sanitize_public_text(safe_text) == safe_text


def test_public_data_sanitization_covers_oauth_objects_and_mapping_keys():
    marker = "synthetic-oauth-marker"
    original = {
        "transaction": {
            "state": marker,
            "code": marker,
        },
        "loginflow": json.dumps({"state": marker}),
        "flat": {
            "state": marker,
            "code": marker,
        },
        "lone_state": {"state": marker},
        "lone_code": {"code": marker},
        "context_by_value": {"type": "oauth", "state": marker},
        "Canary": marker,
        "flow_id": marker,
        f"https://internal.example/{marker}": {"safe": marker},
        f"custom+sync://second-internal/{marker}": marker,
        "nested": {
            f"192.0.2.25:8080/{marker}": marker,
        },
    }

    sanitized = server_module._sanitize_public_data(original)
    serialized = json.dumps(sanitized)

    if marker in serialized:
        raise AssertionError("OAuth or mapping-key data escaped redaction")
    assert server_module.PUBLIC_REDACTION in sanitized
    assert any(
        str(key).startswith(f"{server_module.PUBLIC_REDACTION} ")
        for key in sanitized
    )
    assert sanitized["transaction"]["state"] == server_module.PUBLIC_REDACTION
    assert marker not in sanitized["loginflow"]
    assert sanitized["flat"]["code"] == server_module.PUBLIC_REDACTION
    assert sanitized["lone_state"]["state"] == server_module.PUBLIC_REDACTION
    assert sanitized["lone_code"]["code"] == server_module.PUBLIC_REDACTION
    assert (
        sanitized["context_by_value"]["state"]
        == server_module.PUBLIC_REDACTION
    )


def test_public_data_sanitization_bounds_nested_json_strings():
    marker = "synthetic-depth-marker"
    nested = json.dumps({"Password": marker})
    for _ in range(server_module.MAX_PUBLIC_SANITIZE_DEPTH + 5):
        nested = json.dumps({"wrapped": nested})

    sanitized = server_module._sanitize_public_data(nested)

    if marker in json.dumps(sanitized):
        raise AssertionError("Nested JSON bypassed the sanitization depth bound")


def test_token_file_load_errors_do_not_expose_exception_details(monkeypatch):
    marker = "synthetic-private-token-path"

    def fail_lstat(_path):
        raise OSError(f"cannot access {marker}")

    monkeypatch.setattr(server_module.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(server_module.os, "lstat", fail_lstat)

    with pytest.raises(ToolError) as exc_info:
        server_module._load_token_data()

    assert str(exc_info.value) == "Failed to load the Domoticz OAuth token file."
    if marker in str(exc_info.value):
        raise AssertionError("Token-file exception details escaped")


def test_command_url_helper_encodes_query_params():
    from domoticz_mcp.server import _command_url

    assert _command_url("addlogmessage", {"message": "a&b=1"}) == (
        f"{DOMOTICZ_API_URL}?type=command&param=addlogmessage&message=a%26b%3D1"
    )

@pytest.mark.asyncio
@respx.mock
async def test_get_events():
    from domoticz_mcp.server import get_events
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list").mock(
        return_value=Response(200, json={"result": [{"name": "test"}]})
    )
    response = await get_events()
    data = json.loads(response)
    assert data["status"] == "OK"
    assert data["total_count"] == 1

@pytest.mark.asyncio
@respx.mock
async def test_new_tools():
    from domoticz_mcp.server import call_domoticz_api, check_for_updates, restart_system, get_camera_snapshot
    from domoticz_mcp.server import DOMOTICZ_BASE_URL
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=testparam&myarg=1").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    with pytest.raises(ToolError, match="Confirmation required"):
        await call_domoticz_api("testparam", {"myarg": "1"})
    response = await call_domoticz_api("testparam", {"myarg": "1"}, confirm=True)
    assert response.status == "OK"
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=checkforupdate").mock(
        return_value=Response(200, json={"status": "OK", "HaveUpdate": False})
    )
    response = await check_for_updates()
    assert json.loads(response)["HaveUpdate"] is False
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=system_reboot").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    with pytest.raises(ToolError, match="Confirmation required"):
        await restart_system(confirm=False)
    response_ok = await restart_system(confirm=True)
    assert response_ok.status == "OK"
    respx.get(f"{DOMOTICZ_BASE_URL}/camsnapshot.jpg?idx=1").mock(
        return_value=Response(200, content=b"fakeimage")
    )
    response = await get_camera_snapshot(1)
    assert json.loads(response)["status"] == "OK"

@pytest.mark.asyncio
@respx.mock
async def test_high_impact_tools_require_confirmation():
    from domoticz_mcp.server import delete_device, set_security_status, update_event

    with pytest.raises(ToolError, match="Confirmation required"):
        await set_security_status(1, "1234")
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=setsecstatus&secstatus=1&seccode=1234").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    response = await set_security_status(1, "1234", confirm=True)
    assert response.status == "OK"

    with pytest.raises(ToolError, match="Confirmation required"):
        await update_event(10, "AutoLight", "python", "device", "print('on')")
    respx.post(f"{DOMOTICZ_API_URL}?type=command&param=events").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    response = await update_event(10, "AutoLight", "python", "device", "print('on')", confirm=True)
    assert response.status == "OK"

    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=DEVICES_MOCK_RESPONSE)
    )
    with pytest.raises(ToolError, match="Confirmation required"):
        await delete_device(name="Kitchen Light")
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=setused&used=false&idx=2").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    response = await delete_device(name="Kitchen Light", confirm=True)
    assert response.status == "OK"

@pytest.mark.asyncio
@respx.mock
async def test_new_resources():
    from domoticz_mcp.server import get_dzvents_docs, get_blockly_docs, get_hardware_resource
    assert "dzVents" in await get_dzvents_docs()
    assert "Blockly" in await get_blockly_docs()
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=gethardware").mock(
        return_value=Response(200, json={"result": [{"Name": "MyHardware"}]})
    )
    response = await get_hardware_resource()
    assert "MyHardware" in json.loads(response)["result"][0]["Name"]


@pytest.mark.asyncio
@respx.mock
async def test_hardware_resource_exposes_only_reviewed_metadata():
    markers = [f"hardware-private-{index}" for index in range(10)]
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=gethardware").mock(
        return_value=Response(200, json={
            "status": "OK",
            "title": "Hardware",
            "result": [{
                "idx": "7",
                "Name": "MQTT gateway",
                "Type": "MQTT",
                "Enabled": "true",
                "Password": markers[0],
                "Username": markers[1],
                "Address": markers[2],
                "Port": 1883,
                "PairingKey": markers[3],
                "LinkId": markers[4],
                "OAuthState": markers[5],
                "Extra": json.dumps({"client_secret": markers[6]}),
                "Mode1": markers[7],
                "NestedConfig": {"refresh_token": markers[8]},
                "ArbitraryPluginField": markers[9],
            }],
        })
    )

    response = json.loads(await server_module.get_hardware_resource())
    item = response["result"][0]

    assert response["status"] == "OK"
    assert response["title"] == "Hardware"
    assert item == {
        "idx": "7",
        "Name": "MQTT gateway",
        "Type": "MQTT",
        "Enabled": "true",
    }
    serialized = json.dumps(response)
    if any(marker in serialized for marker in markers):
        raise AssertionError("Hardware configuration escaped its public allowlist")


@pytest.mark.asyncio
@respx.mock
async def test_settings_resource_exposes_only_reviewed_non_sensitive_fields():
    markers = [f"settings-private-{index}" for index in range(5)]
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getsettings").mock(
        return_value=Response(200, json={
            "status": "OK",
            "title": "settings",
            "Language": "en",
            "TempUnit": 1,
            "SecPassword": markers[0],
            "WebLocalNetworks": markers[1],
            "ClientSecret": markers[2],
            "Nested": {"api_key": markers[3]},
            "UnknownSetting": markers[4],
        })
    )

    response = json.loads(await server_module.get_settings_resource())

    assert response == {
        "status": "OK",
        "title": "settings",
        "Language": "en",
        "TempUnit": 1,
    }
    serialized = json.dumps(response)
    if any(marker in serialized for marker in markers):
        raise AssertionError("Private setting escaped its public allowlist")


@pytest.mark.asyncio
@respx.mock
async def test_generic_api_result_cannot_return_unexpected_upstream_fields():
    marker = "generic-api-private-marker"
    respx.get(
        f"{DOMOTICZ_API_URL}?type=command&param=custom"
    ).mock(return_value=Response(200, json={
        "status": "OK",
        "title": "Custom",
        "Password": marker,
        "result": [{"access_token": marker}],
    }))

    result = await server_module.call_domoticz_api(
        "custom",
        {},
        confirm=True,
    )

    assert result.model_dump() == {"status": "OK", "title": "Custom"}
    if marker in result.model_dump_json():
        raise AssertionError("Generic API response exposed an unexpected field")

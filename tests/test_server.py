import pytest
import respx
import httpx
from httpx import Response
import json
import urllib.parse
from domoticz_mcp.server import (
    get_device, toggle_switch, get_room_devices, get_all_devices,
    _resolve_device_idx, _resolve_scene_idx, _resolve_user_variable_idx,
    create_client, DOMOTICZ_API_URL,
    _device_cache, _scene_cache, _user_variable_cache, _plans_cache
)

def setup_function():
    # Reset caches before each test
    _device_cache["data"] = None
    _device_cache["timestamp"] = 0
    _scene_cache["data"] = None
    _scene_cache["timestamp"] = 0
    _user_variable_cache["data"] = None
    _user_variable_cache["timestamp"] = 0
    _plans_cache["data"] = None
    _plans_cache["timestamp"] = 0

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
    assert json.loads(response) == device_data

@pytest.mark.asyncio
@respx.mock
async def test_toggle_switch_by_idx():
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx=5&switchcmd=Toggle").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    
    response = await toggle_switch(idx=5)
    assert json.loads(response) == {"status": "OK"}

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
    assert json.loads(response) == room_devices

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

@pytest.mark.asyncio
@respx.mock
async def test_search_devices():
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=DEVICES_MOCK_RESPONSE)
    )
    
    from domoticz_mcp.server import search_devices
    
    # Search by name
    response = await search_devices(query="Kitchen")
    data = json.loads(response)
    assert len(data["result"]) == 1
    assert data["result"][0]["Name"] == "Kitchen Light"
    
    # Search by name (case insensitive)
    response = await search_devices(query="light")
    data = json.loads(response)
    assert len(data["result"]) == 2

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
    await delete_user_variable(name="DeleteMe")

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
            {"idx": "1", "Name": "Heater", "Usage": "2000W", "CounterToday": "5kWh"},
            {"idx": "2", "Name": "Light", "Data": "On"} # No energy data
        ]
    }
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=mock_data)
    )
    
    response = await analyze_energy_usage()
    data = json.loads(response)
    assert len(data["result"]) == 1
    assert data["result"][0]["Name"] == "Heater"

@pytest.mark.asyncio
@respx.mock
async def test_dashboard_resource():
    from domoticz_mcp.server import get_dashboard_resource
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
    assert len(data["result"]) == 2
    names = [d["Name"] for d in data["result"]]
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
    from domoticz_mcp.server import audit_batteries, find_devices_by_state, maintenance_report, energy_audit
    
    assert "battery levels" in audit_batteries(15)
    assert "15%" in audit_batteries(15)
    assert "currently in the 'on' state" in find_devices_by_state("on")
    assert "health report" in maintenance_report()
    assert "energy audit" in energy_audit()

@pytest.mark.asyncio
@respx.mock
async def test_resource_handlers():
    from domoticz_mcp.server import (
        get_log_resource, get_security_resource, get_device_resource, 
        get_rooms_resource, get_events_resource
    )
    
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
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json=DEVICES_MOCK_RESPONSE)
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list").mock(
        return_value=Response(200, json={"result": []})
    )
    await get_events_resource()

@pytest.mark.asyncio
@respx.mock
async def test_error_cases():
    from domoticz_mcp.server import get_device, toggle_switch
    
    # Device not found
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true").mock(
        return_value=Response(200, json={"result": []})
    )
    response = await get_device(name="Missing")
    assert "Device not found" in json.loads(response)["message"]
    
    # Invalid switch state
    from domoticz_mcp.server import set_switch_state
    response = await set_switch_state("Invalid", idx=1)
    assert "state must be 'On' or 'Off'" in json.loads(response)["message"]

@pytest.mark.asyncio
@respx.mock
async def test_missing_params():
    response = await toggle_switch()
    assert json.loads(response)["status"] == "error"
    assert "Must provide either idx or name" in json.loads(response)["message"]

def test_dns_rebinding_protection_disabled():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient
    
    # Create a fresh FastMCP instance with disabled DNS protection just like in server.py
    mcp_test = FastMCP("Test1", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    app = mcp_test.streamable_http_app()
    
    # Positive test: Valid Host header should not be rejected with 421
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/mcp", headers={"Host": "127.0.0.1", "Content-Type": "application/json"})
        assert response.status_code != 421

def test_dns_rebinding_protection_disabled_negative():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient
    
    # Need a fresh instance per TestClient context
    mcp_test = FastMCP("Test2", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    app = mcp_test.streamable_http_app()

    # Negative test (verifying our disable fix works): Invalid Host header should ALSO not be rejected with 421
    with TestClient(app, base_url="http://192.168.100.100") as client:
        response = client.post("/mcp", headers={"Host": "192.168.100.100", "Content-Type": "application/json"})
        assert response.status_code != 421

def test_dns_rebinding_protection_enabled_rejects():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient
    
    # Explicitly enable it to verify the negative case behaves as expected
    strict_mcp = FastMCP("Test3", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=["127.0.0.1:*"]))
    app = strict_mcp.streamable_http_app()
    
    with TestClient(app, base_url="http://192.168.100.100") as client:
        response = client.post("/mcp", headers={"Host": "192.168.100.100", "Content-Type": "application/json"})
        # Should be rejected with 421
        assert response.status_code == 421

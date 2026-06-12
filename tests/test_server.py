import pytest
import respx
from httpx import Response
import json
import asyncio
from datetime import datetime, timedelta
import domoticz_mcp.server as server_module
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
    await delete_user_variable(name="DeleteMe")

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
    response = await switch_scene("Invalid", idx=1)
    assert "Invalid command" in json.loads(response)["message"]
    
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
    assert "Device not found" in json.loads(response)["message"]

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
async def test_get_overview():
    from domoticz_mcp.server import get_overview
    
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
        return_value=Response(200, json={"result": [{"idx": "10", "name": "AutoLight", "interpreter": "python"}]})
    )
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=load&event=10").mock(
        return_value=Response(200, json={"result": [{"source": "if light_level < 50: turn_on()"}]})
    )
    
    response = await search_scripts(query="light_level")
    data = json.loads(response)
    assert len(data["result"]) == 1
    assert data["result"][0]["Name"] == "AutoLight"

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
    resp = _error_response("Test error")
    data = json.loads(resp)
    assert data["status"] == "error"
    assert data["message"] == "Test error"

def test_format_response_helper():
    from domoticz_mcp.server import _format_response
    test_data = {"status": "OK", "result": {"count": 1}}
    resp = _format_response(test_data)
    assert json.loads(resp) == test_data

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
    response = await call_domoticz_api("testparam", {"myarg": "1"})
    assert json.loads(response)["status"] == "OK"
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=checkforupdate").mock(
        return_value=Response(200, json={"status": "OK", "HaveUpdate": False})
    )
    response = await check_for_updates()
    assert json.loads(response)["HaveUpdate"] is False
    respx.get(f"{DOMOTICZ_API_URL}?type=command&param=system_reboot").mock(
        return_value=Response(200, json={"status": "OK"})
    )
    response_fail = await restart_system(confirm=False)
    assert "error" in json.loads(response_fail)["status"]
    response_ok = await restart_system(confirm=True)
    assert json.loads(response_ok)["status"] == "OK"
    respx.get(f"{DOMOTICZ_BASE_URL}/camsnapshot.jpg?idx=1").mock(
        return_value=Response(200, content=b"fakeimage")
    )
    response = await get_camera_snapshot(1)
    assert json.loads(response)["status"] == "OK"

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

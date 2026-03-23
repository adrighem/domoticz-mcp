import pytest
import respx
import httpx
from httpx import Response
import json
import urllib.parse
from domoticz_mcp.server import (
    get_device, toggle_switch, get_room_devices, 
    _resolve_device_idx, create_client, DOMOTICZ_API_URL
)

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

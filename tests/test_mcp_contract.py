import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

import domoticz_mcp.server as server


PROJECT_ROOT = Path(__file__).parents[1]
CACHES = {
    "device": server._device_cache,
    "scene": server._scene_cache,
    "user_variable": server._user_variable_cache,
    "plans": server._plans_cache,
}


@pytest.fixture(autouse=True)
def reset_caches():
    for cache in CACHES.values():
        cache["data"] = None
        cache["timestamp"] = 0
        cache["generation"] = 0


def test_mcp_v1_dependency_is_bounded_everywhere():
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
    server_source = (PROJECT_ROOT / "src/domoticz_mcp/server.py").read_text()

    assert '"mcp>=1.27,<2"' in pyproject
    assert '#     "mcp>=1.27,<2",' in server_source


@pytest.mark.asyncio
async def test_mcp_tools_expose_complete_contract_metadata():
    async with create_connected_server_and_client_session(server.mcp) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}

    assert len(tools) == 27
    for tool in tools.values():
        assert tool.title
        assert tool.outputSchema
        assert tool.outputSchema["type"] == "object"
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is not None
        assert tool.annotations.destructiveHint is not None
        assert tool.annotations.idempotentHint is not None
        assert tool.annotations.openWorldHint is not None
        for parameter in tool.inputSchema.get("properties", {}).values():
            assert parameter.get("description")

    read_only = {
        "search_scripts_tool",
        "search_devices_tool",
        "get_daily_energy_history",
        "get_weekly_energy_history",
        "get_monthly_energy_history",
    }
    assert {name for name, tool in tools.items() if tool.annotations.readOnlyHint} == read_only
    assert tools["restart_system"].annotations.destructiveHint is True
    assert tools["call_domoticz_api"].annotations.destructiveHint is True
    assert tools["delete_device"].annotations.destructiveHint is True
    assert tools["toggle_switch"].annotations.idempotentHint is False
    assert tools["set_switch_state"].annotations.idempotentHint is True
    additive = {
        "add_user_variable",
        "create_event",
        "create_virtual_sensor",
        "add_log_message",
        "send_notification",
    }
    assert {
        name
        for name, tool in tools.items()
        if not tool.annotations.readOnlyHint and not tool.annotations.destructiveHint
    } == additive

    search_schema = tools["search_devices_tool"].inputSchema["properties"]
    assert search_schema["offset"]["minimum"] == 0
    assert search_schema["limit"]["minimum"] == 1
    assert search_schema["limit"]["maximum"] == 100
    assert tools["set_dimmer_level"].inputSchema["properties"]["level"]["maximum"] == 100
    assert "pattern" in tools["set_switch_state"].inputSchema["properties"]["state"]
    assert "pattern" in tools["control_blinds"].inputSchema["properties"]["command"]
    assert "pattern" in tools["switch_scene"].inputSchema["properties"]["command"]
    device_schema = tools["search_devices_tool"].outputSchema["$defs"]["DomoticzItem"]
    assert {"idx", "Name", "Data", "Status", "Type", "SubType"} <= set(
        device_schema["properties"]
    )
    energy_schema = tools["get_daily_energy_history"].outputSchema
    assert "EnergyHistorySummary" in energy_schema["$defs"]
    assert "EnergyHistorySample" in energy_schema["$defs"]


@pytest.mark.asyncio
async def test_json_resources_advertise_json_mime_type():
    async with create_connected_server_and_client_session(server.mcp) as session:
        resources = (await session.list_resources()).resources
        templates = (await session.list_resource_templates()).resourceTemplates

    for resource in resources:
        expected = "text/plain" if "/docs/" in str(resource.uri) else "application/json"
        assert resource.mimeType == expected
        assert resource.name
        assert resource.title
    for template in templates:
        assert template.mimeType == "application/json"
        assert template.name
        assert template.title


@pytest.mark.asyncio
@respx.mock
async def test_protocol_returns_structured_output_and_real_tool_errors():
    respx.get(
        f"{server.DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true"
    ).mock(return_value=httpx.Response(200, json={
        "result": [{"idx": "1", "Name": "Kitchen Light", "Data": "Off"}],
    }))
    respx.get(
        f"{server.DOMOTICZ_API_URL}?type=command&param=switchlight&idx=1&switchcmd=Toggle"
    ).mock(return_value=httpx.Response(200, json={
        "status": "ERR",
        "message": "Switch command was rejected",
    }))
    respx.get(
        f"{server.DOMOTICZ_API_URL}?type=command&param=switchlight&idx=1&switchcmd=On"
    ).mock(return_value=httpx.Response(200, json={"status": "OK"}))
    respx.get(
        f"{server.DOMOTICZ_API_URL}?type=command&param=switchlight&idx=1&switchcmd=Open"
    ).mock(return_value=httpx.Response(200, json={"status": "OK"}))
    respx.get(
        f"{server.DOMOTICZ_API_URL}?type=command&param=switchscene&idx=1&switchcmd=Toggle"
    ).mock(return_value=httpx.Response(200, json={"status": "OK"}))

    async with create_connected_server_and_client_session(server.mcp) as session:
        search_result = await session.call_tool(
            "search_devices_tool",
            {"query": "Kitchen", "offset": 0, "limit": 10},
        )
        confirmation_error = await session.call_tool(
            "restart_system",
            {"confirm": False},
        )
        upstream_error = await session.call_tool(
            "toggle_switch",
            {"idx": 1},
        )
        mixed_case_state = await session.call_tool(
            "set_switch_state",
            {"state": "oN", "idx": 1},
        )
        mixed_case_blinds = await session.call_tool(
            "control_blinds",
            {"command": "oPeN", "idx": 1},
        )
        mixed_case_scene = await session.call_tool(
            "switch_scene",
            {"command": "tOgGlE", "idx": 1},
        )

    assert search_result.isError is False
    assert search_result.structuredContent["status"] == "OK"
    assert isinstance(search_result.structuredContent["result"], list)
    assert search_result.structuredContent["count"] == 1
    assert json.loads(search_result.content[0].text) == search_result.structuredContent

    assert confirmation_error.isError is True
    assert "Confirmation required" in confirmation_error.content[0].text
    assert upstream_error.isError is True
    assert "Switch command was rejected" in upstream_error.content[0].text
    assert mixed_case_state.isError is False
    assert mixed_case_blinds.isError is False
    assert mixed_case_scene.isError is False


@pytest.mark.asyncio
@respx.mock
async def test_search_scripts_supports_legacy_idx_and_source():
    respx.get(
        f"{server.DOMOTICZ_API_URL}?type=command&param=events&evparam=list"
    ).mock(return_value=httpx.Response(200, json={
        "result": [{"idx": "12", "name": "LegacyEvent"}],
    }))
    respx.get(
        f"{server.DOMOTICZ_API_URL}?type=command&param=events&evparam=load&event=12"
    ).mock(return_value=httpx.Response(200, json={
        "result": [{"source": "legacy_search_term"}],
    }))

    result = json.loads(await server.search_scripts("legacy_search_term"))

    assert result["result"][0]["id"] == "12"
    assert result["result"][0]["idx"] == "12"


@pytest.mark.asyncio
@respx.mock
async def test_search_scripts_rejects_events_without_an_identifier():
    route = respx.get(
        f"{server.DOMOTICZ_API_URL}?type=command&param=events&evparam=list"
    ).mock(return_value=httpx.Response(200, json={
        "result": [{"name": "BrokenEvent"}],
    }))

    with pytest.raises(ToolError, match="without an id"):
        await server.search_scripts("anything")

    assert route.calls.call_count == 1
    assert len(respx.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_collection_resources_do_not_silently_truncate_results():
    devices = [{"idx": str(idx), "Name": f"Device {idx}"} for idx in range(1005)]
    events = [{"id": str(idx), "name": f"Event {idx}"} for idx in range(75)]
    respx.get(
        f"{server.DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true&order=Name"
    ).mock(return_value=httpx.Response(200, json={"result": devices}))
    respx.get(
        f"{server.DOMOTICZ_API_URL}?type=command&param=events&evparam=list"
    ).mock(return_value=httpx.Response(200, json={"result": events}))

    device_result = json.loads(await server.get_all_devices_resource())
    event_result = json.loads(await server.get_events_resource())

    assert device_result["total_count"] == 1005
    assert device_result["count"] == 1005
    assert len(device_result["result"]) == 1005
    assert event_result["total_count"] == 75
    assert event_result["count"] == 75
    assert len(event_result["result"]) == 75


MUTATION_CASES = [
    (server.toggle_switch, {"idx": 1}, {"device", "scene", "user_variable"}),
    (server.set_switch_state, {"state": "On", "idx": 1}, {"device", "scene", "user_variable"}),
    (server.set_dimmer_level, {"level": 50, "idx": 1}, {"device", "scene", "user_variable"}),
    (server.set_temperature_setpoint, {"setpoint": 20.0, "idx": 1}, {"device", "scene", "user_variable"}),
    (server.control_blinds, {"command": "Open", "idx": 1}, {"device", "scene", "user_variable"}),
    (server.switch_scene, {"command": "On", "idx": 1}, {"device", "scene", "user_variable"}),
    (server.add_user_variable, {"name": "Var", "vtype": 0, "value": "1"}, {"user_variable"}),
    (server.update_user_variable, {"name": "Var", "vtype": 0, "value": "2"}, {"device", "scene", "user_variable"}),
    (server.delete_user_variable, {"idx": 1, "confirm": True}, {"user_variable"}),
    (server.call_domoticz_api, {"param": "custom", "kwargs": {}, "confirm": True}, set(CACHES)),
    (server.restart_system, {"confirm": True}, set(CACHES)),
    (server.rename_device, {"new_name": "New", "idx": 1}, {"device"}),
    (server.delete_device, {"idx": 1, "confirm": True}, {"device"}),
    (server.create_virtual_sensor, {"hw_idx": 1, "sensorname": "Virtual", "sensortype": 1}, {"device"}),
    (server.update_device_value, {"idx": 1}, {"device", "scene", "user_variable"}),
    (server.set_security_status, {"secstatus": 1, "seccode": "1234", "confirm": True}, {"device", "scene", "user_variable"}),
    (server.set_color_brightness, {"hue": 120, "brightness": 50, "idx": 1}, {"device", "scene", "user_variable"}),
    (server.set_color_temperature, {"kelvin": 50, "idx": 1}, {"device", "scene", "user_variable"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("mutation", "kwargs", "invalidated"), MUTATION_CASES)
async def test_successful_mutations_invalidate_affected_caches(
    monkeypatch,
    mutation,
    kwargs,
    invalidated,
):
    async def successful_request(*args, **request_kwargs):
        return httpx.Response(200, json={"status": "OK"})

    monkeypatch.setattr(server, "_do_request", successful_request)
    for cache in CACHES.values():
        cache["data"] = [{"stale": True}]
        cache["timestamp"] = 123
        cache["generation"] = 7

    result = await mutation(**kwargs)

    assert result.status == "OK"
    for name, cache in CACHES.items():
        if name in invalidated:
            assert cache["data"] is None
            assert cache["timestamp"] == 0
            assert cache["generation"] == 8
        else:
            assert cache["data"] == [{"stale": True}]
            assert cache["timestamp"] == 123
            assert cache["generation"] == 7


@pytest.mark.asyncio
async def test_rejected_mutation_still_invalidates_affected_caches(monkeypatch):
    async def failed_request(*args, **kwargs):
        return httpx.Response(200, json={"status": "ERR", "message": "Rejected"})

    monkeypatch.setattr(server, "_do_request", failed_request)
    for cache in CACHES.values():
        cache["data"] = [{"current": True}]
        cache["timestamp"] = 123
        cache["generation"] = 7

    with pytest.raises(ToolError, match="Rejected"):
        await server.toggle_switch(idx=1)

    for name, cache in CACHES.items():
        if name in {"device", "scene", "user_variable"}:
            assert cache["data"] is None
            assert cache["timestamp"] == 0
            assert cache["generation"] == 8
        else:
            assert cache["data"] == [{"current": True}]
            assert cache["timestamp"] == 123
            assert cache["generation"] == 7


@pytest.mark.asyncio
async def test_ambiguous_mutation_failure_invalidates_affected_caches(monkeypatch):
    async def interrupted_request(*args, **kwargs):
        raise httpx.ReadTimeout("request outcome is unknown")

    monkeypatch.setattr(server, "_do_request", interrupted_request)
    for cache in CACHES.values():
        cache["data"] = [{"current": True}]
        cache["timestamp"] = 123
        cache["generation"] = 7

    with pytest.raises(httpx.ReadTimeout):
        await server.toggle_switch(idx=1)

    for name, cache in CACHES.items():
        if name in {"device", "scene", "user_variable"}:
            assert cache["data"] is None
            assert cache["timestamp"] == 0
            assert cache["generation"] == 8
        else:
            assert cache["data"] == [{"current": True}]
            assert cache["timestamp"] == 123
            assert cache["generation"] == 7


@pytest.mark.asyncio
async def test_inflight_cache_fill_cannot_restore_pre_mutation_data(monkeypatch):
    local_cache = {"data": None, "timestamp": 0, "generation": 0}
    first_request_started = asyncio.Event()
    release_first_request = asyncio.Event()
    request_count = 0

    async def controlled_request(*args, **kwargs):
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            first_request_started.set()
            await release_first_request.wait()
            return httpx.Response(200, json={"result": [{"value": "stale"}]})
        return httpx.Response(200, json={"result": [{"value": "fresh"}]})

    monkeypatch.setattr(server, "_do_request", controlled_request)
    read_task = asyncio.create_task(
        server._get_cached_data(object(), local_cache, "http://domoticz.invalid/data")
    )
    await asyncio.wait_for(first_request_started.wait(), timeout=1)

    server._invalidate_caches(local_cache)
    release_first_request.set()
    result = await asyncio.wait_for(read_task, timeout=1)

    assert request_count == 2
    assert result == [{"value": "fresh"}]
    assert local_cache["data"] == [{"value": "fresh"}]

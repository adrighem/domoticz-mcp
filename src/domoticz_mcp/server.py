# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp",
#     "httpx",
#     "python-dotenv",
# ]
# ///

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from dotenv import load_dotenv
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
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, TYPE_CHECKING

# Load environment variables from a .env file if it exists
load_dotenv()


class DomoticzError(Exception):
    """Base exception for Domoticz MCP errors."""
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class DeviceNotFoundError(DomoticzError):
    def __init__(self, identifier: str):
        super().__init__(f"Device not found: {identifier}", 404)
        self.identifier = identifier


class SceneNotFoundError(DomoticzError):
    def __init__(self, identifier: str):
        super().__init__(f"Scene not found: {identifier}", 404)
        self.identifier = identifier


class UserVariableNotFoundError(DomoticzError):
    def __init__(self, identifier: str):
        super().__init__(f"User variable not found: {identifier}", 404)
        self.identifier = identifier


class AuthenticationError(DomoticzError):
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, 401)


class InvalidParameterError(DomoticzError):
    def __init__(self, message: str):
        super().__init__(message, 400)
        self.message = message


mcp = FastMCP("Domoticz", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False), stateless_http=True)

# Configuration defaults
DOMOTICZ_BASE_URL = "http://127.0.0.1:8080"
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


def _format_response(data: Dict[str, Any]) -> str:
    """Format a dictionary as a JSON string response."""
    return json.dumps(data)


def _error_response(message: str, status: str = "error") -> str:
    """Format an error response as a JSON string."""
    return json.dumps({"status": status, "message": message})


def _confirmation_required(action: str) -> str:
    """Format a confirmation-required error response."""
    return _error_response(f"Confirmation required for {action}. Retry with confirm=True.")


def _command_url(param: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Build a Domoticz command URL with encoded query parameters."""
    query: Dict[str, Any] = {"type": "command", "param": param}
    if params:
        query.update(params)
    return f"{DOMOTICZ_API_URL}?{urllib.parse.urlencode(query, quote_via=urllib.parse.quote)}"


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

    sys.stderr.write("\n==========================================================\n")
    sys.stderr.write("Authentication required for Domoticz MCP Server.\n")
    sys.stderr.write("Please open this URL in your browser to authenticate:\n\n")
    sys.stderr.write(f"{auth_url}\n\n")
    sys.stderr.write(f"Waiting for authentication on {redirect_uri}...\n")
    sys.stderr.write("==========================================================\n\n")
    sys.stderr.flush()

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    server.serve_forever()

    if code and state_received == state:
        return code, code_verifier, redirect_uri
    return None, None, None

_token_refresh_lock = asyncio.Lock()

async def _fetch_oauth_token(force_refresh: bool = False, old_token: Optional[str] = None) -> Optional[str]:
    global _oauth_token_cache

    if not force_refresh and _oauth_token_cache:
        return _oauth_token_cache

    async with _token_refresh_lock:
        # Check if another task already refreshed the token while we were waiting
        if force_refresh and old_token is not None and _oauth_token_cache != old_token:
            return _oauth_token_cache

        if not force_refresh and _oauth_token_cache:
            return _oauth_token_cache

        # Try loading from file
        existing_token_data = None
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, 'r') as f:
                    existing_token_data = json.load(f)
                    if not force_refresh and existing_token_data and "access_token" in existing_token_data:
                        _oauth_token_cache = existing_token_data["access_token"]
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
                if token_endpoint:
                    if "127.0.0.1" in token_endpoint or "localhost" in token_endpoint:
                        parsed = urllib.parse.urlparse(token_endpoint)
                        token_endpoint = f"{DOMOTICZ_BASE_URL}{parsed.path}"
                else:
                    return None

                auth = (DOMOTICZ_CLIENT_ID, DOMOTICZ_CLIENT_SECRET) if DOMOTICZ_CLIENT_SECRET else None
                token_resp = None

                # Try refresh token first
                if force_refresh and existing_token_data and "refresh_token" in existing_token_data:
                    data = {
                        "grant_type": "refresh_token",
                        "refresh_token": existing_token_data["refresh_token"],
                        "client_id": DOMOTICZ_CLIENT_ID
                    }
                    if DOMOTICZ_CLIENT_SECRET:
                        data["client_secret"] = DOMOTICZ_CLIENT_SECRET

                    try:
                        refresh_resp = await client.post(
                            token_endpoint,
                            auth=auth,
                            data=data
                        )
                        if refresh_resp.status_code == 200:
                            token_resp = refresh_resp
                        else:
                            sys.stderr.write(f"Failed to refresh token (Status: {refresh_resp.status_code}), re-authenticating...\n")
                    except Exception as e:
                        sys.stderr.write(f"Exception during token refresh: {e}\n")

                # If no token_resp yet, perform initial flow
                if token_resp is None:
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

                        token_resp = await client.post(
                            token_endpoint,
                            auth=auth,
                            data=data
                        )

                token_resp.raise_for_status()
                token_data = token_resp.json()

                # Persist the old refresh token if the new response doesn't provide one
                if "refresh_token" not in token_data and existing_token_data and "refresh_token" in existing_token_data:
                    token_data["refresh_token"] = existing_token_data["refresh_token"]

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

async def _do_request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """Perform a request with a single retry on 401 Unauthorized to handle expired tokens."""
    global _oauth_token_cache

    used_token = _oauth_token_cache

    try:
        resp = await client.request(method, url, **kwargs)
        if resp.status_code == 401:
            # Token might be expired. Re-fetch token (this will trigger OAuth flow if needed)
            new_token = await _fetch_oauth_token(force_refresh=True, old_token=used_token)
            if new_token:
                client.headers["Authorization"] = f"Bearer {new_token}"
                client.auth = None

                if "headers" not in kwargs:
                    kwargs["headers"] = {}
                kwargs["headers"]["Authorization"] = f"Bearer {new_token}"

                # Retry the request
                resp = await client.request(method, url, **kwargs)

        resp.raise_for_status()
        return resp
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise Exception("Authentication failed. Please check your credentials or re-authenticate.")
        raise e

# Custom AsyncClient wrapper that ensures the token is added
class DomoticzClient:
    def __init__(self, own_client: bool = False):
        self._own_client = own_client
        if own_client or _global_http_client is None:
            self.client: httpx.AsyncClient = httpx.AsyncClient(timeout=30.0)
            self._owns_client = True
        else:
            self.client = _global_http_client
            self._owns_client = False

    async def __aenter__(self) -> "httpx.AsyncClient":
        oauth_token = DOMOTICZ_OAUTH_TOKEN or _oauth_token_cache

        if DOMOTICZ_CLIENT_ID and not oauth_token:
            oauth_token = await _fetch_oauth_token()

        if oauth_token:
            self.client.headers["Authorization"] = f"Bearer {oauth_token}"
            self.client.auth = None
        elif DOMOTICZ_USERNAME and DOMOTICZ_PASSWORD:
            self.client.headers.pop("Authorization", None)
            self.client.auth = (DOMOTICZ_USERNAME, DOMOTICZ_PASSWORD)
        else:
            self.client.headers.pop("Authorization", None)
            self.client.auth = None

        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._owns_client:
            await self.client.aclose()


def create_client(own_client: bool = False) -> DomoticzClient:
    """Create a DomoticzClient instance."""
    return DomoticzClient(own_client=own_client)


async def close_global_client() -> None:
    """Close the global HTTP client."""
    global _global_http_client
    if _global_http_client is not None:
        await _global_http_client.aclose()
        _global_http_client = None

async def _get_cached_data(client: "httpx.AsyncClient", cache_obj: Dict[str, Any], api_url: str, key_path: str = "result") -> List[Dict[str, Any]]:
    now = time.time()
    if cache_obj["data"] is None or (now - cache_obj["timestamp"]) > CACHE_TTL:
        response = await _do_request(client, "GET", api_url)
        cache_obj["data"] = response.json().get(key_path, [])
        cache_obj["timestamp"] = now
    return cache_obj["data"]

async def _resolve_idx(
    client: "httpx.AsyncClient",
    idx: Optional[int],
    name: Optional[str],
    cache: Dict[str, Any],
    api_url: str
) -> Optional[int]:
    """Resolve an entity to its idx."""
    if idx is not None:
        return idx
    if not name:
        return None
    items = await _get_cached_data(client, cache, api_url)
    for item in items:
        if item.get("Name", "").lower() == name.lower():
            return int(str(item.get("idx")))
    return None


async def _resolve_device_idx(client: "httpx.AsyncClient", idx: Optional[int] = None, name: Optional[str] = None) -> Optional[int]:
    """Resolve a device to its idx."""
    return await _resolve_idx(client, idx, name, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")


async def _resolve_scene_idx(client: "httpx.AsyncClient", idx: Optional[int] = None, name: Optional[str] = None) -> Optional[int]:
    """Resolve a scene to its idx."""
    return await _resolve_idx(client, idx, name, _scene_cache, f"{DOMOTICZ_API_URL}?type=command&param=getscenes")


async def _resolve_user_variable_idx(client: "httpx.AsyncClient", idx: Optional[int] = None, name: Optional[str] = None) -> Optional[int]:
    """Resolve a user variable to its idx."""
    return await _resolve_idx(client, idx, name, _user_variable_cache, f"{DOMOTICZ_API_URL}?type=command&param=getuservariables")

def _simplify_device(dev: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce device dictionary to essential fields."""
    keys_to_keep = [
        "idx", "Name", "Type", "SubType", "Data", "Status",
        "BatteryLevel", "Favorite", "HardwareName", "LastUpdate",
        "TypeImg", "Usage", "CounterToday", "Temp", "Humidity"
    ]
    return {k: dev[k] for k in keys_to_keep if k in dev}

def _paginate(data: list, offset: int, limit: int) -> list:
    """Paginate a list of results."""
    return data[offset:offset + limit]


def _parse_number(value: Any) -> Optional[float]:
    """Parse the first numeric value from a Domoticz value string."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", value.replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _parse_power_w(value: Any) -> Optional[float]:
    number = _parse_number(value)
    if number is None:
        return None
    text = str(value).lower()
    if "mw" in text and "mwh" not in text:
        return number * 1_000_000
    if "kw" in text and "kwh" not in text:
        return number * 1000
    return number


def _parse_energy_kwh(value: Any, assume_energy: bool = True) -> Optional[float]:
    number = _parse_number(value)
    if number is None:
        return None
    text = str(value).lower()
    if "mwh" in text:
        return number * 1000
    if "kwh" in text:
        return number
    if "wh" in text:
        return number / 1000
    if "m3" in text or "m³" in text or "liter" in text or "litre" in text:
        return None
    return number if assume_energy else None


def _parse_volume_m3(value: Any, assume_volume: bool = True) -> Optional[float]:
    number = _parse_number(value)
    if number is None:
        return None
    text = str(value).lower().strip()
    if "m3" in text or "m³" in text:
        return number
    if "liter" in text or "litre" in text or re.search(r"\d\s*l$", text):
        return number / 1000
    return number if assume_volume else None


def _classify_energy_device(dev: Dict[str, Any]) -> str:
    text = " ".join(
        str(dev.get(field, ""))
        for field in ["Name", "Type", "SubType", "SwitchType", "TypeImg", "HardwareName"]
    ).lower()
    if "water" in text:
        return "water"
    if "gas" in text:
        return "gas"
    if any(term in text for term in ["solar", "pv", "inverter", "generation", "generated"]):
        return "electricity_generation"
    if any(term in text for term in ["battery", "powerwall", "storage", "accu"]):
        return "storage"
    return "electricity"


def _energy_device_entry(dev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    category = _classify_energy_device(dev)
    current_power_w = _parse_power_w(dev.get("Usage"))
    delivered_power_w = _parse_power_w(dev.get("UsageDeliv"))
    today_kwh = None
    today_m3 = None

    if category in ["gas", "water"]:
        today_m3 = _parse_volume_m3(dev.get("CounterToday"))
    else:
        today_kwh = _parse_energy_kwh(dev.get("CounterToday"))

    delivered_today_kwh = _parse_energy_kwh(dev.get("CounterDelivToday"), assume_energy=False)
    total_kwh = _parse_energy_kwh(dev.get("Counter"), assume_energy=False)
    delivered_total_kwh = _parse_energy_kwh(dev.get("CounterDeliv"), assume_energy=False)

    if all(value is None for value in [current_power_w, delivered_power_w, today_kwh, today_m3, delivered_today_kwh, total_kwh, delivered_total_kwh]):
        return None

    entry: Dict[str, Any] = {
        "idx": dev.get("idx"),
        "Name": dev.get("Name"),
        "category": category,
        "Usage": dev.get("Usage"),
        "TodayTotal": dev.get("CounterToday"),
        "current_power_w": current_power_w,
        "delivered_power_w": delivered_power_w,
        "today_kwh": today_kwh,
        "today_m3": today_m3,
        "delivered_today_kwh": delivered_today_kwh,
        "total_kwh": total_kwh,
        "delivered_total_kwh": delivered_total_kwh,
    }
    return {key: value for key, value in entry.items() if value is not None}


def _summarize_energy(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {
        "devices_with_energy_data": len(entries),
        "current": {
            "electricity_consumption_w": 0.0,
            "electricity_generation_w": 0.0,
            "electricity_export_w": 0.0,
            "storage_w": 0.0,
            "net_electricity_w": 0.0,
        },
        "today": {
            "electricity_consumption_kwh": 0.0,
            "electricity_generation_kwh": 0.0,
            "electricity_export_kwh": 0.0,
            "gas_m3": 0.0,
            "water_m3": 0.0,
        },
        "top_current_consumers": [],
        "top_daily_consumers": [],
    }

    for entry in entries:
        category = entry.get("category")
        current_power_w = float(entry.get("current_power_w", 0.0))
        delivered_power_w = float(entry.get("delivered_power_w", 0.0))
        today_kwh = float(entry.get("today_kwh", 0.0))
        delivered_today_kwh = float(entry.get("delivered_today_kwh", 0.0))
        today_m3 = float(entry.get("today_m3", 0.0))

        if category == "electricity_generation":
            summary["current"]["electricity_generation_w"] += current_power_w
            summary["today"]["electricity_generation_kwh"] += today_kwh
        elif category == "storage":
            summary["current"]["storage_w"] += current_power_w
            summary["today"]["electricity_consumption_kwh"] += today_kwh
        elif category == "gas":
            summary["today"]["gas_m3"] += today_m3
        elif category == "water":
            summary["today"]["water_m3"] += today_m3
        else:
            summary["current"]["electricity_consumption_w"] += current_power_w
            summary["today"]["electricity_consumption_kwh"] += today_kwh

        summary["current"]["electricity_export_w"] += delivered_power_w
        summary["today"]["electricity_export_kwh"] += delivered_today_kwh

    summary["current"]["net_electricity_w"] = (
        summary["current"]["electricity_consumption_w"]
        - summary["current"]["electricity_generation_w"]
        - summary["current"]["electricity_export_w"]
    )

    def ranked_projection(entry: Dict[str, Any], value_key: str) -> Dict[str, Any]:
        return {
            "idx": entry.get("idx"),
            "Name": entry.get("Name"),
            value_key: entry.get(value_key),
            "category": entry.get("category"),
        }

    current_consumers = [
        entry for entry in entries
        if entry.get("category") in ["electricity", "storage"] and entry.get("current_power_w") is not None
    ]
    daily_consumers = [
        entry for entry in entries
        if entry.get("category") in ["electricity", "storage"] and entry.get("today_kwh") is not None
    ]
    summary["top_current_consumers"] = [
        ranked_projection(entry, "current_power_w")
        for entry in sorted(current_consumers, key=lambda item: item.get("current_power_w", 0), reverse=True)[:5]
    ]
    summary["top_daily_consumers"] = [
        ranked_projection(entry, "today_kwh")
        for entry in sorted(daily_consumers, key=lambda item: item.get("today_kwh", 0), reverse=True)[:5]
    ]

    return summary


def _energy_history_range(period: str) -> str:
    if period == "day":
        return "day"
    if period == "month":
        return "month"
    if period == "week":
        today = datetime.now().date()
        start = today - timedelta(days=6)
        return f"{start.isoformat()}T{today.isoformat()}"
    raise InvalidParameterError("period must be 'day', 'week', or 'month'")


def _normalize_energy_history_row(row: Dict[str, Any], category: str) -> Dict[str, Any]:
    normalized = dict(row)
    numeric_fields = {}

    for key, value in row.items():
        if key == "d":
            continue
        number = _parse_number(value)
        if number is not None:
            numeric_fields[key] = number

    if numeric_fields:
        normalized["numeric"] = numeric_fields

    for key in ["u", "usage", "Usage"]:
        if key in row:
            power_w = _parse_power_w(row[key])
            if power_w is not None:
                normalized["power_w"] = power_w
                break

    for key in ["v", "value", "counter", "c", "Counter"]:
        if key not in row:
            continue
        if category in ["gas", "water"]:
            volume_m3 = _parse_volume_m3(row[key])
            if volume_m3 is not None:
                normalized["volume_m3"] = volume_m3
                break
        else:
            energy_kwh = _parse_energy_kwh(row[key])
            if energy_kwh is not None:
                normalized["energy_kwh"] = energy_kwh
                break

    return normalized


def _summarize_energy_history(records: List[Dict[str, Any]], category: str) -> Dict[str, Any]:
    power_values = [record["power_w"] for record in records if "power_w" in record]
    energy_values = [record["energy_kwh"] for record in records if "energy_kwh" in record]
    volume_values = [record["volume_m3"] for record in records if "volume_m3" in record]

    summary: Dict[str, Any] = {"samples": len(records), "category": category}

    if power_values:
        summary["power"] = {
            "min_w": min(power_values),
            "max_w": max(power_values),
            "avg_w": sum(power_values) / len(power_values),
        }
    if energy_values:
        summary["energy"] = {
            "total_kwh": sum(energy_values),
            "min_kwh": min(energy_values),
            "max_kwh": max(energy_values),
        }
    if volume_values:
        summary["volume"] = {
            "total_m3": sum(volume_values),
            "min_m3": min(volume_values),
            "max_m3": max(volume_values),
        }

    return summary


async def _get_energy_history(
    period: str,
    idx: int | None = None,
    name: str | None = None,
    include_instantaneous: bool = False
) -> str:
    """Internal: Get counter graph history for energy, gas, and water meters."""
    range_param = _energy_history_range(period)
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx:
            return _error_response("Device not found")

        device_name = name
        category = "electricity"
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        for device in devices:
            if str(device.get("idx")) == str(resolved_idx):
                device_name = device.get("Name") or device_name
                category = _classify_energy_device(device)
                break

        params: Dict[str, Any] = {"sensor": "counter", "idx": resolved_idx, "range": range_param}
        if period == "day" and include_instantaneous:
            params["method"] = 1

        response = await _do_request(client, "GET", _command_url("graph", params))
        data = response.json()
        records = [
            _normalize_energy_history_row(row, category)
            for row in data.get("result", [])
            if isinstance(row, dict)
        ]
        return json.dumps({
            "status": data.get("status", "OK"),
            "title": data.get("title"),
            "period": period,
            "range": range_param,
            "idx": resolved_idx,
            "Name": device_name,
            "include_instantaneous": include_instantaneous if period == "day" else False,
            "summary": _summarize_energy_history(records, category),
            "result": records,
        })

# --- Core Functions (Used by Resources and Tests) ---

async def get_overview(detail_level: str = "minimal") -> str:
    """Internal: High-level overview."""
    async with create_client() as client:
        resp = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getversion")
        sys_info = resp.json()
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        scenes = await _get_cached_data(client, _scene_cache, f"{DOMOTICZ_API_URL}?type=command&param=getscenes")
        vars = await _get_cached_data(client, _user_variable_cache, f"{DOMOTICZ_API_URL}?type=command&param=getuservariables")
        plans = await _get_cached_data(client, _plans_cache, f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true")
        hw_resp = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=gethardware")
        hardware = hw_resp.json().get("result", [])
        overview = {
            "system": {"version": sys_info.get("version"), "build_time": sys_info.get("build_time"), "domoticz_url": DOMOTICZ_BASE_URL},
            "counts": {"devices": len(devices), "scenes_and_groups": len(scenes), "user_variables": len(vars), "rooms_plans": len(plans), "hardware_gateways": len(hardware)}
        }
        if detail_level != "minimal":
            favorites = [d for d in devices if d.get("Favorite") == 1][:10]
            overview["favorite_devices"] = [_simplify_device(d) for d in favorites]
        return json.dumps({"status": "OK", "result": overview})

async def get_system_health() -> str:
    """Internal: System health report."""
    async with create_client() as client:
        hw_resp = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=gethardware")
        hardware = hw_resp.json().get("result", [])
        health_report = [{"Name": hw.get("Name"), "Type": hw.get("Type"), "Status": "Online" if hw.get("Enabled") == "true" else "Disabled"} for hw in hardware]
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        now = datetime.now()
        unresponsive_count = 0
        for dev in devices:
            if dev.get("LastUpdate"):
                try:
                    if (now - datetime.strptime(dev["LastUpdate"], "%Y-%m-%d %H:%M:%S")) > timedelta(hours=24):
                        unresponsive_count += 1
                except ValueError: pass
        return json.dumps({"status": "OK", "result": {"hardware_health": health_report, "unresponsive_devices_count": unresponsive_count}})

async def get_all_devices(offset: int = 0, limit: int = 50) -> str:
    """Internal: Get all devices."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true&order=Name")
        return json.dumps({"status": "OK", "result": _paginate(devices, offset, limit), "total_count": len(devices)})

async def get_device(idx: int | None = None, name: str | None = None) -> str:
    """Internal: Get specific device."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid={resolved_idx}")
        return response.text

async def get_scenes() -> str:
    """Internal: Get all scenes."""
    async with create_client() as client:
        scenes = await _get_cached_data(client, _scene_cache, f"{DOMOTICZ_API_URL}?type=command&param=getscenes")
        return json.dumps({"status": "OK", "result": scenes})


async def get_scene(idx: int | None = None, name: str | None = None) -> str:
    """Internal: Get a specific scene or group from the scene list."""
    async with create_client() as client:
        resolved_idx = await _resolve_scene_idx(client, idx, name)
        if not resolved_idx:
            return _error_response("Scene not found")
        scenes = await _get_cached_data(client, _scene_cache, f"{DOMOTICZ_API_URL}?type=command&param=getscenes")
        for scene in scenes:
            if str(scene.get("idx")) == str(resolved_idx):
                return json.dumps({"status": "OK", "result": scene})
        return _error_response("Scene not found")


async def get_rooms() -> str:
    """Internal: Get all rooms."""
    async with create_client() as client:
        plans = await _get_cached_data(client, _plans_cache, f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true")
        return json.dumps({"status": "OK", "result": plans})

async def get_room_devices(idx: int | None = None, room_name: str | None = None) -> str:
    """Internal: Get devices in room."""
    async with create_client() as client:
        if idx is None:
            plans = await _get_cached_data(client, _plans_cache, f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true")
            for plan in plans:
                if plan.get("Name", "").lower() == str(room_name).lower():
                    idx = plan.get("idx")
                    break
        if idx is None: return _error_response("Room not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getdevices&plan={idx}")
        data = response.json()
        if "result" in data: data["result"] = [_simplify_device(d) for d in data["result"]]
        return json.dumps(data)

async def get_user_variables() -> str:
    """Internal: Get all user variables."""
    async with create_client() as client:
        vars = await _get_cached_data(client, _user_variable_cache, f"{DOMOTICZ_API_URL}?type=command&param=getuservariables")
        return json.dumps({"status": "OK", "result": vars})


async def get_user_variable(idx: int | None = None, name: str | None = None) -> str:
    """Internal: Get a specific user variable from the variable list."""
    async with create_client() as client:
        resolved_idx = await _resolve_user_variable_idx(client, idx, name)
        if not resolved_idx:
            return _error_response("User variable not found")
        variables = await _get_cached_data(client, _user_variable_cache, f"{DOMOTICZ_API_URL}?type=command&param=getuservariables")
        for variable in variables:
            if str(variable.get("idx")) == str(resolved_idx):
                return json.dumps({"status": "OK", "result": variable})
        return _error_response("User variable not found")


async def get_battery_levels(threshold: int = 20) -> str:
    """Internal: Get low battery levels."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        results = [{"idx": d.get("idx"), "Name": d.get("Name"), "BatteryLevel": d.get("BatteryLevel")} for d in devices if d.get("BatteryLevel", 255) <= threshold]
        results.sort(key=lambda x: x["BatteryLevel"])
        return json.dumps({"status": "OK", "result": results})

async def get_events(offset: int = 0, limit: int = 50) -> str:
    """Internal: Get event overview."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list")
        events = response.json().get("result", [])
        return json.dumps({"status": "OK", "result": _paginate(events, offset, limit), "total_count": len(events)})

async def get_event(event_id: int) -> str:
    """Internal: Get specific event."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=load&event={event_id}")
        return response.text

async def check_for_updates() -> str:
    """Internal: Check for updates."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=checkforupdate")
        return response.text

async def get_camera_snapshot(idx: int) -> str:
    """Internal: Get camera snapshot."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_BASE_URL}/camsnapshot.jpg?idx={idx}")
        return json.dumps({"status": "OK", "result": base64.b64encode(response.content).decode("utf-8")})

async def get_hardware() -> str:
    """Internal: Get hardware gateways."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=gethardware")
        return response.text

async def get_settings() -> str:
    """Internal: Get settings."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getsettings")
        return response.text

async def get_connectivity_report(hours: int = 24) -> str:
    """Internal: Get unresponsive devices."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        threshold = datetime.now() - timedelta(hours=hours)
        results = []
        for dev in devices:
            if dev.get("LastUpdate"):
                try:
                    if datetime.strptime(dev["LastUpdate"], "%Y-%m-%d %H:%M:%S") < threshold:
                        results.append({"idx": dev.get("idx"), "Name": dev.get("Name"), "LastUpdate": dev["LastUpdate"]})
                except ValueError: pass
        results.sort(key=lambda x: x["LastUpdate"])
        return json.dumps({"status": "OK", "result": results})

async def analyze_energy_usage() -> str:
    """Internal: Analyze energy usage."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        results = [entry for entry in (_energy_device_entry(d) for d in devices) if entry is not None]
        results.sort(key=lambda item: item.get("current_power_w", 0), reverse=True)
        return json.dumps({"status": "OK", "summary": _summarize_energy(results), "result": results})

async def search_scripts(query: str) -> str:
    """Internal: Search scripts."""
    async with create_client() as client:
        list_resp = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list")
        scripts = list_resp.json().get("result", [])
        matches = []
        for script in scripts:
            load_resp = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=load&event={script.get('idx')}")
            script_data = load_resp.json().get("result", [{}])[0]
            if query.lower() in (script_data.get("xml", "") or script_data.get("source", "")).lower():
                matches.append({"idx": script.get("idx"), "Name": script.get("name")})
        return json.dumps({"status": "OK", "result": matches})

async def search_devices(query: str, offset: int = 0, limit: int = 50) -> str:
    """Internal: Search devices."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        results = [d for d in devices if query.lower() in d.get("Name", "").lower() or query.lower() in d.get("Data", "").lower()]
        return json.dumps({"status": "OK", "result": _paginate(results, offset, limit), "total_count": len(results)})

# --- Resources ---

@mcp.resource("domoticz://overview")
async def get_overview_resource() -> str:
    """Orientation: system version, hardware, and device counts."""
    return await get_overview()

@mcp.resource("domoticz://health")
async def get_system_health_resource() -> str:
    """Troubleshooting: hardware health and unresponsive devices."""
    return await get_system_health()

@mcp.resource("domoticz://devices")
async def get_all_devices_resource() -> str:
    """Large output: state of all Domoticz devices."""
    data = json.loads(await get_all_devices(limit=1000))
    return json.dumps([_simplify_device(d) for d in data.get("result", [])])

@mcp.resource("domoticz://device/{idx}")
async def get_device_resource(idx: int) -> str:
    """Specific: metadata and current state for one device."""
    return await get_device(idx=idx)

@mcp.resource("domoticz://device/{type}/{subtype}/{idx}")
async def get_device_by_type_resource(type: str, subtype: str, idx: int) -> str:
    """Alias: typed device URI; type/subtype are descriptive, idx is authoritative."""
    return await get_device(idx=idx)

@mcp.resource("domoticz://device/name/{name}")
async def get_device_by_name_resource(name: str) -> str:
    """Specific: metadata and current state for one named device."""
    return await get_device(name=name)

@mcp.resource("domoticz://scenes")
async def get_scenes_resource() -> str:
    """List: configured scenes and groups."""
    return await get_scenes()

@mcp.resource("domoticz://scene/{idx}")
async def get_scene_resource(idx: int) -> str:
    """Specific: one configured scene or group."""
    return await get_scene(idx=idx)

@mcp.resource("domoticz://scene/name/{name}")
async def get_scene_by_name_resource(name: str) -> str:
    """Specific: one named scene or group."""
    return await get_scene(name=name)

@mcp.resource("domoticz://rooms")
async def get_rooms_resource() -> str:
    """List: Room Plans (logical groupings)."""
    return await get_rooms()

@mcp.resource("domoticz://room/{idx}")
async def get_room_resource(idx: int) -> str:
    """Group: status of all devices in a room."""
    return await get_room_devices(idx=idx)

@mcp.resource("domoticz://room/{room_name}/{idx}")
async def get_room_by_name_resource(room_name: str, idx: int) -> str:
    """Alias: named room URI; idx selects the room."""
    return await get_room_devices(idx=idx)

@mcp.resource("domoticz://user-variables")
async def get_user_variables_resource() -> str:
    """System: current values of user-defined variables."""
    return await get_user_variables()

@mcp.resource("domoticz://user-variable/{idx}")
async def get_user_variable_resource(idx: int) -> str:
    """Specific: one user-defined variable."""
    return await get_user_variable(idx=idx)

@mcp.resource("domoticz://user-variable/name/{name}")
async def get_user_variable_by_name_resource(name: str) -> str:
    """Specific: one named user-defined variable."""
    return await get_user_variable(name=name)

@mcp.resource("domoticz://battery-alerts")
async def get_battery_levels_resource() -> str:
    """Maintenance: devices with <= 20% battery."""
    return await get_battery_levels(threshold=20)

@mcp.resource("domoticz://events")
async def get_events_resource() -> str:
    """Overview: internal event system scripts."""
    return await get_events()

@mcp.resource("domoticz://event/{event_id}")
async def get_event_resource(event_id: int) -> str:
    """Analysis: source code for an automation script."""
    return await get_event(event_id)

@mcp.resource("domoticz://hardware")
async def get_hardware_resource() -> str:
    """System: configured hardware gateways."""
    return await get_hardware()

@mcp.resource("domoticz://dashboard")
async def get_dashboard_resource() -> str:
    """Curated: favorite and active devices status."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        curated = [_simplify_device(d) for d in devices if d.get("Favorite") == 1 or (d.get("Status", "").lower() not in ["off", "closed", "normal", ""])]
        return json.dumps(curated)

@mcp.resource("domoticz://logs")
async def get_log_resource() -> str:
    """Debug: recent system logs."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getlog&lastlogtime=0&loglevel=268435455")
        return response.text

@mcp.resource("domoticz://log")
async def get_log_alias_resource() -> str:
    """Alias: recent system logs."""
    return await get_log_resource()

@mcp.resource("domoticz://logs/error")
async def get_error_logs_resource() -> str:
    """Debug: only 'Error' level log entries."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getlog&lastlogtime=0&loglevel=4")
        return response.text

@mcp.resource("domoticz://security")
async def get_security_resource() -> str:
    """Status: security panel arming state."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getsecstatus")
        return response.text

@mcp.resource("domoticz://settings")
async def get_settings_resource() -> str:
    """Config: global system settings."""
    return await get_settings()

@mcp.resource("domoticz://docs/dzvents_syntax")
async def get_dzvents_docs() -> str:
    """Rules: dzVents automation syntax."""
    return "dzVents Syntax Cheat Sheet: return { on = { devices = { 'My Switch' } }, execute = function(domoticz, device) ... end }"

@mcp.resource("domoticz://docs/blockly_syntax")
async def get_blockly_docs() -> str:
    """Rules: Blockly XML representation."""
    return "Blockly in Domoticz uses an XML representation: <xml xmlns=\"...\"> ... </xml>"

# --- Tools ---

@mcp.tool()
async def search_scripts_tool(query: str) -> str:
    """Search string in scripts. Cross-ref: `domoticz://events`."""
    return await search_scripts(query)

@mcp.tool()
async def search_devices_tool(query: str, offset: int = 0, limit: int = 50) -> str:
    """Search devices by name/status. Cross-ref: `domoticz://devices`."""
    return await search_devices(query, offset, limit)

@mcp.tool()
async def get_daily_energy_history(idx: int | None = None, name: str | None = None, include_instantaneous: bool = True) -> str:
    """Read today's counter history. Preferred: `idx`. Set `include_instantaneous=False` for daily totals."""
    return await _get_energy_history("day", idx=idx, name=name, include_instantaneous=include_instantaneous)

@mcp.tool()
async def get_weekly_energy_history(idx: int | None = None, name: str | None = None) -> str:
    """Read the last 7 days of counter history. Preferred: `idx`."""
    return await _get_energy_history("week", idx=idx, name=name)

@mcp.tool()
async def get_monthly_energy_history(idx: int | None = None, name: str | None = None) -> str:
    """Read this month's counter history. Preferred: `idx`."""
    return await _get_energy_history("month", idx=idx, name=name)

@mcp.tool()
async def toggle_switch(idx: int | None = None, name: str | None = None) -> str:
    """Toggle device. Preferred: `idx`. Cross-ref: `set_switch_state`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd=Toggle")
        return response.text

@mcp.tool()
async def set_switch_state(state: str, idx: int | None = None, name: str | None = None) -> str:
    """Set 'On'/'Off'. Preferred: `idx`. Cross-ref: `toggle_switch`."""
    if state.lower() not in ['on', 'off']: return _error_response("state must be 'On' or 'Off'")
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd={state.capitalize()}")
        return response.text

@mcp.tool()
async def set_dimmer_level(level: int, idx: int | None = None, name: str | None = None) -> str:
    """Set brightness (0-100). Preferred: `idx`. Cross-ref: `domoticz://device/{idx}`."""
    if not (0 <= level <= 100): return _error_response("level must be 0-100")
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd=Set%20Level&level={level}")
        return response.text

@mcp.tool()
async def set_temperature_setpoint(setpoint: float, idx: int | None = None, name: str | None = None) -> str:
    """Set target temp. Preferred: `idx`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=setsetpoint&idx={resolved_idx}&setpoint={setpoint}")
        return response.text

@mcp.tool()
async def control_blinds(command: str, idx: int | None = None, name: str | None = None) -> str:
    """'Open'/'Close'/'Stop'. Preferred: `idx`."""
    if command.capitalize() not in ['Open', 'Close', 'Stop']: return _error_response("Invalid command")
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd={command.capitalize()}")
        return response.text

@mcp.tool()
async def switch_scene(command: str, idx: int | None = None, name: str | None = None) -> str:
    """Activate scene. 'On'/'Off'/'Toggle'. Preferred: `idx`. Cross-ref: `domoticz://scenes`."""
    scene_command = command.capitalize()
    if scene_command not in ["On", "Off", "Toggle"]:
        return _error_response("Invalid command")
    async with create_client() as client:
        resolved_idx = await _resolve_scene_idx(client, idx, name)
        if not resolved_idx: return _error_response("Scene not found")
        response = await _do_request(client, "GET", _command_url("switchscene", {"idx": resolved_idx, "switchcmd": scene_command}))
        return response.text

@mcp.tool()
async def add_user_variable(name: str, vtype: int, value: str) -> str:
    """Add var. vtype: 0=Int, 1=Float, 2=Str, 3=Date, 4=Time. Cross-ref: `domoticz://user-variables`."""
    async with create_client() as client:
        response = await _do_request(client, "GET", _command_url("adduservariable", {"vname": name, "vtype": vtype, "vvalue": value}))
        _user_variable_cache["timestamp"] = 0
        return response.text

@mcp.tool()
async def update_user_variable(name: str, vtype: int, value: str) -> str:
    """Update var value. Cross-ref: `domoticz://user-variables`."""
    async with create_client() as client:
        response = await _do_request(client, "GET", _command_url("updateuservariable", {"vname": name, "vtype": vtype, "vvalue": value}))
        _user_variable_cache["timestamp"] = 0
        return response.text

@mcp.tool()
async def delete_user_variable(idx: int | None = None, name: str | None = None, confirm: bool = False) -> str:
    """Delete var. Preferred: `idx`. Requires `confirm=True`."""
    if not confirm:
        return _confirmation_required("deleting a user variable")
    async with create_client() as client:
        resolved_idx = await _resolve_user_variable_idx(client, idx, name)
        if not resolved_idx: return _error_response("Var not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=deleteuservariable&idx={resolved_idx}")
        _user_variable_cache["timestamp"] = 0
        return response.text

@mcp.tool()
async def create_event(name: str, interpreter: str, event_type: str, xmlstatement: str, eventstatus: str = "1") -> str:
    """Create script. Cross-ref: `domoticz://docs/dzvents_syntax`."""
    async with create_client() as client:
        data = {"evparam": "create", "name": name, "eventstatus": eventstatus, "interpreter": interpreter, "xml": xmlstatement, "eventtype": event_type, "logicarray": ""}
        response = await _do_request(client, "POST", f"{DOMOTICZ_API_URL}?type=command&param=events", data=data)
        return response.text

@mcp.tool()
async def update_event(event_id: int, name: str, interpreter: str, event_type: str, xmlstatement: str, eventstatus: str = "1", confirm: bool = False) -> str:
    """Update script. Cross-ref: `domoticz://event/{event_id}`. Requires `confirm=True`."""
    if not confirm:
        return _confirmation_required("updating an event script")
    async with create_client() as client:
        data = {"evparam": "create", "eventid": str(event_id), "name": name, "eventstatus": eventstatus, "interpreter": interpreter, "xml": xmlstatement, "eventtype": event_type, "logicarray": ""}
        response = await _do_request(client, "POST", f"{DOMOTICZ_API_URL}?type=command&param=events", data=data)
        return response.text

@mcp.tool()
async def call_domoticz_api(param: str, kwargs: dict, confirm: bool = False) -> str:
    """Raw API call. Use if dedicated tools fail. Requires `confirm=True`."""
    if not confirm:
        return _confirmation_required("calling the raw Domoticz API")
    async with create_client() as client:
        response = await _do_request(client, "GET", _command_url(param, kwargs))
        return response.text

@mcp.tool()
async def restart_system(confirm: bool = False) -> str:
    """Reboot server. Requires `confirm=True`."""
    if not confirm: return _confirmation_required("restarting Domoticz")
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=system_reboot")
        return response.text

@mcp.tool()
async def rename_device(new_name: str, idx: int | None = None, old_name: str | None = None) -> str:
    """Rename device. Preferred: `idx`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, old_name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", _command_url("renamedevice", {"name": new_name, "idx": resolved_idx}))
        _device_cache["timestamp"] = 0
        return response.text

@mcp.tool()
async def delete_device(idx: int | None = None, name: str | None = None, confirm: bool = False) -> str:
    """Hide device. Preferred: `idx`. Requires `confirm=True`."""
    if not confirm:
        return _confirmation_required("hiding a device")
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=setused&used=false&idx={resolved_idx}")
        _device_cache["timestamp"] = 0
        return response.text

@mcp.tool()
async def create_virtual_sensor(hw_idx: int, sensorname: str, sensortype: int) -> str:
    """Create dummy. Cross-ref: `domoticz://hardware`."""
    async with create_client() as client:
        response = await _do_request(client, "GET", _command_url("createvirtualsensor", {"idx": hw_idx, "sensorname": sensorname, "sensortype": sensortype}))
        _device_cache["timestamp"] = 0
        return response.text

@mcp.tool()
async def update_device_value(idx: int | None = None, name: str | None = None, nvalue: int = 0, svalue: str = "") -> str:
    """Manual update. Preferred: `idx`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", _command_url("udevice", {"idx": resolved_idx, "nvalue": nvalue, "svalue": svalue}))
        return response.text

@mcp.tool()
async def add_log_message(message: str, level: int = 2) -> str:
    """Log message. level: 1=Normal, 2=Status, 4=Error. Cross-ref: `domoticz://logs`."""
    async with create_client() as client:
        response = await _do_request(client, "GET", _command_url("addlogmessage", {"message": message, "level": level}))
        return response.text

@mcp.tool()
async def send_notification(subject: str, body: str) -> str:
    """Send notification."""
    async with create_client() as client:
        response = await _do_request(client, "GET", _command_url("sendnotification", {"subject": subject, "body": body}))
        return response.text

@mcp.tool()
async def set_security_status(secstatus: int, seccode: str, confirm: bool = False) -> str:
    """Arm panel. 0=Disarm, 1=Home, 2=Away. Requires `confirm=True`. Cross-ref: `domoticz://security`."""
    if not confirm:
        return _confirmation_required("changing security panel status")
    async with create_client() as client:
        response = await _do_request(client, "GET", _command_url("setsecstatus", {"secstatus": secstatus, "seccode": seccode}))
        return response.text

@mcp.tool()
async def set_color_brightness(hue: int, brightness: int, idx: int | None = None, name: str | None = None, iswhite: bool = False) -> str:
    """Set RGB. Preferred: `idx`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=setcolbrightnessvalue&idx={resolved_idx}&hue={hue}&brightness={brightness}&iswhite={str(iswhite).lower()}")
        return response.text

@mcp.tool()
async def set_color_temperature(kelvin: int, idx: int | None = None, name: str | None = None) -> str:
    """Set white temp. Preferred: `idx`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=setkelvinlevel&idx={resolved_idx}&kelvin={kelvin}")
        return response.text

# --- Prompts ---

@mcp.prompt()
def agent_guidance() -> str:
    """Expert rules. ORIENTATION: Read `domoticz://overview`."""
    return "Expert Domoticz rules: 1. START: Read `domoticz://overview`. 2. RESOLVE: Prefer `idx`. 3. BATTERY: 255 = mains. 4. DEBUG: Check `domoticz://logs/error` and `domoticz://health`."

@mcp.prompt()
def maintenance_report() -> str:
    """Health summary. Cross-refs: `domoticz://health`, `domoticz://battery-alerts`, `domoticz://logs/error`."""
    return "Health report: 1. Read `domoticz://battery-alerts`. 2. Read `domoticz://health`. 3. Check `domoticz://logs/error`."

@mcp.prompt()
def summarize_home() -> str:
    """State summary. Cross-ref: `domoticz://dashboard`."""
    return "Summarize home using `domoticz://dashboard`."

@mcp.prompt()
def energy_audit() -> str:
    """Usage analysis. Cross-ref: `domoticz://devices`."""
    return "Audit energy using `domoticz://devices` for current readings and `get_daily_energy_history`, `get_weekly_energy_history`, or `get_monthly_energy_history` for counter trends."

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

    config_spec = {
        "transport": {"default": "stdio", "env_vars": ["DOMOTICZ_MCP_TRANSPORT", "TRANSPORT"]},
        "host": {"default": "127.0.0.1", "env_vars": ["DOMOTICZ_MCP_HOST", "HOST"]},
        "port": {"default": 8000, "type": int, "env_vars": ["DOMOTICZ_MCP_PORT", "PORT"]},
        "domoticz_url": {"default": "http://127.0.0.1:8080", "env_vars": ["DOMOTICZ_URL"]},
        "domoticz_username": {"default": None, "env_vars": ["DOMOTICZ_USERNAME"]},
        "domoticz_password": {"default": None, "env_vars": ["DOMOTICZ_PASSWORD"]},
        "domoticz_client_id": {"default": None, "env_vars": ["DOMOTICZ_CLIENT_ID", "DOMOTICZ_CLIENTID"]},
        "domoticz_client_secret": {"default": None, "env_vars": ["DOMOTICZ_CLIENT_SECRET", "DOMOTICZ_CLIENTSECRET"]},
        "domoticz_oauth_token": {"default": None, "env_vars": ["DOMOTICZ_OAUTH_TOKEN"]},
        "token_file": {"default": os.path.expanduser("~/.config/domoticz-mcp/token.json"), "env_vars": ["DOMOTICZ_MCP_TOKEN_FILE", "TOKEN_FILE"]}
    }

    final_config = {}
    for key, spec in config_spec.items():
        val, source = spec["default"], "default"
        arg_val = getattr(args, key.replace("_", "-"), getattr(args, key, None))
        if arg_val is not None: val, source = arg_val, "command line"
        for env_var in spec.get("env_vars", []):
            env_val = os.environ.get(env_var)
            if env_val:
                env_val = env_val.strip('\'"')
                if spec.get("type") == int:
                    try: val = int(env_val)
                    except ValueError: continue
                else: val = env_val
                source = f"env variable ({env_var})"
                break
        final_config[key] = {"value": val, "source": source}

    url = final_config["domoticz_url"]["value"]
    if not url.startswith(('http://', 'https://')): url = 'http://' + url
    if url.endswith('/json.htm'): url = url[:-9]
    final_config["domoticz_url"]["value"] = url

    DOMOTICZ_BASE_URL = final_config["domoticz_url"]["value"]
    DOMOTICZ_API_URL = f"{DOMOTICZ_BASE_URL}/json.htm"
    DOMOTICZ_USERNAME = final_config["domoticz_username"]["value"]
    DOMOTICZ_PASSWORD = final_config["domoticz_password"]["value"]
    DOMOTICZ_CLIENT_ID = final_config["domoticz_client_id"]["value"]
    DOMOTICZ_CLIENT_SECRET = final_config["domoticz_client_secret"]["value"]
    DOMOTICZ_OAUTH_TOKEN = final_config["domoticz_oauth_token"]["value"]
    TOKEN_FILE = final_config["token_file"]["value"]
    _oauth_token_cache = DOMOTICZ_OAUTH_TOKEN

    transport, host, port = final_config["transport"]["value"], final_config["host"]["value"], final_config["port"]["value"]

    if transport in ["sse", "streamable-http"]:
        import uvicorn
        from starlette.middleware.cors import CORSMiddleware
        mcp.settings.host, mcp.settings.port = host, port
        app = mcp.sse_app() if transport == "sse" else mcp.streamable_http_app()
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
        uvicorn.run(app, host=host, port=port)
    else:
        mcp.run()

if __name__ == "__main__":
    main()

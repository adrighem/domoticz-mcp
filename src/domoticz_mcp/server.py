# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=1.27,<2",
#     "httpx",
#     "pydantic>=2,<3",
#     "python-dotenv",
# ]
# ///

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from dotenv import load_dotenv
import httpx
from pydantic import BaseModel, ConfigDict, Field
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import os
import sys
import json
import base64
import hashlib
import urllib.parse
import secrets
import webbrowser
import asyncio
import re
import stat
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Generic, Literal, NoReturn, Optional, Dict, Any, List, TYPE_CHECKING, TypeVar

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


@asynccontextmanager
async def _server_lifespan(_server: FastMCP):
    """Close the shared upstream client when the MCP server stops."""
    _open_oauth_login_manager()
    try:
        yield {}
    finally:
        await _shutdown_oauth_login_flow()
        await close_global_client()


mcp = FastMCP(
    "domoticz_mcp",
    instructions=(
        "Read domoticz://overview before operating the system. Prefer stable numeric "
        "idx values for devices, scenes, and user variables. If authentication needs "
        "attention, call start_oauth_login, give its URL to the user, then poll "
        "get_oauth_login_status. Event scripts use event IDs."
    ),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    stateless_http=True,
    lifespan=_server_lifespan,
)

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
HTTP_TIMEOUT_SECONDS = 30.0
OAUTH_CALLBACK_TIMEOUT_SECONDS = 120.0
OAUTH_CALLBACK_SOCKET_TIMEOUT_SECONDS = 0.25
OAUTH_STATUS_RETENTION_SECONDS = 300.0
OAUTH_TERMINAL_STATUS_LIMIT = 4
AUTHENTICATION_REQUIRED_MESSAGE = (
    "Domoticz authentication requires attention. Call `start_oauth_login`, or run "
    "`domoticz-mcp --authenticate` in an interactive terminal. Alternatively, "
    "configure a valid OAuth token or username/password."
)

# Cache settings
CACHE_TTL = 300 # 5 minutes
_device_cache = {"data": None, "timestamp": 0, "generation": 0}
_scene_cache = {"data": None, "timestamp": 0, "generation": 0}
_user_variable_cache = {"data": None, "timestamp": 0, "generation": 0}
_plans_cache = {"data": None, "timestamp": 0, "generation": 0}


class DomoticzItem(BaseModel):
    """A Domoticz object while preserving fields added by the upstream API."""

    model_config = ConfigDict(extra="allow")

    idx: int | str = Field(description="Numeric Domoticz device idx")
    Name: str = Field(description="Configured device name")
    Data: str | None = Field(default=None, description="Current human-readable device value")
    Status: str | None = Field(default=None, description="Current switch-style status")
    Type: str | None = Field(default=None, description="Domoticz device type")
    SubType: str | None = Field(default=None, description="Domoticz device subtype")


class ScriptSearchMatch(BaseModel):
    """A matching Domoticz event script."""

    model_config = ConfigDict(extra="allow")

    id: int | str = Field(description="Canonical Domoticz event ID")
    idx: int | str = Field(description="Compatibility alias for the event ID")
    Name: str | None = Field(default=None, description="Event name")


class DomoticzToolResult(BaseModel):
    """A successful Domoticz command response."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(description="Status returned by Domoticz, normally OK")
    title: str | None = Field(default=None, description="Optional Domoticz response title")


class OAuthLoginStartResult(BaseModel):
    """Safe details needed to continue a browser-based OAuth login."""

    status: Literal["pending"] = Field(description="Login flow state")
    flow_id: str = Field(description="Opaque identifier used to check this login flow")
    authorization_url: str = Field(description="URL the user must open to authorize Domoticz")
    expires_at: str = Field(description="UTC time when this login flow expires")
    message: str = Field(description="Next action for the user or agent")


class OAuthLoginStatusResult(BaseModel):
    """Credential-free status for a browser-based OAuth login."""

    status: Literal["pending", "complete", "expired", "error"] = Field(
        description="Current login flow state"
    )
    flow_id: str = Field(description="Opaque login flow identifier")
    expires_at: str = Field(description="UTC time when the authorization window expires")
    message: str = Field(description="Current state and next action")


@dataclass
class _OAuthLoginFlow:
    """Secret-bearing state for one in-process OAuth authorization flow."""

    flow_id: str
    state: str
    code_verifier: str
    authorization_url: str
    redirect_uri: str
    expires_at: str
    expires_monotonic: float
    callback_server: HTTPServer
    callback_future: asyncio.Future[str]
    event_loop: asyncio.AbstractEventLoop
    callback_claimed: threading.Event = field(default_factory=threading.Event)
    callback_thread: threading.Thread | None = None
    supervisor_task: asyncio.Task[None] | None = None
    authorization_code: str | None = None
    listener_stop_task: asyncio.Task[None] | None = None


@dataclass
class _RetainedOAuthStatus:
    """Credential-free terminal status retained briefly for polling."""

    result: OAuthLoginStatusResult
    retain_until_monotonic: float


_oauth_login_flow_lock = threading.Lock()
_oauth_login_start_lock = asyncio.Lock()
_active_oauth_login_flow: _OAuthLoginFlow | None = None
_oauth_terminal_statuses: OrderedDict[str, _RetainedOAuthStatus] = OrderedDict()
_oauth_login_manager_closing = False


ResultItem = TypeVar("ResultItem", bound=BaseModel)


class PaginatedToolResult(BaseModel, Generic[ResultItem]):
    """A page of MCP tool results with explicit continuation metadata."""

    status: str = Field(description="Status returned by Domoticz, normally OK")
    result: list[ResultItem] = Field(description="Results in this page")
    total_count: int = Field(ge=0, description="Total number of matching results")
    count: int = Field(ge=0, description="Number of results in this page")
    offset: int = Field(ge=0, description="Zero-based offset of this page")
    limit: int = Field(ge=1, description="Maximum results requested")
    has_more: bool = Field(description="Whether another page is available")
    next_offset: int | None = Field(
        default=None,
        ge=0,
        description="Offset for the next page, or null when this is the final page",
    )


class EnergyHistorySummary(BaseModel):
    """Aggregate statistics for normalized energy history."""

    model_config = ConfigDict(extra="allow")

    samples: int = Field(ge=0, description="Number of normalized history samples")
    category: str = Field(description="Energy, gas, water, or storage category")
    power: dict[str, float] | None = Field(default=None, description="Power aggregates in watts")
    energy: dict[str, float] | None = Field(default=None, description="Energy aggregates in kilowatt-hours")
    volume: dict[str, float] | None = Field(default=None, description="Volume aggregates in cubic metres")


class EnergyHistorySample(BaseModel):
    """A normalized Domoticz graph sample."""

    model_config = ConfigDict(extra="allow")

    d: str | None = Field(default=None, description="Domoticz sample date or timestamp")
    numeric: dict[str, float] | None = Field(default=None, description="Numeric forms of upstream values")
    power_w: float | None = Field(default=None, description="Normalized power in watts")
    energy_kwh: float | None = Field(default=None, description="Normalized energy in kilowatt-hours")
    volume_m3: float | None = Field(default=None, description="Normalized volume in cubic metres")


class EnergyHistoryToolResult(DomoticzToolResult):
    """Normalized energy, gas, or water history."""

    period: Literal["day", "week", "month"] = Field(description="Requested history period")
    range: str = Field(description="Domoticz graph range sent upstream")
    idx: int = Field(ge=1, description="Resolved device idx")
    Name: str | None = Field(default=None, description="Resolved device name")
    include_instantaneous: bool = Field(description="Whether instantaneous samples were requested")
    summary: EnergyHistorySummary = Field(description="Aggregate statistics for the returned samples")
    result: list[EnergyHistorySample] = Field(description="Normalized history samples")


EntityIdxArg = Annotated[
    int | None,
    Field(ge=1, description="Numeric Domoticz idx; preferred over name when known"),
]
EntityNameArg = Annotated[
    str | None,
    Field(min_length=1, max_length=200, description="Case-insensitive Domoticz entity name"),
]
OffsetArg = Annotated[
    int,
    Field(ge=0, description="Zero-based result offset"),
]
LimitArg = Annotated[
    int,
    Field(ge=1, le=100, description="Maximum number of results to return"),
]
ConfirmArg = Annotated[
    bool,
    Field(description="Explicitly confirm this high-impact operation"),
]


def _tool_annotations(
    *,
    read_only: bool,
    destructive: bool | None = None,
    idempotent: bool = True,
) -> ToolAnnotations:
    """Build complete MCP tool behavior hints."""
    if destructive is None:
        destructive = not read_only
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=True,
    )


def _invalidate_caches(*caches: Dict[str, Any]) -> None:
    """Invalidate cached data and any in-flight reads of the old generation."""
    for cache in caches:
        cache["generation"] = cache.get("generation", 0) + 1
        cache["data"] = None
        cache["timestamp"] = 0


def _format_response(data: Dict[str, Any]) -> str:
    """Format a dictionary as a JSON string response."""
    return json.dumps(data)


def _error_response(message: str, status: str = "error") -> NoReturn:
    """Raise a protocol-visible MCP tool error."""
    raise ToolError(message)


def _confirmation_required(action: str) -> NoReturn:
    """Raise a protocol-visible confirmation error."""
    _error_response(f"Confirmation required for {action}. Retry with confirm=True.")


def _domoticz_payload(response: httpx.Response, action: str) -> Dict[str, Any]:
    """Parse and validate a JSON object returned by Domoticz."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise ToolError(f"{action} failed: Domoticz returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise ToolError(f"{action} failed: Domoticz returned an unexpected response.")

    return _validated_success_payload(payload, action)


def _validated_success_payload(payload: Dict[str, Any], action: str) -> Dict[str, Any]:
    """Require a successful Domoticz status while allowing status-less reads."""
    raw_status = payload.get("status")
    status = "OK" if raw_status is None else str(raw_status)
    if status.upper() not in {"OK", "SUCCESS"}:
        detail = payload.get("message") or payload.get("title") or payload.get("error")
        suffix = f": {detail}" if detail else f" with status {status}"
        raise ToolError(f"{action} failed{suffix}.")

    payload["status"] = status
    return payload


def _tool_result(response: httpx.Response, action: str) -> DomoticzToolResult:
    """Convert a successful Domoticz response into structured MCP output."""
    return DomoticzToolResult.model_validate(_domoticz_payload(response, action))


async def _mutation_result(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    action: str,
    caches: tuple[Dict[str, Any], ...],
    **request_kwargs: Any,
) -> DomoticzToolResult:
    """Run a mutation and invalidate affected caches even if its outcome is ambiguous."""
    try:
        response = await _do_request(client, method, url, **request_kwargs)
        return _tool_result(response, action)
    finally:
        _invalidate_caches(*caches)


def _json_response_text(response: httpx.Response, action: str) -> str:
    """Return validated Domoticz JSON for an MCP resource."""
    return json.dumps(_domoticz_payload(response, action))


def _structured_json_result(
    response_text: str,
    action: str,
    model: type[ResultItem],
) -> ResultItem:
    """Convert an internal JSON response into a typed MCP result."""
    try:
        payload = json.loads(response_text)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{action} failed: internal response was invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise ToolError(f"{action} failed: internal response was not an object.")
    return model.model_validate(_validated_success_payload(payload, action))


def _command_url(param: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Build a Domoticz command URL with encoded query parameters."""
    query: Dict[str, Any] = {"type": "command", "param": param}
    if params:
        query.update(params)
    return f"{DOMOTICZ_API_URL}?{urllib.parse.urlencode(query, quote_via=urllib.parse.quote)}"


def _load_token_data() -> Dict[str, Any] | None:
    """Load persisted OAuth data without exposing token contents."""
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        token_status = os.lstat(TOKEN_FILE)
        if not stat.S_ISREG(token_status.st_mode):
            raise ToolError("Refusing to read a non-regular Domoticz OAuth token file.")
        if stat.S_IMODE(token_status.st_mode) != 0o600:
            os.chmod(TOKEN_FILE, 0o600)
        with open(TOKEN_FILE, "r", encoding="utf-8") as token_stream:
            token_data = json.load(token_stream)
    except ToolError:
        raise
    except (OSError, ValueError) as exc:
        raise ToolError(f"Failed to load the Domoticz OAuth token file: {exc}") from exc
    if not isinstance(token_data, dict):
        raise ToolError("Failed to load the Domoticz OAuth token file: expected a JSON object.")
    return token_data


def _save_token_data(token_data: Dict[str, Any]) -> None:
    """Atomically persist OAuth data with owner-only permissions."""
    token_directory = os.path.dirname(TOKEN_FILE) or "."
    os.makedirs(token_directory, exist_ok=True)
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            dir=token_directory,
            prefix=".token-",
            suffix=".json",
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as token_stream:
            json.dump(token_data, token_stream)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, TOKEN_FILE)
        temporary_path = None
        os.chmod(TOKEN_FILE, 0o600)
    except OSError as exc:
        raise ToolError("Failed to securely save the Domoticz OAuth token file.") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _new_pkce_credentials() -> tuple[str, str, str]:
    """Create independent OAuth state, verifier, and S256 challenge values."""
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    return state, code_verifier, code_challenge


def _build_oauth_authorization_url(
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    """Build a Domoticz authorization URL without including any secret."""
    return (
        f"{DOMOTICZ_BASE_URL}/oauth2/v1/authorize?"
        + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": DOMOTICZ_CLIENT_ID,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            },
            quote_via=urllib.parse.quote,
        )
    )


def _send_oauth_callback_html(
    handler: BaseHTTPRequestHandler,
    status_code: int,
    heading: str,
    message: str,
) -> None:
    """Send a static, non-cacheable callback response."""
    body = (
        f"<html><body><h1>{heading}</h1><p>{message}</p></body></html>"
    ).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class _QuietOAuthHTTPServer(HTTPServer):
    """HTTP server that never logs callback paths or authorization codes."""

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(OAUTH_CALLBACK_SOCKET_TIMEOUT_SECONDS)
        return request, client_address

    def handle_error(self, request, client_address):
        pass


def _record_oauth_callback(
    flow: _OAuthLoginFlow,
    signal: Literal["authorized", "oauth_error", "listener_error", "shutdown"],
    authorization_code: str | None = None,
) -> None:
    """Record a callback result on the owning asyncio event loop."""
    with _oauth_login_flow_lock:
        if _active_oauth_login_flow is not flow or flow.callback_future.done():
            return
        if signal == "authorized":
            flow.authorization_code = authorization_code
        flow.callback_future.set_result(signal)


def _serve_oauth_callback(flow: _OAuthLoginFlow) -> None:
    """Serve one callback listener and report unexpected listener failure safely."""
    try:
        flow.callback_server.serve_forever(poll_interval=0.1)
    except Exception:
        try:
            flow.event_loop.call_soon_threadsafe(
                _record_oauth_callback,
                flow,
                "listener_error",
                None,
            )
        except RuntimeError:
            pass


def _create_oauth_login_flow() -> _OAuthLoginFlow:
    """Bind a loopback callback listener and create secret-bearing flow state."""
    event_loop = asyncio.get_running_loop()
    state, code_verifier, code_challenge = _new_pkce_credentials()
    flow_id = secrets.token_urlsafe(32)
    flow: _OAuthLoginFlow

    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            parsed_path = urllib.parse.urlparse(self.path)
            if parsed_path.path != "/callback":
                _send_oauth_callback_html(
                    self,
                    404,
                    "Not found",
                    "This callback path is not available.",
                )
                return

            query = urllib.parse.parse_qs(
                parsed_path.query,
                keep_blank_values=True,
            )
            states = query.get("state", [])
            codes = query.get("code", [])
            errors = query.get("error", [])
            if (
                len(states) != 1
                or not states[0]
                or not secrets.compare_digest(states[0], flow.state)
            ):
                _send_oauth_callback_html(
                    self,
                    400,
                    "Authentication rejected",
                    "The callback state was invalid. Return to Codex and retry.",
                )
                return

            valid_code = len(codes) == 1 and bool(codes[0]) and not errors
            valid_error = len(errors) == 1 and bool(errors[0]) and not codes
            if not valid_code and not valid_error:
                _send_oauth_callback_html(
                    self,
                    400,
                    "Authentication rejected",
                    "The callback response was incomplete. Return to Codex and retry.",
                )
                return

            if flow.callback_claimed.is_set():
                _send_oauth_callback_html(
                    self,
                    409,
                    "Authentication already received",
                    "Return to Codex to check the login status.",
                )
                return
            flow.callback_claimed.set()

            signal: Literal["authorized", "oauth_error"]
            authorization_code = None
            if valid_code:
                signal = "authorized"
                authorization_code = codes[0]
                status_code = 200
                heading = "Authentication received"
                message = "Return to Codex to check the login status."
            else:
                signal = "oauth_error"
                status_code = 400
                heading = "Authentication was not completed"
                message = "Return to Codex and start a new login flow."

            try:
                flow.event_loop.call_soon_threadsafe(
                    _record_oauth_callback,
                    flow,
                    signal,
                    authorization_code,
                )
            except RuntimeError:
                _send_oauth_callback_html(
                    self,
                    503,
                    "Authentication unavailable",
                    "The MCP server is no longer accepting this callback.",
                )
                return
            _send_oauth_callback_html(self, status_code, heading, message)

    callback_server = _QuietOAuthHTTPServer(("127.0.0.1", 0), OAuthCallbackHandler)
    redirect_uri = f"http://127.0.0.1:{callback_server.server_port}/callback"
    authorization_url = _build_oauth_authorization_url(
        redirect_uri,
        state,
        code_challenge,
    )
    expires_at_datetime = datetime.now(timezone.utc) + timedelta(
        seconds=OAUTH_CALLBACK_TIMEOUT_SECONDS
    )
    flow = _OAuthLoginFlow(
        flow_id=flow_id,
        state=state,
        code_verifier=code_verifier,
        authorization_url=authorization_url,
        redirect_uri=redirect_uri,
        expires_at=expires_at_datetime.isoformat().replace("+00:00", "Z"),
        expires_monotonic=time.monotonic() + OAUTH_CALLBACK_TIMEOUT_SECONDS,
        callback_server=callback_server,
        callback_future=event_loop.create_future(),
        event_loop=event_loop,
    )
    flow.callback_thread = threading.Thread(
        target=_serve_oauth_callback,
        args=(flow,),
        name="domoticz-oauth-callback",
        daemon=True,
    )
    return flow


async def _stop_oauth_callback_listener(flow: _OAuthLoginFlow) -> None:
    """Stop and join a callback listener exactly once."""
    async def stop_listener() -> None:
        callback_thread = flow.callback_thread
        if callback_thread is not None and callback_thread.is_alive():
            await asyncio.to_thread(flow.callback_server.shutdown)
            await asyncio.to_thread(callback_thread.join, 1.0)
        flow.callback_server.server_close()

    with _oauth_login_flow_lock:
        if flow.listener_stop_task is None:
            flow.listener_stop_task = asyncio.create_task(stop_listener())
        listener_stop_task = flow.listener_stop_task
    await asyncio.shield(listener_stop_task)


def _scrub_oauth_flow(flow: _OAuthLoginFlow) -> None:
    """Remove secret-bearing values from a completed or abandoned flow."""
    flow.state = ""
    flow.code_verifier = ""
    flow.authorization_code = None
    flow.redirect_uri = ""
    flow.authorization_url = ""


def _purge_oauth_terminal_statuses_locked(now: float) -> None:
    """Drop retained terminal statuses after their short polling window."""
    expired_ids = [
        flow_id
        for flow_id, retained in _oauth_terminal_statuses.items()
        if retained.retain_until_monotonic <= now
    ]
    for flow_id in expired_ids:
        _oauth_terminal_statuses.pop(flow_id, None)


def _retain_oauth_terminal_status(
    flow: _OAuthLoginFlow,
    status: Literal["complete", "expired", "error"],
    message: str,
) -> None:
    """Replace active secret state with a bounded credential-free status."""
    global _active_oauth_login_flow
    result = OAuthLoginStatusResult(
        status=status,
        flow_id=flow.flow_id,
        expires_at=flow.expires_at,
        message=message,
    )
    with _oauth_login_flow_lock:
        if _active_oauth_login_flow is flow:
            _active_oauth_login_flow = None
        _purge_oauth_terminal_statuses_locked(time.monotonic())
        _oauth_terminal_statuses[flow.flow_id] = _RetainedOAuthStatus(
            result=result,
            retain_until_monotonic=time.monotonic()
            + OAUTH_STATUS_RETENTION_SECONDS,
        )
        while len(_oauth_terminal_statuses) > OAUTH_TERMINAL_STATUS_LIMIT:
            _oauth_terminal_statuses.popitem(last=False)
        _scrub_oauth_flow(flow)


async def _supervise_oauth_login_flow(flow: _OAuthLoginFlow) -> None:
    """Wait for the callback, exchange the code, and retain only safe status."""
    terminal_status: Literal["complete", "expired", "error"] | None = None
    terminal_message = ""
    try:
        remaining = max(0.0, flow.expires_monotonic - time.monotonic())
        signal = await asyncio.wait_for(
            asyncio.shield(flow.callback_future),
            timeout=remaining,
        )
        await _stop_oauth_callback_listener(flow)

        if signal == "oauth_error":
            terminal_status = "error"
            terminal_message = (
                "Domoticz did not authorize the request. Start a new login flow."
            )
            return
        if signal == "listener_error":
            terminal_status = "error"
            terminal_message = (
                "The local OAuth callback listener failed. Start a new login flow."
            )
            return
        if signal == "shutdown":
            return
        if not flow.authorization_code:
            terminal_status = "error"
            terminal_message = (
                "Domoticz returned an incomplete callback. Start a new login flow."
            )
            return

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            token_endpoint = await _discover_token_endpoint(client)
            async with _token_refresh_lock:
                existing_token_data = _load_token_data()
                token_auth = (
                    (DOMOTICZ_CLIENT_ID, DOMOTICZ_CLIENT_SECRET)
                    if DOMOTICZ_CLIENT_SECRET
                    else None
                )
                token_response = await client.post(
                    token_endpoint,
                    auth=token_auth,
                    data={
                        "grant_type": "authorization_code",
                        "code": flow.authorization_code,
                        "redirect_uri": flow.redirect_uri,
                        "client_id": DOMOTICZ_CLIENT_ID,
                        "code_verifier": flow.code_verifier,
                    },
                )
                token_response.raise_for_status()
                _cache_and_save_token(
                    token_response.json(),
                    existing_token_data,
                )
        terminal_status = "complete"
        terminal_message = (
            "Domoticz authentication is complete. Retry the original operation."
        )
    except asyncio.TimeoutError:
        terminal_status = "expired"
        terminal_message = "The login flow expired. Start a new login flow."
    except asyncio.CancelledError:
        raise
    except (httpx.HTTPError, ToolError, ValueError):
        terminal_status = "error"
        terminal_message = (
            "The OAuth token exchange failed. Check the Domoticz OAuth configuration "
            "and start a new login flow."
        )
    finally:
        await _stop_oauth_callback_listener(flow)
        if terminal_status is None:
            with _oauth_login_flow_lock:
                global _active_oauth_login_flow
                if _active_oauth_login_flow is flow:
                    _active_oauth_login_flow = None
                _scrub_oauth_flow(flow)
        else:
            _retain_oauth_terminal_status(
                flow,
                terminal_status,
                terminal_message,
            )


async def _start_oauth_login_flow() -> OAuthLoginStartResult:
    """Start or reuse the one active non-blocking OAuth login flow."""
    global _active_oauth_login_flow
    if not DOMOTICZ_CLIENT_ID:
        raise ToolError(
            "OAuth login requires DOMOTICZ_CLIENT_ID to be configured."
        )

    async with _oauth_login_start_lock:
        with _oauth_login_flow_lock:
            if _oauth_login_manager_closing:
                raise ToolError(
                    "OAuth login is unavailable while the MCP server is shutting down."
                )
            _purge_oauth_terminal_statuses_locked(time.monotonic())
            active_flow = _active_oauth_login_flow
            if active_flow is not None:
                return OAuthLoginStartResult(
                    status="pending",
                    flow_id=active_flow.flow_id,
                    authorization_url=active_flow.authorization_url,
                    expires_at=active_flow.expires_at,
                    message=(
                        "Open the authorization URL, then call "
                        "get_oauth_login_status with this flow_id."
                    ),
                )

        flow = _create_oauth_login_flow()
        with _oauth_login_flow_lock:
            _active_oauth_login_flow = flow
        flow.callback_thread.start()
        flow.supervisor_task = asyncio.create_task(
            _supervise_oauth_login_flow(flow),
            name=f"domoticz-oauth-login-{flow.flow_id}",
        )
        return OAuthLoginStartResult(
            status="pending",
            flow_id=flow.flow_id,
            authorization_url=flow.authorization_url,
            expires_at=flow.expires_at,
            message=(
                "Open the authorization URL, then call get_oauth_login_status "
                "with this flow_id."
            ),
        )


def _get_oauth_login_flow_status(flow_id: str) -> OAuthLoginStatusResult:
    """Return safe status for the active or recently completed login flow."""
    with _oauth_login_flow_lock:
        _purge_oauth_terminal_statuses_locked(time.monotonic())
        if (
            _active_oauth_login_flow is not None
            and _active_oauth_login_flow.flow_id == flow_id
        ):
            flow = _active_oauth_login_flow
            message = (
                "Completing the OAuth token exchange."
                if flow.callback_future.done()
                else "Waiting for the user to open and approve the authorization URL."
            )
            return OAuthLoginStatusResult(
                status="pending",
                flow_id=flow.flow_id,
                expires_at=flow.expires_at,
                message=message,
            )
        retained = _oauth_terminal_statuses.get(flow_id)
        if retained is not None:
            return retained.result
    raise ToolError(
        "OAuth login flow not found or no longer retained. Start a new login flow."
    )


async def _shutdown_oauth_login_flow() -> None:
    """Cancel and drain any active flow during MCP server shutdown."""
    global _active_oauth_login_flow, _oauth_login_manager_closing
    async with _oauth_login_start_lock:
        with _oauth_login_flow_lock:
            _oauth_login_manager_closing = True
            flow = _active_oauth_login_flow
            task = flow.supervisor_task if flow is not None else None
    if task is not None and not task.done():
        if flow is not None and not flow.callback_future.done():
            flow.callback_future.set_result("shutdown")
        else:
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    elif flow is not None:
        await _stop_oauth_callback_listener(flow)
        with _oauth_login_flow_lock:
            if _active_oauth_login_flow is flow:
                _active_oauth_login_flow = None
            _scrub_oauth_flow(flow)
    with _oauth_login_flow_lock:
        _oauth_terminal_statuses.clear()


def _open_oauth_login_manager() -> None:
    """Allow login starts for a newly entered MCP server lifespan."""
    global _oauth_login_manager_closing
    with _oauth_login_flow_lock:
        _oauth_login_manager_closing = False


def _do_interactive_oauth_flow(
    timeout_seconds: float = OAUTH_CALLBACK_TIMEOUT_SECONDS,
) -> tuple[str | None, str | None, str | None]:
    """Run an explicitly requested browser flow with a bounded callback wait."""
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
                received_code = query.get('code', [None])[0]
                received_state = query.get('state', [None])[0]
                if not received_code or received_state != state:
                    self.send_response(400)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h1>Authentication rejected</h1>"
                        b"<p>The callback state was invalid. Please retry.</p></body></html>"
                    )
                    return
                code = received_code
                state_received = received_state
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authentication successful!</h1><p>You can close this window now and return to the application.</p></body></html>")
            else:
                self.send_response(404)
                self.end_headers()

    callback_server = _QuietOAuthHTTPServer(('127.0.0.1', 0), CallbackHandler)
    port = callback_server.server_port

    state, code_verifier, code_challenge = _new_pkce_credentials()

    redirect_uri = f"http://127.0.0.1:{port}/callback"
    auth_url = _build_oauth_authorization_url(
        redirect_uri,
        state,
        code_challenge,
    )

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

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    try:
        while not (code and state_received == state) and time.monotonic() < deadline:
            callback_server.timeout = min(1.0, max(0.0, deadline - time.monotonic()))
            callback_server.handle_request()
    finally:
        callback_server.server_close()

    if code and state_received == state:
        return code, code_verifier, redirect_uri
    sys.stderr.write("Domoticz authentication timed out waiting for the browser callback.\n")
    sys.stderr.flush()
    return None, None, None

_token_refresh_lock = asyncio.Lock()

async def _discover_token_endpoint(client: httpx.AsyncClient) -> str:
    """Discover and normalize the configured Domoticz token endpoint."""
    discovery_url = f"{DOMOTICZ_BASE_URL}/.well-known/openid-configuration"
    response = await client.get(discovery_url)
    response.raise_for_status()
    config = response.json()
    token_endpoint = config.get("token_endpoint")
    if not token_endpoint:
        raise ToolError("Domoticz OAuth discovery did not provide a token endpoint.")
    if "127.0.0.1" in token_endpoint or "localhost" in token_endpoint:
        parsed = urllib.parse.urlparse(token_endpoint)
        token_endpoint = f"{DOMOTICZ_BASE_URL}{parsed.path}"
    parsed_endpoint = urllib.parse.urlparse(token_endpoint)
    if (
        parsed_endpoint.scheme not in {"http", "https"}
        or not parsed_endpoint.netloc
        or parsed_endpoint.username
        or parsed_endpoint.password
    ):
        raise ToolError("Domoticz OAuth discovery returned an unsafe token endpoint.")
    return token_endpoint


def _cache_and_save_token(
    token_data: Dict[str, Any],
    existing_token_data: Dict[str, Any] | None,
) -> Optional[str]:
    """Update the in-memory token and securely persist its refresh metadata."""
    global _oauth_token_cache
    if (
        "refresh_token" not in token_data
        and existing_token_data
        and "refresh_token" in existing_token_data
    ):
        token_data["refresh_token"] = existing_token_data["refresh_token"]
    access_token = token_data.get("access_token")
    if not access_token:
        raise ToolError("Domoticz OAuth token response did not include an access token.")
    _oauth_token_cache = access_token
    _save_token_data(token_data)
    if _global_http_client is not None and not _global_http_client.is_closed:
        _global_http_client.headers["Authorization"] = f"Bearer {access_token}"
        _global_http_client.auth = None
    return _oauth_token_cache


async def _fetch_oauth_token(
    force_refresh: bool = False,
    old_token: Optional[str] = None,
    *,
    allow_interactive: bool = False,
) -> Optional[str]:
    """Load or refresh OAuth credentials without prompting from MCP requests."""
    global _oauth_token_cache

    if not force_refresh and _oauth_token_cache:
        return _oauth_token_cache

    interactive_context: tuple[str, tuple[str, str] | None, Dict[str, Any] | None] | None = None
    async with _token_refresh_lock:
        # Check if another task already refreshed the token while we were waiting
        if force_refresh and old_token is not None and _oauth_token_cache != old_token:
            return _oauth_token_cache

        if not force_refresh and _oauth_token_cache:
            return _oauth_token_cache

        existing_token_data = _load_token_data()
        if not force_refresh and existing_token_data and "access_token" in existing_token_data:
            _oauth_token_cache = existing_token_data["access_token"]
            return _oauth_token_cache

        if not DOMOTICZ_CLIENT_ID:
            return None

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                token_endpoint = await _discover_token_endpoint(client)
                auth = (DOMOTICZ_CLIENT_ID, DOMOTICZ_CLIENT_SECRET) if DOMOTICZ_CLIENT_SECRET else None

                # Try refresh token first
                if force_refresh and existing_token_data and "refresh_token" in existing_token_data:
                    data = {
                        "grant_type": "refresh_token",
                        "refresh_token": existing_token_data["refresh_token"],
                        "client_id": DOMOTICZ_CLIENT_ID
                    }
                    if DOMOTICZ_CLIENT_SECRET:
                        data["client_secret"] = DOMOTICZ_CLIENT_SECRET

                    refresh_response = await client.post(token_endpoint, auth=auth, data=data)
                    if refresh_response.status_code == 200:
                        return _cache_and_save_token(
                            refresh_response.json(),
                            existing_token_data,
                        )

                if DOMOTICZ_USERNAME and DOMOTICZ_PASSWORD:
                    data = {
                        "grant_type": "password",
                        "client_id": DOMOTICZ_CLIENT_ID,
                        "client_secret": DOMOTICZ_CLIENT_SECRET,
                    }
                    token_response = await client.post(
                        token_endpoint,
                        auth=(DOMOTICZ_USERNAME, DOMOTICZ_PASSWORD),
                        data=data,
                    )
                    token_response.raise_for_status()
                    return _cache_and_save_token(
                        token_response.json(),
                        existing_token_data,
                    )

                if allow_interactive:
                    interactive_context = (token_endpoint, auth, existing_token_data)
                else:
                    raise ToolError(AUTHENTICATION_REQUIRED_MESSAGE)
        except ToolError:
            raise
        except httpx.TimeoutException as exc:
            raise ToolError(
                "Domoticz OAuth request timed out. Check connectivity and authentication settings."
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolError(f"{AUTHENTICATION_REQUIRED_MESSAGE} ({type(exc).__name__})") from exc

    if interactive_context is None:
        raise ToolError(AUTHENTICATION_REQUIRED_MESSAGE)

    token_endpoint, auth, existing_token_data = interactive_context
    code, code_verifier, redirect_uri = await asyncio.to_thread(
        _do_interactive_oauth_flow,
        OAUTH_CALLBACK_TIMEOUT_SECONDS,
    )
    if not code:
        raise ToolError(
            "Domoticz authentication timed out. Re-run `domoticz-mcp --authenticate` to try again."
        )

    async with _token_refresh_lock:
        if old_token is not None and _oauth_token_cache != old_token:
            return _oauth_token_cache
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                token_response = await client.post(
                    token_endpoint,
                    auth=auth,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "client_id": DOMOTICZ_CLIENT_ID,
                        "code_verifier": code_verifier,
                    },
                )
                token_response.raise_for_status()
                return _cache_and_save_token(
                    token_response.json(),
                    existing_token_data,
                )
        except httpx.TimeoutException as exc:
            raise ToolError("Domoticz OAuth token exchange timed out.") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolError(
                f"Domoticz OAuth token exchange failed ({type(exc).__name__})."
            ) from exc

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
    except httpx.TimeoutException as e:
        raise ToolError(
            "Domoticz request timed out. Check the Domoticz connection and try again."
        ) from e
    except httpx.RequestError as e:
        raise ToolError(
            f"Could not connect to Domoticz ({type(e).__name__}). Check the configured URL."
        ) from e
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise ToolError(AUTHENTICATION_REQUIRED_MESSAGE) from e
        raise e

# Custom AsyncClient wrapper that ensures the token is added
class DomoticzClient:
    def __init__(self, own_client: bool = False):
        global _global_http_client
        self._own_client = own_client
        if own_client:
            self.client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)
            self._owns_client = True
        else:
            if _global_http_client is None or _global_http_client.is_closed:
                _global_http_client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)
            self.client = _global_http_client
            self._owns_client = False

    async def __aenter__(self) -> "httpx.AsyncClient":
        oauth_token = _oauth_token_cache or DOMOTICZ_OAUTH_TOKEN

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


async def _gather_cancel_on_error(*awaitables: Any) -> list[Any]:
    """Run independent work concurrently and drain siblings after any failure."""
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _get_cached_data(client: "httpx.AsyncClient", cache_obj: Dict[str, Any], api_url: str, key_path: str = "result") -> List[Dict[str, Any]]:
    while True:
        now = time.time()
        if cache_obj["data"] is not None and (now - cache_obj["timestamp"]) <= CACHE_TTL:
            return cache_obj["data"]

        generation = cache_obj.get("generation", 0)
        response = await _do_request(client, "GET", api_url)
        payload = _domoticz_payload(response, "Reading cached Domoticz data")
        data = payload.get(key_path, [])
        if not isinstance(data, list):
            raise ToolError("Reading cached Domoticz data failed: result was not a list.")

        # A mutation may have happened while this request was in flight. In that
        # case the response can be stale, so discard it and fetch the new generation.
        if generation != cache_obj.get("generation", 0):
            continue

        cache_obj["data"] = data
        cache_obj["timestamp"] = time.time()
        return data

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

def _paginate(data: list, offset: int, limit: int | None) -> list:
    """Paginate a list of results."""
    if limit is None:
        return data[offset:]
    return data[offset:offset + limit]


def _paginated_payload(
    data: list[Dict[str, Any]],
    offset: int,
    limit: int | None,
) -> Dict[str, Any]:
    """Return a page plus enough metadata for clients to continue."""
    page = _paginate(data, offset, limit)
    total_count = len(data)
    has_more = offset + len(page) < total_count
    return {
        "status": "OK",
        "result": page,
        "total_count": total_count,
        "count": len(page),
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": offset + len(page) if has_more else None,
    }


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
        data = _domoticz_payload(response, "Reading energy history")
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
        resp, devices, scenes, vars, plans, hw_resp = await _gather_cancel_on_error(
            _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getversion"),
            _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true"),
            _get_cached_data(client, _scene_cache, f"{DOMOTICZ_API_URL}?type=command&param=getscenes"),
            _get_cached_data(client, _user_variable_cache, f"{DOMOTICZ_API_URL}?type=command&param=getuservariables"),
            _get_cached_data(client, _plans_cache, f"{DOMOTICZ_API_URL}?type=command&param=getplans&order=name&used=true"),
            _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=gethardware"),
        )
        sys_info = _domoticz_payload(resp, "Reading Domoticz version")
        hardware = _domoticz_payload(hw_resp, "Reading hardware").get("result", [])
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
        hardware = _domoticz_payload(hw_resp, "Reading hardware health").get("result", [])
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

async def get_all_devices(offset: int = 0, limit: int | None = 50) -> str:
    """Internal: Get all devices."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true&order=Name")
        return json.dumps(_paginated_payload(devices, offset, limit))

async def get_device(idx: int | None = None, name: str | None = None) -> str:
    """Internal: Get specific device."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx: return _error_response("Device not found")
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getdevices&rid={resolved_idx}")
        return _json_response_text(response, "Reading device")

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
        data = _domoticz_payload(response, "Reading room devices")
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

async def get_events(offset: int = 0, limit: int | None = 50) -> str:
    """Internal: Get event overview."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list")
        events = _domoticz_payload(response, "Listing event scripts").get("result", [])
        if not isinstance(events, list):
            _error_response("Listing event scripts failed: result was not a list.")
        return json.dumps(_paginated_payload(events, offset, limit))

async def get_event(event_id: int) -> str:
    """Internal: Get specific event."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=load&event={event_id}")
        return _json_response_text(response, f"Reading event script {event_id}")

async def check_for_updates() -> str:
    """Internal: Check for updates."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=checkforupdate")
        return _json_response_text(response, "Checking for Domoticz updates")

async def get_camera_snapshot(idx: int) -> str:
    """Internal: Get camera snapshot."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_BASE_URL}/camsnapshot.jpg?idx={idx}")
        return json.dumps({"status": "OK", "result": base64.b64encode(response.content).decode("utf-8")})

async def get_hardware() -> str:
    """Internal: Get hardware gateways."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=gethardware")
        return _json_response_text(response, "Reading hardware")

async def get_settings() -> str:
    """Internal: Get settings."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getsettings")
        return _json_response_text(response, "Reading Domoticz settings")

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

async def search_scripts(query: str, offset: int = 0, limit: int | None = 50) -> str:
    """Internal: Search scripts."""
    async with create_client() as client:
        list_resp = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=list")
        scripts = _domoticz_payload(list_resp, "Listing event scripts").get("result", [])
        if not isinstance(scripts, list):
            _error_response("Listing event scripts failed: result was not a list.")
        matches = []
        for script in scripts:
            event_id = script.get("id")
            if event_id is None:
                event_id = script.get("idx")
            if event_id is None:
                _error_response("Domoticz returned an event script without an id.")

            load_resp = await _do_request(
                client,
                "GET",
                f"{DOMOTICZ_API_URL}?type=command&param=events&evparam=load&event={event_id}",
            )
            loaded = _domoticz_payload(load_resp, f"Loading event script {event_id}").get("result", [])
            if not isinstance(loaded, list) or not loaded or not isinstance(loaded[0], dict):
                _error_response(f"Domoticz returned invalid data for event script {event_id}.")
            script_data = loaded[0]
            source = (
                script_data.get("xmlstatement")
                or script_data.get("xml")
                or script_data.get("source")
                or ""
            )
            if query.lower() in str(source).lower():
                matches.append({
                    "id": event_id,
                    "idx": event_id,
                    "Name": script.get("name") or script.get("Name"),
                })
        return json.dumps(_paginated_payload(matches, offset, limit))

async def search_devices(query: str, offset: int = 0, limit: int | None = 50) -> str:
    """Internal: Search devices."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        results = [d for d in devices if query.lower() in d.get("Name", "").lower() or query.lower() in d.get("Data", "").lower()]
        return json.dumps(_paginated_payload(results, offset, limit))

# --- Resources ---

@mcp.resource(
    "domoticz://overview",
    name="get_overview_resource",
    title="Domoticz overview",
    mime_type="application/json",
)
async def get_overview_resource() -> str:
    """Orientation: system version, hardware, and device counts."""
    return await get_overview()

@mcp.resource(
    "domoticz://health",
    name="get_system_health_resource",
    title="Domoticz system health",
    mime_type="application/json",
)
async def get_system_health_resource() -> str:
    """Troubleshooting: hardware health and unresponsive devices."""
    return await get_system_health()

@mcp.resource(
    "domoticz://devices",
    name="get_all_devices_resource",
    title="All Domoticz devices",
    mime_type="application/json",
)
async def get_all_devices_resource() -> str:
    """Large output: state of all Domoticz devices."""
    data = json.loads(await get_all_devices(limit=None))
    data["result"] = [_simplify_device(d) for d in data.get("result", [])]
    return json.dumps(data)

@mcp.resource(
    "domoticz://device/{idx}",
    name="get_device_resource",
    title="Domoticz device by idx",
    mime_type="application/json",
)
async def get_device_resource(idx: int) -> str:
    """Specific: metadata and current state for one device."""
    return await get_device(idx=idx)

@mcp.resource(
    "domoticz://device/{type}/{subtype}/{idx}",
    name="get_device_by_type_resource",
    title="Typed Domoticz device",
    mime_type="application/json",
)
async def get_device_by_type_resource(type: str, subtype: str, idx: int) -> str:
    """Alias: typed device URI; type/subtype are descriptive, idx is authoritative."""
    return await get_device(idx=idx)

@mcp.resource(
    "domoticz://device/name/{name}",
    name="get_device_by_name_resource",
    title="Domoticz device by name",
    mime_type="application/json",
)
async def get_device_by_name_resource(name: str) -> str:
    """Specific: metadata and current state for one named device."""
    return await get_device(name=name)

@mcp.resource(
    "domoticz://scenes",
    name="get_scenes_resource",
    title="Domoticz scenes and groups",
    mime_type="application/json",
)
async def get_scenes_resource() -> str:
    """List: configured scenes and groups."""
    return await get_scenes()

@mcp.resource(
    "domoticz://scene/{idx}",
    name="get_scene_resource",
    title="Domoticz scene by idx",
    mime_type="application/json",
)
async def get_scene_resource(idx: int) -> str:
    """Specific: one configured scene or group."""
    return await get_scene(idx=idx)

@mcp.resource(
    "domoticz://scene/name/{name}",
    name="get_scene_by_name_resource",
    title="Domoticz scene by name",
    mime_type="application/json",
)
async def get_scene_by_name_resource(name: str) -> str:
    """Specific: one named scene or group."""
    return await get_scene(name=name)

@mcp.resource(
    "domoticz://rooms",
    name="get_rooms_resource",
    title="Domoticz rooms",
    mime_type="application/json",
)
async def get_rooms_resource() -> str:
    """List: Room Plans (logical groupings)."""
    return await get_rooms()

@mcp.resource(
    "domoticz://room/{idx}",
    name="get_room_resource",
    title="Domoticz room by idx",
    mime_type="application/json",
)
async def get_room_resource(idx: int) -> str:
    """Group: status of all devices in a room."""
    return await get_room_devices(idx=idx)

@mcp.resource(
    "domoticz://room/{room_name}/{idx}",
    name="get_room_by_name_resource",
    title="Named Domoticz room",
    mime_type="application/json",
)
async def get_room_by_name_resource(room_name: str, idx: int) -> str:
    """Alias: named room URI; idx selects the room."""
    return await get_room_devices(idx=idx)

@mcp.resource(
    "domoticz://user-variables",
    name="get_user_variables_resource",
    title="Domoticz user variables",
    mime_type="application/json",
)
async def get_user_variables_resource() -> str:
    """System: current values of user-defined variables."""
    return await get_user_variables()

@mcp.resource(
    "domoticz://user-variable/{idx}",
    name="get_user_variable_resource",
    title="Domoticz user variable by idx",
    mime_type="application/json",
)
async def get_user_variable_resource(idx: int) -> str:
    """Specific: one user-defined variable."""
    return await get_user_variable(idx=idx)

@mcp.resource(
    "domoticz://user-variable/name/{name}",
    name="get_user_variable_by_name_resource",
    title="Domoticz user variable by name",
    mime_type="application/json",
)
async def get_user_variable_by_name_resource(name: str) -> str:
    """Specific: one named user-defined variable."""
    return await get_user_variable(name=name)

@mcp.resource(
    "domoticz://battery-alerts",
    name="get_battery_levels_resource",
    title="Domoticz battery alerts",
    mime_type="application/json",
)
async def get_battery_levels_resource() -> str:
    """Maintenance: devices with <= 20% battery."""
    return await get_battery_levels(threshold=20)

@mcp.resource(
    "domoticz://events",
    name="get_events_resource",
    title="Domoticz event scripts",
    mime_type="application/json",
)
async def get_events_resource() -> str:
    """Overview: internal event system scripts."""
    return await get_events(limit=None)

@mcp.resource(
    "domoticz://event/{event_id}",
    name="get_event_resource",
    title="Domoticz event script by ID",
    mime_type="application/json",
)
async def get_event_resource(event_id: int) -> str:
    """Analysis: source code for an automation script."""
    return await get_event(event_id)

@mcp.resource(
    "domoticz://hardware",
    name="get_hardware_resource",
    title="Domoticz hardware",
    mime_type="application/json",
)
async def get_hardware_resource() -> str:
    """System: configured hardware gateways."""
    return await get_hardware()

@mcp.resource(
    "domoticz://dashboard",
    name="get_dashboard_resource",
    title="Domoticz dashboard",
    mime_type="application/json",
)
async def get_dashboard_resource() -> str:
    """Curated: favorite and active devices status."""
    async with create_client() as client:
        devices = await _get_cached_data(client, _device_cache, f"{DOMOTICZ_API_URL}?type=command&param=getdevices&filter=all&used=true")
        curated = [_simplify_device(d) for d in devices if d.get("Favorite") == 1 or (d.get("Status", "").lower() not in ["off", "closed", "normal", ""])]
        return json.dumps(curated)

@mcp.resource(
    "domoticz://logs",
    name="get_log_resource",
    title="Domoticz logs",
    mime_type="application/json",
)
async def get_log_resource() -> str:
    """Debug: recent system logs."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getlog&lastlogtime=0&loglevel=268435455")
        return _json_response_text(response, "Reading Domoticz logs")

@mcp.resource(
    "domoticz://log",
    name="get_log_alias_resource",
    title="Domoticz log alias",
    mime_type="application/json",
)
async def get_log_alias_resource() -> str:
    """Alias: recent system logs."""
    return await get_log_resource()

@mcp.resource(
    "domoticz://logs/error",
    name="get_error_logs_resource",
    title="Domoticz error logs",
    mime_type="application/json",
)
async def get_error_logs_resource() -> str:
    """Debug: only 'Error' level log entries."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getlog&lastlogtime=0&loglevel=4")
        return _json_response_text(response, "Reading Domoticz error logs")

@mcp.resource(
    "domoticz://security",
    name="get_security_resource",
    title="Domoticz security status",
    mime_type="application/json",
)
async def get_security_resource() -> str:
    """Status: security panel arming state."""
    async with create_client() as client:
        response = await _do_request(client, "GET", f"{DOMOTICZ_API_URL}?type=command&param=getsecstatus")
        return _json_response_text(response, "Reading security status")

@mcp.resource(
    "domoticz://settings",
    name="get_settings_resource",
    title="Domoticz settings",
    mime_type="application/json",
)
async def get_settings_resource() -> str:
    """Config: global system settings."""
    return await get_settings()

@mcp.resource(
    "domoticz://docs/dzvents_syntax",
    name="get_dzvents_docs",
    title="dzVents syntax guide",
    mime_type="text/plain",
)
async def get_dzvents_docs() -> str:
    """Rules: dzVents automation syntax."""
    return "dzVents Syntax Cheat Sheet: return { on = { devices = { 'My Switch' } }, execute = function(domoticz, device) ... end }"

@mcp.resource(
    "domoticz://docs/blockly_syntax",
    name="get_blockly_docs",
    title="Domoticz Blockly syntax guide",
    mime_type="text/plain",
)
async def get_blockly_docs() -> str:
    """Rules: Blockly XML representation."""
    return "Blockly in Domoticz uses an XML representation: <xml xmlns=\"...\"> ... </xml>"

# --- Tools ---

@mcp.tool(
    title="Start Domoticz OAuth login",
    annotations=_tool_annotations(
        read_only=False,
        destructive=False,
        idempotent=True,
    ),
)
async def start_oauth_login() -> OAuthLoginStartResult:
    """Start or reuse a bounded browser login. Open the returned URL, then poll get_oauth_login_status."""
    return await _start_oauth_login_flow()


@mcp.tool(
    title="Get Domoticz OAuth login status",
    annotations=_tool_annotations(read_only=True),
)
async def get_oauth_login_status(
    flow_id: Annotated[
        str,
        Field(
            min_length=32,
            max_length=128,
            pattern=r"^[A-Za-z0-9_-]+$",
            description="Opaque flow identifier returned by start_oauth_login",
        ),
    ],
) -> OAuthLoginStatusResult:
    """Check a browser login. Retry the original operation only after status is complete."""
    return _get_oauth_login_flow_status(flow_id)


@mcp.tool(
    title="Search event scripts",
    annotations=_tool_annotations(read_only=True),
)
async def search_scripts_tool(
    query: Annotated[str, Field(min_length=1, max_length=200, description="Case-insensitive script source search term")],
    offset: OffsetArg = 0,
    limit: LimitArg = 50,
) -> PaginatedToolResult[ScriptSearchMatch]:
    """Search string in scripts. Cross-ref: `domoticz://events`."""
    response = await search_scripts(query, offset, limit)
    return _structured_json_result(
        response,
        "Searching event scripts",
        PaginatedToolResult[ScriptSearchMatch],
    )


@mcp.tool(
    title="Search devices",
    annotations=_tool_annotations(read_only=True),
)
async def search_devices_tool(
    query: Annotated[str, Field(min_length=1, max_length=200, description="Case-insensitive device name or data search term")],
    offset: OffsetArg = 0,
    limit: LimitArg = 50,
) -> PaginatedToolResult[DomoticzItem]:
    """Search devices by name/status. Cross-ref: `domoticz://devices`."""
    response = await search_devices(query, offset, limit)
    return _structured_json_result(
        response,
        "Searching devices",
        PaginatedToolResult[DomoticzItem],
    )


@mcp.tool(
    title="Get daily energy history",
    annotations=_tool_annotations(read_only=True),
)
async def get_daily_energy_history(
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
    include_instantaneous: Annotated[bool, Field(description="Include instantaneous power samples")] = True,
) -> EnergyHistoryToolResult:
    """Read today's counter history. Preferred: `idx`. Set `include_instantaneous=False` for daily totals."""
    response = await _get_energy_history("day", idx=idx, name=name, include_instantaneous=include_instantaneous)
    return _structured_json_result(response, "Reading daily energy history", EnergyHistoryToolResult)


@mcp.tool(
    title="Get weekly energy history",
    annotations=_tool_annotations(read_only=True),
)
async def get_weekly_energy_history(
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
) -> EnergyHistoryToolResult:
    """Read the last 7 days of counter history. Preferred: `idx`."""
    response = await _get_energy_history("week", idx=idx, name=name)
    return _structured_json_result(response, "Reading weekly energy history", EnergyHistoryToolResult)


@mcp.tool(
    title="Get monthly energy history",
    annotations=_tool_annotations(read_only=True),
)
async def get_monthly_energy_history(
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
) -> EnergyHistoryToolResult:
    """Read this month's counter history. Preferred: `idx`."""
    response = await _get_energy_history("month", idx=idx, name=name)
    return _structured_json_result(response, "Reading monthly energy history", EnergyHistoryToolResult)


@mcp.tool(
    title="Toggle switch",
    annotations=_tool_annotations(read_only=False, idempotent=False),
)
async def toggle_switch(
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
) -> DomoticzToolResult:
    """Toggle device. Preferred: `idx`. Cross-ref: `set_switch_state`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx:
            _error_response("Device not found")
        return await _mutation_result(
            client,
            "GET",
            f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd=Toggle",
            "Toggling switch",
            (_device_cache, _scene_cache, _user_variable_cache),
        )


@mcp.tool(
    title="Set switch state",
    annotations=_tool_annotations(read_only=False),
)
async def set_switch_state(
    state: Annotated[
        str,
        Field(
            pattern=r"^([Oo][Nn]|[Oo][Ff][Ff])$",
            description="Desired switch state: On or Off (case-insensitive)",
        ),
    ],
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
) -> DomoticzToolResult:
    """Set 'On'/'Off'. Preferred: `idx`. Cross-ref: `toggle_switch`."""
    if state.lower() not in ["on", "off"]:
        _error_response("state must be 'On' or 'Off'")
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx:
            _error_response("Device not found")
        return await _mutation_result(
            client,
            "GET",
            f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd={state.capitalize()}",
            "Setting switch state",
            (_device_cache, _scene_cache, _user_variable_cache),
        )


@mcp.tool(
    title="Set dimmer level",
    annotations=_tool_annotations(read_only=False),
)
async def set_dimmer_level(
    level: Annotated[int, Field(ge=0, le=100, description="Brightness percentage")],
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
) -> DomoticzToolResult:
    """Set brightness (0-100). Preferred: `idx`. Cross-ref: `domoticz://device/{idx}`."""
    if not 0 <= level <= 100:
        _error_response("level must be 0-100")
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx:
            _error_response("Device not found")
        return await _mutation_result(
            client,
            "GET",
            f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd=Set%20Level&level={level}",
            "Setting dimmer level",
            (_device_cache, _scene_cache, _user_variable_cache),
        )


@mcp.tool(
    title="Set temperature setpoint",
    annotations=_tool_annotations(read_only=False),
)
async def set_temperature_setpoint(
    setpoint: Annotated[float, Field(ge=-100, le=100, description="Target temperature in degrees Celsius")],
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
) -> DomoticzToolResult:
    """Set target temp. Preferred: `idx`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx:
            _error_response("Device not found")
        return await _mutation_result(
            client,
            "GET",
            f"{DOMOTICZ_API_URL}?type=command&param=setsetpoint&idx={resolved_idx}&setpoint={setpoint}",
            "Setting temperature setpoint",
            (_device_cache, _scene_cache, _user_variable_cache),
        )


@mcp.tool(
    title="Control blinds",
    annotations=_tool_annotations(read_only=False),
)
async def control_blinds(
    command: Annotated[
        str,
        Field(
            pattern=r"^([Oo][Pp][Ee][Nn]|[Cc][Ll][Oo][Ss][Ee]|[Ss][Tt][Oo][Pp])$",
            description="Blind movement command: Open, Close, or Stop (case-insensitive)",
        ),
    ],
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
) -> DomoticzToolResult:
    """'Open'/'Close'/'Stop'. Preferred: `idx`."""
    blind_command = command.capitalize()
    if blind_command not in ["Open", "Close", "Stop"]:
        _error_response("Invalid command")
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx:
            _error_response("Device not found")
        return await _mutation_result(
            client,
            "GET",
            f"{DOMOTICZ_API_URL}?type=command&param=switchlight&idx={resolved_idx}&switchcmd={blind_command}",
            "Controlling blinds",
            (_device_cache, _scene_cache, _user_variable_cache),
        )


@mcp.tool(
    title="Switch scene",
    annotations=_tool_annotations(read_only=False, idempotent=False),
)
async def switch_scene(
    command: Annotated[
        str,
        Field(
            pattern=r"^([Oo][Nn]|[Oo][Ff][Ff]|[Tt][Oo][Gg][Gg][Ll][Ee])$",
            description="Scene or group command: On, Off, or Toggle (case-insensitive)",
        ),
    ],
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
) -> DomoticzToolResult:
    """Activate scene. 'On'/'Off'/'Toggle'. Preferred: `idx`. Cross-ref: `domoticz://scenes`."""
    scene_command = command.capitalize()
    if scene_command not in ["On", "Off", "Toggle"]:
        _error_response("Invalid command")
    async with create_client() as client:
        resolved_idx = await _resolve_scene_idx(client, idx, name)
        if not resolved_idx:
            _error_response("Scene not found")
        return await _mutation_result(
            client,
            "GET",
            _command_url("switchscene", {"idx": resolved_idx, "switchcmd": scene_command}),
            "Switching scene",
            (_device_cache, _scene_cache, _user_variable_cache),
        )


@mcp.tool(
    title="Add user variable",
    annotations=_tool_annotations(read_only=False, destructive=False, idempotent=False),
)
async def add_user_variable(
    name: Annotated[str, Field(min_length=1, max_length=200, description="New user variable name")],
    vtype: Annotated[Literal[0, 1, 2, 3, 4], Field(description="Variable type: 0 integer, 1 float, 2 string, 3 date, 4 time")],
    value: Annotated[str, Field(max_length=2000, description="Initial user variable value")],
) -> DomoticzToolResult:
    """Add var. vtype: 0=Int, 1=Float, 2=Str, 3=Date, 4=Time. Cross-ref: `domoticz://user-variables`."""
    async with create_client() as client:
        return await _mutation_result(
            client,
            "GET",
            _command_url("adduservariable", {"vname": name, "vtype": vtype, "vvalue": value}),
            "Adding user variable",
            (_user_variable_cache,),
        )


@mcp.tool(
    title="Update user variable",
    annotations=_tool_annotations(read_only=False),
)
async def update_user_variable(
    name: Annotated[str, Field(min_length=1, max_length=200, description="Existing user variable name")],
    vtype: Annotated[Literal[0, 1, 2, 3, 4], Field(description="Variable type: 0 integer, 1 float, 2 string, 3 date, 4 time")],
    value: Annotated[str, Field(max_length=2000, description="New user variable value")],
) -> DomoticzToolResult:
    """Update var value. Cross-ref: `domoticz://user-variables`."""
    async with create_client() as client:
        return await _mutation_result(
            client,
            "GET",
            _command_url("updateuservariable", {"vname": name, "vtype": vtype, "vvalue": value}),
            "Updating user variable",
            (_device_cache, _scene_cache, _user_variable_cache),
        )


@mcp.tool(
    title="Delete user variable",
    annotations=_tool_annotations(read_only=False, destructive=True),
)
async def delete_user_variable(
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
    confirm: ConfirmArg = False,
) -> DomoticzToolResult:
    """Delete var. Preferred: `idx`. Requires `confirm=True`."""
    if not confirm:
        _confirmation_required("deleting a user variable")
    async with create_client() as client:
        resolved_idx = await _resolve_user_variable_idx(client, idx, name)
        if not resolved_idx:
            _error_response("User variable not found")
        return await _mutation_result(
            client,
            "GET",
            f"{DOMOTICZ_API_URL}?type=command&param=deleteuservariable&idx={resolved_idx}",
            "Deleting user variable",
            (_user_variable_cache,),
        )


@mcp.tool(
    title="Create event script",
    annotations=_tool_annotations(read_only=False, destructive=False, idempotent=False),
)
async def create_event(
    name: Annotated[str, Field(min_length=1, max_length=200, description="New event script name")],
    interpreter: Annotated[str, Field(min_length=1, max_length=50, description="Domoticz event interpreter")],
    event_type: Annotated[str, Field(min_length=1, max_length=50, description="Domoticz event type")],
    xmlstatement: Annotated[str, Field(min_length=1, max_length=1_000_000, description="Event source or Blockly XML")],
    eventstatus: Annotated[Literal["0", "1"], Field(description="1 to enable the event, 0 to disable it")] = "1",
) -> DomoticzToolResult:
    """Create script. Cross-ref: `domoticz://docs/dzvents_syntax`."""
    async with create_client() as client:
        data = {"evparam": "create", "name": name, "eventstatus": eventstatus, "interpreter": interpreter, "xml": xmlstatement, "eventtype": event_type, "logicarray": ""}
        response = await _do_request(client, "POST", f"{DOMOTICZ_API_URL}?type=command&param=events", data=data)
        return _tool_result(response, "Creating event script")


@mcp.tool(
    title="Update event script",
    annotations=_tool_annotations(read_only=False, destructive=True),
)
async def update_event(
    event_id: Annotated[int, Field(ge=1, description="Canonical Domoticz event ID")],
    name: Annotated[str, Field(min_length=1, max_length=200, description="Event script name")],
    interpreter: Annotated[str, Field(min_length=1, max_length=50, description="Domoticz event interpreter")],
    event_type: Annotated[str, Field(min_length=1, max_length=50, description="Domoticz event type")],
    xmlstatement: Annotated[str, Field(min_length=1, max_length=1_000_000, description="Replacement event source or Blockly XML")],
    eventstatus: Annotated[Literal["0", "1"], Field(description="1 to enable the event, 0 to disable it")] = "1",
    confirm: ConfirmArg = False,
) -> DomoticzToolResult:
    """Update script. Cross-ref: `domoticz://event/{event_id}`. Requires `confirm=True`."""
    if not confirm:
        _confirmation_required("updating an event script")
    async with create_client() as client:
        data = {"evparam": "create", "eventid": str(event_id), "name": name, "eventstatus": eventstatus, "interpreter": interpreter, "xml": xmlstatement, "eventtype": event_type, "logicarray": ""}
        response = await _do_request(client, "POST", f"{DOMOTICZ_API_URL}?type=command&param=events", data=data)
        return _tool_result(response, "Updating event script")


@mcp.tool(
    title="Call raw Domoticz API",
    annotations=_tool_annotations(read_only=False, destructive=True, idempotent=False),
)
async def call_domoticz_api(
    param: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_]+$", description="Domoticz command parameter")],
    kwargs: Annotated[dict[str, str | int | float | bool], Field(description="Additional Domoticz query parameters")],
    confirm: ConfirmArg = False,
) -> DomoticzToolResult:
    """Raw API call. Use if dedicated tools fail. Requires `confirm=True`."""
    if not confirm:
        _confirmation_required("calling the raw Domoticz API")
    async with create_client() as client:
        return await _mutation_result(
            client,
            "GET",
            _command_url(param, kwargs),
            "Calling raw Domoticz API",
            (_device_cache, _scene_cache, _user_variable_cache, _plans_cache),
        )


@mcp.tool(
    title="Restart Domoticz",
    annotations=_tool_annotations(read_only=False, destructive=True, idempotent=False),
)
async def restart_system(
    confirm: ConfirmArg = False,
) -> DomoticzToolResult:
    """Reboot server. Requires `confirm=True`."""
    if not confirm:
        _confirmation_required("restarting Domoticz")
    async with create_client() as client:
        return await _mutation_result(
            client,
            "GET",
            f"{DOMOTICZ_API_URL}?type=command&param=system_reboot",
            "Restarting Domoticz",
            (_device_cache, _scene_cache, _user_variable_cache, _plans_cache),
        )


@mcp.tool(
    title="Rename device",
    annotations=_tool_annotations(read_only=False),
)
async def rename_device(
    new_name: Annotated[str, Field(min_length=1, max_length=200, description="New device name")],
    idx: EntityIdxArg = None,
    old_name: Annotated[str | None, Field(min_length=1, max_length=200, description="Current device name, used when idx is omitted")] = None,
) -> DomoticzToolResult:
    """Rename device. Preferred: `idx`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, old_name)
        if not resolved_idx:
            _error_response("Device not found")
        return await _mutation_result(
            client,
            "GET",
            _command_url("renamedevice", {"name": new_name, "idx": resolved_idx}),
            "Renaming device",
            (_device_cache,),
        )


@mcp.tool(
    title="Hide device",
    annotations=_tool_annotations(read_only=False, destructive=True),
)
async def delete_device(
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
    confirm: ConfirmArg = False,
) -> DomoticzToolResult:
    """Hide device. Preferred: `idx`. Requires `confirm=True`."""
    if not confirm:
        _confirmation_required("hiding a device")
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx:
            _error_response("Device not found")
        return await _mutation_result(
            client,
            "GET",
            f"{DOMOTICZ_API_URL}?type=command&param=setused&used=false&idx={resolved_idx}",
            "Hiding device",
            (_device_cache,),
        )


@mcp.tool(
    title="Create virtual sensor",
    annotations=_tool_annotations(read_only=False, destructive=False, idempotent=False),
)
async def create_virtual_sensor(
    hw_idx: Annotated[int, Field(ge=1, description="Hardware idx that owns the new sensor")],
    sensorname: Annotated[str, Field(min_length=1, max_length=200, description="New virtual sensor name")],
    sensortype: Annotated[int, Field(ge=1, description="Domoticz virtual sensor type number")],
) -> DomoticzToolResult:
    """Create dummy. Cross-ref: `domoticz://hardware`."""
    async with create_client() as client:
        return await _mutation_result(
            client,
            "GET",
            _command_url("createvirtualsensor", {"idx": hw_idx, "sensorname": sensorname, "sensortype": sensortype}),
            "Creating virtual sensor",
            (_device_cache,),
        )


@mcp.tool(
    title="Update device value",
    annotations=_tool_annotations(read_only=False),
)
async def update_device_value(
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
    nvalue: Annotated[int, Field(description="Numeric Domoticz device value")] = 0,
    svalue: Annotated[str, Field(max_length=10_000, description="String Domoticz device value")] = "",
) -> DomoticzToolResult:
    """Manual update. Preferred: `idx`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx:
            _error_response("Device not found")
        return await _mutation_result(
            client,
            "GET",
            _command_url("udevice", {"idx": resolved_idx, "nvalue": nvalue, "svalue": svalue}),
            "Updating device value",
            (_device_cache, _scene_cache, _user_variable_cache),
        )


@mcp.tool(
    title="Add Domoticz log message",
    annotations=_tool_annotations(read_only=False, destructive=False, idempotent=False),
)
async def add_log_message(
    message: Annotated[str, Field(min_length=1, max_length=10_000, description="Message to add to the Domoticz log")],
    level: Annotated[Literal[1, 2, 4], Field(description="Log level: 1 normal, 2 status, 4 error")] = 2,
) -> DomoticzToolResult:
    """Log message. level: 1=Normal, 2=Status, 4=Error. Cross-ref: `domoticz://logs`."""
    async with create_client() as client:
        response = await _do_request(client, "GET", _command_url("addlogmessage", {"message": message, "level": level}))
        return _tool_result(response, "Adding log message")


@mcp.tool(
    title="Send notification",
    annotations=_tool_annotations(read_only=False, destructive=False, idempotent=False),
)
async def send_notification(
    subject: Annotated[str, Field(min_length=1, max_length=500, description="Notification subject")],
    body: Annotated[str, Field(max_length=10_000, description="Notification body")],
) -> DomoticzToolResult:
    """Send notification."""
    async with create_client() as client:
        response = await _do_request(client, "GET", _command_url("sendnotification", {"subject": subject, "body": body}))
        return _tool_result(response, "Sending notification")


@mcp.tool(
    title="Set security status",
    annotations=_tool_annotations(read_only=False, destructive=True),
)
async def set_security_status(
    secstatus: Annotated[Literal[0, 1, 2], Field(description="Security state: 0 disarmed, 1 armed home, 2 armed away")],
    seccode: Annotated[str, Field(min_length=1, max_length=100, description="Domoticz security panel code")],
    confirm: ConfirmArg = False,
) -> DomoticzToolResult:
    """Arm panel. 0=Disarm, 1=Home, 2=Away. Requires `confirm=True`. Cross-ref: `domoticz://security`."""
    if not confirm:
        _confirmation_required("changing security panel status")
    async with create_client() as client:
        return await _mutation_result(
            client,
            "GET",
            _command_url("setsecstatus", {"secstatus": secstatus, "seccode": seccode}),
            "Changing security panel status",
            (_device_cache, _scene_cache, _user_variable_cache),
        )


@mcp.tool(
    title="Set color and brightness",
    annotations=_tool_annotations(read_only=False),
)
async def set_color_brightness(
    hue: Annotated[int, Field(ge=0, le=360, description="Hue in degrees")],
    brightness: Annotated[int, Field(ge=0, le=100, description="Brightness percentage")],
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
    iswhite: Annotated[bool, Field(description="Use white-light mode instead of RGB mode")] = False,
) -> DomoticzToolResult:
    """Set RGB. Preferred: `idx`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx:
            _error_response("Device not found")
        return await _mutation_result(
            client,
            "GET",
            f"{DOMOTICZ_API_URL}?type=command&param=setcolbrightnessvalue&idx={resolved_idx}&hue={hue}&brightness={brightness}&iswhite={str(iswhite).lower()}",
            "Setting color and brightness",
            (_device_cache, _scene_cache, _user_variable_cache),
        )


@mcp.tool(
    title="Set color temperature",
    annotations=_tool_annotations(read_only=False),
)
async def set_color_temperature(
    kelvin: Annotated[int, Field(ge=0, le=100, description="Domoticz normalized white-temperature level")],
    idx: EntityIdxArg = None,
    name: EntityNameArg = None,
) -> DomoticzToolResult:
    """Set normalized white temperature (0-100). Preferred: `idx`."""
    async with create_client() as client:
        resolved_idx = await _resolve_device_idx(client, idx, name)
        if not resolved_idx:
            _error_response("Device not found")
        return await _mutation_result(
            client,
            "GET",
            f"{DOMOTICZ_API_URL}?type=command&param=setkelvinlevel&idx={resolved_idx}&kelvin={kelvin}",
            "Setting color temperature",
            (_device_cache, _scene_cache, _user_variable_cache),
        )

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


async def _run_explicit_authentication() -> str:
    """Complete explicitly requested authentication or fail before server startup."""
    token = await _fetch_oauth_token(
        force_refresh=True,
        old_token=_oauth_token_cache,
        allow_interactive=True,
    )
    if not token:
        raise ToolError(
            "No OAuth token was obtained. Configure DOMOTICZ_CLIENT_ID before using "
            "`--authenticate`, or configure a direct OAuth token or username/password."
        )
    return token


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
    parser.add_argument(
        "--authenticate",
        action="store_true",
        help="Run the bounded interactive OAuth flow before starting the MCP server",
    )

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

    if args.authenticate:
        try:
            asyncio.run(_run_explicit_authentication())
        except ToolError as exc:
            parser.exit(1, f"Authentication failed: {exc}\n")

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

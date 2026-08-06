"""Tests for the MCP client (mcp_client.py) + agent integration.

Uses a tiny fake MCP server (JSON-RPC over stdio) driven through the real
`mcp` SDK, so no network or third-party server is needed.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_client
from mcp_client import McpManager

FAKE_SERVER = """\
import json, sys

TOOLS = [
    {"name": "echo", "description": "Echo text back",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "add", "description": "Add two integers",
     "inputSchema": {"type": "object",
                     "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                     "required": ["a", "b"]}},
]

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()

def recv():
    line = sys.stdin.readline()
    return json.loads(line) if line else None

while True:
    msg = recv()
    if msg is None:
        break
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"protocolVersion": msg["params"]["protocolVersion"],
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "fake", "version": "1.0"}}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = msg["params"]
        name, args = params["name"], params.get("arguments", {})
        if name == "echo":
            text = f"echo:{args.get('text', '')}"
        elif name == "add":
            text = str(int(args.get("a", 0)) + int(args.get("b", 0)))
        else:
            text = "unknown tool"
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"content": [{"type": "text", "text": text}], "isError": False}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
"""


@pytest.fixture
def fake_config(tmp_path):
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({
        "servers": [
            {"name": "fake", "command": sys.executable, "args": [str(script)], "env": {}}
        ]
    }), encoding="utf-8")
    return config


class TestConfig:
    def test_missing_config_yields_no_tools(self, tmp_path):
        mgr = McpManager(config_path=tmp_path / "nope.json")
        assert mgr.list_tools() == []
        assert mgr.configured_servers() == []

    def test_malformed_config_yields_no_tools(self, tmp_path):
        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text("{ not json !", encoding="utf-8")
        mgr = McpManager(config_path=cfg)
        assert mgr.list_tools() == []

    def test_bad_server_entries_skipped(self, tmp_path):
        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "", "command": "npx"},          # no name
            {"name": "x", "command": ""},            # no command
            "not-a-dict",                            # junk entry
        ]}), encoding="utf-8")
        mgr = McpManager(config_path=cfg)
        assert mgr.configured_servers() == []


class TestEndToEnd:
    def test_list_and_call_tools(self, fake_config):
        mgr = McpManager(config_path=fake_config)
        tools = mgr.list_tools()
        assert {t["name"] for t in tools} == {"echo", "add"}
        echo = next(t for t in tools if t["name"] == "echo")
        assert echo["server"] == "fake"

        assert mgr.call_tool("echo", {"text": "hi"}) == "echo:hi"
        assert mgr.call_tool("add", {"a": 2, "b": 3}) == "5"

        assert mgr.has_tool("echo") is True
        assert mgr.has_tool("nope") is False
        mgr.shutdown()

    def test_unknown_tool_raises(self, fake_config):
        mgr = McpManager(config_path=fake_config)
        with pytest.raises(KeyError):
            mgr.call_tool("nope", {})
        mgr.shutdown()

    def test_missing_sdk_is_graceful(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mcp_client, "_sdk_import", lambda: None)
        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "fake", "command": sys.executable, "args": ["x.py"]}
        ]}), encoding="utf-8")
        mgr = McpManager(config_path=cfg)
        # Server start fails cleanly; no tools, failures reported.
        assert mgr.list_tools() == []
        assert mgr.failures()
        mgr.shutdown()


class TestExecutorIntegration:
    def test_executor_mcp_list_and_routing(self, fake_config, monkeypatch):
        from agent import executor

        monkeypatch.setattr("config.profile.is_pro", lambda: True)  # MCP is Pro-gated
        mgr = McpManager(config_path=fake_config)
        monkeypatch.setattr(mcp_client, "get_mcp_manager", lambda config_path=None: mgr)

        listing = executor._call_tool("mcp_list", {}, None)
        assert "echo" in listing and "add" in listing

        routed = executor._call_tool("echo", {"text": "yo"}, None)
        assert routed == "echo:yo"

        unknown = executor._call_tool("definitely_not_a_tool", {}, None)
        assert "Unknown action" in unknown
        mgr.shutdown()

    def test_executor_mcp_list_empty(self, tmp_path, monkeypatch):
        from agent import executor

        monkeypatch.setattr("config.profile.is_pro", lambda: True)
        mgr = McpManager(config_path=tmp_path / "nope.json")
        monkeypatch.setattr(mcp_client, "get_mcp_manager", lambda config_path=None: mgr)
        assert "No MCP tools" in executor._call_tool("mcp_list", {}, None)


class TestServerStatus:
    def test_server_status_not_started(self, tmp_path):
        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "alpha", "command": "npx", "args": ["-y", "pkg"], "env": {}},
            {"name": "beta", "command": "uvx", "args": [], "env": {}},
        ]}), encoding="utf-8")
        mgr = McpManager(config_path=cfg)
        statuses = mgr.server_status()  # must NOT start anything
        assert [s["name"] for s in statuses] == ["alpha", "beta"]
        assert all(not s["started"] for s in statuses)
        assert all(s["tool_count"] == 0 for s in statuses)
        assert all(not s["error"] for s in statuses)
        assert mgr._servers == {}  # nothing was spawned

    def test_server_status_after_start(self, fake_config):
        mgr = McpManager(config_path=fake_config)
        mgr.list_tools()  # starts the fake server
        statuses = mgr.server_status()
        assert len(statuses) == 1
        assert statuses[0]["name"] == "fake"
        assert statuses[0]["started"] is True
        assert statuses[0]["tool_count"] == 2
        assert not statuses[0]["error"]
        mgr.shutdown()

    def test_server_status_reports_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mcp_client, "_sdk_import", lambda: None)
        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "broken", "command": sys.executable, "args": ["nope.py"]}
        ]}), encoding="utf-8")
        mgr = McpManager(config_path=cfg)
        mgr.list_tools()  # attempts start, fails
        statuses = mgr.server_status()
        assert statuses[0]["name"] == "broken"
        assert statuses[0]["started"] is False
        assert statuses[0]["error"]
        mgr.shutdown()

    def test_test_server_unknown(self, fake_config):
        mgr = McpManager(config_path=fake_config)
        result = mgr.test_server("nope")
        assert result["ok"] is False
        assert "not configured" in result["message"]
        mgr.shutdown()

    def test_test_server_connects_single(self, fake_config):
        mgr = McpManager(config_path=fake_config)
        result = mgr.test_server("fake")
        assert result["ok"] is True
        assert result["tool_count"] == 2
        # A second call reuses the running server instead of respawning.
        again = mgr.test_server("fake")
        assert again["ok"] is True
        assert mgr.list_tools() and len(mgr.list_tools()) == 2  # no dupes
        mgr.shutdown()

    def test_test_server_failure_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mcp_client, "_sdk_import", lambda: None)
        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "bad", "command": sys.executable, "args": ["nope.py"]}
        ]}), encoding="utf-8")
        mgr = McpManager(config_path=cfg)
        result = mgr.test_server("bad")
        assert result["ok"] is False
        assert result["message"]
        # Failure is visible in status too.
        assert mgr.server_status()[0]["error"]
        mgr.shutdown()

    def test_test_server_then_start_merges_other_servers(self, tmp_path):
        """A server started via test_server must not starve the remaining
        configured servers when list_tools() later boots everything."""
        script = tmp_path / "srv.py"
        script.write_text(FAKE_SERVER, encoding="utf-8")
        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "fake", "command": sys.executable, "args": [str(script)], "env": {}},
            {"name": "gh", "command": sys.executable, "args": ["nope.py"], "env": {}},
        ]}), encoding="utf-8")
        mgr = McpManager(config_path=cfg)
        assert mgr.test_server("fake")["ok"] is True
        tools = mgr.list_tools()  # must attempt gh too (and merge, not replace)
        assert {t["name"] for t in tools} == {"echo", "add"}
        assert any(f.startswith("gh:") for f in mgr.failures())
        statuses = {s["name"]: s for s in mgr.server_status()}
        assert statuses["fake"]["started"] is True
        assert statuses["gh"]["started"] is False
        assert statuses["gh"]["error"]
        mgr.shutdown()


def _free_port() -> int:
    """Reserve-and-release a random localhost port (race-free enough for tests)."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_server(server) -> None:
    import time
    for _ in range(200):
        if server.started:
            return
        time.sleep(0.05)
    raise RuntimeError("test MCP server did not start")


def _auth_middleware(app, required_token: str, seen: dict | None = None):
    """Pure-ASGI middleware: optionally records headers and/or enforces auth.

    Kept as plain ASGI (not Starlette BaseHTTPMiddleware) so the SSE stream
    used by streamable-HTTP is passed through unmodified.
    """
    class _Mw:
        def __init__(self, inner):
            self.app = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
                if seen is not None:
                    seen.setdefault("auth", []).append(headers.get("authorization", ""))
                    for key in ("x-api-key",):
                        seen.setdefault(key, []).append(headers.get(key, ""))
                if required_token and headers.get("authorization") != f"Bearer {required_token}":
                    body = b'{"detail":"unauthorized"}'
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
                    return
            await self.app(scope, receive, send)

    app.add_middleware(_Mw)
    return app


@pytest.fixture(scope="module")
def http_server():
    """One real FastMCP streamable-HTTP server, shared across the class.

    Requires ``Authorization: Bearer sekrit`` (rejects otherwise) and records
    every Authorization/X-API-Key header it observes so tests can prove the
    client's token + custom-header plumbing. Returning a 401 is what a real
    protected server does — the client handles it as a contained failure.
    """
    from mcp.server.fastmcp import FastMCP
    import uvicorn
    import threading

    mcp = FastMCP("http-test")

    @mcp.tool()
    def echo(text: str) -> str:
        """Echo text back."""
        return f"echo:{text}"

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    seen: dict = {}
    app = _auth_middleware(mcp.streamable_http_app(), required_token="sekrit", seen=seen)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_server(server)
    yield {"url": f"http://127.0.0.1:{port}/mcp", "token": "sekrit", "seen": seen}
    server.should_exit = True
    thread.join(timeout=5)


def _http_config(tmp_path, url: str, **overrides) -> Path:
    entry = {"name": "remote", "transport": "http", "url": url}
    entry.update(overrides)
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(json.dumps({"servers": [entry]}), encoding="utf-8")
    return cfg


class TestHttpTransport:
    """Remote streamable-HTTP MCP servers (transport: "http")."""

    def test_remote_list_and_call_tools(self, tmp_path, http_server):
        mgr = McpManager(config_path=_http_config(tmp_path, http_server["url"], token=http_server["token"]))
        tools = mgr.list_tools()
        assert {t["name"] for t in tools} == {"echo", "add"}
        assert all(t["server"] == "remote" for t in tools)
        assert mgr.call_tool("add", {"a": 2, "b": 3}) == "5"
        assert mgr.call_tool("echo", {"text": "yo"}) == "echo:yo"
        mgr.shutdown()

    def test_token_and_headers_are_sent(self, tmp_path, http_server):
        # Reset the recorded headers so assertions cover ONLY this test's
        # requests, not earlier tests that shared the module-scoped server.
        http_server["seen"].clear()
        mgr = McpManager(config_path=_http_config(
            tmp_path, http_server["url"], token="sekrit",
            headers={"X-API-Key": "abc"},
        ))
        assert {t["name"] for t in mgr.list_tools()} == {"echo", "add"}
        # The server observed the Bearer token AND the custom header on every
        # request (initialize + stream + calls), proving the config plumbing.
        assert "Bearer sekrit" in http_server["seen"]["auth"]
        assert "abc" in http_server["seen"]["x-api-key"]
        mgr.shutdown()

    def test_wrong_token_rejected(self, tmp_path, http_server, monkeypatch):
        monkeypatch.setattr(mcp_client, "HTTP_START_TIMEOUT_S", 2)
        # No token configured -> server 401s; the SDK stalls the handshake,
        # so the manager's start timeout bounds the failure.
        mgr = McpManager(config_path=_http_config(tmp_path, http_server["url"]))
        assert mgr.list_tools() == []
        assert any(f.startswith("remote:") for f in mgr.failures())
        status = mgr.server_status()[0]
        assert status["started"] is False
        assert status["error"]
        mgr.shutdown()

    def test_remote_unreachable_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mcp_client, "HTTP_START_TIMEOUT_S", 2)
        # A port that is guaranteed closed: connection refused fails in ms at
        # the HTTP layer, but the SDK stalls -> bounded by the start timeout.
        port = _free_port()
        mgr = McpManager(config_path=_http_config(tmp_path, f"http://127.0.0.1:{port}/mcp", token="x"))
        assert mgr.list_tools() == []
        assert any(f.startswith("remote:") for f in mgr.failures())
        mgr.shutdown()

    def test_missing_http_sdk_is_graceful(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mcp_client, "_http_sdk_import", lambda: None)
        monkeypatch.setattr(mcp_client, "HTTP_START_TIMEOUT_S", 2)
        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "remote", "transport": "http", "url": "http://127.0.0.1:9/mcp"}
        ]}), encoding="utf-8")
        mgr = McpManager(config_path=cfg)
        assert mgr.list_tools() == []
        assert any(f.startswith("remote:") for f in mgr.failures())
        mgr.shutdown()

    def test_status_shows_transport_and_url(self, tmp_path, http_server):
        mgr = McpManager(config_path=_http_config(tmp_path, http_server["url"], token=http_server["token"]))
        mgr.list_tools()
        status = mgr.server_status()[0]
        assert status["transport"] == "http"
        assert status["url"] == http_server["url"]
        assert status["started"] is True
        assert status["tool_count"] == 2
        mgr.shutdown()

    def test_http_config_validation(self, tmp_path):
        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "no-url", "transport": "http"},
            {"name": "bad-url", "transport": "http", "url": "ftp://x/mcp"},
            {"name": "alias", "transport": "streamable-http", "url": "http://127.0.0.1:9/mcp"},
        ]}), encoding="utf-8")
        mgr = McpManager(config_path=cfg)
        # bad entries skipped; transport aliases parse to the http transport.
        assert mgr.configured_servers() == ["alias"]
        status = mgr.server_status()[0]
        assert status["transport"] == "http"
        assert status["url"] == "http://127.0.0.1:9/mcp"


class TestPlannerIntegration:
    def test_mcp_section_empty_without_tools(self, tmp_path, monkeypatch):
        from agent import planner

        mgr = McpManager(config_path=tmp_path / "nope.json")
        monkeypatch.setattr(mcp_client, "get_mcp_manager", lambda config_path=None: mgr)
        assert planner._mcp_section() == ""

    def test_mcp_section_lists_catalog(self, fake_config, monkeypatch):
        from agent import planner

        monkeypatch.setattr("config.profile.is_pro", lambda: True)
        mgr = McpManager(config_path=fake_config)
        monkeypatch.setattr(mcp_client, "get_mcp_manager", lambda config_path=None: mgr)

        section = planner._mcp_section()
        assert "echo" in section
        assert "mcp_list" in section
        assert "required: text" in section
        mgr.shutdown()

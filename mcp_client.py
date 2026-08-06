"""mcp_client.py — Model Context Protocol (MCP) client for Almighty AI.

Lets the agent call tools from any MCP server — local ``stdio`` processes
(filesystem, GitHub, browser, databases, ...) or **remote HTTP servers** using
the modern streamable-HTTP transport — as if they were native tools.

Servers are declared in ``config/mcp_servers.json`` (gitignored — it may hold
tokens and local paths)::

    {
      "servers": [
        {
          "name": "filesystem",            # stdio (default): spawn a process
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/me/Documents"],
          "env": {}
        },
        {
          "name": "cloud-db",              # remote: streamable-HTTP transport
          "transport": "http",
          "url": "https://mcp.example.com/mcp",
          "token": "..."                    # optional -> Authorization: Bearer
        }
      ]
    }

``transport`` is ``"stdio"`` (default) or ``"http"`` (aliases
``"streamable-http"`` / ``"sse"`` are accepted and use the same
streamable-HTTP client). Remote servers only need ``url`` (http/https),
optional ``token`` (sent as a Bearer header) and optional ``headers``.

Each server runs on its own background thread with a private asyncio event
loop (the official ``mcp`` SDK is async; the app is sync/threaded). Calls are
bridged with ``asyncio.run_coroutine_threadsafe``. The SDK is imported lazily,
so the app runs unchanged when it is not installed — MCP simply reports that
no tools are available.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Optional

CONFIG_PATH = "config/mcp_servers.json"

# How long a server may take to start (spawn + initialize handshake).
SERVER_START_TIMEOUT_S = 30
# Remote HTTP servers are a single round-trip — a stall means auth/reachability
# trouble, so bound their handshake much tighter than a local process spawn.
HTTP_START_TIMEOUT_S = 15
# Default per-call timeout once connected.
CALL_TIMEOUT_S = 120


def _sdk_import():
    """Lazily import the official MCP SDK. Returns (ClientSession, StdioServerParameters, stdio_client) or None."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        return ClientSession, StdioServerParameters, stdio_client
    except Exception:
        return None


def _http_sdk_import():
    """Lazily import the streamable-HTTP client + httpx.

    Returns ``(streamable_http_client, httpx)`` or None. Handles both the
    current ``streamable_http_client`` and the older (deprecated)
    ``streamablehttp_client`` spelling so a range of SDK versions work.
    """
    try:
        from mcp.client.streamable_http import streamable_http_client
    except Exception:
        try:
            from mcp.client.streamable_http import (
                streamablehttp_client as streamable_http_client,
            )
        except Exception:
            return None
    try:
        import httpx
    except Exception:
        return None
    return streamable_http_client, httpx


def _is_http_transport(transport: str) -> bool:
    return (transport or "").strip().lower() in {"http", "streamable-http", "sse"}


class _SdkServer:
    """One connected MCP server, driven by the SDK on a dedicated loop thread.

    ``transport`` selects the connection strategy: ``"stdio"`` spawns
    ``command``/``args`` as a child process, ``"http"`` connects to a remote
    ``url`` with the streamable-HTTP transport (Bearer ``token`` if given).
    """

    def __init__(
        self,
        name: str,
        command: str = "",
        args: list | None = None,
        env: dict | None = None,
        transport: str = "stdio",
        url: str = "",
        headers: dict | None = None,
    ):
        self.name = name
        self.transport = "http" if _is_http_transport(transport) else "stdio"
        self.url = url or ""
        self.tools: list[dict] = []
        self._command = command
        self._args = list(args or [])
        self._env = dict(env or {})
        self._headers = dict(headers or {})
        self._stack: Optional[asyncio.AbstractContextManager] = None
        self._http_client = None
        self._session = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._start_error: Optional[Exception] = None

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self) -> None:
        if self.is_running():
            return  # already running — never spawn a second loop
        # Retry-safe: clear any previous failure state before respawning.
        self._start_error = None
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name=f"mcp-{self.name}", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=SERVER_START_TIMEOUT_S):
            raise TimeoutError(f"MCP server '{self.name}' did not start in {SERVER_START_TIMEOUT_S}s")
        if self._start_error is not None:
            raise self._start_error

    def is_running(self) -> bool:
        # A thread can outlive a failed start for a moment (it still has to
        # close its loop after signalling _ready); a failed server is never
        # "running", so also require that no start error was recorded.
        return (
            self._thread is not None
            and self._thread.is_alive()
            and self._start_error is None
        )

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            # Bound the handshake itself. The mcp SDK can stall forever on a
            # failed HTTP handshake (a rejected/refused POST deadlocks the
            # awaiting request); wait_for makes the thread exit promptly so
            # close() never waits on a zombie worker.
            connect_timeout = HTTP_START_TIMEOUT_S if self.transport == "http" else SERVER_START_TIMEOUT_S
            self._loop.run_until_complete(
                asyncio.wait_for(self._connect_async(), timeout=connect_timeout)
            )
        except BaseException as exc:  # surfaced via _ready
            # BaseException, not Exception: the SDK's anyio cancel scopes can
            # re-raise a raw CancelledError (a BaseException) when the stalled
            # handshake is cancelled — that must never escape the worker thread.
            if isinstance(exc, asyncio.CancelledError):
                exc = TimeoutError(
                    f"MCP server '{self.name}' did not connect in {connect_timeout}s"
                )
            self._start_error = exc
            self._ready.set()
            try:
                self._loop.close()
            except BaseException:
                pass
            return
        try:
            self._loop.run_forever()
        finally:
            try:
                # Let lingering SDK tasks wind down on their own first (clean
                # context exits, e.g. httpx responses get closed); cancel only
                # what is genuinely stuck (a session that deadlocked on close).
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                if pending:
                    self._loop.run_until_complete(
                        asyncio.wait(pending, timeout=1.0, return_when=asyncio.ALL_COMPLETED)
                    )
                    stuck = [t for t in pending if not t.done()]
                    for task in stuck:
                        task.cancel()
                    if stuck:
                        self._loop.run_until_complete(
                            asyncio.wait(stuck, timeout=2.0, return_when=asyncio.ALL_COMPLETED)
                        )
            except BaseException:
                pass
            try:
                self._loop.close()
            except BaseException:
                pass

    async def _connect_async(self) -> None:
        from contextlib import AsyncExitStack

        self._stack = AsyncExitStack()
        if self.transport == "http":
            await self._connect_http()
        else:
            await self._connect_stdio()

    async def _connect_stdio(self) -> None:
        _sdk = _sdk_import()
        if _sdk is None:
            raise RuntimeError("mcp package not installed — cannot start MCP servers")
        ClientSession, StdioServerParameters, stdio_client = _sdk

        env = None
        if self._env:
            env = dict(os.environ)
            env.update({k: str(v) for k, v in self._env.items()})

        params = StdioServerParameters(command=self._command, args=self._args, env=env)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

        await self._load_tools()

    async def _connect_http(self) -> None:
        """Connect to a remote MCP server over streamable HTTP."""
        sdk = _http_sdk_import()
        if sdk is None:
            raise RuntimeError("mcp/httpx package not installed — cannot connect to HTTP MCP servers")
        streamable_http_client, httpx = sdk
        from mcp import ClientSession  # noqa: PLC0415

        client = httpx.AsyncClient(
            headers=dict(self._headers) or None,
            timeout=httpx.Timeout(30.0, read=300.0),
        )
        self._http_client = client
        read, write, _get_session_id = await self._stack.enter_async_context(
            streamable_http_client(self.url, http_client=client)
        )
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

        await self._load_tools()

    async def _load_tools(self) -> None:
        listed = await self._session.list_tools()
        self.tools = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": getattr(tool, "inputSchema", None) or {},
                "server": self.name,
            }
            for tool in listed.tools
        ]
        self._ready.set()

        listed = await self._session.list_tools()
        self.tools = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": getattr(tool, "inputSchema", None) or {},
                "server": self.name,
            }
            for tool in listed.tools
        ]
        self._ready.set()

    # ── calls ───────────────────────────────────────────────────────────────
    def _submit(self, coro, timeout: float):
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError(f"MCP server '{self.name}' is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            # Cancel the hung coroutine so a stuck server does not stall every
            # later call to this server.
            self._loop.call_soon_threadsafe(future.cancel)
            raise

    def call(self, tool_name: str, arguments: dict, timeout: float = CALL_TIMEOUT_S) -> str:
        result = self._submit(self._session.call_tool(tool_name, arguments or {}), timeout)
        return _format_tool_result(result)

    def close(self) -> None:
        if self._loop is not None and self._loop.is_running():
            try:
                self._submit(self._close_async(), timeout=3)
            except Exception:
                pass
            # Guarantee the loop exits even if the SDK session deadlocked on
            # close (it can shield the aclose); _run_loop drains the residue.
            # Guarded: a healthy close may already have stopped+closed the
            # loop by the time we get here.
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    async def _close_async(self) -> None:
        # Best-effort graceful close with a tight bound: the SDK's session
        # aclose can flakily stall, and the _run_loop drain finishes whatever
        # this leaves behind — no reason to make shutdown wait on it.
        if self._stack is not None:
            try:
                await asyncio.wait_for(self._stack.aclose(), timeout=1.5)
            except Exception:
                pass
        if self._http_client is not None:
            try:
                await asyncio.wait_for(self._http_client.aclose(), timeout=1.5)
            except Exception:
                pass
        if self._loop is not None:
            # Schedule the stop rather than calling it directly: stopping from
            # inside the running loop would race the task-completion callback
            # and the caller's future would never resolve.
            self._loop.call_soon_threadsafe(self._loop.stop)


def _format_tool_result(result) -> str:
    """Flatten an SDK CallToolResult into a string for the agent."""
    try:
        parts = []
        for block in getattr(result, "content", []) or []:
            btype = getattr(block, "type", "text")
            if btype == "text":
                parts.append(getattr(block, "text", "") or "")
            elif btype == "resource":
                parts.append(f"[resource: {getattr(block, 'uri', '')}]")
            else:
                parts.append(f"[{btype} content]")
        text = "\n".join(p for p in parts if p)
        if not text:
            structured = getattr(result, "structuredContent", None)
            if structured:
                text = json.dumps(structured, ensure_ascii=False)[:4000]
        if bool(getattr(result, "isError", False)):
            text = f"MCP tool error: {text or 'unknown error'}"
        return text or "MCP tool returned no output."
    except Exception as exc:
        return f"MCP tool result could not be read: {exc}"


class McpManager:
    """Aggregates tools from all configured MCP servers."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = Path(config_path) if config_path else Path(CONFIG_PATH)
        self._servers: dict[str, _SdkServer] = {}
        self._lock = threading.Lock()
        self._started = False
        self._start_attempted = False
        self._failures: list[str] = []

    # ── config ──────────────────────────────────────────────────────────────
    def _load_servers(self) -> dict[str, _SdkServer]:
        servers: dict[str, _SdkServer] = {}
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return servers  # missing or malformed config -> no MCP tools

        for entry in raw.get("servers", []) if isinstance(raw, dict) else []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            transport = str(entry.get("transport") or "stdio").strip().lower()
            if _is_http_transport(transport):
                # Remote streamable-HTTP server: needs a resolvable URL.
                url = str(entry.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    continue
                headers = dict(entry.get("headers") or {})
                token = str(entry.get("token") or "").strip()
                # HTTP header names are case-insensitive: never emit a second
                # Authorization header when the user already set one.
                if token and not any(k.lower() == "authorization" for k in headers):
                    headers["Authorization"] = f"Bearer {token}"
                servers[name] = _SdkServer(
                    name=name, transport="http", url=url, headers=headers
                )
                continue
            # Local stdio server: spawn a process.
            command = str(entry.get("command") or "").strip()
            if not command:
                continue
            servers[name] = _SdkServer(
                name=name,
                command=command,
                args=entry.get("args") or [],
                env=entry.get("env") or {},
            )
        return servers

    def configured_servers(self) -> list[str]:
        return sorted(self._load_servers())

    def server_status(self) -> list[dict]:
        """Per-configured-server status without starting anything.

        Returns a list of dicts (sorted by name) suitable for a UI status
        panel::

            [{"name", "transport", "command", "url", "started", "tool_count", "error"}]

        ``transport`` is ``"stdio"`` or ``"http"``; ``command`` is the launch
        command (stdio) and ``url`` the remote endpoint (http, empty for
        stdio). ``started`` reflects whether that server's loop thread is
        currently alive; ``tool_count`` is 0 until the server has connected;
        ``error`` is the most recent start-failure message for that server
        ("" when healthy or never attempted).
        """
        configured = self._load_servers()
        statuses = []
        for name in sorted(configured):
            server = configured[name]
            live = self._servers.get(name)
            error = next(
                (f.partition(":")[2].strip() for f in self._failures if f.startswith(f"{name}:")),
                "",
            )
            statuses.append({
                "name": name,
                "transport": server.transport,
                "command": server._command,
                "url": server.url,
                "started": bool(live is not None and live.is_running()),
                "tool_count": len(live.tools) if live else 0,
                "error": error,
            })
        return statuses

    def test_server(self, name: str) -> dict:
        """Start (or check) one configured server; never raises.

        Returns ``{"ok": bool, "message": str, "tool_count": int}``. Starts
        only the named server — other configured servers are left alone, so a
        UI "Test Connection" button never triggers a full npx spawn-all.

        On success the server stays connected in the shared manager, which is
        intended: after a live test the agent can immediately use its tools
        (a later ``list_tools()`` merges the remaining configured servers).
        """
        configured = self._load_servers()
        if name not in configured:
            return {"ok": False, "message": f"Server '{name}' is not configured", "tool_count": 0}

        with self._lock:
            # Lock is held across start() so a concurrent _ensure_started can
            # never pass its own is_alive() guard and double-spawn the same
            # server.
            live = self._servers.get(name)
            if live is not None and live.is_running():
                return {"ok": True, "message": "Connected", "tool_count": len(live.tools)}
            server = configured[name]
            self._servers[name] = server
            try:
                server.start()
                return {"ok": True, "message": "Connected", "tool_count": len(server.tools)}
            except Exception as exc:
                err = f"{name}: {exc}"
                if err not in self._failures:
                    self._failures.append(err)
                return {"ok": False, "message": str(exc), "tool_count": 0}

    # ── startup ─────────────────────────────────────────────────────────────
    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            # Merge configured servers into _servers. A server already present
            # (e.g. started earlier by test_server) is kept as-is; anything
            # newly configured since the last load is added so it also starts.
            configured = self._load_servers()
            for name, server in configured.items():
                self._servers.setdefault(name, server)
            # Deliberate: once startup is attempted, _started stays True even if
            # every server failed, so we never hammer a broken config with
            # repeated spawn attempts for the lifetime of the process.
            self._started = True
            for name, server in self._servers.items():
                try:
                    server.start()
                except Exception as exc:
                    self._failures.append(f"{name}: {exc}")
                    print(f"[MCP] Server '{name}' failed to start: {exc}")

    def failures(self) -> list[str]:
        return list(self._failures)

    # ── tools ───────────────────────────────────────────────────────────────
    def list_tools(self) -> list[dict]:
        """All MCP tools; this is what triggers the (lazy) server start."""
        self._ensure_started()
        tools: list[dict] = []
        for server in self._servers.values():
            tools.extend(server.tools)
        return sorted(tools, key=lambda t: t["name"])

    def has_tool(self, name: str) -> bool:
        """Non-starting lookup: only sees tools of already-started servers.

        Deliberately does not spawn servers — callers like the executor's
        unknown-tool fallback must not boot every npx server just because the
        model guessed a wrong tool name. Discovery paths (list_tools) start
        servers, and call_tool starts them if genuinely needed.
        """
        if not self._started:
            return False
        return any(t["name"] == name for t in self._cached_tools())

    def _cached_tools(self) -> list[dict]:
        tools: list[dict] = []
        for server in self._servers.values():
            tools.extend(server.tools)
        return tools

    def call_tool(self, name: str, arguments: dict, timeout: float = CALL_TIMEOUT_S) -> str:
        self._ensure_started()
        for server in self._servers.values():
            if any(t["name"] == name for t in server.tools):
                return server.call(name, arguments or {}, timeout=timeout)
        raise KeyError(f"MCP tool '{name}' is not available on any configured server")

    def call_tool_if_exists(self, name: str, arguments: dict) -> str | None:
        """Call an MCP tool by name, or None when it is not available."""
        if not self.has_tool(name):
            return None
        return self.call_tool(name, arguments)

    def shutdown(self) -> None:
        for server in self._servers.values():
            server.close()
        self._servers.clear()
        self._started = False
        self._failures = []


_mcp_manager: Optional[McpManager] = None


def reset_mcp_manager() -> None:
    """Drop the shared singleton (mainly for tests)."""
    global _mcp_manager
    if _mcp_manager is not None:
        try:
            _mcp_manager.shutdown()
        except Exception:
            pass
    _mcp_manager = None


def get_mcp_manager(config_path: Optional[Path] = None) -> McpManager:
    """Shared process-wide McpManager (lazily created once).

    ``config_path`` is honored only on first creation. For isolated instances
    in tests, construct ``McpManager`` directly instead.
    """
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = McpManager(config_path=config_path)
    return _mcp_manager

"""dashboard/net_utils.py — LAN IP selection + reachability for Mobile Connect.

The phone scans a QR that encodes ``http://<LAN-IP>:8000/auto-login?key=...``.
Two things make that URL fail with "can't load page":

1. The chosen IP belongs to a *virtual* interface the phone can never route to
   (Docker/``br-*`` bridges, Hyper-V ``vEthernet``, VPN tunnels like Tailscale/
   WireGuard, virtualbox/vmnet, ...). This happens whenever the default-route
   probe fails and the code falls back to an arbitrary interface list.
2. Nothing is actually listening on that IP:port (server down, or the IP is a
   stale/aliased address).

This module fixes both: candidates are ranked (default route first, physical
private interfaces next, virtual/loopback/link-local excluded), and the caller
can probe candidates on the dashboard port to pick the first *reachable* one.

Pure stdlib — safe to import anywhere (no FastAPI/psutil required).
"""

from __future__ import annotations

import re
import socket
from typing import Iterable, Optional

# Virtual interfaces that a phone on the LAN can never route to.
_VIRTUAL_IFACE_RE = re.compile(
    r"^(docker|veth|br-|virbr|vmnet|vbox|tailscale|utun|tun[0-9]|tap[0-9]|"
    r"wg[0-9]|ppp[0-9]|hamachi|zerotier|zt[0-9]|vEthernet|isatap|teredo|"
    r"loopback|lo|docker_gwbridge|sit[0-9]|gif[0-9]|ipsec|pan[0-9])",
    re.IGNORECASE,
)

# Well-known addresses used to find the default-route interface. The gateway
# is probed last so machines without internet still resolve their LAN IP.
_DEFAULT_PROBE_HOSTS = ("8.8.8.8", "1.1.1.1", "192.168.1.1")


def is_private_ipv4(ip: str) -> bool:
    try:
        parts = [int(p) for p in ip.split(".")]
        if len(parts) != 4:
            return False
        a, b, c, d = parts
        if a == 10:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        if a == 192 and b == 168:
            return True
        return False
    except Exception:
        return False


def is_virtual_interface(name: str) -> bool:
    """True for interface names that are VPN/tunnel/container bridges.

    Keeps physical adapters (wlan*, eth*, enp*, Ethernet, Wi-Fi) intact.
    """
    return bool(name) and bool(_VIRTUAL_IFACE_RE.match(name or ""))


def _default_route_ip(probe_hosts: Iterable[str]) -> Optional[str]:
    """Outgoing-interface IP via a UDP connect — the OS's own route choice."""
    for host in probe_hosts:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((host, 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            try:
                s.close()
            except Exception:
                pass
    return None


def _interface_map() -> dict:
    """name -> list of IPv4 strings, best-effort (no exceptions)."""
    mapping: dict[str, list[str]] = {}
    try:
        import psutil
        for name, addrs in psutil.net_if_addrs().items():
            ips = [
                getattr(a, "address", "")
                for a in addrs
                if getattr(a, "family", None) and getattr(a.family, "name", "") == "AF_INET"
            ]
            mapping[name] = [ip for ip in ips if ip]
    except Exception:
        pass
    return mapping


def candidate_lan_ips(
    iface_map: Optional[dict] = None,
    probe_hosts: Iterable[str] = _DEFAULT_PROBE_HOSTS,
) -> list[str]:
    """Ordered LAN-facing IPv4 candidates, virtual/loopback/link-local excluded.

    Order: default-route probe IP, physical private IPs, other physical IPs.
    ``iface_map`` may be injected for tests (name -> [IPv4 strings]).
    """
    mapping = iface_map if iface_map is not None else _interface_map()

    def _usable(ip: str) -> bool:
        if not ip or ip in ("127.0.0.1", "0.0.0.0"):
            return False
        if ip.startswith("127.") or ip.startswith("169.254."):
            return False
        return True

    ordered: list[str] = []

    def _add(ip: str) -> None:
        if _usable(ip) and ip not in ordered:
            ordered.append(ip)

    route_ip = _default_route_ip(probe_hosts)
    if route_ip:
        _add(route_ip)

    physical_private: list[str] = []
    physical_other: list[str] = []
    for name, ips in mapping.items():
        if is_virtual_interface(name):
            continue
        for ip in ips:
            if not _usable(ip):
                continue
            (physical_private if is_private_ipv4(ip) else physical_other).append(ip)

    # If the default-route probe succeeded it already heads the list.
    for ip in physical_private:
        _add(ip)
    for ip in physical_other:
        _add(ip)

    return ordered


def pick_lan_ip(
    iface_map: Optional[dict] = None,
    probe_hosts: Iterable[str] = _DEFAULT_PROBE_HOSTS,
) -> str:
    """Best LAN IP for the QR, or '127.0.0.1' when no usable candidate exists."""
    candidates = candidate_lan_ips(iface_map=iface_map, probe_hosts=probe_hosts)
    return candidates[0] if candidates else "127.0.0.1"


def reachable_lan_ip(
    port: int,
    preferred: Optional[str] = None,
    timeout: float = 0.8,
    max_probes: int = 6,
) -> tuple[str, str]:
    """Return (ip, warning): the first candidate that accepts TCP on ``port``.

    ``preferred`` (the previously chosen IP) is tried first so the URL stays
    stable across refreshes. When nothing answers, falls back to the best
    candidate with a non-empty warning so callers can surface the problem
    instead of showing a QR that cannot work.
    """
    candidates = candidate_lan_ips()
    if preferred and preferred not in candidates and preferred != "127.0.0.1":
        candidates.insert(0, preferred)

    if not candidates:
        return "127.0.0.1", "No LAN IP detected — phone cannot reach this computer."

    for ip in candidates[:max_probes]:
        if _tcp_reachable(ip, port, timeout):
            return ip, ""

    return candidates[0], (
        f"Phone can't reach {candidates[0]}:{port}. Check that both devices are on "
        "the same Wi-Fi network and that port 8000 is open in the firewall."
    )


def _tcp_reachable(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False

"""Tests for dashboard.net_utils (LAN IP selection + pairing reachability)."""

import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.net_utils import (
    candidate_lan_ips,
    is_private_ipv4,
    is_virtual_interface,
    pick_lan_ip,
    reachable_lan_ip,
)


class TestVirtualInterfaceFilter:
    @pytest.mark.parametrize(
        "name",
        ["docker0", "veth1234", "br-6417d633629", "virbr0", "vmnet8", "tailscale0",
         "vEthernet (Default Switch)", "utun3", "wg0", "tap0", "hamachi", "zerotier",
         "lo", "isatap", "docker_gwbridge"],
    )
    def test_virtual_names_flagged(self, name):
        assert is_virtual_interface(name), f"{name!r} should be virtual"

    @pytest.mark.parametrize(
        "name",
        ["wlan0", "wlp5s0", "eth0", "enp3s0", "Ethernet", "Wi-Fi", "en0", ""],
    )
    def test_physical_names_kept(self, name):
        assert not is_virtual_interface(name), f"{name!r} should be physical"


class TestCandidateSelection:
    IFACES = {
        "lo": ["127.0.0.1"],
        "docker0": ["172.17.0.1"],
        "br-abc": ["172.20.0.1"],
        "tailscale0": ["100.97.24.91"],
        "wlp5s0": ["172.20.20.20"],
        "eth0": ["192.168.1.50"],
        "vboxnet0": ["10.0.3.1"],
    }

    def test_virtuals_and_loopback_excluded(self):
        ips = candidate_lan_ips(iface_map=self.IFACES, probe_hosts=())
        assert "172.17.0.1" not in ips          # docker
        assert "172.20.0.1" not in ips          # br- bridge
        assert "100.97.24.91" not in ips        # tailscale
        assert "10.0.3.1" not in ips            # virtualbox
        assert "127.0.0.1" not in ips           # loopback
        assert ips == ["172.20.20.20", "192.168.1.50"]

    def test_no_candidates_falls_back_to_loopback(self):
        assert pick_lan_ip(iface_map={}, probe_hosts=()) == "127.0.0.1"

    def test_private_ipv4_ranges(self):
        assert is_private_ipv4("10.1.2.3")
        assert is_private_ipv4("172.16.0.1")
        assert is_private_ipv4("172.31.255.255")
        assert is_private_ipv4("192.168.1.1")
        assert not is_private_ipv4("172.15.0.1")
        assert not is_private_ipv4("8.8.8.8")
        assert not is_private_ipv4("nope")


class TestReachability:
    def _listener(self):
        """Local TCP listener; returns (port, stop_event)."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def _accept():
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                pass

        threading.Thread(target=_accept, daemon=True).start()
        return srv, port

    def test_picks_reachable_candidate(self, monkeypatch):
        srv, port = self._listener()
        try:
            monkeypatch.setattr(
                "dashboard.net_utils.candidate_lan_ips",
                lambda: ["127.0.0.1", "10.0.0.99"],
            )
            ip, warning = reachable_lan_ip(port=port, timeout=1.0)
            assert ip == "127.0.0.1"
            assert warning == ""
        finally:
            srv.close()

    def test_preferred_candidate_tried_first(self, monkeypatch):
        srv, port = self._listener()
        try:
            monkeypatch.setattr(
                "dashboard.net_utils.candidate_lan_ips",
                lambda: ["10.0.0.99", "127.0.0.1"],
            )
            # Preferred IP heads the list even though it is not in candidates.
            ip, warning = reachable_lan_ip(port=port, preferred="127.0.0.1", timeout=1.0)
            assert ip == "127.0.0.1"
            assert warning == ""
        finally:
            srv.close()

    def test_none_reachable_returns_warning(self, monkeypatch):
        monkeypatch.setattr(
            "dashboard.net_utils.candidate_lan_ips",
            lambda: ["10.0.0.99", "192.168.9.9"],
        )
        ip, warning = reachable_lan_ip(port=59999, timeout=0.3, max_probes=2)
        assert ip == "10.0.0.99"          # best candidate, but flagged
        assert "can't reach" in warning

    def test_no_candidates_warns(self, monkeypatch):
        monkeypatch.setattr("dashboard.net_utils.candidate_lan_ips", lambda: [])
        ip, warning = reachable_lan_ip(port=8000)
        assert ip == "127.0.0.1"
        assert "No LAN IP" in warning


class TestPairing:
    def test_new_pairing_returns_reachable_url(self, monkeypatch):
        import dashboard.server as server_mod

        monkeypatch.setattr(server_mod, "reachable_lan_ip", lambda port, preferred: ("10.0.0.5", ""))
        d = server_mod.DashboardServer()
        url, key, auto, manual, warning = d.new_pairing()
        assert url == "http://10.0.0.5:8000"
        assert manual == url
        assert auto == f"http://10.0.0.5:8000/auto-login?key={key}"
        assert warning == ""
        assert len(key) == 6

    def test_new_pairing_propagates_warning(self, monkeypatch):
        import dashboard.server as server_mod

        monkeypatch.setattr(
            server_mod, "reachable_lan_ip",
            lambda port, preferred: ("10.0.0.5", "Phone can't reach 10.0.0.5:8000"),
        )
        d = server_mod.DashboardServer()
        url, key, auto, manual, warning = d.new_pairing()
        assert "can't reach" in warning

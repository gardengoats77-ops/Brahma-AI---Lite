"""
System Monitor for REX
Provides real-time system health metrics: CPU, RAM, disk, network, and processes.
"""

import os
import platform
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from core.error_handler import log_error


def _bytes_human(n: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _get_cpu_info() -> str:
    """Gather CPU metrics."""
    try:
        physical_cores = psutil.cpu_count(logical=False) or 0
        logical_cores = psutil.cpu_count(logical=True) or 0
        usage = psutil.cpu_percent(interval=1)
        freq = psutil.cpu_freq()
        freq_str = f"{freq.current:.0f} MHz" if freq else "N/A"
        load = os.getloadavg()
        return (
            f"CPU\n"
            f"   Usage:     {usage}%\n"
            f"   Cores:     {physical_cores} physical / {logical_cores} logical\n"
            f"   Frequency: {freq_str}\n"
            f"   Load Avg:  {load[0]:.2f} (1m)  {load[1]:.2f} (5m)  {load[2]:.2f} (15m)\n"
        )
    except Exception as e:
        log_error(e, context="actions.system_monitor._get_cpu_info", severity="warning")
        return "CPU: Unable to retrieve info\n"


def _get_memory_info() -> str:
    """Gather RAM and swap metrics."""
    try:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        lines = [
            "Memory (RAM)\n",
            f"   Total:     {_bytes_human(vm.total)}",
            f"   Used:      {_bytes_human(vm.used)} ({vm.percent}%)",
            f"   Available: {_bytes_human(vm.available)}",
            "",
            "Swap\n",
            f"   Total:     {_bytes_human(sw.total)}",
            f"   Used:      {_bytes_human(sw.used)} ({sw.percent}%)",
            f"   Free:      {_bytes_human(sw.free)}",
        ]
        return "\n".join(lines) + "\n"
    except Exception as e:
        log_error(e, context="actions.system_monitor._get_memory_info", severity="warning")
        return "Memory: Unable to retrieve info\n"


def _get_disk_info() -> str:
    """Gather disk usage for all mounted partitions."""
    try:
        lines = ["Disk Usage"]
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                pct_bar = int(usage.percent // 5)
                bar = "\u2588" * pct_bar + "\u2591" * (20 - pct_bar)
                lines.append(
                    f"\n   [{part.device}] {part.mountpoint}\n"
                    f"   {bar} {usage.percent}%\n"
                    f"   Used: {_bytes_human(usage.used)}  Free: {_bytes_human(usage.free)}  Total: {_bytes_human(usage.total)}"
                )
            except PermissionError:
                continue
        # Disk I/O
        try:
            io = psutil.disk_io_counters()
            if io:
                lines.append(
                    f"\n\n   I/O (since boot)\n"
                    f"   Read:  {_bytes_human(io.read_bytes)}\n"
                    f"   Write: {_bytes_human(io.write_bytes)}"
                )
        except Exception as _e:
            log_error(_e, context="system_monitor._get_disk_info", severity="warning")
        return "\n".join(lines) + "\n"
    except Exception as e:
        log_error(e, context="actions.system_monitor._get_disk_info", severity="warning")
        return "Disk: Unable to retrieve info\n"


def _get_network_info() -> str:
    """Gather network interface and connectivity info."""
    try:
        lines = ["Network"]
        # Connectivity check
        try:
            s = socket.create_connection(("8.8.8.8", 53), timeout=3)
            s.close()
            lines.append("   Status:     Online")
        except (OSError, socket.timeout):
            lines.append("   Status:     Offline")

        # Interfaces
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        for iface, addr_list in addrs.items():
            if iface == "lo":
                continue
            for addr in addr_list:
                if addr.family == socket.AF_INET:
                    st = stats.get(iface)
                    speed = f"{st.speed} Mbps" if st and st.speed > 0 else "N/A"
                    lines.append(
                        f"\n   [{iface}]  {addr.address}\n"
                        f"   Speed: {speed}  |  {'Up' if st and st.isup else 'Down'}"
                    )
                    break

        # I/O counters
        try:
            io = psutil.net_io_counters()
            if io:
                lines.append(
                    f"\n\n   Traffic (since boot)\n"
                    f"   Sent:     {_bytes_human(io.bytes_sent)}\n"
                    f"   Received: {_bytes_human(io.bytes_recv)}\n"
                    f"   Packets:  {io.packets_sent} sent / {io.packets_recv} recv"
                )
        except Exception as _e:
            log_error(_e, context="system_monitor._get_network_info", severity="warning")
        return "\n".join(lines) + "\n"
    except Exception as e:
        log_error(e, context="actions.system_monitor._get_network_info", severity="warning")
        return "Network: Unable to retrieve info\n"


def _get_process_list(top_n: int = 15) -> str:
    """Get top processes by CPU and memory usage."""
    try:
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                info = p.info
                procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort by CPU, then memory
        by_cpu = sorted(procs, key=lambda x: x.get("cpu_percent") or 0, reverse=True)[:top_n]
        by_mem = sorted(procs, key=lambda x: x.get("memory_percent") or 0, reverse=True)[:top_n]

        lines = [f"Top Processes (by CPU, top {top_n})\n"]
        lines.append(f"   {'PID':>7}  {'CPU%':>5}  {'RAM%':>5}  {'Status':<10}  Name")
        lines.append(f"   {'-'*7}  {'-'*5}  {'-'*5}  {'-'*10}  {'-'*20}")
        for p in by_cpu:
            lines.append(
                f"   {p['pid']:>7}  {(p.get('cpu_percent') or 0):>5.1f}  "
                f"{(p.get('memory_percent') or 0):>5.1f}  "
                f"{(p.get('status') or '?'):<10}  {p.get('name', '?')}"
            )

        lines.append(f"\nTop Processes (by Memory, top {top_n})\n")
        lines.append(f"   {'PID':>7}  {'CPU%':>5}  {'RAM%':>5}  {'Status':<10}  Name")
        lines.append(f"   {'-'*7}  {'-'*5}  {'-'*5}  {'-'*10}  {'-'*20}")
        for p in by_mem:
            lines.append(
                f"   {p['pid']:>7}  {(p.get('cpu_percent') or 0):>5.1f}  "
                f"{(p.get('memory_percent') or 0):>5.1f}  "
                f"{(p.get('status') or '?'):<10}  {p.get('name', '?')}"
            )

        total = len(procs)
        running = sum(1 for p in procs if p.get("status") == "running")
        sleeping = sum(1 for p in procs if p.get("status") == "sleeping")
        lines.append(f"\n   Total: {total} processes  |  Running: {running}  |  Sleeping: {sleeping}")

        return "\n".join(lines) + "\n"
    except Exception as e:
        log_error(e, context="actions.system_monitor._get_process_list", severity="warning")
        return "Processes: Unable to retrieve info\n"


def _get_system_summary() -> str:
    """Get basic system info: OS, uptime, boot time."""
    try:
        boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
        uptime_sec = time.time() - psutil.boot_time()
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        mins = int((uptime_sec % 3600) // 60)
        uptime_str = f"{days}d {hours}h {mins}m"

        lines = [
            "System Info",
            f"   OS:        {platform.system()} {platform.release()} ({platform.machine()})",
            f"   Hostname:  {platform.node()}",
            f"   Python:    {platform.python_version()}",
            f"   Uptime:    {uptime_str}",
            f"   Boot Time: {boot.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
        ]
        return "\n".join(lines)
    except Exception as e:
        log_error(e, context="actions.system_monitor._get_system_summary", severity="warning")
        return "System Info: Unable to retrieve\n"


def system_monitor(parameters: dict = None, player=None) -> str:
    """
    Main entry point for the system monitor action.

    Parameters:
        scope: "full" (default) | "cpu" | "memory" | "disk" | "network" | "processes" | "summary"
        top_n: Number of top processes to show (default 15)
    """
    params = parameters or {}
    scope = params.get("scope", "full").lower()
    top_n = int(params.get("top_n", 15))

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"REX System Monitor  |  {timestamp}\n" + "=" * 50 + "\n\n"

    sections = {
        "summary":    _get_system_summary,
        "cpu":        _get_cpu_info,
        "memory":     _get_memory_info,
        "disk":       _get_disk_info,
        "network":    _get_network_info,
        "processes":  lambda: _get_process_list(top_n),
    }

    if scope == "full":
        parts = []
        for key in ("summary", "cpu", "memory", "disk", "network", "processes"):
            parts.append(sections[key]())
        result = header + "\n".join(parts)
    elif scope in sections:
        result = header + sections[scope]()
    else:
        result = f"Unknown scope: '{scope}'. Available: full, {', '.join(sections.keys())}"

    if player:
        try:
            player.write_log(result)
        except Exception as _e:
            log_error(_e, context="system_monitor.system_monitor", severity="warning")

    return result

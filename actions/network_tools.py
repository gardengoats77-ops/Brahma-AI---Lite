"""
Network Tools for REX
Provides network scanning, analysis, and utility capabilities.
"""

import json
import os
import socket
import subprocess
import sys
import ipaddress
from pathlib import Path
from datetime import datetime
import time

import requests
from core.error_handler import log_error


def ping_host(host: str, count: int = 4) -> str:
    """Ping a host and report latency."""
    try:
        param = "-n" if os.name == "nt" else "-c"
        result = subprocess.run(
            ["ping", param, str(count), host],
            capture_output=True, text=True, timeout=30
        )
        output = f"🏓 Ping Report: {host}\n"
        output += "=" * 50 + "\n\n"
        if result.returncode == 0:
            output += "✅ Host is reachable\n\n"
            for line in result.stdout.split('\n'):
                if 'time=' in line.lower() or 'ttl=' in line.lower():
                    output += f"   {line.strip()}\n"
        else:
            output += "❌ Host unreachable or request timed out\n"
        output += f"\n📋 Raw Output:\n{result.stdout}"
        return output
    except subprocess.TimeoutExpired:
        return f"❌ Ping to {host} timed out"
    except Exception as e:
        return f"❌ Ping error: {e}"


def port_scan(host: str, ports: str = "common", timeout: float = 1.0) -> str:
    """Scan common ports on a target host."""
    if ports == "common":
        port_list = [21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 993, 995,
                     1433, 1521, 3306, 3389, 5432, 5900, 8080, 8443]
    elif "-" in ports:
        start, end = map(int, ports.split("-"))
        port_list = list(range(start, min(end + 1, 65536)))
    elif "," in ports:
        port_list = [int(p.strip()) for p in ports.split(",")]
    else:
        port_list = [int(ports)]

    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror:
        return f"❌ Cannot resolve hostname: {host}"

    output = f"🔍 Port Scan Report: {host} ({ip})\n"
    output += "=" * 50 + "\n\n"
    output += f"Scanning {len(port_list)} ports...\n\n"

    open_ports = []
    common_services = {
        21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
        80: "HTTP", 110: "POP3", 143: "IMAP", 443: "HTTPS", 445: "SMB",
        993: "IMAPS", 995: "POP3S", 1433: "MSSQL", 1521: "Oracle",
        3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL", 5900: "VNC",
        8080: "HTTP-Alt", 8443: "HTTPS-Alt"
    }

    for port in port_list:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            if result == 0:
                service = common_services.get(port, "Unknown")
                open_ports.append((port, service))
                output += f"✅ Port {port}: OPEN ({service})\n"
            sock.close()
        except Exception as _e:
            log_error(_e, context="actions.network_tools", severity="warning")

    if not open_ports:
        output += "\n⚠️ No open ports found in scan range\n"
    else:
        output += f"\n📊 Summary: {len(open_ports)} open ports found\n"

    return output


def traceroute(host: str, max_hops: int = 30) -> str:
    """Perform traceroute to a host."""
    try:
        cmd = ["tracert", "-d", "-w", "1000", "-h", str(max_hops), host]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = f"🗺️ Traceroute to {host}\n"
        output += "=" * 50 + "\n\n"
        output += result.stdout
        return output
    except subprocess.TimeoutExpired:
        return f"❌ Traceroute to {host} timed out"
    except Exception as e:
        return f"❌ Traceroute error: {e}"


def dns_lookup(domain: str) -> str:
    """Perform comprehensive DNS lookup."""
    output = f"🌐 DNS Lookup: {domain}\n"
    output += "=" * 50 + "\n\n"

    try:
        ips = socket.getaddrinfo(domain, None, socket.AF_INET)
        unique_ips = list(set(addr[4][0] for addr in ips))
        output += f"📡 A Records (IPv4):\n"
        for ip in unique_ips:
            output += f"   • {ip}\n"
        output += "\n"
    except Exception as e:
        output += f"❌ A Record lookup failed: {e}\n\n"

    try:
        ips_v6 = socket.getaddrinfo(domain, None, socket.AF_INET6)
        unique_ipv6 = list(set(addr[4][0] for addr in ips_v6))
        if unique_ipv6:
            output += f"📡 AAAA Records (IPv6):\n"
            for ip in unique_ipv6:
                output += f"   • {ip}\n"
            output += "\n"
    except Exception as _e:
        log_error(_e, context="actions.network_tools", severity="warning")

    try:
        result = subprocess.run(
            ["nslookup", "-type=ns", domain],
            capture_output=True, text=True, timeout=10
        )
        ns_records = [line.split(":")[-1].strip()
                     for line in result.stdout.split('\n')
                     if 'nameserver' in line.lower()]
        if ns_records:
            output += f"🖥️ Name Servers:\n"
            for ns in ns_records:
                output += f"   • {ns}\n"
            output += "\n"
    except Exception as _e:
        log_error(_e, context="actions.network_tools", severity="warning")

    try:
        result = subprocess.run(
            ["nslookup", "-type=mx", domain],
            capture_output=True, text=True, timeout=10
        )
        mx_records = []
        for line in result.stdout.split('\n'):
            if 'mail exchanger' in line.lower():
                parts = line.split("=")
                if len(parts) > 1:
                    mx_records.append(parts[1].strip())
        if mx_records:
            output += f"📧 Mail Servers (MX):\n"
            for mx in mx_records:
                output += f"   • {mx}\n"
            output += "\n"
    except Exception as _e:
        log_error(_e, context="actions.network_tools", severity="warning")

    return output


def network_speed_test() -> str:
    """Test network speed using a public API."""
    output = "⚡ Network Speed Test\n"
    output += "=" * 50 + "\n\n"

    output += "📥 Testing download speed (simple HTTP)...\n"
    try:
        start = time.time()
        resp = requests.get("http://speedtest.tele2.net/10MB.zip", timeout=30, stream=True)
        downloaded = 0
        for chunk in resp.iter_content(chunk_size=8192):
            downloaded += len(chunk)
        elapsed = time.time() - start
        speed = (downloaded / elapsed) / 1_000_000
        output += f"   Download: {speed:.2f} Mbps ({downloaded / 1_000_000:.2f} MB in {elapsed:.2f}s)\n"
    except Exception as e:
        output += f"   ❌ Download test failed: {e}\n"

    return output


def local_network_scan() -> str:
    """Scan the local network for connected devices."""
    try:
        import netifaces
    except ImportError:
        return "❌ netifaces not installed. Install with: pip install netifaces"

    output = "🖥️ Local Network Scan\n"
    output += "=" * 50 + "\n\n"

    interfaces = netifaces.interfaces()
    output += f"📡 Network Interfaces: {len(interfaces)}\n\n"

    for iface in interfaces:
        if iface == 'lo' or iface.startswith('veth'):
            continue

        addrs = netifaces.ifaddresses(iface)
        if netifaces.AF_INET in addrs:
            ip = addrs[netifaces.AF_INET][0]['addr']
            netmask = addrs[netifaces.AF_INET][0].get('netmask', '255.255.255.0')

            output += f"🔌 Interface: {iface}\n"
            output += f"   IP: {ip}\n"
            output += f"   Netmask: {netmask}\n"

            try:
                network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
                output += f"   Network: {network}\n"

                output += "\n🔍 Scanning network (this may take a moment)...\n"
                devices = []

                for host in range(1, 255):
                    test_ip = f"{network.network_address}.{host}"
                    try:
                        param = "-n" if os.name == "nt" else "-c"
                        result = subprocess.run(
                            ["ping", param, "1", "-w", "100", test_ip],
                            capture_output=True, timeout=2
                        )
                        if result.returncode == 0:
                            try:
                                hostname = socket.gethostbyaddr(test_ip)[0]
                            except Exception:
                                hostname = "Unknown"
                            devices.append((test_ip, hostname))
                    except Exception as _e:
                        log_error(_e, context="actions.network_tools", severity="warning")

                if devices:
                    output += f"\n✅ Found {len(devices)} devices:\n"
                    for dev_ip, name in devices:
                        output += f"   • {dev_ip} ({name})\n"
                else:
                    output += "   ⚠️ No devices found\n"

            except Exception as e:
                output += f"   Network calculation error: {e}\n"

            output += "\n"

    return output


def http_headers(url: str) -> str:
    """Fetch and display HTTP headers from a URL."""
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True)

        output = f"🌐 HTTP Headers: {url}\n"
        output += "=" * 50 + "\n\n"
        output += f"📊 Status: {resp.status_code} {resp.reason}\n\n"
        output += "📋 Response Headers:\n"

        for key, value in resp.headers.items():
            output += f"   {key}: {value}\n"

        security_headers = {
            'Strict-Transport-Security': 'HSTS',
            'Content-Security-Policy': 'CSP',
            'X-Frame-Options': 'X-Frame-Options',
            'X-Content-Type-Options': 'X-Content-Type-Options',
            'X-XSS-Protection': 'XSS Protection',
            'Referrer-Policy': 'Referrer Policy',
            'Permissions-Policy': 'Permissions Policy',
        }

        output += "\n🔒 Security Headers:\n"
        for header, name in security_headers.items():
            if header in resp.headers:
                output += f"   ✅ {name}: Present\n"
            else:
                output += f"   ❌ {name}: Missing\n"

        return output
    except Exception as e:
        return f"❌ HTTP header fetch failed: {e}"


def ssl_certificate_check(domain: str) -> str:
    """Check SSL certificate details for a domain."""
    import ssl
    import datetime

    output = f"🔐 SSL Certificate Report: {domain}\n"
    output += "=" * 50 + "\n\n"

    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()

                output += "✅ Certificate is VALID\n\n"

                subject = dict(x[0] for x in cert.get('subject', []))
                output += f"👤 Subject:\n"
                output += f"   • Common Name: {subject.get('commonName', 'N/A')}\n"
                output += f"   • Organization: {subject.get('organizationName', 'N/A')}\n"

                issuer = dict(x[0] for x in cert.get('issuer', []))
                output += f"\n🏢 Issuer:\n"
                output += f"   • Organization: {issuer.get('organizationName', 'N/A')}\n"
                output += f"   • Common Name: {issuer.get('commonName', 'N/A')}\n"

                not_before = datetime.datetime.strptime(cert['notBefore'], '%b %d %H:%M:%S %Y %Z')
                not_after = datetime.datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')
                days_remaining = (not_after - datetime.datetime.now()).days

                output += f"\n📅 Validity:\n"
                output += f"   • Valid From: {not_before.strftime('%Y-%m-%d')}\n"
                output += f"   • Valid Until: {not_after.strftime('%Y-%m-%d')}\n"
                output += f"   • Days Remaining: {days_remaining}\n"

                if days_remaining < 30:
                    output += f"   ⚠️ WARNING: Certificate expires soon!\n"

                san = cert.get('subjectAltName', [])
                if san:
                    output += f"\n🔗 Subject Alternative Names:\n"
                    for san_type, value in san[:10]:
                        output += f"   • {value}\n"

                serial = cert.get('serialNumber', 'N/A')
                output += f"\n🔢 Serial Number: {serial}\n"

    except ssl.SSLCertVerificationError as e:
        output += f"❌ SSL Certificate Verification Failed:\n{e}\n"
    except socket.timeout:
        output += "❌ Connection timed out\n"
    except Exception as e:
        output += f"❌ SSL check failed: {e}\n"

    return output


# Tool definitions for registration
NETWORK_TOOLS = [
    {
        "name": "net_ping",
        "description": "Pings a host and reports reachability and latency.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "host": {"type": "STRING", "description": "Host to ping (IP or hostname)"},
                "count": {"type": "INTEGER", "description": "Number of pings (default: 4)"}
            },
            "required": ["host"]
        }
    },
    {
        "name": "net_port_scan",
        "description": "Scans ports on a target host to find open services. Use 'common' for standard ports, '1-1024' for range, or '22,80,443' for specific ports.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "host": {"type": "STRING", "description": "Target host (IP or hostname)"},
                "ports": {"type": "STRING", "description": "Port range: 'common', '1-1024', or '22,80,443'"},
                "timeout": {"type": "NUMBER", "description": "Timeout per port in seconds (default: 1.0)"}
            },
            "required": ["host"]
        }
    },
    {
        "name": "net_traceroute",
        "description": "Performs traceroute to trace the network path to a destination.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "host": {"type": "STRING", "description": "Destination host (IP or hostname)"},
                "max_hops": {"type": "INTEGER", "description": "Maximum hops (default: 30)"}
            },
            "required": ["host"]
        }
    },
    {
        "name": "net_dns_lookup",
        "description": "Performs comprehensive DNS lookup including A, AAAA, NS, MX, and TXT records.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "domain": {"type": "STRING", "description": "Domain to lookup (e.g., example.com)"}
            },
            "required": ["domain"]
        }
    },
    {
        "name": "net_speed_test",
        "description": "Tests network connection speed including download and ping.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "net_local_scan",
        "description": "Scans the local network for connected devices. Shows network interfaces and discovered hosts.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "net_http_headers",
        "description": "Fetches HTTP headers from a URL and checks for security headers.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {"type": "STRING", "description": "URL to check (e.g., https://example.com)"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "net_ssl_check",
        "description": "Checks SSL certificate details for a domain including validity, issuer, expiration, and SANs.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "domain": {"type": "STRING", "description": "Domain to check (e.g., example.com)"}
            },
            "required": ["domain"]
        }
    },
]


def handle_network_tool(tool_name: str, parameters: dict, speak=None) -> str:
    """Route network tool calls to appropriate functions."""
    try:
        if tool_name == "net_ping":
            return ping_host(parameters.get("host", ""), parameters.get("count", 4))
        elif tool_name == "net_port_scan":
            return port_scan(parameters.get("host", ""), parameters.get("ports", "common"), parameters.get("timeout", 1.0))
        elif tool_name == "net_traceroute":
            return traceroute(parameters.get("host", ""), parameters.get("max_hops", 30))
        elif tool_name == "net_dns_lookup":
            return dns_lookup(parameters.get("domain", ""))
        elif tool_name == "net_speed_test":
            return network_speed_test()
        elif tool_name == "net_local_scan":
            return local_network_scan()
        elif tool_name == "net_http_headers":
            return http_headers(parameters.get("url", ""))
        elif tool_name == "net_ssl_check":
            return ssl_certificate_check(parameters.get("domain", ""))
        else:
            return f"❌ Unknown network tool: {tool_name}"
    except Exception as e:
        return f"❌ Network tool error: {e}"

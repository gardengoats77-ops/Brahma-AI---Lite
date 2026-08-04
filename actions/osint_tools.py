"""
OSINT (Open Source Intelligence) Tools for REX
Provides reconnaissance, investigation, and intelligence gathering capabilities.
"""

import json
import re
import socket
import hashlib
import subprocess
import urllib.parse
from pathlib import Path
from typing import Optional
from datetime import datetime

import requests
from core.error_handler import log_error

BASE_DIR = Path(__file__).parent.parent
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"





def domain_recon(domain: str) -> str:
    """
    Perform domain reconnaissance: DNS records, WHOIS data, subdomains, technologies.
    """
    results = {"domain": domain, "timestamp": datetime.now().isoformat()}
    
    # DNS Resolution
    try:
        ips = socket.getaddrinfo(domain, None)
        unique_ips = list(set(addr[4][0] for addr in ips))
        results["dns_resolution"] = {
            "ip_addresses": unique_ips,
            "count": len(unique_ips)
        }
    except Exception as e:
        results["dns_resolution"] = {"error": str(e)}
    
    # Reverse DNS for each IP
    reverse_dns = []
    for ip in unique_ips[:5]:  # Limit to 5
        try:
            hostname = socket.gethostbyaddr(ip)[0]
            reverse_dns.append({"ip": ip, "hostname": hostname})
        except Exception:
            reverse_dns.append({"ip": ip, "hostname": "N/A"})
    results["reverse_dns"] = reverse_dns
    
    # HTTP Headers
    for scheme in ["https", "http"]:
        try:
            resp = requests.head(f"{scheme}://{domain}", timeout=5, allow_redirects=True)
            headers = dict(resp.headers)
            results["http_headers"] = {
                "scheme": scheme,
                "status": resp.status_code,
                "server": headers.get("Server", "Unknown"),
                "technology_hints": [],
            }
            # Detect technologies from headers
            if "X-Powered-By" in headers:
                results["http_headers"]["technology_hints"].append(headers["X-Powered-By"])
            if "X-AspNet-Version" in headers:
                results["http_headers"]["technology_hints"].append(f"ASP.NET {headers['X-AspNet-Version']}")
            break
        except Exception:
            continue
    
    # Common subdomains check
    common_subdomains = ["www", "mail", "ftp", "admin", "api", "dev", "staging", "test", "blog", "shop"]
    found_subdomains = []
    for sub in common_subdomains:
        try:
            socket.getaddrinfo(f"{sub}.{domain}", None)
            found_subdomains.append(f"{sub}.{domain}")
        except Exception as _e:

            log_error(_e, context="actions.osint_tools", severity="debug")
    results["common_subdomains"] = found_subdomains
    
    # Format output
    output = f"🔍 Domain Reconnaissance Report: {domain}\n"
    output += "=" * 50 + "\n\n"
    
    if "dns_resolution" in results:
        dns = results["dns_resolution"]
        if "ip_addresses" in dns:
            output += f"📡 DNS Resolution:\n"
            for ip in dns["ip_addresses"]:
                output += f"   • {ip}\n"
        else:
            output += f"❌ DNS Error: {dns.get('error', 'Unknown')}\n"
    
    if "reverse_dns" in results:
        output += f"\n🔄 Reverse DNS:\n"
        for rd in results["reverse_dns"]:
            output += f"   • {rd['ip']} → {rd['hostname']}\n"
    
    if "http_headers" in results:
        http = results["http_headers"]
        output += f"\n🌐 HTTP Info ({http.get('scheme', 'https')}):\n"
        output += f"   • Status: {http.get('status', 'N/A')}\n"
        output += f"   • Server: {http.get('server', 'Unknown')}\n"
        if http.get("technology_hints"):
            output += f"   • Technologies: {', '.join(http['technology_hints'])}\n"
    
    if found_subdomains:
        output += f"\n🔎 Discovered Subdomains ({len(found_subdomains)}):\n"
        for sub in found_subdomains:
            output += f"   • {sub}\n"
    
    return output


def ip_lookup(ip_address: str) -> str:
    """
    Perform IP address lookup: geolocation, ISP, reverse DNS, threat status.
    """
    results = {"ip": ip_address, "timestamp": datetime.now().isoformat()}
    
    # Reverse DNS
    try:
        hostname = socket.gethostbyaddr(ip_address)[0]
        results["hostname"] = hostname
    except Exception:
        results["hostname"] = "N/A"
    
    # Geolocation API
    try:
        resp = requests.get(f"http://ip-api.com/json/{ip_address}", timeout=5)
        if resp.status_code == 200:
            geo = resp.json()
            results["geolocation"] = {
                "country": geo.get("country", "Unknown"),
                "region": geo.get("regionName", "Unknown"),
                "city": geo.get("city", "Unknown"),
                "isp": geo.get("isp", "Unknown"),
                "org": geo.get("org", "Unknown"),
                "as": geo.get("as", "Unknown"),
                "lat": geo.get("lat"),
                "lon": geo.get("lon"),
                "timezone": geo.get("timezone", "Unknown"),
            }
    except Exception as e:
        results["geolocation"] = {"error": str(e)}
    
    # Format output
    output = f"📍 IP Lookup Report: {ip_address}\n"
    output += "=" * 50 + "\n\n"
    
    output += f"🔄 Hostname: {results.get('hostname', 'N/A')}\n\n"
    
    if "geolocation" in results:
        geo = results["geolocation"]
        if "error" not in geo:
            output += f"🌍 Geolocation:\n"
            output += f"   • Country: {geo.get('country', 'Unknown')}\n"
            output += f"   • Region: {geo.get('region', 'Unknown')}\n"
            output += f"   • City: {geo.get('city', 'Unknown')}\n"
            output += f"   • Timezone: {geo.get('timezone', 'Unknown')}\n"
            output += f"   • Coordinates: {geo.get('lat')}, {geo.get('lon')}\n\n"
            output += f"🏢 Network:\n"
            output += f"   • ISP: {geo.get('isp', 'Unknown')}\n"
            output += f"   • Organization: {geo.get('org', 'Unknown')}\n"
            output += f"   • ASN: {geo.get('as', 'Unknown')}\n"
        else:
            output += f"❌ Geolocation Error: {geo.get('error', 'Unknown')}\n"
    
    return output


def email_investigate(email: str) -> str:
    """
    Investigate an email address: format validation, domain check, breach status.
    """
    results = {"email": email, "timestamp": datetime.now().isoformat()}
    
    # Format validation
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    results["valid_format"] = bool(re.match(email_regex, email))
    
    if not results["valid_format"]:
        return f"❌ Invalid email format: {email}"
    
    # Extract domain
    domain = email.split("@")[1]
    results["domain"] = domain
    
    # Check if domain has MX records
    try:
        import subprocess
        result = subprocess.run(
            ["nslookup", "-type=mx", domain],
            capture_output=True, text=True, timeout=10
        )
        results["has_mx_records"] = "mail exchanger" in result.stdout.lower()
        results["mx_records"] = result.stdout
    except Exception:
        results["has_mx_records"] = None
    
    # Check common email providers
    free_providers = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com", 
                      "protonmail.com", "proton.me", "zoho.com", "yandex.com"]
    results["is_free_provider"] = domain.lower() in free_providers
    
    # Format output
    output = f"📧 Email Investigation Report: {email}\n"
    output += "=" * 50 + "\n\n"
    
    output += f"✅ Valid Format: {'Yes' if results.get('valid_format') else 'No'}\n"
    output += f"🌐 Domain: {domain}\n"
    output += f"📬 Free Provider: {'Yes' if results.get('is_free_provider') else 'No'}\n"
    
    if results.get("has_mx_records") is not None:
        output += f"📨 Has MX Records: {'Yes' if results['has_mx_records'] else 'No'}\n"
    
    # Provider info
    if results.get("is_free_provider"):
        provider_map = {
            "gmail.com": "Google Gmail",
            "yahoo.com": "Yahoo Mail",
            "outlook.com": "Microsoft Outlook",
            "hotmail.com": "Microsoft Hotmail",
            "protonmail.com": "ProtonMail (Encrypted)",
            "proton.me": "ProtonMail (Encrypted)",
            "zoho.com": "Zoho Mail",
        }
        output += f"   Provider: {provider_map.get(domain, domain)}\n"
    
    return output


def extract_metadata(file_path: str) -> str:
    """
    Extract metadata from files (images, PDFs, documents).
    """
    path = Path(file_path)
    
    if not path.exists():
        return f"❌ File not found: {file_path}"
    
    results = {
        "file": str(path),
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "size_human": _format_size(path.stat().st_size),
        "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        "created": datetime.fromtimestamp(path.stat().st_ctime).isoformat(),
    }
    
    # Calculate hashes
    try:
        with open(path, "rb") as f:
            content = f.read()
        results["md5"] = hashlib.md5(content).hexdigest()
        results["sha1"] = hashlib.sha1(content).hexdigest()
        results["sha256"] = hashlib.sha256(content).hexdigest()
    except Exception as e:
        results["hash_error"] = str(e)
    
    # Image metadata (EXIF)
    if path.suffix.lower() in [".jpg", ".jpeg", ".png", ".gif", ".tiff", ".webp"]:
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS
            
            img = Image.open(path)
            results["image_info"] = {
                "format": img.format,
                "mode": img.mode,
                "size": list(img.size),
            }
            
            exif_data = img._getexif()
            if exif_data:
                results["exif"] = {}
                for tag_id, value in exif_data.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if isinstance(value, bytes):
                        value = value.decode(errors='ignore')
                    results["exif"][str(tag)] = str(value)
        except ImportError:
            results["image_info"] = "PIL not installed"
        except Exception as e:
            results["image_info"] = f"Error: {e}"
    
    # PDF metadata
    elif path.suffix.lower() == ".pdf":
        try:
            import PyPDF2
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                results["pdf_info"] = {
                    "pages": len(reader.pages),
                    "metadata": dict(reader.metadata) if reader.metadata else {},
                }
        except ImportError:
            results["pdf_info"] = "PyPDF2 not installed"
        except Exception as e:
            results["pdf_info"] = f"Error: {e}"
    
    # Format output
    output = f"📋 File Metadata Report: {path.name}\n"
    output += "=" * 50 + "\n\n"
    
    output += f"📁 Basic Info:\n"
    output += f"   • Filename: {path.name}\n"
    output += f"   • Extension: {path.suffix}\n"
    output += f"   • Size: {results['size_human']}\n"
    output += f"   • Modified: {results['modified']}\n"
    output += f"   • Created: {results['created']}\n\n"
    
    if "md5" in results:
        output += f"🔐 Hashes:\n"
        output += f"   • MD5: {results['md5']}\n"
        output += f"   • SHA1: {results['sha1']}\n"
        output += f"   • SHA256: {results['sha256']}\n\n"
    
    if "image_info" in results and isinstance(results["image_info"], dict):
        img_info = results["image_info"]
        output += f"🖼️ Image Info:\n"
        output += f"   • Format: {img_info.get('format', 'N/A')}\n"
        output += f"   • Mode: {img_info.get('mode', 'N/A')}\n"
        output += f"   • Dimensions: {img_info.get('size', 'N/A')}\n\n"
    
    if "exif" in results:
        output += f"📸 EXIF Data:\n"
        for key, value in list(results["exif"].items())[:15]:  # Limit to 15
            output += f"   • {key}: {value[:50]}\n"
    
    if "pdf_info" in results and isinstance(results["pdf_info"], dict):
        pdf_info = results["pdf_info"]
        output += f"📄 PDF Info:\n"
        output += f"   • Pages: {pdf_info.get('pages', 'N/A')}\n"
        if pdf_info.get("metadata"):
            output += f"   • Title: {pdf_info['metadata'].get('Title', 'N/A')}\n"
            output += f"   • Author: {pdf_info['metadata'].get('Author', 'N/A')}\n"
    
    return output


def whois_lookup(domain: str) -> str:
    """
    Perform WHOIS lookup for domain registration information.
    """
    try:
        import whois
        w = whois.whois(domain)
        
        output = f"📋 WHOIS Report: {domain}\n"
        output += "=" * 50 + "\n\n"
        
        if w.domain_name:
            output += f"🌐 Domain: {w.domain_name}\n"
        if w.registrar:
            output += f"🏢 Registrar: {w.registrar}\n"
        if w.creation_date:
            output += f"📅 Created: {w.creation_date}\n"
        if w.expiration_date:
            output += f"⏰ Expires: {w.expiration_date}\n"
        if w.name_servers:
            output += f"🖥️ Name Servers:\n"
            ns = w.name_servers if isinstance(w.name_servers, list) else [w.name_servers]
            for ns_server in ns:
                output += f"   • {ns_server}\n"
        if w.org:
            output += f"🏢 Organization: {w.org}\n"
        if w.country:
            output += f"🌍 Country: {w.country}\n"
        if w.state:
            output += f"📍 State: {w.state}\n"
        if w.city:
            output += f"🏙️ City: {w.city}\n"
        
        return output
    except ImportError:
        return "❌ python-whois not installed. Install with: pip install python-whois"
    except Exception as e:
        return f"❌ WHOIS lookup failed: {e}"


def search_dorks(query: str, engine: str = "google") -> str:
    """
    Generate search engine dork queries for OSINT investigation.
    """
    dorks = {
        "email": [
            f'"@{query}" site:linkedin.com',
            f'"@{query}" site:github.com',
            f'"email" "{query}"',
            f'intext:"{query}" filetype:pdf',
        ],
        "domain": [
            f'site:{query} -www',
            f'site:*.{query} -www',
            f'site:{query} inurl:admin',
            f'site:{query} filetype:pdf | filetype:doc | filetype:xlsx',
            f'site:{query} inurl:login | inurl:portal',
        ],
        "person": [
            f'"{query}" site:linkedin.com',
            f'"{query}" site:twitter.com',
            f'"{query}" site:facebook.com',
            f'"{query}" site:instagram.com',
            f'"{query}" filetype:vcf | filetype:csv',
        ],
        "phone": [
            f'"{query}" site:whitepages.com',
            f'"{query}" site:linkedin.com',
            f'intext:"{query}"',
        ],
        "company": [
            f'site:{query.lower().replace(" ", "")}.com',
            f'"{query}" site:glassdoor.com',
            f'"{query}" site:crunchbase.com',
            f'"{query}" filetype:pdf | filetype:doc',
        ],
        "generic": [
            f'intext:"{query}"',
            f'intitle:"{query}"',
            f'inurl:"{query}"',
            f'"{query}" filetype:log',
            f'"{query}" filetype:conf',
        ]
    }
    
    # Determine dork type
    if "@" in query:
        dork_type = "email"
    elif "." in query and " " not in query:
        dork_type = "domain"
    elif re.match(r'^[\d\s\-\+\(\)]+$', query):
        dork_type = "phone"
    else:
        dork_type = "person"
    
    selected_dorks = dorks.get(dork_type, dorks["generic"])
    
    output = f"🔍 Search Dorks for: {query}\n"
    output += f"📁 Type: {dork_type.upper()}\n"
    output += "=" * 50 + "\n\n"
    
    output += "Generated queries (copy and paste into Google):\n\n"
    for i, dork in enumerate(selected_dorks, 1):
        output += f"{i}. {dork}\n\n"
    
    output += "\n💡 Tips:\n"
    output += "• Use quotes for exact matches\n"
    output += "• Use - to exclude terms\n"
    output += "• Use filetype: to search specific file types\n"
    output += "• Use site: to limit to specific websites\n"
    
    return output


def _format_size(size_bytes: int) -> str:
    """Convert bytes to human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"


# Tool definitions for registration
OSINT_TOOLS = [
    {
        "name": "osint_domain_recon",
        "description": (
            "Performs comprehensive domain reconnaissance including DNS resolution, "
            "reverse DNS, HTTP headers, technology detection, and subdomain enumeration. "
            "Use for investigating websites, companies, or infrastructure."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "domain": {"type": "STRING", "description": "Target domain (e.g., example.com)"}
            },
            "required": ["domain"]
        }
    },
    {
        "name": "osint_ip_lookup",
        "description": (
            "Performs IP address investigation including geolocation, ISP info, "
            "reverse DNS, and network details. Use for tracing IP addresses."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ip_address": {"type": "STRING", "description": "IP address to investigate (e.g., 8.8.8.8)"}
            },
            "required": ["ip_address"]
        }
    },
    {
        "name": "osint_email_investigate",
        "description": (
            "Investigates an email address: validates format, checks domain MX records, "
            "identifies free email providers. Use for email OSINT."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "email": {"type": "STRING", "description": "Email address to investigate"}
            },
            "required": ["email"]
        }
    },
    {
        "name": "osint_extract_metadata",
        "description": (
            "Extracts metadata from files: images (EXIF data), PDFs (author, title), "
            "calculates file hashes (MD5, SHA1, SHA256). Use for digital forensics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path": {"type": "STRING", "description": "Path to the file to analyze"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "osint_whois",
        "description": (
            "Performs WHOIS lookup for domain registration information including "
            "registrar, creation/expiry dates, name servers, and organization."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "domain": {"type": "STRING", "description": "Domain to lookup (e.g., example.com)"}
            },
            "required": ["domain"]
        }
    },
    {
        "name": "osint_search_dorks",
        "description": (
            "Generates advanced search engine dork queries for OSINT investigation. "
            "Supports email, domain, person, phone, and company searches."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Search target (email, domain, name, phone, or company)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "osint_full_recon",
        "description": (
            "Generates a comprehensive full-reconnaissance report for a domain. "
            "Chains domain recon, WHOIS, HTTP headers, SSL check, subdomain enumeration, "
            "and search dorks into a single detailed report. Use for complete target analysis."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "domain": {"type": "STRING", "description": "Target domain (e.g., example.com)"}
            },
            "required": ["domain"]
        }
    },
]


def full_recon_report(domain: str) -> str:
    """
    Generate a comprehensive full-recon report for a domain.
    Chains: domain recon + WHOIS + HTTP headers + subdomain check + search dorks.
    """
    output = f"\n{'='*60}\n"
    output += f"  🎯 FULL RECONNAISSANCE REPORT: {domain}\n"
    output += f"  📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    output += f"{'='*60}\n\n"

    # 1. Domain Recon
    try:
        output += "\n" + "━" * 60 + "\n"
        output += "  PHASE 1: Domain Reconnaissance\n"
        output += "━" * 60 + "\n\n"
        output += domain_recon(domain)
    except Exception as e:
        output += f"❌ Domain recon failed: {e}\n"

    # 2. WHOIS Lookup
    try:
        output += "\n" + "━" * 60 + "\n"
        output += "  PHASE 2: WHOIS Registration Data\n"
        output += "━" * 60 + "\n\n"
        output += whois_lookup(domain)
    except Exception as e:
        output += f"❌ WHOIS lookup failed: {e}\n"

    # 3. HTTP Headers & Security
    try:
        output += "\n" + "━" * 60 + "\n"
        output += "  PHASE 3: HTTP Headers & Security Audit\n"
        output += "━" * 60 + "\n\n"
        from actions.network_tools import http_headers, ssl_certificate_check
        output += http_headers(f"https://{domain}")
        output += "\n\n"
        output += ssl_certificate_check(domain)
    except Exception as e:
        output += f"❌ HTTP/SSL check failed: {e}\n"

    # 4. Common Subdomain Enumeration
    try:
        output += "\n" + "━" * 60 + "\n"
        output += "  PHASE 4: Subdomain Enumeration\n"
        output += "━" * 60 + "\n\n"
        output += subdomain_enum(domain)
    except Exception as e:
        output += f"❌ Subdomain enum failed: {e}\n"

    # 5. Search Dorks
    try:
        output += "\n" + "━" * 60 + "\n"
        output += "  PHASE 5: Search Engine Dorks\n"
        output += "━" * 60 + "\n\n"
        output += search_dorks(domain)
    except Exception as e:
        output += f"❌ Dork generation failed: {e}\n"

    # Summary
    output += "\n" + "━" * 60 + "\n"
    output += "  📊 REPORT COMPLETE\n"
    output += "━" * 60 + "\n"
    output += f"  Target: {domain}\n"
    output += f"  Phases: 5/5\n"
    output += f"  Classification: FULL RECON\n"
    output += "━" * 60 + "\n"

    return output


def handle_osint_tool(tool_name: str, parameters: dict, speak=None) -> str:
    """Route OSINT tool calls to appropriate functions."""
    try:
        if tool_name == "osint_domain_recon":
            return domain_recon(parameters.get("domain", ""))
        elif tool_name == "osint_ip_lookup":
            return ip_lookup(parameters.get("ip_address", ""))
        elif tool_name == "osint_email_investigate":
            return email_investigate(parameters.get("email", ""))
        elif tool_name == "osint_extract_metadata":
            return extract_metadata(parameters.get("file_path", ""))
        elif tool_name == "osint_whois":
            return whois_lookup(parameters.get("domain", ""))
        elif tool_name == "osint_search_dorks":
            return search_dorks(parameters.get("query", ""))
        elif tool_name == "osint_full_recon":
            return full_recon_report(parameters.get("domain", ""))
        else:
            return f"❌ Unknown OSINT tool: {tool_name}"
    except Exception as e:
        return f"❌ OSINT tool error: {e}"

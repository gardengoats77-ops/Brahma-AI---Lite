"""
Deep Research Plugin for REX
Multi-source web research with crawling, summarization, and citation.
Crawls multiple sources, extracts key information, and produces cited reports.
"""

import json
import os
import re
import hashlib
import time
from pathlib import Path
from typing import Optional
from datetime import datetime
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from core.error_handler import log_error

BASE_DIR = Path(__file__).parent.parent
RESEARCH_CACHE_DIR = BASE_DIR / "config" / "research_cache"
RESEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
# Web Fetching & Extraction
# ═══════════════════════════════════════════════════════════════════

def _fetch_url(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch a URL and return HTML content."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[Research] Failed to fetch {url}: {e}")
        return None


def _extract_text_from_html(html: str, url: str = "") -> dict:
    """Extract readable text and metadata from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    
    # Remove scripts, styles, nav, footer
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
        tag.decompose()
    
    # Get title
    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)
    
    # Get meta description
    meta_desc = ""
    meta_tag = soup.find("meta", attrs={"name": "description"})
    if meta_tag:
        meta_desc = meta_tag.get("content", "")
    
    # Extract main content
    # Try article or main tags first
    main_content = soup.find("article") or soup.find("main") or soup.find("body")
    
    if main_content:
        # Get paragraphs
        paragraphs = main_content.find_all("p")
        text_parts = []
        for p in paragraphs:
            text = p.get_text(strip=True)
            if len(text) > 20:  # Skip very short paragraphs
                text_parts.append(text)
        
        content = "\n\n".join(text_parts)
    else:
        content = soup.get_text(separator="\n", strip=True)
    
    # Extract headings for structure
    headings = []
    for h in soup.find_all(["h1", "h2", "h3"]):
        headings.append(h.get_text(strip=True))
    
    # Extract links
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        link_text = a.get_text(strip=True)
        if link_text and len(link_text) > 3:
            full_url = urljoin(url, href) if url else href
            links.append({"text": link_text, "url": full_url})
    
    return {
        "title": title,
        "meta_description": meta_desc,
        "content": content[:10000],  # Limit content length
        "headings": headings[:20],
        "links": links[:30],
        "url": url,
        "word_count": len(content.split()),
    }


def _extract_key_sentences(text: str, max_sentences: int = 10) -> list:
    """Extract key sentences from text using simple heuristics."""
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 30]
    
    # Score sentences by importance indicators
    important_words = {"important", "significant", "key", "main", "primary", "crucial",
                       "essential", "fundamental", "critical", "notable", "major",
                       "first", "second", "third", "finally", "conclusion", "result",
                       "found", "shows", "indicates", "reveals", "demonstrates"}
    
    scored = []
    for sentence in sentences:
        words = set(sentence.lower().split())
        score = len(words & important_words)
        # Bonus for being at the start or end
        scored.append((score, sentence))
    
    # Sort by score and return top sentences
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:max_sentences]]


def _cache_result(key: str, data: dict, ttl_hours: int = 24) -> None:
    """Cache research results."""
    cache_file = RESEARCH_CACHE_DIR / f"{hashlib.md5(key.encode()).hexdigest()}.json"
    data["_cached_at"] = datetime.now().isoformat()
    data["_ttl_hours"] = ttl_hours
    cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _get_cached(key: str) -> Optional[dict]:
    """Get cached research results if still valid."""
    cache_file = RESEARCH_CACHE_DIR / f"{hashlib.md5(key.encode()).hexdigest()}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(data["_cached_at"])
            ttl_hours = data.get("_ttl_hours", 24)
            if (datetime.now() - cached_at).total_seconds() < ttl_hours * 3600:
                return data
        except Exception as _e:
            log_error(_e, context="actions.deep_research", severity="debug")
    return None


# ═══════════════════════════════════════════════════════════════════
# Research Functions
# ═══════════════════════════════════════════════════════════════════

def research_topic(query: str, num_sources: int = 5, depth: str = "standard") -> str:
    """
    Conduct deep research on a topic.
    Crawls multiple sources, extracts key information, and produces a cited report.
    
    Args:
        query: Research topic or question
        num_sources: Number of sources to crawl (3-10)
        depth: quick | standard | deep
    """
    # Check cache
    cache_key = f"research:{query}:{depth}"
    cached = _get_cached(cache_key)
    if cached:
        return cached.get("report", "No cached report found.")
    
    num_sources = max(3, min(10, num_sources))
    
    output = f"🔍 Deep Research: {query}\n"
    output += "=" * 60 + "\n\n"
    output += f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    output += f"🎯 Sources target: {num_sources}\n"
    output += f"📊 Depth: {depth}\n\n"
    
    # Step 1: Search for sources
    output += "━━━ Phase 1: Finding Sources ━━━\n\n"
    
    search_results = _search_web(query, num_results=num_sources * 2)
    
    if not search_results:
        output += "❌ No search results found. Try a different query.\n"
        return output
    
    output += f"Found {len(search_results)} potential sources\n\n"
    
    # Step 2: Crawl and extract content
    output += "━━━ Phase 2: Crawling Sources ━━━\n\n"
    
    sources = []
    for i, result in enumerate(search_results[:num_sources], 1):
        url = result.get("url", "")
        title = result.get("title", "Unknown")
        
        output += f"⏳ [{i}/{num_sources}] Crawling: {title[:50]}...\n"
        
        html = _fetch_url(url)
        if html:
            extracted = _extract_text_from_html(html, url)
            if extracted["word_count"] > 50:  # Only keep substantial content
                sources.append(extracted)
                output += f"   ✅ Extracted {extracted['word_count']} words\n"
            else:
                output += f"   ⚠️ Content too short ({extracted['word_count']} words)\n"
        else:
            output += f"   ❌ Failed to fetch\n"
        
        time.sleep(0.5)  # Be polite to servers
    
    if not sources:
        output += "\n❌ No usable sources found.\n"
        return output
    
    output += f"\n✅ Successfully crawled {len(sources)} sources\n\n"
    
    # Step 3: Analyze and synthesize
    output += "━━━ Phase 3: Analysis & Synthesis ━━━\n\n"
    
    # Combine all content
    all_content = []
    all_headings = []
    for source in sources:
        all_content.append(source["content"])
        all_headings.extend(source["headings"])
    
    combined_content = "\n\n".join(all_content)
    
    # Extract key points
    key_sentences = _extract_key_sentences(combined_content, max_sentences=15)
    
    # Count topic frequency
    topic_words = query.lower().split()
    word_freq = {}
    for word in combined_content.lower().split():
        if len(word) > 3 and word not in {"the", "and", "that", "this", "with", "from", "are", "was", "for", "have", "has", "had"}:
            word_freq[word] = word_freq.get(word, 0) + 1
    
    # Top related words
    related_words = sorted(word_freq.items(), key=lambda x: -x[1])[:15]
    
    # Step 4: Generate report
    output += "━━━ Phase 4: Report Generation ━━━\n\n"
    
    report = f"📄 Research Report: {query}\n"
    report += "=" * 60 + "\n\n"
    report += f"📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    report += f"📚 Sources analyzed: {len(sources)}\n"
    report += f"📝 Total content: {len(combined_content.split())} words\n\n"
    
    # Source Summary
    report += "━━━ Sources Used ━━━\n\n"
    for i, source in enumerate(sources, 1):
        report += f"{i}. {source['title'][:60]}\n"
        report += f"   🔗 {source['url']}\n"
        report += f"   📊 {source['word_count']} words extracted\n\n"
    
    # Key Findings
    report += "━━━ Key Findings ━━━\n\n"
    for i, sentence in enumerate(key_sentences, 1):
        report += f"{i}. {sentence.strip()}.\n\n"
    
    # Topic Analysis
    report += "━━━ Topic Analysis ━━━\n\n"
    report += "Most frequent related terms:\n"
    for word, freq in related_words[:10]:
        report += f"  • {word}: {freq} occurrences\n"
    
    # Common Themes
    report += "\n━━━ Common Themes ━━━\n\n"
    
    # Extract common headings as themes
    heading_freq = {}
    for h in all_headings:
        h_lower = h.lower()
        heading_freq[h_lower] = heading_freq.get(h_lower, 0) + 1
    
    common_themes = sorted(heading_freq.items(), key=lambda x: -x[1])[:5]
    for theme, count in common_themes:
        if count > 1:
            report += f"  📌 {theme.title()} (mentioned {count} sources)\n"
    
    # Citations
    report += "\n━━━ Citations ━━━\n\n"
    for i, source in enumerate(sources, 1):
        report += f"[{i}] {source['title']}\n"
        report += f"    {source['url']}\n"
        if source.get("meta_description"):
            report += f"    {source['meta_description'][:100]}\n"
        report += "\n"
    
    # Methodology
    report += "━━━ Methodology ━━━\n\n"
    report += f"• Search query: {query}\n"
    report += f"• Sources crawled: {len(sources)}\n"
    report += f"• Extraction method: HTML parsing with BeautifulSoup\n"
    report += f"• Analysis: Key sentence extraction, term frequency, theme detection\n"
    
    output += report
    
    # Cache results
    _cache_result(cache_key, {"report": output, "sources": [s["url"] for s in sources]})
    
    return output


def research_question(question: str, num_sources: int = 5) -> str:
    """
    Research a specific question and provide a direct answer with citations.
    """
    output = f"❓ Research Question: {question}\n"
    output += "=" * 60 + "\n\n"
    
    # Search for the question
    results = _search_web(question, num_results=num_sources * 2)
    
    if not search_results:
        return output + "❌ No results found for this question."
    
    # Crawl top sources
    sources = []
    for result in search_results[:num_sources]:
        url = result.get("url", "")
        html = _fetch_url(url)
        if html:
            extracted = _extract_text_from_html(html, url)
            if extracted["word_count"] > 50:
                sources.append(extracted)
        time.sleep(0.3)
    
    if not sources:
        return output + "❌ Could not extract usable content from search results."
    
    # Find relevant sentences
    all_content = " ".join(s["content"] for s in sources)
    relevant_sentences = _extract_key_sentences(all_content, max_sentences=8)
    
    # Format answer
    output += "📝 Answer (based on {0} sources):\n\n".format(len(sources))
    
    # Direct answer from extracted sentences
    for i, sentence in enumerate(relevant_sentences[:5], 1):
        output += f"{sentence.strip()}.\n\n"
    
    # Citations
    output += "━━━ Sources ━━━\n\n"
    for i, source in enumerate(sources, 1):
        output += f"[{i}] {source['title'][:60]}\n"
        output += f"    {source['url']}\n\n"
    
    return output


def research_competitor(company: str) -> str:
    """
    Research a competitor: about, products, pricing, news, reviews.
    """
    queries = [
        f"{company} company overview",
        f"{company} products and services",
        f"{company} pricing plans",
        f"{company} reviews and complaints",
        f"{company} latest news 2024",
    ]
    
    output = f"🏢 Competitor Research: {company}\n"
    output += "=" * 60 + "\n\n"
    
    all_sources = []
    
    for query in queries:
        output += f"🔍 Searching: {query}\n"
        results = _search_web(query, num_results=3)
        
        for result in results[:2]:
            url = result.get("url", "")
            html = _fetch_url(url)
            if html:
                extracted = _extract_text_from_html(html, url)
                if extracted["word_count"] > 30:
                    extracted["query"] = query
                    all_sources.append(extracted)
            time.sleep(0.3)
    
    if not all_sources:
        return output + "❌ No information found."
    
    # Deduplicate by URL
    seen_urls = set()
    unique_sources = []
    for s in all_sources:
        if s["url"] not in seen_urls:
            seen_urls.add(s["url"])
            unique_sources.append(s)
    
    # Organize by category
    output += f"\n✅ Found information from {len(unique_sources)} unique sources\n\n"
    
    output += "━━━ Company Overview ━━━\n\n"
    overview_sources = [s for s in unique_sources if "overview" in s.get("query", "").lower()]
    for source in overview_sources[:2]:
        key_points = _extract_key_sentences(source["content"], max_sentences=3)
        for point in key_points:
            output += f"• {point.strip()}\n"
        output += f"  📎 Source: {source['url']}\n\n"
    
    output += "━━━ Products & Services ━━━\n\n"
    product_sources = [s for s in unique_sources if "product" in s.get("query", "").lower()]
    for source in product_sources[:2]:
        key_points = _extract_key_sentences(source["content"], max_sentences=3)
        for point in key_points:
            output += f"• {point.strip()}\n"
        output += f"  📎 Source: {source['url']}\n\n"
    
    output += "━━━ Recent News ━━━\n\n"
    news_sources = [s for s in unique_sources if "news" in s.get("query", "").lower()]
    for source in news_sources[:2]:
        key_points = _extract_key_sentences(source["content"], max_sentences=2)
        for point in key_points:
            output += f"• {point.strip()}\n"
        output += f"  📎 Source: {source['url']}\n\n"
    
    # All sources
    output += "━━━ All Sources ━━━\n\n"
    for i, source in enumerate(unique_sources, 1):
        output += f"[{i}] {source['title'][:50]}\n"
        output += f"    {source['url']}\n\n"
    
    return output


def research_trend(topic: str) -> str:
    """
    Research current trends and news about a topic.
    """
    queries = [
        f"{topic} trends 2024",
        f"{topic} latest news",
        f"{topic} developments",
    ]
    
    output = f"📈 Trend Research: {topic}\n"
    output += "=" * 60 + "\n\n"
    
    all_sentences = []
    sources = []
    
    for query in queries:
        results = _search_web(query, num_results=3)
        for result in results[:2]:
            url = result.get("url", "")
            html = _fetch_url(url)
            if html:
                extracted = _extract_text_from_html(html, url)
                if extracted["word_count"] > 30:
                    sentences = _extract_key_sentences(extracted["content"], max_sentences=5)
                    all_sentences.extend(sentences)
                    sources.append(extracted)
            time.sleep(0.3)
    
    if not all_sentences:
        return output + "❌ No trend information found."
    
    output += f"📊 Found {len(all_sentences)} key insights from {len(sources)} sources\n\n"
    
    output += "━━━ Key Trends ━━━\n\n"
    for i, sentence in enumerate(all_sentences[:10], 1):
        output += f"{i}. {sentence.strip()}\n\n"
    
    output += "━━━ Sources ━━━\n\n"
    for i, source in enumerate(sources, 1):
        output += f"[{i}] {source['title'][:50]}\n"
        output += f"    {source['url']}\n\n"
    
    return output


def _search_web(query: str, num_results: int = 10) -> list:
    """
    Search the web and return results.
    Uses DuckDuckGo HTML search for reliable results.
    """
    results = []
    
    try:
        # Try DuckDuckGo HTML search (more reliable than Google scraping)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=headers,
            timeout=10
        )
        
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            
            for result in soup.find_all("div", class_="result"):
                title_elem = result.find("a", class_="result__a")
                snippet_elem = result.find("a", class_="result__snippet")
                
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    url = title_elem.get("href", "")
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                    
                    # DuckDuckGo returns redirected URLs, extract actual URL
                    if "uddg=" in url:
                        from urllib.parse import unquote
                        url = unquote(url.split("uddg=")[1].split("&")[0])
                    
                    if url.startswith("http"):
                        results.append({
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                        })
    
    except Exception as e:
        print(f"[Research] Search failed: {e}")
    
    return results[:num_results]


def list_research_cache() -> str:
    """List cached research results."""
    cache_files = list(RESEARCH_CACHE_DIR.glob("*.json"))
    
    if not cache_files:
        return "📭 No cached research results."
    
    output = f"📁 Research Cache ({len(cache_files)} entries)\n"
    output += "=" * 50 + "\n\n"
    
    for cache_file in sorted(cache_files, key=lambda f: f.stat().st_mtime, reverse=True)[:20]:
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            cached_at = data.get("_cached_at", "Unknown")
            sources = data.get("sources", [])
            output += f"📄 {cache_file.stem[:20]}...\n"
            output += f"   Cached: {cached_at}\n"
            output += f"   Sources: {len(sources)}\n\n"
        except Exception as _e:
            log_error(_e, context="actions.deep_research", severity="debug")
    
    return output


def clear_research_cache() -> str:
    """Clear all cached research results."""
    cache_files = list(RESEARCH_CACHE_DIR.glob("*.json"))
    for f in cache_files:
        try:
            f.unlink()
        except Exception as _e:
            log_error(_e, context="actions.deep_research", severity="debug")
    return f"✅ Cleared {len(cache_files)} cached research entries."


# ═══════════════════════════════════════════════════════════════════
# Tool Definitions for Registration
# ═══════════════════════════════════════════════════════════════════

RESEARCH_TOOLS = [
    {
        "name": "research_topic",
        "description": (
            "Conducts deep research on a topic by crawling multiple web sources, "
            "extracting key information, and producing a comprehensive cited report. "
            "Use for in-depth research, analysis, and report generation."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Research topic or question"},
                "num_sources": {"type": "INTEGER", "description": "Number of sources to crawl (3-10, default: 5)"},
                "depth": {"type": "STRING", "description": "quick | standard | deep (default: standard)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "research_question",
        "description": (
            "Answers a specific question by researching multiple sources. "
            "Provides a direct answer with citations."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "question": {"type": "STRING", "description": "The question to research and answer"},
                "num_sources": {"type": "INTEGER", "description": "Number of sources to check (3-10, default: 5)"}
            },
            "required": ["question"]
        }
    },
    {
        "name": "research_competitor",
        "description": (
            "Researches a competitor: company overview, products, pricing, "
            "reviews, and recent news. Aggregates findings from multiple sources."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "company": {"type": "STRING", "description": "Company or competitor name to research"}
            },
            "required": ["company"]
        }
    },
    {
        "name": "research_trend",
        "description": (
            "Researches current trends and news about a topic. "
            "Finds recent developments and emerging patterns."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic": {"type": "STRING", "description": "Topic to research trends for"}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "research_cache",
        "description": "Lists or clears cached research results.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | clear (default: list)"}
            },
            "required": []
        }
    },
]


def handle_research_tool(tool_name: str, parameters: dict, speak=None) -> str:
    """Route research tool calls to appropriate functions."""
    try:
        if tool_name == "research_topic":
            return research_topic(
                query=parameters.get("query", ""),
                num_sources=parameters.get("num_sources", 5),
                depth=parameters.get("depth", "standard")
            )
        elif tool_name == "research_question":
            return research_question(
                question=parameters.get("question", ""),
                num_sources=parameters.get("num_sources", 5)
            )
        elif tool_name == "research_competitor":
            return research_competitor(company=parameters.get("company", ""))
        elif tool_name == "research_trend":
            return research_trend(topic=parameters.get("topic", ""))
        elif tool_name == "research_cache":
            action = parameters.get("action", "list")
            if action == "clear":
                return clear_research_cache()
            return list_research_cache()
        else:
            return f"❌ Unknown research tool: {tool_name}"
    except Exception as e:
        return f"❌ Research tool error: {e}"

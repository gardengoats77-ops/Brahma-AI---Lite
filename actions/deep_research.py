"""
Deep Research Plugin for REX
Multi-angle, multi-source web research with per-claim citations, source dating,
corroboration tracking, and citation-integrity checking.

Upgraded capability (beyond plain search lookups):
- Query planning: expands a topic into multiple angle queries (no more single-query "research")
- Robust search: duckduckgo-search package first, HTML scrape fallback
- Per-claim citation keys: every key finding references its source-registry entry [n]
- Source dates: publish-date extraction from meta tags, "n.d." fallback
- Corroboration: each finding tagged multi-sourced vs single-sourced (verify before citing)
- Citation integrity: final check that every in-text [n] resolves to the registry, and vice versa
"""

import json
import os
import re
import warnings
import hashlib
import time
from pathlib import Path
from typing import Optional
from datetime import datetime
from urllib.parse import urlparse, urljoin, unquote

import requests
from bs4 import BeautifulSoup
from core.error_handler import log_error

BASE_DIR = Path(__file__).parent.parent
RESEARCH_CACHE_DIR = BASE_DIR / "config" / "research_cache"
RESEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

_STOPWORDS = {
    "the", "and", "that", "this", "with", "from", "are", "was", "for", "have",
    "has", "had", "but", "not", "its", "their", "there", "which", "will",
    "would", "should", "about", "into", "over", "after", "before", "than",
}


# ═══════════════════════════════════════════════════════════════════
# Query Planning
# ═══════════════════════════════════════════════════════════════════

def _plan_queries(topic: str, depth: str = "standard") -> list:
    """Expand a topic into multiple angle queries so research is not single-lookup.

    depth: quick (2 queries) | standard (4) | deep (6)
    """
    topic = topic.strip()
    plans = {
        "quick": [
            f"{topic}",
            f"{topic} latest",
        ],
        "standard": [
            f"{topic}",
            f"{topic} latest developments",
            f"{topic} overview and how it works",
            f"{topic} pros cons or limitations",
        ],
        "deep": [
            f"{topic}",
            f"{topic} latest developments 2025",
            f"{topic} overview how it works",
            f"{topic} pros cons limitations",
            f"{topic} comparison alternatives",
            f"{topic} statistics data",
        ],
    }
    return plans.get(depth, plans["standard"])


# ═══════════════════════════════════════════════════════════════════
# Web Fetching & Extraction
# ═══════════════════════════════════════════════════════════════════

def _fetch_url(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch a URL and return HTML content."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[Research] Failed to fetch {url}: {e}")
        return None


def _extract_source_date(soup) -> Optional[str]:
    """Extract a publish date from common meta tags; return YYYY-MM-DD or None."""
    for selector in ("article:published_time", "datePublished", "og:published_time",
                     "pubdate", "publishdate", "sailthru.date"):
        tag = soup.find("meta", attrs={"property": selector}) or soup.find("meta", attrs={"name": selector})
        if tag and tag.get("content"):
            value = str(tag["content"]).strip()
            match = re.search(r"(\d{4}-\d{2}-\d{2})", value)
            if match:
                return match.group(1)
    return None


def _extract_text_from_html(html: str, url: str = "") -> dict:
    """Extract readable text, metadata, and publish date from HTML."""
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
    main_content = soup.find("article") or soup.find("main") or soup.find("body")

    if main_content:
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
        "date": _extract_source_date(soup),
    }


def _extract_key_sentences(text: str, max_sentences: int = 10) -> list:
    """Extract key sentences from text using simple heuristics."""
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 30]

    important_words = {"important", "significant", "key", "main", "primary", "crucial",
                       "essential", "fundamental", "critical", "notable", "major",
                       "first", "second", "third", "finally", "conclusion", "result",
                       "found", "shows", "indicates", "reveals", "demonstrates"}

    scored = []
    for sentence in sentences:
        words = set(sentence.lower().split())
        score = len(words & important_words)
        scored.append((score, sentence))

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
# Search
# ═══════════════════════════════════════════════════════════════════

_AD_URL_MARKERS = ("/y.js", "ad_domain=", "ad_type=")


def _is_ad_url(url: str) -> bool:
    """DuckDuckGo ads come through a y.js redirect URL with ad_* params."""
    return any(m in url for m in _AD_URL_MARKERS)


def _search_web(query: str, num_results: int = 10) -> list:
    """Search the web for a query.

    Tries the duckduckgo-search package first (same import fallback used by
    web_search.py), then falls back to DuckDuckGo HTML scraping.
    """
    # 1) duckduckgo-search package (modern, structured)
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # noqa: F401 — emits rename warning at construction, not import

        # The library itself calls warnings.simplefilter("always") before warning,
        # so a pre-set filter can't suppress it — capture and discard instead.
        results = []
        with warnings.catch_warnings(record=True) as _quiet:
            warnings.simplefilter("always")
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=num_results):
                    url = r.get("href", "")
                    if _is_ad_url(url):
                        continue
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "url": url,
                    })
        if results:
            return results[:num_results]
    except Exception as e:
        print(f"[Research] ddgs search failed ({e}); falling back to HTML...")

    # 2) DuckDuckGo HTML search (fallback)
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": _HEADERS["User-Agent"]},
            timeout=10,
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for result in soup.find_all("div", class_="result"):
                title_elem = result.find("a", class_="result__a")
                snippet_elem = result.find("a", class_="result__snippet")
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    url = title_elem.get("href", "")
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                    if "uddg=" in url:
                        url = unquote(url.split("uddg=")[1].split("&")[0])
                    if url.startswith("http") and not _is_ad_url(url):
                        results.append({"title": title, "url": url, "snippet": snippet})
            if results:
                return results[:num_results]
    except Exception as e:
        print(f"[Research] HTML search failed: {e}")

    return []


# ═══════════════════════════════════════════════════════════════════
# Synthesis helpers
# ═══════════════════════════════════════════════════════════════════

def _dedupe_sources(sources: list) -> list:
    """Drop duplicate URLs, keeping the first occurrence."""
    seen = set()
    unique = []
    for s in sources:
        url = s.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(s)
    return unique


def _significant_words(text: str) -> set:
    """Words useful for comparing claims across sources."""
    words = set()
    for w in re.findall(r"[a-zA-Z]{5,}", text.lower()):
        if w not in _STOPWORDS:
            words.add(w)
    return words


def _corroboration_count(finding_words: set, sources: list, origin_idx: int) -> int:
    """Count sources OTHER than the origin sharing >=2 significant words.

    The origin source is excluded: its content obviously shares words with a
    sentence extracted from it, which would otherwise make every finding look
    corroborated.
    """
    count = 0
    for idx, s in enumerate(sources, 1):
        if idx == origin_idx:
            continue
        shared = _significant_words(s.get("content", "")) & finding_words
        if len(shared) >= 2:
            count += 1
    return count


def _integrity_check(keys_used: list, registry_len: int, dated: int, total: int) -> str:
    """Build the citation-integrity section of the report."""
    unique_keys = sorted(set(keys_used))
    all_resolve = all(1 <= k <= registry_len for k in unique_keys) if unique_keys else False
    lines = [
        "━━━ Citation Integrity ━━━\n",
        f"✓ {len(unique_keys)} in-text citation keys {'all resolve to the Source Registry' if all_resolve else 'MISMATCH — fix keys'}: {unique_keys}",
        f"✓ registry entries cited at least once: {len(unique_keys)}/{registry_len}",
        f"✓ sources dated: {dated}/{total} ('n.d.' = date not published on page)",
    ]
    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════
# Research Functions
# ═══════════════════════════════════════════════════════════════════

def research_topic(query: str, num_sources: int = 5, depth: str = "standard") -> str:
    """
    Conduct deep research on a topic: plan angle queries, search multiple sources,
    crawl, and produce a report with per-claim citation keys, source dates,
    corroboration flags, and a citation-integrity check.

    Args:
        query: Research topic or question
        num_sources: Number of sources to crawl (3-10)
        depth: quick | standard | deep
    """
    query = (query or "").strip()
    if not query:
        return "❌ Please provide a research topic, sir."

    cache_key = f"research:{query}:{depth}"
    cached = _get_cached(cache_key)
    if cached:
        return cached.get("report", "No cached report found.")

    num_sources = max(3, min(10, num_sources))
    queries = _plan_queries(query, depth)

    output = f"🔍 Deep Research: {query}\n"
    output += "=" * 60 + "\n\n"
    output += f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    output += f"🎯 Sources target: {num_sources} | 📊 Depth: {depth}\n\n"

    # Step 1: Plan + search multiple angles
    output += "━━━ Phase 1: Query Plan & Source Discovery ━━━\n\n"
    output += f"Queries planned ({len(queries)}):\n"
    for q in queries:
        output += f"  • {q}\n"
    output += "\n"

    candidates = []
    for q in queries:
        found = _search_web(q, num_results=max(3, num_sources))
        if found:
            output += f"✅ '{q}' → {len(found)} result(s)\n"
            candidates.extend(found)
        else:
            output += f"⚠️ '{q}' → no results\n"
        time.sleep(0.3)

    candidates = _dedupe_sources(candidates)
    output += f"\nFound {len(candidates)} unique candidate sources\n\n"

    if not candidates:
        output += "❌ No search results found. Try a different query.\n"
        return output

    # Step 2: Crawl and extract content
    output += "━━━ Phase 2: Crawling Sources ━━━\n\n"

    sources = []
    for i, result in enumerate(candidates[:num_sources], 1):
        url = result.get("url", "")
        title = result.get("title", "Unknown")

        output += f"⏳ [{i}/{num_sources}] Crawling: {title[:50]}...\n"

        html = _fetch_url(url)
        if html:
            extracted = _extract_text_from_html(html, url)
            if extracted["word_count"] > 50:
                sources.append(extracted)
                output += f"   ✅ {extracted['word_count']} words, date={extracted.get('date') or 'n.d.'}\n"
            else:
                output += f"   ⚠️ Content too short ({extracted['word_count']} words)\n"
        else:
            output += f"   ❌ Failed to fetch\n"

        time.sleep(0.5)

    if not sources:
        output += "\n❌ No usable sources found.\n"
        return output

    output += f"\n✅ Crawled {len(sources)} usable sources\n\n"

    # Step 3: Extract per-source key findings, tagged with citation keys
    output += "━━━ Phase 3: Synthesis (Key Findings) ━━━\n\n"

    findings = []  # (source_index, sentence)
    for idx, source in enumerate(sources, 1):
        sentences = _extract_key_sentences(source["content"], max_sentences=3)
        for sentence in sentences:
            findings.append((idx, sentence))

    keys_used = []
    for n, (src_idx, sentence) in enumerate(findings, 1):
        source = sources[src_idx - 1]
        corroborated = _corroboration_count(_significant_words(sentence), sources, src_idx)
        if corroborated >= 1:
            tag = f"— ✅ corroborated by {corroborated} other source(s)"
        else:
            tag = "— ⚠ single source; verify before citing as fact"
        output += f"{n}. {sentence.strip()}. [Source {src_idx}] {tag}\n\n"
        keys_used.append(src_idx)

    # Topic analysis (term frequency)
    combined_content = "\n\n".join(s["content"] for s in sources)
    word_freq = {}
    for word in combined_content.lower().split():
        if len(word) > 3 and word not in _STOPWORDS:
            word_freq[word] = word_freq.get(word, 0) + 1
    related_words = sorted(word_freq.items(), key=lambda x: -x[1])[:10]

    output += "━━━ Topic Terms ━━━\n\n"
    output += "Frequent related terms:\n"
    for word, freq in related_words:
        output += f"  • {word}: {freq} occurrences\n"
    output += "\n"

    # Step 4: Source Registry (with dates)
    output += "━━━ Source Registry ━━━\n\n"
    dated = 0
    for i, source in enumerate(sources, 1):
        date = source.get("date") or "n.d."
        if date != "n.d.":
            dated += 1
        output += f"[{i}] {source['title'][:60]}\n"
        output += f"    URL: {source['url']}\n"
        output += f"    Date: {date} | Words: {source['word_count']}\n"
        if source.get("meta_description"):
            output += f"    {source['meta_description'][:100]}\n"
        output += "\n"

    # Step 5: Citation integrity check
    output += _integrity_check(keys_used, len(sources), dated, len(sources))

    # Methodology
    output += "\n━━━ Methodology ━━━\n\n"
    output += f"• Topic: {query}\n"
    output += f"• Angle queries: {', '.join(queries)}\n"
    output += f"• Sources crawled: {len(sources)}\n"
    output += "• Extraction: HTML parsing (BeautifulSoup), key-sentence scoring\n"
    output += "• Corroboration: overlap of significant words across sources\n"

    _cache_result(cache_key, {"report": output, "sources": [s["url"] for s in sources]})
    return output


def research_question(question: str, num_sources: int = 5) -> str:
    """
    Research a specific question and provide a direct answer with citations.
    """
    output = f"❓ Research Question: {question}\n"
    output += "=" * 60 + "\n\n"

    results = _search_web(question, num_results=num_sources * 2)

    if not results:
        return output + "❌ No results found for this question."

    # Crawl top sources
    sources = []
    for result in results[:num_sources]:
        url = result.get("url", "")
        html = _fetch_url(url)
        if html:
            extracted = _extract_text_from_html(html, url)
            if extracted["word_count"] > 50:
                sources.append(extracted)
        time.sleep(0.3)

    if not sources:
        return output + "❌ Could not extract usable content from search results."

    # Per-source relevant sentences, tagged with citation keys
    output += f"📝 Answer (based on {len(sources)} sources):\n\n"
    keys_used = []
    for idx, source in enumerate(sources, 1):
        sentences = _extract_key_sentences(source["content"], max_sentences=2)
        for sentence in sentences:
            output += f"• {sentence.strip()}. [Source {idx}]\n\n"
            keys_used.append(idx)

    output += "━━━ Sources ━━━\n\n"
    for i, source in enumerate(sources, 1):
        date = source.get("date") or "n.d."
        output += f"[{i}] {source['title'][:60]}\n"
        output += f"    {source['url']} (published {date})\n\n"

    output += _integrity_check(keys_used, len(sources), sum(1 for s in sources if s.get("date")), len(sources))
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
        f"{company} latest news 2025",
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

    unique_sources = _dedupe_sources(all_sources)

    output += f"\n✅ Found information from {len(unique_sources)} unique sources\n\n"

    sections = [
        ("Company Overview", "overview"),
        ("Products & Services", "product"),
        ("Pricing", "pricing"),
        ("Recent News", "news"),
        ("Reviews", "review"),
    ]

    for heading, keyword in sections:
        output += f"━━━ {heading} ━━━\n\n"
        matched = [s for s in unique_sources if keyword in s.get("query", "").lower()]
        for source in matched[:2]:
            key_points = _extract_key_sentences(source["content"], max_sentences=2)
            for point in key_points:
                output += f"• {point.strip()}\n"
            date = source.get("date") or "n.d."
            output += f"  📎 {source['url']} (published {date})\n\n"

    output += "━━━ All Sources ━━━\n\n"
    for i, source in enumerate(unique_sources, 1):
        date = source.get("date") or "n.d."
        output += f"[{i}] {source['title'][:50]}\n"
        output += f"    {source['url']} (published {date})\n\n"

    return output


def research_trend(topic: str) -> str:
    """
    Research current trends and news about a topic.
    """
    queries = [
        f"{topic} trends 2025",
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
                    sentences = _extract_key_sentences(extracted["content"], max_sentences=3)
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
        date = source.get("date") or "n.d."
        output += f"[{i}] {source['title'][:50]}\n"
        output += f"    {source['url']} (published {date})\n\n"

    return output


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
            "Conducts deep multi-angle research on a topic: plans several search queries, "
            "crawls multiple independent sources, and produces a report with per-claim "
            "citation keys, source dates, corroboration flags, and a citation-integrity "
            "check. Use for in-depth research, analysis, and report generation."
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
            "Provides a direct answer with per-source citations and source dates."
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
            "Researches a competitor: company overview, products, pricing, reviews, "
            "and recent news. Aggregates findings from multiple sources with dated citations."
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
            "Finds recent developments and emerging patterns with dated sources."
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

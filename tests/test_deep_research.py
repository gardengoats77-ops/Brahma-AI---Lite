"""
tests/test_deep_research.py — Tests for the upgraded deep-research action.

Verifies the citation-integrity methodology: multi-angle query planning,
per-claim citation keys that resolve to the source registry, source dating,
and the research_question NameError regression. All network calls are mocked.
"""

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from actions import deep_research as dr


# ═══════════════════════════════════════════════════════════════════
# Fakes
# ═══════════════════════════════════════════════════════════════════

FAKE_RESULTS = [
    {"title": "Alpha Official Guide", "url": "https://example.com/alpha", "snippet": "Alpha is the leading framework."},
    {"title": "Beta Deep Dive", "url": "https://example.com/beta", "snippet": "Beta explains Alpha usage."},
    {"title": "Gamma News Report", "url": "https://example.com/gamma", "snippet": "Gamma covers Alpha developments."},
]

_SENTENCE = (
    "The Alpha framework remains the leading open source solution for building "
    "production grade systems and it scales well across large deployments in "
    "enterprise environments around the world."
)


def _fake_html(title: str) -> str:
    """HTML with enough words (>50) to be kept, plus a publish date."""
    paras = "".join(f"<p>{_SENTENCE}</p>" for _ in range(4))
    return (
        f'<html><head><title>{title}</title>'
        f'<meta property="article:published_time" content="2025-06-01T10:00:00Z">'
        f'<meta name="description" content="meta description for {title}">'
        f'</head><body><article><h1>{title}</h1>{paras}</article></body></html>'
    )


def _fake_fetch(url, timeout=15):
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return _fake_html(slug.title())


# Distinct per-source content so corroboration is meaningful: A and B share
# vocabulary (inference/devices/throughput); C shares almost nothing.
_DISTINCT_BODIES = {
    "https://example.com/alpha": (
        "Alpha processors deliver the fastest inference for edge devices with low "
        "power draw and exceptional throughput in real production workloads."
    ),
    "https://example.com/beta": (
        "Beta models offer the cheapest inference for mobile devices at modest "
        "accuracy and acceptable throughput in typical everyday use cases."
    ),
    "https://example.com/gamma": (
        "Gamma handles scheduling for cloud warehouses using event queues and "
        "relational storage with batch processing and nightly consolidation."
    ),
}


def _fake_fetch_distinct(url, timeout=15):
    body = _DISTINCT_BODIES.get(url, _SENTENCE)
    title = url.rstrip("/").rsplit("/", 1)[-1].title()
    paras = "".join(f"<p>{body} {body}</p>" for _ in range(3))
    return (
        f'<html><head><title>{title}</title>'
        f'<meta property="article:published_time" content="2025-06-01T10:00:00Z">'
        f'</head><body><article><h1>{title}</h1>{paras}</article></body></html>'
    )


def _no_cache(_key):
    return None


def _no_store(*_args, **_kwargs):
    return None


@pytest.fixture(autouse=True)
def _isolated_research():
    """Patch all network + cache + sleep side effects."""
    with patch.object(dr, "_search_web", return_value=list(FAKE_RESULTS)), \
         patch.object(dr, "_fetch_url", side_effect=_fake_fetch), \
         patch.object(dr, "_get_cached", side_effect=_no_cache), \
         patch.object(dr, "_cache_result", side_effect=_no_store), \
         patch.object(dr.time, "sleep", return_value=None):
        yield


# ═══════════════════════════════════════════════════════════════════
# Query planning
# ═══════════════════════════════════════════════════════════════════

class TestQueryPlanning:
    def test_depth_controls_number_of_angles(self):
        assert len(dr._plan_queries("Alpha framework", "quick")) == 2
        assert len(dr._plan_queries("Alpha framework", "standard")) == 4
        assert len(dr._plan_queries("Alpha framework", "deep")) == 6

    def test_queries_are_distinct_angles(self):
        queries = dr._plan_queries("Alpha framework", "standard")
        assert len(set(queries)) == len(queries), "plans should be distinct"


class TestAdFiltering:
    def test_ad_urls_are_detected(self):
        assert dr._is_ad_url("https://duckduckgo.com/y.js?ad_domain=foo&ad_type=txad")
        assert dr._is_ad_url("https://duckduckgo.com/y.js")

    def test_organic_urls_pass(self):
        assert not dr._is_ad_url("https://dev.to/top-10-frameworks")
        assert not dr._is_ad_url("https://example.com/article?ref=search")


# ═══════════════════════════════════════════════════════════════════
# Citation integrity
# ═══════════════════════════════════════════════════════════════════

class TestCitationIntegrity:
    def test_report_contains_findings_and_registry(self):
        report = dr.research_topic("Alpha framework", num_sources=3, depth="standard")
        assert "Key Findings" in report
        assert "Source Registry" in report
        assert "Citation Integrity" in report

    def test_every_finding_key_resolves_to_registry(self):
        report = dr.research_topic("Alpha framework", num_sources=3, depth="standard")

        findings_section = report.split("Key Findings")[1].split("Topic Terms")[0]
        registry_section = report.split("Source Registry")[1].split("Citation Integrity")[0]

        keys_used = {int(k) for k in re.findall(r"\[Source (\d+)\]", findings_section)}
        registry_keys = {int(k) for k in re.findall(r"^\[(\d+)\]", registry_section, re.M)}

        assert keys_used, "findings should carry [Source n] citation keys"
        assert keys_used.issubset(registry_keys), \
            f"dangling citation keys: {keys_used - registry_keys}"

    def test_registry_entries_are_dated(self):
        report = dr.research_topic("Alpha framework", num_sources=3, depth="standard")
        registry_section = report.split("Source Registry")[1].split("Citation Integrity")[0]
        assert "Date: 2025-06-01" in registry_section
        assert registry_section.count("Date:") == 3

    def test_findings_tagged_with_corroboration(self):
        report = dr.research_topic("Alpha framework", num_sources=3, depth="standard")
        findings_section = report.split("Key Findings")[1].split("Topic Terms")[0]
        assert ("corroborated" in findings_section) or ("single source" in findings_section)

    def test_single_sourced_findings_flagged(self):
        """Distinct source content: A/B corroborate, C must be flagged single-sourced."""
        with patch.object(dr, "_fetch_url", side_effect=_fake_fetch_distinct):
            report = dr.research_topic("Gamma edge", num_sources=3, depth="quick")
        findings_section = report.split("Key Findings")[1].split("Topic Terms")[0]
        assert "corroborated" in findings_section
        assert "single source" in findings_section

    def test_corroboration_excludes_origin(self):
        """The origin source must not count as corroborating its own finding."""
        sources = [
            {"content": "alpha inference devices throughput fast edge power draw"},
            {"content": "beta inference devices throughput cheap mobile accuracy"},
            {"content": "gamma scheduling warehouses queues storage batch nightly"},
        ]
        alpha_words = {"alpha", "inference", "devices", "throughput", "fast", "edge", "power"}
        gamma_words = {"gamma", "scheduling", "warehouses", "queues", "storage", "batch", "nightly"}
        # alpha finding: beta corroborates (3 shared words); gamma does not -> 1.
        # (alpha itself shares 7 but is the origin and must be excluded.)
        assert dr._corroboration_count(alpha_words, sources, origin_idx=1) == 1
        # gamma finding: no other source shares >=2 words -> single-sourced.
        assert dr._corroboration_count(gamma_words, sources, origin_idx=3) == 0

    def test_integrity_check_passes(self):
        report = dr.research_topic("Alpha framework", num_sources=3, depth="standard")
        integrity_section = report.split("Citation Integrity")[1].split("Methodology")[0]
        assert "all resolve to the Source Registry" in integrity_section


# ═══════════════════════════════════════════════════════════════════
# Regression + tool contract
# ═══════════════════════════════════════════════════════════════════

class TestQuestionAndContract:
    def test_research_question_no_namerror(self):
        """Regression: research_question previously referenced undefined search_results."""
        report = dr.research_question("What is Alpha?", num_sources=3)
        assert isinstance(report, str)
        assert "Sources" in report

    def test_tool_declarations_valid(self):
        for tool in dr.RESEARCH_TOOLS:
            assert "name" in tool and "description" in tool and "parameters" in tool
            assert tool["parameters"]["type"] == "OBJECT"
            assert "properties" in tool["parameters"]

    def test_handle_research_tool_unknown_returns_string(self):
        result = dr.handle_research_tool("nonexistent_tool_xyz", {}, None)
        assert isinstance(result, str)

    def test_handle_research_topic_routes(self):
        result = dr.handle_research_tool("research_topic", {"query": "Alpha framework", "num_sources": 3}, None)
        assert "Deep Research" in result

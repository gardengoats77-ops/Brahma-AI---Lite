# tests/test_mobile_dashboard.py
"""Tests for the FastAPI HTMX mobile dashboard (dashboard/mobile.py).

Covers: index route returns HTML, route definitions exist, and the
FastAPI app is properly configured with Jinja2 templates.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

# Ensure repo root is importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_index_returns_html():
    """GET / should return 200 with HTML content."""
    from dashboard.mobile import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Should contain key dashboard elements
    assert "<title>" in response.text.lower() or "<h1" in response.text.lower()


def test_health_endpoint():
    """GET /api/health should return a JSON status payload."""
    from dashboard.mobile import app
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] in ("online", "degraded")


def test_dispatch_page_returns_html():
    """GET /dispatch should return dispatch form + history."""
    from dashboard.mobile import app
    client = TestClient(app)
    response = client.get("/dispatch")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_fleet_page_returns_html():
    """GET /fleet should return device cards."""
    from dashboard.mobile import app
    client = TestClient(app)
    response = client.get("/fleet")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_voice_page_returns_html():
    """GET /voice should return voice log stream."""
    from dashboard.mobile import app
    client = TestClient(app)
    response = client.get("/voice")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
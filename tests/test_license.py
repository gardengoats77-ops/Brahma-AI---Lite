"""Tests for commercial licensing enforcement (config/profile.py license module).

Uses an ephemeral Ed25519 keypair generated in the fixture — nothing touches
the real vendor key in config/license_private.pem. The public key constant is
monkeypatched so the validator verifies against the test keypair.
"""

import base64
import datetime
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import profile  # noqa: E402


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign(priv, payload: dict) -> str:
    """Sign a payload exactly like scripts/make_license.py does."""
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = priv.sign(payload_bytes)
    return f"{_b64(payload_bytes)}.{_b64(sig)}"


@pytest.fixture
def community(tmp_path, monkeypatch):
    """Force the Community (no-license) state, isolated from the real config
    dir and any ALMIGHTY_LICENSE_KEY in the ambient environment."""
    monkeypatch.setattr(profile, "LICENSE_FILE", tmp_path / "license.json")
    monkeypatch.setattr(profile, "_cached", None)
    monkeypatch.delenv("ALMIGHTY_LICENSE_KEY", raising=False)


@pytest.fixture
def signer(tmp_path, monkeypatch):
    """Ed25519 keypair + a sign helper; points the validator at it."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    priv = ed25519.Ed25519PrivateKey.generate()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(profile, "_LICENSE_PUBLIC_KEY_PEM", pub_pem)
    # Isolate persistence + cache from the real config dir.
    monkeypatch.setattr(profile, "LICENSE_FILE", tmp_path / "license.json")
    monkeypatch.setattr(profile, "_cached", None)
    return lambda payload: _sign(priv, payload)


def _pro_payload(licensee="Acme Corp", days=30, **overrides):
    today = datetime.date.today()
    payload = {"licensee": licensee, "tier": "pro", "issued": today.isoformat()}
    if days is not None:
        payload["expires"] = (today + datetime.timedelta(days=days)).isoformat()
    payload.update(overrides)
    return payload


class TestValidator:
    def test_community_by_default(self, community):
        assert profile.is_pro() is False
        state = profile.load_license_state()
        assert state.valid and state.tier == "community"

    def test_valid_pro_key(self, signer):
        state = profile._validate_key(signer(_pro_payload()))
        assert state.valid is True
        assert state.tier == "pro"
        assert state.licensee == "Acme Corp"
        assert state.expires

    def test_non_expiring_key(self, signer):
        state = profile._validate_key(signer(_pro_payload(days=None)))
        assert state.valid is True
        assert state.expires == ""

    def test_tampered_signature(self, signer):
        key = signer(_pro_payload())
        tampered = key[:-4] + ("A" if key[-4] != "A" else "B")
        assert profile._validate_key(tampered).valid is False

    def test_tampered_payload(self, signer):
        key = signer(_pro_payload())
        # Flip a payload character inside the base64 text, keep signature.
        payload_part, sig_part = key.split(".", 1)
        flipped = ("A" if payload_part[-2] != "A" else "B") + payload_part[-1:]
        bad = payload_part[:-2] + flipped + "." + sig_part
        state = profile._validate_key(bad)
        assert state.valid is False

    def test_wrong_tier_rejected(self, signer):
        key = signer(_pro_payload(tier="community"))
        assert profile._validate_key(key).valid is False

    def test_expired_key_rejected(self, signer):
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        key = signer(_pro_payload(expires=past))
        state = profile._validate_key(key)
        assert state.valid is False
        assert "expired" in state.reason

    def test_malformed_keys(self, signer):
        for bad in ("", "   ", "no-dot", "a.b.c", "%%%%.$$$$", "not-json.payload"):
            assert profile._validate_key(bad).valid is False

    def test_signature_from_wrong_key(self, signer):
        from cryptography.hazmat.primitives.asymmetric import ed25519
        other = ed25519.Ed25519PrivateKey.generate()
        key = _sign(other, _pro_payload())
        assert profile._validate_key(key).valid is False


class TestActivation:
    def test_activate_persists_and_gates(self, signer):
        key = signer(_pro_payload())
        result = profile.activate_license(key)
        assert result["ok"] is True and result["tier"] == "pro"
        assert profile.is_pro() is True
        assert profile.LICENSE_FILE.exists()

        # Re-load from disk (fresh cache) still resolves to pro.
        profile._cached = None
        assert profile.load_license_state().tier == "pro"

        deactivated = profile.deactivate_license()
        assert deactivated["ok"] is True and deactivated["tier"] == "community"
        assert profile.is_pro() is False
        assert not profile.LICENSE_FILE.exists()

    def test_activate_rejects_invalid_and_persists_nothing(self, signer, tmp_path):
        profile._cached = None
        result = profile.activate_license("garbage-key")
        assert result["ok"] is False
        assert profile.is_pro() is False
        assert not profile.LICENSE_FILE.exists()

    def test_activate_rejects_expired(self, signer):
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        result = profile.activate_license(signer(_pro_payload(expires=past)))
        assert result["ok"] is False
        assert profile.is_pro() is False

    def test_invalid_saved_key_reports_reason(self, signer):
        profile.LICENSE_FILE.write_text(json.dumps({"key": "bogus"}), encoding="utf-8")
        profile._cached = None
        state = profile.load_license_state()
        assert state.tier == "community"
        assert state.reason  # surfaced so the UI can show why the key failed

    def test_env_key_override(self, signer, monkeypatch):
        key = signer(_pro_payload(licensee="Env User"))
        monkeypatch.setenv("ALMIGHTY_LICENSE_KEY", key)
        assert profile.is_pro() is True
        state = profile.load_license_state()
        assert state.licensee == "Env User"

        monkeypatch.setenv("ALMIGHTY_LICENSE_KEY", "bad")
        assert profile.is_pro() is False
        monkeypatch.delenv("ALMIGHTY_LICENSE_KEY", raising=False)

    def test_invalid_env_key_does_not_shadow_saved_key(self, signer, monkeypatch):
        """An invalid ALMIGHTY_LICENSE_KEY must fall through to a valid saved
        key instead of silently downgrading the user."""
        key = signer(_pro_payload(licensee="Saved Co"))
        assert profile.activate_license(key)["ok"] is True
        monkeypatch.setenv("ALMIGHTY_LICENSE_KEY", "definitely-not-a-key")
        assert profile.is_pro() is True
        assert profile.load_license_state().licensee == "Saved Co"
        monkeypatch.delenv("ALMIGHTY_LICENSE_KEY", raising=False)


class TestFeatureGating:
    """Skills + MCP servers are Pro-gated across planner/executor surfaces."""

    def test_planner_sections_omitted_when_community(self, community):
        from agent import planner

        assert planner._skills_section() == ""
        assert planner._mcp_section() == ""

    def test_planner_sections_present_when_pro(self, signer, tmp_path, monkeypatch):
        from agent import planner
        from mcp_client import McpManager
        from skill_manager import SkillManager

        monkeypatch.setenv("ALMIGHTY_LICENSE_KEY", signer(_pro_payload()))

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "demo.md").write_text("---\nname: demo\ndescription: Demo skill\n---\nBody\n", encoding="utf-8")
        mgr = SkillManager(tmp_path)
        monkeypatch.setattr("skill_manager.get_skill_manager", lambda base_dir=None: mgr)
        assert "demo" in planner._skills_section()

        cfg = tmp_path / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": []}), encoding="utf-8")
        mcp_mgr = McpManager(config_path=cfg)
        monkeypatch.setattr("mcp_client.get_mcp_manager", lambda config_path=None: mcp_mgr)
        assert planner._mcp_section() == ""  # no tools configured

        monkeypatch.delenv("ALMIGHTY_LICENSE_KEY", raising=False)

    def test_executor_returns_lock_message_when_community(self, community):
        from agent import executor

        for tool in ("list_skills", "load_skill", "mcp_list"):
            result = executor._call_tool(tool, {"name": "x"} if tool == "load_skill" else {}, None)
            assert "Pro" in result

    def test_executor_mcp_fallback_gated_when_community(self, community, monkeypatch):
        from agent import executor
        from mcp_client import McpManager
        import tempfile
        import tests.test_mcp as tm

        script = Path(tempfile.mkdtemp()) / "srv.py"
        script.write_text(tm.FAKE_SERVER, encoding="utf-8")
        cfg = Path(tempfile.mkdtemp()) / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "fake", "command": sys.executable, "args": [str(script)], "env": {}}
        ]}), encoding="utf-8")
        mgr = McpManager(config_path=cfg)
        mgr.list_tools()  # start the server so has_tool would otherwise match
        monkeypatch.setattr("mcp_client.get_mcp_manager", lambda config_path=None: mgr)
        result = executor._call_tool("echo", {"text": "yo"}, None)
        assert "Unknown action" in result  # gated -> falls through, never routed
        mgr.shutdown()

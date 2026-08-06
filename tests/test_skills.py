"""Tests for the markdown skills system (skill_manager + plugin integration)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import skill_manager
from skill_manager import SkillManager


@pytest.fixture(autouse=True)
def _isolated_singleton(monkeypatch):
    """Every test starts with a fresh shared manager (no repo-skills bleed)."""
    skill_manager.reset_skill_manager()
    yield
    skill_manager.reset_skill_manager()


def _write_skill(root: Path, name: str, content: str, as_dir: bool = False) -> Path:
    if as_dir:
        path = root / name / "SKILL.md"
    else:
        path = root / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


FRONTMATTER_SKILL = """---
name: business-plan
description: Build a complete business plan — deck, written plan, and financial sheet.
---

# Business Plan Skill

Body line one.
"""


class TestDiscovery:
    def test_frontmatter_skill_discovered(self, tmp_path):
        _write_skill(tmp_path / "skills", "business-plan", FRONTMATTER_SKILL, as_dir=True)
        mgr = SkillManager(tmp_path)

        skills = mgr.list_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "business-plan"
        assert "business plan" in skills[0]["description"].lower()

        body = mgr.load_skill("business-plan")
        assert body is not None
        assert "# Business Plan Skill" in body
        assert "name: business-plan" not in body  # frontmatter stripped

    def test_bare_markdown_falls_back_to_filename_and_first_line(self, tmp_path):
        _write_skill(tmp_path / "skills", "my-skill", "Do the thing.\n\nDetails here.\n")
        mgr = SkillManager(tmp_path)

        skills = mgr.list_skills()
        assert skills[0]["name"] == "my-skill"
        assert skills[0]["description"] == "Do the thing."

    def test_empty_and_malformed_skills_skipped(self, tmp_path):
        _write_skill(tmp_path / "skills", "empty", "")
        _write_skill(tmp_path / "skills", "fm-only", "---\nname: fm-only\ndescription: no body\n---\n")
        mgr = SkillManager(tmp_path)
        assert mgr.list_skills() == []

    def test_duplicate_name_last_wins(self, tmp_path):
        _write_skill(tmp_path / "skills", "dup", "---\nname: dup\ndescription: first\n---\nFirst body\n")
        _write_skill(tmp_path / "skills", "other", "---\nname: dup\ndescription: second\n---\nSecond body\n")
        mgr = SkillManager(tmp_path)
        assert len(mgr.list_skills()) == 1
        assert "Second body" in mgr.load_skill("dup")

    def test_missing_skills_dir_is_noop(self, tmp_path):
        mgr = SkillManager(tmp_path)
        assert mgr.list_skills() == []
        assert mgr.load_skill("nope") is None

    def test_non_utf8_skill_file_is_skipped_not_crash(self, tmp_path):
        bad = tmp_path / "skills" / "bad.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"\xff\xfe\x00binary\x00")
        _write_skill(tmp_path / "skills", "good", "Fine\n")

        mgr = SkillManager(tmp_path)  # must not raise
        assert mgr.load_skill("good") == "Fine"
        assert mgr.load_skill("bad") is None

    def test_load_skill_is_case_insensitive(self, tmp_path):
        _write_skill(tmp_path / "skills", "my-skill", "Body\n")
        mgr = SkillManager(tmp_path)
        assert mgr.load_skill("My-Skill") == "Body"


class TestPluginRegistration:
    def test_register_adds_plugin_skill_and_survives_rescan(self, tmp_path):
        mgr = SkillManager(tmp_path)
        mgr.register("plugin-skill", "From a plugin", "# Plugin Skill\nBody")
        assert mgr.load_skill("plugin-skill") == "# Plugin Skill\nBody"
        assert mgr.all_skills()[0].source == "plugin"

        mgr.discover()  # file re-scan must not drop plugin skills
        assert mgr.load_skill("plugin-skill") is not None

    def test_plugin_manager_collects_get_skills_hook(self, tmp_path):
        import plugin_manager as pm_module

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "skill_plugin.py").write_text(
            "def get_skills():\n"
            "    return [{'name': 'p-skill', 'description': 'From plugin',"
            " 'content': '# P\\nBody'},\n"
            "            {'name': 'bad', 'description': 'missing content'}]  # skipped\n",
            encoding="utf-8",
        )
        pm = pm_module.PluginManager(tmp_path)
        pm.load_plugins()

        assert pm.load_skill("p-skill") == "# P\nBody"
        assert pm.load_skill("bad") is None
        names = [s["name"] for s in pm.list_skills()]
        assert "p-skill" in names and "bad" not in names

    def test_plugin_get_skills_error_is_contained(self, tmp_path, monkeypatch, capsys):
        import plugin_manager as pm_module

        class BadPlugin:
            def get_skills(self):
                raise RuntimeError("boom")

        pm = pm_module.PluginManager(tmp_path)
        pm.plugins = [BadPlugin()]
        assert pm.collect_skills() == 0  # error logged, not raised
        assert "boom" in capsys.readouterr().out


class TestExecutorIntegration:
    def test_executor_list_and_load_skill(self, tmp_path, monkeypatch):
        from agent import executor

        monkeypatch.setattr("config.profile.is_pro", lambda: True)  # skills are Pro-gated
        _write_skill(tmp_path / "skills", "test-skill", "---\nname: test-skill\ndescription: A test skill\n---\nInstructions body\n")
        mgr = SkillManager(tmp_path)
        monkeypatch.setattr(skill_manager, "get_skill_manager", lambda base_dir=None: mgr)

        listing = executor._call_tool("list_skills", {}, None)
        assert "test-skill" in listing and "A test skill" in listing

        loaded = executor._call_tool("load_skill", {"name": "test-skill"}, None)
        assert loaded == "Instructions body"

        missing = executor._call_tool("load_skill", {"name": "nope"}, None)
        assert "not found" in missing

        with pytest.raises(ValueError):
            executor._call_tool("load_skill", {}, None)

    def test_executor_list_skills_empty(self, tmp_path, monkeypatch):
        from agent import executor

        monkeypatch.setattr("config.profile.is_pro", lambda: True)
        mgr = SkillManager(tmp_path)
        monkeypatch.setattr(skill_manager, "get_skill_manager", lambda base_dir=None: mgr)
        assert "No skills" in executor._call_tool("list_skills", {}, None)


class TestDeclarationHandlerCoverage:
    def test_every_declared_tool_has_a_handler(self):
        """AST-level guard: a renamed tool declaration without a matching
        handler branch would silently surface as "Unknown tool: X" in live
        chat. Works without importing main (which needs PyQt6/sounddevice)."""
        import ast

        src = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        def declared_names(assign_name: str) -> set[str]:
            names: set[str] = set()
            for node in ast.walk(tree):
                if (isinstance(node, ast.Assign)
                        and node.targets
                        and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id == assign_name
                        and isinstance(node.value, ast.List)):
                    for elt in node.value.elts:
                        if not isinstance(elt, ast.Dict):
                            continue
                        for key, val in zip(elt.keys, elt.values):
                            if (isinstance(key, ast.Constant) and key.value == "name"
                                    and isinstance(val, ast.Constant)
                                    and isinstance(val.value, str)):
                                names.add(val.value)
            return names

        declared = declared_names("TOOL_DECLARATIONS") | declared_names("SKILL_TOOL_DECLARATIONS")

        handler_names: set[str] = set()
        for node in ast.walk(tree):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "_execute_tool"):
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Compare) and len(sub.ops) == 1
                            and isinstance(sub.ops[0], ast.Eq)
                            and isinstance(sub.left, ast.Name) and sub.left.id == "name"
                            and len(sub.comparators) == 1
                            and isinstance(sub.comparators[0], ast.Constant)
                            and isinstance(sub.comparators[0].value, str)):
                        handler_names.add(sub.comparators[0].value)

        missing = declared - handler_names
        assert not missing, f"Declared tools without a handler branch: {sorted(missing)}"


class TestPlannerIntegration:
    def test_skills_section_empty_without_skills(self, tmp_path, monkeypatch):
        from agent import planner

        mgr = SkillManager(tmp_path)
        monkeypatch.setattr(skill_manager, "get_skill_manager", lambda base_dir=None: mgr)
        assert planner._skills_section() == ""

    def test_skills_section_lists_catalog(self, tmp_path, monkeypatch):
        from agent import planner

        monkeypatch.setattr("config.profile.is_pro", lambda: True)
        _write_skill(tmp_path / "skills", "test-skill", "---\nname: test-skill\ndescription: A test skill\n---\nBody\n")
        mgr = SkillManager(tmp_path)
        monkeypatch.setattr(skill_manager, "get_skill_manager", lambda base_dir=None: mgr)

        section = planner._skills_section()
        assert "test-skill" in section
        assert "load_skill" in section
        assert "A test skill" in section

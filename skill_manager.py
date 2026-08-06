"""skill_manager.py — markdown "skills" for Almighty AI.

A skill is a small markdown instruction set that the agent can discover and
load on demand, in the spirit of Claude Code skills. Skills live in the
project ``skills/`` folder as either ``skills/<name>.md`` or
``skills/<name>/SKILL.md``. Each skill may start with a YAML-ish frontmatter
block::

    ---
    name: my-skill
    description: What this skill does, when to use it.
    ---

    Instructions the agent should follow when this skill applies.

Parsing is deliberately dependency-free (no PyYAML): frontmatter is a
``---``-delimited header of ``key: value`` lines. Missing frontmatter falls
back to the file name for ``name`` and the first heading/paragraph for
``description``, so even a bare markdown file works as a skill.

Skills can also be contributed by plugins through
:meth:`PluginManager.collect_skills` — see ``plugin_manager.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Match a leading --- frontmatter block:  ---\n key: value lines \n---
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n", re.DOTALL)


@dataclass
class Skill:
    """A discovered or plugin-registered skill."""

    name: str
    description: str
    body: str
    source: str = "file"          # "file" or "plugin"
    path: Optional[Path] = None   # filesystem source (file skills only)

    def to_catalog_entry(self) -> dict:
        """Short {name, description} entry for prompt/tool catalog listing."""
        return {"name": self.name, "description": self.description}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a skill file into (metadata, body). No PyYAML dependency."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    meta: dict = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip()

    return meta, text[match.end():].strip()


def _fallback_description(body: str) -> str:
    """Derive a description from the first heading or non-empty paragraph."""
    for line in body.splitlines():
        line = line.strip()
        if not line or line in ("---", "..."):
            continue
        if line.startswith("#"):
            return line.lstrip("#").strip()
        return line[:200]
    return ""


class SkillManager:
    """Discovers filesystem skills and aggregates plugin-contributed ones."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.skills_dir = self.base_dir / "skills"
        self._skills: dict[str, Skill] = {}
        self.discover()

    # ── discovery ──────────────────────────────────────────────────────────
    def discover(self) -> None:
        """(Re)scan the skills/ folder and rebuild the file-skill index.

        Plugin-contributed skills are preserved across a re-scan. On a name
        collision between a file skill and a plugin skill, the local file wins
        (a user's own override beats a plugin-packaged skill).
        """
        plugin_skills = {
            name: skill
            for name, skill in self._skills.items()
            if skill.source == "plugin"
        }
        self._skills = dict(plugin_skills)

        if not self.skills_dir.is_dir():
            return

        for path in sorted(self.skills_dir.glob("*.md")):
            self._register_file_skill(path)
        for path in sorted(self.skills_dir.glob("*/SKILL.md")):
            self._register_file_skill(path)

    def _register_file_skill(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            body = body.strip()  # consistent trimming with the frontmatter path
            name = str(meta.get("name") or path.stem).strip()
            description = str(meta.get("description") or _fallback_description(body)).strip()
            if not name or not body:
                return
            self._skills[name] = Skill(
                name=name,
                description=description,
                body=body,
                source="file",
                path=path,
            )
        except (OSError, UnicodeDecodeError, ValueError):
            # Skip unreadable / non-UTF-8 / malformed files without aborting
            # the whole discovery pass.
            pass

    # ── registration (plugins) ─────────────────────────────────────────────
    def register(self, name: str, description: str, content: str, source: str = "plugin") -> None:
        """Register a skill contributed by a plugin (or anything external)."""
        name = (name or "").strip()
        if not name or not content:
            return
        self._skills[name] = Skill(
            name=name,
            description=(description or "").strip(),
            body=content.strip(),
            source=source,
        )

    # ── queries ────────────────────────────────────────────────────────────
    def list_skills(self) -> list[dict]:
        """All skills as [{name, description}] catalog entries, sorted."""
        return sorted(
            (s.to_catalog_entry() for s in self._skills.values()),
            key=lambda entry: entry["name"],
        )

    def all_skills(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def get(self, name: str) -> Optional[Skill]:
        if not name:
            return None
        exact = self._skills.get(name)
        if exact:
            return exact
        lower = name.lower()
        for key, skill in self._skills.items():
            if key.lower() == lower:
                return skill
        return None

    def load_skill(self, name: str) -> Optional[str]:
        """Full markdown body for a skill, or None when not found."""
        skill = self.get(name)
        return skill.body if skill else None


_skill_manager: Optional[SkillManager] = None


def reset_skill_manager() -> None:
    """Drop the shared singleton so the next get_skill_manager() rebuilds it.

    Mainly for tests that need an isolated manager bound to a temp base_dir.
    """
    global _skill_manager
    _skill_manager = None


def get_skill_manager(base_dir: Optional[Path] = None) -> SkillManager:
    """Shared process-wide SkillManager (lazily created once).

    ``base_dir`` is only honored on first creation; later callers share the
    existing instance. For isolated instances in tests, construct
    ``SkillManager`` directly instead.
    """
    global _skill_manager
    if _skill_manager is None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent
        _skill_manager = SkillManager(base_dir)
    return _skill_manager

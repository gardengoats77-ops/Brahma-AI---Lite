import importlib.util
import sys
from pathlib import Path
from typing import Any


class PluginManager:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.plugins_dir = self.base_dir / "plugins"
        self.plugins: list[Any] = []
        self.rex = None
        self.skill_manager = None  # lazy-bound by collect_skills()

    def _ensure_skill_manager(self):
        """Get or create the shared SkillManager for this base_dir."""
        if self.skill_manager is not None:
            return self.skill_manager
        try:
            from skill_manager import get_skill_manager
            self.skill_manager = get_skill_manager(self.base_dir)
        except Exception:
            try:
                from skill_manager import SkillManager
                self.skill_manager = SkillManager(self.base_dir)
            except Exception as exc:
                print(f"[Plugins] SkillManager unavailable: {exc}")
                self.skill_manager = None
        return self.skill_manager

    def load_plugins(self) -> None:
        if not self.plugins_dir.exists():
            return
        for p in sorted(self.plugins_dir.glob("*.py")):
            if p.name.startswith("__"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(p.stem, str(p))
                if not spec or not spec.loader:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[p.stem] = mod
                spec.loader.exec_module(mod)
                plugin = getattr(mod, "plugin", None)
                if plugin is None:
                    # fallback: accept module with functions
                    plugin = mod
                self.plugins.append(plugin)
                print(f"[Plugins] Loaded {p.name}")
            except Exception as exc:
                print(f"[Plugins] Failed to load {p.name}: {exc}")

        # Auto-collect any skills registered by loaded plugins (get_skills hook).
        try:
            self.collect_skills()
        except Exception as exc:
            print(f"[Plugins] collect_skills failed: {exc}")

    def register_rex(self, rex_obj) -> None:
        self.rex = rex_obj
        for p in list(self.plugins):
            try:
                fn = getattr(p, "on_rex_created", None)
                if callable(fn):
                    fn(rex_obj)
            except Exception as exc:
                print(f"[Plugins] on_rex_created error: {exc}")

    def dispatch(self, hook: str, *args, **kwargs):
        """Call hook on plugins. If any plugin returns True, stop and return True."""
        for p in list(self.plugins):
            try:
                fn = getattr(p, hook, None)
                if callable(fn):
                    # call with rex if plugin expects it
                    try:
                        res = fn(*args, **kwargs, rex=self.rex)
                    except TypeError:
                        res = fn(*args, **kwargs)
                    if res is True:
                        return True
            except Exception as exc:
                print(f"[Plugins] Hook {hook} error in {getattr(p,'__name__',str(p))}: {exc}")
        return False

    # ── Skill integration ───────────────────────────────────────────────
    # Plugins can contribute reusable skills (markdown prompts) by exposing
    # a get_skills() hook returning a list of {name, description, content}
    # dicts. PluginManager collects them into the shared SkillManager so the
    # planner/executor can query them like file-based skills.

    def collect_skills(self) -> int:
        """Ask every plugin for get_skills() and register the results.

        Returns the number of skills registered. Errors are logged and
        skipped — one bad plugin never blocks the rest.
        """
        sm = self._ensure_skill_manager()
        if sm is None:
            return 0
        count = 0
        for p in list(self.plugins):
            try:
                fn = getattr(p, "get_skills", None)
                if not callable(fn):
                    continue
                for entry in fn() or []:
                    name = entry.get("name")
                    content = entry.get("content")
                    if not name or content is None:
                        continue  # skip incomplete entries
                    sm.register(
                        name=name,
                        description=entry.get("description", ""),
                        content=content,
                        source="plugin",
                    )
                    count += 1
            except Exception as exc:
                print(f"[Plugins] get_skills error in {getattr(p,'__name__',str(p))}: {exc}")
        return count

    def load_skill(self, name: str):
        """Return the markdown body for a skill name, or None."""
        sm = self._ensure_skill_manager()
        if sm is None:
            return None
        return sm.load_skill(name)

    def list_skills(self) -> list[dict]:
        """Return the catalog list of skills (name + description)."""
        sm = self._ensure_skill_manager()
        if sm is None:
            return []
        return sm.list_skills()

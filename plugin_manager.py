import importlib.util
import sys
from pathlib import Path
from typing import Any


class PluginManager:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.plugins_dir = self.base_dir / "plugins"
        self.plugins: list[Any] = []
        self.almighty = None

    # ── plugin loading ──────────────────────────────────────────────────────
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

        # Register any skills plugins contribute into the shared manager.
        try:
            self.collect_skills()
        except Exception as exc:
            print(f"[Plugins] collect_skills error: {exc}")

    # ── skills integration ──────────────────────────────────────────────────
    def _skill_manager(self):
        from skill_manager import get_skill_manager
        return get_skill_manager(self.base_dir)

    def collect_skills(self) -> int:
        """Register skills exported by plugins via a ``get_skills()`` hook.

        Each plugin may define ``get_skills()`` returning a list of dicts::

            [{"name": "my-skill", "description": "When to use it",
              "content": "full markdown instructions"}]

        Returns the number of skills registered.
        """
        manager = self._skill_manager()
        count = 0
        for p in list(self.plugins):
            fn = getattr(p, "get_skills", None)
            if not callable(fn):
                continue
            try:
                for item in fn() or []:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    content = str(item.get("content") or item.get("body") or "").strip()
                    if not name or not content:
                        continue
                    manager.register(
                        name,
                        description=str(item.get("description") or "").strip(),
                        content=content,
                        source="plugin",
                    )
                    count += 1
            except Exception as exc:
                print(f"[Plugins] get_skills error in {getattr(p, '__name__', str(p))}: {exc}")
        if count:
            print(f"[Plugins] Registered {count} plugin skill(s)")
        return count

    def list_skills(self) -> list[dict]:
        """All available skills (filesystem + plugins) as {name, description}."""
        return self._skill_manager().list_skills()

    def load_skill(self, name: str) -> str | None:
        """Full markdown body for a skill, or None when not found."""
        return self._skill_manager().load_skill(name)

    def register_almighty(self, almighty_obj) -> None:
        self.almighty = almighty_obj
        for p in list(self.plugins):
            try:
                fn = getattr(p, "on_almighty_created", None)
                if callable(fn):
                    fn(almighty_obj)
            except Exception as exc:
                print(f"[Plugins] on_almighty_created error: {exc}")

    def dispatch(self, hook: str, *args, **kwargs):
        """Call hook on plugins. If any plugin returns True, stop and return True."""
        for p in list(self.plugins):
            try:
                fn = getattr(p, hook, None)
                if callable(fn):
                    # call with almighty if plugin expects it
                    try:
                        res = fn(*args, **kwargs, almighty=self.almighty)
                    except TypeError:
                        res = fn(*args, **kwargs)
                    if res is True:
                        return True
            except Exception as exc:
                print(f"[Plugins] Hook {hook} error in {getattr(p,'__name__',str(p))}: {exc}")
        return False

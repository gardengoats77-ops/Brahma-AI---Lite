"""
Batch replace silent except blocks with log_error() calls.

For each file:
1. Find `except <ExceptionType>:\n    pass` patterns
2. Replace with `except <ExceptionType> as _e:\n    log_error(_e, context="module.function_name", severity="warning")`
3. Ensure `from core.error_handler import log_error` is imported
"""

import re
import sys
from pathlib import Path

# Severity mapping by file
SEVERITY_MAP = {
    "main.py": "warning",
    "ui.py": "debug",
    "dashboard/server.py": "error",
    "agent/task_queue.py": "warning",
    "core/error_handler.py": "debug",
}

# Files to skip (intentionally silent)
SKIP_FILES = {
    "core/error_handler.py",  # _rotate_logs intentionally silent
}

# Files where specific exceptions should stay silent
SILENT_EXCEPTIONS = {
    "ImportError",  # Optional dependency guards
    "ValueError",   # Parsing fallbacks
    "FileNotFoundError",  # File not found fallbacks
    "WebSocketDisconnect",  # Expected client disconnects
}


def get_severity(filepath: str) -> str:
    """Determine severity based on file path."""
    for pattern, severity in SEVERITY_MAP.items():
        if pattern in filepath:
            return severity
    return "warning"


def get_context(filepath: str, exception_type: str, line_num: int, lines: list) -> str:
    """Generate a context string for the log_error call."""
    # Try to find the enclosing function name
    for i in range(line_num - 1, -1, -1):
        line = lines[i].strip()
        match = re.match(r'def\s+(\w+)\s*\(', line)
        if match:
            return f"{Path(filepath).stem}.{match.group(1)}"
        match = re.match(r'async\s+def\s+(\w+)\s*\(', line)
        if match:
            return f"{Path(filepath).stem}.{match.group(1)}"
    return f"{Path(filepath).stem}.unknown"


def fix_file(filepath: Path) -> int:
    """Fix silent except blocks in a single file. Returns count of replacements."""
    if str(filepath) in SKIP_FILES:
        return 0

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return 0

    lines = content.split("\n")
    replacements = 0
    new_lines = []
    i = 0
    has_log_error_import = "from core.error_handler import log_error" in content

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Match: except SomeException:
        except_match = re.match(r'^(\s*)except\s+(\w[^:]*)\s*:\s*$', stripped if not stripped.startswith('#') else '')
        
        if not except_match:
            # Also try matching with the original line
            except_match = re.match(r'^(\s*)except\s+(\w[^:]*)\s*:\s*$', line)

        if except_match:
            indent = except_match.group(1) or line[:len(line) - len(line.lstrip())]
            exc_type = except_match.group(2).strip()

            # Skip specific exceptions that should stay silent
            if exc_type in SILENT_EXCEPTIONS:
                new_lines.append(line)
                i += 1
                continue

            # Look ahead for `pass` on the next non-empty line
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1

            if j < len(lines) and lines[j].strip() == "pass":
                # Found silent except block — replace
                context = get_context(filepath, exc_type, i, lines)
                severity = get_severity(str(filepath))

                # Replace the except line
                new_lines.append(f"{indent}except {exc_type} as _e:")
                # Replace pass with log_error
                pass_indent = lines[j][:len(lines[j]) - len(lines[j].lstrip())]
                new_lines.append(f"{pass_indent}log_error(_e, context=\"{context}\", severity=\"{severity}\")")

                replacements += 1
                i = j + 1
                continue

        new_lines.append(line)
        i += 1

    if replacements > 0:
        new_content = "\n".join(new_lines)

        # Add import if needed
        if not has_log_error_import and "log_error" in new_content:
            # Find the right place to add the import
            import_added = False
            for idx, l in enumerate(new_lines):
                if l.startswith("from core.error_handler import"):
                    # Already has some error_handler imports — check if log_error is there
                    if "log_error" not in l:
                        new_lines[idx] = l.rstrip() + ", log_error"
                        new_content = "\n".join(new_lines)
                    import_added = True
                    break
                elif l.startswith("from core.") or l.startswith("import core."):
                    # Add before this line
                    new_lines.insert(idx, "from core.error_handler import log_error")
                    new_content = "\n".join(new_lines)
                    import_added = True
                    break

            if not import_added:
                # Add after the last import line
                for idx in range(len(new_lines) - 1, -1, -1):
                    if new_lines[idx].startswith("import ") or new_lines[idx].startswith("from "):
                        new_lines.insert(idx + 1, "from core.error_handler import log_error")
                        new_content = "\n".join(new_lines)
                        break

        filepath.write_text(new_content, encoding="utf-8")
        print(f"  Fixed {filepath}: {replacements} replacements")

    return replacements


def main():
    root = Path(".")

    # Files to fix (from the audit)
    files_to_fix = [
        "main.py",
        "ui.py",
        "actions/computer_control.py",
        "actions/daily_briefing.py",
        "actions/desktop.py",
        "actions/file_controller.py",
        "actions/flight_finder.py",
        "actions/game_updater.py",
        "actions/redteam_tools.py",
        "actions/system_monitor.py",
        "agent/task_queue.py",
        "dashboard/server.py",
    ]

    total = 0
    for f in files_to_fix:
        filepath = root / f
        if filepath.exists():
            total += fix_file(filepath)

    print(f"\nTotal replacements: {total}")


if __name__ == "__main__":
    main()

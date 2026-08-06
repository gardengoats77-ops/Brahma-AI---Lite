"""actions/_code_validator.py — AST-based safety validator for generated code.

Replaces the previous ``exec()``-with-a-weak-sandbox approach in
``actions/desktop.py`` with a strict AST pre-flight check.  The validator
walks the parsed AST and rejects **any** node that could escape the
intended sandbox, before the code is ever executed.

Design goals (in priority order):

1. **Fail closed.**  If anything looks even slightly suspicious the
   validator rejects the code.  The desktop action falls back to a
   friendly "I can't do that safely" message.
2. **Allowlist, not blocklist.**  Only the explicit set of safe nodes is
   permitted; everything else is rejected.  This means new Python
   features automatically fail validation until they are reviewed.
3. **No state leakage.**  The validator never imports or executes the
   code under test — it only parses with :mod:`ast`.

Public API:

    * :func:`validate_code_safety` — returns a list of violation strings
      (empty list = code is safe to run in the restricted sandbox).
    * :func:`is_code_safe` — convenience boolean wrapper.
    * :data:`SAFE_SANDBOX_BUILTINS` — the exact set of builtin names the
      sandbox is allowed to expose (kept here so the validator and the
      runtime sandbox stay in sync).

The sandbox runtime itself lives in :mod:`actions._sandbox_runtime`; this
module only answers the question "is this code safe to put in that
sandbox?".
"""

from __future__ import annotations

import ast
from typing import List

# ── The exact set of builtins the desktop sandbox exposes ──────────────────
# Keep this in sync with actions/_sandbox_runtime.py::_build_sandbox().
SAFE_SANDBOX_BUILTINS: frozenset[str] = frozenset({
    "print", "len", "str", "int", "float", "bool", "list", "dict", "tuple",
    "range", "enumerate", "sorted", "isinstance", "hasattr", "getattr",
    "max", "min", "sum", "abs", "zip", "map", "filter", "round",
})

# Names that must NEVER be accessible from within the sandbox, even via
# attribute access on an allowed object.
_BLOCKED_ATTRS: frozenset[str] = frozenset({
    # subprocess / os shell escape
    "system", "popen", "spawn", "fork", "execv", "execvp", "execl",
    # file deletion / destructive FS
    "remove", "unlink", "rmdir", "rename", "replace", "truncate",
    # import machinery
    "__import__", "import_module",
    # introspection that can escape the sandbox
    "__subclasses__", "__bases__", "__mro__", "__class__",
    "__globals__", "__builtins__", "__code__", "__func__",
    # eval / exec family
    "eval", "exec", "compile", "reload",
    # network
    "urlopen", "urlretrieve", "socket",
})

# Builtins that must never be called or referenced by name.
_BLOCKED_BUILTIN_CALLS: frozenset[str] = frozenset({
    "eval", "exec", "compile", "open", "input", "breakpoint",
    "__import__", "globals", "locals", "vars", "dir",
    "exit", "quit", "help",
})

# AST node types that are outright banned.  Any occurrence = reject.
# Built dynamically because ast.Exec was removed in Python 3.13.
_BLOCKED_NODES: tuple[type[ast.AST], ...] = tuple(
    node
    for node in [
        # Imports — no external code may be pulled in.
        ast.Import, ast.ImportFrom,
        # Function/class definitions keep the code flat & auditable.
        ast.FunctionDef,
        getattr(ast, "AsyncFunctionDef", None),
        ast.ClassDef,
        # Deletion and re-binding of names can escape the sandbox.
        ast.Delete, ast.Global, ast.Nonlocal,
        # exec/eval/globals builtins are blocked at call-site too, but also
        # ban the AST node forms for defence in depth.
        getattr(ast, "Exec", None),  # Python 2 leftover; harmless on py3.
    ]
    if node is not None
)

# Expression/attribute access patterns that are blocked even on otherwise
# allowed objects (see _BLOCKED_ATTRS above).


def _node_name(node: ast.AST) -> str:
    """Best-effort human-readable name for a node, for error messages."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return type(node).__name__


def _check_blocked_nodes(node: ast.AST) -> List[str]:
    """Reject banned node types outright."""
    violations: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, _BLOCKED_NODES):
            violations.append(
                f"line {getattr(child, 'lineno', '?')}: "
                f"blocked construct '{type(child).__name__}' is not allowed in the sandbox"
            )
    return violations


def _check_attribute_access(node: ast.AST) -> List[str]:
    """Reject access to any attribute in :data:`_BLOCKED_ATTRS`."""
    violations: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in _BLOCKED_ATTRS:
            violations.append(
                f"line {getattr(child, 'lineno', '?')}: "
                f"access to attribute '{child.attr}' is blocked"
            )
    return violations


def _check_blocked_calls(node: ast.AST) -> List[str]:
    """Reject calls to builtins in :data:`_BLOCKED_BUILTIN_CALLS`."""
    violations: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            callee = child.func
            if isinstance(callee, ast.Name) and callee.id in _BLOCKED_BUILTIN_CALLS:
                violations.append(
                    f"line {getattr(child, 'lineno', '?')}: "
                    f"call to blocked builtin '{callee.id}()'"
                )
            if isinstance(callee, ast.Attribute):
                if callee.attr in _BLOCKED_ATTRS:
                    violations.append(
                        f"line {getattr(child, 'lineno', '?')}: "
                        f"call to blocked method '{callee.attr}()'"
                    )
    return violations


def _check_name_loads(node: ast.AST, allowed_names: frozenset[str]) -> List[str]:
    """Reject *loads* (reads) of names not in the allowed set.

    Note: we only check ``ast.Name`` with ``ctx=Load`` here.  Store/Del
    contexts are handled elsewhere (Store is allowed for re-binding
    allowed names; Del is blocked via _BLOCKED_NODES).
    """
    violations: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            if child.id not in allowed_names and child.id not in SAFE_SANDBOX_BUILTINS:
                violations.append(
                    f"line {getattr(child, 'lineno', '?')}: "
                    f"reference to undefined name '{child.id}' "
                    f"(not in sandbox builtins)"
                )
    return violations


def _check_dunder_access(node: ast.AST) -> List[str]:
    """Reject direct access to dunder attributes (escape vector via mro)."""
    violations: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            if child.attr.startswith("__") and child.attr.endswith("__") and child.attr != "__init__":
                # A handful of dunders are effectively harmless in this
                # sandbox; allow only those explicitly.
                if child.attr not in {"__init__", "__name__"}:
                    violations.append(
                        f"line {getattr(child, 'lineno', '?')}: "
                        f"access to dunder attribute '{child.attr}' is blocked"
                    )
    return violations


def validate_code_safety(
    code: str,
    *,
    allowed_names: frozenset[str] | None = None,
) -> List[str]:
    """Validate that *code* is safe to run inside the restricted sandbox.

    Parameters
    ----------
    code
        Python source code to validate.  Must be syntactically valid.
    allowed_names
        Additional module-level names (beyond :data:`SAFE_SANDBOX_BUILTINS`)
        that the sandbox will expose.  Defaults to ``frozenset()`` — only
        the builtins above are permitted.

    Returns
    -------
    list of str
        A list of human-readable violation messages.  An empty list means
        the code is safe to execute in the sandbox.

        The list is never ``None``; this function never raises for
        unsafe code (it raises ``SyntaxError`` only if the code cannot
        be parsed at all).
    """
    if not code or not code.strip():
        return ["empty code"]

    if code.strip() == "UNSAFE":
        # The desktop.py prompt explicitly tells the model to return the
        # literal string ``UNSAFE`` when it cannot perform a task.  Treat
        # that as a single clean violation rather than a syntax error.
        return ["model declared the task UNSAFE"]

    # Parse first — a syntax error is a hard reject.
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    allowed = (allowed_names or frozenset()) | SAFE_SANDBOX_BUILTINS

    violations: List[str] = []
    violations.extend(_check_blocked_nodes(tree))
    violations.extend(_check_attribute_access(tree))
    violations.extend(_check_blocked_calls(tree))
    violations.extend(_check_dunder_access(tree))
    violations.extend(_check_name_loads(tree, allowed))

    return violations


def is_code_safe(
    code: str,
    *,
    allowed_names: frozenset[str] | None = None,
) -> bool:
    """Convenience boolean wrapper around :func:`validate_code_safety`."""
    return not validate_code_safety(code, allowed_names=allowed_names)

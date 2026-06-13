"""Rewrite a vendored hermes-agent TEST file into the component namespace.

Companion to ``rewrite_imports`` (used for the production _vendor tree). A test
file needs both rewrites:

  1. Its real ``import`` / ``from ... import`` statements -> delegated verbatim
     to ``rewrite_imports`` (AST-anchored; docstrings/identifiers untouched).
  2. The *string-literal* module paths the import rewriter deliberately leaves
     alone, but which only occur in tests: the first string argument of
     ``patch(...)`` (and ``mock.patch``), ``monkeypatch.setattr/delattr(...)``,
     ``importlib.import_module(...)``, the key of ``sys.modules[...]``, and a
     ``logger=`` keyword (e.g. ``caplog.at_level``).

Both steps route through the SAME ``rewrite_imports._target`` mapping, so a
string is sent to ``_vendor`` / ``_shims`` exactly as an import of the same
module would be. This keeps the provenance "verbatim + a mechanical
import-and-patch-target rewrite".

Strings that are NOT in those call positions are left byte-for-byte: assertion
expected-values, ``mock.assert_called_once_with("tools.x")``,
``patch.dict("os.environ", ...)``, and ``patch.object(obj, "attr")`` attribute
names. ``_target`` returns ``None`` for anything whose first segment is neither a
vendored top nor a shim prefix, so unrelated strings are never touched.

Run over a tree:  ``python rewrite_test_imports.py <dir-or-file>``
"""
import ast

from rewrite_imports import _target, rewrite_imports

# Call funcs (by name or attribute tail) whose FIRST positional string arg is a
# module path to rewrite. ``patch`` covers ``patch(...)``, ``mock.patch(...)``
# and the ``@patch(...)`` decorator form (all are ``ast.Call``). ``patch.object``
# has attr ``object`` -> excluded, so its attr-name string stays untouched.
_FIRST_ARG_FUNCS = {"patch", "setattr", "delattr", "import_module"}


def _maybe(edits, node):
    """Record (node, target) if *node* is a string literal that maps to a namespace."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        target = _target(node.value)
        if target:
            edits.append((node, target))


def _string_target_edits(tree):
    """Collect (Constant, new_value) edits for rewritable string-literal module paths."""
    edits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in _FIRST_ARG_FUNCS and node.args:
                _maybe(edits, node.args[0])
            for kw in node.keywords:
                if kw.arg == "logger":
                    _maybe(edits, kw.value)
        elif isinstance(node, ast.Subscript):
            val = node.value
            if (
                isinstance(val, ast.Attribute)
                and val.attr == "modules"
                and isinstance(val.value, ast.Name)
                and val.value.id == "sys"
            ):
                sl = node.slice
                _maybe(edits, sl if isinstance(sl, ast.Constant) else getattr(sl, "value", sl))
    return edits


def rewrite_test_source(source):
    """Rewrite import statements AND string-literal module-path targets in a test file."""
    source = rewrite_imports(source)  # step 1: real import statements
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    edits = _string_target_edits(tree)
    if not edits:
        return source

    lines = source.splitlines(keepends=True)
    # Apply last-to-first so earlier spans keep their offsets.
    for node, new_val in sorted(edits, key=lambda e: (e[0].lineno, e[0].col_offset), reverse=True):
        # Module-path string literals are single-line and ASCII; bail on anything
        # that doesn't look right rather than risk corrupting the line.
        if node.end_lineno != node.lineno:
            continue
        idx = node.lineno - 1
        line = lines[idx]
        start, end = node.col_offset, node.end_col_offset
        if start >= len(line) or line[start] not in "\"'":
            continue  # offset/quote mismatch (e.g. non-ASCII earlier on the line)
        quote = line[start]
        lines[idx] = line[:start] + quote + new_val + quote + line[end:]
    return "".join(lines)


def _apply_to_tree(root):
    """Rewrite every .py under *root* (or a single file) in place. Idempotent."""
    import pathlib

    path = pathlib.Path(root)
    files = [path] if path.is_file() else sorted(path.rglob("*.py"))
    changed = 0
    for p in files:
        original = p.read_text(encoding="utf-8")
        rewritten = rewrite_test_source(original)
        if rewritten != original:
            p.write_text(rewritten, encoding="utf-8")
            changed += 1
    return changed


if __name__ == "__main__":
    import sys

    target = sys.argv[1]
    print(f"rewrote {_apply_to_tree(target)} files under {target}")

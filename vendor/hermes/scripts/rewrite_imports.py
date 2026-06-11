"""Rewrite the vendored hermes-agent tree's absolute imports into the component namespace.

AST-targeted: only the physical lines that contain a real ``import`` / ``from ... import``
statement are rewritten, so identifiers, comments, and import-looking lines inside
docstrings/strings are left byte-for-byte. This keeps the _vendor tree's provenance as
"verbatim + a mechanical import-rewrite".

Mapping:
  tools.* / agent.* / hermes_constants / utils  ->  calfkit_tools.hermes._vendor.*
  app-runtime edges (hermes_cli.* / gateway.* / model_tools / agent.auxiliary_client /
    agent.lsp[.*])                              ->  calfkit_tools.hermes._shims.*
Relative imports (`from .x`) are left as-is (they stay valid after relocation).
"""
import ast
import re

_VENDOR = "calfkit_tools.hermes._vendor"
_SHIM = "calfkit_tools.hermes._shims"
_VENDOR_TOPS = ("tools", "agent", "hermes_constants", "utils")
# App-runtime edges that become shims. Shim prefixes are matched BEFORE the vendor-tops
# check, so the specific `agent.lsp` / `agent.auxiliary_client` win over the general
# `agent.*` vendor rule.
_SHIM_PREFIXES = ("agent.auxiliary_client", "agent.lsp", "hermes_cli", "gateway", "model_tools")

_FROM_RE = re.compile(r"^(\s*from\s+)([A-Za-z_][\w.]*)")
_IMPORT_RE = re.compile(r"^(\s*import\s+)([A-Za-z_][\w.]*)")


def _target(module):
    """Return the rewritten module path, or None to leave the import unchanged."""
    for prefix in _SHIM_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return f"{_SHIM}.{module}"
    if module.split(".", 1)[0] in _VENDOR_TOPS:
        return f"{_VENDOR}.{module}"
    return None


def _rewrite_line(line):
    def repl(m):
        target = _target(m.group(2))
        return m.group(1) + target if target else m.group(0)

    line = _FROM_RE.sub(repl, line)
    return _IMPORT_RE.sub(repl, line)


def rewrite_imports(source):
    """Rewrite absolute imports of vendored / app-runtime modules into the namespace.

    Only real import statements (located via the AST) are touched.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source  # leave files we can't parse untouched

    import_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — stays valid after relocation
                continue
            import_lines.add(node.lineno)
        elif isinstance(node, ast.Import):
            import_lines.add(node.lineno)
    if not import_lines:
        return source

    lines = source.splitlines(keepends=True)
    for lineno in import_lines:
        idx = lineno - 1
        if 0 <= idx < len(lines):
            lines[idx] = _rewrite_line(lines[idx])
    return "".join(lines)


def _apply_to_tree(root):
    """Rewrite every .py under *root* in place. Idempotent. Returns count changed."""
    import pathlib

    changed = 0
    for path in sorted(pathlib.Path(root).rglob("*.py")):
        original = path.read_text(encoding="utf-8")
        rewritten = rewrite_imports(original)
        if rewritten != original:
            path.write_text(rewritten, encoding="utf-8")
            changed += 1
    return changed


if __name__ == "__main__":
    import sys

    target = sys.argv[1]
    print(f"rewrote {_apply_to_tree(target)} files under {target}")

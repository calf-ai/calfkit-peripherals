"""Rewrite the vendored hermes-agent tree's absolute imports into the component namespace.

Line-oriented (anchored on `from`/`import` statements) so it never touches identifiers
or strings that merely look like module paths.
"""
import re

_VENDOR = "calfkit_hermes._vendor"
_SHIM = "calfkit_hermes._shims"
_VENDOR_TOPS = ("tools", "agent", "hermes_constants", "utils")
# App-runtime edges that become shims. Order doesn't matter for correctness here
# (each is matched as a whole dotted prefix), but the SHIM check runs BEFORE the
# vendor-tops check so the specific `agent.lsp` / `agent.auxiliary_client` win over
# the general `agent.*` vendor rule (most-specific-prefix-wins).
_SHIM_PREFIXES = ("agent.auxiliary_client", "agent.lsp", "hermes_cli", "gateway", "model_tools")


def _target(module):
    """Return the rewritten module path, or None to leave the import unchanged."""
    for prefix in _SHIM_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return f"{_SHIM}.{module}"
    first = module.split(".", 1)[0]
    if first in _VENDOR_TOPS:
        return f"{_VENDOR}.{module}"
    return None


def rewrite_imports(source):
    def repl(m):
        target = _target(m.group(2))
        if target is None:
            return m.group(0)
        return m.group(1) + target

    # `from MODULE import ...` and `import MODULE [as ...]`. Both anchored at the start
    # of a (possibly indented) line so identifiers/strings that merely contain a dotted
    # name are never touched. Leading-dot relative imports don't match `[A-Za-z_]` and
    # are left as-is (they stay valid after relocation).
    source = re.sub(r"^(\s*from\s+)([A-Za-z_][\w.]*)", repl, source, flags=re.M)
    source = re.sub(r"^(\s*import\s+)([A-Za-z_][\w.]*)", repl, source, flags=re.M)
    return source


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

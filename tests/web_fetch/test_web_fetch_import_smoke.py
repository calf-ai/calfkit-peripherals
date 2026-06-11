"""Import-smoke for the web_fetch source: every rewritten vendored module imports
cleanly, and no un-inlined ``pydantic_ai`` import survived the port. Parity with the
hermes import-smoke guards (tests/hermes/test_import_smoke.py).
"""

import importlib
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def test_all_web_fetch_modules_import():
    files = sorted(SRC.glob("calfkit_tools/web_fetch/**/*.py"))
    assert files, f"glob found no modules under {SRC} — package layout changed?"
    failures = {}
    for path in files:
        mod = ".".join(path.relative_to(SRC).with_suffix("").parts)
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            failures[mod] = f"{type(e).__name__}: {e}"
    assert not failures, "modules failed to import:\n" + "\n".join(
        f"  {m}: {e}" for m, e in failures.items()
    )


def test_no_pydantic_ai_imports_survive():
    """The port inlines the few pydantic-ai leaf helpers (ADR-0001); no ``pydantic_ai``
    import should remain anywhere in the vendored web_fetch tree."""
    files = sorted(SRC.glob("calfkit_tools/web_fetch/**/*.py"))
    assert files
    offenders = [
        f"{path.relative_to(SRC)}:{i}: {line.strip()}"
        for path in files
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if re.match(r"^\s*(from|import)\s+pydantic_ai(\.|\s|$)", line)
    ]
    assert not offenders, "un-inlined pydantic_ai imports remain:\n" + "\n".join(offenders)

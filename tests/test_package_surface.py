"""Unified-distribution surface.

The merge's whole reason to exist: both vendored sources coexist under the single
``calfkit_tools`` namespace and expose their tool nodes. This contract is owned by
neither per-source sub-suite (each of those tests only its own source in isolation).
"""

import subprocess
import sys

EXPECTED_HERMES_TOOL_NAMES = {
    "terminal",
    "process",
    "execute_code",
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "todo",
    "web_search",
    "web_extract",
}


def _tool_name(node_id: str) -> str:
    """The tool name calfkit derives the node_id from.

    calfkit 0.12 dropped the historical ``tool_`` node-id prefix (topics stay
    ``tool.<name>.*``). Stripping the optional prefix lets this surface contract
    hold across the supported calfkit range (>=0.9,<0.13). No tool name itself
    begins with ``tool_``, so the strip is unambiguous.
    """
    return node_id.removeprefix("tool_")


def test_unified_namespace_exposes_both_sources():
    from calfkit_tools.hermes.node import HERMES_NODES
    from calfkit_tools.web_fetch.node import web_fetch

    assert len(HERMES_NODES) == 10
    assert {_tool_name(n.node_id) for n in HERMES_NODES} == EXPECTED_HERMES_TOOL_NAMES
    assert _tool_name(web_fetch.node_id) == "web_fetch"


def test_no_node_id_collision_across_sources():
    from calfkit_tools.hermes.node import HERMES_NODES
    from calfkit_tools.web_fetch.node import web_fetch

    all_ids = [n.node_id for n in HERMES_NODES] + [web_fetch.node_id]
    assert len(all_ids) == len(set(all_ids)), "node_id collision across sources"


def test_top_level_import_is_side_effect_free():
    """``import calfkit_tools`` must pull in no source subpackage or optional tool
    dependency — the documented contract in ``src/calfkit_tools/__init__.py``. Run
    in a fresh subprocess so an earlier test that already imported a ``.node`` module
    can't mask a regression via a warm ``sys.modules``.
    """
    probe = (
        "import sys, calfkit_tools\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m.startswith('calfkit_tools.')\n"
        "    or m.split('.')[0] in {'httpx', 'ddgs', 'psutil', 'markdownify', 'ptyprocess'}\n"
        ")\n"
        "assert not leaked, leaked\n"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)

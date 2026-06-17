"""The public tool API for calfkit-tools.

Import any tool from this module::

    from calfkit_tools.tools import terminal, read_file, web_search, web_fetch

or grab the whole set to host in one worker::

    from calfkit_tools.tools import ALL_TOOLS

Each tool is a calfkit ``ToolNodeDef``. ``InMemoryTodoStore`` is the in-memory
resource the ``todo`` tool binds, and ``ALL_TOOLS`` is the list of every tool
node.

Tools are resolved lazily (PEP 562) on first attribute access, so importing
this module stays cheap and pulls in only the tools you touch. The internal
layout — one subpackage per vendored upstream source — is an implementation
detail and never appears in this surface.
"""

from __future__ import annotations

import importlib

# Public tool name -> the internal module that defines it. The source-subpackage
# layout is an implementation detail; consumers import everything from here.
_EXPORTS: dict[str, str] = {
    "terminal": "calfkit_tools.hermes.node",
    "process": "calfkit_tools.hermes.node",
    "read_file": "calfkit_tools.hermes.node",
    "write_file": "calfkit_tools.hermes.node",
    "patch": "calfkit_tools.hermes.node",
    "search_files": "calfkit_tools.hermes.node",
    "execute_code": "calfkit_tools.hermes.node",
    "todo": "calfkit_tools.hermes.node",
    "web_search": "calfkit_tools.hermes.node",
    "web_extract": "calfkit_tools.hermes.node",
    "InMemoryTodoStore": "calfkit_tools.hermes.node",
    "web_fetch": "calfkit_tools.web_fetch.node",
}

__all__ = [*sorted(_EXPORTS), "ALL_TOOLS"]

# Served lazily by ``__getattr__`` below. Declared (annotation only, never bound)
# so it is a defined name for ``__all__`` and type checkers without eagerly
# building the bundle on import.
ALL_TOOLS: list


def _load_all_tools() -> list:
    """Build the all-tools bundle, importing each source module on demand."""
    from calfkit_tools.hermes.node import HERMES_NODES
    from calfkit_tools.web_fetch.node import web_fetch

    return [*HERMES_NODES, web_fetch]


def __getattr__(name: str):
    """Resolve a public export lazily (PEP 562).

    Deliberately uncached: it resolves against the live source module on every
    access, so a re-export is always identical to the object the source module
    currently holds (robust if a source module is reloaded). ``import_module``
    of an already-imported module is a cheap ``sys.modules`` lookup.
    """
    if name == "ALL_TOOLS":
        return _load_all_tools()
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__() -> list[str]:
    return sorted(__all__)

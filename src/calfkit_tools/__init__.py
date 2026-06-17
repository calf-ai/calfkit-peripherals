"""calfkit-tools — agent tools for the calfkit SDK.

Import tools from :mod:`calfkit_tools.tools`::

    from calfkit_tools.tools import terminal, read_file, web_search, web_fetch
    from calfkit_tools.tools import ALL_TOOLS   # every tool node

This top-level package is intentionally side-effect free: importing
``calfkit_tools`` pulls in no tool code or optional dependencies. Import
``calfkit_tools.tools`` (which resolves each tool lazily on first access) for
the tools you need.
"""

__all__: list[str] = []

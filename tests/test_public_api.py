"""The public tool API: every tool importable from ``calfkit_tools.tools``.

The internal layout (one subpackage per vendored upstream source) is an
implementation detail. Consumers import tools from ``calfkit_tools.tools``;
these tests pin that surface and the lazy-import contract that keeps importing
the top-level package side-effect free.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from calfkit_tools import tools

# The 11 deployable tool nodes, by their public name.
EXPECTED_TOOLS = {
    "terminal",
    "process",
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "execute_code",
    "todo",
    "web_search",
    "web_extract",
    "web_fetch",
}
# Non-tool exports: the todo resource and the all-tools bundle.
EXPECTED_EXTRA = {"InMemoryTodoStore", "ALL_TOOLS"}


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_every_tool_importable_from_tools_namespace(name):
    assert hasattr(tools, name)


def test_reexport_is_the_same_object_as_the_source():
    from calfkit_tools.tools import terminal, web_fetch
    from calfkit_tools.hermes.node import terminal as hermes_terminal
    from calfkit_tools.web_fetch.node import web_fetch as wf

    assert terminal is hermes_terminal
    assert web_fetch is wf


def test_in_memory_todo_store_reexported():
    from calfkit_tools.tools import InMemoryTodoStore
    from calfkit_tools.hermes.node import InMemoryTodoStore as source

    assert InMemoryTodoStore is source


def test_all_tools_bundle_is_every_node():
    from calfkit_tools.tools import ALL_TOOLS
    from calfkit_tools.hermes.node import HERMES_NODES
    from calfkit_tools.web_fetch.node import web_fetch

    assert len(ALL_TOOLS) == 11
    assert list(ALL_TOOLS) == [*HERMES_NODES, web_fetch]


def test_dir_and_all_expose_the_public_names():
    public = EXPECTED_TOOLS | EXPECTED_EXTRA
    assert public <= set(tools.__all__)
    assert public <= set(dir(tools))


def test_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError):
        tools.does_not_exist  # noqa: B018


def test_importing_top_level_package_is_side_effect_free():
    # A fresh interpreter: neither importing the top-level package NOR the tools
    # namespace may eagerly pull in tool modules (or their optional deps).
    # Accessing a specific tool is what triggers the lazy load.
    code = (
        "import sys, calfkit_tools;"
        "assert 'calfkit_tools.hermes.node' not in sys.modules, 'top-level import leak';"
        "import calfkit_tools.tools;"
        "assert 'calfkit_tools.hermes.node' not in sys.modules, 'tools import is not lazy';"
        "from calfkit_tools.tools import terminal;"
        "assert 'calfkit_tools.hermes.node' in sys.modules, 'lazy import did not fire';"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"

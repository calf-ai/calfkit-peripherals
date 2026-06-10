"""calfkit tool nodes for the hermes-agent vendor — one deployable node per tool.

Each export is a calfkit ``ToolNodeDef`` (Stage D, docs/design/node-port.md):
import it, hand it to a ``Worker`` (or ``calfkit run calfkit_hermes.node:terminal``),
and it serves its tool over the Kafka mesh on name-derived topics
(``tool.<name>.input`` / ``tool.<name>.output``).

``HERMES_NODES`` is a convenience list for hosting every hermes node in one
Worker — each entry remains its own node (own topics, own identity), not a
proxy. Stateful nodes are correct at one process per node (ADR-0004).
"""

from calfkit_hermes.node.files import patch, read_file, search_files, write_file
from calfkit_hermes.node.shell import process, terminal
from calfkit_hermes.node.todo import InMemoryTodoStore, todo
from calfkit_hermes.node.web import web_extract, web_search

HERMES_NODES = [
    terminal,
    process,
    read_file,
    write_file,
    patch,
    search_files,
    todo,
    web_search,
    web_extract,
]

__all__ = [
    "HERMES_NODES",
    "InMemoryTodoStore",
    "patch",
    "process",
    "read_file",
    "search_files",
    "terminal",
    "todo",
    "web_extract",
    "web_search",
]

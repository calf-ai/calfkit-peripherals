"""The ``todo`` tool node: a per-session task list over the vendored Operator.

Design: docs/design/node-port.md §3; docs/design/todo-tool-port.md §6;
ADR-0003 (vendor the Operator, own the Store), ADR-0004 (tenancy + in-memory).

The vendored ``TodoStore`` is a stateless *Operator* (it holds a list but the
node owns its lifecycle). The node owns the *Store*: a worker-lifetime
``InMemoryTodoStore`` keyed by ``session_key`` (ADR-0004's shipped variant; the
durable Faust-backed Store is deferred). Per call the node seeds a fresh
``TodoStore`` from the Store via the ADR-0003 constructor-seed patch, dispatches
through the upstream registry seam with ``store=``, and persists
``store.read()`` back **only on writes** (``todos is not None``) — reads never
``put()`` (the id-collapse / changelog-amplification hazard of ADR-0003).

Strict item schema (deliberate): ``TodoItem`` marks ``id``/``content``/``status``
all required, so calfkit's arg validator REJECTS a partial/invalid item before
it reaches the Operator. This is intentionally stricter than upstream's
``TodoStore._validate``, which would *salvage* such items (id -> ``"?"``,
status -> ``"pending"``, content -> ``"(no description)"``). We mirror upstream's
*advertised* schema (``TODO_SCHEMA`` marks all three required) and let calfkit's
``FailedToolCall`` give the model a structured retry rather than silently
accepting a guessed-at item it never intended (see ``test_node_todo.py``
``TestStrictSchemaRejectsPartialItems``).
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from typing import Literal

from calfkit import ToolContext, agent_tool
from typing_extensions import TypedDict  # pydantic needs typing_extensions on Py < 3.12

from calfkit_tools.hermes._vendor.tools.todo_tool import TodoStore
from calfkit_tools.hermes.node._runtime import dispatch, session_key


class TodoItem(TypedDict):
    """One task-list item. Mirrors the upstream ``TODO_SCHEMA`` item shape
    (all three fields required there)."""

    id: str
    content: str
    status: Literal["pending", "in_progress", "completed", "cancelled"]


class InMemoryTodoStore:
    """In-memory per-session task-list Store (ADR-0004 shipped variant).

    A ``dict[session_key, list[item]]`` guarded by a lock. Values are stored and
    returned as copies so callers can't mutate the backing state. Durable,
    multi-tenant Stores (a Faust table) are deferred (ADR-0003); this dev/ship
    variant deliberately never evicts.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, list[dict]] = {}

    def get(self, session_key: str) -> list[dict]:
        with self._lock:
            items = self._data.get(session_key)
            return [dict(item) for item in items] if items else []

    def put(self, session_key: str, items: list[dict]) -> None:
        with self._lock:
            self._data[session_key] = [dict(item) for item in items]

    def delete(self, session_key: str) -> None:
        with self._lock:
            self._data.pop(session_key, None)


@agent_tool
def todo(
    ctx: ToolContext,
    todos: list[TodoItem] | None = None,
    merge: bool = False,
) -> dict | str:
    """Manage your task list for the current session. Use for complex tasks with
    3+ steps or when the user provides multiple tasks. Call with no parameters to
    read the current list.

    Writing:
    - Provide 'todos' array to create/update items.
    - merge=false (default): replace the entire list with a fresh plan.
    - merge=true: update existing items by id, add any new ones.

    Each item: {id: string, content: string,
    status: pending|in_progress|completed|cancelled}. List order is priority.
    Only ONE item in_progress at a time. Mark items completed immediately when
    done. If something fails, cancel it and add a revised item. Always returns
    the full current list.

    Args:
        todos: Task items to write. Omit to read the current list.
        merge: true: update existing items by id, add new ones. false (default):
            replace the entire list.
    """
    key = session_key(ctx)
    store: InMemoryTodoStore = ctx.resources["todo_state"]
    # Seed the Operator from the Store via the ADR-0003 constructor seam (no
    # re-validation of stored data).
    seeded = TodoStore(items=store.get(key))
    result = dispatch("todo", {"todos": todos, "merge": merge}, session_key=key, store=seeded)
    # Persist only on writes; reads must NOT put() (ADR-0003 id-collapse hazard).
    if todos is not None:
        store.put(key, seeded.read())
    return result


@todo.resource("todo_state")
async def _todo_state(setup_ctx) -> AsyncIterator[InMemoryTodoStore]:
    """Worker-lifetime in-memory Store for the ``todo`` node (ADR-0004)."""
    yield InMemoryTodoStore()

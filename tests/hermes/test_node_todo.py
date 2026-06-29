"""Tests for the ``todo`` tool node (Stage D).

Design: docs/design/node-port.md §3/§5; docs/design/todo-tool-port.md §6;
ADR-0003 (Operator/Store seam), ADR-0004 (tenancy + in-memory Store).

The node seeds a vendored ``TodoStore`` from a node-owned in-memory Store keyed
by ``session_key``, dispatches through the upstream registry seam with
``store=``, and persists ``store.read()`` back ONLY on writes (reads never put —
the id-collapse / changelog hazard of ADR-0003).
"""

import asyncio

from calfkit import ToolContext

from calfkit_tools.hermes._vendor.tools.todo_tool import TodoStore
from calfkit_tools.hermes.node.todo import InMemoryTodoStore, todo


def make_ctx(agent_name="agent-a", deps=None, store=None):
    """Build a ToolContext wired with the node's ``todo_state`` resource."""
    return ToolContext(
        deps=deps or {},
        agent_name=agent_name,
        tool_call_id="tc-1",
        tool_name="todo",
        messages=[],
        run_id="run-1",
        resources={"todo_state": store if store is not None else InMemoryTodoStore()},
    )


def call_todo(ctx, todos=None, merge=False):
    """Invoke the underlying node function (ctx is auto-hidden in the schema)."""
    return todo._tool.function(ctx, todos=todos, merge=merge)


# ---------------------------------------------------------------------------
# InMemoryTodoStore (the in-memory Store — ADR-0004 shipped variant)
# ---------------------------------------------------------------------------


class TestInMemoryTodoStore:
    def test_get_missing_returns_empty_list(self):
        store = InMemoryTodoStore()
        assert store.get("k") == []

    def test_put_then_get_roundtrips(self):
        store = InMemoryTodoStore()
        items = [{"id": "1", "content": "a", "status": "pending"}]
        store.put("k", items)
        assert store.get("k") == items

    def test_values_are_stored_as_copies(self):
        store = InMemoryTodoStore()
        items = [{"id": "1", "content": "a", "status": "pending"}]
        store.put("k", items)
        # Mutating the caller's list must not affect stored state.
        items.append({"id": "2", "content": "b", "status": "pending"})
        assert len(store.get("k")) == 1
        # Mutating what get() returns must not affect stored state either.
        got = store.get("k")
        got.append({"id": "x", "content": "x", "status": "pending"})
        assert len(store.get("k")) == 1

    def test_delete_removes_key(self):
        store = InMemoryTodoStore()
        store.put("k", [{"id": "1", "content": "a", "status": "pending"}])
        store.delete("k")
        assert store.get("k") == []

    def test_delete_missing_key_is_noop(self):
        InMemoryTodoStore().delete("absent")  # must not raise


# ---------------------------------------------------------------------------
# Node behaviour
# ---------------------------------------------------------------------------


class TestTodoNode:
    def test_fresh_session_reads_empty(self):
        result = call_todo(make_ctx())
        assert result["todos"] == []
        assert result["summary"]["total"] == 0

    def test_write_persists_to_store(self):
        store = InMemoryTodoStore()
        ctx = make_ctx(store=store)
        items = [{"id": "1", "content": "write tests", "status": "in_progress"}]
        result = call_todo(ctx, todos=items)
        assert result["todos"] == items
        # A NEW seeded store reading the same key sees the persisted items.
        ctx2 = make_ctx(agent_name="agent-a", store=store)
        again = call_todo(ctx2)
        assert again["todos"] == items

    def test_read_does_not_put(self):
        store = InMemoryTodoStore()
        puts = []
        original_put = store.put
        store.put = lambda key, items: puts.append(key) or original_put(key, items)
        call_todo(make_ctx(store=store))  # a read
        assert puts == []

    def test_write_calls_put(self):
        store = InMemoryTodoStore()
        puts = []
        original_put = store.put
        store.put = lambda key, items: puts.append(key) or original_put(key, items)
        call_todo(make_ctx(store=store), todos=[{"id": "1", "content": "a", "status": "pending"}])
        assert puts == ["agent-a:default"]

    def test_empty_list_is_a_write_that_clears_and_persists(self):
        """``todos=[]`` is a WRITE (``todos is not None``), not a read: it clears
        the list and DOES persist via ``store.put`` — upstream's ``todo_tool``
        treats any non-None ``todos`` as a write (``if todos is not None``)."""
        store = InMemoryTodoStore()
        # Seed a non-empty list first.
        call_todo(
            make_ctx(store=store),
            todos=[{"id": "1", "content": "a", "status": "pending"}],
        )
        puts = []
        original_put = store.put
        store.put = lambda key, items: puts.append((key, list(items))) or original_put(key, items)
        # An empty list clears and must persist (not be treated as a read).
        result = call_todo(make_ctx(store=store), todos=[])
        assert result["todos"] == []
        assert puts == [("agent-a:default", [])]
        # A fresh seeded read sees the cleared (empty) list, proving it persisted.
        assert call_todo(make_ctx(store=store))["todos"] == []

    def test_merge_is_read_modify_write(self):
        store = InMemoryTodoStore()
        call_todo(
            make_ctx(store=store),
            todos=[
                {"id": "1", "content": "first", "status": "pending"},
                {"id": "2", "content": "second", "status": "pending"},
            ],
        )
        # merge updates id=1 by id, leaving id=2 intact.
        result = call_todo(
            make_ctx(store=store),
            todos=[{"id": "1", "content": "first", "status": "completed"}],
            merge=True,
        )
        by_id = {i["id"]: i for i in result["todos"]}
        assert by_id["1"]["status"] == "completed"
        assert by_id["2"]["status"] == "pending"
        assert len(result["todos"]) == 2

    def test_isolation_across_agent_names(self):
        store = InMemoryTodoStore()
        call_todo(
            make_ctx(agent_name="agent-a", store=store),
            todos=[{"id": "1", "content": "a-task", "status": "pending"}],
        )
        # A different agent, same store, sees nothing.
        b = call_todo(make_ctx(agent_name="agent-b", store=store))
        assert b["todos"] == []

    def test_isolation_across_session_ids(self):
        store = InMemoryTodoStore()
        call_todo(
            make_ctx(deps={"session_id": "user-1"}, store=store),
            todos=[{"id": "1", "content": "u1-task", "status": "pending"}],
        )
        other = call_todo(make_ctx(deps={"session_id": "user-2"}, store=store))
        assert other["todos"] == []

    def test_seeded_items_skip_revalidation(self):
        """The ADR-0003 constructor-seed seam: stored items round-trip on read
        WITHOUT re-running _validate/_dedupe_by_id (which would collapse two
        empty-id items into one). Two items upstream validation would each map
        to id "?" must survive a read intact."""
        store = InMemoryTodoStore()
        # Two items that upstream _validate would both normalize to id "?".
        store.put(
            "agent-a:default",
            [
                {"id": "", "content": "task one", "status": "pending"},
                {"id": "", "content": "task two", "status": "pending"},
            ],
        )
        result = call_todo(make_ctx(store=store))  # a read — seeds then reads
        # Both survive: re-validation via write(stored) would have collapsed them.
        assert len(result["todos"]) == 2
        assert {i["content"] for i in result["todos"]} == {"task one", "task two"}


# ---------------------------------------------------------------------------
# Resource hook
# ---------------------------------------------------------------------------


class TestResourceHook:
    def test_todo_state_resource_registered(self):
        names = [name for name, _ in todo._resource_registry()]
        assert "todo_state" in names

    def test_resource_yields_working_store(self):
        cms = dict(todo._resource_registry())
        gen_fn = cms["todo_state"]

        async def drive():
            agen = gen_fn(None)
            store = await agen.__anext__()
            return store

        store = asyncio.run(drive())
        assert isinstance(store, InMemoryTodoStore)
        store.put("k", [{"id": "1", "content": "a", "status": "pending"}])
        assert store.get("k")[0]["id"] == "1"


# ---------------------------------------------------------------------------
# Schema sanity (catches signature drift from TODO_SCHEMA)
# ---------------------------------------------------------------------------


class TestSchema:
    def test_node_id(self):
        # calfkit derives the node_id from the tool name; 0.12 dropped the
        # historical ``tool_`` prefix (topics stay ``tool.<name>.*``). Accept
        # both so the supported range (calfkit >=0.9,<0.13) stays green.
        assert todo.node_id in ("todo", "tool_todo")

    def test_params_expose_todos_and_merge(self):
        props = todo.tool_schema.parameters_json_schema["properties"]
        assert "todos" in props
        assert "merge" in props

    def test_todos_items_have_id_content_status_enum(self):
        props = todo.tool_schema.parameters_json_schema["properties"]
        # todos is `list[TodoItem] | None` -> resolve to the array branch.
        schema = props["todos"]
        # Walk anyOf if calfkit wraps the Optional.
        defs = todo.tool_schema.parameters_json_schema.get("$defs", {})

        def resolve_item_schema(node):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                return defs[ref]
            return node

        # Find the array variant.
        candidates = schema.get("anyOf", [schema])
        array_schema = next(c for c in candidates if c.get("type") == "array")
        item = resolve_item_schema(array_schema["items"])
        item_props = item["properties"]
        assert set(item_props) >= {"id", "content", "status"}
        assert item_props["status"]["enum"] == [
            "pending",
            "in_progress",
            "completed",
            "cancelled",
        ]


# ---------------------------------------------------------------------------
# Strict-schema contract (deliberately stricter than upstream's salvage)
# ---------------------------------------------------------------------------


class TestStrictSchemaRejectsPartialItems:
    """The node's ``TodoItem`` TypedDict marks ``id``/``content``/``status`` all
    required, so calfkit's arg validator REJECTS a partial/invalid item before
    it ever reaches the Operator.

    This is *deliberately stricter* than upstream's ``TodoStore._validate``,
    which would SALVAGE such items (id -> ``"?"``, status -> ``"pending"``,
    content -> ``"(no description)"``). We keep the strict schema because it
    faithfully mirrors upstream's *advertised* schema — ``TODO_SCHEMA`` marks
    all three fields required — and a hard rejection gives the model a
    structured ``FailedToolCall`` to retry against, rather than silently
    accepting a guessed-at item the model never intended.

    The validator under test is the calfkit-built arg validator at
    ``todo._tool.function_schema.validator`` (a pydantic_core
    ``SchemaValidator`` derived from the ``TodoItem`` annotation).
    """

    def _validator(self):
        return todo._tool.function_schema.validator

    def test_missing_status_is_rejected(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc:
            # Salvageable upstream (status -> "pending"); rejected here.
            self._validator().validate_python(
                {"todos": [{"id": "1", "content": "a"}], "merge": False}
            )
        errors = exc.value.errors()
        assert any(
            e["loc"] == ("todos", 0, "status") and e["type"] == "missing"
            for e in errors
        )

    def test_bogus_status_is_rejected(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._validator().validate_python(
                {"todos": [{"id": "1", "content": "a", "status": "bogus"}], "merge": False}
            )

    def test_complete_item_is_accepted(self):
        # Sanity: a fully-specified item passes the same validator.
        out = self._validator().validate_python(
            {"todos": [{"id": "1", "content": "a", "status": "pending"}], "merge": False}
        )
        assert out["todos"][0]["status"] == "pending"

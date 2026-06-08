# calfkit-peripherals

The shared monorepo where calfkit vendors agent tools from many upstream
codebases and ports them to run as calfkit **nodes**. This glossary fixes the
language used across vendoring and porting decisions.

## Language

**Vendor**:
To bring an upstream component's source code *into* this repo (copy + port,
import-rewritten, with attribution and a pinned provenance), so calfkit can use
it. The repo's reason to exist.
_Avoid_: Buy (a colloquialism for "vendor" here — it does **not** mean take a
package-manager dependency), import, depend, copy.

**Hand-roll**:
To write first-party code from scratch instead of vendoring an existing
upstream implementation. A last resort, used only when no suitable upstream
source exists to vendor.
_Avoid_: Build, roll our own.

**Node**:
A process that speaks calfkit's Kafka protocol (consume a request topic →
produce a reply). The unit calfkit orchestrates. A node is *not* necessarily a
Python import; a vendored tool becomes usable by being wrapped as a node.
_Avoid_: Service, worker, tool-process.

**Tool**:
A capability a node exposes over its Kafka contract (e.g. `web_fetch`,
`web_search`). One vendored source can expose several tools, and one tool is the
unit a calfkit agent invokes.

**Provider** (web search):
A per-backend normalization adapter that conforms a single search engine
(ddgs, Brave, SearXNG, Exa, …) to one common result contract. Providers differ
in transport, auth, and response shape; they are interchangeable only at the
*selection* layer, not by swapping a URL.
_Avoid_: Backend (overloaded — Hermes' config key uses it), engine.

**Search** (capability):
A provider capability: given a *query string*, return ranked result links
(`title/url/description/position`). Backs the `web_search` tool.

**Extract** (capability):
A provider capability: given *URLs*, return their fetched, cleaned page content
(`title/content/raw_content/metadata`). The hosted-provider twin of `web_fetch`
— a third-party service (Tavily, Exa, …) does the fetching — as opposed to
`web_fetch`, which fetches in-process behind our own SSRF guard.
_Avoid_: Scrape; "fetch" (reserve that for the in-process `web_fetch` path).

**Operator**:
A vendored tool's logic — the input→output computation (e.g. validate / merge /
dedup a todo list) — driven so it holds no *durable* state of its own; calfkit
supplies its state via a Store. We vendor the operator when a tool's value is its
logic, not its storage. An upstream class may be *named* like a store yet be an
operator in our terms: hermes' `TodoStore` retains an in-memory list, but we drive
it per-call (seed it, apply, read the result back) so the durable copy lives in
our Store.
_Avoid_: Engine, handler, executor.

**Store**:
A pluggable, calfkit-owned state layer behind an Operator — `get` / `put` /
`delete` keyed by a `session_key`. The unit calfkit supplies so a vendored
Operator stays stateless across calls. Implementations are interchangeable: an
in-memory dict for dev/tests, a Kafka-backed Faust table for durable, multi-tenant
state. Parallels Provider, but for *state* rather than an external *backend*.
_Avoid_: Backend (reserve for Provider/transport); cache; the upstream class name
(`TodoStore` is an Operator in our terms, not a Store).

**Session**:
The unit a stateful tool's state is scoped to — one agent run, identified by a
`session_key`. A todo list (like a shell's cwd/env) belongs to exactly one session
and starts empty for a new session; it does **not** carry across sessions of the
same agent. "Durable" for session-scoped state means it survives a node
restart/rebalance *within* the session's life, not across sessions.
_Avoid_: Conversation; Agent (one agent identity can span many sessions); Task
(`task_id` is a different, finer key that upstream collapses to `"default"`).

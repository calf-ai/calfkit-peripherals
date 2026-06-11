"""Shim for hermes-agent ``agent.auxiliary_client``.

``call_llm`` makes a secondary LLM call (used only by approval's "smart approve" path,
which is non-default). calfkit wires its own LLM elsewhere; this raises so the caller's
try/except falls back to escalation (the secure direction). Stage D may wire this to
calfkit's LLM if smart-approve is wanted.
"""


def call_llm(*args, **kwargs):
    raise RuntimeError("call_llm is not available in the calfkit-tools hermes shim")

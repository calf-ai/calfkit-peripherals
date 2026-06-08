"""Shim for ``agent.lsp.reporter``. No diagnostics to report."""


def report_for_file(*args, **kwargs) -> str:
    return ""


def truncate(s: str, *, limit: int = 10000) -> str:
    if s is None:
        return s
    return s if len(s) <= limit else s[:limit]

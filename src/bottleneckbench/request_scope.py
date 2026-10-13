"""Optional condition-scoped binding for durable completion clients."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


def _scoped_client(client: object) -> object | None:
    """Find a scoped provider through transparent client wrappers."""

    current: object | None = client
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if callable(getattr(current, "bind_request_scope", None)):
            return current
        current = getattr(current, "base", None)
    return None


@contextmanager
def completion_request_scope(
    client: object,
    *,
    condition_id: str,
    repeat: int | None = None,
    attempt: int = 1,
) -> Iterator[None]:
    """Bind one episode when the provider supports durable request scopes."""

    scoped = _scoped_client(client)
    if scoped is None:
        yield
        return
    is_bound = getattr(scoped, "request_scope_bound", None)
    if callable(is_bound) and is_bound():
        yield
        return
    binder = getattr(scoped, "bind_request_scope")
    with binder(condition_id=condition_id, repeat=repeat, attempt=attempt):
        yield

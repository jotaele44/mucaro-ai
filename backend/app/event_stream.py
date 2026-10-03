"""Opt-in progress events for long-running CLI operations (agent steps, container checks).

Producers call emit(); the consumer is whatever the CLI wires in: NDJSON on stdout for
machine readers, or readable lines on stderr for people. Nothing is emitted unless a sink is given.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

EventSink = Callable[[dict[str, Any]], None]


class EventSinkError(BaseException):
    """The event consumer failed, typically because its pipe was closed.

    A BaseException on purpose: the agent loop turns ordinary tool exceptions into feedback for the model
    and carries on, which would leave a run going, and possibly mutating, with nobody watching.
    This must stop it instead, so it cannot be caught by `except Exception`.
    """

    def __init__(self, cause: Exception) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.cause = cause


def emit(sink: EventSink | None, event: str, **fields: Any) -> None:
    if sink is None:
        return
    try:
        sink({"event": event, **fields})
    except Exception as exc:
        raise EventSinkError(exc) from exc

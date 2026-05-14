"""Simple in-memory publish/subscribe event bus.

The project utilises a light‑weight event system so that disparate
components can communicate without holding direct references to each
other.  The implementation here intentionally avoids any third‑party
dependencies and keeps state in memory, which is sufficient for the unit
tests and small simulations run inside this repository.

The bus offers two basic operations:

``emit_event(event_type, payload)``
    Immediately invokes all handlers previously registered for the given
    ``event_type``.  Handlers are executed synchronously in the order they
    were registered.  Any exceptions raised by a handler are logged and do
    not stop subsequent handlers from running.

``subscribe_to_event(event_type, handler)``
    Registers ``handler`` to be called whenever ``event_type`` is emitted.
    Handlers are stored once; duplicate registrations are ignored.
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Callable, DefaultDict, Dict, List

import logging

_LOGGER = logging.getLogger(__name__)

#
# Internal storage for the event bus.  A lock guards mutations so that the
# bus is safe to use in multi‑threaded environments.
#
_subscribers: DefaultDict[str, List[Callable[[Dict], None]]] = defaultdict(list)
_lock = Lock()


def emit_event(event_type: str, payload: Dict) -> None:
    """Publishes a new event to all subscribers.

    Parameters
    ----------
    event_type:
        The string identifier representing the type of event.
    payload:
        Arbitrary data associated with the event.  The payload is passed as
        the sole argument to each subscribed handler.
    """

    # Snapshot the handlers under a lock to prevent race conditions if
    # subscriptions change during iteration.
    with _lock:
        handlers = list(_subscribers.get(event_type, []))

    for handler in handlers:
        try:
            handler(payload)
        except Exception:  # pragma: no cover - defensive logging path
            _LOGGER.exception("Error in event handler for %s", event_type)


def subscribe_to_event(event_type: str, handler: Callable[[Dict], None]) -> None:
    """Registers ``handler`` to be called when ``event_type`` is emitted.

    Duplicate registrations are ignored so the handler will only be called
    once per event emission.
    """

    with _lock:
        if handler not in _subscribers[event_type]:
            _subscribers[event_type].append(handler)


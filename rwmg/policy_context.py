"""Thread-local attachment of ``ModelProvider`` for policy internals."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import AsyncGenerator, Generator, Optional

from rwmg.model_provider import ModelProvider

_active_model: ContextVar[Optional[ModelProvider]] = ContextVar(
    "_active_model_provider", default=None
)


def get_active_model_provider() -> Optional[ModelProvider]:
    return _active_model.get()


@contextmanager
def model_provider_scope(provider: Optional[ModelProvider]) -> Generator[None, None, None]:
    token = _active_model.set(provider)
    try:
        yield None
    finally:
        _active_model.reset(token)


@asynccontextmanager
async def async_model_provider_scope(
    provider: Optional[ModelProvider],
) -> AsyncGenerator[None, None]:
    """Same as :func:`model_provider_scope` for async ``Engine.aprocess`` entrypoints."""

    token = _active_model.set(provider)
    try:
        yield None
    finally:
        _active_model.reset(token)


__all__ = [
    "async_model_provider_scope",
    "get_active_model_provider",
    "model_provider_scope",
]

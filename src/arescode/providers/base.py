"""ModelProvider interface: the async streaming chat contract every backend implements.

``chat`` yields :class:`Chunk` deltas as they arrive; ``complete`` is a non-streaming
convenience wrapper that concatenates a full response (context.md §4.7, TASKS 1.1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Wire-format message as sent to the model: {"role": ..., "content": ...}.
WireMessage = Mapping[str, Any]


@dataclass(slots=True)
class Chunk:
    """One streamed delta of a model response."""

    content: str
    done: bool = False


class ProviderError(RuntimeError):
    """A user-facing provider failure (connection, timeout, or HTTP error)."""


class ModelProvider(ABC):
    """Streaming chat interface. Implementations override ``chat``; ``complete`` is shared."""

    @abstractmethod
    def chat(self, messages: Sequence[WireMessage], **opts: Any) -> AsyncIterator[Chunk]:
        """Yield response chunks as they stream from the model."""
        raise NotImplementedError

    async def complete(self, messages: Sequence[WireMessage], **opts: Any) -> str:
        """Collect a full (non-streamed) response as a single string."""
        return "".join([chunk.content async for chunk in self.chat(messages, **opts)])

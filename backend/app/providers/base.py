"""Model provider abstraction. Application logic only ever talks to this interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


class ProviderError(Exception):
    """Inference failure. `kind` is one of: unreachable, timeout, model_not_found, out_of_memory, bad_response."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class ChatChunk:
    content: str = ""
    done: bool = False
    metrics: dict = field(default_factory=dict)


class ModelProvider(ABC):
    @abstractmethod
    async def health(self) -> dict: ...

    @abstractmethod
    async def list_models(self) -> list[dict]: ...

    @abstractmethod
    def chat_stream(
        self, model: str, messages: list[dict], options: dict, timeout: float
    ) -> AsyncIterator[ChatChunk]: ...

    @abstractmethod
    async def loaded_models(self) -> list[dict]: ...

    async def unload(self, model: str) -> None:
        """Release the model from RAM now (optional; default does nothing)."""
        return None

    @abstractmethod
    async def aclose(self) -> None: ...

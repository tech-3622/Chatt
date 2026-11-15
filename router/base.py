from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class RouteRequest:
    course_id: str
    task_type: str  # "assess" | "teach"
    difficulty: str = "normal"  # "normal" | "high"
    preferred_provider: Optional[str] = None
    preferred_model: Optional[str] = None


@dataclass
class AIResponse:
    text: str
    provider_name: str
    model_name: str


class AIProvider(ABC):
    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider is configured and ready."""

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        model: str,
        json_mode: bool = False,
        temperature: float = 0.7,
    ) -> str:
        """Generate a text (or JSON) response from the model."""

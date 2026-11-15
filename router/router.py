from __future__ import annotations

from typing import Dict, List, Tuple

import config
from router.base import AIProvider, AIResponse, RouteRequest
from router.providers.gemini_provider import GeminiProvider
from router.providers.groq_provider import GroqProvider
from router.providers.mock_provider import MockProvider


class AIRouter:
    def __init__(self) -> None:
        self.providers: Dict[str, AIProvider] = {}
        self.register_provider(GroqProvider())
        self.register_provider(GeminiProvider())
        self.register_provider(MockProvider())

    def register_provider(self, provider: AIProvider) -> None:
        self.providers[provider.name] = provider

    def _auto_select_provider(self) -> str:
        if self.providers["groq"].is_available():
            return "groq"
        if self.providers["gemini"].is_available():
            return "gemini"
        raise RuntimeError(
            "No live AI API key found. Export GROQ_API_KEY or GEMINI_API_KEY, "
            "or run with USE_MOCK_AI=1 to test offline."
        )

    def _default_model_for(self, provider_name: str, task_type: str) -> str:
        if provider_name == "groq":
            return (
                config.GROQ_ASSESS_MODEL
                if task_type == "assess"
                else config.GROQ_TEACH_MODEL
            )
        if provider_name == "gemini":
            return (
                config.GEMINI_ASSESS_MODEL
                if task_type == "assess"
                else config.GEMINI_TEACH_MODEL
            )
        return "mock-v1"

    def _choose_provider_and_model(self, request: RouteRequest) -> Tuple[str, str]:
        if config.USE_MOCK_AI:
            return "mock", "mock-v1"

        provider_name = request.preferred_provider or config.DEFAULT_PROVIDER
        if provider_name == "auto":
            provider_name = self._auto_select_provider()

        provider = self.providers.get(provider_name)
        if provider is None or not provider.is_available():
            raise RuntimeError(
                f"AI provider '{provider_name}' is not available. "
                "Export GROQ_API_KEY or GEMINI_API_KEY, or run with USE_MOCK_AI=1."
            )

        model = request.preferred_model or self._default_model_for(
            provider_name, request.task_type
        )
        return provider_name, model

    def generate(
        self,
        request: RouteRequest,
        system_prompt: str,
        messages: List[Dict[str, str]],
        json_mode: bool = False,
        temperature: float = 0.7,
    ) -> AIResponse:
        provider_name, model = self._choose_provider_and_model(request)
        provider = self.providers[provider_name]

        try:
            text = provider.generate(
                system_prompt=system_prompt,
                messages=messages,
                model=model,
                json_mode=json_mode,
                temperature=temperature,
            )
            return AIResponse(text=text, provider_name=provider_name, model_name=model)
        except Exception as primary_err:
            # 1. Try lighter/faster fallback model on the same provider
            fallback_model = self._default_model_for(provider_name, "assess")
            if model != fallback_model:
                try:
                    text = provider.generate(
                        system_prompt=system_prompt,
                        messages=messages,
                        model=fallback_model,
                        json_mode=json_mode,
                        temperature=temperature,
                    )
                    return AIResponse(
                        text=text,
                        provider_name=provider_name,
                        model_name=fallback_model,
                    )
                except Exception:
                    pass

            # 2. If another live provider is configured, fall back across providers
            other_provider = "gemini" if provider_name == "groq" else "groq"
            if other_provider in self.providers and self.providers[other_provider].is_available():
                other_model = self._default_model_for(other_provider, request.task_type)
                text = self.providers[other_provider].generate(
                    system_prompt=system_prompt,
                    messages=messages,
                    model=other_model,
                    json_mode=json_mode,
                    temperature=temperature,
                )
                return AIResponse(
                    text=text,
                    provider_name=other_provider,
                    model_name=other_model,
                )

            raise primary_err

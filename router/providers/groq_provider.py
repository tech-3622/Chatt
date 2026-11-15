from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

import config
from router.base import AIProvider


class GroqProvider(AIProvider):
    name = "groq"

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key or config.GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")

    def is_available(self) -> bool:
        key = self.api_key or os.getenv("GROQ_API_KEY", "")
        return bool(key)

    def _build_messages(
        self, system_prompt: str, messages: List[Dict[str, str]], json_mode: bool
    ) -> List[Dict[str, str]]:
        sys_text = system_prompt.strip()
        if json_mode and "json" not in sys_text.lower():
            sys_text += "\n\nRespond strictly with a valid JSON object."

        formatted: List[Dict[str, str]] = [{"role": "system", "content": sys_text}]
        for msg in messages:
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            raw_role = msg.get("role", "user")
            role = "assistant" if raw_role in ("ai", "assistant", "model") else "user"
            formatted.append({"role": role, "content": content})

        if len(formatted) == 1:
            formatted.append({"role": "user", "content": "Please begin."})
        return formatted

    def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        model: str,
        json_mode: bool = False,
        temperature: float = 0.7,
    ) -> str:
        api_key = self.api_key or os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set.")

        url = f"{config.GROQ_API_BASE}/chat/completions"

        # Groq requires temperature > 0
        safe_temp = 0.2 if json_mode else max(0.1, float(temperature))

        payload: Dict[str, Any] = {
            "model": model,
            "messages": self._build_messages(system_prompt, messages, json_mode),
            "temperature": safe_temp,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "AdaptiveAIUniversity/1.0",
        }

        last_error: Exception | None = None
        for attempt in range(config.AI_MAX_RETRIES + 1):
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(
                    req, timeout=config.AI_TIMEOUT_SECONDS
                ) as resp:
                    raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                return self._extract_text(data)
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(
                    f"Groq HTTP {exc.code}: {err_body[:300]}"
                )
                if exc.code in (429, 500, 502, 503, 504) and attempt < config.AI_MAX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last_error
            except urllib.error.URLError as exc:
                last_error = RuntimeError(f"Network error calling Groq: {exc}")
                if attempt < config.AI_MAX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last_error

        raise last_error or RuntimeError("Unknown Groq error")

    def _extract_text(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"No choices returned from Groq: {data}")
        text = (choices[0].get("message", {}).get("content") or "").strip()
        # Strip any accidental <think>...</think> blocks if a reasoning model emits them
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        if not text:
            raise RuntimeError(f"Empty response returned from Groq: {data}")
        return text

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

import config
from router.base import AIProvider


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key or config.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")

    def is_available(self) -> bool:
        key = self.api_key or os.getenv("GEMINI_API_KEY", "")
        return bool(key)

    def _normalize_contents(
        self, messages: List[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        contents: List[Dict[str, Any]] = []
        for msg in messages:
            text = (msg.get("content") or "").strip()
            if not text:
                continue
            raw_role = msg.get("role", "user")
            role = "model" if raw_role in ("ai", "assistant", "model") else "user"

            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"][0]["text"] += "\n\n" + text
            else:
                contents.append({"role": role, "parts": [{"text": text}]})

        if not contents:
            contents = [{"role": "user", "parts": [{"text": "Begin."}]}]
        elif contents[-1]["role"] != "user":
            contents.append(
                {"role": "user", "parts": [{"text": "Please continue."}]}
            )
        return contents

    def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        model: str,
        json_mode: bool = False,
        temperature: float = 0.7,
    ) -> str:
        api_key = self.api_key or os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")

        url = f"{config.GEMINI_API_BASE}/models/{model}:generateContent"

        generation_config: Dict[str, Any] = {
            "temperature": 0.2 if json_mode else temperature,
        }
        if json_mode:
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "contents": self._normalize_contents(messages),
            "generationConfig": generation_config,
        }

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
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
                    f"Gemini HTTP {exc.code}: {err_body[:300]}"
                )
                if exc.code in (429, 500, 502, 503, 504) and attempt < config.AI_MAX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last_error
            except urllib.error.URLError as exc:
                last_error = RuntimeError(f"Network error calling Gemini: {exc}")
                if attempt < config.AI_MAX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last_error

        raise last_error or RuntimeError("Unknown Gemini error")

    def _extract_text(self, data: Dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"No candidates returned from Gemini: {data}")
        parts = candidates[0].get("content", {}).get("parts", [])
        texts = [p.get("text", "") for p in parts if "text" in p]
        result = "".join(texts).strip()
        if not result:
            raise RuntimeError(f"Empty text returned from Gemini: {data}")
        return result

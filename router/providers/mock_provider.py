from __future__ import annotations

import json
from typing import Dict, List

from router.base import AIProvider


class MockProvider(AIProvider):
    name = "mock"

    def is_available(self) -> bool:
        return True

    def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        model: str,
        json_mode: bool = False,
        temperature: float = 0.7,
    ) -> str:
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = msg.get("content", "").lower()
                break

        if json_mode:
            if "foundation" in last_user:
                return json.dumps(
                    {
                        "status": "NOT_UNDERSTOOD",
                        "diagnosis": "Student is missing a prerequisite idea.",
                        "missing_foundation": "part_vs_whole",
                        "missing_foundation_goal": "Understand the difference between looking at a whole group and a small portion of it.",
                        "recommended_strategy": "remediate_foundation",
                        "observed_learning_style": "everyday_examples",
                        "confidence": 0.85,
                    }
                )
            if "confused" in last_user or "don't know" in last_user or "idk" in last_user:
                return json.dumps(
                    {
                        "status": "NOT_UNDERSTOOD",
                        "diagnosis": "The student did not grasp the concept yet.",
                        "missing_foundation": None,
                        "missing_foundation_goal": None,
                        "recommended_strategy": "real_life_analogy",
                        "observed_learning_style": "everyday_examples",
                        "confidence": 0.9,
                    }
                )
            if "maybe" in last_user or "partly" in last_user:
                return json.dumps(
                    {
                        "status": "PARTLY_UNDERSTOOD",
                        "diagnosis": "Student understands part of the idea but needs clarity.",
                        "missing_foundation": None,
                        "missing_foundation_goal": None,
                        "recommended_strategy": "guided_example",
                        "observed_learning_style": "step_by_step_numbers",
                        "confidence": 0.75,
                    }
                )
            return json.dumps(
                {
                    "status": "UNDERSTOOD",
                    "diagnosis": "Student explained the concept clearly.",
                    "missing_foundation": None,
                    "missing_foundation_goal": None,
                    "recommended_strategy": "advance",
                    "observed_learning_style": "everyday_examples",
                    "confidence": 0.92,
                }
            )

        if "phase: greeting" in system_prompt.lower():
            return "Hey! How are you doing today? I hope your day is going well."
        if "phase: diagnostic_intro" in system_prompt.lower():
            return (
                "That's great! Today we will be learning Descriptive Statistics. "
                "Don't worry, we will take everything one small step at a time.\n\n"
                "Before we begin, what do you think statistics means?"
            )
        return (
            "[Mock Lecturer] Great effort! Think of this like sorting fruits in a market basket "
            "before counting them. In your own words, why do you think organizing data first helps?"
        )

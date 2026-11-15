from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from core.models import AssessmentResult, StudentIntent, UnderstandingStatus
from router.base import RouteRequest
from router.router import AIRouter


class UnderstandingEngine:
    def __init__(self, router: AIRouter) -> None:
        self.router = router

    def assess(
        self,
        course_id: str,
        concept_id: str,
        concept_title: str,
        learning_goal: str,
        key_idea: str,
        student_answer: str,
        recent_messages: List[Dict[str, str]],
        attempt_count: int = 0,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
    ) -> AssessmentResult:
        last_lecturer_msg = ""
        for msg in reversed(recent_messages):
            if msg.get("role") in ("ai", "assistant", "model"):
                last_lecturer_msg = msg.get("content", "")
                break

        system_prompt = f"""You are the internal Understanding Engine for an Adaptive AI University.
Evaluate BOTH the student's intent and their understanding of the current concept.

Current Concept: [{concept_id}] {concept_title}
Learning Goal: {learning_goal}
Core Idea: {key_idea}
Attempts so far: {attempt_count}

First, classify "student_intent":
- "wants_practice": Student asks for a question, quiz, or exercise to try (e.g., "give me the question", "test me", "let me try one").
- "wants_example": Student asks for another example or practical demonstration (e.g., "give me more examples", "show me how to do it in my book").
- "wants_restart": Student asks to start over or repeat from scratch (e.g., "let's start again", "start over").
- "answering": Student is answering the question, giving their understanding, or saying they don't understand.

Second, classify "status" (ONLY relevant when answering, or if they also demonstrated understanding):
- "UNDERSTOOD": Student clearly grasps the core idea in their own words or answered accurately enough to move forward. (IMPORTANT: if student_intent is "wants_practice" or "wants_example", they want to stay on this concept to practice first—so only mark UNDERSTOOD if they didn't ask to practice more.)
- "PARTLY_UNDERSTOOD": Partially right, vague, or minor confusion.
- "NOT_UNDERSTOOD": Confused, wrong, or says "I don't know".

Return valid JSON ONLY:
{{
  "student_intent": "answering | wants_practice | wants_example | wants_restart",
  "status": "UNDERSTOOD | PARTLY_UNDERSTOOD | NOT_UNDERSTOOD",
  "diagnosis": "1 short sentence explaining what the student meant or understood.",
  "missing_foundation": null or "short_snake_case_key",
  "missing_foundation_goal": null or "1 sentence describing the missing foundation",
  "recommended_strategy": "advance | different_analogy | smaller_step | concrete_numbers | notebook_exercise | remediate_foundation",
  "observed_learning_style": null or "everyday_examples | step_by_step_numbers | analogies | visual_tables",
  "confidence": 0.0 to 1.0
}}"""

        user_prompt = (
            f"Lecturer last said:\n{last_lecturer_msg}\n\n"
            f"Student replied:\n{student_answer}"
        )

        req = RouteRequest(
            course_id=course_id,
            task_type="assess",
            difficulty="normal",
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )

        response = self.router.generate(
            request=req,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            json_mode=True,
            temperature=0.2,
        )

        return self._parse_assessment(response.text)

    def _parse_assessment(self, raw_text: str) -> AssessmentResult:
        cleaned = raw_text.strip()
        fence_match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if fence_match:
            cleaned = fence_match.group(0)

        try:
            data: Dict[str, Any] = json.loads(cleaned)
        except json.JSONDecodeError:
            return AssessmentResult(
                status=UnderstandingStatus.PARTLY_UNDERSTOOD,
                diagnosis="Check gently with the student.",
                student_intent=StudentIntent.ANSWERING,
                recommended_strategy="smaller_step",
                confidence=0.3,
            )

        raw_intent = str(data.get("student_intent", "answering")).strip().lower()
        if raw_intent in (
            StudentIntent.WANTS_PRACTICE,
            StudentIntent.WANTS_EXAMPLE,
            StudentIntent.WANTS_RESTART,
        ):
            intent = raw_intent
        else:
            intent = StudentIntent.ANSWERING

        raw_status = str(data.get("status", "")).strip().upper()
        if raw_status == "UNDERSTOOD":
            status = UnderstandingStatus.UNDERSTOOD
        elif "PART" in raw_status:
            status = UnderstandingStatus.PARTLY_UNDERSTOOD
        elif "NOT" in raw_status or "CONFUS" in raw_status or "WRONG" in raw_status:
            status = UnderstandingStatus.NOT_UNDERSTOOD
        else:
            status = UnderstandingStatus.PARTLY_UNDERSTOOD

        return AssessmentResult(
            status=status,
            diagnosis=str(data.get("diagnosis", "")).strip() or "Evaluated student reply.",
            student_intent=intent,
            missing_foundation=data.get("missing_foundation") or None,
            missing_foundation_goal=data.get("missing_foundation_goal") or None,
            recommended_strategy=str(
                data.get("recommended_strategy") or "different_analogy"
            ),
            observed_learning_style=data.get("observed_learning_style") or None,
            confidence=float(data.get("confidence", 0.7)),
        )

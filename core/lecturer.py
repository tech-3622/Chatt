from __future__ import annotations

import re
from typing import Dict, List

from core.models import (
    AssessmentResult,
    ExplanationRecord,
    MicroConcept,
    StackFrame,
    StudentProfile,
)
from courses.loader import CourseBrain
from router.base import RouteRequest
from router.router import AIRouter


class Lecturer:
    def __init__(self, router: AIRouter) -> None:
        self.router = router

    def _clean_text(self, text: str) -> str:
        cleaned = text.replace("**", "").replace("__", "")
        cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
        return cleaned.strip()

    def _base_persona_prompt(
        self, course: CourseBrain, profile: StudentProfile, weak_foundations: List[str]
    ) -> str:
        weak_text = ", ".join(weak_foundations) if weak_foundations else "none yet"

        return f"""You are a sharp, warm private tutor teaching {course.code} — {course.title}.
Student style: {profile.preferred_learning_style} | Weak areas: {weak_text}

HARD BREVITY & CLARITY RULES:
- Maximum 35 to 55 words TOTAL. Never write long paragraphs!
- Use 2 or 3 short, crisp lines separated by a blank line so it reads easily on a phone.
- Teach ONLY ONE tiny idea.
- Plain spoken text only: NO markdown (**bold**, bullets) and NO labels like "Example:" or "Question:".
- Always end with ONE crystal-clear, logical question."""

    def generate_greeting(self, course: CourseBrain) -> str:
        return course.opening.get(
            "greeting",
            "Hey! How are you doing today? I hope your day is going well.",
        )

    def generate_diagnostic_intro(
        self,
        course: CourseBrain,
        profile: StudentProfile,
        student_greeting_reply: str,
    ) -> str:
        intro_line = course.opening.get(
            "intro_line",
            f"Today we will learn {course.title}, one tiny step at a time.",
        )
        diag_q = course.opening.get(
            "diagnostic_question",
            "Before we begin, what do you think statistics means?",
        )

        system_prompt = (
            f"Respond warmly in 1 short sentence to how the student feels.\n"
            f"Then say: '{intro_line}'\n"
            f"End with: '{diag_q}'\n"
            f"Max 45 words total. Plain text only."
        )

        req = RouteRequest(
            course_id=course.course_id,
            task_type="teach",
            preferred_provider=course.ai_preferences.get("preferred_provider"),
            preferred_model=course.ai_preferences.get("teach_model"),
        )
        res = self.router.generate(
            request=req,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": student_greeting_reply}],
            temperature=0.6,
        )
        return self._clean_text(res.text)

    def teach_new_concept(
        self,
        course: CourseBrain,
        concept: MicroConcept,
        profile: StudentProfile,
        weak_foundations: List[str],
        recent_messages: List[Dict[str, str]],
        bridge_note: str = "",
    ) -> str:
        base = self._base_persona_prompt(course, profile, weak_foundations)
        system_prompt = f"""{base}

TASK: Introduce Concept [{concept.id}] {concept.title} ONLY.
Core Idea: {concept.key_idea}
Simple Example: {concept.suggested_example}
Check Question Idea: {concept.check_prompt}
Context: {bridge_note or "Start cleanly."}

Write 3 short lines (under 55 words total):
Line 1: 1 short warm reaction to the student.
Line 2: Explain THIS concept in 1 simple sentence + 1 tiny example.
Line 3: Ask 1 clear question (make sure the answer is unmistakable and tests THIS concept only)."""

        req = RouteRequest(
            course_id=course.course_id,
            task_type="teach",
            preferred_provider=course.ai_preferences.get("preferred_provider"),
            preferred_model=course.ai_preferences.get("teach_model"),
        )
        res = self.router.generate(
            request=req,
            system_prompt=system_prompt,
            messages=recent_messages,
            temperature=0.6,
        )
        return self._clean_text(res.text)

    def give_practice_or_example(
        self,
        course: CourseBrain,
        concept_id: str,
        concept_title: str,
        key_idea: str,
        mode: str,  # "wants_practice" | "wants_example" | "wants_restart"
        profile: StudentProfile,
        weak_foundations: List[str],
        recent_messages: List[Dict[str, str]],
    ) -> str:
        base = self._base_persona_prompt(course, profile, weak_foundations)

        if mode == "wants_practice":
            instruction = (
                "The student asked for a practice question right now. "
                "Give ONE short, hands-on mini exercise they can do in their notebook or head "
                "(e.g., 4-5 tiny numbers or a clear real-life scenario) and ask them to answer it."
            )
        elif mode == "wants_example":
            instruction = (
                "The student asked for a practical example (like something they can try in a notebook). "
                "Show ONE super clear 2-line practical example of this concept, then give them a tiny question to try."
            )
        else:
            instruction = (
                "The student wants to restart this concept cleanly from zero. "
                "Say 'Sure, let's reset this step cleanly!' Explain the core idea in 1 ultra-simple sentence "
                "with 1 tiny example, then ask 1 easy question."
            )

        system_prompt = f"""{base}

Current Concept [{concept_id}]: {concept_title}
Core Idea: {key_idea}

{instruction}
Keep total reply under 55 words."""

        req = RouteRequest(
            course_id=course.course_id,
            task_type="teach",
            preferred_provider=course.ai_preferences.get("preferred_provider"),
            preferred_model=course.ai_preferences.get("teach_model"),
        )
        res = self.router.generate(
            request=req,
            system_prompt=system_prompt,
            messages=recent_messages,
            temperature=0.65,
        )
        return self._clean_text(res.text)

    def adapt_explanation(
        self,
        course: CourseBrain,
        concept_id: str,
        concept_title: str,
        learning_goal: str,
        key_idea: str,
        assessment: AssessmentResult,
        failed_attempts: List[ExplanationRecord],
        profile: StudentProfile,
        weak_foundations: List[str],
        recent_messages: List[Dict[str, str]],
        is_remediation: bool = False,
    ) -> str:
        base = self._base_persona_prompt(course, profile, weak_foundations)

        avoid_lines = (
            "\n".join(f"- Avoid: {rec.explanation_summary}" for rec in failed_attempts)
            if failed_attempts
            else "- None yet."
        )

        system_prompt = f"""{base}

TASK: Stay on [{concept_id}] {concept_title} and explain SIMPLY in a NEW way.
Core Idea: {key_idea}
Diagnosis: {assessment.diagnosis}
Do NOT repeat:
{avoid_lines}

Write 3 short lines (under 50 words total):
Line 1: Warm encouragement (no blame).
Line 2: 1 ultra-simple explanation or tiny number example.
Line 3: 1 easy yes/no or short-answer question."""

        req = RouteRequest(
            course_id=course.course_id,
            task_type="teach",
            preferred_provider=course.ai_preferences.get("preferred_provider"),
            preferred_model=course.ai_preferences.get("teach_model"),
        )
        res = self.router.generate(
            request=req,
            system_prompt=system_prompt,
            messages=recent_messages,
            temperature=0.7,
        )
        return self._clean_text(res.text)

    def introduce_remediation(
        self,
        course: CourseBrain,
        frame: StackFrame,
        paused_concept_title: str,
        profile: StudentProfile,
        weak_foundations: List[str],
        recent_messages: List[Dict[str, str]],
    ) -> str:
        base = self._base_persona_prompt(course, profile, weak_foundations)
        system_prompt = f"""{base}

TASK: Pause '{paused_concept_title}' and teach smaller foundation '{frame.title}' first.
Goal: {frame.learning_goal}

Write 3 short lines (under 50 words total):
Line 1: Reassure them—let's check one tiny building block first.
Line 2: Explain '{frame.title}' in 1 simple sentence + 1 tiny example.
Line 3: 1 easy check question."""

        req = RouteRequest(
            course_id=course.course_id,
            task_type="teach",
            preferred_provider=course.ai_preferences.get("preferred_provider"),
            preferred_model=course.ai_preferences.get("teach_model"),
        )
        res = self.router.generate(
            request=req,
            system_prompt=system_prompt,
            messages=recent_messages,
            temperature=0.65,
        )
        return self._clean_text(res.text)

    def summarize_explanation(self, text: str, max_len: int = 160) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_len:
            return compact
        return compact[: max_len - 3] + "..."

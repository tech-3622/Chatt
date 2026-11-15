from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


class UnderstandingStatus:
    UNDERSTOOD = "UNDERSTOOD"
    PARTLY_UNDERSTOOD = "PARTLY_UNDERSTOOD"
    NOT_UNDERSTOOD = "NOT_UNDERSTOOD"


class StudentIntent:
    ANSWERING = "answering"
    WANTS_PRACTICE = "wants_practice"
    WANTS_EXAMPLE = "wants_example"
    WANTS_RESTART = "wants_restart"


class LessonPhase:
    GREETING = "GREETING"
    DIAGNOSTIC_INTRO = "DIAGNOSTIC_INTRO"
    TEACHING = "TEACHING"
    REMEDIATION = "REMEDIATION"
    COMPLETED = "COMPLETED"


@dataclass
class MicroConcept:
    id: str
    topic_id: str
    topic_title: str
    title: str
    learning_goal: str
    key_idea: str
    suggested_example: str
    check_prompt: str
    prerequisites: List[str] = field(default_factory=list)


@dataclass
class AssessmentResult:
    status: str  # UNDERSTOOD | PARTLY_UNDERSTOOD | NOT_UNDERSTOOD
    diagnosis: str
    student_intent: str = StudentIntent.ANSWERING
    missing_foundation: Optional[str] = None
    missing_foundation_goal: Optional[str] = None
    recommended_strategy: str = "simple_explanation"
    observed_learning_style: Optional[str] = None
    confidence: float = 0.0


@dataclass
class StackFrame:
    concept_id: str
    title: str
    learning_goal: str
    is_remediation: bool = False
    reason: str = ""
    attempts: int = 0


@dataclass
class LessonState:
    course_id: str
    phase: str = LessonPhase.GREETING
    current_topic_id: str = ""
    current_concept_id: str = ""
    concept_stack: List[StackFrame] = field(default_factory=list)
    attempt_count: int = 0
    last_strategy: str = ""
    last_explanation_summary: str = ""


@dataclass
class StudentProfile:
    student_id: str = "default_student"
    name: str = "Student"
    preferred_learning_style: str = "everyday_examples"
    pace_notes: str = "steady, one small step at a time"


@dataclass
class ExplanationRecord:
    concept_id: str
    strategy_used: str
    explanation_summary: str
    outcome: str  # worked | partly_worked | did_not_work

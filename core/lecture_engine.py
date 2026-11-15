from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.lecturer import Lecturer
from core.models import (
    AssessmentResult,
    LessonPhase,
    LessonState,
    StackFrame,
    StudentIntent,
    UnderstandingStatus,
)
from core.understanding_engine import UnderstandingEngine
from courses.loader import CourseBrain
from memory.student_memory import StudentMemory
from router.base import RouteRequest
from router.router import AIRouter


@dataclass
class TurnResult:
    reply: str
    phase: str
    concept_id: str
    concept_title: str
    assessment: Optional[AssessmentResult] = None
    moved_forward: bool = False
    remediation_active: bool = False


class LectureEngine:
    def __init__(
        self,
        course: CourseBrain,
        memory: StudentMemory,
        router: AIRouter,
    ) -> None:
        self.course = course
        self.memory = memory
        self.router = router
        self.understanding_engine = UnderstandingEngine(router)
        self.lecturer = Lecturer(router)

    def start_or_resume(self) -> TurnResult:
        profile = self.memory.get_or_create_profile()
        state = self.memory.get_course_state(self.course.course_id)

        if state is None:
            first = self.course.get_first_concept()
            if first is None:
                raise RuntimeError(f"Course '{self.course.course_id}' has no concepts.")

            state = LessonState(
                course_id=self.course.course_id,
                phase=LessonPhase.GREETING,
                current_topic_id=first.topic_id,
                current_concept_id=first.id,
            )
            self.memory.save_course_state(state)

            greeting = self.lecturer.generate_greeting(self.course)
            self.memory.add_message(self.course.course_id, "ai", greeting, first.id)
            return TurnResult(
                reply=greeting,
                phase=state.phase,
                concept_id=first.id,
                concept_title=first.title,
            )

        concept = self.course.get_concept(state.current_concept_id)
        concept_title = concept.title if concept else "Course Review"

        if state.phase == LessonPhase.GREETING:
            recent = self.memory.get_recent_messages(self.course.course_id, limit=1)
            msg = (
                recent[-1]["content"]
                if recent and recent[-1]["role"] == "ai"
                else self.lecturer.generate_greeting(self.course)
            )
            return TurnResult(
                reply=msg,
                phase=state.phase,
                concept_id=state.current_concept_id,
                concept_title=concept_title,
            )

        if state.phase == LessonPhase.COMPLETED:
            msg = (
                f"Welcome back! You completed all concepts in {self.course.code}. "
                f"Ask to review any topic, or reset to start fresh."
            )
            return TurnResult(
                reply=msg,
                phase=state.phase,
                concept_id=state.current_concept_id,
                concept_title=concept_title,
            )

        summary = self.memory.get_progress_summary(self.course.course_id)
        mastered_count = len(summary["mastered"])
        recent = self.memory.get_recent_messages(self.course.course_id, limit=6)
        weak = self.memory.get_weak_foundations()

        active_title = (
            state.concept_stack[-1].title
            if state.phase == LessonPhase.REMEDIATION and state.concept_stack
            else concept_title
        )

        recap_prompt = (
            f"Welcome the student back warmly to {self.course.code}.\n"
            f"Mastered so far: {mastered_count}. Current step: {active_title}.\n"
            f"Weak areas: {', '.join(weak) if weak else 'none'}.\n"
            f"Style: {profile.preferred_learning_style}.\n"
            f"Keep it under 45 words, plain text: greet them, state where we paused, and ask 1 short check question."
        )
        req = RouteRequest(
            course_id=self.course.course_id,
            task_type="teach",
            preferred_provider=self.course.ai_preferences.get("preferred_provider"),
            preferred_model=self.course.ai_preferences.get("teach_model"),
        )
        res = self.router.generate(
            request=req,
            system_prompt=recap_prompt,
            messages=recent or [{"role": "user", "content": "I am back."}],
            temperature=0.6,
        )
        clean_reply = self.lecturer._clean_text(res.text)
        self.memory.add_message(
            self.course.course_id, "ai", clean_reply, state.current_concept_id
        )
        return TurnResult(
            reply=clean_reply,
            phase=state.phase,
            concept_id=state.current_concept_id,
            concept_title=active_title,
            remediation_active=(state.phase == LessonPhase.REMEDIATION),
        )

    def handle_student_message(self, student_text: str) -> TurnResult:
        state = self.memory.get_course_state(self.course.course_id)
        if state is None:
            return self.start_or_resume()

        profile = self.memory.get_or_create_profile()
        weak_foundations = self.memory.get_weak_foundations()
        current_concept = self.course.get_concept(state.current_concept_id)

        self.memory.add_message(
            self.course.course_id, "user", student_text, state.current_concept_id
        )
        recent = self.memory.get_recent_messages(self.course.course_id, limit=8)

        if state.phase == LessonPhase.GREETING:
            reply = self.lecturer.generate_diagnostic_intro(
                self.course, profile, student_text
            )
            state.phase = LessonPhase.DIAGNOSTIC_INTRO
            self.memory.save_course_state(state)
            self.memory.add_message(
                self.course.course_id, "ai", reply, state.current_concept_id
            )
            return TurnResult(
                reply=reply,
                phase=state.phase,
                concept_id=state.current_concept_id,
                concept_title=current_concept.title if current_concept else "",
            )

        if state.phase == LessonPhase.DIAGNOSTIC_INTRO:
            assert current_concept is not None
            state.phase = LessonPhase.TEACHING
            state.attempt_count = 0
            state.last_strategy = "initial_explanation"
            self.memory.mark_concept_progress(
                self.course.course_id, current_concept.id, "in_progress", 0
            )
            reply = self.lecturer.teach_new_concept(
                course=self.course,
                concept=current_concept,
                profile=profile,
                weak_foundations=weak_foundations,
                recent_messages=recent,
                bridge_note="Acknowledge what the student said in 1 short sentence and introduce Concept 1.1.",
            )
            state.last_explanation_summary = self.lecturer.summarize_explanation(reply)
            self.memory.save_course_state(state)
            self.memory.add_message(
                self.course.course_id, "ai", reply, current_concept.id
            )
            return TurnResult(
                reply=reply,
                phase=state.phase,
                concept_id=current_concept.id,
                concept_title=current_concept.title,
            )

        if state.phase == LessonPhase.COMPLETED:
            req = RouteRequest(
                course_id=self.course.course_id,
                task_type="teach",
                preferred_provider=self.course.ai_preferences.get("preferred_provider"),
                preferred_model=self.course.ai_preferences.get("teach_model"),
            )
            res = self.router.generate(
                request=req,
                system_prompt=f"Friendly {self.course.code} tutor helping with short review questions (under 50 words).",
                messages=recent,
                temperature=0.6,
            )
            clean_reply = self.lecturer._clean_text(res.text)
            self.memory.add_message(
                self.course.course_id, "ai", clean_reply, "completed"
            )
            return TurnResult(
                reply=clean_reply,
                phase=state.phase,
                concept_id="completed",
                concept_title="Course Completed",
            )

        if state.phase == LessonPhase.REMEDIATION and state.concept_stack:
            return self._handle_remediation_turn(
                state, profile, weak_foundations, student_text, recent
            )

        assert current_concept is not None
        return self._handle_teaching_turn(
            state, current_concept, profile, weak_foundations, student_text, recent
        )

    def _handle_teaching_turn(
        self,
        state: LessonState,
        concept,
        profile,
        weak_foundations,
        student_text: str,
        recent,
    ) -> TurnResult:
        assessment = self.understanding_engine.assess(
            course_id=self.course.course_id,
            concept_id=concept.id,
            concept_title=concept.title,
            learning_goal=concept.learning_goal,
            key_idea=concept.key_idea,
            student_answer=student_text,
            recent_messages=recent,
            attempt_count=state.attempt_count,
            preferred_provider=self.course.ai_preferences.get("preferred_provider"),
            preferred_model=self.course.ai_preferences.get("assess_model"),
        )

        if assessment.observed_learning_style:
            self.memory.update_learning_style(assessment.observed_learning_style)
            profile = self.memory.get_or_create_profile()

        # Honor student requests for practice, more examples, or restarting THIS step
        if assessment.student_intent in (
            StudentIntent.WANTS_PRACTICE,
            StudentIntent.WANTS_EXAMPLE,
            StudentIntent.WANTS_RESTART,
        ):
            if assessment.student_intent == StudentIntent.WANTS_RESTART:
                state.attempt_count = 0

            reply = self.lecturer.give_practice_or_example(
                course=self.course,
                concept_id=concept.id,
                concept_title=concept.title,
                key_idea=concept.key_idea,
                mode=assessment.student_intent,
                profile=profile,
                weak_foundations=weak_foundations,
                recent_messages=recent,
            )
            state.last_strategy = assessment.student_intent
            state.last_explanation_summary = self.lecturer.summarize_explanation(reply)
            self.memory.save_course_state(state)
            self.memory.add_message(self.course.course_id, "ai", reply, concept.id)
            return TurnResult(
                reply=reply,
                phase=state.phase,
                concept_id=concept.id,
                concept_title=concept.title,
                assessment=assessment,
                moved_forward=False,
            )

        # UNDERSTOOD -> Advance strictly because student understands
        if assessment.status == UnderstandingStatus.UNDERSTOOD:
            if state.last_explanation_summary:
                self.memory.log_explanation_attempt(
                    self.course.course_id,
                    concept.id,
                    state.last_strategy or "initial_explanation",
                    state.last_explanation_summary,
                    "worked",
                )

            self.memory.mark_concept_progress(
                self.course.course_id,
                concept.id,
                "mastered",
                state.attempt_count + 1,
                assessment.diagnosis,
            )

            next_concept = self.course.get_next_concept(concept.id)
            if next_concept is None:
                state.phase = LessonPhase.COMPLETED
                state.attempt_count = 0
                self.memory.save_course_state(state)
                reply = (
                    f"Amazing! You mastered '{concept.title}' and finished all steps in {self.course.code}. "
                    f"Want to review any topic?"
                )
                self.memory.add_message(self.course.course_id, "ai", reply, concept.id)
                return TurnResult(
                    reply=reply,
                    phase=state.phase,
                    concept_id=concept.id,
                    concept_title=concept.title,
                    assessment=assessment,
                    moved_forward=True,
                )

            state.current_concept_id = next_concept.id
            state.current_topic_id = next_concept.topic_id
            state.attempt_count = 0
            state.last_strategy = "initial_explanation"
            self.memory.mark_concept_progress(
                self.course.course_id, next_concept.id, "in_progress", 0
            )

            reply = self.lecturer.teach_new_concept(
                course=self.course,
                concept=next_concept,
                profile=profile,
                weak_foundations=weak_foundations,
                recent_messages=recent,
                bridge_note=f"Praise them in 1 short sentence for mastering '{concept.title}', then teach '{next_concept.title}'.",
            )
            state.last_explanation_summary = self.lecturer.summarize_explanation(reply)
            self.memory.save_course_state(state)
            self.memory.add_message(self.course.course_id, "ai", reply, next_concept.id)
            return TurnResult(
                reply=reply,
                phase=state.phase,
                concept_id=next_concept.id,
                concept_title=next_concept.title,
                assessment=assessment,
                moved_forward=True,
            )

        # NOT_UNDERSTOOD with Missing Foundation -> Push onto Foundation Stack
        if (
            assessment.status == UnderstandingStatus.NOT_UNDERSTOOD
            and assessment.missing_foundation
        ):
            if state.last_explanation_summary:
                self.memory.log_explanation_attempt(
                    self.course.course_id,
                    concept.id,
                    state.last_strategy or "initial_explanation",
                    state.last_explanation_summary,
                    "did_not_work",
                )

            state.attempt_count += 1
            self.memory.mark_concept_progress(
                self.course.course_id,
                concept.id,
                "difficult",
                state.attempt_count,
                assessment.diagnosis,
            )

            foundation_key = assessment.missing_foundation
            self.memory.record_foundation(
                foundation_key, "needs_support", assessment.diagnosis
            )

            frame = StackFrame(
                concept_id=f"foundation:{foundation_key}",
                title=foundation_key.replace("_", " ").title(),
                learning_goal=(
                    assessment.missing_foundation_goal
                    or f"Build intuition for {foundation_key.replace('_', ' ')}."
                ),
                is_remediation=True,
                reason=assessment.diagnosis,
                attempts=0,
            )
            state.concept_stack.append(frame)
            state.phase = LessonPhase.REMEDIATION
            state.last_strategy = "remediate_foundation"

            reply = self.lecturer.introduce_remediation(
                course=self.course,
                frame=frame,
                paused_concept_title=concept.title,
                profile=profile,
                weak_foundations=self.memory.get_weak_foundations(),
                recent_messages=recent,
            )
            state.last_explanation_summary = self.lecturer.summarize_explanation(reply)
            self.memory.save_course_state(state)
            self.memory.add_message(self.course.course_id, "ai", reply, frame.concept_id)
            return TurnResult(
                reply=reply,
                phase=state.phase,
                concept_id=frame.concept_id,
                concept_title=frame.title,
                assessment=assessment,
                moved_forward=False,
                remediation_active=True,
            )

        # PARTLY_UNDERSTOOD or NOT_UNDERSTOOD (Stay on same concept, explain simpler!)
        outcome = (
            "partly_worked"
            if assessment.status == UnderstandingStatus.PARTLY_UNDERSTOOD
            else "did_not_work"
        )
        if state.last_explanation_summary:
            self.memory.log_explanation_attempt(
                self.course.course_id,
                concept.id,
                state.last_strategy or "initial_explanation",
                state.last_explanation_summary,
                outcome,
            )

        state.attempt_count += 1
        status_label = "difficult" if state.attempt_count >= 2 else "in_progress"
        self.memory.mark_concept_progress(
            self.course.course_id,
            concept.id,
            status_label,
            state.attempt_count,
            assessment.diagnosis,
        )

        failed_attempts = self.memory.get_failed_explanations(
            self.course.course_id, concept.id
        )
        reply = self.lecturer.adapt_explanation(
            course=self.course,
            concept_id=concept.id,
            concept_title=concept.title,
            learning_goal=concept.learning_goal,
            key_idea=concept.key_idea,
            assessment=assessment,
            failed_attempts=failed_attempts,
            profile=profile,
            weak_foundations=weak_foundations,
            recent_messages=recent,
            is_remediation=False,
        )

        state.last_strategy = assessment.recommended_strategy
        state.last_explanation_summary = self.lecturer.summarize_explanation(reply)
        self.memory.save_course_state(state)
        self.memory.add_message(self.course.course_id, "ai", reply, concept.id)

        return TurnResult(
            reply=reply,
            phase=state.phase,
            concept_id=concept.id,
            concept_title=concept.title,
            assessment=assessment,
            moved_forward=False,
        )

    def _handle_remediation_turn(
        self,
        state: LessonState,
        profile,
        weak_foundations,
        student_text: str,
        recent,
    ) -> TurnResult:
        frame = state.concept_stack[-1]
        assessment = self.understanding_engine.assess(
            course_id=self.course.course_id,
            concept_id=frame.concept_id,
            concept_title=frame.title,
            learning_goal=frame.learning_goal,
            key_idea=frame.learning_goal,
            student_answer=student_text,
            recent_messages=recent,
            attempt_count=frame.attempts,
            preferred_provider=self.course.ai_preferences.get("preferred_provider"),
            preferred_model=self.course.ai_preferences.get("assess_model"),
        )

        if assessment.student_intent in (
            StudentIntent.WANTS_PRACTICE,
            StudentIntent.WANTS_EXAMPLE,
            StudentIntent.WANTS_RESTART,
        ):
            reply = self.lecturer.give_practice_or_example(
                course=self.course,
                concept_id=frame.concept_id,
                concept_title=frame.title,
                key_idea=frame.learning_goal,
                mode=assessment.student_intent,
                profile=profile,
                weak_foundations=weak_foundations,
                recent_messages=recent,
            )
            state.last_strategy = assessment.student_intent
            state.last_explanation_summary = self.lecturer.summarize_explanation(reply)
            self.memory.save_course_state(state)
            self.memory.add_message(self.course.course_id, "ai", reply, frame.concept_id)
            return TurnResult(
                reply=reply,
                phase=state.phase,
                concept_id=frame.concept_id,
                concept_title=frame.title,
                assessment=assessment,
                moved_forward=False,
                remediation_active=True,
            )

        if assessment.status == UnderstandingStatus.UNDERSTOOD:
            if state.last_explanation_summary:
                self.memory.log_explanation_attempt(
                    self.course.course_id,
                    frame.concept_id,
                    state.last_strategy or "remediate_foundation",
                    state.last_explanation_summary,
                    "worked",
                )
            foundation_key = frame.concept_id.replace("foundation:", "")
            self.memory.record_foundation(
                foundation_key, "understood", assessment.diagnosis
            )

            state.concept_stack.pop()

            if not state.concept_stack:
                state.phase = LessonPhase.TEACHING
                paused_concept = self.course.get_concept(state.current_concept_id)
                assert paused_concept is not None
                state.last_strategy = "post_remediation_bridge"

                reply = self.lecturer.teach_new_concept(
                    course=self.course,
                    concept=paused_concept,
                    profile=profile,
                    weak_foundations=self.memory.get_weak_foundations(),
                    recent_messages=recent,
                    bridge_note=f"Praise them for mastering '{frame.title}' and bring them smoothly back to '{paused_concept.title}'.",
                )
                state.last_explanation_summary = self.lecturer.summarize_explanation(
                    reply
                )
                self.memory.save_course_state(state)
                self.memory.add_message(
                    self.course.course_id, "ai", reply, paused_concept.id
                )
                return TurnResult(
                    reply=reply,
                    phase=state.phase,
                    concept_id=paused_concept.id,
                    concept_title=paused_concept.title,
                    assessment=assessment,
                    moved_forward=True,
                    remediation_active=False,
                )

        outcome = (
            "partly_worked"
            if assessment.status == UnderstandingStatus.PARTLY_UNDERSTOOD
            else "did_not_work"
        )
        if state.last_explanation_summary:
            self.memory.log_explanation_attempt(
                self.course.course_id,
                frame.concept_id,
                state.last_strategy or "remediate_foundation",
                state.last_explanation_summary,
                outcome,
            )

        frame.attempts += 1
        failed = self.memory.get_failed_explanations(
            self.course.course_id, frame.concept_id
        )
        reply = self.lecturer.adapt_explanation(
            course=self.course,
            concept_id=frame.concept_id,
            concept_title=frame.title,
            learning_goal=frame.learning_goal,
            key_idea=frame.learning_goal,
            assessment=assessment,
            failed_attempts=failed,
            profile=profile,
            weak_foundations=weak_foundations,
            recent_messages=recent,
            is_remediation=True,
        )
        state.last_strategy = assessment.recommended_strategy
        state.last_explanation_summary = self.lecturer.summarize_explanation(reply)
        self.memory.save_course_state(state)
        self.memory.add_message(
            self.course.course_id, "ai", reply, frame.concept_id
        )
        return TurnResult(
            reply=reply,
            phase=state.phase,
            concept_id=frame.concept_id,
            concept_title=frame.title,
            assessment=assessment,
            moved_forward=False,
            remediation_active=True,
        )

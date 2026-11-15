from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.lecturer import Lecturer
from core.models import (
    AssessmentResult,
    LessonPhase,
    StackFrame,
    StudentIntent,
    UnderstandingStatus,
)
from courses.loader import CourseBrain
from memory.classroom_memory import ClassroomMemory, ClassroomState, UserAccount
from memory.student_memory import StudentMemory
from router.base import RouteRequest
from router.router import AIRouter


@dataclass
class ClassTurnResult:
    status: str  # "waiting" | "replied"
    reply: Optional[str] = None
    waiting_for: List[str] = field(default_factory=list)
    phase: str = ""
    concept_id: str = ""
    concept_title: str = ""
    moved_forward: bool = False
    remediation_active: bool = False
    class_decision: str = ""  # GO_FORWARD | STAY_AND_BRIDGE | REMEDIATE | DRILL
    student_assessments: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class ClassroomEngine:
    def __init__(
        self,
        course: CourseBrain,
        class_memory: ClassroomMemory,
        shared_memory: StudentMemory,
        router: AIRouter,
    ) -> None:
        self.course = course
        self.cm = class_memory
        self.mem = shared_memory
        self.router = router
        self.lecturer = Lecturer(router)

    def start_room_if_needed(self, room_code: str) -> Optional[str]:
        room = self.cm.get_room(room_code)
        if room is None or room.status != "ACTIVE":
            return None

        existing = self.cm.get_room_messages(room.room_code, limit=1)
        if existing:
            return None

        members = self.cm.get_room_members(room.room_code)
        names = " and ".join(m.display_name for m in members) or "everyone"
        greeting = (
            f"Hey {names}! Welcome to {self.course.code} — {self.course.title}. "
            f"How are you both doing today?"
        )
        self.cm.add_room_message(
            room_code=room.room_code,
            role="ai",
            sender_name=f"{self.course.code} Lecturer",
            content=greeting,
            concept_id=room.current_concept_id,
            turn_id=room.turn_id,
        )
        return greeting

    def submit_student_message(
        self, room_code: str, user: UserAccount, text: str
    ) -> ClassTurnResult:
        room = self.cm.get_room(room_code)
        if room is None:
            raise ValueError(f"Room '{room_code}' does not exist.")
        if room.status == "WAITING":
            members = self.cm.get_room_members(room.room_code)
            remaining = max(0, room.required_students - len(members))
            return ClassTurnResult(
                status="waiting",
                waiting_for=[f"{remaining} more student(s) to join"],
                phase=room.phase,
                concept_id=room.current_concept_id,
            )

        self.cm.add_room_message(
            room_code=room.room_code,
            role="user",
            sender_name=user.display_name,
            content=text,
            concept_id=room.current_concept_id,
            turn_id=room.turn_id,
            user_id=user.user_id,
        )

        # Open review after completing entire course
        if room.phase == LessonPhase.COMPLETED:
            recent = self._recent_for_llm(room.room_code)
            req = RouteRequest(
                course_id=self.course.course_id,
                task_type="teach",
                preferred_provider=self.course.ai_preferences.get("preferred_provider"),
                preferred_model=self.course.ai_preferences.get("teach_model"),
            )
            res = self.router.generate(
                request=req,
                system_prompt=(
                    f"Friendly {self.course.code} lecturer answering a review question "
                    f"for the class in under 55 words, plain text."
                ),
                messages=recent,
                temperature=0.6,
            )
            clean_reply = self.lecturer._clean_text(res.text)
            room.turn_id += 1
            self.cm.save_room(room)
            self.cm.add_room_message(
                room.room_code,
                "ai",
                f"{self.course.code} Lecturer",
                clean_reply,
                "completed",
                room.turn_id,
            )
            return ClassTurnResult(
                status="replied",
                reply=clean_reply,
                phase=room.phase,
                concept_id="completed",
                concept_title="Course Completed",
            )

        self.cm.submit_answer(room.room_code, room.turn_id, user.user_id, text)
        members = self.cm.get_room_members(room.room_code)
        submissions = self.cm.get_turn_submissions(room.room_code, room.turn_id)

        submitted_ids = {s["user_id"] for s in submissions}
        waiting_names = [
            m.display_name for m in members if m.user_id not in submitted_ids
        ]

        if waiting_names:
            concept = self.course.get_concept(room.current_concept_id)
            return ClassTurnResult(
                status="waiting",
                waiting_for=waiting_names,
                phase=room.phase,
                concept_id=room.current_concept_id,
                concept_title=concept.title if concept else "",
            )

        return self._process_class_turn(room, members, submissions)

    def force_continue_turn(self, room_code: str) -> ClassTurnResult:
        room = self.cm.get_room(room_code)
        if room is None:
            raise ValueError(f"Room '{room_code}' does not exist.")
        members = self.cm.get_room_members(room.room_code)
        submissions = self.cm.get_turn_submissions(room.room_code, room.turn_id)
        if not submissions:
            raise ValueError("At least one student must answer before continuing.")
        return self._process_class_turn(room, members, submissions)

    def _recent_for_llm(self, room_code: str, limit: int = 10) -> List[Dict[str, str]]:
        msgs = self.cm.get_room_messages(room_code, limit=limit)
        formatted: List[Dict[str, str]] = []
        for m in msgs:
            if m["role"] == "ai":
                formatted.append({"role": "assistant", "content": m["content"]})
            else:
                formatted.append(
                    {"role": "user", "content": f"{m['sender_name']}: {m['content']}"}
                )
        return formatted

    def _call_class_lecturer(self, system_prompt: str, messages: List[Dict[str, str]]) -> str:
        req = RouteRequest(
            course_id=self.course.course_id,
            task_type="teach",
            preferred_provider=self.course.ai_preferences.get("preferred_provider"),
            preferred_model=self.course.ai_preferences.get("teach_model"),
        )
        res = self.router.generate(
            request=req,
            system_prompt=system_prompt,
            messages=messages,
            temperature=0.65,
        )
        return self.lecturer._clean_text(res.text)

    def _process_class_turn(
        self,
        room: ClassroomState,
        members: List[UserAccount],
        submissions: List[Dict[str, Any]],
    ) -> ClassTurnResult:
        concept = self.course.get_concept(room.current_concept_id)
        names = " and ".join(m.display_name for m in members)
        recent = self._recent_for_llm(room.room_code)

        # 1. GREETING -> DIAGNOSTIC_INTRO
        if room.phase == LessonPhase.GREETING:
            intro_line = self.course.opening.get(
                "intro_line",
                f"Today we will learn {self.course.title}, one tiny step at a time.",
            )
            diag_q = self.course.opening.get(
                "diagnostic_question",
                "Before we begin, what do you think statistics means?",
            )
            student_lines = "\n".join(
                f"- {s['display_name']}: {s['content']}" for s in submissions
            )
            prompt = (
                f"You are a warm private lecturer teaching two students ({names}) together.\n"
                f"They just responded to your greeting:\n{student_lines}\n\n"
                f"Respond warmly in 1 short sentence mentioning both if natural, "
                f"add '{intro_line}', and end with: '{diag_q}'\n"
                f"Max 50 words total. Plain spoken text only (no markdown)."
            )
            reply = self._call_class_lecturer(prompt, recent)
            room.phase = LessonPhase.DIAGNOSTIC_INTRO
            room.turn_id += 1
            self.cm.save_room(room)
            self.cm.add_room_message(
                room.room_code,
                "ai",
                f"{self.course.code} Lecturer",
                reply,
                room.current_concept_id,
                room.turn_id,
            )
            return ClassTurnResult(
                status="replied",
                reply=reply,
                phase=room.phase,
                concept_id=room.current_concept_id,
                concept_title=concept.title if concept else "",
            )

        # 2. DIAGNOSTIC_INTRO -> TEACHING Concept 1.1
        if room.phase == LessonPhase.DIAGNOSTIC_INTRO:
            assert concept is not None
            student_lines = "\n".join(
                f"- {s['display_name']}: {s['content']}" for s in submissions
            )
            prompt = (
                f"You are teaching {names} together in {self.course.code}.\n"
                f"Their guesses on what statistics means:\n{student_lines}\n\n"
                f"Now introduce ONLY Concept [{concept.id}] {concept.title}.\n"
                f"Core Idea: {concept.key_idea}\n"
                f"Example: {concept.suggested_example}\n"
                f"Question Idea: {concept.check_prompt}\n\n"
                f"Write 3 short lines (under 60 words total, plain text, no markdown):\n"
                f"Line 1: Warmly affirm their thoughts.\n"
                f"Line 2: Explain THIS concept in 1 simple sentence + 1 tiny example.\n"
                f"Line 3: Ask 1 clear question for both students to answer."
            )
            reply = self._call_class_lecturer(prompt, recent)
            room.phase = LessonPhase.TEACHING
            room.attempt_count = 0
            room.last_strategy = "initial_explanation"
            room.last_explanation_summary = self.lecturer.summarize_explanation(reply)
            room.turn_id += 1
            self.cm.save_room(room)
            for m in members:
                self.cm.mark_student_progress(
                    m.user_id, self.course.course_id, concept.id, "in_progress", 0
                )
            self.cm.add_room_message(
                room.room_code,
                "ai",
                f"{self.course.code} Lecturer",
                reply,
                concept.id,
                room.turn_id,
            )
            return ClassTurnResult(
                status="replied",
                reply=reply,
                phase=room.phase,
                concept_id=concept.id,
                concept_title=concept.title,
            )

        # 3. REMEDIATION or TEACHING
        is_remediation = room.phase == LessonPhase.REMEDIATION and len(room.concept_stack) > 0
        if is_remediation:
            frame = room.concept_stack[-1]
            active_id = frame.concept_id
            active_title = frame.title
            active_goal = frame.learning_goal
            active_idea = frame.learning_goal
            attempts_so_far = frame.attempts
        else:
            assert concept is not None
            active_id = concept.id
            active_title = concept.title
            active_goal = concept.learning_goal
            active_idea = concept.key_idea
            attempts_so_far = room.attempt_count

        assessments = self._assess_class(
            concept_id=active_id,
            concept_title=active_title,
            learning_goal=active_goal,
            key_idea=active_idea,
            submissions=submissions,
            recent_messages=recent,
            attempts=attempts_so_far,
        )

        # Save per-student status
        if not is_remediation:
            for sub in submissions:
                uid = sub["user_id"]
                a = assessments[uid]
                st_status = (
                    "mastered"
                    if a.status == UnderstandingStatus.UNDERSTOOD
                    else ("difficult" if attempts_so_far >= 1 else "in_progress")
                )
                self.cm.mark_student_progress(
                    uid,
                    self.course.course_id,
                    active_id,
                    st_status,
                    attempts_so_far + 1,
                    a.diagnosis,
                )

        summary_map = {
            sub["display_name"]: {
                "status": assessments[sub["user_id"]].status,
                "intent": assessments[sub["user_id"]].student_intent,
                "diagnosis": assessments[sub["user_id"]].diagnosis,
            }
            for sub in submissions
        }

        # Check for Practice / Example / Restart requests
        special_intents = [
            (sub, assessments[sub["user_id"]])
            for sub in submissions
            if assessments[sub["user_id"]].student_intent
            in (
                StudentIntent.WANTS_PRACTICE,
                StudentIntent.WANTS_EXAMPLE,
                StudentIntent.WANTS_RESTART,
            )
        ]
        if special_intents:
            mode = special_intents[0][1].student_intent
            requester = special_intents[0][0]["display_name"]
            prompt = (
                f"You are teaching {names} together in {self.course.code}.\n"
                f"Current Concept [{active_id}]: {active_title}\n"
                f"Core Idea: {active_idea}\n"
                f"{requester} asked for: {mode}.\n"
                f"Give ONE short practical notebook example or mini-question on THIS concept for both students to try.\n"
                f"Max 55 words, 2-3 short lines, plain text only."
            )
            reply = self._call_class_lecturer(prompt, recent)
            room.last_strategy = mode
            room.last_explanation_summary = self.lecturer.summarize_explanation(reply)
            room.turn_id += 1
            self.cm.save_room(room)
            self.cm.add_room_message(
                room.room_code,
                "ai",
                f"{self.course.code} Lecturer",
                reply,
                active_id,
                room.turn_id,
            )
            return ClassTurnResult(
                status="replied",
                reply=reply,
                phase=room.phase,
                concept_id=active_id,
                concept_title=active_title,
                moved_forward=False,
                remediation_active=is_remediation,
                class_decision="DRILL",
                student_assessments=summary_map,
            )

        all_understood = all(
            assessments[s["user_id"]].status == UnderstandingStatus.UNDERSTOOD
            for s in submissions
        )

        # Case 1: ALL UNDERSTOOD -> GO FORWARD!
        if all_understood:
            if room.last_explanation_summary:
                self.mem.log_explanation_attempt(
                    f"room:{room.room_code}",
                    active_id,
                    room.last_strategy or "class_explanation",
                    room.last_explanation_summary,
                    "worked",
                )

            if is_remediation:
                finished_frame = room.concept_stack.pop()
                if not room.concept_stack:
                    room.phase = LessonPhase.TEACHING
                    paused = self.course.get_concept(room.current_concept_id)
                    assert paused is not None
                    prompt = (
                        f"Both {names} mastered the foundation '{finished_frame.title}'!\n"
                        f"Praise them warmly in 1 sentence, then bring them back to Concept [{paused.id}] {paused.title}.\n"
                        f"Core Idea: {paused.key_idea}\n"
                        f"Example: {paused.suggested_example}\n"
                        f"Question: {paused.check_prompt}\n"
                        f"Max 60 words, 3 short lines, plain text."
                    )
                    reply = self._call_class_lecturer(prompt, recent)
                    room.last_strategy = "post_remediation_bridge"
                    room.last_explanation_summary = self.lecturer.summarize_explanation(
                        reply
                    )
                    room.turn_id += 1
                    self.cm.save_room(room)
                    self.cm.add_room_message(
                        room.room_code,
                        "ai",
                        f"{self.course.code} Lecturer",
                        reply,
                        paused.id,
                        room.turn_id,
                    )
                    return ClassTurnResult(
                        status="replied",
                        reply=reply,
                        phase=room.phase,
                        concept_id=paused.id,
                        concept_title=paused.title,
                        moved_forward=True,
                        remediation_active=False,
                        class_decision="GO_FORWARD",
                        student_assessments=summary_map,
                    )

            assert concept is not None
            next_concept = self.course.get_next_concept(concept.id)
            if next_concept is None:
                room.phase = LessonPhase.COMPLETED
                room.status = "COMPLETED"
                room.turn_id += 1
                self.cm.save_room(room)
                reply = (
                    f"Amazing teamwork, {names}! You both mastered '{concept.title}' and finished "
                    f"all concepts in {self.course.code}. Want to review any topic together?"
                )
                self.cm.add_room_message(
                    room.room_code,
                    "ai",
                    f"{self.course.code} Lecturer",
                    reply,
                    concept.id,
                    room.turn_id,
                )
                return ClassTurnResult(
                    status="replied",
                    reply=reply,
                    phase=room.phase,
                    concept_id=concept.id,
                    concept_title=concept.title,
                    moved_forward=True,
                    class_decision="GO_FORWARD",
                    student_assessments=summary_map,
                )

            room.current_concept_id = next_concept.id
            room.current_topic_id = next_concept.topic_id
            room.attempt_count = 0
            room.last_strategy = "initial_explanation"
            for m in members:
                self.cm.mark_student_progress(
                    m.user_id, self.course.course_id, next_concept.id, "in_progress", 0
                )

            prompt = (
                f"Both {names} understood '{concept.title}'!\n"
                f"Now introduce ONLY Concept [{next_concept.id}] {next_concept.title}.\n"
                f"Core Idea: {next_concept.key_idea}\n"
                f"Example: {next_concept.suggested_example}\n"
                f"Question Idea: {next_concept.check_prompt}\n\n"
                f"Write 3 short lines (under 60 words, plain text, no markdown):\n"
                f"Line 1: Celebrate both getting the previous step.\n"
                f"Line 2: Explain THIS new concept simply + 1 tiny example.\n"
                f"Line 3: Ask 1 clear question for both students."
            )
            reply = self._call_class_lecturer(prompt, recent)
            room.last_explanation_summary = self.lecturer.summarize_explanation(reply)
            room.turn_id += 1
            self.cm.save_room(room)
            self.cm.add_room_message(
                room.room_code,
                "ai",
                f"{self.course.code} Lecturer",
                reply,
                next_concept.id,
                room.turn_id,
            )
            return ClassTurnResult(
                status="replied",
                reply=reply,
                phase=room.phase,
                concept_id=next_concept.id,
                concept_title=next_concept.title,
                moved_forward=True,
                class_decision="GO_FORWARD",
                student_assessments=summary_map,
            )

        # Case 2: Missing Foundation Detected -> Push onto Foundation Stack!
        missing_found = next(
            (
                assessments[s["user_id"]]
                for s in submissions
                if assessments[s["user_id"]].status == UnderstandingStatus.NOT_UNDERSTOOD
                and assessments[s["user_id"]].missing_foundation
            ),
            None,
        )
        if missing_found and not is_remediation:
            if room.last_explanation_summary:
                self.mem.log_explanation_attempt(
                    f"room:{room.room_code}",
                    active_id,
                    room.last_strategy or "class_explanation",
                    room.last_explanation_summary,
                    "did_not_work",
                )
            fkey = missing_found.missing_foundation or "basic_foundation"
            frame = StackFrame(
                concept_id=f"foundation:{fkey}",
                title=fkey.replace("_", " ").title(),
                learning_goal=(
                    missing_found.missing_foundation_goal
                    or f"Build intuition for {fkey.replace('_', ' ')}."
                ),
                is_remediation=True,
                reason=missing_found.diagnosis,
                attempts=0,
            )
            room.concept_stack.append(frame)
            room.phase = LessonPhase.REMEDIATION
            room.last_strategy = "remediate_foundation"

            prompt = (
                f"You are teaching {names}. Before continuing '{active_title}', let's quickly build "
                f"a simpler foundation: '{frame.title}' ({frame.learning_goal}).\n"
                f"Write 3 short lines (under 55 words, plain text): reassure both, explain '{frame.title}' "
                f"with 1 tiny example, and ask 1 easy check question."
            )
            reply = self._call_class_lecturer(prompt, recent)
            room.last_explanation_summary = self.lecturer.summarize_explanation(reply)
            room.turn_id += 1
            self.cm.save_room(room)
            self.cm.add_room_message(
                room.room_code,
                "ai",
                f"{self.course.code} Lecturer",
                reply,
                frame.concept_id,
                room.turn_id,
            )
            return ClassTurnResult(
                status="replied",
                reply=reply,
                phase=room.phase,
                concept_id=frame.concept_id,
                concept_title=frame.title,
                moved_forward=False,
                remediation_active=True,
                class_decision="REMEDIATE",
                student_assessments=summary_map,
            )

        # Case 3: Mixed or Not Understood -> STAY & BRIDGE (Leave no one behind!)
        if room.last_explanation_summary:
            self.mem.log_explanation_attempt(
                f"room:{room.room_code}",
                active_id,
                room.last_strategy or "class_explanation",
                room.last_explanation_summary,
                "did_not_work",
            )

        if is_remediation:
            room.concept_stack[-1].attempts += 1
        else:
            room.attempt_count += 1

        failed = self.mem.get_failed_explanations(f"room:{room.room_code}", active_id)
        avoid = (
            "\n".join(f"- Avoid repeating: {r.explanation_summary}" for r in failed)
            if failed
            else "- None yet."
        )
        diag_lines = "\n".join(
            f"- {s['display_name']}: {assessments[s['user_id']].status} ({assessments[s['user_id']].diagnosis})"
            for s in submissions
        )

        prompt = (
            f"You are teaching {names} together. DO NOT move forward yet—help everyone get THIS concept.\n"
            f"Concept [{active_id}]: {active_title}\n"
            f"Core Idea: {active_idea}\n\n"
            f"Student Status:\n{diag_lines}\n\n"
            f"Previous attempts that did NOT fully click:\n{avoid}\n\n"
            f"Write 3 short lines (under 60 words total, plain text, no markdown):\n"
            f"Line 1: Praise whoever got part/all right by name, and warmly encourage whoever is stuck.\n"
            f"Line 2: Explain the SAME idea from a fresher, simpler angle.\n"
            f"Line 3: Ask 1 easy joint check question."
        )
        reply = self._call_class_lecturer(prompt, recent)
        room.last_strategy = "stay_and_bridge"
        room.last_explanation_summary = self.lecturer.summarize_explanation(reply)
        room.turn_id += 1
        self.cm.save_room(room)
        self.cm.add_room_message(
            room.room_code,
            "ai",
            f"{self.course.code} Lecturer",
            reply,
            active_id,
            room.turn_id,
        )
        return ClassTurnResult(
            status="replied",
            reply=reply,
            phase=room.phase,
            concept_id=active_id,
            concept_title=active_title,
            moved_forward=False,
            remediation_active=is_remediation,
            class_decision="STAY_AND_BRIDGE",
            student_assessments=summary_map,
        )

    def _assess_class(
        self,
        concept_id: str,
        concept_title: str,
        learning_goal: str,
        key_idea: str,
        submissions: List[Dict[str, Any]],
        recent_messages: List[Dict[str, str]],
        attempts: int,
    ) -> Dict[int, AssessmentResult]:
        last_ai = ""
        for m in reversed(recent_messages):
            if m["role"] == "assistant":
                last_ai = m["content"]
                break

        subs_text = "\n".join(
            f"- user_id={s['user_id']} ({s['display_name']}): {s['content']}"
            for s in submissions
        )

        system_prompt = f"""You are the Understanding Engine for a 2-student shared classroom.
Evaluate EACH student's reply independently for Concept [{concept_id}] {concept_title}.
Learning Goal: {learning_goal}
Core Idea: {key_idea}
Class attempts so far: {attempts}

For EACH student, determine:
- "student_intent": "answering" | "wants_practice" | "wants_example" | "wants_restart"
- "status": "UNDERSTOOD" | "PARTLY_UNDERSTOOD" | "NOT_UNDERSTOOD"
- "diagnosis": 1 short sentence
- "missing_foundation": null or short_snake_case_key
- "missing_foundation_goal": null or 1 short sentence

Return valid JSON ONLY:
{{
  "evaluations": [
    {{
      "user_id": 1,
      "student_intent": "answering",
      "status": "UNDERSTOOD | PARTLY_UNDERSTOOD | NOT_UNDERSTOOD",
      "diagnosis": "...",
      "missing_foundation": null,
      "missing_foundation_goal": null
    }}
  ]
}}"""

        user_prompt = f"Lecturer asked:\n{last_ai}\n\nStudent replies:\n{subs_text}"
        req = RouteRequest(
            course_id=self.course.course_id,
            task_type="assess",
            preferred_provider=self.course.ai_preferences.get("preferred_provider"),
            preferred_model=self.course.ai_preferences.get("assess_model"),
        )
        res = self.router.generate(
            request=req,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            json_mode=True,
            temperature=0.2,
        )

        results: Dict[int, AssessmentResult] = {}
        cleaned = res.text.strip()
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            cleaned = match.group(0)

        try:
            data = json.loads(cleaned)
            for item in data.get("evaluations", []):
                uid = int(item["user_id"])
                raw_st = str(item.get("status", "")).upper()
                if raw_st == "UNDERSTOOD":
                    status = UnderstandingStatus.UNDERSTOOD
                elif "NOT" in raw_st:
                    status = UnderstandingStatus.NOT_UNDERSTOOD
                else:
                    status = UnderstandingStatus.PARTLY_UNDERSTOOD

                raw_int = str(item.get("student_intent", "answering")).lower()
                intent = (
                    raw_int
                    if raw_int
                    in (
                        StudentIntent.WANTS_PRACTICE,
                        StudentIntent.WANTS_EXAMPLE,
                        StudentIntent.WANTS_RESTART,
                    )
                    else StudentIntent.ANSWERING
                )
                results[uid] = AssessmentResult(
                    status=status,
                    diagnosis=str(item.get("diagnosis", "")).strip() or "Evaluated.",
                    student_intent=intent,
                    missing_foundation=item.get("missing_foundation") or None,
                    missing_foundation_goal=item.get("missing_foundation_goal") or None,
                )
        except Exception:
            pass

        for s in submissions:
            if s["user_id"] not in results:
                results[s["user_id"]] = AssessmentResult(
                    status=UnderstandingStatus.PARTLY_UNDERSTOOD,
                    diagnosis="Check gently with this student.",
                )
        return results

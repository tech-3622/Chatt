from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

from core.models import (
    ExplanationRecord,
    LessonState,
    StackFrame,
    StudentProfile,
)
from memory.database import get_connection, init_db


class StudentMemory:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path
        init_db(self.db_path)

    def _connect(self):
        return get_connection(self.db_path)

    # --- Student Profile (Central Personal Learning Profile) ---

    def get_or_create_profile(self, student_id: str = "default_student") -> StudentProfile:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM student_profile WHERE student_id = ?",
                (student_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO student_profile (student_id) VALUES (?)",
                    (student_id,),
                )
                conn.commit()
                return StudentProfile(student_id=student_id)
            return StudentProfile(
                student_id=row["student_id"],
                name=row["name"],
                preferred_learning_style=row["preferred_learning_style"],
                pace_notes=row["pace_notes"],
            )
        finally:
            conn.close()

    def update_learning_style(
        self, learning_style: str, student_id: str = "default_student"
    ) -> None:
        self.get_or_create_profile(student_id)
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE student_profile
                SET preferred_learning_style = ?, updated_at = CURRENT_TIMESTAMP
                WHERE student_id = ?
                """,
                (learning_style, student_id),
            )
            conn.commit()
        finally:
            conn.close()

    # --- Course State & Foundation Stack ---

    def get_course_state(self, course_id: str) -> Optional[LessonState]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM course_state WHERE course_id = ?",
                (course_id,),
            ).fetchone()
            if row is None:
                return None

            raw_stack = json.loads(row["concept_stack_json"] or "[]")
            stack = [StackFrame(**item) for item in raw_stack]
            return LessonState(
                course_id=row["course_id"],
                phase=row["phase"],
                current_topic_id=row["current_topic_id"],
                current_concept_id=row["current_concept_id"],
                concept_stack=stack,
                attempt_count=row["attempt_count"],
                last_strategy=row["last_strategy"],
                last_explanation_summary=row["last_explanation_summary"],
            )
        finally:
            conn.close()

    def save_course_state(self, state: LessonState) -> None:
        stack_json = json.dumps([asdict(frame) for frame in state.concept_stack])
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO course_state (
                    course_id, phase, current_topic_id, current_concept_id,
                    concept_stack_json, attempt_count, last_strategy,
                    last_explanation_summary, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(course_id) DO UPDATE SET
                    phase = excluded.phase,
                    current_topic_id = excluded.current_topic_id,
                    current_concept_id = excluded.current_concept_id,
                    concept_stack_json = excluded.concept_stack_json,
                    attempt_count = excluded.attempt_count,
                    last_strategy = excluded.last_strategy,
                    last_explanation_summary = excluded.last_explanation_summary,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    state.course_id,
                    state.phase,
                    state.current_topic_id,
                    state.current_concept_id,
                    stack_json,
                    state.attempt_count,
                    state.last_strategy,
                    state.last_explanation_summary,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # --- Concept Mastery & Difficult Concepts ---

    def mark_concept_progress(
        self,
        course_id: str,
        concept_id: str,
        status: str,
        attempts: int,
        diagnosis: str = "",
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO concept_progress (
                    course_id, concept_id, status, attempts, last_diagnosis, updated_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(course_id, concept_id) DO UPDATE SET
                    status = excluded.status,
                    attempts = excluded.attempts,
                    last_diagnosis = excluded.last_diagnosis,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (course_id, concept_id, status, attempts, diagnosis),
            )
            conn.commit()
        finally:
            conn.close()

    def get_concepts_by_status(self, course_id: str, status: str) -> List[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT concept_id FROM concept_progress
                WHERE course_id = ? AND status = ?
                ORDER BY updated_at ASC
                """,
                (course_id, status),
            ).fetchall()
            return [row["concept_id"] for row in rows]
        finally:
            conn.close()

    def get_progress_summary(self, course_id: str) -> Dict[str, List[str]]:
        return {
            "mastered": self.get_concepts_by_status(course_id, "mastered"),
            "difficult": self.get_concepts_by_status(course_id, "difficult"),
            "in_progress": self.get_concepts_by_status(course_id, "in_progress"),
        }

    # --- Explanation History (Prevent Repeating What Failed) ---

    def log_explanation_attempt(
        self,
        course_id: str,
        concept_id: str,
        strategy_used: str,
        explanation_summary: str,
        outcome: str,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO explanation_attempts (
                    course_id, concept_id, strategy_used, explanation_summary, outcome
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (course_id, concept_id, strategy_used, explanation_summary, outcome),
            )
            conn.commit()
        finally:
            conn.close()

    def get_failed_explanations(
        self, course_id: str, concept_id: str
    ) -> List[ExplanationRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT concept_id, strategy_used, explanation_summary, outcome
                FROM explanation_attempts
                WHERE course_id = ? AND concept_id = ? AND outcome != 'worked'
                ORDER BY id DESC
                LIMIT 6
                """,
                (course_id, concept_id),
            ).fetchall()
            return [
                ExplanationRecord(
                    concept_id=row["concept_id"],
                    strategy_used=row["strategy_used"],
                    explanation_summary=row["explanation_summary"],
                    outcome=row["outcome"],
                )
                for row in rows
            ]
        finally:
            conn.close()

    def get_worked_strategies(self, course_id: str) -> List[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT strategy_used, COUNT(*) as cnt
                FROM explanation_attempts
                WHERE course_id = ? AND outcome = 'worked'
                GROUP BY strategy_used
                ORDER BY cnt DESC
                LIMIT 3
                """,
                (course_id,),
            ).fetchall()
            return [row["strategy_used"] for row in rows]
        finally:
            conn.close()

    # --- Conversation History ---

    def add_message(
        self, course_id: str, role: str, content: str, concept_id: str = ""
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO session_messages (course_id, role, content, concept_id)
                VALUES (?, ?, ?, ?)
                """,
                (course_id, role, content, concept_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_recent_messages(self, course_id: str, limit: int = 12) -> List[Dict[str, str]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT role, content, concept_id
                FROM session_messages
                WHERE course_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (course_id, limit),
            ).fetchall()
            return [
                {
                    "role": row["role"],
                    "content": row["content"],
                    "concept_id": row["concept_id"],
                }
                for row in reversed(rows)
            ]
        finally:
            conn.close()

    # --- Cross-Course Foundations ---

    def record_foundation(
        self, foundation_key: str, status: str, notes: str = ""
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO cross_course_foundations (
                    foundation_key, status, notes, updated_at
                )
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(foundation_key) DO UPDATE SET
                    status = excluded.status,
                    notes = excluded.notes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (foundation_key, status, notes),
            )
            conn.commit()
        finally:
            conn.close()

    def get_weak_foundations(self) -> List[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT foundation_key FROM cross_course_foundations
                WHERE status = 'needs_support'
                ORDER BY updated_at DESC
                """
            ).fetchall()
            return [row["foundation_key"] for row in rows]
        finally:
            conn.close()

    # --- Reset Helper (for testing or restarting a course) ---

    def reset_course(self, course_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM course_state WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM concept_progress WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM explanation_attempts WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM session_messages WHERE course_id = ?", (course_id,))
            conn.commit()
        finally:
            conn.close()

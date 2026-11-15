from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.models import LessonPhase, StackFrame
from memory.database import get_connection, init_db


@dataclass
class UserAccount:
    user_id: int
    username: str
    display_name: str
    preferred_learning_style: str


@dataclass
class ClassroomState:
    room_code: str
    course_id: str
    required_students: int
    status: str  # WAITING | ACTIVE | COMPLETED
    phase: str
    current_topic_id: str
    current_concept_id: str
    concept_stack: List[StackFrame]
    turn_id: int
    attempt_count: int
    last_strategy: str
    last_explanation_summary: str


class ClassroomMemory:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path
        init_db(self.db_path)

    def _connect(self):
        return get_connection(self.db_path)

    # --- Password / PIN Hashing ---

    def _hash_pin(self, pin: str, salt: Optional[str] = None) -> str:
        salt = salt or secrets.token_hex(8)
        digest = hashlib.pbkdf2_hmac(
            "sha256", pin.encode("utf-8"), salt.encode("utf-8"), 50000
        ).hex()
        return f"{salt}${digest}"

    def _verify_pin(self, pin: str, stored_hash: str) -> bool:
        try:
            salt, expected = stored_hash.split("$", 1)
            candidate = self._hash_pin(pin, salt).split("$", 1)[1]
            return hmac.compare_digest(candidate, expected)
        except ValueError:
            return False

    # --- Auth: Sign Up / Login / Token ---

    def signup(
        self, username: str, display_name: str, pin: str
    ) -> Tuple[UserAccount, str]:
        uname = username.strip()
        dname = display_name.strip() or uname
        if len(uname) < 2:
            raise ValueError("Username must be at least 2 characters.")
        if len(pin.strip()) < 3:
            raise ValueError("PIN/Password must be at least 3 characters.")

        pin_hash = self._hash_pin(pin.strip())
        token = secrets.token_hex(24)

        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT user_id FROM users WHERE username = ?", (uname,)
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Username '{uname}' is already taken. Try logging in.")

            cur = conn.execute(
                """
                INSERT INTO users (username, display_name, pin_hash)
                VALUES (?, ?, ?)
                """,
                (uname, dname, pin_hash),
            )
            user_id = cur.lastrowid
            conn.execute(
                "INSERT INTO auth_sessions (token, user_id) VALUES (?, ?)",
                (token, user_id),
            )
            conn.commit()
            return (
                UserAccount(
                    user_id=user_id,
                    username=uname,
                    display_name=dname,
                    preferred_learning_style="everyday_examples",
                ),
                token,
            )
        finally:
            conn.close()

    def login(self, username: str, pin: str) -> Tuple[UserAccount, str]:
        uname = username.strip()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (uname,)
            ).fetchone()
            if row is None or not self._verify_pin(pin.strip(), row["pin_hash"]):
                raise ValueError("Invalid username or PIN.")

            token = secrets.token_hex(24)
            conn.execute(
                "INSERT INTO auth_sessions (token, user_id) VALUES (?, ?)",
                (token, row["user_id"]),
            )
            conn.commit()
            return (
                UserAccount(
                    user_id=row["user_id"],
                    username=row["username"],
                    display_name=row["display_name"],
                    preferred_learning_style=row["preferred_learning_style"],
                ),
                token,
            )
        finally:
            conn.close()

    def get_user_by_token(self, token: str) -> Optional[UserAccount]:
        if not token:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT u.*
                FROM auth_sessions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.token = ?
                """,
                (token,),
            ).fetchone()
            if row is None:
                return None
            return UserAccount(
                user_id=row["user_id"],
                username=row["username"],
                display_name=row["display_name"],
                preferred_learning_style=row["preferred_learning_style"],
            )
        finally:
            conn.close()

    # --- Classrooms & Waiting Lobby ---

    def create_or_join_room(
        self,
        room_code: str,
        course_id: str,
        user_id: int,
        first_topic_id: str,
        first_concept_id: str,
        required_students: int = 2,
    ) -> Tuple[ClassroomState, List[UserAccount], bool]:
        code = room_code.strip().upper()
        if len(code) < 2:
            raise ValueError("Room code must be at least 2 characters.")

        conn = self._connect()
        just_activated = False
        try:
            room = conn.execute(
                "SELECT * FROM classrooms WHERE room_code = ?", (code,)
            ).fetchone()

            if room is None:
                conn.execute(
                    """
                    INSERT INTO classrooms (
                        room_code, course_id, required_students, status,
                        phase, current_topic_id, current_concept_id
                    )
                    VALUES (?, ?, ?, 'WAITING', ?, ?, ?)
                    """,
                    (
                        code,
                        course_id,
                        required_students,
                        LessonPhase.GREETING,
                        first_topic_id,
                        first_concept_id,
                    ),
                )

            members = conn.execute(
                "SELECT user_id FROM classroom_members WHERE room_code = ?",
                (code,),
            ).fetchall()
            member_ids = [m["user_id"] for m in members]

            if user_id not in member_ids:
                req_count = (
                    room["required_students"] if room is not None else required_students
                )
                if len(member_ids) >= req_count:
                    raise ValueError(
                        f"Room '{code}' is already full ({len(member_ids)}/{req_count} students)."
                    )
                conn.execute(
                    "INSERT INTO classroom_members (room_code, user_id) VALUES (?, ?)",
                    (code, user_id),
                )
                member_ids.append(user_id)

            # Check if room just reached required_students -> Start Class!
            room_row = conn.execute(
                "SELECT * FROM classrooms WHERE room_code = ?", (code,)
            ).fetchone()
            if (
                room_row["status"] == "WAITING"
                and len(member_ids) >= room_row["required_students"]
            ):
                conn.execute(
                    """
                    UPDATE classrooms
                    SET status = 'ACTIVE', updated_at = CURRENT_TIMESTAMP
                    WHERE room_code = ?
                    """,
                    (code,),
                )
                just_activated = True

            conn.commit()
        finally:
            conn.close()

        return self.get_room(code), self.get_room_members(code), just_activated

    def get_room(self, room_code: str) -> Optional[ClassroomState]:
        code = room_code.strip().upper()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM classrooms WHERE room_code = ?", (code,)
            ).fetchone()
            if row is None:
                return None
            raw_stack = json.loads(row["concept_stack_json"] or "[]")
            stack = [StackFrame(**item) for item in raw_stack]
            return ClassroomState(
                room_code=row["room_code"],
                course_id=row["course_id"],
                required_students=row["required_students"],
                status=row["status"],
                phase=row["phase"],
                current_topic_id=row["current_topic_id"],
                current_concept_id=row["current_concept_id"],
                concept_stack=stack,
                turn_id=row["turn_id"],
                attempt_count=row["attempt_count"],
                last_strategy=row["last_strategy"],
                last_explanation_summary=row["last_explanation_summary"],
            )
        finally:
            conn.close()

    def save_room(self, room: ClassroomState) -> None:
        stack_json = json.dumps([asdict(f) for f in room.concept_stack])
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE classrooms
                SET status = ?,
                    phase = ?,
                    current_topic_id = ?,
                    current_concept_id = ?,
                    concept_stack_json = ?,
                    turn_id = ?,
                    attempt_count = ?,
                    last_strategy = ?,
                    last_explanation_summary = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE room_code = ?
                """,
                (
                    room.status,
                    room.phase,
                    room.current_topic_id,
                    room.current_concept_id,
                    stack_json,
                    room.turn_id,
                    room.attempt_count,
                    room.last_strategy,
                    room.last_explanation_summary,
                    room.room_code,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_room_members(self, room_code: str) -> List[UserAccount]:
        code = room_code.strip().upper()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT u.*
                FROM classroom_members m
                JOIN users u ON u.user_id = m.user_id
                WHERE m.room_code = ?
                ORDER BY m.joined_at ASC
                """,
                (code,),
            ).fetchall()
            return [
                UserAccount(
                    user_id=r["user_id"],
                    username=r["username"],
                    display_name=r["display_name"],
                    preferred_learning_style=r["preferred_learning_style"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    # --- Turn Submissions & Shared Messages ---

    def submit_answer(
        self, room_code: str, turn_id: int, user_id: int, content: str
    ) -> None:
        code = room_code.strip().upper()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO classroom_submissions (room_code, turn_id, user_id, content)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(room_code, turn_id, user_id) DO UPDATE SET
                    content = excluded.content,
                    created_at = CURRENT_TIMESTAMP
                """,
                (code, turn_id, user_id, content),
            )
            conn.commit()
        finally:
            conn.close()

    def get_turn_submissions(
        self, room_code: str, turn_id: int
    ) -> List[Dict[str, Any]]:
        code = room_code.strip().upper()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT s.user_id, u.display_name, u.username, s.content
                FROM classroom_submissions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.room_code = ? AND s.turn_id = ?
                ORDER BY s.created_at ASC
                """,
                (code, turn_id),
            ).fetchall()
            return [
                {
                    "user_id": r["user_id"],
                    "display_name": r["display_name"],
                    "username": r["username"],
                    "content": r["content"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def add_room_message(
        self,
        room_code: str,
        role: str,
        sender_name: str,
        content: str,
        concept_id: str = "",
        turn_id: int = 1,
        user_id: Optional[int] = None,
    ) -> None:
        code = room_code.strip().upper()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO classroom_messages (
                    room_code, role, sender_name, user_id, content, concept_id, turn_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (code, role, sender_name, user_id, content, concept_id, turn_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_room_messages(
        self, room_code: str, limit: int = 60
    ) -> List[Dict[str, Any]]:
        code = room_code.strip().upper()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, role, sender_name, user_id, content, concept_id, turn_id
                FROM classroom_messages
                WHERE room_code = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (code, limit),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "role": r["role"],
                    "sender_name": r["sender_name"],
                    "user_id": r["user_id"],
                    "content": r["content"],
                    "concept_id": r["concept_id"],
                    "turn_id": r["turn_id"],
                }
                for r in reversed(rows)
            ]
        finally:
            conn.close()

    # --- Per-Student Progress ---

    def mark_student_progress(
        self,
        user_id: int,
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
                INSERT INTO student_concept_progress (
                    user_id, course_id, concept_id, status, attempts, last_diagnosis, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, course_id, concept_id) DO UPDATE SET
                    status = excluded.status,
                    attempts = excluded.attempts,
                    last_diagnosis = excluded.last_diagnosis,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, course_id, concept_id, status, attempts, diagnosis),
            )
            conn.commit()
        finally:
            conn.close()

    def get_student_progress_summary(
        self, user_id: int, course_id: str
    ) -> Dict[str, List[str]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT concept_id, status
                FROM student_concept_progress
                WHERE user_id = ? AND course_id = ?
                ORDER BY updated_at ASC
                """,
                (user_id, course_id),
            ).fetchall()
            summary: Dict[str, List[str]] = {
                "mastered": [],
                "difficult": [],
                "in_progress": [],
            }
            for r in rows:
                st = r["status"]
                if st in summary:
                    summary[st].append(r["concept_id"])
            return summary
        finally:
            conn.close()


    def list_rooms_summary(self, user_id: int) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT c.room_code, c.course_id, c.required_students, c.status,
                       c.phase, c.current_concept_id,
                       EXISTS(
                           SELECT 1 FROM classroom_members m
                           WHERE m.room_code = c.room_code AND m.user_id = ?
                       ) AS is_member
                FROM classrooms c
                WHERE c.status = 'WAITING'
                   OR EXISTS(
                       SELECT 1 FROM classroom_members m2
                       WHERE m2.room_code = c.room_code AND m2.user_id = ?
                   )
                ORDER BY c.updated_at DESC
                LIMIT 10
                """,
                (user_id, user_id),
            ).fetchall()

            result: List[Dict[str, Any]] = []
            for r in rows:
                members = self.get_room_members(r["room_code"])
                result.append(
                    {
                        "room_code": r["room_code"],
                        "course_id": r["course_id"],
                        "required_students": r["required_students"],
                        "joined_count": len(members),
                        "member_names": [m.display_name for m in members],
                        "status": r["status"],
                        "phase": r["phase"],
                        "current_concept_id": r["current_concept_id"],
                        "is_member": bool(r["is_member"]),
                    }
                )
            return result
        finally:
            conn.close()

    def reset_room(
        self, room_code: str, first_topic_id: str, first_concept_id: str
    ) -> None:
        code = room_code.strip().upper()
        conn = self._connect()
        try:
            conn.execute("DELETE FROM classroom_submissions WHERE room_code = ?", (code,))
            conn.execute("DELETE FROM classroom_messages WHERE room_code = ?", (code,))
            conn.execute(
                """
                UPDATE classrooms
                SET status = 'ACTIVE',
                    phase = ?,
                    current_topic_id = ?,
                    current_concept_id = ?,
                    concept_stack_json = '[]',
                    turn_id = 1,
                    attempt_count = 0,
                    last_strategy = '',
                    last_explanation_summary = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE room_code = ?
                """,
                (LessonPhase.GREETING, first_topic_id, first_concept_id, code),
            )
            conn.commit()
        finally:
            conn.close()

from __future__ import annotations

import json
import os
import socket
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import config
from core.classroom_engine import ClassTurnResult, ClassroomEngine
from core.lecture_engine import LectureEngine, TurnResult
from core.models import LessonPhase
from courses.loader import load_course
from memory.classroom_memory import ClassroomMemory, UserAccount
from memory.student_memory import StudentMemory
from router.router import AIRouter

WEB_DIR = Path(__file__).resolve().parent / "web"
SOLO_LOCK = threading.Lock()
ROOM_LOCKS_GUARD = threading.Lock()
ROOM_LOCKS: Dict[str, threading.Lock] = {}

course = load_course(config.DEFAULT_COURSE_ID)
memory = StudentMemory()
class_memory = ClassroomMemory()
router = AIRouter()
solo_engine = LectureEngine(course, memory, router)
class_engine = ClassroomEngine(course, class_memory, memory, router)


def get_room_lock(room_code: str) -> threading.Lock:
    key = room_code.strip().upper()
    with ROOM_LOCKS_GUARD:
        if key not in ROOM_LOCKS:
            ROOM_LOCKS[key] = threading.Lock()
        return ROOM_LOCKS[key]


def detect_local_ip() -> Optional[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return None
    finally:
        sock.close()


def build_solo_payload(last_turn: Optional[TurnResult] = None) -> Dict[str, Any]:
    state = memory.get_course_state(course.course_id)
    profile = memory.get_or_create_profile()
    summary = memory.get_progress_summary(course.course_id)
    weak = memory.get_weak_foundations()
    messages = memory.get_recent_messages(course.course_id, limit=50)

    if state is None:
        first = course.get_first_concept()
        concept_id = first.id if first else ""
        concept_title = first.title if first else ""
        topic_title = first.topic_title if first else ""
        phase = LessonPhase.GREETING
        remediation_active = False
        stack = []
    else:
        concept = course.get_concept(state.current_concept_id)
        remediation_active = (
            state.phase == LessonPhase.REMEDIATION and len(state.concept_stack) > 0
        )
        if remediation_active:
            top = state.concept_stack[-1]
            concept_id = top.concept_id
            concept_title = top.title
        else:
            concept_id = state.current_concept_id
            concept_title = concept.title if concept else "Course Completed"
        topic_title = concept.topic_title if concept else ""
        phase = state.phase
        stack = [asdict(f) for f in state.concept_stack]

    payload: Dict[str, Any] = {
        "mode": "solo",
        "course": {
            "course_id": course.course_id,
            "code": course.code,
            "title": course.title,
            "total_concepts": len(course.concepts),
        },
        "state": {
            "phase": phase,
            "concept_id": concept_id,
            "concept_title": concept_title,
            "topic_title": topic_title,
            "remediation_active": remediation_active,
            "stack": stack,
        },
        "progress": {
            "mastered": summary["mastered"],
            "in_progress": summary["in_progress"],
            "difficult": summary["difficult"],
            "weak_foundations": weak,
            "learning_style": profile.preferred_learning_style,
        },
        "messages": messages,
    }

    if last_turn is not None:
        payload["last_turn"] = {
            "reply": last_turn.reply,
            "phase": last_turn.phase,
            "concept_id": last_turn.concept_id,
            "concept_title": last_turn.concept_title,
            "moved_forward": last_turn.moved_forward,
            "remediation_active": last_turn.remediation_active,
            "assessment": asdict(last_turn.assessment) if last_turn.assessment else None,
        }

    return payload


def build_room_payload(
    room_code: str,
    user: UserAccount,
    turn_result: Optional[ClassTurnResult] = None,
) -> Dict[str, Any]:
    room = class_memory.get_room(room_code)
    if room is None:
        raise ValueError(f"Room '{room_code}' not found.")

    members = class_memory.get_room_members(room.room_code)
    submissions = class_memory.get_turn_submissions(room.room_code, room.turn_id)
    submitted_ids = {s["user_id"] for s in submissions}
    waiting_for = [m.display_name for m in members if m.user_id not in submitted_ids]
    messages = class_memory.get_room_messages(room.room_code, limit=60)
    progress = class_memory.get_student_progress_summary(
        user.user_id, course.course_id
    )

    concept = course.get_concept(room.current_concept_id)
    remediation_active = (
        room.phase == LessonPhase.REMEDIATION and len(room.concept_stack) > 0
    )
    if remediation_active:
        top = room.concept_stack[-1]
        concept_id = top.concept_id
        concept_title = top.title
    else:
        concept_id = room.current_concept_id
        concept_title = concept.title if concept else "Course Completed"

    payload: Dict[str, Any] = {
        "mode": "classroom",
        "course": {
            "course_id": course.course_id,
            "code": course.code,
            "title": course.title,
            "total_concepts": len(course.concepts),
        },
        "user": {
            "user_id": user.user_id,
            "username": user.username,
            "display_name": user.display_name,
        },
        "room": {
            "room_code": room.room_code,
            "status": room.status,
            "required_students": room.required_students,
            "joined_count": len(members),
            "members": [
                {
                    "user_id": m.user_id,
                    "display_name": m.display_name,
                    "submitted_current_turn": m.user_id in submitted_ids,
                }
                for m in members
            ],
            "turn_id": room.turn_id,
            "phase": room.phase,
            "concept_id": concept_id,
            "concept_title": concept_title,
            "remediation_active": remediation_active,
            "submitted_count": len(submissions),
            "waiting_for": waiting_for,
            "current_user_submitted": user.user_id in submitted_ids,
        },
        "progress": progress,
        "messages": messages,
    }

    if turn_result is not None:
        payload["turn_result"] = {
            "status": turn_result.status,
            "reply": turn_result.reply,
            "waiting_for": turn_result.waiting_for,
            "class_decision": turn_result.class_decision,
            "moved_forward": turn_result.moved_forward,
            "student_assessments": turn_result.student_assessments,
        }

    return payload


class AdaptiveUniHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send_json(self, data: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _require_user(self) -> Optional[UserAccount]:
        token = self.headers.get("X-Session-Token", "").strip()
        user = class_memory.get_user_by_token(token)
        if user is None:
            self._send_json(
                {"error": "Please sign in first."}, HTTPStatus.UNAUTHORIZED
            )
            return None
        return user

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            index_file = WEB_DIR / "index.html"
            if not index_file.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "web/index.html not found")
                return
            html = index_file.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        # --- Solo GET ---
        if path == "/api/state":
            with SOLO_LOCK:
                existing = memory.get_course_state(course.course_id)
                msgs = memory.get_recent_messages(course.course_id, limit=1)
                if existing is None or not msgs:
                    solo_engine.start_or_resume()
                payload = build_solo_payload()
            self._send_json(payload)
            return

        # --- Auth & Classroom GET ---
        if path == "/api/auth/me":
            user = self._require_user()
            if user is None:
                return
            self._send_json({"user": asdict(user)})
            return


        if path == "/api/dashboard":
            user = self._require_user()
            if user is None:
                return
            prog = class_memory.get_student_progress_summary(user.user_id, course.course_id)
            rooms = class_memory.list_rooms_summary(user.user_id)
            self._send_json(
                {
                    "user": asdict(user),
                    "course": {
                        "course_id": course.course_id,
                        "code": course.code,
                        "title": course.title,
                        "description": course.description,
                        "total_concepts": len(course.concepts),
                    },
                    "progress": prog,
                    "rooms": rooms,
                }
            )
            return

        if path == "/api/rooms/state":
            user = self._require_user()
            if user is None:
                return
            room_code = (query.get("room_code") or [""])[0].strip()
            if not room_code:
                self._send_json({"error": "room_code is required"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                with get_room_lock(room_code):
                    class_engine.start_room_if_needed(room_code)
                    payload = build_room_payload(room_code, user)
                self._send_json(payload)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            # --- Solo POST ---
            if path == "/api/message":
                body = self._read_json()
                text = str(body.get("message", "")).strip()
                if not text:
                    self._send_json({"error": "Message cannot be empty."}, HTTPStatus.BAD_REQUEST)
                    return
                with SOLO_LOCK:
                    turn = solo_engine.handle_student_message(text)
                    payload = build_solo_payload(last_turn=turn)
                self._send_json(payload)
                return

            if path == "/api/resume":
                with SOLO_LOCK:
                    turn = solo_engine.start_or_resume()
                    payload = build_solo_payload(last_turn=turn)
                self._send_json(payload)
                return

            if path == "/api/reset":
                with SOLO_LOCK:
                    memory.reset_course(course.course_id)
                    turn = solo_engine.start_or_resume()
                    payload = build_solo_payload(last_turn=turn)
                self._send_json(payload)
                return

            if path == "/api/style":
                body = self._read_json()
                style = str(body.get("style", "")).strip()
                if style:
                    memory.update_learning_style(style)
                with SOLO_LOCK:
                    payload = build_solo_payload()
                self._send_json(payload)
                return

            # --- Auth POST ---
            if path == "/api/auth/signup":
                body = self._read_json()
                user, token = class_memory.signup(
                    username=str(body.get("username", "")),
                    display_name=str(body.get("display_name", "")),
                    pin=str(body.get("pin", "")),
                )
                self._send_json({"user": asdict(user), "token": token})
                return

            if path == "/api/auth/login":
                body = self._read_json()
                user, token = class_memory.login(
                    username=str(body.get("username", "")),
                    pin=str(body.get("pin", "")),
                )
                self._send_json({"user": asdict(user), "token": token})
                return

            # --- Classroom POST ---
            if path == "/api/rooms/join":
                user = self._require_user()
                if user is None:
                    return
                body = self._read_json()
                room_code = str(body.get("room_code", "")).strip()
                req_students = int(body.get("required_students", 2))
                first = course.get_first_concept()
                assert first is not None

                with get_room_lock(room_code):
                    room, _, _ = class_memory.create_or_join_room(
                        room_code=room_code,
                        course_id=course.course_id,
                        user_id=user.user_id,
                        first_topic_id=first.topic_id,
                        first_concept_id=first.id,
                        required_students=req_students,
                    )
                    if room.status == "ACTIVE":
                        class_engine.start_room_if_needed(room.room_code)
                    payload = build_room_payload(room.room_code, user)
                self._send_json(payload)
                return

            if path == "/api/rooms/message":
                user = self._require_user()
                if user is None:
                    return
                body = self._read_json()
                room_code = str(body.get("room_code", "")).strip()
                text = str(body.get("message", "")).strip()
                if not room_code or not text:
                    self._send_json(
                        {"error": "room_code and message are required."},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                with get_room_lock(room_code):
                    turn_res = class_engine.submit_student_message(room_code, user, text)
                    payload = build_room_payload(room_code, user, turn_result=turn_res)
                self._send_json(payload)
                return

            if path == "/api/rooms/force_continue":
                user = self._require_user()
                if user is None:
                    return
                body = self._read_json()
                room_code = str(body.get("room_code", "")).strip()
                with get_room_lock(room_code):
                    turn_res = class_engine.force_continue_turn(room_code)
                    payload = build_room_payload(room_code, user, turn_result=turn_res)
                self._send_json(payload)
                return

            if path == "/api/rooms/reset":
                user = self._require_user()
                if user is None:
                    return
                body = self._read_json()
                room_code = str(body.get("room_code", "")).strip()
                first = course.get_first_concept()
                assert first is not None
                with get_room_lock(room_code):
                    class_memory.reset_room(room_code, first.topic_id, first.id)
                    class_engine.start_room_if_needed(room_code)
                    payload = build_room_payload(room_code, user)
                self._send_json(payload)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def run() -> None:
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), AdaptiveUniHandler)
    mode = "OFFLINE MOCK" if config.USE_MOCK_AI else "LIVE AI"
    local_ip = detect_local_ip()

    print(f"=== Adaptive AI University Web Classroom ({mode}) ===")
    print(f"This phone   : http://127.0.0.1:{port}")
    if local_ip:
        print(f"2nd phone URL: http://{local_ip}:{port}  (same Wi-Fi / Hotspot)")
    print("Press Ctrl+C to stop the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped. All progress is saved in SQLite.")
        server.server_close()


if __name__ == "__main__":
    run()

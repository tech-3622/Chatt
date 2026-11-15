from __future__ import annotations

import json
from dataclasses import asdict
from http import HTTPStatus
from typing import Any, Callable, Dict, Iterable, List, Tuple
from urllib.parse import parse_qs

import web_server
from memory.classroom_memory import UserAccount

STATUS_TEXT = {
    HTTPStatus.OK: "200 OK",
    HTTPStatus.BAD_REQUEST: "400 Bad Request",
    HTTPStatus.UNAUTHORIZED: "401 Unauthorized",
    HTTPStatus.NOT_FOUND: "404 Not Found",
    HTTPStatus.INTERNAL_SERVER_ERROR: "500 Internal Server Error",
}


def _json_resp(
    start_response: Callable, data: Dict[str, Any], status: int = HTTPStatus.OK
) -> List[bytes]:
    raw = json.dumps(data).encode("utf-8")
    start_response(
        STATUS_TEXT.get(status, f"{status} OK"),
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(raw))),
        ],
    )
    return [raw]


def _read_body(environ: Dict[str, Any]) -> Dict[str, Any]:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    raw = environ["wsgi.input"].read(length).decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def _require_user(environ: Dict[str, Any]) -> UserAccount | None:
    token = environ.get("HTTP_X_SESSION_TOKEN", "").strip()
    return web_server.class_memory.get_user_by_token(token)


def application(
    environ: Dict[str, Any], start_response: Callable
) -> Iterable[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")
    query = parse_qs(environ.get("QUERY_STRING", ""))

    try:
        if method == "GET":
            if path in ("/", "/index.html"):
                index_file = web_server.WEB_DIR / "index.html"
                if not index_file.exists():
                    return _json_resp(
                        start_response, {"error": "index.html not found"}, HTTPStatus.NOT_FOUND
                    )
                html = index_file.read_bytes()
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Content-Length", str(len(html))),
                    ],
                )
                return [html]

            if path == "/api/state":
                with web_server.SOLO_LOCK:
                    existing = web_server.memory.get_course_state(
                        web_server.course.course_id
                    )
                    msgs = web_server.memory.get_recent_messages(
                        web_server.course.course_id, limit=1
                    )
                    if existing is None or not msgs:
                        web_server.solo_engine.start_or_resume()
                    payload = web_server.build_solo_payload()
                return _json_resp(start_response, payload)

            if path == "/api/auth/me":
                user = _require_user(environ)
                if user is None:
                    return _json_resp(
                        start_response,
                        {"error": "Please sign in first."},
                        HTTPStatus.UNAUTHORIZED,
                    )
                return _json_resp(start_response, {"user": asdict(user)})

            if path == "/api/dashboard":
                user = _require_user(environ)
                if user is None:
                    return _json_resp(
                        start_response,
                        {"error": "Please sign in first."},
                        HTTPStatus.UNAUTHORIZED,
                    )
                prog = web_server.class_memory.get_student_progress_summary(
                    user.user_id, web_server.course.course_id
                )
                rooms = web_server.class_memory.list_rooms_summary(user.user_id)
                return _json_resp(
                    start_response,
                    {
                        "user": asdict(user),
                        "course": {
                            "course_id": web_server.course.course_id,
                            "code": web_server.course.code,
                            "title": web_server.course.title,
                            "description": web_server.course.description,
                            "total_concepts": len(web_server.course.concepts),
                        },
                        "progress": prog,
                        "rooms": rooms,
                    },
                )

            if path == "/api/rooms/state":
                user = _require_user(environ)
                if user is None:
                    return _json_resp(
                        start_response,
                        {"error": "Please sign in first."},
                        HTTPStatus.UNAUTHORIZED,
                    )
                room_code = (query.get("room_code") or [""])[0].strip()
                if not room_code:
                    return _json_resp(
                        start_response,
                        {"error": "room_code is required"},
                        HTTPStatus.BAD_REQUEST,
                    )
                with web_server.get_room_lock(room_code):
                    web_server.class_engine.start_room_if_needed(room_code)
                    payload = web_server.build_room_payload(room_code, user)
                return _json_resp(start_response, payload)

            return _json_resp(
                start_response, {"error": "Not found"}, HTTPStatus.NOT_FOUND
            )

        if method == "POST":
            if path == "/api/message":
                body = _read_body(environ)
                text = str(body.get("message", "")).strip()
                if not text:
                    return _json_resp(
                        start_response,
                        {"error": "Message cannot be empty."},
                        HTTPStatus.BAD_REQUEST,
                    )
                with web_server.SOLO_LOCK:
                    turn = web_server.solo_engine.handle_student_message(text)
                    payload = web_server.build_solo_payload(last_turn=turn)
                return _json_resp(start_response, payload)

            if path == "/api/resume":
                with web_server.SOLO_LOCK:
                    turn = web_server.solo_engine.start_or_resume()
                    payload = web_server.build_solo_payload(last_turn=turn)
                return _json_resp(start_response, payload)

            if path == "/api/reset":
                with web_server.SOLO_LOCK:
                    web_server.memory.reset_course(web_server.course.course_id)
                    turn = web_server.solo_engine.start_or_resume()
                    payload = web_server.build_solo_payload(last_turn=turn)
                return _json_resp(start_response, payload)

            if path == "/api/style":
                body = _read_body(environ)
                style = str(body.get("style", "")).strip()
                if style:
                    web_server.memory.update_learning_style(style)
                with web_server.SOLO_LOCK:
                    payload = web_server.build_solo_payload()
                return _json_resp(start_response, payload)

            if path == "/api/auth/signup":
                body = _read_body(environ)
                user, token = web_server.class_memory.signup(
                    username=str(body.get("username", "")),
                    display_name=str(body.get("display_name", "")),
                    pin=str(body.get("pin", "")),
                )
                return _json_resp(
                    start_response, {"user": asdict(user), "token": token}
                )

            if path == "/api/auth/login":
                body = _read_body(environ)
                user, token = web_server.class_memory.login(
                    username=str(body.get("username", "")),
                    pin=str(body.get("pin", "")),
                )
                return _json_resp(
                    start_response, {"user": asdict(user), "token": token}
                )

            if path == "/api/rooms/join":
                user = _require_user(environ)
                if user is None:
                    return _json_resp(
                        start_response,
                        {"error": "Please sign in first."},
                        HTTPStatus.UNAUTHORIZED,
                    )
                body = _read_body(environ)
                room_code = str(body.get("room_code", "")).strip()
                req_students = int(body.get("required_students", 2))
                first = web_server.course.get_first_concept()
                assert first is not None
                with web_server.get_room_lock(room_code):
                    room, _, _ = web_server.class_memory.create_or_join_room(
                        room_code=room_code,
                        course_id=web_server.course.course_id,
                        user_id=user.user_id,
                        first_topic_id=first.topic_id,
                        first_concept_id=first.id,
                        required_students=req_students,
                    )
                    if room.status == "ACTIVE":
                        web_server.class_engine.start_room_if_needed(room.room_code)
                    payload = web_server.build_room_payload(room.room_code, user)
                return _json_resp(start_response, payload)

            if path == "/api/rooms/message":
                user = _require_user(environ)
                if user is None:
                    return _json_resp(
                        start_response,
                        {"error": "Please sign in first."},
                        HTTPStatus.UNAUTHORIZED,
                    )
                body = _read_body(environ)
                room_code = str(body.get("room_code", "")).strip()
                text = str(body.get("message", "")).strip()
                if not room_code or not text:
                    return _json_resp(
                        start_response,
                        {"error": "room_code and message are required."},
                        HTTPStatus.BAD_REQUEST,
                    )
                with web_server.get_room_lock(room_code):
                    turn_res = web_server.class_engine.submit_student_message(
                        room_code, user, text
                    )
                    payload = web_server.build_room_payload(
                        room_code, user, turn_result=turn_res
                    )
                return _json_resp(start_response, payload)

            if path == "/api/rooms/force_continue":
                user = _require_user(environ)
                if user is None:
                    return _json_resp(
                        start_response,
                        {"error": "Please sign in first."},
                        HTTPStatus.UNAUTHORIZED,
                    )
                body = _read_body(environ)
                room_code = str(body.get("room_code", "")).strip()
                with web_server.get_room_lock(room_code):
                    turn_res = web_server.class_engine.force_continue_turn(room_code)
                    payload = web_server.build_room_payload(
                        room_code, user, turn_result=turn_res
                    )
                return _json_resp(start_response, payload)

            if path == "/api/rooms/reset":
                user = _require_user(environ)
                if user is None:
                    return _json_resp(
                        start_response,
                        {"error": "Please sign in first."},
                        HTTPStatus.UNAUTHORIZED,
                    )
                body = _read_body(environ)
                room_code = str(body.get("room_code", "")).strip()
                first = web_server.course.get_first_concept()
                assert first is not None
                with web_server.get_room_lock(room_code):
                    web_server.class_memory.reset_room(
                        room_code, first.topic_id, first.id
                    )
                    web_server.class_engine.start_room_if_needed(room_code)
                    payload = web_server.build_room_payload(room_code, user)
                return _json_resp(start_response, payload)

            return _json_resp(
                start_response, {"error": "Not found"}, HTTPStatus.NOT_FOUND
            )

        return _json_resp(
            start_response, {"error": "Method not allowed"}, HTTPStatus.BAD_REQUEST
        )
    except ValueError as exc:
        return _json_resp(start_response, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
    except Exception as exc:
        return _json_resp(
            start_response, {"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR
        )

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import config


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS student_profile (
                student_id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT 'Student',
                preferred_learning_style TEXT NOT NULL DEFAULT 'everyday_examples',
                pace_notes TEXT NOT NULL DEFAULT 'steady, one small step at a time',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS course_state (
                course_id TEXT PRIMARY KEY,
                phase TEXT NOT NULL,
                current_topic_id TEXT NOT NULL DEFAULT '',
                current_concept_id TEXT NOT NULL DEFAULT '',
                concept_stack_json TEXT NOT NULL DEFAULT '[]',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_strategy TEXT NOT NULL DEFAULT '',
                last_explanation_summary TEXT NOT NULL DEFAULT '',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS concept_progress (
                course_id TEXT NOT NULL,
                concept_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_diagnosis TEXT NOT NULL DEFAULT '',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (course_id, concept_id)
            );

            CREATE TABLE IF NOT EXISTS explanation_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id TEXT NOT NULL,
                concept_id TEXT NOT NULL,
                strategy_used TEXT NOT NULL,
                explanation_summary TEXT NOT NULL,
                outcome TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS session_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                concept_id TEXT NOT NULL DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cross_course_foundations (
                foundation_key TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- --- Multi-Student Classroom Tables ---

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                pin_hash TEXT NOT NULL,
                preferred_learning_style TEXT NOT NULL DEFAULT 'everyday_examples',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS classrooms (
                room_code TEXT PRIMARY KEY COLLATE NOCASE,
                course_id TEXT NOT NULL,
                required_students INTEGER NOT NULL DEFAULT 2,
                status TEXT NOT NULL DEFAULT 'WAITING',
                phase TEXT NOT NULL DEFAULT 'GREETING',
                current_topic_id TEXT NOT NULL DEFAULT '',
                current_concept_id TEXT NOT NULL DEFAULT '',
                concept_stack_json TEXT NOT NULL DEFAULT '[]',
                turn_id INTEGER NOT NULL DEFAULT 1,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_strategy TEXT NOT NULL DEFAULT '',
                last_explanation_summary TEXT NOT NULL DEFAULT '',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS classroom_members (
                room_code TEXT NOT NULL COLLATE NOCASE,
                user_id INTEGER NOT NULL,
                joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (room_code, user_id),
                FOREIGN KEY (room_code) REFERENCES classrooms(room_code) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS classroom_submissions (
                room_code TEXT NOT NULL COLLATE NOCASE,
                turn_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (room_code, turn_id, user_id),
                FOREIGN KEY (room_code) REFERENCES classrooms(room_code) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS classroom_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_code TEXT NOT NULL COLLATE NOCASE,
                role TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                user_id INTEGER,
                content TEXT NOT NULL,
                concept_id TEXT NOT NULL DEFAULT '',
                turn_id INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (room_code) REFERENCES classrooms(room_code) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS student_concept_progress (
                user_id INTEGER NOT NULL,
                course_id TEXT NOT NULL,
                concept_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_diagnosis TEXT NOT NULL DEFAULT '',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, course_id, concept_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

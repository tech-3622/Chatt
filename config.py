from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
COURSES_DIR = BASE_DIR / "courses"
DB_PATH = DATA_DIR / "university.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# AI Provider Settings ("auto", "groq", "gemini", or "mock")
DEFAULT_PROVIDER = os.getenv("AI_PROVIDER", "auto")
USE_MOCK_AI = os.getenv("USE_MOCK_AI", "0") == "1"

# Groq Configuration (set GROQ_API_KEY in Termux)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_ASSESS_MODEL = os.getenv("GROQ_ASSESS_MODEL", "openai/gpt-oss-20b")
GROQ_TEACH_MODEL = os.getenv("GROQ_TEACH_MODEL", "openai/gpt-oss-120b")
GROQ_API_BASE = "https://api.groq.com/openai/v1"

# Gemini Configuration (kept modular for switching/fallback later)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_ASSESS_MODEL = os.getenv("GEMINI_ASSESS_MODEL", "gemini-3.5-flash-lite")
GEMINI_TEACH_MODEL = os.getenv("GEMINI_TEACH_MODEL", "gemini-3.8-flash")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Request reliability settings for mobile connections
AI_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "30"))
AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "2"))

DEFAULT_COURSE_ID = "sta111"

# Adaptive AI University — Project Handover & Engineering Guide

## 1. Core Vision
We are building an **Adaptive AI University**—NOT a normal Q&A chatbot, and NOT a scripted slideshow.
It is a private, patient, human-like AI university lecturer that teaches one tiny idea at a time and adapts in real time to student understanding.

### The Golden Rule
"Never move forward just because the lesson plan says so. Move forward only because the student understands."

### Non-Negotiable Teaching Rules
1. Teach ONLY ONE atomic micro-concept at a time.
2. Keep lecturer replies short, clear, and easy to read on a phone screen (2-3 short lines, around 35-55 words max, plain spoken text—NO markdown bold/headings or robotic labels like "Example:" / "Check question:").
3. Always give the student a chance to respond before advancing.
4. Understanding Engine controls progression—never pure step count.
5. If PARTLY_UNDERSTOOD or NOT_UNDERSTOOD, adapt using a NEW explanation/angle; SQLite tracks failed explanations so they are NEVER repeated.
6. If a prerequisite foundation is missing, pause the current concept, push the foundation onto the **Concept Stack** (`REMEDIATION`), teach that first until understood, and then pop back to the paused concept.
7. Recognize student learning intents (`wants_practice`, `wants_example`, `wants_restart`) without falsely marking them as `NOT_UNDERSTOOD` or skipping ahead.
8. In 2-Student Classroom Mode, obey **Leave No One Behind**: wait for both students (`2/2`), evaluate both together in 1 AI call, and only move forward when BOTH understand.

---

## 2. How We Develop Together (Workflow & Constraints)
- **Primary Dev Environment:** Android phone using **Termux** (`~/adaptive_uni`).
- **Editing Style:** Build cleanly in small, testable stages using `cat > filename << 'EOF'` commands. Avoid giant monolithic files or full-project rewrites when a surgical update works.
- **Zero Heavy Dependencies:** Pure Python 3 standard library only (`sqlite3`, `json`, `urllib.request`, `http.server`, `dataclasses`, `pathlib`, `hashlib`). No heavy C/Rust `pip` packages so Termux stays fast and error-free.
- **Security Hygiene:** NEVER hardcode API keys (`GROQ_API_KEY`, `GEMINI_API_KEY`) or GitHub tokens inside Python files, `config.py`, `HANDOVER.md`, or `.git/config` remote URLs. Use environment variables, PythonAnywhere's WSGI config, and `~/.git-credentials` (with `chmod 600`), and keep `data/*.db` and `.env` inside `.gitignore`.

---

## 3. Architecture & File Map

    adaptive_uni/
    ├── config.py                     # Paths, env-based API keys, model defaults
    ├── main.py                       # Termux CLI classroom (/progress, /debug, /reset)
    ├── web_server.py                 # Pure-Python HTTP server (Solo + Classroom APIs)
    ├── wsgi.py                       # WSGI entry point for PythonAnywhere
    ├── HANDOVER.md                   # This context & handover guide
    ├── README.md                     # Quick project overview
    ├── .gitignore                    # Excludes data/*.db, __pycache__, .env
    ├── core/
    │   ├── models.py                 # Dataclasses, LessonPhase, StudentIntent, StackFrame
    │   ├── understanding_engine.py   # Evaluates status + intent + missing foundations (JSON)
    │   ├── lecturer.py               # Generates warm, short, plain-text tutor replies
    │   ├── lecture_engine.py         # Solo pedagogical state machine + Foundation Stack
    │   └── classroom_engine.py       # 2-Student shared classroom state machine (wait 2/2)
    ├── router/
    │   ├── base.py                   # AIProvider abstract interface & RouteRequest
    │   ├── router.py                 # Task-aware routing (assess vs teach) + fallbacks
    │   └── providers/
    │       ├── groq_provider.py      # Pure-Python Groq REST adapter (active primary)
    │       ├── gemini_provider.py    # Pure-Python Gemini REST adapter (modular backup)
    │       └── mock_provider.py      # Offline simulator for Termux testing (USE_MOCK_AI=1)
    ├── memory/
    │   ├── database.py               # SQLite schema initializer (Solo + Multi-Student tables)
    │   ├── student_memory.py         # Solo state, failed explanations, foundations, profile
    │   └── classroom_memory.py       # Auth (users/sessions), rooms, turn submissions, progress
    ├── courses/
    │   ├── loader.py                 # Generic CourseBrain loader
    │   └── sta111/
    │       ├── course.json           # STA 111 persona, rules, opening, AI preferences
    │       └── curriculum.json       # STA 111 Topics 1-4 broken into 18 micro-concepts
    ├── web/
    │   └── index.html                # Mobile-first UI: Register/Login -> Dashboard -> Live Class
    └── data/
        └── university.db             # Local SQLite database (gitignored)

---

## 4. AI Router & Models
- **Primary Provider:** Groq (`https://api.groq.com/openai/v1/chat/completions`) — chosen because it is fast and allowlisted on PythonAnywhere's free tier.
  - `assess` task default: `openai/gpt-oss-20b`
  - `teach` task default: `openai/gpt-oss-120b`
- **Secondary Provider:** Gemini (`generativelanguage.googleapis.com/v1beta`) — modularly wired (`gemini-3.5-flash-lite` / `gemini-3.8-flash`) for future switching or paid hosting.
- **Offline Test Mode:** `USE_MOCK_AI=1 python3 ...` runs the entire state machine offline with zero API calls.

---

## 5. Multi-Student Classroom & Auth Design
- **Auth & Tabs:** Uses `sessionStorage` + `X-Session-Token` headers so 2 incognito tabs on a single phone (or 2 separate phones) act as 2 independent students without session collision.
- **Flow:** Register / Log In -> Student Dashboard (shows STA 111 progress, Solo button, Create/Join Room, and list of `WAITING` rooms) -> Waiting Lobby (`1/2`) -> Live Classroom (`2/2`).
- **Turn Sync:** Room increments `turn_id` only after the Lecturer replies. While `submitted_count < required_students`, the AI waits quietly without spending API calls (with an optional "Continue anyway" override).

---

## 6. Deployment & Git Workflow
- **GitHub Repo:** https://github.com/tech-3622/Chatt.git (branch: `main`)
- **Live Hosting:** https://Codeverseai.pythonanywhere.com (PythonAnywhere Manual Configuration WSGI)
- **PythonAnywhere Project Path:** `/home/Codeverseai/Chatt` (using `os.path.expanduser("~/Chatt")` in `/var/www/codeverseai_pythonanywhere_com_wsgi.py` with `GROQ_API_KEY` and `AI_PROVIDER=groq` set in env).

### Standard Update Loop
1. Edit & test in Termux (`~/adaptive_uni`):
       python3 web_server.py
2. Push changes to GitHub:
       git add .
       git commit -m "Describe your change"
       git push
3. Pull on PythonAnywhere Bash console & reload Web tab:
       cd ~/Chatt
       git pull
   Then click the green **Reload** button on the PythonAnywhere **Web** tab.

---

## 7. Current Status & Next Roadmap
### Completed
- Core Adaptive Lecture Engine + Understanding Engine + Foundation Stack
- SQLite persistence (progress, difficult concepts, failed explanation avoidance, learning style)
- First Course Brain: **STA 111 — Descriptive Statistics** (18 atomic micro-concepts across 4 topics)
- Modular AI Router (Groq, Gemini, Mock)
- Mobile Web UI with Register/Login, Student Dashboard, Solo Mode, and 2-Student Classroom Mode
- Live WSGI deployment on PythonAnywhere

### Planned Next Upgrades
1. End-to-end live testing of 2-Student Classroom Mode across incognito tabs / friends.
2. Per-user Solo Mode progress (tying Solo progress to logged-in `user_id` instead of shared `default_student`).
3. Topic Checkpoints (short 2-question review quizzes at the end of each Topic).
4. Post-lesson Self-Reflection Engine (automatically distilling what teaching styles work best for each student).
5. Expanding Course Brains: Programming, Mathematics, Discrete Structures, Digital Logic—all connected through the Central Personal Learning Profile.

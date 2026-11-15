from __future__ import annotations

import sys
import textwrap

import config
from core.lecture_engine import LectureEngine, TurnResult
from courses.loader import load_course
from memory.student_memory import StudentMemory
from router.router import AIRouter

# Clean ANSI colors for Termux
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
DIM = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_lecturer(text: str) -> None:
    print(f"\n{CYAN}{BOLD}Lecturer:{RESET}")
    for para in text.strip().split("\n"):
        if para.strip():
            print(textwrap.fill(para.strip(), width=76))
        else:
            print()


def print_debug(turn: TurnResult) -> None:
    if turn.assessment is None:
        print(f"{DIM}[Engine] Phase={turn.phase} | Concept={turn.concept_id}{RESET}")
        return
    a = turn.assessment
    print(
        f"{DIM}[Engine] Status={a.status} | Phase={turn.phase} | "
        f"Next={turn.concept_id} ({turn.concept_title})\n"
        f"         Diagnosis: {a.diagnosis}"
        + (f"\n         Missing Foundation: {a.missing_foundation}" if a.missing_foundation else "")
        + RESET
    )


def show_help() -> None:
    print(
        f"""
{YELLOW}Available Commands:{RESET}
  /progress        Show mastered & difficult concepts, foundations, and style
  /style [name]    View or change learning style (e.g. everyday_examples, analogies)
  /debug           Toggle Understanding Engine status display on/off
  /reset           Reset STA 111 progress and start from the beginning
  /help            Show this command list
  /quit            Exit (your progress is automatically saved in SQLite)
"""
    )


def show_progress(memory: StudentMemory, course_id: str) -> None:
    profile = memory.get_or_create_profile()
    state = memory.get_course_state(course_id)
    summary = memory.get_progress_summary(course_id)
    weak = memory.get_weak_foundations()

    print(f"\n{YELLOW}{BOLD}--- Personal Learning Progress ({course_id.upper()}) ---{RESET}")
    if state:
        print(f"Phase            : {state.phase}")
        print(f"Current Concept  : {state.current_concept_id} (topic: {state.current_topic_id})")
        if state.concept_stack:
            stack_names = " -> ".join(f.title for f in state.concept_stack)
            print(f"Foundation Stack : {stack_names}")
    print(f"Learning Style   : {profile.preferred_learning_style}")
    print(f"Mastered         : {', '.join(summary['mastered']) or 'None yet'}")
    print(f"In Progress      : {', '.join(summary['in_progress']) or 'None'}")
    print(f"Difficult        : {', '.join(summary['difficult']) or 'None'}")
    print(f"Weak Foundations : {', '.join(weak) or 'None'}")
    print()


def main() -> None:
    course_id = config.DEFAULT_COURSE_ID
    course = load_course(course_id)
    memory = StudentMemory()
    router = AIRouter()
    engine = LectureEngine(course, memory, router)

    debug_mode = True  # Useful while testing STA 111; toggle with /debug

    mode_label = "OFFLINE MOCK" if config.USE_MOCK_AI else "LIVE AI"
    print(f"{BOLD}{CYAN}=== Adaptive AI University — {course.code}: {course.title} ==={RESET}")
    print(f"{DIM}Mode: {mode_label} | Type /help for commands | Type /quit to pause & exit{RESET}")

    try:
        opening_turn = engine.start_or_resume()
        print_lecturer(opening_turn.reply)
        if debug_mode:
            print_debug(opening_turn)
    except Exception as exc:
        print(f"{YELLOW}Startup error: {exc}{RESET}")
        sys.exit(1)

    while True:
        try:
            user_input = input(f"\n{GREEN}{BOLD}You:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{DIM}Session paused and saved. See you next time!{RESET}")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()

            if cmd in ("/quit", "/exit"):
                print(f"{DIM}Session paused and saved. See you next time!{RESET}")
                break
            elif cmd == "/help":
                show_help()
            elif cmd == "/progress":
                show_progress(memory, course_id)
            elif cmd == "/debug":
                debug_mode = not debug_mode
                print(f"{DIM}Understanding Engine debug output: {'ON' if debug_mode else 'OFF'}{RESET}")
            elif cmd == "/style":
                if len(parts) == 2 and parts[1].strip():
                    new_style = parts[1].strip()
                    memory.update_learning_style(new_style)
                    print(f"{DIM}Preferred learning style updated to: {new_style}{RESET}")
                else:
                    prof = memory.get_or_create_profile()
                    print(f"{DIM}Current learning style: {prof.preferred_learning_style}{RESET}")
            elif cmd == "/reset":
                confirm = input(f"{YELLOW}Reset all STA 111 progress? (yes/no): {RESET}").strip().lower()
                if confirm in ("y", "yes"):
                    memory.reset_course(course_id)
                    print(f"{DIM}STA 111 reset. Starting a fresh lecture...{RESET}")
                    opening_turn = engine.start_or_resume()
                    print_lecturer(opening_turn.reply)
                    if debug_mode:
                        print_debug(opening_turn)
            else:
                print(f"{YELLOW}Unknown command. Type /help to see options.{RESET}")
            continue

        try:
            turn = engine.handle_student_message(user_input)
            print_lecturer(turn.reply)
            if debug_mode:
                print_debug(turn)
        except Exception as exc:
            print(f"\n{YELLOW}Connection/AI error: {exc}{RESET}")
            print(f"{DIM}Your progress is safe. You can try sending your answer again.{RESET}")


if __name__ == "__main__":
    main()

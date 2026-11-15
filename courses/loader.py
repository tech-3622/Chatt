from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from core.models import MicroConcept


@dataclass
class CourseBrain:
    course_id: str
    code: str
    title: str
    description: str
    lecturer_persona: str
    teaching_rules: List[str]
    opening: Dict[str, str]
    ai_preferences: Dict[str, Any]
    concepts: List[MicroConcept] = field(default_factory=list)

    def get_first_concept(self) -> Optional[MicroConcept]:
        return self.concepts[0] if self.concepts else None

    def get_concept(self, concept_id: str) -> Optional[MicroConcept]:
        for concept in self.concepts:
            if concept.id == concept_id:
                return concept
        return None

    def get_next_concept(self, current_concept_id: str) -> Optional[MicroConcept]:
        for idx, concept in enumerate(self.concepts):
            if concept.id == current_concept_id:
                if idx + 1 < len(self.concepts):
                    return self.concepts[idx + 1]
                return None
        return None

    def get_concept_index(self, concept_id: str) -> int:
        for idx, concept in enumerate(self.concepts):
            if concept.id == concept_id:
                return idx
        return -1


def load_course(course_id: str, courses_dir: Optional[Path] = None) -> CourseBrain:
    base = courses_dir or config.COURSES_DIR
    course_path = base / course_id

    meta_file = course_path / "course.json"
    curriculum_file = course_path / "curriculum.json"

    if not meta_file.exists():
        raise FileNotFoundError(f"Course metadata not found: {meta_file}")
    if not curriculum_file.exists():
        raise FileNotFoundError(f"Course curriculum not found: {curriculum_file}")

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    curriculum = json.loads(curriculum_file.read_text(encoding="utf-8"))

    concepts: List[MicroConcept] = []
    for topic in curriculum.get("topics", []):
        topic_id = topic["topic_id"]
        topic_title = topic["topic_title"]
        for item in topic.get("concepts", []):
            concepts.append(
                MicroConcept(
                    id=item["id"],
                    topic_id=topic_id,
                    topic_title=topic_title,
                    title=item["title"],
                    learning_goal=item["learning_goal"],
                    key_idea=item["key_idea"],
                    suggested_example=item["suggested_example"],
                    check_prompt=item["check_prompt"],
                    prerequisites=item.get("prerequisites", []),
                )
            )

    return CourseBrain(
        course_id=meta["course_id"],
        code=meta.get("code", meta["course_id"].upper()),
        title=meta["title"],
        description=meta.get("description", ""),
        lecturer_persona=meta.get("lecturer_persona", ""),
        teaching_rules=meta.get("teaching_rules", []),
        opening=meta.get("opening", {}),
        ai_preferences=meta.get("ai_preferences", {}),
        concepts=concepts,
    )

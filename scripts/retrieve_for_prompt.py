#!/usr/bin/env python3
"""CLI: retrieve learned skills for a task and print a prompt snippet.

For agents without hook support: call this before answering and inject
the printed snippet into the working context.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat2skill.config import DATA_HOME, base_user_id
from chat2skill.retrieval import SkillRetriever
from chat2skill.storage import init_db, load_skills, record_skill_usage


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieve learned Chat2Skill skills.")
    parser.add_argument("task", nargs="+", help="Current user task text.")
    parser.add_argument("--user-id", default=base_user_id())
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()

    task = " ".join(args.task)
    init_db()
    skills = load_skills(args.user_id, include_pending=False)
    retriever = SkillRetriever()
    retrieved = retriever.retrieve(task, skills, top_k=args.top_k, active_only=True)

    if not retrieved:
        print("No relevant Chat2Skill skills found.")
        return 0

    record_skill_usage(args.user_id, [item.skill.name for item in retrieved])

    print("## Chat2Skill Prompt Snippet")
    print(retriever.format_for_prompt(retrieved))
    print()
    print("## Skill Files")
    for item in retrieved:
        path = DATA_HOME / "skills" / args.user_id / item.skill.name / "SKILL.md"
        print(f"- {item.skill.name} score={item.score:.3f} path={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

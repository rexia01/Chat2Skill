#!/usr/bin/env python3
"""UserPromptSubmit hook: inject learned skills into the conversation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat2skill.config import base_user_id
from chat2skill.hookio import (
    json_hook_output,
    log_event,
    project_dir_from_input,
    project_user_id,
    prompt_from_input,
    read_hook_input,
)
from chat2skill.response_guard import reset_guard_state
from chat2skill.retrieval import SkillRetriever
from chat2skill.runner import PROJECT_SUMMARY_FILE, PROJECT_SUMMARY_NAME
from chat2skill.storage import SKILL_DIR, init_db, load_skills, record_skill_usage


def main() -> int:
    data = read_hook_input()
    prompt = prompt_from_input(data)
    project_dir = project_dir_from_input(data)
    scoped_user_id = project_user_id(project_dir)
    reset_guard_state(scoped_user_id)
    log_event(
        "UserPromptSubmit.start",
        project_dir=project_dir,
        user_id=scoped_user_id,
        prompt_preview=prompt[:160],
    )

    init_db()
    project_skill_path = SKILL_DIR / scoped_user_id / PROJECT_SUMMARY_FILE
    if project_skill_path.exists():
        project_skill = project_skill_path.read_text(encoding="utf-8").strip()
        if project_skill:
            # The summary is built from every active skill, so all of them
            # are in effect for this prompt — count usage for each so
            # maintenance does not archive the summary's own sources.
            record_skill_usage(
                scoped_user_id,
                [
                    s.name
                    for s in load_skills(scoped_user_id, include_pending=False)
                    if s.name != PROJECT_SUMMARY_NAME
                ],
            )
            json_hook_output(
                "Chat2Skill project summary skill. Apply this when relevant:\n\n"
                f"{project_skill}\n\n"
                f"Project skill namespace: {scoped_user_id}"
            )
            log_event(
                "UserPromptSubmit.done",
                project_dir=project_dir,
                user_id=scoped_user_id,
                retrieved=1,
                skills=[PROJECT_SUMMARY_FILE],
            )
            return 0

    default_user = base_user_id()
    skills = list(load_skills(scoped_user_id, include_pending=False))
    owners = {s.name: scoped_user_id for s in skills}
    if default_user != scoped_user_id:
        for skill in load_skills(default_user, include_pending=False):
            owners.setdefault(skill.name, default_user)
            skills.append(skill)

    retriever = SkillRetriever()
    retrieved = retriever.retrieve(prompt, skills, top_k=6, active_only=True)
    if not retrieved:
        log_event(
            "UserPromptSubmit.done",
            project_dir=project_dir,
            user_id=scoped_user_id,
            retrieved=0,
        )
        return 0

    by_owner: dict = {}
    for item in retrieved:
        owner = owners.get(item.skill.name, scoped_user_id)
        by_owner.setdefault(owner, []).append(item.skill.name)
    for owner, names in by_owner.items():
        record_skill_usage(owner, names)

    snippet = retriever.format_for_prompt(retrieved)
    json_hook_output(
        "Chat2Skill retrieved user/project skills. Apply these when relevant:\n\n"
        f"{snippet}\n\n"
        f"Project skill namespace: {scoped_user_id}"
    )
    log_event(
        "UserPromptSubmit.done",
        project_dir=project_dir,
        user_id=scoped_user_id,
        retrieved=len(retrieved),
        skills=[item.skill.name for item in retrieved],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

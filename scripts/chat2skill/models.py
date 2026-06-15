"""Data records shared between local storage and the cloud API.

These mirror the server-side schema. The extraction algorithm itself runs
on the server; the client only stores and retrieves these records.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class Skill:
    name: str
    description: str
    content: str
    version: int = 1
    source_sessions: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    skill_type: str = "preference"  # preference | procedure | planning | functional | atomic | mistake
    scope: str = "user"             # user | domain | global
    evidence_count: int = 0
    confidence: float = 0.0
    status: str = "draft"           # active | rejected | draft
    embedding_text: str = ""
    embedding_vector: List[float] = field(default_factory=list)
    embedding_model: str = ""
    replay_score: float = 0.0
    replay_cases: int = 0
    replay_wins: int = 0
    replay_losses: int = 0
    replay_rationale: str = ""
    language: str = "en"
    parent_skill: Optional[str] = None
    quality_notes: List[str] = field(default_factory=list)
    judge_rationale: str = ""
    memory_items: List[dict] = field(default_factory=list)
    response_guard: dict = field(default_factory=dict)

    def refresh_embedding_text(self) -> None:
        self.embedding_text = "\n".join(
            part for part in [self.name, self.description, self.content[:1200]] if part
        )

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Skill":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class MemoryItem:
    item_type: str  # success | failure_cause | failure_memory | constraint
    title: str
    description: str
    content: str
    evidence: str
    source_session: str
    confidence: float
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class UserModel:
    """Opaque per-user profile. The server evolves it; the client stores it."""

    user_id: str
    preferences: Dict[str, float] = field(default_factory=dict)
    constraints: Dict[str, dict] = field(default_factory=dict)
    interaction_count: int = 0
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "preferences": self.preferences,
            "constraints": self.constraints,
            "interaction_count": self.interaction_count,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UserModel":
        return cls(
            user_id=d["user_id"],
            preferences=d.get("preferences", {}),
            constraints=d.get("constraints", {}),
            interaction_count=d.get("interaction_count", 0),
            last_updated=d.get("last_updated", datetime.now().isoformat()),
        )

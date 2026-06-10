"""Skill Maintenance — AutoRefine-style score / prune / dedupe.

Stateless: callers load skills and usage counts from storage, this module
decides actions, callers persist them. Pruning archives (reversible)
rather than deletes; dedupe absorbs the weaker duplicate's metadata into
the stronger one without LLM rewriting.

Scoring inputs available today:
- utilization: retrieval hits recorded by the prompt hooks (skill_usage table)
- effectiveness: cross-time replay score (stands in for user feedback,
  which has no collection mechanism yet)
- recency: time since the skill was last reinforced
- overlap: similarity against other active skills of the same type
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple

from . import similarity
from .models import Skill


@dataclass
class SkillScore:
    skill_name: str
    utilization: float
    effectiveness: float
    recency: float
    overlap: float
    total: float


@dataclass
class MaintenanceReport:
    kept: List[str]
    pruned: List[str]                      # archived: old, unused, no positive signal
    merged: List[Tuple[str, str, float]]   # (loser, winner, similarity)
    scores: Dict[str, SkillScore]


class SkillMaintainer:
    """Decide prune/dedupe actions for one user's active skills."""

    PRUNE_THRESHOLD = 0.35
    MIN_PRUNE_AGE_DAYS = 14    # never archive a skill this fresh
    USAGE_TARGET = 5           # hits in the window for full utilization marks
    RECENCY_WINDOW_DAYS = 30
    MERGE_COSINE_THRESHOLD = similarity.MERGE_COSINE_THRESHOLD
    MERGE_LEXICAL_THRESHOLD = similarity.MERGE_LEXICAL_THRESHOLD

    def maintain(self, skills: List[Skill], usage_counts: Dict[str, int]) -> MaintenanceReport:
        active = [s for s in skills if s.status == "active"]
        merged = self._find_duplicates(active, usage_counts)
        losers = {loser for loser, _, _ in merged}
        survivors = [s for s in active if s.name not in losers]

        kept: List[str] = []
        pruned: List[str] = []
        scores: Dict[str, SkillScore] = {}
        for skill in survivors:
            score = self.score_skill(skill, usage_counts.get(skill.name, 0), survivors)
            scores[skill.name] = score
            if score.total < self.PRUNE_THRESHOLD and self._old_enough(skill):
                pruned.append(skill.name)
            else:
                kept.append(skill.name)

        return MaintenanceReport(kept=kept, pruned=pruned, merged=merged, scores=scores)

    def score_skill(self, skill: Skill, usage_count: int, all_skills: List[Skill]) -> SkillScore:
        utilization = min(usage_count / self.USAGE_TARGET, 1.0)
        # Replay stands in for user feedback until a feedback channel exists;
        # skills never replayed stay neutral.
        effectiveness = skill.replay_score if skill.replay_cases else 0.5
        recency = self._recency(skill)
        overlap = self._max_overlap(skill, all_skills)
        total = (
            utilization * 0.3
            + effectiveness * 0.4
            + recency * 0.2
            + (1.0 - overlap) * 0.1
        )
        return SkillScore(
            skill_name=skill.name,
            utilization=utilization,
            effectiveness=effectiveness,
            recency=recency,
            overlap=overlap,
            total=total,
        )

    def _find_duplicates(
        self, active: List[Skill], usage_counts: Dict[str, int]
    ) -> List[Tuple[str, str, float]]:
        """Pairs of near-duplicate same-type skills: archive the weaker."""
        merged: List[Tuple[str, str, float]] = []
        losers: set[str] = set()
        for i, left in enumerate(active):
            if left.name in losers:
                continue
            for right in active[i + 1:]:
                if right.name in losers or right.skill_type != left.skill_type:
                    continue
                cosine = self._cosine_similarity(left, right)
                lexical = self._lexical_similarity(left, right)
                if cosine < self.MERGE_COSINE_THRESHOLD and lexical < self.MERGE_LEXICAL_THRESHOLD:
                    continue
                pair_similarity = max(cosine, lexical)
                winner, loser = self._rank_pair(left, right, usage_counts)
                merged.append((loser.name, winner.name, pair_similarity))
                losers.add(loser.name)
                if loser is left:
                    break
        return merged

    def _rank_pair(
        self, left: Skill, right: Skill, usage_counts: Dict[str, int]
    ) -> Tuple[Skill, Skill]:
        def strength(skill: Skill) -> tuple:
            return (
                usage_counts.get(skill.name, 0),
                skill.replay_score if skill.replay_cases else 0.0,
                skill.evidence_count,
                skill.confidence,
                skill.version,
            )

        return (left, right) if strength(left) >= strength(right) else (right, left)

    def _old_enough(self, skill: Skill) -> bool:
        try:
            created = datetime.fromisoformat(skill.created_at)
        except (TypeError, ValueError):
            return True
        return (datetime.now() - created).days >= self.MIN_PRUNE_AGE_DAYS

    def _recency(self, skill: Skill) -> float:
        try:
            updated = datetime.fromisoformat(skill.updated_at)
        except (TypeError, ValueError):
            return 0.5
        days_old = (datetime.now() - updated).days
        return max(0.0, 1.0 - days_old / self.RECENCY_WINDOW_DAYS)

    def _max_overlap(self, skill: Skill, all_skills: List[Skill]) -> float:
        best = 0.0
        for other in all_skills:
            if other.name == skill.name or other.skill_type != skill.skill_type:
                continue
            best = max(best, self._similarity(skill, other))
        return best

    def _similarity(self, left: Skill, right: Skill) -> float:
        return max(self._cosine_similarity(left, right), self._lexical_similarity(left, right))

    def _cosine_similarity(self, left: Skill, right: Skill) -> float:
        if not left.embedding_vector or not right.embedding_vector:
            return 0.0
        return similarity.cosine(left.embedding_vector, right.embedding_vector)

    def _lexical_similarity(self, left: Skill, right: Skill) -> float:
        return similarity.jaccard(
            similarity.tokens(left.embedding_text or left.content),
            similarity.tokens(right.embedding_text or right.content),
        )




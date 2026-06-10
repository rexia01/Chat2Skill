"""Local storage. Runs on USER's machine.

Stores: conversations, skills, user profile.
"""
import json
import re
import sqlite3
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from .config import DATA_HOME
from .models import MemoryItem, Skill, UserModel
from .similarity import (
    MERGE_COSINE_THRESHOLD,
    MERGE_LEXICAL_THRESHOLD,
    cosine as _cosine,
    jaccard as _jaccard,
    tokens as _tokens,
)

DB_PATH = DATA_HOME / "chat2skill.db"
SKILL_DIR = DATA_HOME / "skills"


def init_db():
    """Initialize SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            session_id TEXT PRIMARY KEY,
            user_id TEXT,
            messages TEXT,
            feedback TEXT,
            timestamp TEXT
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            name TEXT PRIMARY KEY,
            description TEXT,
            content TEXT,
            version INTEGER,
            source_sessions TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS skill_records (
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            content TEXT,
            version INTEGER,
            source_sessions TEXT,
            created_at TEXT,
            updated_at TEXT,
            skill_type TEXT,
            scope TEXT,
            evidence_count INTEGER,
            confidence REAL,
            status TEXT,
            embedding_text TEXT,
            embedding_vector TEXT,
            embedding_model TEXT,
            replay_score REAL,
            replay_cases INTEGER,
            replay_wins INTEGER,
            replay_losses INTEGER,
            replay_rationale TEXT,
            language TEXT,
            parent_skill TEXT,
            quality_notes TEXT,
            judge_rationale TEXT,
            PRIMARY KEY (user_id, name)
        )
    """)

    _ensure_column(c, "skill_records", "embedding_vector", "TEXT")
    _ensure_column(c, "skill_records", "embedding_model", "TEXT")
    _ensure_column(c, "skill_records", "replay_score", "REAL")
    _ensure_column(c, "skill_records", "replay_cases", "INTEGER")
    _ensure_column(c, "skill_records", "replay_wins", "INTEGER")
    _ensure_column(c, "skill_records", "replay_losses", "INTEGER")
    _ensure_column(c, "skill_records", "replay_rationale", "TEXT")
    _ensure_column(c, "skill_records", "language", "TEXT")

    c.execute("""
        CREATE TABLE IF NOT EXISTS memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            skill_name TEXT,
            item_type TEXT,
            title TEXT,
            description TEXT,
            content TEXT,
            evidence TEXT,
            source_session TEXT,
            confidence REAL,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            profile_json TEXT,
            updated_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS skill_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            used_at TEXT NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_skill_usage_user ON skill_usage (user_id, used_at)")

    c.execute("""
        INSERT OR IGNORE INTO skill_records
        (user_id, name, description, content, version, source_sessions, created_at,
         updated_at, skill_type, scope, evidence_count, confidence, status,
         embedding_text, embedding_vector, embedding_model, replay_score,
         replay_cases, replay_wins, replay_losses, replay_rationale,
         language, parent_skill, quality_notes, judge_rationale)
        SELECT 'default', name, description, content, version, source_sessions,
               created_at, updated_at, 'preference', 'user', 0, 0.0,
               'active', name || char(10) || description, NULL, NULL,
               0.0, 0, 0, 0, NULL, 'en', NULL, '[]',
               'migrated from legacy skills table'
        FROM skills
    """)
    
    conn.commit()
    conn.close()


def _ensure_column(cursor, table: str, column: str, column_type: str):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise


def save_conversation(session_id: str, user_id: str, messages: list, feedback: Optional[dict] = None):
    """Save conversation to local DB."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO conversations VALUES (?, ?, ?, ?, ?)",
        (session_id, user_id, json.dumps(messages), json.dumps(feedback) if feedback else None, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def load_conversations(user_id: str, limit: int = 100) -> list:
    """Load recent conversations for a user."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "SELECT session_id, messages, feedback, timestamp FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return [
        {
            "session_id": r[0],
            "messages": json.loads(r[1]),
            "feedback": json.loads(r[2]) if r[2] else None,
            "timestamp": r[3]
        }
        for r in rows
    ]


def save_skill(skill: Skill, user_id: str = "default", embedding_client=None):
    """Save skill to local DB and filesystem."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    if not skill.embedding_text:
        skill.refresh_embedding_text()
    if embedding_client and not skill.embedding_vector and hasattr(embedding_client, "embed"):
        try:
            skill.embedding_vector = embedding_client.embed(skill.embedding_text)
            skill.embedding_model = getattr(
                embedding_client,
                "embedding_model",
                skill.embedding_model or "text-embedding-3-small",
            )
        except Exception as e:
            skill.quality_notes.append(f"embedding_failed:{type(e).__name__}")

    existing_skills = load_skills(user_id, include_pending=True) if DB_PATH.exists() else []
    same_name = next((existing for existing in existing_skills if existing.name == skill.name), None)
    if same_name:
        _merge_same_name(skill, same_name)
    else:
        merge_target = _find_merge_target(skill, existing_skills)
        if merge_target:
            _merge_into_existing(skill, merge_target)
    _sync_skill_content_metadata(skill)
    
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        INSERT OR REPLACE INTO skill_records
        (user_id, name, description, content, version, source_sessions, created_at,
         updated_at, skill_type, scope, evidence_count, confidence, status,
         embedding_text, embedding_vector, embedding_model, replay_score,
         replay_cases, replay_wins, replay_losses, replay_rationale,
         language, parent_skill, quality_notes, judge_rationale)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            skill.name,
            skill.description,
            skill.content,
            skill.version,
            json.dumps(skill.source_sessions),
            skill.created_at,
            datetime.now().isoformat(),
            skill.skill_type,
            skill.scope,
            skill.evidence_count,
            skill.confidence,
            skill.status,
            skill.embedding_text,
            json.dumps(skill.embedding_vector) if skill.embedding_vector else None,
            skill.embedding_model or None,
            skill.replay_score,
            skill.replay_cases,
            skill.replay_wins,
            skill.replay_losses,
            skill.replay_rationale,
            skill.language,
            skill.parent_skill,
            json.dumps(skill.quality_notes, ensure_ascii=False),
            skill.judge_rationale,
        ),
    )

    # Keep the legacy table populated for old local callers.
    c.execute(
        "INSERT OR REPLACE INTO skills VALUES (?, ?, ?, ?, ?, ?, ?)",
        (skill.name, skill.description, skill.content, skill.version,
         json.dumps(skill.source_sessions), skill.created_at, skill.updated_at)
    )
    conn.commit()
    conn.close()
    
    skill_dir = SKILL_DIR / user_id / skill.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill.content, encoding="utf-8")

    if skill.memory_items:
        _save_memory_dicts(skill.memory_items, user_id=user_id, skill_name=skill.name)


def _find_merge_target(candidate: Skill, existing_skills: List[Skill]) -> Optional[Skill]:
    best_skill = None
    best_score = 0.0
    candidate_tokens = _tokens(candidate.embedding_text or candidate.content)

    for existing in existing_skills:
        if existing.name == candidate.name:
            continue
        if existing.status != "active":
            continue
        if existing.skill_type != candidate.skill_type:
            continue

        vector_score = 0.0
        if candidate.embedding_vector and existing.embedding_vector:
            vector_score = _cosine(candidate.embedding_vector, existing.embedding_vector)
        existing_text = existing.embedding_text or existing.content
        lexical_score = _jaccard(candidate_tokens, _tokens(existing_text))
        if vector_score < MERGE_COSINE_THRESHOLD and lexical_score < MERGE_LEXICAL_THRESHOLD:
            continue

        score = max(vector_score, lexical_score)

        if score > best_score:
            best_score = score
            best_skill = existing

    return best_skill


def _merge_into_existing(candidate: Skill, existing: Skill) -> None:
    candidate.quality_notes.append(f"merged_into_existing:{existing.name}")
    candidate.name = existing.name
    _merge_existing_metadata(candidate, existing)


def _merge_same_name(candidate: Skill, existing: Skill) -> None:
    candidate.quality_notes.append(f"updated_existing:{existing.name}")
    _merge_existing_metadata(candidate, existing)


def _merge_existing_metadata(candidate: Skill, existing: Skill) -> None:
    if candidate.version <= existing.version:
        candidate.version = existing.version + 1
    candidate.created_at = existing.created_at
    candidate.parent_skill = existing.name
    has_new_sessions = any(
        session not in existing.source_sessions for session in candidate.source_sessions
    )
    candidate.source_sessions = sorted(set(existing.source_sessions + candidate.source_sessions))
    # Stop hooks re-process the same growing transcript, so a session can be
    # extracted many times. Only new sessions add evidence; a re-extraction
    # may refresh the count but never stack it.
    if has_new_sessions:
        candidate.evidence_count = existing.evidence_count + candidate.evidence_count
    else:
        candidate.evidence_count = max(existing.evidence_count, candidate.evidence_count)
    candidate.confidence = max(existing.confidence, candidate.confidence)
    candidate.status = "active"


def _sync_skill_content_metadata(skill: Skill) -> None:
    if not skill.content.startswith("---"):
        return
    replacements = {
        "name": skill.name,
        "description": json.dumps(skill.description, ensure_ascii=False),
        "version": str(skill.version),
        "created": skill.created_at,
    }
    content = skill.content
    for key, value in replacements.items():
        pattern = rf"(?m)^{key}:\s*.*$"
        line = f"{key}: {value}"
        if re.search(pattern, content):
            content = re.sub(pattern, line, content, count=1)
    skill.content = content








def save_memory_items(items: List[MemoryItem], user_id: str, skill_name: Optional[str] = None):
    """Persist extracted evidence items."""
    if not items:
        return
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.executemany(
        """
        INSERT INTO memory_items
        (user_id, skill_name, item_type, title, description, content, evidence,
         source_session, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                user_id,
                skill_name,
                item.item_type,
                item.title,
                item.description,
                item.content,
                item.evidence,
                item.source_session,
                item.confidence,
                item.created_at,
            )
            for item in items
        ],
    )
    conn.commit()
    conn.close()


def _save_memory_dicts(items: List[dict], user_id: str, skill_name: Optional[str] = None):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    # Re-extraction of the same session replaces its items instead of stacking.
    sessions = {item.get("source_session", "") for item in items if item.get("source_session")}
    for session in sessions:
        c.execute(
            "DELETE FROM memory_items WHERE user_id = ? AND skill_name IS ? AND source_session = ?",
            (user_id, skill_name, session),
        )
    c.executemany(
        """
        INSERT INTO memory_items
        (user_id, skill_name, item_type, title, description, content, evidence,
         source_session, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                user_id,
                skill_name,
                item.get("item_type", ""),
                item.get("title", ""),
                item.get("description", ""),
                item.get("content", ""),
                item.get("evidence", ""),
                item.get("source_session", ""),
                item.get("confidence", 0.0),
                item.get("created_at", datetime.now().isoformat()),
            )
            for item in items
        ],
    )
    conn.commit()
    conn.close()


def load_skills(user_id: Optional[str] = None, include_pending: bool = True) -> List[Skill]:
    """Load skills from local DB, optionally scoped to one user."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    where = []
    params = []
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
    if not include_pending:
        where.append("status = 'active'")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    c.execute(
        f"""
        SELECT name, description, content, version, source_sessions, created_at,
               updated_at, skill_type, scope, evidence_count, confidence, status,
               embedding_text, embedding_vector, embedding_model, replay_score,
               replay_cases, replay_wins, replay_losses, replay_rationale,
               language, parent_skill, quality_notes, judge_rationale
        FROM skill_records
        {where_sql}
        """,
        params,
    )
    rows = c.fetchall()
    conn.close()
    return [_skill_from_record(r) for r in rows]


def get_skill(name: str, user_id: str = "default") -> Optional[Skill]:
    """Get single skill by name."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        SELECT name, description, content, version, source_sessions, created_at,
               updated_at, skill_type, scope, evidence_count, confidence, status,
               embedding_text, embedding_vector, embedding_model, replay_score,
               replay_cases, replay_wins, replay_losses, replay_rationale,
               language, parent_skill, quality_notes, judge_rationale
        FROM skill_records WHERE user_id = ? AND name = ?
        """,
        (user_id, name),
    )
    row = c.fetchone()
    conn.close()
    if row:
        return _skill_from_record(row)
    return None


def record_skill_usage(user_id: str, skill_names: List[str]):
    """Log retrieval hits so maintenance can score utilization."""
    if not skill_names:
        return
    now = datetime.now().isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.executemany(
        "INSERT INTO skill_usage (user_id, skill_name, used_at) VALUES (?, ?, ?)",
        [(user_id, name, now) for name in skill_names],
    )
    conn.commit()
    conn.close()


def load_usage_counts(user_id: str, days: int = 30) -> dict:
    """Per-skill retrieval counts within the recent window."""
    from datetime import timedelta

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "SELECT skill_name, COUNT(*) FROM skill_usage WHERE user_id = ? AND used_at >= ? GROUP BY skill_name",
        (user_id, cutoff),
    )
    counts = dict(c.fetchall())
    conn.close()
    return counts


def set_skill_status(name: str, user_id: str, status: str, note: Optional[str] = None):
    """Change a skill's lifecycle status (e.g. archive during maintenance)."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if note:
        row = c.execute(
            "SELECT quality_notes FROM skill_records WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()
        notes = []
        if row and row[0]:
            try:
                notes = json.loads(row[0])
            except json.JSONDecodeError:
                notes = [row[0]]
        notes.append(note)
        c.execute(
            "UPDATE skill_records SET status = ?, quality_notes = ?, updated_at = ? WHERE user_id = ? AND name = ?",
            (status, json.dumps(notes, ensure_ascii=False), datetime.now().isoformat(), user_id, name),
        )
    else:
        c.execute(
            "UPDATE skill_records SET status = ?, updated_at = ? WHERE user_id = ? AND name = ?",
            (status, datetime.now().isoformat(), user_id, name),
        )
    conn.commit()
    conn.close()


def absorb_skill_sources(winner_name: str, loser_name: str, user_id: str):
    """Metadata-only merge: the winner inherits the loser's sessions.

    Evidence only accumulates when the loser brings sessions the winner
    has not already counted (same rule as _merge_existing_metadata).
    """
    winner = get_skill(winner_name, user_id)
    loser = get_skill(loser_name, user_id)
    if not winner or not loser:
        return
    new_sessions = [s for s in loser.source_sessions if s not in winner.source_sessions]
    merged_sessions = sorted(set(winner.source_sessions + loser.source_sessions))
    evidence = winner.evidence_count + (loser.evidence_count if new_sessions else 0)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "UPDATE skill_records SET source_sessions = ?, evidence_count = ?, updated_at = ? WHERE user_id = ? AND name = ?",
        (json.dumps(merged_sessions), evidence, datetime.now().isoformat(), user_id, winner_name),
    )
    conn.commit()
    conn.close()


def load_user_profile(user_id: str) -> UserModel:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT profile_json FROM user_profiles WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return UserModel.from_dict(json.loads(row[0]))
    return UserModel(user_id=user_id)


def save_user_profile(profile: UserModel):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO user_profiles VALUES (?, ?, ?)",
        (profile.user_id, json.dumps(profile.to_dict(), ensure_ascii=False), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def _skill_from_record(row) -> Skill:
    notes = []
    if row[22]:
        try:
            notes = json.loads(row[22])
        except json.JSONDecodeError:
            notes = [row[22]]
    embedding_vector = []
    if row[13]:
        try:
            embedding_vector = json.loads(row[13])
        except json.JSONDecodeError:
            embedding_vector = []
    skill = Skill(
        name=row[0],
        description=row[1] or "",
        content=row[2] or "",
        version=row[3] or 1,
        source_sessions=json.loads(row[4]) if row[4] else [],
        created_at=row[5] or datetime.now().isoformat(),
        updated_at=row[6] or datetime.now().isoformat(),
        skill_type=row[7] or "preference",
        scope=row[8] or "user",
        evidence_count=row[9] or 0,
        confidence=row[10] or 0.0,
        status=row[11] or "draft",
        embedding_text=row[12] or "",
        embedding_vector=embedding_vector,
        embedding_model=row[14] or "",
        replay_score=row[15] or 0.0,
        replay_cases=row[16] or 0,
        replay_wins=row[17] or 0,
        replay_losses=row[18] or 0,
        replay_rationale=row[19] or "",
        language=row[20] or "en",
        parent_skill=row[21],
        quality_notes=notes,
        judge_rationale=row[23] or "",
    )
    if not skill.embedding_text:
        skill.refresh_embedding_text()
    return skill

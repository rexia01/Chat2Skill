"""Local Chat2Skill admin server.

This server is intentionally local-only. It reads and edits the user's
~/.chat2skill/c2s.db and serves a lightweight React UI for memory and skill
management.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import socket
import sqlite3
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import runner, storage
from .config import load_config

ADMIN_STATIC_DIR = Path(__file__).with_name("admin_static")


class SkillPatch(BaseModel):
    description: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None
    skill_type: Optional[str] = None
    confidence: Optional[float] = None
    language: Optional[str] = None
    quality_note: Optional[str] = None


class MemoryPatch(BaseModel):
    content: Optional[str] = None
    memory_type: Optional[str] = None
    section: Optional[str] = None
    salience: Optional[float] = None
    confidence: Optional[float] = None
    is_active: Optional[bool] = None
    is_archived: Optional[bool] = None


class RebuildRequest(BaseModel):
    recent_messages: list[dict] = []


class ProjectSkillPatch(BaseModel):
    content: str


def create_app(token: str) -> FastAPI:
    app = FastAPI(title="Chat2Skill Admin", version="0.1")

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-chat2skill-admin-token") or request.query_params.get("token")
            if not secrets.compare_digest(str(supplied or ""), token):
                return JSONResponse({"detail": "invalid admin token"}, status_code=401)
        return await call_next(request)

    if ADMIN_STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(ADMIN_STATIC_DIR)), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index():
        index_path = ADMIN_STATIC_DIR / "index.html"
        if not index_path.exists():
            return HTMLResponse("<h1>Chat2Skill Admin assets not found</h1>", status_code=500)
        return FileResponse(index_path)

    @app.get("/api/health")
    def health():
        storage.init_db()
        return {
            "status": "ok",
            "db_path": str(storage.DB_PATH),
            "skill_dir": str(storage.SKILL_DIR),
        }

    @app.get("/api/projects")
    def projects():
        storage.init_db()
        return {"projects": _projects()}

    @app.post("/api/projects/{user_id:path}/archive")
    def archive_project(user_id: str):
        storage.init_db()
        if not _project_exists(user_id):
            raise HTTPException(status_code=404, detail="project not found")
        _set_project_status(user_id, "archived")
        return {"project": _project_by_user_id(user_id)}

    @app.post("/api/projects/{user_id:path}/restore")
    def restore_project(user_id: str):
        storage.init_db()
        if not _project_exists(user_id):
            raise HTTPException(status_code=404, detail="project not found")
        _set_project_status(user_id, "active")
        return {"project": _project_by_user_id(user_id)}

    @app.delete("/api/projects/{user_id:path}")
    def delete_project(user_id: str):
        storage.init_db()
        if not _project_exists(user_id):
            raise HTTPException(status_code=404, detail="project not found")
        if _project_status(user_id) != "archived":
            raise HTTPException(status_code=409, detail="archive project before deleting it")
        deleted = _delete_project(user_id)
        return {"deleted": deleted}

    @app.get("/api/projects/{user_id:path}/overview")
    def project_overview(user_id: str):
        storage.init_db()
        return _project_overview(user_id)

    @app.get("/api/projects/{user_id:path}/project-skill")
    def project_skill(user_id: str):
        storage.init_db()
        project = storage.load_project_skill(user_id)
        if not project:
            raise HTTPException(status_code=404, detail="project skill not found")
        version = project.get("version")
        sources = storage.load_project_skill_sources(
            user_id,
            int(version) if version is not None else None,
        )
        return {"project_skill": project, "sources": sources}

    @app.patch("/api/projects/{user_id:path}/project-skill")
    def update_project_skill(user_id: str, body: ProjectSkillPatch):
        storage.init_db()
        project = storage.load_project_skill(user_id)
        if not project:
            raise HTTPException(status_code=404, detail="project skill not found")
        content = body.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="project skill content cannot be empty")
        previous_version = project.get("version")
        previous_sources = storage.load_project_skill_sources(
            user_id,
            int(previous_version) if previous_version is not None else None,
        )
        file_path = _project_skill_file_path(user_id, project)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        storage.save_project_skill(
            user_id,
            content,
            file_path=file_path,
            source_skill_count=project.get("source_skill_count"),
            source_memory_count=project.get("source_memory_count"),
        )
        updated = storage.load_project_skill(user_id)
        sources = []
        if updated and updated.get("version") is not None and previous_sources:
            sources = [
                {
                    "skill_name": source.get("skill_name"),
                    "skill_type": source.get("skill_type"),
                    "confidence": source.get("confidence"),
                    "evidence_count": source.get("evidence_count"),
                    "source_memory_count": source.get("source_memory_count"),
                }
                for source in previous_sources
            ]
            storage.save_project_skill_sources(user_id, int(updated["version"]), sources)
            sources = storage.load_project_skill_sources(user_id, int(updated["version"]))
        return {"project_skill": updated, "sources": sources}

    @app.post("/api/projects/{user_id:path}/project-skill/rebuild")
    def rebuild_project_skill(user_id: str, body: RebuildRequest):
        config = load_config()
        try:
            path = runner.rebuild_project_skill(user_id, config, body.recent_messages)
        except Exception as exc:
            detail = _admin_error_detail(exc)
            status_code = 429 if _is_rate_limit_error(exc) else 500
            raise HTTPException(status_code=status_code, detail=detail) from exc
        project = storage.load_project_skill(user_id)
        return {
            "path": str(path) if path else None,
            "project_skill": project,
        }

    @app.get("/api/projects/{user_id:path}/skills")
    def skills(user_id: str, status: str = "all", q: str = ""):
        storage.init_db()
        return {"skills": _skills(user_id, status=status, query=q)}

    @app.get("/api/projects/{user_id:path}/skills/{skill_name:path}")
    def skill_detail(user_id: str, skill_name: str):
        storage.init_db()
        skill = storage.get_skill(skill_name, user_id)
        if not skill:
            raise HTTPException(status_code=404, detail="skill not found")
        return {
            "skill": skill.to_dict(),
            "memory_items": storage.load_skill_memory_items(user_id, [skill_name]).get(skill_name, []),
            "usage_count": storage.load_usage_counts(user_id).get(skill_name, 0),
        }

    @app.patch("/api/projects/{user_id:path}/skills/{skill_name:path}")
    def update_skill(user_id: str, skill_name: str, body: SkillPatch):
        storage.init_db()
        updated = _update_skill(user_id, skill_name, body)
        if not updated:
            raise HTTPException(status_code=404, detail="skill not found")
        return {"skill": updated}

    @app.delete("/api/projects/{user_id:path}/skills/{skill_name:path}")
    def delete_skill(user_id: str, skill_name: str):
        storage.init_db()
        deleted = _delete_skill(user_id, skill_name)
        if not deleted:
            raise HTTPException(status_code=404, detail="skill not found")
        return {"deleted": True}

    @app.get("/api/projects/{user_id:path}/memories")
    def memories(user_id: str, context_key: str = "project", status: str = "active", q: str = ""):
        storage.init_db()
        return {"memories": _memories(user_id, context_key=context_key, status=status, query=q)}

    @app.patch("/api/projects/{user_id:path}/memories/{context_key}/{memory_id:path}")
    def update_memory(user_id: str, context_key: str, memory_id: str, body: MemoryPatch):
        storage.init_db()
        memory = _update_memory(user_id, context_key, memory_id, body)
        if not memory:
            raise HTTPException(status_code=404, detail="memory not found")
        return {"memory": memory}

    @app.delete("/api/projects/{user_id:path}/memories/{context_key}/{memory_id:path}")
    def delete_memory(user_id: str, context_key: str, memory_id: str):
        storage.init_db()
        deleted = _delete_memory(user_id, context_key, memory_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="memory not found")
        return {"deleted": True}

    @app.get("/api/projects/{user_id:path}/materializations")
    def materializations(user_id: str, limit: int = 30):
        storage.init_db()
        return {"materializations": _materializations(user_id, limit)}

    return app


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(storage.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "http 429" in text or "rate limit" in text


def _admin_error_detail(exc: Exception) -> str:
    if _is_rate_limit_error(exc):
        return "Project skill rebuild is rate limited by api.chat2skill.com. Wait and try again later."
    return str(exc)


def _project_skill_file_path(user_id: str, project: dict) -> Path:
    file_path = project.get("file_path")
    if file_path:
        return Path(str(file_path))
    return storage.SKILL_DIR / user_id / runner.PROJECT_SKILL_FILE


def _project_exists(user_id: str) -> bool:
    conn = _connect()
    row = conn.execute(
        """
        SELECT 1 FROM project_skills WHERE user_id = ?
        UNION SELECT 1 FROM skill_records WHERE user_id = ?
        UNION SELECT 1 FROM memory_contexts WHERE user_id = ?
        LIMIT 1
        """,
        (user_id, user_id, user_id),
    ).fetchone()
    conn.close()
    return row is not None


def _project_status(user_id: str) -> str:
    conn = _connect()
    row = conn.execute(
        "SELECT status FROM project_admin_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return str(row["status"]) if row and row["status"] else "active"


def _set_project_status(user_id: str, status: str) -> None:
    now = datetime.now().isoformat()
    archived_at = now if status == "archived" else None
    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO project_admin_state
        (user_id, status, archived_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            status = excluded.status,
            archived_at = excluded.archived_at,
            updated_at = excluded.updated_at
        """,
        (user_id, status, archived_at, now, now),
    )
    conn.commit()
    conn.close()


def _project_by_user_id(user_id: str) -> Optional[dict]:
    for project in _projects():
        if project.get("user_id") == user_id:
            return project
    return None


def _delete_project(user_id: str) -> bool:
    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    tables = [
        "project_skills",
        "project_skill_sources",
        "skill_records",
        "skill_memory_items",
        "skill_usage",
        "memory_contexts",
        "memory_items",
        "memory_schemas",
        "memory_materializations",
        "memory_activity",
        "project_admin_state",
        "user_profiles",
        "conversations",
    ]
    deleted = False
    for table in tables:
        c.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        deleted = deleted or c.rowcount > 0
    conn.commit()
    conn.close()
    shutil.rmtree(storage.SKILL_DIR / user_id, ignore_errors=True)
    return deleted


def _projects() -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        """
        WITH users AS (
            SELECT user_id FROM project_skills
            UNION SELECT user_id FROM skill_records
            UNION SELECT user_id FROM memory_contexts
        )
        SELECT
            users.user_id,
            ps.language,
            ps.version AS project_skill_version,
            ps.updated_at AS project_skill_updated_at,
            ps.source_skill_count,
            ps.source_memory_count,
            COALESCE(sr.active_skills, 0) AS active_skills,
            COALESCE(sr.total_skills, 0) AS total_skills,
            COALESCE(mi.active_memories, 0) AS active_memories,
            COALESCE(mi.total_memories, 0) AS total_memories,
            sr.max_skill_updated_at,
            mi.max_memory_updated_at,
            mc.project_dir,
            mc.max_context_updated_at,
            COALESCE(pas.status, 'active') AS status,
            pas.archived_at
        FROM users
        LEFT JOIN project_skills ps ON ps.user_id = users.user_id
        LEFT JOIN project_admin_state pas ON pas.user_id = users.user_id
        LEFT JOIN (
            SELECT user_id,
                   SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_skills,
                   COUNT(*) AS total_skills,
                   MAX(updated_at) AS max_skill_updated_at
            FROM skill_records GROUP BY user_id
        ) sr ON sr.user_id = users.user_id
        LEFT JOIN (
            SELECT user_id,
                   SUM(CASE WHEN is_active = 1 AND is_archived = 0 THEN 1 ELSE 0 END) AS active_memories,
                   COUNT(*) AS total_memories,
                   MAX(updated_at) AS max_memory_updated_at
            FROM memory_items GROUP BY user_id
        ) mi ON mi.user_id = users.user_id
        LEFT JOIN (
            SELECT user_id,
                   MAX(project_dir) AS project_dir,
                   MAX(updated_at) AS max_context_updated_at
            FROM memory_contexts GROUP BY user_id
        ) mc ON mc.user_id = users.user_id
        ORDER BY COALESCE(ps.updated_at, users.user_id) DESC
        """
    ).fetchall()
    conn.close()
    projects = []
    for row in rows:
        item = dict(row)
        candidates = [
            item.get("project_skill_updated_at"),
            item.get("max_skill_updated_at"),
            item.get("max_memory_updated_at"),
            item.get("max_context_updated_at"),
        ]
        item["last_updated_at"] = max([value for value in candidates if value] or [""])
        projects.append(item)
    return sorted(projects, key=lambda item: (item.get("last_updated_at") or "", item.get("user_id") or ""), reverse=True)


def _project_overview(user_id: str) -> dict:
    conn = _connect()
    skill_status = conn.execute(
        "SELECT status, COUNT(*) AS count FROM skill_records WHERE user_id = ? GROUP BY status",
        (user_id,),
    ).fetchall()
    skill_types = conn.execute(
        "SELECT skill_type, COUNT(*) AS count FROM skill_records WHERE user_id = ? GROUP BY skill_type",
        (user_id,),
    ).fetchall()
    memory_types = conn.execute(
        "SELECT memory_type, COUNT(*) AS count FROM memory_items WHERE user_id = ? GROUP BY memory_type",
        (user_id,),
    ).fetchall()
    contexts = conn.execute(
        """
        SELECT context_key, project_dir, length(core_memory) AS core_memory_length, updated_at
        FROM memory_contexts
        WHERE user_id = ?
        ORDER BY updated_at DESC
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return {
        "user_id": user_id,
        "skill_status": [dict(row) for row in skill_status],
        "skill_types": [dict(row) for row in skill_types],
        "memory_types": [dict(row) for row in memory_types],
        "contexts": [dict(row) for row in contexts],
    }


def _skills(user_id: str, status: str, query: str) -> list[dict]:
    where = ["user_id = ?"]
    params: list = [user_id]
    if status != "all":
        where.append("status = ?")
        params.append(status)
    if query:
        where.append("(name LIKE ? OR description LIKE ? OR content LIKE ?)")
        params.extend([f"%{query}%"] * 3)
    conn = _connect()
    rows = conn.execute(
        f"""
        SELECT name, description, version, created_at, updated_at, skill_type,
               scope, evidence_count, confidence, status, replay_score,
               replay_cases, language, parent_skill
        FROM skill_records
        WHERE {' AND '.join(where)}
        ORDER BY updated_at DESC, evidence_count DESC, confidence DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _update_skill(user_id: str, skill_name: str, patch: SkillPatch) -> Optional[dict]:
    skill = storage.get_skill(skill_name, user_id)
    if not skill:
        return None
    data = patch.model_dump(exclude_unset=True)
    for key in ("description", "content", "status", "skill_type", "confidence", "language"):
        if key in data and data[key] is not None:
            setattr(skill, key, data[key])
    if data.get("quality_note"):
        skill.quality_notes.append(str(data["quality_note"]))
    skill.updated_at = datetime.now().isoformat()
    skill.refresh_embedding_text()

    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        UPDATE skill_records
        SET description = ?, content = ?, updated_at = ?, skill_type = ?,
            confidence = ?, status = ?, embedding_text = ?, language = ?,
            quality_notes = ?
        WHERE user_id = ? AND name = ?
        """,
        (
            skill.description,
            skill.content,
            skill.updated_at,
            skill.skill_type,
            skill.confidence,
            skill.status,
            skill.embedding_text,
            skill.language,
            json.dumps(skill.quality_notes, ensure_ascii=False),
            user_id,
            skill_name,
        ),
    )
    changed = c.rowcount
    conn.commit()
    conn.close()
    if changed:
        _write_skill_file(user_id, skill_name, skill.content)
        saved = storage.get_skill(skill_name, user_id)
        return saved.to_dict() if saved else None
    return None


def _delete_skill(user_id: str, skill_name: str) -> bool:
    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    c.execute("DELETE FROM skill_records WHERE user_id = ? AND name = ?", (user_id, skill_name))
    deleted = c.rowcount > 0
    c.execute("DELETE FROM skill_memory_items WHERE user_id = ? AND skill_name = ?", (user_id, skill_name))
    c.execute("DELETE FROM skill_usage WHERE user_id = ? AND skill_name = ?", (user_id, skill_name))
    c.execute("DELETE FROM project_skill_sources WHERE user_id = ? AND skill_name = ?", (user_id, skill_name))
    conn.commit()
    conn.close()
    if deleted:
        shutil.rmtree(storage.SKILL_DIR / user_id / skill_name, ignore_errors=True)
    return deleted


def _memories(user_id: str, context_key: str, status: str, query: str) -> list[dict]:
    where = ["mi.user_id = ?"]
    params: list = [user_id]
    if context_key != "all":
        where.append("mi.context_key = ?")
        params.append(context_key)
    if status == "active":
        where.append("mi.is_active = 1 AND mi.is_archived = 0")
    elif status == "archived":
        where.append("mi.is_archived = 1")
    if query:
        where.append("(mi.content LIKE ? OR mi.memory_type LIKE ? OR mi.section LIKE ?)")
        params.extend([f"%{query}%"] * 3)
    conn = _connect()
    rows = conn.execute(
        f"""
        SELECT mi.user_id, mi.context_key, mi.id, mi.content, mi.memory_type,
               mi.section, mi.salience, mi.confidence, mi.source_session,
               mi.source_agent, mi.recall_count, mi.hit_count, mi.miss_count,
               mi.is_active, mi.is_archived, mi.created_at, mi.updated_at,
               mc.project_dir
        FROM memory_items mi
        LEFT JOIN memory_contexts mc
          ON mc.user_id = mi.user_id AND mc.context_key = mi.context_key
        WHERE {' AND '.join(where)}
        ORDER BY mi.updated_at DESC, mi.salience DESC, mi.confidence DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return [_memory_dict(row) for row in rows]


def _update_memory(user_id: str, context_key: str, memory_id: str, patch: MemoryPatch) -> Optional[dict]:
    data = patch.model_dump(exclude_unset=True)
    allowed = {
        "content",
        "memory_type",
        "section",
        "salience",
        "confidence",
        "is_active",
        "is_archived",
    }
    if not any(key in data for key in allowed):
        return _get_memory(user_id, context_key, memory_id)
    assignments = []
    params = []
    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if key in {"is_active", "is_archived"}:
            value = 1 if value else 0
        assignments.append(f"{key} = ?")
        params.append(value)
    assignments.append("updated_at = ?")
    params.append(datetime.now().isoformat())
    params.extend([user_id, context_key, memory_id])
    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    c.execute(
        f"""
        UPDATE memory_items
        SET {', '.join(assignments)}
        WHERE user_id = ? AND context_key = ? AND id = ?
        """,
        params,
    )
    changed = c.rowcount
    conn.commit()
    conn.close()
    return _get_memory(user_id, context_key, memory_id) if changed else None


def _delete_memory(user_id: str, context_key: str, memory_id: str) -> bool:
    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    c.execute(
        "DELETE FROM memory_items WHERE user_id = ? AND context_key = ? AND id = ?",
        (user_id, context_key, memory_id),
    )
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def _get_memory(user_id: str, context_key: str, memory_id: str) -> Optional[dict]:
    conn = _connect()
    row = conn.execute(
        """
        SELECT mi.user_id, mi.context_key, mi.id, mi.content, mi.memory_type,
               mi.section, mi.salience, mi.confidence, mi.source_session,
               mi.source_agent, mi.recall_count, mi.hit_count, mi.miss_count,
               mi.is_active, mi.is_archived, mi.created_at, mi.updated_at,
               mc.project_dir
        FROM memory_items mi
        LEFT JOIN memory_contexts mc
          ON mc.user_id = mi.user_id AND mc.context_key = mi.context_key
        WHERE mi.user_id = ? AND mi.context_key = ? AND mi.id = ?
        """,
        (user_id, context_key, memory_id),
    ).fetchone()
    conn.close()
    return _memory_dict(row) if row else None


def _memory_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["is_active"] = bool(item.get("is_active"))
    item["is_archived"] = bool(item.get("is_archived"))
    return item


def _materializations(user_id: str, limit: int) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT context_key, materialization_id, memories_included, skills_included,
               query, rendered_prompt, token_count, outcome, created_at
        FROM memory_materializations
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, max(1, min(limit, 200))),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["memories_included"] = json.loads(item.get("memories_included") or "[]")
        except json.JSONDecodeError:
            item["memories_included"] = []
        try:
            item["skills_included"] = json.loads(item.get("skills_included") or "[]")
        except json.JSONDecodeError:
            item["skills_included"] = []
        return_value = item.get("outcome")
        if isinstance(return_value, str) and return_value.startswith("{"):
            try:
                item["outcome"] = json.loads(return_value)
            except json.JSONDecodeError:
                pass
        out.append(item)
    return out


def _write_skill_file(user_id: str, skill_name: str, content: str) -> None:
    skill_dir = storage.SKILL_DIR / user_id / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def run(host: str, port: int, open_browser: bool) -> None:
    import uvicorn

    if not _port_available(host, port):
        raise RuntimeError(
            f"127.0.0.1:{port} is already in use. Stop the existing Chat2Skill Admin "
            f"process or start with --port {port + 1}."
        )

    token = secrets.token_urlsafe(24)
    app = create_app(token)
    url = f"http://{host}:{port}/?token={quote(token)}"
    print(f"Chat2Skill Admin: {url}")
    print(f"Database: {storage.DB_PATH}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="info")


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Chat2Skill admin UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    try:
        run(args.host, args.port, not args.no_open)
    except RuntimeError as exc:
        print(f"Chat2Skill Admin failed: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

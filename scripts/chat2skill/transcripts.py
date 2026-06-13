"""Transcript parsing for supported coding agents.

Handles two JSONL layouts:
- Codex rollouts: {"type": "response_item", "payload": {"role", "content"}}
- Claude Code transcripts: {"type": "user"|"assistant", "message": {"role", "content"}}
- Cursor agent transcripts: {"role": "user"|"assistant", "message": {"content": ...}}

Noise (agent instructions, environment banners, system reminders) is
stripped before anything is sent to the cloud.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

NOISE_MARKERS = (
    "# AGENTS.md",
    "<INSTRUCTIONS>",
    "<environment_context>",
    "<permissions instructions>",
    "You are Codex, a coding agent",
    "## Skills",
    "Filesystem sandboxing defines",
    "Codex desktop context",
    "<system-reminder>",
    "<command-name>",
    "<local-command-stdout>",
    "Caveat: The messages below were generated",
)


def parse_transcript(path: Path, clean: bool = True) -> List[dict]:
    """Return [{"role", "content"}, ...] from any supported transcript."""
    messages: List[dict] = []
    if not path.exists():
        return messages
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = _message_from_record(record)
            if message is None:
                continue
            role, content = message
            if clean:
                content = clean_message_content(role, content)
            if content:
                messages.append({"role": role, "content": content})
    return messages


def _message_from_record(record: dict) -> Optional[tuple[str, str]]:
    record_type = record.get("type")

    if record_type == "response_item":  # Codex rollout
        payload = record.get("payload", {})
        role = payload.get("role")
        if role not in ("user", "assistant"):
            return None
        return role, _flatten_content(payload.get("content", ""))

    if record_type in ("user", "assistant"):  # Claude Code transcript
        payload = record.get("message", {})
        role = payload.get("role") or record_type
        if role not in ("user", "assistant"):
            return None
        return role, _flatten_content(payload.get("content", ""))

    role = record.get("role")  # Cursor agent transcript
    if role in ("user", "assistant"):
        payload = record.get("message", {})
        if isinstance(payload, dict):
            return role, _flatten_content(payload.get("content", ""))
        return role, _flatten_content(payload)

    return None


def _flatten_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
        return "\n".join(texts)
    return ""


def clean_message_content(role: str, content: str) -> str:
    """Remove agent/system noise before extraction."""
    if not isinstance(content, str):
        return ""
    text = content.strip()
    if not text:
        return ""
    if any(marker in text for marker in NOISE_MARKERS):
        return ""
    if role == "assistant" and text.startswith("Chunk ID:"):
        return ""
    return text


def find_latest_session(project_dir: str = "") -> Optional[Path]:
    """Fallback when the hook input carries no transcript path."""
    candidates: List[Path] = []
    cursor_root = Path.home() / ".cursor" / "projects"
    cursor_workspace = _cursor_workspace_transcripts(cursor_root, project_dir)
    roots = [
        Path.home() / ".codex" / "sessions",
        Path.home() / ".claude" / "projects",
    ]
    if cursor_workspace is not None:
        roots.insert(0, cursor_workspace)
    elif cursor_root.exists():
        roots.append(cursor_root)
    for root in roots:
        if root.exists():
            candidates.extend(p for p in root.rglob("*.jsonl") if p.is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _cursor_workspace_transcripts(cursor_root: Path, project_dir: str) -> Optional[Path]:
    if not project_dir or not cursor_root.exists():
        return None
    try:
        raw = str(Path(project_dir).expanduser().resolve())
    except OSError:
        raw = str(Path(project_dir).expanduser())
    slug = raw.strip("/").replace("/", "-")
    transcript_dir = cursor_root / slug / "agent-transcripts"
    return transcript_dir if transcript_dir.exists() else None

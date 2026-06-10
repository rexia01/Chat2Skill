"""User-level configuration.

Everything lives under the data home (default ~/.chat2skill, overridable
with CHAT2SKILL_HOME). The LLM api key belongs to the user (BYOK); it is
sent to the Chat2Skill cloud only to run this user's own extraction calls.
"""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
from typing import Optional

DATA_HOME = Path(os.environ.get("CHAT2SKILL_HOME") or Path.home() / ".chat2skill")
CONFIG_PATH = DATA_HOME / "config.json"

DEFAULT_API_URL = "https://api.chat2skill.dev"


def load_config() -> dict:
    config: dict = {}
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            config = {}

    # Environment variables override the file.
    config.setdefault("api_url", DEFAULT_API_URL)
    if os.environ.get("CHAT2SKILL_API_URL"):
        config["api_url"] = os.environ["CHAT2SKILL_API_URL"]

    llm = dict(config.get("llm") or {})
    if os.environ.get("OPENAI_API_KEY") and not llm.get("api_key"):
        llm["api_key"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("OPENAI_BASE_URL") and not llm.get("base_url"):
        llm["base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.environ.get("CHAT2SKILL_MODEL"):
        llm["model"] = os.environ["CHAT2SKILL_MODEL"]
    llm.setdefault("model", "gpt-4.1")
    config["llm"] = llm

    if os.environ.get("CHAT2SKILL_USER_ID"):
        config["user_id"] = os.environ["CHAT2SKILL_USER_ID"]
    return config


def llm_payload(config: dict) -> Optional[dict]:
    """LLM block for API requests, or None to use server-side heuristics."""
    llm = config.get("llm") or {}
    if not llm.get("api_key"):
        return None
    return {
        "api_key": llm["api_key"],
        "base_url": llm.get("base_url"),
        "model": llm.get("model", "gpt-4.1"),
        "embedding_model": llm.get("embedding_model"),
    }


def base_user_id(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return config.get("user_id") or _safe_username() or "default"


def _safe_username() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""

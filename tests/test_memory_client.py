from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from chat2skill import memory_client
from chat2skill.context_store import apply_memory_result, load_context, save_context
from chat2skill.models import Skill


def _config() -> dict:
    return {
        "backend": "memory",
        "api_url": "https://api.example.test",
        "memory": {
            "target_model": "generic",
            "token_budget": 4000,
            "agent_id": "chat2skill-test",
        },
    }


class MemoryClientTests(unittest.TestCase):
    def test_materialize_uses_local_context_and_records_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            context_dir = Path(tmp) / "contexts"
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"

            with patch("chat2skill.context_store.CONTEXTS_DIR", context_dir):
                with patch.object(memory_client.storage, "DB_PATH", db_path):
                    with patch.object(memory_client.storage, "SKILL_DIR", skill_dir):
                        memory_client.storage.init_db()
                        save_context(
                            "/repo/project",
                            "user-1",
                            {
                                "core_memory": "Project deploys on EC2.",
                                "memories": [
                                    {
                                        "id": "b1",
                                        "content": "EC2 deploy uses the durable rollout path.",
                                        "memory_type": "procedure",
                                        "section": "deployment",
                                        "confidence": 0.9,
                                        "salience": 0.9,
                                    }
                                ],
                                "schemas": [],
                                "recent_raw_hashes": [],
                            },
                        )
                        memory_client.storage.save_skill(
                            Skill(
                                name="ec2-deploy-check",
                                description="Use the EC2 deploy checklist.",
                                content="Check EC2 rollout before deploy.",
                                skill_type="procedure",
                                status="active",
                                confidence=0.8,
                            ),
                            user_id="user-1",
                        )
                        with patch.object(memory_client.api_client, "unified_retrieve") as cloud_retrieve:
                            result = memory_client.materialize_for_prompt(
                                _config(), "/repo/project", "current EC2 deploy task", "user-1"
                            )
                            cloud_retrieve.assert_not_called()
                        context = load_context("/repo/project", "user-1")

            self.assertIn("Project deploys on EC2.", result["rendered_text"])
            self.assertIn("ec2-deploy-check", result["rendered_text"])
            self.assertEqual(context["last_materialization"]["materialization_id"], result["materialization_id"])
            self.assertEqual(context["last_materialization"]["memories_included"], ["b1"])
            self.assertTrue(db_path.exists())
            self.assertFalse(list(context_dir.rglob("*.json")))

    def test_commit_transcript_calls_unified_learn_and_applies_memory_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            context_dir = Path(tmp) / "contexts"
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "response_item",
                                "payload": {"role": "user", "content": "remember EC2 deploy"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "payload": {"role": "assistant", "content": "done"},
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_learn(api_url, payload):
                calls.append((api_url, payload))
                self.assertEqual(api_url, "https://api.example.test")
                self.assertIn("existing_memory", payload)
                self.assertIn("existing_skills", payload)
                return {
                    "session_id": "session",
                    "llm_used": False,
                    "memory": {
                        "delta_batch": {
                            "id": "delta-1",
                            "trigger": "commit",
                            "operations": [
                                {
                                    "op_type": "add_memory",
                                    "target_id": "b1",
                                    "section": "deployment",
                                    "content": "EC2 deploy is durable memory.",
                                    "memory_type": "fact",
                                    "confidence": 0.8,
                                    "previous_state": {},
                                }
                            ],
                        },
                        "core_memory_update": "Project uses EC2 deploy.",
                        "raw_input_hash": "hash-1",
                        "memories_added": 1,
                    },
                    "skills": {
                        "skill": None,
                        "updated_profile": {
                            "user_id": "user-1",
                            "preferences": {},
                            "constraints": {},
                            "interaction_count": 0,
                            "last_updated": "now",
                        },
                        "reason": "no_actionable_signals",
                    },
                }

            with patch("chat2skill.context_store.CONTEXTS_DIR", context_dir):
                with patch.object(memory_client.storage, "DB_PATH", db_path):
                    with patch.object(memory_client.storage, "SKILL_DIR", skill_dir):
                        memory_client.storage.init_db()
                        save_context(
                            "/repo/project",
                            "user-1",
                            {
                                "core_memory": "Project uses EC2 deploy.",
                                "memories": [
                                    {
                                        "id": "m1",
                                        "content": "EC2 deploy must keep payloads compact.",
                                        "memory_type": "decision",
                                        "section": "deployment",
                                        "confidence": 0.9,
                                        "salience": 0.9,
                                    }
                                ],
                                "schemas": [],
                                "recent_raw_hashes": [],
                            },
                        )
                        memory_client.storage.save_skill(
                            Skill(
                                name="ec2-deploy-payloads",
                                description="Keep EC2 deploy payloads compact.",
                                content="x" * 5000,
                                skill_type="procedure",
                                status="active",
                                confidence=0.9,
                            ),
                            user_id="user-1",
                        )
                        with patch.object(memory_client.api_client, "unified_learn", side_effect=fake_learn):
                            result = memory_client.commit_transcript(
                                transcript, "user-1", _config(), project_dir="/repo/project"
                            )
                        context = load_context("/repo/project", "user-1")

            self.assertEqual(result["status"], "memory_saved")
            self.assertEqual(result["memory"]["memories_added"], 1)
            self.assertEqual(result["memory"]["context_path"], str(db_path))
            self.assertEqual(context["core_memory"], "Project uses EC2 deploy.")
            self.assertIn("b1", {item["id"] for item in context["memories"]})
            self.assertEqual(context["recent_raw_hashes"], ["hash-1"])
            self.assertEqual(calls[0][1]["messages"][0]["content"], "remember EC2 deploy")
            self.assertEqual([item["id"] for item in calls[0][1]["existing_memory"]["memories"]], ["m1"])
            self.assertEqual(len(calls[0][1]["existing_skills"]), 1)
            self.assertLess(len(calls[0][1]["existing_skills"][0]["content"]), 2500)
            self.assertEqual(calls[0][1]["existing_skills"][0]["embedding_vector"], [])
            self.assertTrue(db_path.exists())
            self.assertFalse(list(context_dir.rglob("*.json")))
            conn = sqlite3.connect(str(db_path))
            activity_count = conn.execute("SELECT COUNT(*) FROM c2s_memory_activity").fetchone()[0]
            conn.close()
            self.assertEqual(activity_count, 1)

    def test_apply_memory_result_updates_existing_memory(self):
        context = {
            "core_memory": "",
            "memories": [{"id": "b1", "content": "old", "confidence": 0.3}],
            "schemas": [],
            "recent_raw_hashes": [],
        }
        updated = apply_memory_result(
            context,
            {
                "delta_batch": {
                    "operations": [
                        {
                            "op_type": "update_memory",
                            "target_id": "b1",
                            "content": "new",
                            "confidence": 0.9,
                        }
                    ]
                }
            },
        )
        self.assertEqual(updated["memories"][0]["content"], "new")
        self.assertEqual(updated["memories"][0]["confidence"], 0.9)


if __name__ == "__main__":
    unittest.main()

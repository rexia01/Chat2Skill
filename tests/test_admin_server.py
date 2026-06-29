import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fastapi.testclient import TestClient

from chat2skill import admin_server, storage
from chat2skill.models import Skill


class AdminServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "c2s.db"
        self.skill_dir = Path(self.tmp.name) / "skills"
        self.db_patch = patch.object(storage, "DB_PATH", self.db_path)
        self.skill_patch = patch.object(storage, "SKILL_DIR", self.skill_dir)
        self.db_patch.start()
        self.skill_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.skill_patch.stop)
        storage.init_db()
        storage.save_skill(
            Skill(
                name="plan-before-action",
                description="先给计划再执行。",
                content="---\nname: plan-before-action\n---\n\n## 使用场景\n- 修改前",
                status="active",
                skill_type="planning",
                confidence=0.9,
                evidence_count=3,
                language="zh-Hans",
            ),
            user_id="u1",
        )
        storage.save_project_skill(
            "u1",
            "---\nname: project-skill\nlanguage: zh-Hans\n---\n\n## 何时应用\n- 测试",
            source_skill_count=1,
            source_memory_count=1,
        )
        storage.save_project_skill_sources(
            "u1",
            1,
            [
                {
                    "skill_name": "plan-before-action",
                    "skill_type": "planning",
                    "confidence": 0.9,
                    "evidence_count": 3,
                    "source_memory_count": 0,
                }
            ],
        )
        storage.save_project_memory_context(
            "u1",
            "project",
            {
                "project_dir": "/repo/demo",
                "core_memory": "",
                "recent_raw_hashes": [],
                "memories": [
                    {
                        "id": "m1",
                        "content": "项目使用本地管理页查看记忆。",
                        "memory_type": "fact",
                        "section": "project",
                        "salience": 0.8,
                        "confidence": 0.9,
                    }
                ],
                "schemas": [],
            },
        )
        storage.save_project_memory_materialization(
            "u1",
            "project",
            {
                "materialization_id": "mat-1",
                "memories_included": ["m1"],
                "skills_included": ["plan-before-action"],
                "query": "怎么查看本地记忆",
                "rendered_prompt": "## Chat2Skill Memory and Skills\n\n## Relevant Project Memories\n- [fact/project] 项目使用本地管理页查看记忆。",
                "token_count": 31,
            },
        )
        self.client = TestClient(admin_server.create_app("test-token"))
        self.headers = {"X-Chat2Skill-Admin-Token": "test-token"}

    def test_rejects_missing_token(self):
        response = self.client.get("/api/projects")
        self.assertEqual(response.status_code, 401)

    def test_lists_projects(self):
        response = self.client.get("/api/projects", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        projects = response.json()["projects"]
        project = next(item for item in projects if item["user_id"] == "u1")
        self.assertEqual(project["active_skills"], 1)
        self.assertEqual(project["active_memories"], 1)
        self.assertEqual(project["status"], "active")
        self.assertTrue(project["last_updated_at"])

    def test_archives_and_deletes_project(self):
        delete_before_archive = self.client.delete("/api/projects/u1", headers=self.headers)
        self.assertEqual(delete_before_archive.status_code, 409)

        archive = self.client.post("/api/projects/u1/archive", headers=self.headers)
        self.assertEqual(archive.status_code, 200)
        self.assertEqual(archive.json()["project"]["status"], "archived")

        delete = self.client.delete("/api/projects/u1", headers=self.headers)
        self.assertEqual(delete.status_code, 200)
        self.assertTrue(delete.json()["deleted"])

        response = self.client.get("/api/projects", headers=self.headers)
        self.assertFalse(any(item["user_id"] == "u1" for item in response.json()["projects"]))
        self.assertEqual(self.client.get("/api/projects/u1/project-skill", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get("/api/projects/u1/skills", headers=self.headers).json()["skills"], [])
        self.assertEqual(self.client.get("/api/projects/u1/memories?context_key=all", headers=self.headers).json()["memories"], [])

    def test_updates_skill_status(self):
        response = self.client.patch(
            "/api/projects/u1/skills/plan-before-action",
            headers=self.headers,
            json={"status": "archived", "quality_note": "admin archived"},
        )
        self.assertEqual(response.status_code, 200)
        skill = response.json()["skill"]
        self.assertEqual(skill["status"], "archived")
        self.assertIn("admin archived", skill["quality_notes"])

    def test_archives_memory(self):
        response = self.client.patch(
            "/api/projects/u1/memories/project/m1",
            headers=self.headers,
            json={"is_active": False, "is_archived": True},
        )
        self.assertEqual(response.status_code, 200)
        memory = response.json()["memory"]
        self.assertFalse(memory["is_active"])
        self.assertTrue(memory["is_archived"])

    def test_reads_project_skill(self):
        response = self.client.get("/api/projects/u1/project-skill", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("何时应用", response.json()["project_skill"]["content"])

    def test_updates_project_skill_content(self):
        next_content = "---\nname: project-skill\nlanguage: zh-Hans\n---\n\n## 何时应用\n- 已编辑"
        response = self.client.patch(
            "/api/projects/u1/project-skill",
            headers=self.headers,
            json={"content": next_content},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["project_skill"]["version"], 2)
        self.assertIn("已编辑", body["project_skill"]["content"])
        self.assertEqual(body["sources"][0]["skill_name"], "plan-before-action")
        written = self.skill_dir / "u1" / "PROJECT_SKILL.md"
        self.assertEqual(written.read_text(encoding="utf-8"), next_content)

    def test_rebuild_project_skill_maps_rate_limit(self):
        with patch.object(
            admin_server.runner,
            "rebuild_project_skill",
            side_effect=Exception("project skill generation failed: ApiError: HTTP 429 rate limit exceeded"),
        ):
            response = self.client.post(
                "/api/projects/u1/project-skill/rebuild",
                headers=self.headers,
                json={"recent_messages": []},
            )
        self.assertEqual(response.status_code, 429)
        self.assertIn("rate limited", response.json()["detail"])

    def test_reads_prompt_materializations(self):
        response = self.client.get("/api/projects/u1/materializations", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        records = response.json()["materializations"]
        self.assertEqual(records[0]["materialization_id"], "mat-1")
        self.assertEqual(records[0]["memories_included"], ["m1"])
        self.assertEqual(records[0]["skills_included"], ["plan-before-action"])
        self.assertIn("Relevant Project Memories", records[0]["rendered_prompt"])
        self.assertEqual(records[0]["token_count"], 31)


if __name__ == "__main__":
    unittest.main()

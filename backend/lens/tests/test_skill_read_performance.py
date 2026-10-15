"""Keep Skill selector reads small while preserving full editable definitions."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from lens.models import Skill


class SkillReadPerformanceTests(TestCase):
    """Verify explicit compact reads and legacy full reads through public routes."""

    def setUp(self):
        """Create a definition with content that list consumers never display."""

        self.client = APIClient()
        self.client.force_authenticate(get_user_model().objects.create_user(username="skill-reader", is_staff=True))
        self.skill = Skill.objects.create(
            name="Large skill",
            definition={
                "description": "Short description",
                "description_zh": "简要说明",
                "environment": [{"name": "EXAMPLE", "required": True}],
                "content": "body" * 20000,
                "api": {"policy": "original"},
            },
            package_manifest={"file_count": 2, "files": [{"path": "SKILL.md"}, {"path": "script.py"}]},
        )

    def test_compact_list_projects_summary_and_detail_remains_editable(self):
        """Compact reads omit bodies in SQL and JSON without losing edit data."""

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/lens/admin/skills/", {"compact": "true"})
        self.assertEqual(response.status_code, 200)
        row = response.data["results"][0]
        self.assertEqual(row["definition"]["description_zh"], "简要说明")
        self.assertEqual(row["definition"]["environment"], self.skill.definition["environment"])
        self.assertNotIn("content", row["definition"])
        self.assertEqual(row["package_manifest"], {"file_count": 2})
        self.assertLess(len(response.content), 4000)
        for query in queries:
            projection = query["sql"].split(" FROM ", 1)[0]
            self.assertNotIn(', "lens_skill"."definition",', projection)
            self.assertNotIn(', "lens_skill"."package_manifest",', projection)
        for url in ["/api/lens/admin/skills/", f"/api/lens/admin/skills/{self.skill.uuid}/?compact=true"]:
            full = self.client.get(url)
            result = full.data["results"][0] if "results" in full.data else full.data
            self.assertEqual(result["definition"], self.skill.definition)
            self.assertEqual(result["package_manifest"], self.skill.package_manifest)

    def test_update_check_can_return_compact_summaries(self):
        """Update metadata refresh does not reintroduce large list bodies."""

        with patch("lens.views.skills.check_skill_github_update"):
            response = self.client.post("/api/lens/admin/skills/check-updates/?compact=true")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("content", response.data[0]["definition"])

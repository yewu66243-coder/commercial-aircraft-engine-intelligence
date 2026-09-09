import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gpt_researcher.intelligence_templates import (  # noqa: E402
    delete_intelligence_template,
    get_template_catalog,
    save_intelligence_template,
)


class IntelligenceTemplateCrudTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_local_docs = os.environ.get("LOCAL_DOCS_PATH")
        os.environ["LOCAL_DOCS_PATH"] = self.tempdir.name
        root = Path(self.tempdir.name)
        (root / "demand_models.json").write_text("[]\n", encoding="utf-8")
        (root / "task_templates.json").write_text("[]\n", encoding="utf-8")
        (root / "source_templates.json").write_text("[]\n", encoding="utf-8")

    def tearDown(self):
        if self.previous_local_docs is None:
            os.environ.pop("LOCAL_DOCS_PATH", None)
        else:
            os.environ["LOCAL_DOCS_PATH"] = self.previous_local_docs
        self.tempdir.cleanup()

    def test_create_and_delete_task_template(self):
        result = save_intelligence_template(
            "task_templates",
            {
                "name": "适航通告跟踪",
                "task_text": "跟踪 FAA、EASA 与 CAAC 发布的发动机适航通告。",
                "recommended_scopes": "web,user_docs",
                "source_categories": "regulator,oem",
            },
        )

        self.assertEqual(result["action"], "created")
        template = result["template"]
        self.assertEqual(template["recommended_scopes"], ["web", "user_docs"])
        self.assertEqual(template["source_categories"], ["regulator", "oem"])
        self.assertIn(template, get_template_catalog()["task_templates"])

        deleted = delete_intelligence_template("task_templates", template["id"])
        self.assertEqual(deleted["deleted_id"], template["id"])
        self.assertEqual(get_template_catalog()["task_templates"], [])

    def test_create_source_template_normalizes_domain(self):
        result = save_intelligence_template(
            "source_templates",
            {
                "name": "Boeing",
                "domain": "https://www.boeing.com/commercial/",
                "category": "airframer",
                "category_label": "飞机制造商",
                "default_enabled": True,
                "keywords": "Boeing, 737 MAX, 787",
            },
        )

        template = result["template"]
        self.assertEqual(template["domain"], "www.boeing.com")
        self.assertEqual(template["keywords"], ["Boeing", "737 MAX", "787"])
        self.assertTrue(template["default_enabled"])

        path = Path(self.tempdir.name) / "source_templates.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["id"], template["id"])

    def test_rejects_unsupported_template_type(self):
        with self.assertRaises(ValueError):
            save_intelligence_template("demand_models", {"name": "x"})


if __name__ == "__main__":
    unittest.main()

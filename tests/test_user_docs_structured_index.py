import os
import tempfile
import unittest
from pathlib import Path

from gpt_researcher.document.local_index import prepare_local_docs_for_query
from gpt_researcher.document.local_library import (
    list_local_library,
    rebuild_user_docs_index_from_pool,
    resolve_local_library_file,
)


class StructuredUserDocsIndexTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_local_docs = os.environ.get("LOCAL_DOCS_PATH")
        os.environ["LOCAL_DOCS_PATH"] = self.tempdir.name
        root = Path(self.tempdir.name)
        self.user_docs = root / "user_docs"
        self.fixture = self.user_docs / "总资料库" / "国标库" / "GB／T 35794-2018_民用飞机氧气系统安全性设计.txt"
        self.fixture.parent.mkdir(parents=True, exist_ok=True)
        self.fixture.write_text(
            "\n".join(
                [
                    "GB/T 35794-2018 民用飞机氧气系统安全性设计",
                    "范围",
                    "本文件规定了民用飞机氧气系统安全性设计的基本要求。",
                    "规范性引用文件",
                    "CCAR-25 SAE ARP4761 FHA FMEA FTA PSSA SSA",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        if self.previous_local_docs is None:
            os.environ.pop("LOCAL_DOCS_PATH", None)
        else:
            os.environ["LOCAL_DOCS_PATH"] = self.previous_local_docs
        self.tempdir.cleanup()

    def test_rebuild_user_docs_index_keeps_recursive_metadata(self):
        result = rebuild_user_docs_index_from_pool()
        self.assertEqual(result["entry_count"], 1)

        library = list_local_library()
        self.assertEqual(library["stats"]["user_docs_count"], 1)
        self.assertEqual(library["stats"]["user_index_count"], 1)
        record = library["user_docs"][0]
        self.assertEqual(record["relative_path"], "总资料库/国标库/GB／T 35794-2018_民用飞机氧气系统安全性设计.txt")
        self.assertEqual(record["source_library"], "国标库")
        self.assertTrue(record["indexed"])

        path = resolve_local_library_file("user_docs", record["relative_path"])
        self.assertEqual(path, self.fixture.resolve())

    def test_prepare_local_docs_selects_nested_user_doc(self):
        rebuild_user_docs_index_from_pool()
        with tempfile.TemporaryDirectory() as selected_root:
            selection = prepare_local_docs_for_query(
                "民用飞机氧气系统安全性设计 适航",
                max_docs=3,
                local_docs_root=self.tempdir.name,
                temp_root=selected_root,
                include_papers=False,
                include_user_docs=True,
                include_patents=False,
            )

        self.assertTrue(selection.has_documents)
        self.assertEqual(selection.selected[0].source_type, "用户资料")
        self.assertIn("氧气系统", selection.selected[0].title)


if __name__ == "__main__":
    unittest.main()

import json

import pytest

from gpt_researcher.evaluation import entity_evaluator


class EntityGroundTruthTests:
    __test__ = True

    def test_category_labels_expose_complete_stable_mapping(self):
        assert entity_evaluator.ENTITY_CATEGORY_LABELS == {
            "organization": "机构",
            "model": "型号",
            "material": "材料",
            "parameter": "参数",
            "time": "时间",
            "other": "其他",
        }

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("机构", "organization"),
            ("企业", "organization"),
            ("organization", "organization"),
            ("型号", "model"),
            ("model", "model"),
            ("材料", "material"),
            ("material", "material"),
            ("参数", "parameter"),
            ("parameter", "parameter"),
            ("时间", "time"),
            ("time", "time"),
            ("not-a-category", "other"),
        ],
    )
    def test_category_aliases_normalize_to_stable_ids(self, value, expected):
        assert entity_evaluator.normalize_entity_category(value) == expected

    def test_valid_task_named_json_loads_and_classifies_entities(self, tmp_path, monkeypatch):
        task = "engine-materials"
        path = tmp_path / f"{task}.json"
        path.write_text(
            json.dumps(
                {
                    "task": task,
                    "entities": [
                        {"name": "粉末冶金", "category": "材料", "aliases": ["powder metal"]},
                        {"name": "燃油消耗", "category": "parameter", "aliases": ["fuel consumption"]},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["status"] == "loaded"
        assert result["path"] == str(path)
        assert len(result["entities"]) == 2
        assert [item["category"] for item in result["entities"]] == ["material", "parameter"]
        assert result["entities"][0]["aliases"] == ["powdermetal"]

    def test_malformed_json_is_invalid_without_echoing_raw_content(self, tmp_path, monkeypatch):
        task = "broken"
        (tmp_path / f"{task}.json").write_text("{not-json", encoding="utf-8")
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["status"] == "invalid_ground_truth"
        assert result["error_code"] == "invalid_json"
        assert "{not-json" not in result["message"]

    def test_declared_task_mismatch_is_invalid(self, tmp_path, monkeypatch):
        task = "expected-task"
        (tmp_path / f"{task}.json").write_text(
            json.dumps({"task": "different-task", "entities": []}), encoding="utf-8"
        )
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["status"] == "invalid_ground_truth"
        assert result["error_code"] == "task_mismatch"

    def test_missing_file_is_missing_with_empty_entities(self, tmp_path, monkeypatch):
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth("does-not-exist")

        assert result["status"] == "missing"
        assert result["entities"] == []

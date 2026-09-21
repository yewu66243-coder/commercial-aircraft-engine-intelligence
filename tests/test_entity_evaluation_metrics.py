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

    @pytest.mark.parametrize("bad_name", [{"oops": "PW1000G"}, ["PW1000G"], True, 123])
    def test_rejects_non_string_raw_entity_names(self, tmp_path, monkeypatch, bad_name):
        task = "bad-name"
        (tmp_path / f"{task}.json").write_text(json.dumps([{"name": bad_name}]), encoding="utf-8")
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["status"] == "invalid_ground_truth"
        assert result["error_code"] == "invalid_entity"

    def test_accepts_legacy_chinese_name_key(self, tmp_path, monkeypatch):
        task = "legacy-name"
        (tmp_path / f"{task}.json").write_text(json.dumps([{"实体": "PW1000G"}]), encoding="utf-8")
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["status"] == "loaded"
        assert result["entities"][0]["name"] == "PW1000G"

    def test_category_null_falls_back_to_type(self, tmp_path, monkeypatch):
        task = "category-fallback"
        (tmp_path / f"{task}.json").write_text(
            json.dumps([{"name": "PW1000G", "category": None, "type": "model"}]), encoding="utf-8"
        )
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["entities"][0]["category"] == "model"

    def test_task_specific_candidate_precedes_generic(self, tmp_path, monkeypatch):
        task = "precedence"
        (tmp_path / f"{task}.json").write_text(json.dumps([{"name": "specific"}]), encoding="utf-8")
        (tmp_path / "ground_truth.json").write_text(json.dumps([{"name": "generic"}]), encoding="utf-8")
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["entities"][0]["name"] == "specific"

    def test_loads_top_level_list_and_legacy_expected_entities(self, tmp_path, monkeypatch):
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)
        list_task = "legacy-list"
        (tmp_path / f"{list_task}.json").write_text(json.dumps([{"name": "A"}]), encoding="utf-8")
        dict_task = "legacy-dict"
        (tmp_path / f"{dict_task}.json").write_text(
            json.dumps({"expected_entities": [{"name": "B"}]}), encoding="utf-8"
        )

        assert entity_evaluator.load_ground_truth(list_task)["status"] == "loaded"
        assert entity_evaluator.load_ground_truth(dict_task)["entities"][0]["name"] == "B"

    @pytest.mark.parametrize(
        "payload,error_code",
        [
            ("null", "invalid_schema"),
            ('{"entities": {}}', "invalid_schema"),
            ('{"entities": [{"name": "A", "aliases": "bad"}]}', "invalid_aliases"),
        ],
    )
    def test_rejects_malformed_schema_and_aliases(self, tmp_path, monkeypatch, payload, error_code):
        task = "malformed"
        (tmp_path / f"{task}.json").write_text(payload, encoding="utf-8")
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["status"] == "invalid_ground_truth"
        assert result["error_code"] == error_code

    def test_normalizes_and_deduplicates_aliases(self, tmp_path, monkeypatch):
        task = "aliases"
        (tmp_path / f"{task}.json").write_text(
            json.dumps([{"name": "A", "aliases": ["Powder Metal", "powder-metal", ""]}]), encoding="utf-8"
        )
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["entities"][0]["aliases"] == ["powdermetal"]

    def test_evaluate_report_entities_uses_legacy_ground_truth(self, tmp_path, monkeypatch):
        task = "evaluate-legacy"
        (tmp_path / f"{task}.json").write_text(json.dumps([{"name": "PW1000G"}]), encoding="utf-8")
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)
        report = "## \u5b9e\u4f53\u4e0e\u53c2\u6570\u6e05\u5355\n| \u5b9e\u4f53/\u53c2\u6570 | \u6570\u503c/\u63cf\u8ff0 | \u8bc1\u636e |\n| --- | --- | --- |\n| PW1000G | engine | - |"

        result = entity_evaluator.evaluate_report_entities(report, task)

        assert result["status"] == "auto_evaluated"
        assert result["expected_entities"][0]["name"] == "PW1000G"
        assert result["accuracy"] == 1.0

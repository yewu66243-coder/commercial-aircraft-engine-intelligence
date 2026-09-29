import json

import pytest

from gpt_researcher.evaluation import entity_evaluator


def _entity(name, category, **extra):
    return {"name": name, "category": category, **extra}


def _report(*rows):
    body = [
        "## 实体与参数清单",
        "| 类别 | 实体/参数 | 数值/描述 | 证据 |",
        "| --- | --- | --- | --- |",
    ]
    body.extend(f"| {category} | {name} | {value} | {evidence} |" for category, name, value, evidence in rows)
    return "\n".join(body)


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

    @pytest.mark.parametrize(
        "payload",
        [
            {"entities": [{"name": "generic"}]},
            {"task": "", "entities": [{"name": "generic"}]},
            {"task": "different-task", "entities": [{"name": "generic"}]},
            [{"name": "generic"}],
        ],
    )
    def test_generic_ground_truth_requires_matching_task_binding(
        self, tmp_path, monkeypatch, payload
    ):
        task = "bound-task"
        (tmp_path / "ground_truth.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["status"] == "invalid_ground_truth"
        assert result["error_code"] == "task_mismatch"

    def test_generic_ground_truth_loads_when_task_matches(self, tmp_path, monkeypatch):
        task = "bound-task"
        (tmp_path / "ground_truth.json").write_text(
            json.dumps({"task": task, "entities": [{"name": "generic"}]}), encoding="utf-8"
        )
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["status"] == "loaded"
        assert result["entities"][0]["name"] == "generic"

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

    @pytest.mark.parametrize(
        "entity",
        [
            {"name": "推力", "type": "参数", "value": {}},
            {"name": "推力", "type": "参数", "value": True},
            {"name": "推力", "type": "参数", "unit": {}},
            {"name": "推力", "type": "参数", "tolerance": None},
            {"name": "推力", "type": "参数", "tolerance": -1},
            {"name": "推力", "type": "参数", "tolerance": float("inf")},
        ],
    )
    def test_rejects_malformed_parameter_ground_truth(self, tmp_path, monkeypatch, entity):
        task = "bad-parameter"
        (tmp_path / f"{task}.json").write_text(
            json.dumps({"entities": [entity]}), encoding="utf-8"
        )
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        result = entity_evaluator.load_ground_truth(task)

        assert result["status"] == "invalid_ground_truth"
        assert result["error_code"] == "invalid_parameter"

    def test_well_formed_unknown_parameter_unit_loads_for_audited_matching(self, tmp_path, monkeypatch):
        task = "unknown-unit"
        (tmp_path / f"{task}.json").write_text(
            json.dumps(
                {"entities": [{"name": "距离", "type": "参数", "value": 10, "unit": "furlong"}]}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

        assert entity_evaluator.load_ground_truth(task)["status"] == "loaded"

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


class TestEntityMetrics:
    def test_organization_equivalent_terms_match_as_aliases(self):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("普惠", "机构")],
            [_entity("Pratt & Whitney", "organization")],
        )

        assert result["overall"]["true_positive"] == 1
        assert result["matches"][0]["matched"] is True
        assert result["matches"][0]["match_type"] == "alias"

    def test_material_name_normalization_handles_case_and_punctuation(self):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("Ti-6Al-4V", "material")],
            [_entity("ti 6al 4v", "材料")],
        )

        assert result["overall"]["true_positive"] == 1
        assert result["matches"][0]["match_type"] == "exact_name"

    def test_curated_equivalent_term_expansion_is_not_generic_substring_matching(self):
        curated = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("PW1100G", "model")],
            [_entity("GTF", "model")],
        )
        arbitrary = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("Adobe", "organization")],
            [_entity("Airworthiness Directive", "organization")],
        )

        assert curated["overall"]["true_positive"] == 1
        assert arbitrary["overall"]["true_positive"] == 0

    def test_same_name_in_different_categories_does_not_match(self):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("A320neo", "organization")],
            [_entity("A320neo", "model")],
        )

        assert result["overall"] == {
            "true_positive": 0,
            "false_positive": 1,
            "false_negative": 1,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
        }

    @pytest.mark.parametrize(
        ("predicted", "expected", "should_match"),
        [("100.9 kN", "100 kN", True), ("101.1 kN", "100 kN", False)],
    )
    def test_parameter_values_use_default_one_percent_relative_tolerance(
        self, predicted, expected, should_match
    ):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("thrust", "parameter", value=predicted)],
            [_entity("thrust", "parameter", value=expected)],
        )

        assert result["overall"]["true_positive"] == int(should_match)

    def test_expected_parameter_tolerance_overrides_default(self):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("thrust", "parameter", value="104 kN")],
            [_entity("thrust", "parameter", value="100 kN", tolerance=0.05)],
        )

        assert result["overall"]["true_positive"] == 1
        assert result["matches"][0]["match_type"] == "within_tolerance"

    @pytest.mark.parametrize(
        ("predicted", "expected"),
        [
            ({"value": 1000, "unit": "lbf"}, {"value": "4.448221615 kN"}),
            ({"value": "39.37007874 in"}, {"value": 1, "unit": "m"}),
        ],
    )
    def test_parameter_values_convert_force_and_length_units(self, predicted, expected):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("rating", "parameter", **predicted)],
            [_entity("rating", "parameter", **expected)],
        )

        assert result["overall"]["true_positive"] == 1

    @pytest.mark.parametrize(
        ("predicted", "expected", "reason_fragment"),
        [
            ({"value": "10 furlong"}, {"value": "10 m"}, "unknown_unit"),
            ({"value": "10 kg"}, {"value": "10 m"}, "unit_mismatch"),
            ({"value": "many kN"}, {"value": "10 kN"}, "unparseable_value"),
            ({"value": "10 kN"}, {}, "missing_value_on_one_side"),
        ],
    )
    def test_invalid_parameter_comparisons_do_not_match_and_are_audited(
        self, predicted, expected, reason_fragment
    ):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("rating", "parameter", **predicted)],
            [_entity("rating", "parameter", **expected)],
        )

        assert result["overall"]["true_positive"] == 0
        assert any(
            not item["matched"] and reason_fragment in item["reason"]
            for item in result["match_audit"]
        )

    @pytest.mark.parametrize(
        ("predicted", "expected"),
        [
            ("10 kg/s", "10 kg"),
            ("100 kN/m", "100 kN"),
            ("10-20", "10"),
            ("10 to 20 kN", "10 kN"),
        ],
    )
    def test_compound_or_range_parameter_values_never_truncate_to_scalar(
        self, predicted, expected
    ):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("rating", "parameter", value=predicted)],
            [_entity("rating", "parameter", value=expected)],
        )

        assert result["overall"]["true_positive"] == 0
        assert result["match_audit"][0]["reason"] in {"unknown_unit", "unparseable_value"}

    @pytest.mark.parametrize("tolerance", [-0.1, "invalid"])
    def test_invalid_expected_tolerance_rejects_parameter_match(self, tolerance):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("rating", "parameter", value="10 kN")],
            [_entity("rating", "parameter", value="10 kN", tolerance=tolerance)],
        )

        assert result["overall"]["true_positive"] == 0
        assert any("invalid_tolerance" in item["reason"] for item in result["match_audit"])

    def test_invalid_tolerance_also_rejects_name_only_parameter_match(self):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("rating", "parameter")],
            [_entity("rating", "parameter", tolerance=-0.1)],
        )

        assert result["overall"]["true_positive"] == 0
        assert result["match_audit"][0]["reason"] == "invalid_tolerance"

    def test_both_missing_parameter_values_allow_audited_name_only_match(self):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [_entity("bypass ratio", "parameter")],
            [_entity("bypass-ratio", "parameter")],
        )

        assert result["overall"]["true_positive"] == 1
        assert result["matches"][0]["match_type"] == "name_only_parameter"
        assert "both_values_absent" in result["matches"][0]["reason"]

    def test_matching_prioritizes_exact_main_name_and_is_one_to_one(self):
        predictions = [
            _entity("普惠", "organization"),
            _entity("Pratt & Whitney", "organization"),
        ]
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            predictions,
            [_entity("Pratt & Whitney", "organization")],
        )

        accepted = [item for item in result["matches"] if item["matched"]]
        assert [item["predicted_index"] for item in accepted] == [1]
        assert len(result["matches"]) == 1
        assert any(not item["matched"] for item in result["match_audit"])
        assert result["correct_entities"] == [predictions[1]]
        assert result["wrong_entities"] == [predictions[0]]

    def test_matching_ties_use_original_prediction_order(self):
        predictions = [
            _entity("P&W", "organization"),
            _entity("普惠", "organization"),
        ]
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            predictions,
            [_entity("Pratt & Whitney", "organization")],
        )

        accepted = [item for item in result["matches"] if item["matched"]]
        assert [item["predicted_index"] for item in accepted] == [0]

    def test_category_and_micro_metrics_have_correct_counts_and_scores(self):
        result = entity_evaluator.evaluate_entities_against_ground_truth(
            [
                _entity("Pratt & Whitney", "organization"),
                _entity("Ti-6Al-4V", "material"),
            ],
            [
                _entity("普惠", "organization"),
                _entity("thrust", "parameter", value="100 kN"),
            ],
        )

        assert result["overall"] == {
            "true_positive": 1,
            "false_positive": 1,
            "false_negative": 1,
            "precision": 0.5,
            "recall": 0.5,
            "f1": 0.5,
        }
        assert result["categories"]["organization"] == {
            "label": "机构",
            "true_positive": 1,
            "false_positive": 0,
            "false_negative": 0,
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
        }
        assert result["categories"]["material"]["precision"] == 0.0
        assert result["categories"]["material"]["recall"] is None
        assert result["categories"]["material"]["f1"] == 0.0
        assert result["categories"]["parameter"]["precision"] is None
        assert result["categories"]["parameter"]["recall"] == 0.0
        assert result["categories"]["parameter"]["f1"] == 0.0

    def test_empty_inputs_produce_null_metric_denominators(self):
        result = entity_evaluator.evaluate_entities_against_ground_truth([], [])

        assert result["overall"] == {
            "true_positive": 0,
            "false_positive": 0,
            "false_negative": 0,
            "precision": None,
            "recall": None,
            "f1": None,
        }
        assert all(item["precision"] is None for item in result["categories"].values())
        assert all(item["recall"] is None for item in result["categories"].values())
        assert all(item["f1"] is None for item in result["categories"].values())


class TestEntityReportEvaluationModes:
    def test_loaded_truth_uses_strict_metrics_and_compatibility_scores(self, monkeypatch):
        monkeypatch.setattr(
            entity_evaluator,
            "load_ground_truth",
            lambda _task: {
                "status": "loaded",
                "path": "truth.json",
                "entities": [_entity("PW1000G", "model")],
                "error_code": "",
                "message": "",
            },
        )

        result = entity_evaluator.evaluate_report_entities(
            _report(("型号", "PW1000G", "engine", "-")), "task", threshold=0.9
        )

        assert result["mode"] == "strict"
        assert result["metrics"]["overall"]["f1"] == 1.0
        assert result["accuracy"] == 1.0
        assert result["accuracy_without_missed"] == 1.0
        assert result["accuracy_without_missed_method"] == "ground_truth_precision"
        assert result["requirement_met"] is True

    def test_missing_truth_uses_proxy_without_fabricating_strict_accuracy(self, monkeypatch):
        monkeypatch.setattr(
            entity_evaluator,
            "load_ground_truth",
            lambda _task: {
                "status": "missing",
                "path": "",
                "entities": [],
                "error_code": "",
                "message": "",
            },
        )

        result = entity_evaluator.evaluate_report_entities(
            _report(("型号", "PW1000G", "engine", "https://example.com/source")), "task"
        )

        assert result["mode"] == "proxy"
        assert result["metrics"] is None
        assert result["accuracy"] is None
        assert result["accuracy_without_missed"] == result["auto_evidence_eval"]["auto_evidence_accuracy"]

    def test_invalid_truth_returns_safe_invalid_mode(self, monkeypatch):
        monkeypatch.setattr(
            entity_evaluator,
            "load_ground_truth",
            lambda _task: {
                "status": "invalid_ground_truth",
                "path": "truth.json",
                "entities": [],
                "error_code": "invalid_json",
                "message": "标准答案文件不是有效 JSON。",
            },
        )

        result = entity_evaluator.evaluate_report_entities(
            _report(("型号", "PW1000G", "engine", "-")), "task"
        )

        assert result["mode"] == "invalid"
        assert result["status"] == "invalid_ground_truth"
        assert result["metrics"] is None
        assert result["accuracy"] is None
        assert result["correct_entities"] is None
        assert result["ground_truth_error_code"] == "invalid_json"

    def test_loaded_truth_without_extracted_table_still_scores_all_truth_as_missed(self, monkeypatch):
        expected = [_entity("PW1000G", "model")]
        monkeypatch.setattr(
            entity_evaluator,
            "load_ground_truth",
            lambda _task: {
                "status": "loaded",
                "path": "truth.json",
                "entities": expected,
                "error_code": "",
                "message": "",
            },
        )

        result = entity_evaluator.evaluate_report_entities("# Report without entity table", "task")

        assert result["mode"] == "strict"
        assert result["status"] == "auto_evaluated"
        assert result["missed_entities"] == expected
        assert result["metrics"]["overall"]["recall"] == 0.0
        assert result["metrics"]["overall"]["f1"] == 0.0
        assert result["accuracy"] == 0.0


class TestExplicitGroundTruthPath:
    """The active ground-truth file selected by the caller must win over defaults."""

    @staticmethod
    def _write_truth(path, *names):
        path.write_text(
            json.dumps(
                {
                    "task": "ground-truth-path",
                    "entities": [{"name": name, "type": "型号"} for name in names],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_explicit_path_is_used_even_when_default_dir_holds_another_file(self, tmp_path, monkeypatch):
        task = "ground-truth-path"
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        self._write_truth(default_dir / f"{task}.json", "默认实体")
        self._write_truth(default_dir / "ground_truth.json", "通用实体")
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: default_dir)

        specified = tmp_path / "active.json"
        self._write_truth(specified, "指定实体")

        result = entity_evaluator.evaluate_report_entities(
            _report(("型号", "指定实体", "engine", "-")), task, ground_truth_path=specified
        )

        assert result["mode"] == "strict"
        assert result["ground_truth_status"] == "loaded"
        assert result["ground_truth_path"] == str(specified)
        assert [item["name"] for item in result["expected_entities"]] == ["指定实体"]
        assert result["metrics"]["overall"]["f1"] == 1.0

    def test_explicit_none_forces_proxy_mode_without_loading_defaults(self, tmp_path, monkeypatch):
        task = "ground-truth-path"
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        self._write_truth(default_dir / f"{task}.json", "默认实体")
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: default_dir)

        result = entity_evaluator.evaluate_report_entities(
            _report(("型号", "默认实体", "engine", "https://example.test/source")),
            task,
            ground_truth_path=None,
        )

        assert result["mode"] == "proxy"
        assert result["ground_truth_status"] == "missing"
        assert result["ground_truth_path"] == ""
        assert result["metrics"] is None
        assert result["accuracy"] is None

    def test_explicit_path_without_file_reports_missing_instead_of_falling_back(self, tmp_path, monkeypatch):
        task = "ground-truth-path"
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        self._write_truth(default_dir / f"{task}.json", "默认实体")
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: default_dir)

        result = entity_evaluator.evaluate_report_entities(
            _report(("型号", "默认实体", "engine", "-")), task, ground_truth_path=tmp_path / "absent.json"
        )

        assert result["mode"] == "proxy"
        assert result["ground_truth_status"] == "missing"
        assert result["accuracy"] is None

    def test_explicit_invalid_path_reports_safe_invalid_state(self, tmp_path, monkeypatch):
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: default_dir)
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")

        result = entity_evaluator.evaluate_report_entities(
            _report(("型号", "默认实体", "engine", "-")), "ground-truth-path", ground_truth_path=broken
        )

        assert result["mode"] == "invalid"
        assert result["status"] == "invalid_ground_truth"
        assert result["ground_truth_error_code"] == "invalid_json"
        assert result["ground_truth_path"] == str(broken)

    def test_omitted_path_keeps_default_task_resolution(self, tmp_path, monkeypatch):
        task = "ground-truth-path"
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        self._write_truth(default_dir / f"{task}.json", "默认实体")
        monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: default_dir)

        result = entity_evaluator.evaluate_report_entities(
            _report(("型号", "默认实体", "engine", "-")), task
        )

        assert result["mode"] == "strict"
        assert result["ground_truth_path"] == str(default_dir / f"{task}.json")

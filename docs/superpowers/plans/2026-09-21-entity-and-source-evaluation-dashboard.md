# Core Entity and Public-Link Evaluation Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic, per-report entity Precision/Recall/F1 and public-link accessibility measurement, persist a stable evaluation summary, and show it above the generated report.

**Architecture:** Extend the existing entity evaluator with validated ground-truth loading, category-aware one-to-one matching, numeric/unit normalization, and micro-averaged metrics. Add a pure evaluation-summary adapter between backend evaluators and the frontend, then render that stable payload through a small testable browser module while keeping report export resilient to evaluation failures.

**Tech Stack:** Python 3.11, `unittest`, FastAPI service integration, vanilla JavaScript, Node.js assertions, HTML/CSS.

---

## File map

- Modify `gpt_researcher/evaluation/entity_evaluator.py`: validate task ground truth, normalize categories, compare parameter values/units, and calculate category plus overall Precision/Recall/F1.
- Create `gpt_researcher/evaluation/evaluation_summary.py`: convert existing entity and URL results into a stable UI/audit payload without I/O.
- Modify `gpt_researcher/evaluation/__init__.py`: export the summary builder.
- Modify `three_agent_service.py`: make URL accessibility strict, evaluate every unique report URL, isolate evaluation failures, and publish `evaluation_summary`.
- Create `frontend/evaluation_panel.js`: pure view-model formatting plus DOM rendering/reset logic.
- Modify `frontend/index.html`: add the hidden summary cards, category table, and new script include.
- Modify `frontend/scripts.js`: reset the panel for a new run and render the returned summary.
- Modify `frontend/styles.css`: responsive card, state, and detail-table styles.
- Create `tests/test_entity_evaluation_metrics.py`: entity loader, matching, unit conversion, and metrics tests.
- Create `tests/test_evaluation_summary.py`: stable strict/proxy/no-link/error payload tests.
- Create `tests/test_evaluation_pipeline.py`: URL semantics, complete coverage, pipeline publication, and failure isolation tests.
- Create `tests/test_evaluation_dashboard_ui.py`: markup and integration wiring assertions.
- Create `tests/js/check_evaluation_panel.cjs`: behavioral tests for the browser module's view model.

## Task 1: Validate and classify entity ground truth

**Files:**
- Modify: `gpt_researcher/evaluation/entity_evaluator.py:18-139,822-845`
- Create: `tests/test_entity_evaluation_metrics.py`

- [ ] **Step 1: Write the failing category and ground-truth tests**

Create `tests/test_entity_evaluation_metrics.py` with:

```python
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpt_researcher.evaluation import entity_evaluator as evaluator


class EntityGroundTruthTests(unittest.TestCase):
    def test_category_aliases_are_normalized(self):
        cases = {
            "制造商": "organization",
            "监管机构": "organization",
            "发动机型号": "model",
            "合金": "material",
            "技术指标": "parameter",
            "日期": "time",
            "未知分类": "other",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(evaluator.normalize_entity_category(raw), expected)

    def test_task_named_ground_truth_is_loaded_and_validated(self):
        payload = {
            "task": "GTF 材料研究",
            "entities": [
                {"type": "材料", "name": "粉末金属"},
                {"type": "参数", "name": "起飞推力", "value": 33110, "unit": "lbf"},
            ],
        }
        with TemporaryDirectory() as temporary, patch.object(
            evaluator, "get_ground_truth_dir", return_value=Path(temporary)
        ):
            path = Path(temporary) / "GTF_材料研究.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            result = evaluator.load_ground_truth("GTF 材料研究")

        self.assertEqual(result["status"], "loaded")
        self.assertEqual(len(result["entities"]), 2)
        self.assertEqual(result["entities"][0]["category"], "material")

    def test_invalid_json_and_task_mismatch_are_explicit(self):
        with TemporaryDirectory() as temporary, patch.object(
            evaluator, "get_ground_truth_dir", return_value=Path(temporary)
        ):
            directory = Path(temporary)
            (directory / "broken.json").write_text("{not-json", encoding="utf-8")
            broken = evaluator.load_ground_truth("broken")
            (directory / "other.json").write_text(
                json.dumps({"task": "different", "entities": []}), encoding="utf-8"
            )
            mismatch = evaluator.load_ground_truth("other")

        self.assertEqual(broken["status"], "invalid_ground_truth")
        self.assertEqual(broken["error_code"], "invalid_json")
        self.assertNotIn("{not-json", broken["message"])
        self.assertEqual(mismatch["error_code"], "task_mismatch")

    def test_missing_ground_truth_is_not_an_error(self):
        with TemporaryDirectory() as temporary, patch.object(
            evaluator, "get_ground_truth_dir", return_value=Path(temporary)
        ):
            result = evaluator.load_ground_truth("missing")
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["entities"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new tests and verify the expected RED failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_entity_evaluation_metrics.EntityGroundTruthTests -v
```

Expected: FAIL because `normalize_entity_category` and `load_ground_truth` do not exist.

- [ ] **Step 3: Add category normalization and safe loader**

Add the following public constants and helpers near the top of `entity_evaluator.py`, then replace `_load_ground_truth` with `load_ground_truth`:

```python
ENTITY_CATEGORY_LABELS = {
    "organization": "机构",
    "model": "型号",
    "material": "材料",
    "parameter": "参数",
    "time": "时间",
    "other": "其他",
}
ENTITY_CATEGORY_ALIASES = {
    "organization": {"机构", "企业", "公司", "制造商", "监管机构", "研究机构", "organization"},
    "model": {"型号", "产品", "发动机型号", "部件型号", "平台", "model"},
    "material": {"材料", "合金", "涂层", "复合材料", "工艺材料", "material"},
    "parameter": {"参数", "性能参数", "技术指标", "数值", "规格", "parameter"},
    "time": {"时间", "日期", "年份", "阶段", "里程碑", "time"},
}


def normalize_entity_category(value: Any) -> str:
    normalized = _normalize_entity(value)
    for category, aliases in ENTITY_CATEGORY_ALIASES.items():
        if normalized in {_normalize_entity(alias) for alias in aliases}:
            return category
    return "other"


def _invalid_ground_truth(path: Path, code: str, message: str) -> Dict[str, Any]:
    return {
        "status": "invalid_ground_truth",
        "path": str(path),
        "entities": [],
        "error_code": code,
        "message": message,
    }


def load_ground_truth(task: str) -> Dict[str, Any]:
    for path in _ground_truth_candidates(task):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _invalid_ground_truth(path, "invalid_json", f"标准答案文件无法解析：{exc.__class__.__name__}")

        if isinstance(data, list):
            entities = data
            declared_task = ""
        elif isinstance(data, dict):
            declared_task = str(data.get("task") or "").strip()
            entities = data.get("entities") or data.get("expected_entities")
        else:
            return _invalid_ground_truth(path, "invalid_schema", "标准答案顶层必须是对象或数组。")

        if declared_task and _normalize_entity(declared_task) != _normalize_entity(task):
            return _invalid_ground_truth(path, "task_mismatch", "标准答案中的任务名称与当前任务不一致。")
        if not isinstance(entities, list):
            return _invalid_ground_truth(path, "invalid_schema", "entities 必须是数组。")

        normalized_entities = []
        for index, item in enumerate(entities):
            if not isinstance(item, dict) or not _entity_name(item):
                return _invalid_ground_truth(path, "invalid_entity", f"第 {index + 1} 条实体缺少有效名称。")
            aliases = item.get("aliases") or []
            if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
                return _invalid_ground_truth(path, "invalid_aliases", f"第 {index + 1} 条实体的 aliases 必须是字符串数组。")
            normalized = dict(item)
            normalized["category"] = normalize_entity_category(item.get("type") or item.get("category"))
            normalized["aliases"] = aliases
            normalized_entities.append(normalized)

        return {
            "status": "loaded",
            "path": str(path),
            "entities": normalized_entities,
            "error_code": "",
            "message": "已加载任务标准答案。",
        }

    return {
        "status": "missing",
        "path": "",
        "entities": [],
        "error_code": "",
        "message": "未找到任务标准答案。",
    }
```

Keep `_ground_truth_candidates` and `_entity_name`; move `_entity_name` above `load_ground_truth` so the loader can call it. Do not expose raw JSON content in error messages.

- [ ] **Step 4: Run the tests and verify GREEN**

Run the Step 2 command. Expected: 4 tests PASS.

- [ ] **Step 5: Commit Task 1**

```powershell
git add gpt_researcher/evaluation/entity_evaluator.py tests/test_entity_evaluation_metrics.py
git commit -m "feat: validate entity evaluation ground truth"
```

## Task 2: Match typed entities and calculate category metrics

**Files:**
- Modify: `gpt_researcher/evaluation/entity_evaluator.py:139-188,847-956`
- Modify: `tests/test_entity_evaluation_metrics.py`

- [ ] **Step 1: Add failing parameter and metrics tests**

Append to `tests/test_entity_evaluation_metrics.py`:

```python
class EntityMetricsTests(unittest.TestCase):
    def test_alias_material_and_category_boundaries(self):
        extracted = [
            {"type": "机构", "name": "普惠"},
            {"type": "材料", "name": "Powder-Metal"},
            {"type": "机构", "name": "同名实体"},
        ]
        expected = [
            {"type": "机构", "name": "Pratt & Whitney", "aliases": ["普惠"]},
            {"type": "材料", "name": "powder metal"},
            {"type": "材料", "name": "同名实体"},
        ]
        result = evaluator.evaluate_entities_against_ground_truth(extracted, expected)
        self.assertEqual(result["overall"]["true_positive"], 2)
        self.assertEqual(result["overall"]["false_positive"], 1)
        self.assertEqual(result["overall"]["false_negative"], 1)

    def test_parameter_unit_conversion_and_tolerance(self):
        extracted = [
            {"type": "参数", "name": "起飞推力", "value": "147.1 kN"},
            {"type": "参数", "name": "风扇直径", "value": 81, "unit": "in"},
            {"type": "参数", "name": "压力", "value": 100, "unit": "psi"},
        ]
        expected = [
            {"type": "参数", "name": "起飞推力", "value": 33110, "unit": "lbf"},
            {"type": "参数", "name": "风扇直径", "value": 2.05, "unit": "m", "tolerance": 0.02},
            {"type": "参数", "name": "压力", "value": 1, "unit": "MPa"},
        ]
        result = evaluator.evaluate_entities_against_ground_truth(extracted, expected)
        self.assertEqual(result["categories"]["parameter"]["true_positive"], 2)
        self.assertEqual(result["categories"]["parameter"]["false_positive"], 1)
        self.assertEqual(result["categories"]["parameter"]["false_negative"], 1)

    def test_micro_metrics_and_null_denominators(self):
        result = evaluator.evaluate_entities_against_ground_truth(
            [{"type": "机构", "name": "A"}, {"type": "材料", "name": "wrong"}],
            [{"type": "机构", "name": "A"}, {"type": "材料", "name": "B"}],
        )
        self.assertEqual(result["overall"]["precision"], 0.5)
        self.assertEqual(result["overall"]["recall"], 0.5)
        self.assertEqual(result["overall"]["f1"], 0.5)
        self.assertIsNone(result["categories"]["time"]["precision"])

    def test_evaluate_report_uses_strict_f1_and_proxy_fallback(self):
        extracted = [{"type": "机构", "name": "普惠", "evidence": "[原文1]"}]
        loaded = {
            "status": "loaded",
            "path": "truth.json",
            "entities": [{"type": "机构", "name": "Pratt & Whitney", "aliases": ["普惠"]}],
            "error_code": "",
            "message": "ok",
        }
        with patch.object(evaluator, "extract_entities_from_report", return_value=extracted), patch.object(
            evaluator, "_run_auto_evidence_check", return_value={"auto_evidence_accuracy": 1.0, "requirement_met": True}
        ), patch.object(evaluator, "load_ground_truth", return_value=loaded):
            strict = evaluator.evaluate_report_entities("report", "task")
        self.assertEqual(strict["mode"], "strict")
        self.assertEqual(strict["metrics"]["overall"]["f1"], 1.0)
        self.assertTrue(strict["requirement_met"])

        missing = {"status": "missing", "path": "", "entities": [], "error_code": "", "message": "missing"}
        with patch.object(evaluator, "extract_entities_from_report", return_value=extracted), patch.object(
            evaluator, "_run_auto_evidence_check", return_value={"auto_evidence_accuracy": 0.75, "requirement_met": False}
        ), patch.object(evaluator, "load_ground_truth", return_value=missing):
            proxy = evaluator.evaluate_report_entities("report", "task")
        self.assertEqual(proxy["mode"], "proxy")
        self.assertIsNone(proxy["metrics"])
        self.assertEqual(proxy["accuracy_without_missed"], 0.75)
```

- [ ] **Step 2: Run the new class and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_entity_evaluation_metrics.EntityMetricsTests -v
```

Expected: FAIL because `evaluate_entities_against_ground_truth` is missing and `evaluate_report_entities` does not return `mode`/`metrics`.

- [ ] **Step 3: Add deterministic value and unit normalization**

Add to `entity_evaluator.py`:

```python
PARAMETER_NUMBER_RE = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")
UNIT_SPECS = {
    "n": ("force", 1.0, 0.0), "kn": ("force", 1000.0, 0.0), "lbf": ("force", 4.4482216153, 0.0),
    "g": ("mass", 0.001, 0.0), "kg": ("mass", 1.0, 0.0), "t": ("mass", 1000.0, 0.0), "lb": ("mass", 0.45359237, 0.0),
    "mm": ("length", 0.001, 0.0), "cm": ("length", 0.01, 0.0), "m": ("length", 1.0, 0.0), "in": ("length", 0.0254, 0.0),
    "pa": ("pressure", 1.0, 0.0), "kpa": ("pressure", 1000.0, 0.0), "mpa": ("pressure", 1_000_000.0, 0.0),
    "bar": ("pressure", 100_000.0, 0.0), "psi": ("pressure", 6894.757293168, 0.0),
    "°c": ("temperature", 1.0, 273.15), "c": ("temperature", 1.0, 273.15),
    "k": ("temperature", 1.0, 0.0), "°f": ("temperature", 5.0 / 9.0, 255.3722222222), "f": ("temperature", 5.0 / 9.0, 255.3722222222),
    "s": ("time", 1.0, 0.0), "min": ("time", 60.0, 0.0), "h": ("time", 3600.0, 0.0),
    "rpm": ("rotation", 1.0, 0.0), "%": ("ratio", 0.01, 0.0), "％": ("ratio", 0.01, 0.0),
}


def _parse_parameter(item: Dict[str, Any]) -> tuple[Optional[float], str, str]:
    raw_value = item.get("value")
    raw_unit = str(item.get("unit") or "").strip().lower()
    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
        number = float(raw_value)
    else:
        text = str(raw_value or "").strip()
        match = PARAMETER_NUMBER_RE.search(text)
        if not match:
            return None, "", "unparseable_value"
        number = float(match.group(0).replace(",", ""))
        if not raw_unit:
            raw_unit = text[match.end():].strip().lower()
    unit = raw_unit.replace(" ", "")
    if not unit:
        return number, "unitless", ""
    spec = UNIT_SPECS.get(unit)
    if spec is None:
        return None, "", "unknown_unit"
    dimension, scale, offset = spec
    return number * scale + offset, dimension, ""
```

- [ ] **Step 4: Replace substring matching with typed one-to-one metrics**

Replace `_match_entities` with these helpers and update `evaluate_report_entities` to use them:

```python
def _entity_alias_keys(item: Dict[str, Any]) -> set[str]:
    names = [_entity_name(item), *(item.get("aliases") or [])]
    expanded = []
    for name in names:
        expanded.extend(_synonym_variants(str(name)))
    return {_normalize_match_text(name) for name in expanded if _normalize_match_text(name)}


def _name_match_rank(extracted: Dict[str, Any], expected: Dict[str, Any]) -> Optional[int]:
    if _normalize_match_text(_entity_name(extracted)) == _normalize_match_text(_entity_name(expected)):
        return 0
    return 1 if _entity_alias_keys(extracted) & _entity_alias_keys(expected) else None


def _parameter_match(extracted: Dict[str, Any], expected: Dict[str, Any], default_tolerance: float) -> tuple[bool, str]:
    extracted_has_value = extracted.get("value") not in (None, "")
    expected_has_value = expected.get("value") not in (None, "")
    if not extracted_has_value and not expected_has_value:
        return True, "name_only_parameter_match"
    if extracted_has_value != expected_has_value:
        return False, "missing_value"
    actual, actual_dimension, actual_error = _parse_parameter(extracted)
    target, target_dimension, target_error = _parse_parameter(expected)
    if actual_error or target_error:
        return False, actual_error or target_error
    if actual_dimension != target_dimension:
        return False, "unit_mismatch"
    tolerance = float(expected.get("tolerance", default_tolerance))
    if tolerance < 0:
        return False, "invalid_tolerance"
    denominator = max(abs(target or 0.0), 1e-12)
    relative_error = abs((actual or 0.0) - (target or 0.0)) / denominator
    if relative_error == 0:
        return True, "exact_value_match"
    if relative_error <= tolerance:
        return True, "value_within_tolerance"
    return False, "value_out_of_tolerance"


def _metric_counts(true_positive: int, false_positive: int, false_negative: int) -> Dict[str, Any]:
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = true_positive / precision_denominator if precision_denominator else None
    recall = true_positive / recall_denominator if recall_denominator else None
    if true_positive == 0 and (false_positive or false_negative):
        f1 = 0.0
    elif precision is None or recall is None:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": None if precision is None else round(precision, 4),
        "recall": None if recall is None else round(recall, 4),
        "f1": None if f1 is None else round(f1, 4),
    }


def evaluate_entities_against_ground_truth(
    extracted: List[Dict[str, Any]], expected: List[Dict[str, Any]], default_tolerance: float = 0.01
) -> Dict[str, Any]:
    predicted = [dict(item, category=normalize_entity_category(item.get("type") or item.get("category"))) for item in extracted]
    truth = [dict(item, category=normalize_entity_category(item.get("type") or item.get("category"))) for item in expected]
    matched_predicted: set[int] = set()
    matched_truth: set[int] = set()
    matches = []

    for truth_index, truth_item in enumerate(truth):
        candidates = []
        for predicted_index, predicted_item in enumerate(predicted):
            if predicted_index in matched_predicted or predicted_item["category"] != truth_item["category"]:
                continue
            name_rank = _name_match_rank(predicted_item, truth_item)
            if name_rank is None:
                continue
            reason = "name_match"
            priority = name_rank
            if truth_item["category"] == "parameter":
                value_match, reason = _parameter_match(predicted_item, truth_item, default_tolerance)
                if not value_match:
                    continue
                if reason == "exact_value_match":
                    priority = name_rank
                elif reason == "value_within_tolerance":
                    priority = 2
                else:
                    priority = 3
            candidates.append((priority, predicted_index, reason))
        if candidates:
            _priority, predicted_index, reason = sorted(candidates)[0]
            matched_predicted.add(predicted_index)
            matched_truth.add(truth_index)
            matches.append({"predicted_index": predicted_index, "truth_index": truth_index, "reason": reason})

    categories = {}
    for category, label in ENTITY_CATEGORY_LABELS.items():
        tp = sum(1 for item in matches if truth[item["truth_index"]]["category"] == category)
        fp = sum(1 for index, item in enumerate(predicted) if item["category"] == category and index not in matched_predicted)
        fn = sum(1 for index, item in enumerate(truth) if item["category"] == category and index not in matched_truth)
        categories[category] = {"label": label, **_metric_counts(tp, fp, fn)}

    overall_tp = len(matches)
    overall_fp = len(predicted) - len(matched_predicted)
    overall_fn = len(truth) - len(matched_truth)
    return {
        "overall": _metric_counts(overall_tp, overall_fp, overall_fn),
        "categories": categories,
        "matches": matches,
        "correct_entities": [predicted[index] for index in sorted(matched_predicted)],
        "wrong_entities": [item for index, item in enumerate(predicted) if index not in matched_predicted],
        "missed_entities": [item for index, item in enumerate(truth) if index not in matched_truth],
    }
```

In `evaluate_report_entities`, call `ground_truth = load_ground_truth(task)`. Preserve current extraction and automatic evidence checks, then return:

```python
base.update({
    "ground_truth_status": ground_truth["status"],
    "ground_truth_path": ground_truth["path"],
    "ground_truth_error_code": ground_truth["error_code"],
    "ground_truth_message": ground_truth["message"],
    "mode": "proxy",
    "metrics": None,
})
if ground_truth["status"] == "invalid_ground_truth":
    base.update({"status": "invalid_ground_truth", "mode": "invalid"})
    return base
if ground_truth["status"] == "missing":
    return base

metrics = evaluate_entities_against_ground_truth(extracted, ground_truth["entities"])
f1 = metrics["overall"]["f1"]
base.update({
    "status": "auto_evaluated",
    "mode": "strict",
    "metrics": metrics,
    "expected_entities": ground_truth["entities"],
    "correct_entities": metrics["correct_entities"],
    "wrong_entities": metrics["wrong_entities"],
    "missed_entities": metrics["missed_entities"],
    "accuracy": f1,
    "accuracy_without_missed": metrics["overall"]["precision"],
    "accuracy_without_missed_method": "ground_truth_precision",
    "accuracy_without_missed_requirement_met": metrics["overall"]["precision"] is not None and metrics["overall"]["precision"] >= threshold,
    "requirement_met": f1 is not None and f1 >= threshold,
    "note": "已根据标准答案计算分类及综合 Precision、Recall 和 F1。",
})
return base
```

- [ ] **Step 5: Run all entity tests and the existing report finalization tests**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_entity_evaluation_metrics -v
.\.venv\Scripts\python.exe -m unittest tests.test_report_finalization -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit Task 2**

```powershell
git add gpt_researcher/evaluation/entity_evaluator.py tests/test_entity_evaluation_metrics.py
git commit -m "feat: calculate typed entity precision recall and f1"
```

## Task 3: Build the stable evaluation summary

**Files:**
- Create: `gpt_researcher/evaluation/evaluation_summary.py`
- Modify: `gpt_researcher/evaluation/__init__.py`
- Create: `tests/test_evaluation_summary.py`

- [ ] **Step 1: Write failing strict, proxy, and no-link summary tests**

Create `tests/test_evaluation_summary.py`:

```python
import unittest


class EvaluationSummaryTests(unittest.TestCase):
    def builder(self):
        from gpt_researcher.evaluation.evaluation_summary import build_evaluation_summary
        return build_evaluation_summary

    def test_strict_summary_publishes_f1_categories_and_thresholds(self):
        metrics = {
            "overall": {"true_positive": 9, "false_positive": 1, "false_negative": 0,
                        "precision": 0.9, "recall": 1.0, "f1": 0.9474},
            "categories": {"organization": {"label": "机构", "true_positive": 2,
                "false_positive": 0, "false_negative": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}},
        }
        summary = self.builder()(
            {"mode": "strict", "status": "auto_evaluated", "metrics": metrics,
             "ground_truth_path": "truth.json", "auto_evidence_eval": {}},
            {"total_urls": 100, "checked_urls": 100, "accessible_urls": 98,
             "failed_urls": 2, "accessibility_rate": 0.98, "results": []},
        )
        self.assertEqual(summary["status"], "completed")
        self.assertTrue(summary["entity"]["overall"]["requirement_met"])
        self.assertTrue(summary["public_links"]["requirement_met"])

    def test_proxy_summary_never_labels_support_rate_as_accuracy(self):
        summary = self.builder()(
            {"mode": "proxy", "status": "auto_evidence_checked", "metrics": None,
             "auto_evidence_eval": {"auto_evidence_accuracy": 0.75}},
            {"total_urls": 0, "checked_urls": 0, "accessible_urls": 0,
             "failed_urls": 0, "accessibility_rate": None, "results": []},
        )
        self.assertIsNone(summary["entity"]["overall"])
        self.assertEqual(summary["entity"]["proxy_evidence_support_rate"], 0.75)
        self.assertEqual(summary["public_links"]["status"], "no_public_urls")
        self.assertIsNone(summary["public_links"]["requirement_met"])

    def test_invalid_ground_truth_and_link_error_produce_failed_summary(self):
        summary = self.builder()(
            {"mode": "invalid", "status": "invalid_ground_truth", "metrics": None,
             "ground_truth_path": "broken.json", "ground_truth_message": "无法解析",
             "auto_evidence_eval": {}},
            {"evaluation_error": "TimeoutError", "results": []},
        )
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(len(summary["errors"]), 2)
        self.assertNotIn("secret", repr(summary))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_evaluation_summary -v
```

Expected: FAIL with `ModuleNotFoundError` for `evaluation_summary`.

- [ ] **Step 3: Implement the pure summary builder**

Create `gpt_researcher/evaluation/evaluation_summary.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

ENTITY_THRESHOLD = 0.90
PUBLIC_LINK_THRESHOLD = 0.98


def _entity_summary(entity_eval: Dict[str, Any]) -> tuple[Dict[str, Any], list[Dict[str, str]]]:
    mode = entity_eval.get("mode") or "proxy"
    errors = []
    overall = None
    categories = {}
    status = entity_eval.get("status") or "evaluation_failed"
    message = entity_eval.get("note") or ""
    if mode == "strict" and isinstance(entity_eval.get("metrics"), dict):
        metrics = entity_eval["metrics"]
        overall = dict(metrics.get("overall") or {})
        f1 = overall.get("f1")
        overall["requirement_met"] = f1 is not None and f1 >= ENTITY_THRESHOLD
        categories = metrics.get("categories") or {}
        status = "completed"
        message = "已根据任务金标准计算严格实体指标。"
    elif mode == "invalid":
        file_name = Path(str(entity_eval.get("ground_truth_path") or "")).name or "标准答案文件"
        reason = entity_eval.get("ground_truth_message") or "标准答案无效。"
        errors.append({
            "scope": "entity",
            "code": entity_eval.get("ground_truth_error_code") or "invalid_ground_truth",
            "message": f"{file_name}：{reason}",
        })
    return ({
        "mode": mode,
        "status": status,
        "ground_truth_path": entity_eval.get("ground_truth_path") or "",
        "threshold": ENTITY_THRESHOLD,
        "overall": overall,
        "categories": categories,
        "proxy_evidence_support_rate": (entity_eval.get("auto_evidence_eval") or {}).get("auto_evidence_accuracy") if mode != "strict" else None,
        "message": message,
    }, errors)


def _link_summary(url_check: Dict[str, Any]) -> tuple[Dict[str, Any], list[Dict[str, str]]]:
    errors = []
    rate = url_check.get("accessibility_rate")
    if url_check.get("evaluation_error"):
        status = "evaluation_failed"
        errors.append({"scope": "public_links", "code": "evaluation_failed",
                       "message": "公开链接可访问性测评未完成。"})
    elif not url_check.get("total_urls"):
        status = "no_public_urls"
    else:
        status = "completed"
    return ({
        "status": status,
        "threshold": PUBLIC_LINK_THRESHOLD,
        "total_count": int(url_check.get("total_urls") or 0),
        "checked_count": int(url_check.get("checked_urls") or 0),
        "accessible_count": int(url_check.get("accessible_urls") or 0),
        "inaccessible_count": int(url_check.get("failed_urls") or 0),
        "accessibility_rate": rate,
        "requirement_met": rate is not None and rate >= PUBLIC_LINK_THRESHOLD if status == "completed" else None,
    }, errors)


def build_evaluation_summary(entity_eval: Dict[str, Any], url_check: Dict[str, Any]) -> Dict[str, Any]:
    entity, entity_errors = _entity_summary(entity_eval or {})
    public_links, link_errors = _link_summary(url_check or {})
    errors = entity_errors + link_errors
    successful_parts = sum((entity["status"] not in {"invalid_ground_truth", "evaluation_failed"},
                            public_links["status"] != "evaluation_failed"))
    status = "completed" if not errors else ("partial" if successful_parts else "failed")
    return {"status": status, "entity": entity, "public_links": public_links, "errors": errors}
```

Export `build_evaluation_summary` from `gpt_researcher/evaluation/__init__.py`.

- [ ] **Step 4: Run and verify GREEN**

Run the Step 2 command. Expected: 3 tests PASS.

- [ ] **Step 5: Commit Task 3**

```powershell
git add gpt_researcher/evaluation/evaluation_summary.py gpt_researcher/evaluation/__init__.py tests/test_evaluation_summary.py
git commit -m "feat: add stable report evaluation summary"
```

## Task 4: Make URL accessibility strict and integrate resilient evaluation

**Files:**
- Modify: `three_agent_service.py:37-46,620-776,1398-1480,1570-1645`
- Create: `tests/test_evaluation_pipeline.py`

- [ ] **Step 1: Write failing URL semantics and coverage tests**

Create `tests/test_evaluation_pipeline.py` with:

```python
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError, URLError

from three_agent_service import ThreeAgentRequestData, ThreeAgentService


class UrlAccessibilityTests(unittest.TestCase):
    def test_get_403_is_inaccessible_and_head_405_retries_get(self):
        response = MagicMock()
        response.status = 200
        response.getcode.return_value = 200
        response.__enter__.return_value = response
        with patch("three_agent_service.urlopen", side_effect=[
            HTTPError("https://example.test", 405, "method", {}, None), response
        ]) as opener:
            result = ThreeAgentService._check_url_sync("https://example.test")
        self.assertTrue(result["accessible"])
        self.assertEqual(result["method"], "GET")
        self.assertEqual(opener.call_count, 2)

        with patch("three_agent_service.urlopen", side_effect=[
            HTTPError("https://example.test", 403, "forbidden", {}, None),
            HTTPError("https://example.test", 403, "forbidden", {}, None),
        ]):
            forbidden = ThreeAgentService._check_url_sync("https://example.test")
        self.assertFalse(forbidden["accessible"])
        self.assertEqual(forbidden["failure_reason"], "http_status")

    def test_tls_timeout_and_404_are_inaccessible(self):
        cases = [
            URLError("CERTIFICATE_VERIFY_FAILED"),
            URLError("timed out"),
            HTTPError("https://example.test", 404, "missing", {}, None),
        ]
        for error in cases:
            with self.subTest(error=error), patch("three_agent_service.urlopen", side_effect=error):
                self.assertFalse(ThreeAgentService._check_url_sync("https://example.test")["accessible"])

    def test_default_url_inspection_checks_every_unique_url(self):
        report = "\n".join(f"https://example.test/{index}" for index in range(72))
        service = ThreeAgentService(ThreeAgentRequestData(task="url test"))
        with patch.object(service, "_check_url_sync", return_value={"accessible": True,
                "ssl_verified": True, "failure_reason": "", "url": "x"}) as checker:
            result = asyncio.run(service.inspect_report_urls(report))
        self.assertEqual(checker.call_count, 72)
        self.assertEqual(result["checked_urls"], 72)
        self.assertEqual(result["accessibility_rate"], 1.0)
```

- [ ] **Step 2: Run URL tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_evaluation_pipeline.UrlAccessibilityTests -v
```

Expected: FAIL because 403 is currently accepted, TLS uses an unverified retry, and inspection caps at 50 URLs.

- [ ] **Step 3: Tighten URL semantics and remove the default cap**

In `_check_url_sync`:

```python
accessible = 200 <= status_code < 400
```

Use that expression in both normal response branches. Keep retrying GET after a HEAD `403` or `405`, but return any GET 4xx/5xx as inaccessible. Remove the SSL-unverified retry block so certificate failures stay inaccessible.

Change the inspector signature and slice logic:

```python
async def inspect_report_urls(self, report: str, max_urls: Optional[int] = None) -> Dict[str, Any]:
    urls = self._extract_urls(report)
    checked_urls = urls if max_urls is None else urls[:max_urls]
    skipped_count = max(0, len(urls) - len(checked_urls))
```

Preserve existing result field names so the run-statistics formatter stays compatible.

- [ ] **Step 4: Run URL tests and verify GREEN**

Run the Step 2 command. Expected: 3 tests PASS.

- [ ] **Step 5: Write the failing pipeline publication and failure-isolation test**

Append to `tests/test_evaluation_pipeline.py`:

```python
class EvaluationPipelineTests(unittest.TestCase):
    def test_pipeline_publishes_summary_and_url_failure_does_not_block_exports(self):
        from test_formal_report import BASE
        service = ThreeAgentService(ThreeAgentRequestData(task="GTF evaluation", report_source="web"))
        service.generation_status = "ready"
        saved = []
        entity_eval = {"mode": "proxy", "status": "auto_evidence_checked", "metrics": None,
                       "auto_evidence_eval": {"auto_evidence_accuracy": 0.8}}
        with patch.object(service, "pre_search_abstracts", new=AsyncMock()), \
             patch.object(service, "planner_agent", return_value=["GTF"]), \
             patch.object(service, "research_agent", new=AsyncMock(return_value=[])), \
             patch.object(service, "collect_report_images"), \
             patch.object(service, "writer_agent", new=AsyncMock(return_value=BASE)), \
             patch.object(service, "inspect_report_urls", new=AsyncMock(side_effect=TimeoutError("secret-url"))), \
             patch.object(service, "append_evaluation_record", side_effect=lambda record: saved.append(record) or "record.json"), \
             patch("three_agent_service.evaluate_public_url_sources", return_value={}), \
             patch("three_agent_service.prune_redundant_unchecked_url_citations", side_effect=lambda text, stats: (text, {})), \
             patch("three_agent_service.evaluate_report_entities", return_value=entity_eval), \
             patch("three_agent_service.write_text_to_md", new=AsyncMock(return_value="outputs/report.md")), \
             patch("three_agent_service.write_md_to_word", new=AsyncMock(return_value="outputs/report.docx")), \
             patch("three_agent_service.write_md_to_pdf", new=AsyncMock(return_value="outputs/report.pdf")):
            result = asyncio.run(service.run())

        summary = result["run_statistics"]["evaluation_summary"]
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["public_links"]["status"], "evaluation_failed")
        self.assertTrue(all(result["export_status"].values()))
        self.assertEqual(saved[0]["evaluation_summary"], summary)
        self.assertNotIn("secret-url", repr(summary))
```

- [ ] **Step 6: Run the pipeline test and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_evaluation_pipeline.EvaluationPipelineTests -v
```

Expected: FAIL because URL inspection still raises out of `run()` and no `evaluation_summary` exists.

- [ ] **Step 7: Isolate evaluator failures and publish the summary**

Import the builder in `three_agent_service.py`:

```python
from gpt_researcher.evaluation.evaluation_summary import build_evaluation_summary
```

Replace the direct URL inspection with:

```python
try:
    url_check = await self.inspect_report_urls(final_report)
except Exception as exc:
    logger.exception("Public URL accessibility evaluation failed")
    url_check = {
        "total_urls": len(self._extract_urls(final_report)),
        "checked_urls": 0,
        "accessible_urls": 0,
        "failed_urls": 0,
        "accessibility_rate": None,
        "skipped_urls": 0,
        "ssl_unverified_accessible_urls": 0,
        "failure_reasons": {},
        "results": [],
        "evaluation_error": exc.__class__.__name__,
    }
```

Wrap entity evaluation similarly, returning an `evaluation_failed` dictionary with no exception text. After both results exist, add:

```python
evaluation_summary = build_evaluation_summary(entity_eval, url_check)
```

Add `"evaluation_summary": evaluation_summary` to `run_stats` before export. Do not duplicate it at response top level; the frontend reads `data.run_statistics.evaluation_summary`.

- [ ] **Step 8: Run pipeline, formal pipeline, and finalization tests**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_evaluation_pipeline -v
.\.venv\Scripts\python.exe -m unittest tests.test_formal_pipeline tests.test_report_finalization -v
```

Expected: all tests PASS.

- [ ] **Step 9: Commit Task 4**

```powershell
git add three_agent_service.py tests/test_evaluation_pipeline.py
git commit -m "feat: publish resilient report evaluation metrics"
```

## Task 5: Add the report-top evaluation panel

**Files:**
- Create: `frontend/evaluation_panel.js`
- Create: `tests/js/check_evaluation_panel.cjs`
- Create: `tests/test_evaluation_dashboard_ui.py`
- Modify: `frontend/index.html:10,232-242,315-317`
- Modify: `frontend/scripts.js:2122-2145,2285-2315`
- Modify: `frontend/styles.css:3755-3766`

- [ ] **Step 1: Write failing browser view-model tests**

Create `tests/js/check_evaluation_panel.cjs`:

```javascript
const assert = require('assert');
const panel = require('../../frontend/evaluation_panel.js');

const strict = panel.buildViewModel({
  status: 'completed',
  entity: {
    mode: 'strict', threshold: 0.9,
    overall: { precision: 0.95, recall: 0.9, f1: 0.9231, requirement_met: true },
    categories: { organization: { label: '机构', true_positive: 4, false_positive: 0,
      false_negative: 0, precision: 1, recall: 1, f1: 1 } },
  },
  public_links: { status: 'completed', accessibility_rate: 0.98, requirement_met: true },
  errors: [],
});
assert.deepStrictEqual(strict.cards.map(card => card.value), ['92.31%', '90.00%', '98.00%']);
assert.strictEqual(strict.cards[0].state, 'pass');
assert.strictEqual(strict.rows[0].label, '机构');

const proxy = panel.buildViewModel({
  status: 'completed',
  entity: { mode: 'proxy', overall: null, categories: {}, proxy_evidence_support_rate: 0.75 },
  public_links: { status: 'no_public_urls', accessibility_rate: null, requirement_met: null },
  errors: [],
});
assert.deepStrictEqual(proxy.cards.map(card => card.value), ['待标准答案', '待标准答案', '无公开链接']);
assert(proxy.message.includes('证据支撑率（代理指标）：75.00%'));
assert(!proxy.message.includes('准确率：75.00%'));

const invalid = panel.buildViewModel({
  status: 'partial',
  entity: { mode: 'invalid', status: 'invalid_ground_truth', overall: null, categories: {} },
  public_links: { status: 'evaluation_failed', accessibility_rate: null },
  errors: [{ scope: 'entity', message: '标准答案文件无法解析。' }],
});
assert.strictEqual(invalid.cards[0].value, '标准答案错误');
assert.strictEqual(invalid.cards[2].value, '测评未完成');
assert(invalid.message.includes('标准答案文件无法解析'));

console.log('evaluation_panel_checks=passed');
```

Create `tests/test_evaluation_dashboard_ui.py`:

```python
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EvaluationDashboardUiTests(unittest.TestCase):
    def test_markup_and_runtime_wiring_exist(self):
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend/scripts.js").read_text(encoding="utf-8")
        self.assertIn('id="evaluationPanel"', html)
        self.assertIn('id="evaluationEntityF1"', html)
        self.assertIn('id="evaluationEntityRecall"', html)
        self.assertIn('id="evaluationLinkAccessibility"', html)
        self.assertIn('id="evaluationCategoryBody"', html)
        self.assertLess(html.index('id="evaluationPanel"'), html.index('id="reportContainer"'))
        self.assertIn('/site/evaluation_panel.js?v=evaluation-dashboard-20260921', html)
        self.assertIn('EvaluationPanel.reset()', script)
        self.assertIn('EvaluationPanel.render(data.run_statistics?.evaluation_summary)', script)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run both UI tests and verify RED**

```powershell
node tests/js/check_evaluation_panel.cjs
.\.venv\Scripts\python.exe -m unittest tests.test_evaluation_dashboard_ui -v
```

Expected: Node fails because `frontend/evaluation_panel.js` is missing; Python fails because panel markup and wiring are missing.

- [ ] **Step 3: Implement the testable frontend module**

Create `frontend/evaluation_panel.js` as a UMD-style browser/CommonJS module with these stable exports:

```javascript
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EvaluationPanel = api;
})(typeof window !== 'undefined' ? window : null, function () {
  const order = ['organization', 'model', 'material', 'parameter', 'time', 'other'];
  const percent = value => Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—';
  const state = met => met === true ? 'pass' : met === false ? 'fail' : 'neutral';

  function buildViewModel(summary) {
    const entity = summary?.entity || {};
    const links = summary?.public_links || {};
    const strict = entity.mode === 'strict' && entity.overall;
    const invalid = entity.mode === 'invalid' || entity.status === 'invalid_ground_truth';
    const cards = [
      { id: 'evaluationEntityF1', value: invalid ? '标准答案错误' : strict ? percent(entity.overall.f1) : '待标准答案', state: invalid ? 'fail' : strict ? state(entity.overall.requirement_met) : 'neutral' },
      { id: 'evaluationEntityRecall', value: invalid ? '标准答案错误' : strict ? percent(entity.overall.recall) : '待标准答案', state: invalid ? 'fail' : strict ? state(entity.overall.requirement_met) : 'neutral' },
      { id: 'evaluationLinkAccessibility', value: links.status === 'no_public_urls' ? '无公开链接' : links.status === 'evaluation_failed' ? '测评未完成' : percent(links.accessibility_rate), state: links.status === 'completed' ? state(links.requirement_met) : links.status === 'evaluation_failed' ? 'fail' : 'neutral' },
    ];
    const categories = entity.categories || {};
    const rows = order.filter(key => categories[key]).map(key => ({ key, ...categories[key] }));
    const messages = (summary?.errors || []).map(item => item.message).filter(Boolean);
    if (!strict && Number.isFinite(entity.proxy_evidence_support_rate)) {
      messages.unshift(`证据支撑率（代理指标）：${percent(entity.proxy_evidence_support_rate)}；该指标不能替代实体准确率。`);
    }
    return { cards, rows, message: messages.join(' '), visible: Boolean(summary) };
  }

  function render(summary, doc = document) {
    const model = buildViewModel(summary);
    const panel = doc.getElementById('evaluationPanel');
    if (!panel) return model;
    panel.hidden = !model.visible;
    model.cards.forEach(card => {
      const value = doc.getElementById(card.id);
      if (!value) return;
      value.textContent = card.value;
      if (value.parentElement) value.parentElement.dataset.state = card.state;
    });
    const body = doc.getElementById('evaluationCategoryBody');
    if (body) {
      body.replaceChildren();
      model.rows.forEach(row => {
        const tr = doc.createElement('tr');
        [row.label, row.true_positive, row.false_positive, row.false_negative,
         percent(row.precision), percent(row.recall), percent(row.f1)].forEach(value => {
          const td = doc.createElement('td');
          td.textContent = value ?? '—';
          tr.appendChild(td);
        });
        body.appendChild(tr);
      });
    }
    const message = doc.getElementById('evaluationMessage');
    if (message) message.textContent = model.message;
    return model;
  }

  function reset(doc = document) {
    const panel = doc.getElementById('evaluationPanel');
    if (panel) panel.hidden = true;
    const body = doc.getElementById('evaluationCategoryBody');
    if (body) body.replaceChildren();
    const message = doc.getElementById('evaluationMessage');
    if (message) message.textContent = '';
  }

  return { buildViewModel, render, reset };
});
```

- [ ] **Step 4: Add panel markup and styles**

Insert this block immediately after `reportQualityStatus` in `frontend/index.html`:

```html
<section id="evaluationPanel" class="evaluation-panel" aria-labelledby="evaluationPanelTitle" hidden>
  <div class="evaluation-heading">
    <h3 id="evaluationPanelTitle">自动测评结果</h3>
    <span>实体 F1 达标线 90% · 链接可访问率达标线 98%</span>
  </div>
  <div class="evaluation-metrics">
    <div class="evaluation-card"><span>实体 F1</span><b id="evaluationEntityF1">—</b></div>
    <div class="evaluation-card"><span>实体召回率</span><b id="evaluationEntityRecall">—</b></div>
    <div class="evaluation-card"><span>链接可访问率</span><b id="evaluationLinkAccessibility">—</b></div>
  </div>
  <p id="evaluationMessage" class="evaluation-message" aria-live="polite"></p>
  <details class="evaluation-details">
    <summary>查看分类明细</summary>
    <div class="evaluation-table-wrap">
      <table><thead><tr><th>类别</th><th>TP</th><th>FP</th><th>FN</th><th>Precision</th><th>Recall</th><th>F1</th></tr></thead>
      <tbody id="evaluationCategoryBody"></tbody></table>
    </div>
  </details>
</section>
```

Add `/site/evaluation_panel.js?v=evaluation-dashboard-20260921` before `scripts.js`, and update the local CSS/JS cache query strings to `evaluation-dashboard-20260921`.

Append CSS that uses existing color variables:

```css
.evaluation-panel { margin: 12px 0 16px; padding: 14px; border: 1px solid var(--is-line); border-radius: 10px; background: var(--is-panel); }
.evaluation-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.evaluation-heading h3 { margin: 0; font-size: 16px; }
.evaluation-heading span, .evaluation-message { color: var(--is-muted); font-size: 12px; }
.evaluation-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
.evaluation-card { padding: 10px; border: 1px solid var(--is-line); border-radius: 8px; background: rgba(91, 110, 128, .06); }
.evaluation-card span { color: var(--is-muted); font-size: 12px; }
.evaluation-card b { display: block; margin-top: 4px; font-size: 20px; }
.evaluation-card[data-state="pass"] { background: #edf7f1; color: #286448; }
.evaluation-card[data-state="fail"] { background: #fff0f0; color: #973c3c; }
.evaluation-message { margin: 10px 0 0; line-height: 1.5; }
.evaluation-details { margin-top: 10px; }
.evaluation-details summary { cursor: pointer; color: var(--is-blue); font-size: 13px; }
.evaluation-table-wrap { margin-top: 8px; overflow-x: auto; }
.evaluation-table-wrap table { width: 100%; border-collapse: collapse; font-size: 12px; }
.evaluation-table-wrap th, .evaluation-table-wrap td { padding: 7px; border-bottom: 1px solid var(--is-line); text-align: right; }
.evaluation-table-wrap th:first-child, .evaluation-table-wrap td:first-child { text-align: left; }
@media (max-width: 760px) { .evaluation-metrics { grid-template-columns: 1fr; } .evaluation-heading { align-items: flex-start; flex-direction: column; } }
```

- [ ] **Step 5: Wire reset and render into the report lifecycle**

In `startResearch`, after clearing the prior report, add:

```javascript
window.EvaluationPanel?.reset();
```

After `writeReport` and `renderSelectedSources`, add:

```javascript
window.EvaluationPanel?.render(data.run_statistics?.evaluation_summary);
```

- [ ] **Step 6: Run behavioral, static, and syntax checks**

```powershell
node tests/js/check_evaluation_panel.cjs
.\.venv\Scripts\python.exe -m unittest tests.test_evaluation_dashboard_ui -v
node --check frontend/evaluation_panel.js
node --check frontend/scripts.js
```

Expected: `evaluation_panel_checks=passed`, Python test PASS, and both syntax checks exit 0.

- [ ] **Step 7: Commit Task 5**

```powershell
git add frontend/evaluation_panel.js frontend/index.html frontend/scripts.js frontend/styles.css tests/js/check_evaluation_panel.cjs tests/test_evaluation_dashboard_ui.py
git commit -m "feat: show evaluation metrics above generated reports"
```

## Task 6: Run the complete regression and smoke verification

**Files:**
- Verify: all files changed by Tasks 1-5

- [ ] **Step 1: Run the focused evaluation suite**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_entity_evaluation_metrics tests.test_evaluation_summary tests.test_evaluation_pipeline tests.test_evaluation_dashboard_ui -v
node tests/js/check_evaluation_panel.cjs
```

Expected: all Python tests PASS and Node prints `evaluation_panel_checks=passed`.

- [ ] **Step 2: Run related report regressions**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_formal_pipeline tests.test_report_finalization tests.test_report_content tests.test_model_provider_selection -v
```

Expected: all tests PASS with no errors or failures.

- [ ] **Step 3: Run frontend syntax checks**

```powershell
node --check frontend/evaluation_panel.js
node --check frontend/scripts.js
```

Expected: both commands exit 0 with no output.

- [ ] **Step 4: Start an isolated server and verify HTTP delivery**

Start on port 8001:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
```

In a second terminal run:

```powershell
$root = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8001/
$panel = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8001/site/evaluation_panel.js?v=evaluation-dashboard-20260921
"root=$($root.StatusCode) panel=$($panel.StatusCode) hasMarkup=$($root.Content.Contains('evaluationPanel'))"
```

Expected: `root=200 panel=200 hasMarkup=True`.

- [ ] **Step 5: Perform one controlled UI acceptance run**

Place a small task-named ground-truth JSON in `outputs/records/entity_ground_truths/`, generate the matching short report, and verify:

1. three cards appear above the report;
2. strict entity F1 and recall are numeric, not proxy-labelled;
3. category detail expands and shows TP/FP/FN plus Precision/Recall/F1;
4. link accessibility shows `无公开链接` or a numeric rate;
5. report downloads remain enabled;
6. `outputs/records/evaluation_records.json` contains the same `evaluation_summary` returned by the API.

Remove only the temporary ground-truth fixture created for this acceptance run after recording the result.

- [ ] **Step 6: Inspect the final diff and repository state**

```powershell
git diff --check origin/main...HEAD
git status --short --branch
git log --oneline --decorate -8
```

Expected: no whitespace errors; only intentional commits are ahead of `origin/main`; no temporary fixture or generated report is staged.

- [ ] **Step 7: Request code review before integration**

Invoke `requesting-code-review`, address any findings with failing regression tests first, rerun Steps 1-3, then use `finishing-a-development-branch` to present merge/push/cleanup options.

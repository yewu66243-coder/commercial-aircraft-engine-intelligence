import json
import math
from io import BytesIO

import pytest
from openpyxl import Workbook

from gpt_researcher.evaluation import entity_evaluator, ground_truth_io
from gpt_researcher.evaluation.ground_truth_io import (
    ALLOWED_SUFFIXES,
    MAX_UPLOAD_BYTES,
    GroundTruthValidationError,
    ground_truth_path_for_task,
    load_ground_truth_upload,
    persist_ground_truth_upload,
)


TASK = "engine-reliability"


def _valid_payload(**overrides):
    payload = {
        "task": TASK,
        "entities": [
            {
                "type": "参数",
                "name": "推力",
                "aliases": ["thrust"],
                "value": 100,
                "unit": "kN",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _write_json(tmp_path, payload, name="truth.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=True), encoding="utf-8")
    return path


def _xlsx_bytes(rows):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "实体表"
    for row in rows:
        worksheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_xlsx(tmp_path, rows, name="truth.xlsx"):
    path = tmp_path / name
    path.write_bytes(_xlsx_bytes(rows))
    return path


def _assert_error(exc_info, code, message_part=""):
    error = exc_info.value
    assert error.code == code
    assert error.message == str(error)
    if message_part:
        assert message_part in error.message


def test_json_and_xlsx_normalize_to_the_same_canonical_entities(tmp_path):
    json_path = _write_json(tmp_path, _valid_payload())
    xlsx_path = _write_xlsx(
        tmp_path,
        [
            ["类别", "名称", "别名", "数值", "单位", "容差"],
            ["参数", "推力", "thrust", 100, "kN", None],
        ],
    )

    json_truth = load_ground_truth_upload(json_path, TASK)
    xlsx_truth = load_ground_truth_upload(xlsx_path, TASK)

    assert ALLOWED_SUFFIXES == {".json", ".xlsx"}
    assert json_truth == xlsx_truth == {
        "task": TASK,
        "entities": [
            {
                "type": "parameter",
                "name": "推力",
                "aliases": ["thrust"],
                "value": 100,
                "unit": "kN",
                "tolerance": 0.01,
            }
        ],
    }


@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        pytest.param("truth.txt", b"{}", "unsupported_file_type", id="unsupported"),
        pytest.param("truth.json", b"", "empty_file", id="empty"),
        pytest.param("truth.json", b"{" + b" " * MAX_UPLOAD_BYTES, "file_too_large", id="oversized"),
    ],
)
def test_upload_rejects_unsupported_empty_and_oversized_files(tmp_path, name, content, code):
    path = tmp_path / name
    path.write_bytes(content)

    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(path, TASK)

    _assert_error(exc_info, code)


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ([{"type": "model", "name": "LEAP"}], "invalid_schema"),
        (_valid_payload(task="another-task"), "task_mismatch"),
        (_valid_payload(entities=[]), "empty_entities"),
        (_valid_payload(entities=[{"name": "LEAP"}]), "invalid_entity"),
        (_valid_payload(entities=[{"type": "model"}]), "invalid_entity"),
        (_valid_payload(entities=[{"type": "model", "name": "LEAP", "aliases": "bad"}]), "invalid_aliases"),
        (_valid_payload(entities=[{"type": "parameter", "name": "ratio", "value": True}]), "invalid_parameter"),
        (_valid_payload(entities=[{"type": "parameter", "name": "ratio", "value": float("nan")}]), "invalid_parameter"),
        (_valid_payload(entities=[{"type": "parameter", "name": "ratio", "tolerance": -0.01}]), "invalid_parameter"),
        (_valid_payload(entities=[{"type": "parameter", "name": "ratio", "tolerance": math.inf}]), "invalid_parameter"),
    ],
)
def test_json_schema_validation_has_stable_error_codes(tmp_path, payload, code):
    path = _write_json(tmp_path, payload)

    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(path, TASK)

    _assert_error(exc_info, code)


def test_missing_task_is_injected_and_duplicate_normalized_entities_are_rejected(tmp_path):
    missing_task_payload = _valid_payload()
    del missing_task_payload["task"]
    injected_path = _write_json(tmp_path, missing_task_payload)
    assert load_ground_truth_upload(injected_path, TASK)["task"] == TASK

    duplicate_path = _write_json(
        tmp_path,
        _valid_payload(
            entities=[
                {"type": "型号", "name": "LEAP-1A"},
                {"type": "model", "name": "leap 1a"},
            ]
        ),
        "duplicate.json",
    )
    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(duplicate_path, TASK)

    _assert_error(exc_info, "duplicate_entity")


def test_xlsx_requires_columns_and_reports_sheet_name_and_actual_row(tmp_path):
    missing_columns = _write_xlsx(tmp_path, [["类别", "别名"], ["型号", "LEAP"]])
    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(missing_columns, TASK)
    _assert_error(exc_info, "missing_columns", "实体表")

    invalid_row = _write_xlsx(
        tmp_path,
        [["类别", "名称"], ["型号", "LEAP"], ["参数", None]],
        "invalid-row.xlsx",
    )
    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(invalid_row, TASK)
    _assert_error(exc_info, "invalid_entity", "实体表，第 3 行")


def test_xlsx_formula_without_cached_value_is_rejected_with_sheet_and_row(tmp_path):
    path = _write_xlsx(
        tmp_path,
        [["类别", "名称", "数值"], ["参数", "推力", "=100"]],
    )

    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(path, TASK)

    _assert_error(exc_info, "formula_without_cached_value", "实体表，第 2 行")


def test_persist_upload_uses_hashed_canonical_json_inside_destination(tmp_path):
    destination = tmp_path / "stored"
    metadata = persist_ground_truth_upload(
        json.dumps(_valid_payload(), ensure_ascii=False).encode("utf-8"),
        "../../outside.json",
        TASK,
        destination,
    )
    target = ground_truth_path_for_task(TASK, destination)

    assert target.parent == destination
    assert target.exists()
    assert metadata["stored_name"] == target.name
    assert len(metadata["sha256"]) == 64
    assert metadata["entity_count"] == 1
    assert metadata["category_counts"] == {"parameter": 1}
    assert json.loads(target.read_text(encoding="utf-8"))["entities"][0]["tolerance"] == 0.01
    assert not (tmp_path / "outside.json").exists()
    assert all(path.parent == destination for path in destination.iterdir())
    assert list(destination.iterdir()) == [target]


def test_parameter_string_value_must_be_a_finite_single_number(tmp_path):
    path = _write_json(
        tmp_path,
        _valid_payload(entities=[{"type": "parameter", "name": "ratio", "value": "garbage"}]),
    )

    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(path, TASK)

    _assert_error(exc_info, "invalid_parameter")


def test_duplicate_keys_preserve_negative_sign_and_normalize_numeric_equivalence(tmp_path):
    distinct = _write_json(
        tmp_path,
        _valid_payload(
            entities=[
                {"type": "parameter", "name": "ratio", "value": -1},
                {"type": "parameter", "name": "ratio", "value": 1},
            ]
        ),
        "distinct.json",
    )
    assert len(load_ground_truth_upload(distinct, TASK)["entities"]) == 2

    equivalent = _write_json(
        tmp_path,
        _valid_payload(
            entities=[
                {"type": "parameter", "name": "ratio", "value": 100},
                {"type": "parameter", "name": "ratio", "value": "100.0"},
            ]
        ),
        "equivalent.json",
    )
    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(equivalent, TASK)
    _assert_error(exc_info, "duplicate_entity")


def test_xlsx_all_formula_data_row_without_cached_values_is_not_ignored(tmp_path):
    path = _write_xlsx(
        tmp_path,
        [["类别", "名称", "数值"], ["=\"参数\"", "=\"推力\"", "=100"]],
    )

    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(path, TASK)

    _assert_error(exc_info, "formula_without_cached_value", "实体表，第 2 行")


def test_persist_cleans_a_temporary_file_when_canonical_json_write_fails(tmp_path, monkeypatch):
    destination = tmp_path / "stored"

    def fail_json_dump(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ground_truth_io.json, "dump", fail_json_dump)
    with pytest.raises(OSError, match="disk full"):
        persist_ground_truth_upload(
            json.dumps(_valid_payload(), ensure_ascii=False).encode("utf-8"),
            "truth.json",
            TASK,
            destination,
        )

    assert list(destination.iterdir()) == []


def test_persist_cleans_a_temporary_file_when_upload_write_fails(tmp_path, monkeypatch):
    destination = tmp_path / "stored"
    real_named_temporary_file = ground_truth_io.tempfile.NamedTemporaryFile

    class FailingTemporaryFile:
        def __init__(self, *args, **kwargs):
            self._temporary = real_named_temporary_file(*args, **kwargs)
            self.name = self._temporary.name

        def __enter__(self):
            self._temporary.__enter__()
            return self

        def __exit__(self, *args):
            return self._temporary.__exit__(*args)

        def write(self, _content):
            raise OSError("disk full")

    monkeypatch.setattr(ground_truth_io.tempfile, "NamedTemporaryFile", FailingTemporaryFile)
    with pytest.raises(OSError, match="disk full"):
        persist_ground_truth_upload(
            json.dumps(_valid_payload(), ensure_ascii=False).encode("utf-8"),
            "truth.json",
            TASK,
            destination,
        )

    assert list(destination.iterdir()) == []


def test_nonconvertible_tolerance_returns_a_stable_validation_error(tmp_path):
    path = _write_json(
        tmp_path,
        _valid_payload(entities=[{"type": "parameter", "name": "ratio", "tolerance": 10**400}]),
    )

    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(path, TASK)

    _assert_error(exc_info, "invalid_parameter")


@pytest.mark.parametrize("declared_task", [None, ""])
def test_explicit_missing_like_task_values_are_not_injected(tmp_path, declared_task):
    path = _write_json(tmp_path, _valid_payload(task=declared_task))

    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(path, TASK)

    _assert_error(exc_info, "task_mismatch")


def test_explicit_null_aliases_are_invalid(tmp_path):
    path = _write_json(
        tmp_path,
        _valid_payload(entities=[{"type": "model", "name": "LEAP", "aliases": None}]),
    )

    with pytest.raises(GroundTruthValidationError) as exc_info:
        load_ground_truth_upload(path, TASK)

    _assert_error(exc_info, "invalid_aliases")


def test_legacy_evaluator_fallback_keeps_shared_file_size_validation(tmp_path, monkeypatch):
    task = "legacy-oversized"
    path = tmp_path / f"{task}.json"
    path.write_bytes(json.dumps({"entities": [{"name": "LEAP"}]}).encode() + b" " * MAX_UPLOAD_BYTES)
    monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: tmp_path)

    result = entity_evaluator.load_ground_truth(task)

    assert result["status"] == "invalid_ground_truth"
    assert result["error_code"] == "file_too_large"


def test_legacy_evaluator_fallback_keeps_shared_file_type_validation(tmp_path, monkeypatch):
    path = tmp_path / "legacy.txt"
    path.write_text(json.dumps([{"name": "LEAP"}]), encoding="utf-8")
    monkeypatch.setattr(entity_evaluator, "_ground_truth_candidates", lambda _task: [path])

    result = entity_evaluator.load_ground_truth("legacy-type")

    assert result["status"] == "invalid_ground_truth"
    assert result["error_code"] == "unsupported_file_type"


def test_corrupt_uploads_have_stable_parse_errors(tmp_path):
    invalid_json = tmp_path / "broken.json"
    invalid_json.write_bytes(b"{")
    invalid_xlsx = tmp_path / "broken.xlsx"
    invalid_xlsx.write_bytes(b"not an xlsx")

    for path, code in ((invalid_json, "invalid_json"), (invalid_xlsx, "invalid_excel")):
        with pytest.raises(GroundTruthValidationError) as exc_info:
            load_ground_truth_upload(path, TASK)
        _assert_error(exc_info, code)


def test_xlsx_uses_first_visible_sheet_and_splits_both_semicolon_forms(tmp_path):
    workbook = Workbook()
    hidden = workbook.active
    hidden.title = "隐藏"
    hidden.append(["错误", "表头"])
    hidden.sheet_state = "hidden"
    visible = workbook.create_sheet("标准答案")
    visible.append(["类别", "名称", "别名"])
    visible.append(["型号", "LEAP", "LEAP-1A; LEAP-1B；LEAP"])
    path = tmp_path / "visible.xlsx"
    workbook.save(path)

    canonical = load_ground_truth_upload(path, TASK)

    assert canonical["entities"] == [
        {"type": "model", "name": "LEAP", "aliases": ["leap1a", "leap1b", "leap"]}
    ]

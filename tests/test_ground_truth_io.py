import json
import math
from io import BytesIO

import pytest
from openpyxl import Workbook

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
    injected_path = _write_json(tmp_path, _valid_payload(task=None))
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

"""HTTP boundary tests for ground-truth uploads and saved-report reevaluation."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

import main
from gpt_researcher.evaluation.ground_truth_io import ground_truth_path_for_task


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture
def ground_truth_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "entity_ground_truths"
    monkeypatch.setattr(main, "EVALUATION_GROUND_TRUTH_DIR", directory)
    return directory


def _valid_json(task: str, name: str = "Pratt & Whitney") -> bytes:
    return json.dumps(
        {"task": task, "entities": [{"type": "机构", "name": name}]},
        ensure_ascii=False,
    ).encode("utf-8")


def _valid_xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "标准答案"
    sheet.append(["类别", "名称", "别名", "数值", "单位", "容差"])
    sheet.append(["参数", "起飞推力", "推力", 33110, "lbf", 0.01])
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    return content.getvalue()


@pytest.mark.parametrize(
    ("filename", "content", "expected_code"),
    [
        ("truth.txt", b"not-json", "unsupported_file_type"),
        ("truth.json", b"", "empty_file"),
        ("truth.json", b"{broken", "invalid_json"),
        ("truth.xlsx", b"not-a-workbook", "invalid_excel"),
    ],
)
def test_upload_rejects_invalid_files_with_safe_validation_detail(
    client: TestClient,
    ground_truth_dir: Path,
    filename: str,
    content: bytes,
    expected_code: str,
) -> None:
    response = client.post(
        "/api/evaluation-ground-truth",
        data={"task": "GTF"},
        files={"file": (filename, content, "application/octet-stream")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == expected_code
    assert "not-a-workbook" not in response.text
    assert not list(ground_truth_dir.glob("*"))


def test_upload_requires_task(client: TestClient) -> None:
    response = client.post(
        "/api/evaluation-ground-truth",
        files={"file": ("truth.json", _valid_json("GTF"), "application/json")},
    )

    assert response.status_code == 422


def test_upload_rejects_oversized_body_before_parsing(
    client: TestClient,
    ground_truth_dir: Path,
) -> None:
    response = client.post(
        "/api/evaluation-ground-truth",
        data={"task": "GTF"},
        files={
            "file": (
                "truth.json",
                b"x" * (main.MAX_EVALUATION_UPLOAD_BYTES + 1),
                "application/json",
            )
        },
    )

    assert response.status_code == 413
    assert "5 MiB" in response.json()["detail"]
    assert not list(ground_truth_dir.glob("*"))


def test_upload_rejects_task_mismatch(
    client: TestClient,
    ground_truth_dir: Path,
) -> None:
    response = client.post(
        "/api/evaluation-ground-truth",
        data={"task": "GTF"},
        files={"file": ("truth.json", _valid_json("LEAP"), "application/json")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "task_mismatch"
    assert not list(ground_truth_dir.glob("*"))


def test_json_upload_uses_hashed_server_name_and_returns_digest(
    client: TestClient,
    ground_truth_dir: Path,
) -> None:
    task = "GTF 技术评估"
    content = _valid_json(task)

    response = client.post(
        "/api/evaluation-ground-truth",
        data={"task": task},
        files={
            "file": (
                "../../private/Authorization=Bearer-secret.json",
                content,
                "application/json",
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "stored_name": ground_truth_path_for_task(task, ground_truth_dir).name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "entity_count": 1,
        "category_counts": {"organization": 1},
    }
    assert "private" not in response.text
    assert "secret" not in response.text
    stored = (ground_truth_dir / payload["stored_name"]).resolve()
    assert stored.parent == ground_truth_dir.resolve()
    assert json.loads(stored.read_text(encoding="utf-8"))["task"] == task


def test_xlsx_upload_and_replacement_update_the_active_task_file(
    client: TestClient,
    ground_truth_dir: Path,
) -> None:
    task = "GTF"
    first = client.post(
        "/api/evaluation-ground-truth",
        data={"task": task},
        files={"file": ("truth.xlsx", _valid_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    replacement_content = _valid_json(task, "RTX")
    second = client.post(
        "/api/evaluation-ground-truth",
        data={"task": task},
        files={"file": ("replacement.json", replacement_content, "application/json")},
    )

    assert first.status_code == 200
    assert first.json()["category_counts"] == {"parameter": 1}
    assert second.status_code == 200
    assert second.json()["stored_name"] == first.json()["stored_name"]
    assert second.json()["sha256"] == hashlib.sha256(replacement_content).hexdigest()
    stored = ground_truth_dir / second.json()["stored_name"]
    assert json.loads(stored.read_text(encoding="utf-8"))["entities"][0]["name"] == "RTX"
    assert len(list(ground_truth_dir.glob("*.json"))) == 1


class _RecordStoreStub:
    def __init__(self, record: dict | None):
        self.record = record
        self.appended: list[tuple[str, dict]] = []

    def get_run(self, run_id: str):
        return self.record

    def append_reevaluation(self, run_id: str, result: dict) -> None:
        self.appended.append((run_id, result))


def test_reevaluation_returns_404_for_unknown_run(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "evaluation_record_store", _RecordStoreStub(None))

    response = client.post("/api/report-evaluation/missing-run")

    assert response.status_code == 404
    assert response.json()["detail"] == "未找到该报告记录。"


def test_reevaluation_returns_409_when_saved_final_report_is_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordStoreStub({"task": "GTF", "evidence_report": "  "})
    monkeypatch.setattr(main, "evaluation_record_store", store)

    response = client.post("/api/report-evaluation/run-1")

    assert response.status_code == 409
    assert response.json()["detail"] == "该记录缺少可重新测评的最终正文。"
    assert store.appended == []


def test_reevaluation_uses_current_ground_truth_without_starting_research(
    client: TestClient,
    ground_truth_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = "GTF"
    active_path = ground_truth_path_for_task(task, ground_truth_dir)
    active_path.parent.mkdir(parents=True)
    active_path.write_bytes(_valid_json(task))
    record = {
        "task": task,
        "evidence_report": "# 最终正文\n\n结论。[来源](https://example.test)",
        "report_style_cleanup": {"removed_count": 2},
    }
    store = _RecordStoreStub(record)
    result = {
        "status": "completed",
        "evaluation_summary": {"status": "completed"},
        "evaluation_report_paths": {"word": "/outputs/evaluations/a.docx"},
    }
    evaluator = AsyncMock(return_value=result)

    monkeypatch.setattr(main, "evaluation_record_store", store)
    monkeypatch.setattr(main, "evaluate_saved_report", evaluator)
    monkeypatch.setattr(
        main,
        "ThreeAgentService",
        lambda *_args, **_kwargs: pytest.fail("reevaluation must not start research"),
    )

    response = client.post("/api/report-evaluation/run-1")

    assert response.status_code == 200
    assert response.json() == result
    evaluator.assert_awaited_once_with(
        task=task,
        run_id="run-1",
        report=record["evidence_report"],
        style_cleanup=record["report_style_cleanup"],
        ground_truth_path=active_path,
        selected_sources=None,
        output_dir=main.EVALUATION_REPORT_DIR,
    )
    assert store.appended == [("run-1", result)]


def test_reevaluation_returns_409_when_saved_task_is_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordStoreStub({"evidence_report": "最终正文"})
    monkeypatch.setattr(main, "evaluation_record_store", store)

    response = client.post("/api/report-evaluation/run-1")

    assert response.status_code == 409
    assert response.json()["detail"] == "该记录缺少可重新测评的任务信息。"
    assert store.appended == []


def test_reevaluation_reuses_saved_sources_and_hides_internal_paths(
    client: TestClient,
    ground_truth_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = "GTF"
    active_path = ground_truth_path_for_task(task, ground_truth_dir)
    active_path.parent.mkdir(parents=True)
    active_path.write_bytes(_valid_json(task))
    store = _RecordStoreStub({
        "task": task,
        "evidence_report": "# 正文\n\n结论。",
        "selected_source_files": ["spec.pdf"],
    })
    evaluator = AsyncMock(return_value={"evaluation_summary": {"status": "completed"}})
    monkeypatch.setattr(main, "evaluation_record_store", store)
    monkeypatch.setattr(main, "evaluate_saved_report", evaluator)

    response = client.post("/api/report-evaluation/run-1")

    assert response.status_code == 200
    assert evaluator.await_args.kwargs["selected_sources"] == ["spec.pdf"]
    assert str(active_path.parent) not in response.text
    assert str(tmp_path.parent) not in response.text


def test_reevaluation_with_real_store_appends_history_and_returns_partial(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gpt_researcher.evaluation.records import EvaluationRecordStore

    store = EvaluationRecordStore(tmp_path / "records.json")
    store.append_run({
        "run_id": "run-live",
        "task": "GTF",
        "evidence_report": "# 正文\n\n结论。",
        "report_style_cleanup": {"removed_count": 1},
    })
    monkeypatch.setattr(main, "evaluation_record_store", store)
    monkeypatch.setattr(
        main, "evaluate_saved_report", AsyncMock(return_value={"status": "partial"})
    )

    response = client.post("/api/report-evaluation/run-live")

    assert response.status_code == 200
    assert store.get_run("run-live")["reevaluations"] == [{"status": "partial"}]


def test_reevaluation_returns_500_with_safe_detail_when_orchestration_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordStoreStub({"task": "GTF", "evidence_report": "正文"})
    monkeypatch.setattr(main, "evaluation_record_store", store)
    monkeypatch.setattr(
        main, "evaluate_saved_report", AsyncMock(side_effect=RuntimeError("db secret leaked"))
    )

    response = client.post("/api/report-evaluation/run-1")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "evaluation_failed"
    assert "db secret leaked" not in response.text


def test_reevaluation_still_returns_result_when_history_append_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingStore:
        def get_run(self, run_id: str):
            return {"task": "GTF", "evidence_report": "正文"}

        def append_reevaluation(self, run_id: str, result: dict) -> None:
            raise OSError("disk full")

    result = {"evaluation_summary": {"status": "completed"}}
    monkeypatch.setattr(main, "evaluation_record_store", _FailingStore())
    monkeypatch.setattr(main, "evaluate_saved_report", AsyncMock(return_value=result))

    response = client.post("/api/report-evaluation/run-1")

    assert response.status_code == 200
    assert response.json() == result


def test_reevaluation_returns_partial_result_when_one_component_failed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = _RecordStoreStub({"task": "GTF", "evidence_report": "最终正文"})
    partial = {
        "status": "partial",
        "evaluation_summary": {
            "status": "partial",
            "errors": [{"component": "claim_support", "code": "evaluation_failed"}],
        },
        "evaluation_report_paths": {"word": "/outputs/evaluations/partial.docx"},
    }
    evaluator = AsyncMock(return_value=partial)
    monkeypatch.setattr(main, "evaluation_record_store", store)
    monkeypatch.setattr(main, "evaluate_saved_report", evaluator)
    monkeypatch.setattr(main, "EVALUATION_GROUND_TRUTH_DIR", tmp_path)

    response = client.post("/api/report-evaluation/run-partial")

    assert response.status_code == 200
    assert response.json() == partial
    assert store.appended == [("run-partial", partial)]

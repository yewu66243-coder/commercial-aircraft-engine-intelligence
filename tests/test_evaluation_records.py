from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from gpt_researcher.evaluation.records import EvaluationRecordStore


def test_empty_store_returns_no_run_without_creating_the_file(tmp_path):
    path = tmp_path / "evaluation_records.json"
    store = EvaluationRecordStore(path)

    assert store.get_run("missing") is None
    assert not path.exists()


def test_append_run_is_queryable_by_run_id_and_persisted_atomically(tmp_path):
    path = tmp_path / "evaluation_records.json"
    store = EvaluationRecordStore(path)
    record = {"run_id": "run-1", "task": "GTF", "evidence_report": "# Clean"}

    assert store.append_run(record) == str(path)
    assert store.get_run("run-1") == {**record, "reevaluations": []}
    assert store.get_run("unknown") is None
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {**record, "reevaluations": []}
    ]
    assert not list(tmp_path.glob(".evaluation-records-*.tmp"))


def test_corrupt_store_is_backed_up_before_a_new_run_is_written(tmp_path):
    path = tmp_path / "evaluation_records.json"
    path.write_text("{not-json", encoding="utf-8")
    store = EvaluationRecordStore(path)

    store.append_run({"run_id": "run-after-corruption", "task": "GTF"})

    backups = list(tmp_path.glob("evaluation_records.broken_*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not-json"
    assert store.get_run("run-after-corruption")["task"] == "GTF"


def test_concurrent_appends_are_serialized_without_lost_records(tmp_path):
    path = tmp_path / "evaluation_records.json"
    stores = [EvaluationRecordStore(path) for _ in range(4)]

    def append(index: int) -> None:
        stores[index % len(stores)].append_run({"run_id": f"run-{index}"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(40)))

    records = json.loads(path.read_text(encoding="utf-8"))
    assert {item["run_id"] for item in records} == {f"run-{index}" for index in range(40)}
    assert len(records) == 40


def test_append_reevaluation_preserves_original_and_appends_history(tmp_path):
    store = EvaluationRecordStore(tmp_path / "evaluation_records.json")
    original = {"run_id": "run-1", "evaluation_summary": {"status": "completed"}}
    store.append_run(original)

    first = store.append_reevaluation("run-1", {"evaluated_at": "2026-01-01", "score": 0.8})
    second = store.append_reevaluation("run-1", {"evaluated_at": "2026-01-02", "score": 0.9})

    assert first["evaluation_summary"] == original["evaluation_summary"]
    assert second["evaluation_summary"] == original["evaluation_summary"]
    assert second["reevaluations"] == [
        {"evaluated_at": "2026-01-01", "score": 0.8},
        {"evaluated_at": "2026-01-02", "score": 0.9},
    ]


def test_append_reevaluation_rejects_unknown_run(tmp_path):
    store = EvaluationRecordStore(tmp_path / "evaluation_records.json")

    with pytest.raises(KeyError, match="unknown"):
        store.append_reevaluation("unknown", {"evaluated_at": "2026-01-01"})


def test_failed_atomic_replace_leaves_existing_store_readable(tmp_path, monkeypatch):
    path = tmp_path / "evaluation_records.json"
    store = EvaluationRecordStore(path)
    store.append_run({"run_id": "stable"})
    before = path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("disk full")

    monkeypatch.setattr("gpt_researcher.evaluation.records.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        store.append_run({"run_id": "not-written"})

    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".evaluation-records-*.tmp"))

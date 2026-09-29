"""Atomic persistence for report evaluation runs and reevaluation history."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _default_records_path() -> Path:
    return Path(__file__).resolve().parents[2] / "outputs" / "records" / "evaluation_records.json"


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False)).casefold()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


class EvaluationRecordStore:
    """Store a JSON list using per-path locking and atomic replacement."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _default_records_path()
        self._lock = _lock_for(self.path)

    @staticmethod
    def _clone(value: Any) -> Any:
        return copy.deepcopy(value)

    def _backup_corrupt(self) -> None:
        if not self.path.exists():
            return
        backup = self.path.with_name(
            f"{self.path.stem}.broken_{time.time_ns()}_{uuid.uuid4().hex[:8]}{self.path.suffix}"
        )
        shutil.copy2(self.path, backup)

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise ValueError("evaluation record root must be a list of objects")
            return payload
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
            self._backup_corrupt()
            return []

    def _write_unlocked(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".evaluation-records-", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(records, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validated_record(record: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise TypeError("evaluation record must be an object")
        run_id = record.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("evaluation record requires a non-empty run_id")
        cloned = EvaluationRecordStore._clone(record)
        history = cloned.setdefault("reevaluations", [])
        if not isinstance(history, list):
            raise ValueError("reevaluations must be a list")
        return cloned

    def append_run(self, record: dict[str, Any]) -> str:
        prepared = self._validated_record(record)
        with self._lock:
            records = self._read_unlocked()
            records.append(prepared)
            self._write_unlocked(records)
        return str(self.path)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            records = self._read_unlocked()
            for record in reversed(records):
                if record.get("run_id") == run_id:
                    result = self._clone(record)
                    result.setdefault("reevaluations", [])
                    return result
        return None

    def append_reevaluation(self, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise TypeError("reevaluation result must be an object")
        prepared = self._clone(result)
        with self._lock:
            records = self._read_unlocked()
            for index in range(len(records) - 1, -1, -1):
                if records[index].get("run_id") != run_id:
                    continue
                history = records[index].setdefault("reevaluations", [])
                if not isinstance(history, list):
                    raise ValueError("reevaluations must be a list")
                history.append(prepared)
                self._write_unlocked(records)
                return self._clone(records[index])
        raise KeyError(run_id)


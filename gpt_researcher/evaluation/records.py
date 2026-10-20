"""Atomic persistence for report evaluation runs and reevaluation history."""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any


LOGGER = logging.getLogger(__name__)

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

    @contextlib.contextmanager
    def _locked(self):
        """Serialize the server and offline review CLI, not only Python threads."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(self.path.suffix + '.lock').open('a+b') as stream:
                stream.seek(0, os.SEEK_END)
                if not stream.tell():
                    stream.write(b'0')
                    stream.flush()
                deadline = time.monotonic() + 60
                while True:
                    try:
                        stream.seek(0)
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Evaluation records are busy; retry later')
                        time.sleep(.05)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _clone(value: Any) -> Any:
        return copy.deepcopy(value)

    def _backup_corrupt(self) -> None:
        """Best-effort copy of an unreadable file; failures must not mask the read error."""
        if not self.path.exists():
            return
        backup = self.path.with_name(
            f"{self.path.stem}.broken_{time.time_ns()}_{uuid.uuid4().hex[:8]}{self.path.suffix}"
        )
        try:
            shutil.copy2(self.path, backup)
        except OSError:
            LOGGER.warning("Could not back up unreadable evaluation records file")

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
            stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(descriptor)
            raise
        try:
            with stream:
                json.dump(records, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            with contextlib.suppress(OSError):
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
        with self._locked():
            records = self._read_unlocked()
            records.append(prepared)
            self._write_unlocked(records)
        return str(self.path)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._locked():
            records = self._read_unlocked()
            for record in reversed(records):
                if record.get("run_id") == run_id:
                    result = self._clone(record)
                    result.setdefault("reevaluations", [])
                    return result
        return None

    def list_runs(self) -> list[dict[str, Any]]:
        with self._locked():
            return self._clone(self._read_unlocked())

    def append_manual_review(self, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("mode") != "human" or result.get("run_id") != run_id:
            raise ValueError("manual review must belong to the run")
        with self._locked():
            records = self._read_unlocked()
            for record in reversed(records):
                if record.get("run_id") == run_id:
                    record.setdefault("manual_reviews", []).append(self._clone(result))
                    # Keep automatic diagnostics and historical model results intact.
                    record["manual_acceptance"] = {
                        "review_id": result["review_id"], "overall": result["overall"],
                        "report_sha256": result["report_sha256"],
                    }
                    self._write_unlocked(records)
                    return self._clone(record)
        raise KeyError(run_id)

    def append_reevaluation(self, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise TypeError("reevaluation result must be an object")
        prepared = self._clone(result)
        with self._locked():
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

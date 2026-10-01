"""Case results stored as one UTF-8 JSON file per case."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional, Union

from .models import CaseResult

CASE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class CaseStore:
    def __init__(self, directory: Union[str, Path]):
        self.directory = Path(directory)

    def _path(self, case_id: str) -> Path:
        if not CASE_ID_RE.match(case_id):
            raise ValueError(f"invalid case id: {case_id!r}")
        return self.directory / f"{case_id}.json"

    def save(self, result: CaseResult) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(result.case_id)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(result.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return target

    def list_cases(self) -> list[CaseResult]:
        """All readable stored cases, newest first. Unreadable or foreign files are skipped."""
        if not self.directory.is_dir():
            return []
        cases: list[CaseResult] = []
        for path in self.directory.glob("*.json"):
            if not CASE_ID_RE.match(path.stem):
                continue
            try:
                cases.append(CaseResult.model_validate(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
        cases.sort(key=lambda r: r.created_at, reverse=True)
        return cases

    def load(self, case_id: str) -> Optional[CaseResult]:
        path = self._path(case_id)
        if not path.is_file():
            return None
        return CaseResult.model_validate(json.loads(path.read_text(encoding="utf-8")))

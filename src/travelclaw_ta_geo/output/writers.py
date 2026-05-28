from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO


class NdjsonWriter:
    def __init__(self, path: str | Path, *, lazy: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self._handle: TextIO | None = None
        if not lazy:
            self._open()

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def write(self, item: dict[str, Any]) -> None:
        handle = self._open()
        handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        self.count += 1

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _open(self) -> TextIO:
        if self._handle is None:
            self._handle = self.path.open("a", encoding="utf-8")
        return self._handle

    def __enter__(self) -> "NdjsonWriter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


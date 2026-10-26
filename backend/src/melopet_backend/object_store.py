from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .util import new_id


class LocalObjectStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, object_ref: str) -> Path:
        candidate = (self.root / object_ref).resolve()
        if self.root not in candidate.parents:
            raise ValueError("invalid object reference")
        return candidate

    def put(self, data: bytes, *, suffix: str = ".bin") -> str:
        safe_suffix = suffix if suffix.startswith(".") and len(suffix) <= 10 else ".bin"
        object_ref = f"{new_id('obj')}{safe_suffix}"
        path = self._resolve(object_ref)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".upload-", delete=False) as handle:
                temporary_path = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return object_ref

    def get(self, object_ref: str) -> bytes:
        return self._resolve(object_ref).read_bytes()

    def delete(self, object_ref: str) -> None:
        path = self._resolve(object_ref)
        path.unlink(missing_ok=True)

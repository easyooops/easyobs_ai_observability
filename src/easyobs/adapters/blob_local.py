from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


class LocalFilesystemBlobStore:
    """Local NDJSON blob store. Swap for an S3 / Azure Blob / GCS adapter in production."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _trace_shard(self, trace_id_hex: str) -> Path:
        prefix = trace_id_hex[:2] if len(trace_id_hex) >= 2 else "00"
        return self._root / f"shard={prefix}"

    def write_trace_batch(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str:
        shard = self._trace_shard(trace_id_hex)
        shard.mkdir(parents=True, exist_ok=True)
        batch = f"{uuid.uuid4().hex}.jsonl"
        rel = shard.relative_to(self._root) / batch
        path = shard / batch
        with path.open("w", encoding="utf-8") as f:
            for obj in lines:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        return str(rel).replace("\\", "/")

    def read_batch_lines(self, batch_relpath: str) -> list[dict[str, Any]]:
        path = self._root / batch_relpath
        if not path.is_file():
            return []
        out: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out

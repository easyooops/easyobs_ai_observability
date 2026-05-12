"""Hybrid blob store: Local Parquet (7-day hot) + S3 (all data cold).

Dual-write architecture:
- Every ingest call writes to BOTH the local Parquet store (fast DuckDB scan
  for real-time queries within 7 days) and to S3 (full archive for long-term
  custom-range queries).
- Reads for preset windows (1h/6h/24h/7d) hit the local store exclusively.
- Custom-range queries that extend beyond the hot window route to S3.

The local store acts as a hot cache with configurable retention (default 7
days). A background cleanup job can be wired to prune partitions older than
the retention window.

Circuit breaker: If S3 is unreachable (e.g. local dev with no bucket),
after N consecutive failures the store stops attempting S3 writes and logs
a single warning instead of per-write stack traces.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from easyobs.adapters.blob_parquet import LocalParquetBlobStore
from easyobs.adapters.blob_s3 import S3ParquetBlobStore
from easyobs.services.app_settings import BlobConfig

_log = logging.getLogger("easyobs.blob.hybrid")

DEFAULT_HOT_RETENTION_DAYS = 7
_CIRCUIT_BREAKER_THRESHOLD = 3
_CIRCUIT_BREAKER_COOLDOWN_SEC = 300


class HybridBlobStore:
    """Dual-write blob store: local Parquet (hot) + S3 (archive).

    Includes a circuit breaker: after ``_CIRCUIT_BREAKER_THRESHOLD``
    consecutive S3 failures, further writes are skipped for
    ``_CIRCUIT_BREAKER_COOLDOWN_SEC`` seconds. This prevents log spam
    in local dev environments where no S3 bucket exists.
    """

    def __init__(
        self,
        *,
        local_root: Path,
        s3_cfg: BlobConfig,
        hot_retention_days: int = DEFAULT_HOT_RETENTION_DAYS,
    ) -> None:
        self._local = LocalParquetBlobStore(local_root)
        self._s3 = S3ParquetBlobStore(s3_cfg)
        self._hot_retention_days = hot_retention_days

        # Circuit breaker state
        self._s3_consecutive_failures = 0
        self._s3_circuit_open_until: float = 0.0

        _log.info(
            "hybrid blob store initialized",
            extra={
                "local_root": str(local_root),
                "s3_bucket": s3_cfg.bucket,
                "hot_retention_days": hot_retention_days,
            },
        )

    @property
    def root(self) -> Path:
        return self._local.root

    @property
    def storage_format(self) -> str:
        return "parquet"

    @property
    def local_store(self) -> LocalParquetBlobStore:
        return self._local

    @property
    def s3_store(self) -> S3ParquetBlobStore:
        return self._s3

    @property
    def hot_retention_days(self) -> int:
        return self._hot_retention_days

    # ------------------------------------------------------------------
    # Circuit breaker helpers
    # ------------------------------------------------------------------

    def _s3_is_available(self) -> bool:
        """Return False when the circuit breaker is open (S3 presumed down)."""
        if self._s3_circuit_open_until == 0.0:
            return True
        now = time.monotonic()
        if now >= self._s3_circuit_open_until:
            # Cooldown expired — attempt again (half-open)
            self._s3_circuit_open_until = 0.0
            self._s3_consecutive_failures = 0
            _log.info("S3 circuit breaker reset — retrying archive writes")
            return True
        return False

    def _s3_record_failure(self, exc: Exception) -> None:
        self._s3_consecutive_failures += 1
        if self._s3_consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
            self._s3_circuit_open_until = time.monotonic() + _CIRCUIT_BREAKER_COOLDOWN_SEC
            _log.warning(
                "S3 archive unreachable after %d failures; "
                "circuit breaker OPEN for %ds (local writes continue normally)",
                self._s3_consecutive_failures,
                _CIRCUIT_BREAKER_COOLDOWN_SEC,
            )
        else:
            _log.warning(
                "S3 archive write failed (%d/%d): %s",
                self._s3_consecutive_failures,
                _CIRCUIT_BREAKER_THRESHOLD,
                str(exc),
            )

    def _s3_record_success(self) -> None:
        if self._s3_consecutive_failures > 0:
            _log.info("S3 archive write recovered after %d failures", self._s3_consecutive_failures)
        self._s3_consecutive_failures = 0
        self._s3_circuit_open_until = 0.0

    # ------------------------------------------------------------------
    # Write — dual-write to both stores
    # ------------------------------------------------------------------

    def write_trace_parquet(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str:
        local_relpath = self._local.write_trace_parquet(
            trace_id_hex=trace_id_hex, lines=lines,
        )

        if self._s3_is_available():
            try:
                self._s3.write_trace_parquet(trace_id_hex=trace_id_hex, lines=lines)
                self._s3_record_success()
            except Exception as exc:
                self._s3_record_failure(exc)

        return local_relpath

    def write_trace_batch(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str:
        return self.write_trace_parquet(trace_id_hex=trace_id_hex, lines=lines)

    # ------------------------------------------------------------------
    # Read — default to local, fallback to S3 for old data
    # ------------------------------------------------------------------

    def read_batch_lines(self, batch_relpath: str) -> list[dict[str, Any]]:
        result = self._local.read_batch_lines(batch_relpath)
        if result:
            return result
        if self._s3_is_available():
            return self._s3.read_batch_lines(batch_relpath)
        return []

    # ------------------------------------------------------------------
    # Scan URIs for DuckDB queries
    # ------------------------------------------------------------------

    def scan_uri(self, pattern: str = "**/*.parquet") -> str:
        """Return the local scan URI (hot store, used for preset windows)."""
        return self._local.scan_uri(pattern)

    def scan_uri_archive(self, pattern: str = "**/*.parquet") -> str:
        """Return the S3 scan URI (cold archive, used for custom ranges beyond 7d)."""
        return self._s3.scan_uri(pattern)

    # ------------------------------------------------------------------
    # Query routing helpers
    # ------------------------------------------------------------------

    def is_within_hot_window(
        self,
        from_ts: datetime | None,
        to_ts: datetime | None,
    ) -> bool:
        """Determine whether the requested time range falls entirely within
        the hot (local) retention window.

        Rules:
        - Preset windows (1h/6h/24h/7d) always return True.
        - Custom ranges return True only if ``from_ts`` is within the
          retention window (i.e. no older than hot_retention_days from now).
        """
        if from_ts is None:
            return True
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self._hot_retention_days)
        return from_ts >= cutoff

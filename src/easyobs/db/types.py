"""Custom SQLAlchemy column types.

The single most error-prone surface in EasyObs is "what timezone does a
``datetime`` actually carry once it's been through SQLite?". Use the
``UtcDateTime`` decorator below for **every** timestamp column in
``db.models`` so the rest of the code can treat all loaded datetimes as
timezone-aware UTC unconditionally.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """``DateTime(timezone=True)`` that survives a SQLite round-trip.

    Why this exists
    ---------------
    SQLAlchemy's SQLite dialect uses a default ``storage_format`` that
    does **not** include a timezone offset. So an aware UTC ``datetime``
    written to a ``DateTime(timezone=True)`` column comes back as a
    **naive** ``datetime`` on read. From that point on,
    ``naive.astimezone(timezone.utc)`` is a footgun: Python interprets
    the naive value as **system-local time** (KST on a Korean Windows
    box) and silently shifts the timestamp by the local UTC offset.

    On a server in Asia/Seoul this manifests as "every trace timestamp
    is 9 hours in the past" -- the 1h / 6h workspace windows go empty
    and tooltips show timestamps from the previous evening.

    Behaviour
    ---------
    - On bind (write): convert aware datetimes to UTC and drop the
      tzinfo. We always store *UTC clock components* so the format SQLite
      receives is unambiguous regardless of which dialect parsed it.
      Naive values are passed through verbatim (the caller has asserted
      they're already UTC).
    - On result (read): re-attach ``timezone.utc`` if the dialect handed
      us back a naive value, otherwise normalise to UTC. Either way the
      caller receives an aware UTC datetime and ``astimezone()`` /
      ``isoformat()`` produce stable UTC output.

    The decorator works equally well on Postgres -- there
    ``DateTime(timezone=True)`` already round-trips correctly, and the
    two no-op branches in this class collapse to identity.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # Trust the caller: every internal _now() / fromtimestamp(.., tz=utc)
            # site already produces aware UTC, and the few raw constructors that
            # don't (e.g. test fixtures) pass UTC clock components anyway.
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # SQLite default storage_format dropped the offset on write;
            # we know everything we wrote was UTC, so re-tag without
            # shifting clock components.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

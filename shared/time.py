"""Time utilities for deadline management and phase timing.

The coordinator is the sole authority on deadlines; this module is the
shared vocabulary for expressing and checking them. Every function takes
an explicit ``current`` time rather than reading the clock internally, so
deadline logic stays deterministic and testable — callers pass
``shared.time.now()`` in production and a fixed value in tests.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass


def now() -> float:
    """Current wall-clock time, as Unix epoch seconds."""
    return _time.time()


@dataclass(frozen=True)
class Deadline:
    """A point in time a phase must be resolved by, as Unix epoch seconds."""

    expires_at: float

    @classmethod
    def after(cls, seconds: float, *, start: float | None = None) -> Deadline:
        """A deadline ``seconds`` after ``start`` (defaults to now)."""
        return cls(expires_at=(start if start is not None else now()) + seconds)

    def remaining(self, current: float | None = None) -> float:
        """Seconds until expiry; negative or zero once expired."""
        return self.expires_at - (current if current is not None else now())

    def is_expired(self, current: float | None = None) -> bool:
        return self.remaining(current) <= 0

    def warning_due(self, lead_seconds: float, current: float | None = None) -> bool:
        """True once within ``lead_seconds`` of expiry, but not yet expired."""
        remaining = self.remaining(current)
        return 0 < remaining <= lead_seconds

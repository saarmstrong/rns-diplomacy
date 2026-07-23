"""Protocol-level constants: version numbers, magic bytes, limits.

Central place for values shared between coordinator and client that must
stay in sync across protocol versions.
"""

from __future__ import annotations

PROTOCOL_VERSION = 1

# Maximum size, in bytes, of a single encoded protocol message. Enforced at
# both encode and decode time to prevent DoS via oversized payloads.
MAX_MESSAGE_SIZE = 64 * 1024

MAX_PLAYERS = 7

# Default phase durations, in seconds, per CLAUDE.md's deadline model.
DEFAULT_MOVEMENT_PHASE_SECONDS = 24 * 60 * 60
DEFAULT_RETREAT_PHASE_SECONDS = 12 * 60 * 60
DEFAULT_ADJUSTMENT_PHASE_SECONDS = 12 * 60 * 60

# How long before a phase deadline the coordinator sends PHASE_DEADLINE_WARNING.
DEADLINE_WARNING_LEAD_SECONDS = 60 * 60

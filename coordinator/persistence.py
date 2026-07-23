"""Coordinator state persistence — SQLite schema and data access layer.

Every piece of match state that must survive a coordinator restart lives
here: match lifecycle/phase, the coordinator's signing identity, joined
players and their faction assignments, every order revision ever
submitted, the signed phase-result hash chain, and draw votes. All
mutations are wrapped in SQLite transactions (``with self._conn:``), so a
crash mid-write leaves the database in its previous consistent state.

This module is pure data access: it stores and retrieves rows. Business
rules (faction assignment, order supersession, draw outcomes, ...) live
in ``coordinator/lobby.py``, ``coordinator/orders.py``, and
``coordinator/draws.py``, which call these primitives.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from coordinator.phases import MatchState
from engine.model import Phase
from protocol.messages import MatchStatus
from shared.identity import Identity

_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    turn INTEGER NOT NULL,
    year INTEGER NOT NULL,
    coordinator_private_key BLOB NOT NULL,
    deadline_expires_at REAL,
    paused_remaining_seconds REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    public_key BLOB NOT NULL,
    faction_id TEXT NOT NULL,
    display_name TEXT,
    joined_at REAL NOT NULL,
    PRIMARY KEY (match_id, public_key)
);

CREATE TABLE IF NOT EXISTS orders (
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    public_key BLOB NOT NULL,
    turn INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    orders_blob BLOB NOT NULL,
    order_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    submitted_at REAL NOT NULL,
    PRIMARY KEY (match_id, public_key, turn, revision)
);

CREATE TABLE IF NOT EXISTS phase_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    turn INTEGER NOT NULL,
    phase TEXT NOT NULL,
    canonical_state BLOB NOT NULL,
    state_hash TEXT NOT NULL,
    previous_state_hash TEXT,
    signature BLOB NOT NULL,
    resolved_at REAL NOT NULL,
    UNIQUE (match_id, turn, phase)
);

CREATE TABLE IF NOT EXISTS draw_votes (
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    turn INTEGER NOT NULL,
    public_key BLOB NOT NULL,
    vote INTEGER NOT NULL,
    voted_at REAL NOT NULL,
    PRIMARY KEY (match_id, turn, public_key)
);
"""


@dataclass(frozen=True)
class MatchRecord:
    """A match's persisted lifecycle/phase row."""

    match_id: str
    state: MatchState
    created_at: float


@dataclass(frozen=True)
class PlayerRecord:
    """A joined player's persisted faction assignment."""

    public_key: bytes
    faction_id: str
    display_name: str | None
    joined_at: float


@dataclass(frozen=True)
class OrderRecord:
    """One persisted order submission revision."""

    public_key: bytes
    turn: int
    revision: int
    orders_blob: bytes
    order_hash: str
    status: str
    submitted_at: float


@dataclass(frozen=True)
class PhaseResultRecord:
    """One persisted, signed phase result — a link in the hash chain."""

    turn: int
    phase: Phase
    canonical_state: bytes
    state_hash: str
    previous_state_hash: str | None
    signature: bytes
    resolved_at: float


class MatchStore:
    """SQLite-backed persistence for one coordinator's matches.

    A single database can hold multiple matches (rows are scoped by
    ``match_id``); the coordinator process typically owns one store.
    """

    def __init__(self, path: str | Path) -> None:
        database_path = Path(path)
        database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(database_path))
        # Match databases contain private coordinator keys and player mapping.
        # sqlite creates the file using the process umask, so explicitly lock
        # it down after opening (also repairs permissive legacy files).
        if str(path) != ":memory:":
            os.chmod(database_path, 0o600)
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._conn:
            self._conn.executescript(_SCHEMA)
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(matches)")}
            if "paused_remaining_seconds" not in columns:
                self._conn.execute("ALTER TABLE matches ADD COLUMN paused_remaining_seconds REAL")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MatchStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- Match lifecycle -------------------------------------------------

    def create_match(self, match_id: str, coordinator_identity: Identity, *, created_at: float) -> None:
        """Register a new match in the lobby, with a fresh coordinator identity."""
        state = MatchState()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO matches
                    (match_id, status, phase, turn, year, coordinator_private_key, deadline_expires_at, paused_remaining_seconds, created_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    match_id,
                    state.status.value,
                    state.phase.value,
                    state.turn,
                    state.year,
                    coordinator_identity.private_bytes(),
                    created_at,
                ),
            )

    def get_match(self, match_id: str) -> MatchRecord | None:
        row = self._conn.execute(
            "SELECT status, phase, turn, year, created_at FROM matches WHERE match_id = ?",
            (match_id,),
        ).fetchone()
        if row is None:
            return None
        status, phase, turn, year, created_at = row
        return MatchRecord(
            match_id=match_id,
            state=MatchState(status=MatchStatus(status), phase=Phase(phase), turn=turn, year=year),
            created_at=created_at,
        )

    def save_match_state(self, match_id: str, state: MatchState) -> None:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE matches SET status = ?, phase = ?, turn = ?, year = ? WHERE match_id = ?",
                (state.status.value, state.phase.value, state.turn, state.year, match_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"no such match: {match_id!r}")

    def load_coordinator_identity(self, match_id: str) -> Identity:
        row = self._conn.execute(
            "SELECT coordinator_private_key FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such match: {match_id!r}")
        return Identity.from_private_bytes(row[0])

    def set_deadline(self, match_id: str, deadline_expires_at: float | None) -> None:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE matches SET deadline_expires_at = ? WHERE match_id = ?",
                (deadline_expires_at, match_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"no such match: {match_id!r}")

    def pause_deadline(self, match_id: str, *, remaining_seconds: float) -> None:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE matches SET deadline_expires_at = NULL, paused_remaining_seconds = ? WHERE match_id = ?",
                (remaining_seconds, match_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"no such match: {match_id!r}")

    def resume_deadline(self, match_id: str, *, expires_at: float) -> None:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE matches SET deadline_expires_at = ?, paused_remaining_seconds = NULL WHERE match_id = ?",
                (expires_at, match_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"no such match: {match_id!r}")

    def get_paused_remaining(self, match_id: str) -> float | None:
        row = self._conn.execute(
            "SELECT paused_remaining_seconds FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such match: {match_id!r}")
        return row[0]

    def get_deadline(self, match_id: str) -> float | None:
        row = self._conn.execute(
            "SELECT deadline_expires_at FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such match: {match_id!r}")
        return row[0]

    # --- Players -----------------------------------------------------------

    def add_player(
        self,
        match_id: str,
        public_key: bytes,
        faction_id: str,
        *,
        display_name: str | None,
        joined_at: float,
    ) -> None:
        """Persist a player's faction assignment.

        Raises ``sqlite3.IntegrityError`` if this public key already joined
        this match.
        """
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO players (match_id, public_key, faction_id, display_name, joined_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (match_id, public_key, faction_id, display_name, joined_at),
            )

    def list_players(self, match_id: str) -> list[PlayerRecord]:
        rows = self._conn.execute(
            "SELECT public_key, faction_id, display_name, joined_at FROM players "
            "WHERE match_id = ? ORDER BY joined_at",
            (match_id,),
        ).fetchall()
        return [PlayerRecord(*row) for row in rows]

    def get_player_faction(self, match_id: str, public_key: bytes) -> str | None:
        row = self._conn.execute(
            "SELECT faction_id FROM players WHERE match_id = ? AND public_key = ?",
            (match_id, public_key),
        ).fetchone()
        return row[0] if row is not None else None

    # --- Orders --------------------------------------------------------

    def record_order_submission(
        self,
        match_id: str,
        public_key: bytes,
        turn: int,
        revision: int,
        orders_blob: bytes,
        order_hash: str,
        *,
        status: str,
        submitted_at: float,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO orders (match_id, public_key, turn, revision, orders_blob, order_hash, status, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (match_id, public_key, turn, revision, orders_blob, order_hash, status, submitted_at),
            )

    def update_order_status(self, match_id: str, public_key: bytes, turn: int, revision: int, status: str) -> None:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE orders SET status = ? WHERE match_id = ? AND public_key = ? AND turn = ? AND revision = ?",
                (status, match_id, public_key, turn, revision),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"no such order revision: match={match_id!r} turn={turn} revision={revision}")

    def get_latest_order(self, match_id: str, public_key: bytes, turn: int) -> OrderRecord | None:
        row = self._conn.execute(
            """
            SELECT public_key, turn, revision, orders_blob, order_hash, status, submitted_at
            FROM orders WHERE match_id = ? AND public_key = ? AND turn = ?
            ORDER BY revision DESC LIMIT 1
            """,
            (match_id, public_key, turn),
        ).fetchone()
        return OrderRecord(*row) if row is not None else None

    def get_order_revisions(self, match_id: str, public_key: bytes, turn: int) -> list[OrderRecord]:
        rows = self._conn.execute(
            """
            SELECT public_key, turn, revision, orders_blob, order_hash, status, submitted_at
            FROM orders WHERE match_id = ? AND public_key = ? AND turn = ?
            ORDER BY revision
            """,
            (match_id, public_key, turn),
        ).fetchall()
        return [OrderRecord(*row) for row in rows]

    def get_latest_orders_for_turn(self, match_id: str, turn: int) -> list[OrderRecord]:
        """The latest order revision submitted by each player for ``turn``."""
        rows = self._conn.execute(
            """
            SELECT o.public_key, o.turn, o.revision, o.orders_blob, o.order_hash, o.status, o.submitted_at
            FROM orders o
            WHERE o.match_id = ? AND o.turn = ? AND o.revision = (
                SELECT MAX(o2.revision) FROM orders o2
                WHERE o2.match_id = o.match_id AND o2.public_key = o.public_key AND o2.turn = o.turn
            )
            ORDER BY o.public_key
            """,
            (match_id, turn),
        ).fetchall()
        return [OrderRecord(*row) for row in rows]

    # --- Phase results / hash chain -----------------------------------

    def record_phase_result(
        self,
        match_id: str,
        turn: int,
        phase: Phase,
        canonical_state: bytes,
        state_hash: str,
        previous_state_hash: str | None,
        signature: bytes,
        *,
        resolved_at: float,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO phase_results
                    (match_id, turn, phase, canonical_state, state_hash, previous_state_hash, signature, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (match_id, turn, phase.value, canonical_state, state_hash, previous_state_hash, signature, resolved_at),
            )

    def get_phase_result(self, match_id: str, turn: int, phase: Phase) -> PhaseResultRecord | None:
        row = self._conn.execute(
            """
            SELECT turn, phase, canonical_state, state_hash, previous_state_hash, signature, resolved_at
            FROM phase_results WHERE match_id = ? AND turn = ? AND phase = ?
            """,
            (match_id, turn, phase.value),
        ).fetchone()
        if row is None:
            return None
        turn_, phase_, canonical_state, state_hash, previous_state_hash, signature, resolved_at = row
        return PhaseResultRecord(
            turn=turn_,
            phase=Phase(phase_),
            canonical_state=canonical_state,
            state_hash=state_hash,
            previous_state_hash=previous_state_hash,
            signature=signature,
            resolved_at=resolved_at,
        )

    def get_hash_chain(self, match_id: str) -> list[PhaseResultRecord]:
        """All phase results for a match, in chain order (oldest first)."""
        rows = self._conn.execute(
            """
            SELECT turn, phase, canonical_state, state_hash, previous_state_hash, signature, resolved_at
            FROM phase_results WHERE match_id = ? ORDER BY id
            """,
            (match_id,),
        ).fetchall()
        return [
            PhaseResultRecord(
                turn=turn,
                phase=Phase(phase),
                canonical_state=canonical_state,
                state_hash=state_hash,
                previous_state_hash=previous_state_hash,
                signature=signature,
                resolved_at=resolved_at,
            )
            for turn, phase, canonical_state, state_hash, previous_state_hash, signature, resolved_at in rows
        ]

    def get_latest_phase_result(self, match_id: str) -> PhaseResultRecord | None:
        chain = self.get_hash_chain(match_id)
        return chain[-1] if chain else None

    # --- Draws -----------------------------------------------------------

    def record_draw_vote(self, match_id: str, turn: int, public_key: bytes, vote: bool, *, voted_at: float) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO draw_votes (match_id, turn, public_key, vote, voted_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (match_id, turn, public_key)
                DO UPDATE SET vote = excluded.vote, voted_at = excluded.voted_at
                """,
                (match_id, turn, public_key, int(vote), voted_at),
            )

    def get_draw_votes(self, match_id: str, turn: int) -> dict[bytes, bool]:
        rows = self._conn.execute(
            "SELECT public_key, vote FROM draw_votes WHERE match_id = ? AND turn = ?",
            (match_id, turn),
        ).fetchall()
        return {public_key: bool(vote) for public_key, vote in rows}

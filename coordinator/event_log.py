"""Small append-only, queryable audit event log."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    game_id: str
    event_type: str
    payload: dict[str, Any]
    turn: int | None = None


class EventLog:
    """In-memory append-only log; callers may persist events through MatchStore."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    def append_event(self, game_id: str, event: Event) -> None:
        if event.game_id != game_id:
            raise ValueError("event game_id does not match log target")
        self._events.append(event)

    def get_events(self, game_id: str, since_turn: int | None = None) -> list[Event]:
        return [
            event for event in self._events
            if event.game_id == game_id and (since_turn is None or event.turn is None or event.turn >= since_turn)
        ]

"""Tests for the Deadline helper in shared/time.py."""

from __future__ import annotations

from shared.time import Deadline, now


def test_now_returns_increasing_wall_clock_time() -> None:
    a = now()
    b = now()
    assert b >= a


def test_after_computes_expiry_from_explicit_start() -> None:
    deadline = Deadline.after(3600, start=1_000_000.0)
    assert deadline.expires_at == 1_000_000.0 + 3600


def test_remaining_before_expiry() -> None:
    deadline = Deadline.after(100, start=0.0)
    assert deadline.remaining(current=40.0) == 60.0


def test_remaining_after_expiry_is_negative() -> None:
    deadline = Deadline.after(100, start=0.0)
    assert deadline.remaining(current=150.0) == -50.0


def test_is_expired_false_before_deadline() -> None:
    deadline = Deadline.after(100, start=0.0)
    assert deadline.is_expired(current=50.0) is False


def test_is_expired_true_at_and_after_deadline() -> None:
    deadline = Deadline.after(100, start=0.0)
    assert deadline.is_expired(current=100.0) is True
    assert deadline.is_expired(current=200.0) is True


def test_warning_due_within_lead_window() -> None:
    deadline = Deadline.after(100, start=0.0)
    assert deadline.warning_due(lead_seconds=20, current=85.0) is True


def test_warning_due_false_outside_lead_window() -> None:
    deadline = Deadline.after(100, start=0.0)
    assert deadline.warning_due(lead_seconds=20, current=50.0) is False


def test_warning_due_false_once_expired() -> None:
    deadline = Deadline.after(100, start=0.0)
    assert deadline.warning_due(lead_seconds=20, current=150.0) is False


def test_warning_due_true_at_exact_lead_boundary() -> None:
    deadline = Deadline.after(100, start=0.0)
    assert deadline.warning_due(lead_seconds=20, current=80.0) is True

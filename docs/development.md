# Development

## Setup

Requires Python 3.12+.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

This installs `rns`, `lxmf`, `msgpack`, `pydantic` (runtime) and `pytest`, `pytest-cov`, `ruff`
(dev), plus the two console scripts (`rns-diplomacy-coordinator`, `rns-diplomacy-client`) in
editable mode.

## Running the test suite

```bash
.venv/bin/pytest
```

The suite mirrors the package layout (`tests/engine/`, `tests/protocol/`, `tests/coordinator/`,
`tests/client/`, `tests/shared/`, `tests/integration/`). Nearly everything runs against
`InMemoryTransport` and completes in well under a second; a handful of tests in
`tests/integration/` spawn real subprocesses to exercise `ReticulumTransport` over actual TCP
loopback (see below) and take on the order of a couple of seconds.

Lint:

```bash
.venv/bin/ruff check .
```

## Test categories and what they're for

- **`tests/engine/`** — the adjudication engine. This is the most important suite in the
  project: every order type, every resolution scenario (bounces, cuts, dislodgements, retreats,
  adjustments, circular movement), and determinism itself are all tested here. The engine is a
  pure function, so these tests need no mocking at all.
- **`tests/protocol/`** — encoding round-trips and validation for every message type.
- **`tests/coordinator/`** — state machine transitions, persistence round-trips and restart
  recovery, join/order/draw handling, and `MatchCoordinator` end-to-end against
  `InMemoryTransport`.
- **`tests/client/`** — `PlayerClient` (exercised against a real `MatchCoordinator`, not a
  mock), local order composition, verification (including tamper-detection tests), negotiation.
- **`tests/integration/`** — multi-client scenarios that don't fit cleanly under one package:
  the full 7-client match flow, multi-turn repetition, coordinator restart/recovery mid-match,
  deadline-driven default orders, order revision/cancellation, and the real-network tests.

## Working with `ReticulumTransport` tests

Two hard constraints, both documented in `shared/reticulum_transport.py`'s module docstring and
worth knowing before touching this code:

1. **`RNS.Reticulum()` is a per-process singleton.** A second call in the same process raises
   `OSError: Attempt to reinitialise Reticulum`. Every test that needs a live Reticulum node
   runs it in its own subprocess (see the `_reticulum_*_worker.py` scripts in
   `tests/integration/`) — the pytest process itself never constructs a `ReticulumTransport`.
2. **Two identities sharing one process's Reticulum instance don't reliably path-find to each
   other**, even with real interfaces looped back to themselves. This is a genuine degenerate
   case (real deployments never have two "nodes" in one process); testing it properly means
   spawning real, separate processes, the same way two real machines would talk.

`tests/integration/test_reticulum_match_join.py` has a known, intermittent full-timeout failure
(documented in its own module docstring) — reproduces roughly 1 run in 4 under pytest, but the
underlying mechanism was 20/20 reliable run standalone outside pytest, and the simpler
send/receive test (`test_reticulum_transport.py`, no round-trip path discovery needed) is
consistently reliable. If this test fails, rerun it in isolation before assuming a regression.

## Manually running a match end to end

The two CLIs were extensively smoke-tested this way during development (see
`coordinator/server.py` and `client/cli.py`'s docstrings for the process model this relies on):

```bash
# Terminal 1: create and serve a match. `serve` must stay running — it's the
# only process that holds the coordinator's live network connection.
# `create` prints the coordinator's public key (also shown by `status`, and
# in `serve`'s startup log line) — players need it to join.
rns-diplomacy-coordinator create match-1
rns-diplomacy-coordinator serve match-1 --listen 0.0.0.0:4242

# Terminal 2+ (x7): each player joins with their own --data-dir.
rns-diplomacy-client --data-dir ./alice join match-1 \
    --coordinator 127.0.0.1:4242 --coordinator-pub <hex from `create`/`status`/serve's logs, or a discover call>

# Once 7 have joined (check with `coordinator list-players`):
rns-diplomacy-coordinator start match-1

# Each player composes and submits orders:
rns-diplomacy-client --data-dir ./alice order match-1 --revision 1 \
    --order "vet_peak:hold" --order "vet_shore:move:pale_ridge"

# Advance the phase manually (dev/testing — bypasses the real deadline):
rns-diplomacy-coordinator advance-phase match-1

# Any player can then check history/verify the result:
rns-diplomacy-client --data-dir ./alice history match-1
rns-diplomacy-client --data-dir ./alice verify match-1
```

`--data-dir` defaults to `.rns-diplomacy` (coordinator) / `.rns-diplomacy-client` (client) in
the current directory; each match gets its own subdirectory holding the SQLite database (or, on
the client side, session state and locally observed history) and a Reticulum config directory.

## Code conventions

- Type hints everywhere; dataclasses for data, plain functions for behavior. No premature
  abstraction — see `CLAUDE.md`'s "Agent Working Rules" for the project's full working
  conventions, which this codebase follows throughout.
- `coordinator/` and `client/` never import from each other; anything both need lives in
  `engine/` or `shared/` (see `docs/architecture.md`'s Layering section).
- The adjudication engine (`engine/adjudicator.py`) must stay a pure function: no I/O, no
  randomness, no clock reads. This is load-bearing for the entire verification model, not just
  a style preference.

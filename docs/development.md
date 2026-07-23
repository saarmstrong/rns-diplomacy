# Development

Use Python 3.12+ and install `pip install -e '.[dev]'`. Run `pytest` and `ruff check .`. The normal test transport is `InMemoryTransport`; it requires no Reticulum daemon. For live use, configure an RNS TCP interface in the host Reticulum config, construct `ReticulumTransport` with a match identity and aspect, and announce/discover before sending.

Coordinator databases include private key material and are automatically set to mode `0600`.

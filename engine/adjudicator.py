"""Order adjudication engine.

Resolves submitted orders for a phase: detects conflicts, computes
support strengths, resolves battles, and produces the resulting state
delta. Implements the core Diplomacy resolution algorithm adapted for
the custom map.
"""

# TODO: Implement adjudication
#   - resolve_orders(state, orders) -> ResolutionResult
#   - Conflict detection and strength calculation
#   - Support cutting logic
#   - Standoff / bounce handling
#   - Retreat phase generation when units are dislodged

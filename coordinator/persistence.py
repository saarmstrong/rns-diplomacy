"""Game state persistence — save and restore games.

Serializes game state to disk so games can survive coordinator restarts.
Uses a simple file-based store (SQLite or msgpack files).
"""

# TODO: Implement persistence
#   - save_game(game_id, state)
#   - load_game(game_id) -> GameState
#   - list_games() -> list of game summaries
#   - Auto-save after each phase resolution

"""Application configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./shacharya.db")
STOCKFISH_PATH: str = os.getenv("STOCKFISH_PATH", "")
CHESS_COM_USERNAME: str = os.getenv("CHESS_COM_USERNAME", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Analysis thresholds (centipawn swing) — configurable for tuning
BLUNDER_THRESHOLD: int = 200
MISTAKE_THRESHOLD: int = 100
INACCURACY_THRESHOLD: int = 50
GOOD_THRESHOLD: int = 10

# Game phase detection heuristics (documented per AGENT_CONTEXT.md)
# Opening: first 12 moves (24 plies) OR until both sides have developed 2+ minor pieces
# Endgame: total non-pawn material on board < 14 (Q=9, R=5, B=3, N=3)
# Middlegame: everything not opening or endgame
OPENING_MAX_PLIES: int = 24
ENDGAME_MAX_MATERIAL: int = 14

# Stockfish analysis settings
STOCKFISH_DEPTH: int = 15  # analysis depth per move
STOCKFISH_MULTIPV: int = 3  # top N moves to evaluate

"""SQLAlchemy database models for Shacharya."""

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    ForeignKey,
    create_engine,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session
from backend.app.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


class Player(Base):
    """A chess.com player whose games are synced."""

    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chess_com_username = Column(String, unique=True, nullable=False, index=True)
    last_synced_month = Column(
        String, nullable=True
    )  # "YYYY/MM" — tracks incremental sync
    created_at = Column(DateTime, default=datetime.utcnow)

    games = relationship("Game", back_populates="player", cascade="all, delete-orphan")


class Game(Base):
    """A single chess game pulled from chess.com."""

    __tablename__ = "games"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    chess_com_uuid = Column(
        String, unique=True, nullable=False
    )  # API uuid for dedup
    pgn = Column(Text, nullable=False)
    time_control = Column(String, nullable=True)  # e.g. "300" (5 min)
    time_class = Column(String, nullable=True)  # rapid, blitz, bullet, daily
    result = Column(String, nullable=True)  # win, loss, draw
    opponent = Column(String, nullable=True)
    opponent_rating = Column(Integer, nullable=True)
    color = Column(String, nullable=True)  # white, black
    date_played = Column(DateTime, nullable=False, index=True)
    eco = Column(String, nullable=True)  # ECO opening code
    white_player = Column(String, nullable=True)
    black_player = Column(String, nullable=True)
    termination = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    player = relationship("Player", back_populates="games")
    moves = relationship("Move", back_populates="game", cascade="all, delete-orphan")


class Move(Base):
    """Engine analysis for one move (half-move / ply) in a game."""

    __tablename__ = "moves"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False, index=True)
    player_id = Column(
        Integer, ForeignKey("players.id"), nullable=False, index=True
    )  # per AGENT_CONTEXT.md multi-user-readiness
    ply = Column(Integer, nullable=False)  # 1-indexed half-move number
    move_san = Column(String, nullable=True)  # Standard Algebraic Notation
    move_uci = Column(String, nullable=True)  # UCI format (e.g. "e2e4")

    # Centipawn evaluation (None when position is a forced mate)
    eval_before = Column(Integer, nullable=True)
    eval_after = Column(Integer, nullable=True)

    # Mate sentinel fields per AGENT_CONTEXT.md:
    # positive = side to move mates in N; negative = opponent mates in N
    mate_in_before = Column(Integer, nullable=True)
    mate_in_after = Column(Integer, nullable=True)

    eval_swing = Column(Integer, nullable=True)  # centipawn swing of the move

    # Engine's best move suggestion
    best_move_san = Column(String, nullable=True)
    best_move_uci = Column(String, nullable=True)

    # Classification: best, excellent, good, inaccuracy, mistake, blunder
    classification = Column(String, nullable=True)

    # Game phase at this ply: opening, middlegame, endgame
    phase = Column(String, nullable=True)

    # Seconds remaining on the clock (from PGN clock annotation, if present)
    clock_remaining = Column(Integer, nullable=True)

    game = relationship("Game", back_populates="moves")

    __table_args__ = (
        UniqueConstraint("game_id", "ply", name="uq_game_ply"),
    )


class WeaknessProfile(Base):
    """Cached, regeneratable snapshot of aggregated stats per player.

    Per AGENT_CONTEXT.md section 6: not computed live on every request.
    Regenerated on sync completion, stored as a JSON blob keyed by player.
    """

    __tablename__ = "weakness_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(
        Integer, ForeignKey("players.id"), nullable=False, unique=True, index=True
    )
    profile_json = Column(JSON, nullable=False)
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# Engine and session factory
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    echo=False,
)


def init_db() -> None:
    """Create all tables if they don't exist (raw create_all for Phase 0)."""
    Base.metadata.create_all(engine)


def get_session() -> Session:
    """Return a new SQLAlchemy session."""
    return Session(engine)

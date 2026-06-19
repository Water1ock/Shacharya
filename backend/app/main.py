"""FastAPI application for the Shacharya dashboard backend."""

from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from backend.app.models.schema import (
    init_db,
    get_session,
    Player,
    Game,
    Move,
    WeaknessProfile,
)

app = FastAPI(title="Shacharya", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()


# --- Response models ---


class GameSummary(BaseModel):
    id: int
    white: str
    black: str
    result: str
    color: str
    date: str
    eco: Optional[str] = None
    time_class: Optional[str] = None
    opponent: Optional[str] = None
    opponent_rating: Optional[int] = None
    moves_analyzed: int = 0


class MoveDetail(BaseModel):
    ply: int
    san: str
    uci: str
    eval_before: Optional[int]
    eval_after: Optional[int]
    eval_swing: Optional[int]
    classification: Optional[str]
    phase: Optional[str]
    clock_remaining: Optional[int]
    best_move_san: Optional[str]


# --- Endpoints ---


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/profile/{username}")
def get_profile(username: str) -> dict:
    """Return the cached Weakness Profile for a player."""
    session = get_session()
    try:
        player = (
            session.query(Player).filter_by(chess_com_username=username).first()
        )
        if not player:
            raise HTTPException(404, f"Player '{username}' not found")

        wp = (
            session.query(WeaknessProfile)
            .filter_by(player_id=player.id)
            .first()
        )
        if not wp:
            return {
                "username": username,
                "player_id": player.id,
                "profile": None,
                "message": "No profile generated yet — run sync-all first",
            }

        return {
            "username": username,
            "player_id": player.id,
            "profile": wp.profile_json,
            "generated_at": wp.generated_at.isoformat(),
        }
    finally:
        session.close()


@app.get("/api/games/{username}")
def get_games(
    username: str,
    limit: int = 100,
    offset: int = 0,
    month: Optional[str] = None,
) -> dict:
    """List games for a player, with pagination and optional month filter."""
    session = get_session()
    try:
        player = (
            session.query(Player).filter_by(chess_com_username=username).first()
        )
        if not player:
            raise HTTPException(404, f"Player '{username}' not found")

        query = (
            session.query(Game)
            .filter(Game.player_id == player.id)
            .order_by(Game.date_played.desc())
        )

        if month:
            y, m = month.split("-")
            month_start = datetime(int(y), int(m), 1, tzinfo=timezone.utc)
            if int(m) == 12:
                month_end = datetime(int(y) + 1, 1, 1, tzinfo=timezone.utc)
            else:
                month_end = datetime(int(y), int(m) + 1, 1, tzinfo=timezone.utc)
            query = query.filter(
                Game.date_played >= month_start, Game.date_played < month_end
            )

        total = query.count()
        games = query.offset(offset).limit(limit).all()

        summaries = []
        for g in games:
            move_count = session.query(Move).filter_by(game_id=g.id).count()
            summaries.append(
                {
                    "id": g.id,
                    "chess_com_uuid": g.chess_com_uuid,
                    "white": g.white_player,
                    "black": g.black_player,
                    "result": g.result,
                    "color": g.color,
                    "date": g.date_played.isoformat() if g.date_played else None,
                    "eco": g.eco,
                    "time_class": g.time_class,
                    "opponent": g.opponent,
                    "opponent_rating": g.opponent_rating,
                    "moves_analyzed": move_count,
                }
            )

        return {
            "username": username,
            "total": total,
            "limit": limit,
            "offset": offset,
            "games": summaries,
        }
    finally:
        session.close()


@app.get("/api/games/{username}/{game_id}/moves")
def get_game_moves(username: str, game_id: int) -> dict:
    """Get move-by-move analysis for a specific game."""
    session = get_session()
    try:
        player = (
            session.query(Player).filter_by(chess_com_username=username).first()
        )
        if not player:
            raise HTTPException(404, f"Player '{username}' not found")

        game = (
            session.query(Game)
            .filter_by(id=game_id, player_id=player.id)
            .first()
        )
        if not game:
            raise HTTPException(404, f"Game {game_id} not found")

        moves = (
            session.query(Move)
            .filter_by(game_id=game.id)
            .order_by(Move.ply)
            .all()
        )

        return {
            "game_id": game.id,
            "white": game.white_player,
            "black": game.black_player,
            "result": game.result,
            "color": game.color,
            "date": game.date_played.isoformat() if game.date_played else None,
            "eco": game.eco,
            "moves": [
                {
                    "ply": m.ply,
                    "san": m.move_san,
                    "uci": m.move_uci,
                    "eval_before": m.eval_before,
                    "eval_after": m.eval_after,
                    "eval_swing": m.eval_swing,
                    "classification": m.classification,
                    "phase": m.phase,
                    "clock_remaining": m.clock_remaining,
                    "best_move_san": m.best_move_san,
                }
                for m in moves
            ],
        }
    finally:
        session.close()

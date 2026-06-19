"""Weakness Profile Engine (F3) — aggregates analyzed game data into structured stats.

Per AGENT_CONTEXT.md section 6: this is a regeneratable, cached snapshot
stored as JSON keyed by player, not computed live on every request.

Critical: the analysis pipeline classifies ALL moves (player + opponent) from the
player's perspective. An opponent "blunder" means the opponent blundered (good for
the player). This engine filters to ONLY the player's own moves using ply parity
(odd plies for white player, even plies for black player).
"""

import logging
from datetime import datetime, timezone
from collections import defaultdict

from sqlalchemy import func
from backend.app.models.schema import (
    Game,
    Move,
    WeaknessProfile,
    get_session,
)

logger = logging.getLogger(__name__)

PRESSURE_BUCKETS = [
    ("<30s", 0, 30),
    ("30s–2min", 30, 120),
    (">2min", 120, float("inf")),
]

GOOD_CLASSIFICATIONS = {"best", "excellent", "good"}


def _is_player_move(move: Move, game_color_map: dict[int, str]) -> bool:
    """Return True if this move was made by the player (not the opponent).

    White player: odd plies (1, 3, 5, ...)
    Black player: even plies (2, 4, 6, ...)
    """
    color = game_color_map.get(move.game_id)
    if color is None:
        return True  # fallback: include if we can't determine
    if color == "white":
        return move.ply % 2 == 1
    return move.ply % 2 == 0


def _fetch_player_moves(
    session, player_id: int
) -> tuple[list[Move], dict[int, str]]:
    """Fetch all analyzed moves for a player and the game color map.

    Returns (moves, {game_id: color}) where moves are only the player's own moves.
    """
    # Build color map from games
    games = (
        session.query(Game.id, Game.color)
        .filter(Game.player_id == player_id)
        .all()
    )
    color_map = {g.id: g.color for g in games}

    # Fetch all moves for this player (joins aren't needed — filter in Python)
    all_moves = (
        session.query(Move)
        .filter(Move.player_id == player_id)
        .all()
    )

    player_moves = [m for m in all_moves if _is_player_move(m, color_map)]
    return player_moves, color_map


def _compute_phase_stats(moves: list[Move]) -> dict:
    """Compute blunder/mistake/inaccuracy counts per game phase."""
    phases = defaultdict(
        lambda: {"total": 0, "blunders": 0, "mistakes": 0, "inaccuracies": 0}
    )
    for m in moves:
        phase = m.phase or "unknown"
        phases[phase]["total"] += 1
        if m.classification == "blunder":
            phases[phase]["blunders"] += 1
        elif m.classification == "mistake":
            phases[phase]["mistakes"] += 1
        elif m.classification == "inaccuracy":
            phases[phase]["inaccuracies"] += 1

    result = {}
    for phase, counts in phases.items():
        t = counts["total"]
        result[phase] = {
            "total_moves": t,
            "blunders": counts["blunders"],
            "blunder_rate": round(counts["blunders"] / t * 100, 1) if t else 0,
            "mistakes": counts["mistakes"],
            "mistake_rate": round(counts["mistakes"] / t * 100, 1) if t else 0,
            "inaccuracies": counts["inaccuracies"],
            "inaccuracy_rate": round(counts["inaccuracies"] / t * 100, 1) if t else 0,
        }
    return result


def _compute_pressure_stats(moves: list[Move]) -> dict:
    """Compute blunder rate per time-pressure bucket."""
    buckets = {}
    for label, lo, hi in PRESSURE_BUCKETS:
        bucket_moves = [
            m
            for m in moves
            if m.clock_remaining is not None and lo <= m.clock_remaining < hi
        ]
        total = len(bucket_moves)
        blunders = sum(1 for m in bucket_moves if m.classification == "blunder")
        mistakes = sum(1 for m in bucket_moves if m.classification == "mistake")
        inaccs = sum(1 for m in bucket_moves if m.classification == "inaccuracy")
        buckets[label] = {
            "total_moves": total,
            "blunders": blunders,
            "blunder_rate": round(blunders / total * 100, 1) if total else 0,
            "mistakes": mistakes,
            "mistake_rate": round(mistakes / total * 100, 1) if total else 0,
            "inaccuracies": inaccs,
            "inaccuracy_rate": round(inaccs / total * 100, 1) if total else 0,
        }
    return buckets


def _compute_opening_stats(
    session, player_id: int, color_map: dict[int, str]
) -> list[dict]:
    """Compute per-opening win rate, accuracy, and blunder rate.

    Uses batch move fetching (single query) instead of N+1 per-game queries.
    Only counts the player's own moves (filtered by ply parity).
    """
    games = (
        session.query(Game)
        .filter(Game.player_id == player_id, Game.eco != None)
        .all()
    )

    if not games:
        return []

    # Batch-fetch all moves for these games (single query)
    game_ids = [g.id for g in games]
    all_moves = (
        session.query(Move).filter(Move.game_id.in_(game_ids)).all()
    )

    # Group moves by game_id and filter to player moves
    moves_by_game = defaultdict(list)
    for m in all_moves:
        if _is_player_move(m, color_map):
            moves_by_game[m.game_id].append(m)

    opening_data = defaultdict(
        lambda: {
            "games": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "total_moves": 0,
            "good_moves": 0,
            "blunders": 0,
            "color_white": 0,
            "color_black": 0,
        }
    )

    for game in games:
        eco = game.eco
        opening_data[eco]["games"] += 1
        if game.result == "win":
            opening_data[eco]["wins"] += 1
        elif game.result == "loss":
            opening_data[eco]["losses"] += 1
        else:
            opening_data[eco]["draws"] += 1
        if game.color == "white":
            opening_data[eco]["color_white"] += 1
        else:
            opening_data[eco]["color_black"] += 1

        for m in moves_by_game.get(game.id, []):
            opening_data[eco]["total_moves"] += 1
            if m.classification in GOOD_CLASSIFICATIONS:
                opening_data[eco]["good_moves"] += 1
            if m.classification == "blunder":
                opening_data[eco]["blunders"] += 1

    result = []
    for eco, data in sorted(
        opening_data.items(), key=lambda x: x[1]["games"], reverse=True
    ):
        g = data["games"]
        tm = data["total_moves"]
        result.append(
            {
                "eco": eco,
                "games": g,
                "wins": data["wins"],
                "losses": data["losses"],
                "draws": data["draws"],
                "win_rate": round(data["wins"] / g * 100, 1) if g else 0,
                "primary_color": (
                    "white"
                    if data["color_white"] >= data["color_black"]
                    else "black"
                ),
                "avg_accuracy": round(data["good_moves"] / tm * 100, 1) if tm else 0,
                "blunder_rate": round(data["blunders"] / tm * 100, 1) if tm else 0,
            }
        )
    return result


def _compute_trend(
    session, player_id: int, color_map: dict[int, str]
) -> list[dict]:
    """Monthly accuracy/blunder trend over time (player moves only)."""
    months = (
        session.query(
            func.strftime("%Y-%m", Game.date_played).label("month"),
            func.count(Game.id).label("games"),
        )
        .filter(Game.player_id == player_id)
        .group_by("month")
        .order_by("month")
        .all()
    )

    # Fetch all games with date info for month lookup
    all_games = (
        session.query(Game.id, Game.date_played)
        .filter(Game.player_id == player_id)
        .all()
    )
    game_month = {
        g.id: f"{g.date_played.year:04d}-{g.date_played.month:02d}"
        for g in all_games
    }

    # Batch-fetch all moves for all games
    game_ids = [g.id for g in all_games]
    all_moves = (
        session.query(Move).filter(Move.game_id.in_(game_ids)).all()
    )

    # Group player moves by month
    month_moves = defaultdict(list)
    for m in all_moves:
        if _is_player_move(m, color_map):
            month_label = game_month.get(m.game_id, "unknown")
            month_moves[month_label].append(m)

    trend = []
    for month_row in months:
        month = month_row.month
        moves = month_moves.get(month, [])
        total = len(moves)
        blunders = sum(1 for m in moves if m.classification == "blunder")
        good = sum(1 for m in moves if m.classification in GOOD_CLASSIFICATIONS)
        swings = [
            m.eval_swing
            for m in moves
            if m.eval_swing is not None and m.eval_swing > 0
        ]
        avg_swing = round(sum(swings) / len(swings), 1) if swings else 0

        trend.append(
            {
                "month": month,
                "games": month_row.games,
                "total_moves": total,
                "avg_swing": avg_swing,
                "blunder_rate": round(blunders / total * 100, 1) if total else 0,
                "accuracy": round(good / total * 100, 1) if total else 0,
            }
        )
    return trend


def generate_profile(player_id: int) -> dict:
    """Generate the full Weakness Profile for a player.

    Only counts the player's own moves (filtered by ply parity).
    Returns the profile dict (also stores in DB).
    """
    session = get_session()
    try:
        player_moves, color_map = _fetch_player_moves(session, player_id)

        if not player_moves:
            logger.warning("No player moves found for player %d", player_id)
            profile = {
                "player_id": player_id,
                "total_games_analyzed": 0,
                "total_moves_analyzed": 0,
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        else:
            total_moves = len(player_moves)
            total_blunders = sum(
                1 for m in player_moves if m.classification == "blunder"
            )
            total_mistakes = sum(
                1 for m in player_moves if m.classification == "mistake"
            )
            total_inaccs = sum(
                1 for m in player_moves if m.classification == "inaccuracy"
            )
            total_good = sum(
                1 for m in player_moves if m.classification in GOOD_CLASSIFICATIONS
            )
            biggest_swing = max(
                (
                    m.eval_swing
                    for m in player_moves
                    if m.eval_swing is not None and m.eval_swing > 0
                ),
                default=0,
            )

            by_phase = _compute_phase_stats(player_moves)
            by_pressure = _compute_pressure_stats(player_moves)
            by_opening = _compute_opening_stats(session, player_id, color_map)
            trend = _compute_trend(session, player_id, color_map)

            worst_phase = max(
                by_phase.items(),
                key=lambda x: x[1].get("blunder_rate", 0),
                default=("unknown", {"blunder_rate": 0}),
            )[0]

            worst_pressure = max(
                by_pressure.items(),
                key=lambda x: x[1].get("blunder_rate", 0),
                default=("unknown", {"blunder_rate": 0}),
            )[0]

            analyzed_games = len({m.game_id for m in player_moves})

            profile = {
                "player_id": player_id,
                "total_games_analyzed": analyzed_games,
                "total_moves_analyzed": total_moves,
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
                "by_phase": by_phase,
                "by_time_pressure": by_pressure,
                "by_opening": by_opening,
                "trend": trend,
                "overall": {
                    "blunder_rate": (
                        round(total_blunders / total_moves * 100, 1)
                        if total_moves
                        else 0
                    ),
                    "mistake_rate": (
                        round(total_mistakes / total_moves * 100, 1)
                        if total_moves
                        else 0
                    ),
                    "inaccuracy_rate": (
                        round(total_inaccs / total_moves * 100, 1)
                        if total_moves
                        else 0
                    ),
                    "accuracy": (
                        round(total_good / total_moves * 100, 1)
                        if total_moves
                        else 0
                    ),
                    "total_blunders": total_blunders,
                    "total_mistakes": total_mistakes,
                    "total_inaccuracies": total_inaccs,
                    "biggest_swing": biggest_swing,
                    "most_blunder_phase": worst_phase,
                    "most_blunder_pressure": worst_pressure,
                },
            }

        # Upsert into DB
        existing = (
            session.query(WeaknessProfile).filter_by(player_id=player_id).first()
        )
        if existing:
            existing.profile_json = profile
            existing.generated_at = datetime.now(tz=timezone.utc)
        else:
            wp = WeaknessProfile(
                player_id=player_id,
                profile_json=profile,
                generated_at=datetime.now(tz=timezone.utc),
            )
            session.add(wp)

        session.commit()
        logger.info(
            "Profile generated for player %d: %d games, %.1f%% accuracy",
            player_id,
            profile.get("total_games_analyzed", 0),
            profile.get("overall", {}).get("accuracy", 0),
        )
        return profile

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

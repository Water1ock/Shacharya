"""Weakness Profile Engine (F3) — aggregates analyzed game data into structured stats.

Per AGENT_CONTEXT.md section 6: this is a regeneratable, cached snapshot
stored as JSON keyed by player, not computed live on every request.
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

# Time-pressure buckets in seconds
PRESSURE_BUCKETS = [
    ("<30s", 0, 30),
    ("30s–2min", 30, 120),
    (">2min", 120, float("inf")),
]

# Accuracy: what % of moves are NOT blunders/mistakes/inaccuracies?
# Only count the player's own moves (but for simplicity, count all moves
# since we classify from the player's perspective already).
GOOD_CLASSIFICATIONS = {"best", "excellent", "good"}


def _compute_phase_stats(moves) -> dict:
    """Compute blunder/mistake/inaccuracy counts per game phase."""
    phases = defaultdict(lambda: {"total": 0, "blunders": 0, "mistakes": 0, "inaccuracies": 0})
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


def _compute_pressure_stats(moves) -> dict:
    """Compute blunder rate per time-pressure bucket."""
    buckets = {}
    for label, lo, hi in PRESSURE_BUCKETS:
        bucket_moves = [
            m for m in moves
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


def _compute_opening_stats(session, player_id: int) -> list[dict]:
    """Compute per-opening win rate, accuracy, and blunder rate."""
    games = (
        session.query(Game)
        .filter(Game.player_id == player_id, Game.eco != None)
        .all()
    )

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
        key = eco
        opening_data[key]["games"] += 1
        if game.result == "win":
            opening_data[key]["wins"] += 1
        elif game.result == "loss":
            opening_data[key]["losses"] += 1
        else:
            opening_data[key]["draws"] += 1
        if game.color == "white":
            opening_data[key]["color_white"] += 1
        else:
            opening_data[key]["color_black"] += 1

        # Count moves in this game
        moves = (
            session.query(Move).filter_by(game_id=game.id).all()
        )
        for m in moves:
            opening_data[key]["total_moves"] += 1
            if m.classification in GOOD_CLASSIFICATIONS:
                opening_data[key]["good_moves"] += 1
            if m.classification == "blunder":
                opening_data[key]["blunders"] += 1

    result = []
    for eco, data in sorted(
        opening_data.items(),
        key=lambda x: x[1]["games"],
        reverse=True,
    ):
        g = data["games"]
        result.append(
            {
                "eco": eco,
                "games": g,
                "wins": data["wins"],
                "losses": data["losses"],
                "draws": data["draws"],
                "win_rate": round(data["wins"] / g * 100, 1) if g else 0,
                "primary_color": "white" if data["color_white"] >= data["color_black"] else "black",
                "avg_accuracy": (
                    round(data["good_moves"] / data["total_moves"] * 100, 1)
                    if data["total_moves"]
                    else 0
                ),
                "blunder_rate": (
                    round(data["blunders"] / data["total_moves"] * 100, 1)
                    if data["total_moves"]
                    else 0
                ),
            }
        )
    return result


def _compute_trend(session, player_id: int) -> list[dict]:
    """Monthly accuracy/blunder trend over time."""
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

    trend = []
    for month_row in months:
        month = month_row.month
        # Get all moves in games from this month
        game_ids = (
            session.query(Game.id)
            .filter(
                Game.player_id == player_id,
                func.strftime("%Y-%m", Game.date_played) == month,
            )
            .subquery()
        )
        moves = (
            session.query(Move)
            .filter(Move.game_id.in_(session.query(game_ids.c.id)))
            .all()
        )
        total = len(moves)
        blunders = sum(1 for m in moves if m.classification == "blunder")
        good = sum(1 for m in moves if m.classification in GOOD_CLASSIFICATIONS)
        swings = [m.eval_swing for m in moves if m.eval_swing is not None and m.eval_swing > 0]
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

    Returns the profile dict (also stores in DB).
    """
    session = get_session()
    try:
        # Get all analyzed moves for this player
        moves = session.query(Move).filter_by(player_id=player_id).all()

        if not moves:
            logger.warning("No moves found for player %d", player_id)
            profile = {
                "player_id": player_id,
                "total_games_analyzed": 0,
                "total_moves_analyzed": 0,
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        else:
            total_moves = len(moves)
            total_blunders = sum(1 for m in moves if m.classification == "blunder")
            total_mistakes = sum(1 for m in moves if m.classification == "mistake")
            total_inaccs = sum(1 for m in moves if m.classification == "inaccuracy")
            total_good = sum(1 for m in moves if m.classification in GOOD_CLASSIFICATIONS)
            biggest_swing = max(
                (m.eval_swing for m in moves if m.eval_swing is not None and m.eval_swing > 0),
                default=0,
            )

            by_phase = _compute_phase_stats(moves)
            by_pressure = _compute_pressure_stats(moves)
            by_opening = _compute_opening_stats(session, player_id)
            trend = _compute_trend(session, player_id)

            # Find worst phase
            worst_phase = max(
                by_phase.items(),
                key=lambda x: x[1].get("blunder_rate", 0),
                default=("unknown", {"blunder_rate": 0}),
            )[0]

            # Find worst pressure bucket
            worst_pressure = max(
                by_pressure.items(),
                key=lambda x: x[1].get("blunder_rate", 0),
                default=("unknown", {"blunder_rate": 0}),
            )[0]

            profile = {
                "player_id": player_id,
                "total_games_analyzed": (
                    session.query(func.count(func.distinct(Move.game_id)))
                    .filter(Move.player_id == player_id)
                    .scalar()
                ),
                "total_moves_analyzed": total_moves,
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
                "by_phase": by_phase,
                "by_time_pressure": by_pressure,
                "by_opening": by_opening,
                "trend": trend,
                "overall": {
                    "blunder_rate": round(total_blunders / total_moves * 100, 1) if total_moves else 0,
                    "mistake_rate": round(total_mistakes / total_moves * 100, 1) if total_moves else 0,
                    "inaccuracy_rate": round(total_inaccs / total_moves * 100, 1) if total_moves else 0,
                    "accuracy": round(total_good / total_moves * 100, 1) if total_moves else 0,
                    "total_blunders": total_blunders,
                    "total_mistakes": total_mistakes,
                    "total_inaccuracies": total_inaccs,
                    "biggest_swing": biggest_swing,
                    "most_blunder_phase": worst_phase,
                    "most_blunder_pressure": worst_pressure,
                },
            }

        # Store in DB (upsert — replace existing)
        existing = (
            session.query(WeaknessProfile)
            .filter_by(player_id=player_id)
            .first()
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
            "Profile generated for player %d: %s",
            player_id,
            {k: v for k, v in profile.get("overall", {}).items()},
        )
        return profile

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

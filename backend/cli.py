"""
Shacharya CLI — command-line interface for game sync and analysis.

Usage:
    python -m backend.cli analyze <username> <YYYY> <MM>
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from backend.app.config import CHESS_COM_USERNAME
from backend.app.models.schema import (
    Player,
    Game,
    Move,
    init_db,
    get_session,
)
from backend.app.ingestion.chess_com import sync_month
from backend.app.analysis.engine import analyze_game

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("shacharya")


def _store_moves(session, game_id: int, player_id: int, moves_data: list[dict]) -> int:
    """Store analysis results into the moves table. Returns count of moves stored."""
    count = 0
    for md in moves_data:
        move = Move(
            game_id=md["game_id"],
            player_id=md["player_id"],
            ply=md["ply"],
            move_san=md["move_san"],
            move_uci=md["move_uci"],
            eval_before=md["eval_before"],
            eval_after=md["eval_after"],
            mate_in_before=md["mate_in_before"],
            mate_in_after=md["mate_in_after"],
            eval_swing=md["eval_swing"],
            best_move_san=md["best_move_san"],
            best_move_uci=md["best_move_uci"],
            classification=md["classification"],
            phase=md["phase"],
            clock_remaining=md["clock_remaining"],
        )
        session.add(move)
        count += 1

    session.commit()
    logger.info("Stored %d moves for game %d", count, game_id)
    return count


def cmd_analyze(username: str, year: int, month: int) -> None:
    """Run ingestion + analysis for one month, printing move-by-move JSON to stdout."""
    init_db()
    session = get_session()

    try:
        # 1. Sync games from chess.com
        logger.info(
            "Syncing games for %s / %04d-%02d", username, year, month
        )
        sync_result = sync_month(username, year, month)
        logger.info("Sync result: %s", json.dumps(sync_result))

        player = (
            session.query(Player)
            .filter_by(chess_com_username=username)
            .first()
        )
        if not player:
            logger.error("Player not found after sync")
            return

        # 2. Get all games for that month that need analysis (no moves yet)
        month_start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            month_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            month_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

        games = (
            session.query(Game)
            .filter(
                Game.player_id == player.id,
                Game.date_played >= month_start,
                Game.date_played < month_end,
            )
            .all()
        )

        logger.info("Found %d games in %04d-%02d", len(games), year, month)

        # 3. Analyze each game
        all_output = []
        for game in games:
            # Check if already analyzed
            existing_moves = (
                session.query(Move).filter_by(game_id=game.id).count()
            )
            if existing_moves > 0:
                logger.info(
                    "Game %s already has %d moves analyzed, skipping",
                    game.chess_com_uuid,
                    existing_moves,
                )
                continue

            logger.info(
                "Analyzing game %s (%s vs %s, %s)",
                game.chess_com_uuid,
                game.white_player,
                game.black_player,
                game.color,
            )

            moves_data = analyze_game(
                pgn_text=game.pgn,
                player_color=game.color,
                player_id=player.id,
                game_id=game.id,
            )

            if moves_data:
                _store_moves(session, game.id, player.id, moves_data)
                logger.info(
                    "Stored %d moves for game %s", len(moves_data), game.chess_com_uuid
                )

            # Prepare JSON output for this game
            game_output = {
                "game_uuid": game.chess_com_uuid,
                "white": game.white_player,
                "black": game.black_player,
                "result": game.result,
                "color": game.color,
                "date": game.date_played.isoformat() if game.date_played else None,
                "moves": moves_data,
            }
            all_output.append(game_output)

        # 4. Print JSON to stdout
        print(json.dumps(all_output, indent=2, default=str))

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shacharya — AI Chess Coach CLI",
        prog="shacharya",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # analyze subcommand
    analyze_parser = subparsers.add_parser(
        "analyze", help="Sync and analyze one month of games"
    )
    analyze_parser.add_argument("username", help="chess.com username")
    analyze_parser.add_argument("year", type=int, help="Year (e.g. 2026)")
    analyze_parser.add_argument("month", type=int, help="Month (1-12)")
    analyze_parser.set_defaults(func=_run_analyze)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    args.func(args)


def _run_analyze(args) -> None:
    cmd_analyze(args.username, args.year, args.month)


if __name__ == "__main__":
    main()

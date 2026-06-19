"""
Shacharya CLI — command-line interface for game sync and analysis.

Usage:
    python -m backend.cli analyze <username> <YYYY> <MM>
    python -m backend.cli sync-all <username>
    python -m backend.cli sync-all <username> --start 2024-01
"""

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone

from backend.app.config import CHESS_COM_USERNAME, STOCKFISH_DEPTH
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


def _parse_eco_from_pgn(pgn_text: str) -> str | None:
    """Extract ECO code from PGN headers (e.g. '\\[ECO "C54"\\]')."""
    match = re.search(r'\[ECO\s+"([^"]+)"\]', pgn_text)
    return match.group(1) if match else None


def _backfill_eco(session, player_id: int) -> int:
    """Fill in ECO codes for all games that have PGN but no ECO stored."""
    games = (
        session.query(Game)
        .filter(Game.player_id == player_id, Game.eco == None, Game.pgn != "")
        .all()
    )
    count = 0
    for game in games:
        eco = _parse_eco_from_pgn(game.pgn)
        if eco:
            game.eco = eco
            count += 1
    if count:
        session.commit()
        logger.info("Backfilled ECO for %d games", count)
    return count


def _analyze_unanalyzed_games(
    session, player_id: int, username: str, month_label: str | None = None
) -> dict:
    """Analyze all unanalyzed games for a player, optionally filtered by month.

    Returns {analyzed, moves_stored, errors}."""
    player = session.query(Player).filter_by(id=player_id).first()
    if not player:
        return {"analyzed": 0, "moves_stored": 0, "errors": 0}

    query = session.query(Game).filter(Game.player_id == player_id)

    if month_label:
        year, month = month_label.split("/")
        month_start = datetime(int(year), int(month), 1, tzinfo=timezone.utc)
        m = int(month)
        y = int(year)
        if m == 12:
            month_end = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        else:
            month_end = datetime(y, m + 1, 1, tzinfo=timezone.utc)
        query = query.filter(
            Game.date_played >= month_start, Game.date_played < month_end
        )

    games = query.all()
    analyzed = 0
    moves_total = 0
    errors = 0

    for game in games:
        existing = session.query(Move).filter_by(game_id=game.id).count()
        if existing > 0:
            continue

        try:
            moves_data = analyze_game(
                pgn_text=game.pgn,
                player_color=game.color or "white",
                player_id=player_id,
                game_id=game.id,
            )
            if moves_data:
                _store_moves(session, game.id, player_id, moves_data)
                analyzed += 1
                moves_total += len(moves_data)
                logger.info(
                    "Game %d/%d: %s vs %s — %d moves [%s]",
                    analyzed,
                    len(games),
                    game.white_player,
                    game.black_player,
                    len(moves_data),
                    game.result or "?",
                )
        except Exception:
            logger.error("Failed to analyze game %s", game.chess_com_uuid, exc_info=True)
            errors += 1

    return {"analyzed": analyzed, "moves_stored": moves_total, "errors": errors}


def cmd_sync_all(
    username: str | None = None,
    start_year: int | None = None,
    start_month: int | None = None,
    skip_analysis: bool = False,
) -> None:
    """Sync all available months from chess.com and optionally analyze all games.

    Resumes from last_synced_month for incremental sync.
    If start_year/month provided, syncs from that point forward.
    """
    username = username or CHESS_COM_USERNAME
    if not username:
        raise ValueError("No username provided")

    init_db()
    session = get_session()

    try:
        player = (
            session.query(Player).filter_by(chess_com_username=username).first()
        )
        if not player:
            player = Player(chess_com_username=username)
            session.add(player)
            session.flush()
            logger.info("Created player %s (id=%d)", username, player.id)

        now = datetime.now(tz=timezone.utc)

        # Determine start month
        if start_year and start_month:
            y, m = start_year, start_month
        elif player.last_synced_month:
            parts = player.last_synced_month.split("/")
            y, m = int(parts[0]), int(parts[1])
            # Move to next month (last_synced is already sync'd)
            if m == 12:
                y += 1
                m = 1
            else:
                m += 1
            logger.info(
                "Resuming from last_synced=%s, starting at %04d/%02d",
                player.last_synced_month,
                y,
                m,
            )
        else:
            y, m = 2024, 1
            logger.info("No prior sync; starting from %04d/%02d", y, m)

        total_new = 0
        total_skipped = 0
        months_synced = 0
        current_y, current_m = now.year, now.month

        while (y < current_y) or (y == current_y and m <= current_m):
            logger.info("--- Syncing %04d/%02d ---", y, m)
            result = sync_month(username, y, m)
            total_new += result["new_games"]
            total_skipped += result["skipped"]
            months_synced += 1

            # Move to next month
            if m == 12:
                y += 1
                m = 1
            else:
                m += 1

            # Polite pause between API calls
            time.sleep(0.3)

        logger.info(
            "Sync complete: %d months, %d new games, %d skipped",
            months_synced,
            total_new,
            total_skipped,
        )

        # Backfill ECO codes
        eco_count = _backfill_eco(session, player.id)
        logger.info("ECO backfill: %d games updated", eco_count)

        if not skip_analysis:
            logger.info("Starting analysis of unanalyzed games...")
            logger.info("Stockfish depth: %d", STOCKFISH_DEPTH)
            result = _analyze_unanalyzed_games(session, player.id, username)
            logger.info(
                "Analysis complete: %d games, %d moves, %d errors",
                result["analyzed"],
                result["moves_stored"],
                result["errors"],
            )
        else:
            logger.info("Skipping analysis (--skip-analysis flag set)")

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

    # sync-all subcommand
    sync_parser = subparsers.add_parser(
        "sync-all", help="Sync and analyze ALL available months"
    )
    sync_parser.add_argument("username", help="chess.com username")
    sync_parser.add_argument(
        "--start",
        help="Start month as YYYY-MM (default: resume from last_synced or 2024-01)",
        default=None,
    )
    sync_parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Only sync games, don't run Stockfish analysis",
    )
    sync_parser.set_defaults(func=_run_sync_all)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    args.func(args)


def _run_analyze(args) -> None:
    cmd_analyze(args.username, args.year, args.month)


def _run_sync_all(args) -> None:
    start_year = None
    start_month = None
    if args.start:
        parts = args.start.split("-")
        start_year = int(parts[0])
        start_month = int(parts[1])
    cmd_sync_all(args.username, start_year, start_month, args.skip_analysis)


if __name__ == "__main__":
    main()

"""Stockfish analysis pipeline — move-by-move evaluation via UCI."""

import logging
import re
from typing import Optional

import chess
import chess.engine
import chess.pgn

from backend.app.config import (
    STOCKFISH_PATH,
    STOCKFISH_DEPTH,
    BLUNDER_THRESHOLD,
    MISTAKE_THRESHOLD,
    INACCURACY_THRESHOLD,
    GOOD_THRESHOLD,
    OPENING_MAX_PLIES,
    ENDGAME_MAX_MATERIAL,
)

logger = logging.getLogger(__name__)

# Piece values for game phase / material count detection
PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.QUEEN: 9,
    chess.ROOK: 5,
    chess.BISHOP: 3,
    chess.KNIGHT: 3,
    chess.PAWN: 1,
}


def _get_engine() -> chess.engine.SimpleEngine:
    """Open a Stockfish UCI engine subprocess.

    The caller is responsible for closing the engine when done.
    """
    if not STOCKFISH_PATH:
        raise RuntimeError(
            "STOCKFISH_PATH not set; add it to your .env file"
        )
    logger.info("Starting Stockfish engine: %s", STOCKFISH_PATH)
    return chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)


def _extract_clock_seconds(comment: str) -> Optional[int]:
    """Extract remaining clock seconds from a PGN clock annotation.

    PGN clock annotations look like: [%clk 0:05:23] or [%clk 1:45:03.5]
    Returns total seconds as int, or None if not found.
    """
    match = re.search(r'\[%clk\s+(\d+):(\d+):(\d+(?:\.\d+)?)\]', comment)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        return int(hours * 3600 + minutes * 60 + seconds)
    return None


def _extract_eval_score(
    info: chess.engine.InfoDict,
    player_color: chess.Color,
) -> tuple[int | None, int | None]:
    """Extract centipawn and mate-in values from an engine info dict.

    Returns (centipawns, mate_in) where exactly one is non-None.
    centipawns is from the perspective of player_color.
    mate_in > 0 means player_color mates in N; negative means player_color is mated in N.
    """
    score = info.get("score")
    if score is None:
        return None, None

    relative_score = score.relative
    if relative_score.is_mate():
        # python-chess: positive = side to move mates. Convert to player_color perspective.
        mate_in = relative_score.mate()
        if info.get("turn") != player_color:
            mate_in = -mate_in
        return None, mate_in
    else:
        cp = relative_score.score()
        if cp is not None and info.get("turn") != player_color:
            cp = -cp
        return cp, None


def _detect_game_phase(board: chess.Board, ply: int) -> str:
    """Classify game phase as opening, middlegame, or endgame.

    Heuristic (documented per AGENT_CONTEXT.md):
    - Opening: first 24 plies (12 moves) OR both sides lack 2+ developed minor pieces
    - Endgame: total non-pawn material on board < 14 points (Q=9, R=5, B=3, N=3)
    - Middlegame: everything else

    This heuristic will need revisiting as we gather data.
    """
    # Opening = first N plies or undeveloped
    if ply <= OPENING_MAX_PLIES:
        return "opening"

    # Count minor pieces still on their starting squares
    # "Developed" = a minor piece has moved from its start square
    # For simplicity, use the ply-based opener for now
    # More sophisticated: check if bishops haven't moved from c1/f1 or c8/f8
    # and knights haven't moved from b1/g1 or b8/g8

    # Endgame check: total non-pawn material
    non_pawn_material = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece and piece.piece_type != chess.PAWN:
            non_pawn_material += PIECE_VALUES.get(piece.piece_type, 0)

    if non_pawn_material < ENDGAME_MAX_MATERIAL:
        return "endgame"

    return "middlegame"


def _classify_move(
    player_uci: str | None,
    best_uci: str | None,
    swing: int | None,
) -> str:
    """Classify a move based on how close it is to the engine's best move.

    Classification:
    - "best": player played engine's top move
    - "excellent": different move but eval swing < 10 cp
    - "good": swing < 50 cp
    - "inaccuracy": swing < 100 cp
    - "mistake": swing < 200 cp
    - "blunder": swing >= 200 cp

    If swing is None (mate score involved), falls back to move comparison only.
    """
    if player_uci and best_uci and player_uci == best_uci:
        return "best"

    if swing is None:
        return "unknown"

    if swing <= GOOD_THRESHOLD:
        return "excellent"
    elif swing <= INACCURACY_THRESHOLD:
        return "good"
    elif swing <= MISTAKE_THRESHOLD:
        return "inaccuracy"
    elif swing <= BLUNDER_THRESHOLD:
        return "mistake"
    else:
        return "blunder"


def analyze_game(
    pgn_text: str,
    player_color: str,
    player_id: int,
    game_id: int,
) -> list[dict]:
    """Analyze a single game move-by-move using Stockfish.

    Args:
        pgn_text: The raw PGN string from chess.com.
        player_color: 'white' or 'black' — the user's color in this game.
        player_id: The Player DB id.
        game_id: The Game DB id.

    Returns:
        A list of dicts, each representing one move's analysis ready for DB insertion.
    """
    # Parse PGN
    pgn_io = chess.pgn.StringIO(pgn_text)
    game = chess.pgn.read_game(pgn_io)
    if game is None:
        logger.warning("Could not parse PGN for game %d", game_id)
        return []

    board = game.board()
    color = chess.WHITE if player_color == "white" else chess.BLACK

    engine = _get_engine()
    moves_data = []

    try:
        for node in game.mainline():
            move = node.move
            san = board.san(move)
            uci = move.uci()
            ply = board.ply() + 1  # 1-indexed
            comment = node.comment or ""

            # --- Evaluate position BEFORE the move ---
            info_before = engine.analyse(
                board,
                chess.engine.Limit(depth=STOCKFISH_DEPTH),
            )
            cp_before, mate_before = _extract_eval_score(info_before, color)

            # Get engine's best move
            best_move = info_before.get("pv")
            best_move_san = None
            best_move_uci = None
            if best_move:
                best_move_uci = best_move[0].uci()
                best_move_san = board.san(best_move[0])
            else:
                logger.debug("No PV from engine at ply %d", ply)

            # --- Make the actual move and evaluate AFTER ---
            board.push(move)

            info_after = engine.analyse(
                board,
                chess.engine.Limit(depth=STOCKFISH_DEPTH),
            )
            cp_after, mate_after = _extract_eval_score(info_after, color)

            # --- Compute eval swing ---
            swing = None
            if cp_before is not None and cp_after is not None:
                # For White player: eval_before - eval_after (positive = worse position)
                # For Black player: eval_after - eval_before (positive = worse position)
                if player_color == "white":
                    swing = cp_before - cp_after
                else:
                    swing = cp_after - cp_before
                # Clamp swing at 0 (no negative swings — if position improved, swing = 0)
                if swing < 0:
                    swing = 0

            # --- Classification ---
            classification = _classify_move(uci, best_move_uci, swing)

            # --- Game phase ---
            phase = _detect_game_phase(board, ply)

            # --- Clock remaining ---
            clock_remaining = _extract_clock_seconds(comment)

            moves_data.append({
                "game_id": game_id,
                "player_id": player_id,
                "ply": ply,
                "move_san": san,
                "move_uci": uci,
                "eval_before": cp_before,
                "eval_after": cp_after,
                "mate_in_before": mate_before,
                "mate_in_after": mate_after,
                "eval_swing": swing,
                "best_move_san": best_move_san,
                "best_move_uci": best_move_uci,
                "classification": classification,
                "phase": phase,
                "clock_remaining": clock_remaining,
            })

        logger.info(
            "Analyzed %d moves for game %d (%s)",
            len(moves_data),
            game_id,
            "white" if player_color == "white" else "black",
        )

    finally:
        engine.quit()

    return moves_data

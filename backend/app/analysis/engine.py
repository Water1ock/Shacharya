"""Stockfish analysis pipeline — move-by-move evaluation via UCI."""

import io
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
    board_turn: chess.Color,
) -> tuple[int | None, int | None]:
    """Extract centipawn and mate-in values from an engine info dict.

    Returns (centipawns, mate_in) where exactly one is non-None.
    centipawns is from the perspective of player_color.
    mate_in > 0 means player_color mates in N; negative means player_color is mated in N.

    Uses board_turn to correctly convert score.relative (which is from
    side-to-move's perspective) to player_color's perspective.
    """
    pov_score = info.get("score")
    if pov_score is None:
        return None, None

    relative = pov_score.relative  # Cp or Mate from side-to-move's perspective
    if relative.is_mate():
        mate_in = relative.mate()
        if board_turn != player_color:
            mate_in = -mate_in
        return None, mate_in
    else:
        cp = relative.score()
        if cp is not None and board_turn != player_color:
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

    # Phase 0 uses a ply-only heuristic for opening detection.
    # Future: also check minor piece development (bishops from c1/f1/c8/f8,
    # knights from b1/g1/b8/g8) as mentioned in config.

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
    pgn_io = io.StringIO(pgn_text)
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
            cp_before, mate_before = _extract_eval_score(info_before, color, board.turn)

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
            cp_after, mate_after = _extract_eval_score(info_after, color, board.turn)

            # --- Compute eval swing ---
            # cp_before and cp_after are already from the player's perspective
            # (converted by _extract_eval_score), so swing = cp_before - cp_after
            # works for both colors: positive swing = player's position got worse
            swing = None
            if cp_before is not None and cp_after is not None:
                swing = cp_before - cp_after
                if swing < 0:
                    swing = 0  # clamp: position improved, no negative swings

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

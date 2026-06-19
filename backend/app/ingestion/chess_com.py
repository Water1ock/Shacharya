"""chess.com game ingestion — pulls games from the public API and stores them in the DB."""

import time
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from backend.app.config import CHESS_COM_USERNAME
from backend.app.models.schema import Player, Game, get_session

logger = logging.getLogger(__name__)

CHESS_COM_API_BASE = "https://api.chess.com/pub/player"
# Polite User-Agent per chess.com API conventions
USER_AGENT = f"Shacharya/0.1 (chess coach; user:{CHESS_COM_USERNAME})"


def _api_url(username: str, year: int, month: int) -> str:
    """Build the chess.com API URL for a given year/month."""
    return f"{CHESS_COM_API_BASE}/{username}/games/{year:04d}/{month:02d}"


def fetch_month_games(
    username: str, year: int, month: int
) -> list[dict]:
    """Fetch all games for a given username and month from the chess.com API.

    Returns the raw list of game dicts from the API response.
    Returns an empty list if no games are found for that month.
    """
    url = _api_url(username, year, month)
    logger.info("Fetching games from chess.com: %s", url)

    response = httpx.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )

    if response.status_code == 429:
        # Rate limited — wait and retry once
        logger.warning("Rate limited by chess.com API; waiting 10s before retry...")
        time.sleep(10)
        response = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30.0,
        )

    if response.status_code == 404:
        logger.warning(
            "No data for %s/%04d/%02d (user not found or no games)", username, year, month
        )
        return []

    response.raise_for_status()
    data = response.json()
    games = data.get("games", [])
    logger.info("Fetched %d games for %s/%04d/%02d", len(games), username, year, month)
    return games


def _parse_game_date(end_time: Optional[int]) -> datetime:
    """Convert chess.com POSIX end_time to a datetime."""
    if end_time is None:
        return datetime.now(tz=timezone.utc)
    return datetime.fromtimestamp(end_time, tz=timezone.utc)


def _determine_color(game: dict, username: str) -> str:
    """Return 'white', 'black', or 'unknown' for the given username."""
    white = game.get("white", {})
    black = game.get("black", {})
    if white.get("username", "").lower() == username.lower():
        return "white"
    elif black.get("username", "").lower() == username.lower():
        return "black"
    return "unknown"


def _determine_result(game: dict, username: str) -> str:
    """Determine the game result from the perspective of the given username.

    Reads the result field from chess.com's per-player-side data and maps
    it to a canonical 'win' / 'loss' / 'draw' value.
    """
    white = game.get("white", {})
    black = game.get("black", {})
    color = _determine_color(game, username)
    if color == "white":
        result_str = white.get("result", "")
    elif color == "black":
        result_str = black.get("result", "")
    else:
        logger.warning(
            "Could not determine color for %s in game vs %s/%s; result will be unknown",
            username,
            white.get("username", "?"),
            black.get("username", "?"),
        )
        result_str = ""
    result_map = {
        "win": "win",
        "checkmated": "loss",
        "resigned": "loss",
        "timeout": "loss",
        "abandoned": "loss",
        "stalemate": "draw",
        "insufficient": "draw",
        "agreed": "draw",
        "repetition": "draw",
        "50move": "draw",
        "timevsinsufficient": "draw",
    }
    return result_map.get(result_str, result_str or "unknown")


def _determine_opponent(game: dict, username: str) -> tuple[str, int | None]:
    """Return (opponent_username, opponent_rating) from the game data."""
    white = game.get("white", {})
    black = game.get("black", {})
    if white.get("username", "").lower() == username.lower():
        opponent = black
    elif black.get("username", "").lower() == username.lower():
        opponent = white
    else:
        logger.warning(
            "Could not determine opponent for %s (white=%s, black=%s)",
            username,
            white.get("username", "?"),
            black.get("username", "?"),
        )
        return "unknown", None
    return opponent.get("username", "unknown"), opponent.get("rating")


def store_game(
    session, player: Player, game_data: dict, username: str
) -> Game | None:
    """Store a single game from chess.com API data into the DB.

    Returns the Game object if stored, or None if the game already exists.
    """
    uuid = game_data.get("uuid")
    if not uuid:
        logger.warning("Game has no UUID, skipping: %s", game_data.get("url"))
        return None

    # Check for duplicate
    existing = session.query(Game).filter_by(chess_com_uuid=uuid).first()
    if existing:
        logger.debug("Game %s already stored, skipping", uuid)
        return None

    opponent_name, opponent_rating = _determine_opponent(game_data, username)
    color = _determine_color(game_data, username)
    result = _determine_result(game_data, username)
    date_played = _parse_game_date(game_data.get("end_time"))

    game = Game(
        player_id=player.id,
        chess_com_uuid=uuid,
        pgn=game_data.get("pgn", ""),
        time_control=game_data.get("time_control"),
        time_class=game_data.get("time_class"),
        result=result,
        opponent=opponent_name,
        opponent_rating=opponent_rating,
        color=color,
        date_played=date_played,
        eco=None,  # parsed from PGN later if needed
        white_player=game_data.get("white", {}).get("username"),
        black_player=game_data.get("black", {}).get("username"),
        termination=None,
    )
    session.add(game)
    return game


def sync_month(
    username: str | None = None,
    year: int | None = None,
    month: int | None = None,
) -> dict:
    """Sync one month of games for a user.

    If month params are omitted, uses the current month.
    Creates the Player record if it doesn't exist.
    Returns a summary dict: {new_games, skipped, total, month_key}.
    """
    username = username or CHESS_COM_USERNAME
    if not username:
        raise ValueError(
            "No username provided; set CHESS_COM_USERNAME in .env or pass as argument"
        )

    now = datetime.now(tz=timezone.utc)
    year = year or now.year
    month = month or now.month

    session = get_session()
    try:
        # Find or create player
        player = session.query(Player).filter_by(chess_com_username=username).first()
        if not player:
            player = Player(chess_com_username=username)
            session.add(player)
            session.flush()
            logger.info("Created player record for %s (id=%d)", username, player.id)

        # Fetch games from API
        games = fetch_month_games(username, year, month)

        new_count = 0
        skipped_count = 0

        for game_data in games:
            game = store_game(session, player, game_data, username)
            if game:
                new_count += 1
            else:
                skipped_count += 1

        # Update last synced month
        month_key = f"{year:04d}/{month:02d}"
        player.last_synced_month = month_key

        session.commit()
        logger.info(
            "Sync complete for %s/%s: %d new, %d skipped, %d total",
            username,
            month_key,
            new_count,
            skipped_count,
            len(games),
        )

        return {
            "new_games": new_count,
            "skipped": skipped_count,
            "total": len(games),
            "month_key": month_key,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# Shacharya ♟️

**Shatranja + Acharya.** An AI chess coach that analyzes your real chess.com games, finds your real weaknesses, and builds a personalized plan to fix them.

> This project is under active development. See [`PRODUCT_PLAN.md`](./PRODUCT_PLAN.md) for the full product plan and [`AGENT_CONTEXT.md`](./AGENT_CONTEXT.md) for context if you're a coding agent working on this repo.

## What it does

1. **Analyzes your games** — pulls your full chess.com history and runs every move through Stockfish.
2. **Finds your weaknesses** — aggregates blunders by game phase, time pressure, opening, and tactical theme.
3. **Coaches you** — a chat interface (powered by Claude) that discusses your weaknesses in plain language, grounded in your actual stats.
4. **Teaches openings** — surfaces theory for your most/least successful openings and drills you on main lines.
5. **Serves personalized puzzles** — pulls from the Lichess puzzle database, filtered toward your weak tactical themes.
6. **Builds a training plan** — a weekly plan generated from your current weaknesses, adapted based on what you actually do.
7. **Plays with you** — a skill-calibrated engine opponent, with an automatic debrief after each game.

## Status

🚧 Pre-MVP. See the Build Phases section in `PRODUCT_PLAN.md` for the current roadmap and what's shipped so far.

## Tech Stack

- **Backend:** Python (FastAPI), `python-chess`, Stockfish
- **Database:** SQLite (via SQLAlchemy)
- **Frontend:** React, `react-chessboard`
- **LLM:** Claude API (coaching chat, training plan generation, move explanations)
- **Data sources:** chess.com public API, Lichess puzzle database, Lichess opening explorer

## Getting Started

> Setup instructions will be filled in as Phase 0 lands. Expect roughly:

```bash
git clone https://github.com/<you>/shacharya.git
cd shacharya

# backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# requires a local Stockfish binary — see backend/README.md once added
uvicorn app.main:app --reload

# frontend
cd ../frontend
npm install
npm run dev
```

You'll also need:
- A chess.com username (no API key required — it's a public API)
- An Anthropic API key for the coach chat / training plan features (`ANTHROPIC_API_KEY` in `.env`)

## Project Structure (planned)

```
shacharya/
├── backend/
│   ├── app/
│   │   ├── ingestion/       # chess.com sync
│   │   ├── analysis/        # Stockfish pipeline, move classification
│   │   ├── profile/         # Weakness Profile Engine
│   │   ├── coach/           # Claude API integration (chat, plans, explanations)
│   │   ├── puzzles/         # Lichess puzzle sync + selection
│   │   ├── play/            # engine opponent module
│   │   └── models/          # DB schema
│   └── tests/
├── frontend/
│   └── src/
│       ├── pages/           # dashboard, chat, puzzles, play, openings
│       └── components/
├── PRODUCT_PLAN.md
├── AGENT_CONTEXT.md
└── README.md
```

## License

TBD.

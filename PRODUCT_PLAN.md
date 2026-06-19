# Shacharya — Product Plan

**Shatranja + Acharya = Shacharya.** An AI chess coach that analyzes your real games, finds your real weaknesses, and builds a real training plan around them.

---

## 1. Vision & Problem Statement

Most chess improvement tools fall into two camps:
- **Pure analytics** (chess.com/Lichess analysis boards): show you *what* happened, not *what to do about it*.
- **Pure content** (courses, books, YouTube): generic, not personalized to your actual mistakes.

Shacharya's thesis: **personalization beats generic content.** A coach who has seen your last 200 games and knows you hang pieces on move 18 when your clock drops below 3 minutes is more useful than a generic "Top 10 Endgame Tips" video.

### Target user (v1)
A single self-hosted user (you) in the 800–1800 Elo range on chess.com, playing mostly rapid/blitz, who wants structured, data-driven improvement rather than ad-hoc study.

### Non-goals (v1)
- Not a multi-tenant SaaS product (no billing, no public signup flow) — though the architecture shouldn't actively prevent it later.
- Not trying to beat Stockfish at engine accuracy — we *use* Stockfish, not compete with it.
- Not a real-time anti-cheat or game-integrity tool.

---

## 2. Core Feature Set

### F1 — Game Ingestion
Pull all games for a given chess.com username via the public API, store PGNs + metadata (time control, result, opponent rating, ECO code, date) in a local database. Idempotent and incremental (only fetch new months since last sync).

### F2 — Engine Analysis Pipeline
Run every game through Stockfish move-by-move. For each move, record:
- Engine eval before/after the move (centipawns, or mate score)
- Eval swing (the "cost" of the move)
- Best move per engine
- Classification: `best / excellent / good / inaccuracy / mistake / blunder`
- Game phase at that move (`opening / middlegame / endgame`) — determined by material count + move number heuristic
- Clock time remaining at that move (from PGN clock annotations, if present)

Output stored per-move in DB, plus a per-game summary (accuracy %, blunder count, biggest swing, phase of biggest swing).

### F3 — Weakness Profile Engine
Aggregates F2 data across all games into a structured **Weakness Profile**:
- Blunder rate by game phase
- Blunder rate by time-pressure bucket (e.g., <30s, 30s–2min, >2min remaining)
- Most-played openings and win rate / average accuracy per opening (both as White and Black)
- Tactical motifs most often missed (cross-referenced against puzzle themes when the missed move matches a known pattern — fork, pin, skewer, back-rank, etc.)
- Endgame type performance (K+P, rook endgames, opposite-color bishops, etc. — best-effort tagging)
- Trend over time (is accuracy improving month over month?)

This profile is the backbone of every other feature — puzzles, training plan, and coach chat all read from it.

### F4 — Coach Chat
A conversational interface (via Claude API) that:
- Has access to the structured Weakness Profile (not raw PGNs — keeps it cheap, grounded, and fast)
- Can pull up a specific game or position on request and discuss it
- Explains *why* a move was bad in plain language, not just "−2.3"
- Answers general chess questions (opening theory, strategic concepts, endgame technique)
- Has a persistent "memory" of past conversations/goals — e.g., if you said two weeks ago "I want to stop hanging pieces in time pressure," the coach should reference progress on that

### F5 — Opening Trainer
- Identify your most-played openings and your worst-performing ones
- Show theory/main lines for openings you should learn or fix, sourced from an opening database (e.g., Lichess opening explorer API, or a bundled ECO reference)
- Drill mode: play out an opening's main line against the engine, with corrections when you deviate
- (Stretch) Spaced-repetition review queue for opening lines, à la Anki

### F6 — Puzzle Trainer
- Pull from the Lichess puzzle database (CSV, themed, rated by difficulty)
- Filter/prioritize puzzles by tactical themes matching your Weakness Profile
- Track puzzle rating (separate from game rating) and accuracy trend
- Daily puzzle queue, sized to a configurable session length

### F7 — Personalized Training Plan
- Generated weekly (or on-demand) by Claude, grounded in the current Weakness Profile
- Structured output: e.g., "This week: 15 min/day rook-endgame puzzles, review Italian Game main line, play 3 rated games focusing on not moving under 10 seconds of thought in critical positions"
- Tracks completion and adapts next week's plan based on what you did/skipped and whether weak areas improved

### F8 — Play vs. Coach
- Play a full game against an engine calibrated to feel human and to your level
- v1: Stockfish with skill-level/depth limiting
- v2 (recommended upgrade): **Maia Chess** — a neural network trained on human games at specific rating bands (1100–1900), which plays much more humanly than a weakened Stockfish
- Post-game: automatic debrief using F2+F4 (analyze the just-played game immediately, chat about it)

### F9 (stretch) — Extra features worth considering
- **Live game watcher**: poll chess.com for your most recent game right after it finishes, auto-trigger analysis + debrief
- **Time-management report**: many players' real bottleneck is clock usage, not understanding — flag this explicitly if it shows up
- **Opponent prep**: before playing a known opponent, summarize their tendencies from their public game history
- **Rating-impact estimator**: "If you cut blunders in the middlegame in half, expected rating impact: +Y"
- **Voice mode** for the coach chat (nice-to-have, not core)

---

## 3. Architecture

```
                          ┌─────────────────────┐
                          │   chess.com API      │
                          └─────────┬────────────┘
                                    │ PGNs + metadata
                                    ▼
                          ┌─────────────────────┐
                          │  Ingestion Service   │  (incremental sync)
                          └─────────┬────────────┘
                                    ▼
                          ┌─────────────────────┐
                          │   Database (SQLite)  │  games, moves, profile, plans, chat history
                          └─────────┬────────────┘
                                    │
                     ┌──────────────┼───────────────┐
                     ▼              ▼               ▼
           ┌─────────────┐  ┌──────────────┐  ┌──────────────┐
           │  Stockfish   │  │  Weakness    │  │  Lichess      │
           │  Analysis    │─▶│  Profile     │  │  Puzzle DB    │
           │  Engine      │  │  Engine      │  │  (bundled/    │
           └─────────────┘  └──────┬───────┘  │  synced)      │
                                    │           └──────┬───────┘
                                    ▼                   ▼
                          ┌─────────────────────────────────┐
                          │     Claude API (Coach Layer)     │
                          │  chat / training plan / opening  │
                          │     explanations                 │
                          └─────────────┬─────────────────────┘
                                        ▼
                          ┌─────────────────────┐
                          │   Web Frontend       │  dashboard, chat, board UI, puzzles
                          │   (React)            │
                          └─────────────────────┘
```

### Key architectural decisions
1. **Claude never sees raw PGN dumps for analysis tasks.** It sees pre-computed, structured summaries (JSON) from the Weakness Profile Engine. This keeps responses grounded in actual engine numbers rather than the LLM trying to "read" a chess position from text (which it does unreliably), and keeps token costs predictable.
2. **Stockfish does all the actual chess evaluation.** Claude's job is explanation, coaching tone, planning, and conversation — not move evaluation.
3. **SQLite first.** No need for Postgres/multi-tenant complexity for a single-user tool. Schema should be clean enough to migrate later if this ever becomes multi-user.
4. **Engine runs locally as a subprocess via UCI**, not a cloud API, to avoid rate limits/cost on the (potentially) thousands of moves across a full game history.

---

## 4. Tech Stack (recommended)

| Layer | Choice | Why |
|---|---|---|
| Backend | Python (FastAPI) | `python-chess` is the best library for PGN parsing, board logic, and UCI engine communication |
| Chess engine | Stockfish (binary) + `python-chess` UCI wrapper | Free, extremely strong, well-supported |
| Human-like play | Maia Chess weights (v2 feature) | Plays like a human at a target rating, not just "weak Stockfish" |
| Database | SQLite (via SQLAlchemy) | Zero-ops, plenty for single-user scale |
| Frontend | React + `react-chessboard` | Standard, well-documented board UI component |
| LLM | Claude API (Sonnet for chat/planning) | Coaching language, training plans, conversational memory |
| Puzzle data | Lichess puzzle database (CSV export) | Free, large, themed and rated |
| Opening data | Lichess opening explorer API or bundled ECO/PGN reference | Free, no key required |
| Background jobs | Simple async task queue (e.g. `arq` or just async FastAPI background tasks) | Game sync + analysis can be slow; don't block requests |

---

## 5. Data Model (high level)

- **players** — chess.com username, last sync timestamp
- **games** — id, PGN, time control, result, opponent rating, date, ECO code, color played
- **moves** — game_id, ply, move SAN/UCI, eval_before, eval_after, eval_swing, classification, phase, clock_remaining
- **weakness_profile** — derived/cached snapshot: JSON blob of aggregated stats, regenerated on each sync
- **puzzles** — synced from Lichess puzzle DB (id, FEN, moves, themes, rating)
- **puzzle_attempts** — puzzle_id, timestamp, success, time_taken
- **training_plans** — week_start, plan JSON, completion status
- **chat_sessions / chat_messages** — for coach memory/continuity
- **played_games** (vs. coach) — separate from chess.com games; same `moves` analysis pipeline applies

---

## 6. Build Phases & Milestones

### Phase 0 — Foundation (Week 1)
- Repo scaffolding, DB schema, chess.com ingestion script, Stockfish wired up via `python-chess`
- **Done when:** you can run a CLI command and get a fully analyzed move-by-move breakdown of one game, printed as JSON

### Phase 1 — Analysis Dashboard (Weeks 2–3)
- Full game sync for a username, analysis pipeline run across all games
- Weakness Profile Engine v1 (phase/time-pressure/opening aggregation)
- Basic React dashboard showing the stats
- **Done when:** you can see your real weaknesses on a screen, backed by real data

### Phase 2 — Coach Chat (Week 4)
- Claude API integration, fed the Weakness Profile JSON
- Chat UI, persistent chat history in DB
- **Done when:** you can ask "what's my biggest weakness?" and get a grounded, specific answer referencing real stats

### Phase 3 — Puzzles (Week 5)
- Sync/bundle Lichess puzzle DB, filter by weakness themes
- Puzzle UI (board + solve flow), attempt tracking
- **Done when:** daily puzzle queue is personalized to your actual misses

### Phase 4 — Training Plan (Week 6)
- Claude-generated weekly plan grounded in profile + puzzle/play history
- Plan tracking UI
- **Done when:** a new plan generates each week and references last week's actual activity

### Phase 5 — Play vs. Coach (Weeks 7–8)
- Play UI (board + move input), Stockfish skill-limited opponent
- Post-game auto-debrief using existing analysis pipeline
- **Done when:** you can play a full game and immediately get a coached debrief

### Phase 6 — Opening Trainer + polish (Weeks 9+)
- Opening drill mode, spaced repetition (stretch)
- Maia integration for more human-like play (stretch)
- Live game watcher, time-management report, opponent prep (stretch grab-bag)

This is intentionally sequenced so that **every phase ships something usable** — you should be getting value from Shacharya from Phase 1 onward, not just at the very end.

---

## 7. Open Questions to Resolve Early
1. Single-user only, or design for future multi-user from the start? (Recommend: single-user now, but keep `player_id` as a foreign key everywhere so multi-user is a later migration, not a rewrite.)
2. Self-hosted only, or deployed somewhere (e.g. a small VPS) for access from your phone? Affects frontend hosting decisions.
3. How much engine analysis depth/time per move are you willing to wait for on a full history backfill (hundreds of games)? Trade-off between accuracy and sync time — worth making configurable.
4. Maia integration now or later? It's a nice-to-have v1 feature but adds setup complexity (model weights, separate inference path) — fine to defer to Phase 5/6.

---

## 8. Success Criteria

Shacharya is "working" when, after a sync:
1. You can name your top 3 weaknesses in your own words, and they match what the dashboard shows.
2. A puzzle session contains noticeably more puzzles from your weak themes than random ones.
3. A coach chat answer about "why did I lose this game" matches what you already know was wrong with it (or surfaces something you missed).
4. A weekly plan feels specific to you, not like generic chess advice you could've read anywhere.

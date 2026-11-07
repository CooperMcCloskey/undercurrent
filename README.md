# Undercurrent

**See the story before it breaks.** Undercurrent groups news (GDELT), social posts (Bluesky, Reddit) and Wikipedia activity into *stories*, puts them on a 3D globe, and forecasts which ones will grow over the next week.

Built at Hack the Burgh for the HRT (*Best Use of Data for Predictions & Decision Making*) and G-Research (*Make the invisible visible*) challenges.

## Quickstart

Prerequisites: Node 22.12 or newer, and [uv](https://docs.astral.sh/uv/getting-started/installation/). uv installs the right Python (3.13) by itself.

```bash
npm install            # web app + tooling
uv sync                # Python pipeline + API (first run downloads ~1.2 GB, mostly PyTorch)
cp .env.example .env   # optional
npm run dev:mock       # web only, on mock data: http://localhost:5173
npm run dev            # API on :8000 + web on :5173
```

## What you see

- **The globe:** every story is a marker coloured by its category (politics, conflict, economy...), over a heatmap of the places it is happening. Click a category in the legend to hide or show it.
- **Hover a story** to outline its area and burst out a dot for every place each of its articles mentions, with each place named. That is why the heat is where it is. Dots are sized by how central the article is to the story.
- **Click a story** to fly to it and open a panel with every article, post and Wikipedia page in it, linked. Filter by source, sort by importance or date. Hovering a row lights its dots on the globe, and clicking a dot jumps to its row.

Until the real pipeline exports, the globe shows the mock data (`npm run dev:mock`) or the hand-written demo (`undercurrent.demo`). Neither is real news.

## Commands (from the repo root)

| Command | What it does |
|---|---|
| `npm run dev` | API + web together |
| `npm run dev:mock` | Web only, reading `web/public/mock/` (no Python needed) |
| `npm run gen:types` | Regenerate `web/src/api/types.gen.ts` from `pipeline/undercurrent/contracts.py` |
| `npm run gen:mock` | Regenerate `web/public/mock/` from `pipeline/undercurrent/mock.py` |
| `npm run check` | Typecheck + ruff + pytest |
| `npm run format` | Prettier + ruff format |
| `uv run python -m undercurrent.<area>.<module>` | Run a pipeline step |
| `uv run python -m undercurrent.demo` | Embed + cluster the hand-written demo items, score them, export to `data/export/` (then `npm run dev` shows them) |

## Repo map

```
pipeline/undercurrent/
  contracts.py     shared data contract (source of truth for every JSON shape)
  config.py        paths + shared constants
  mock.py          generates web/public/mock/
  ingest/          A: GDELT, Bluesky, Wikipedia, Reddit -> data/raw, data/processed
  vectors/         V: text cleaning + embeddings for every source -> data/processed/vectors
  stories/         B: clustering, story tracking, footprints -> data/export
  forecast/        C: labels, features, model, backtest
  api/             D: FastAPI over data/export
  demo/            hand-written items -> embed -> cluster -> export (python -m undercurrent.demo)
pipeline/tests/    pytest
web/               D: Vite + React + react-globe.gl
  src/globe/       the globe: land, heatmap, category colours, article dots
  src/story/       story panel, article ranking and dot layout
data/              gitignored; see data/README.md
notebooks/         exploration, one folder per person
docs/ARCHITECTURE.md   design, data sources, gotchas
docs/WORKPLAN.md       who builds what, in what order, and the hand-off files
PLAN.md            plan, owners, status
AGENTS.md          rules for coding agents (Claude Code reads it via CLAUDE.md)
```

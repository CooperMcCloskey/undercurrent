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

## Repo map

```
pipeline/undercurrent/
  contracts.py     shared data contract (source of truth for every JSON shape)
  config.py        paths + shared constants
  mock.py          generates web/public/mock/
  ingest/          A: GDELT, Bluesky, Wikipedia, Reddit -> data/raw, data/processed
  stories/         B: embeddings, clustering, story tracking -> data/export
  forecast/        C: labels, features, model, backtest
  api/             D: FastAPI over data/export
pipeline/tests/    pytest
web/               D: Vite + React + react-globe.gl
data/              gitignored; see data/README.md
notebooks/         exploration, one folder per person
docs/ARCHITECTURE.md   design, data sources, gotchas
PLAN.md            plan, owners, status
AGENTS.md          rules for coding agents (Claude Code reads it via CLAUDE.md)
```

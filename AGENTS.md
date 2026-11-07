# Instructions for coding agents

This file is for Claude Code (it loads this file through `CLAUDE.md`), Codex, and any other agent. Four people are working in this repo at the same time, each with their own agent. Most of these rules exist so you don't collide with the others.

## What we're building

Undercurrent is a hackathon project (Hack the Burgh). It does three things:

1. Groups GDELT news, Bluesky/Reddit posts and Wikipedia activity into **stories**, using cosine similarity of text embeddings.
2. Shows the stories on a 3D globe.
3. **Forecasts** which stories will grow next week (HRT track), and makes that early signal visible (G-Research track).

- Plan, owners and status: `PLAN.md`
- Design, data sources and known pitfalls: `docs/ARCHITECTURE.md`. Read the section for your area before writing code.

## Layout and ownership

| Path | What | Owner |
|---|---|---|
| `pipeline/undercurrent/ingest/` | Fetch and parse GDELT, Bluesky, Wikipedia, Reddit | A: Ingest |
| `pipeline/undercurrent/vectors/` | Text cleaning, embeddings for every source, vector store, similarity thresholds, Wikipedia text | V: Vectors (Cooper) |
| `pipeline/undercurrent/stories/` | Clustering, story tracking, footprints, attaching posts, export | B: Stories |
| `pipeline/undercurrent/forecast/` | Labels, features, model, backtest | C: Forecast |
| `pipeline/undercurrent/api/`, `web/` | FastAPI server, globe UI | D: Web |
| `pipeline/undercurrent/contracts.py` | Shared data contract | Everyone (see below) |
| `pipeline/undercurrent/config.py` | Paths and shared constants | Everyone, small edits only |
| `notebooks/<name>/` | Exploration | Each person has their own folder |

- **Stay inside your workstream's folders.** If you need a change in someone else's area, don't make it. Write it under "Needs from others" in your section of `PLAN.md`, tell your human, and carry on with a stub.
- New modules go in your workstream's package. Each one should run as `uv run python -m undercurrent.<area>.<module>` with `argparse` options.

## The contract (the most important rule)

Everything that crosses a workstream boundary goes through `pipeline/undercurrent/contracts.py` (Pydantic models):

1. The pipeline writes JSON in exactly those shapes to `data/export/`.
2. The API serves those files unchanged.
3. The web app reads them using `web/src/api/types.gen.ts`, which is generated from the contract.

Rules:

- **Never hand-edit `web/src/api/types.gen.ts`.**
- **To change the contract**, do all of these steps in one commit, then tell the team:
  1. Edit `contracts.py`.
  2. Run `npm run gen:types`.
  3. Update `mock.py` and run `npm run gen:mock`.
  4. Run `uv run pytest`.
- Prefer adding optional fields over renaming or removing fields. Other people are coding against the contract right now.
- `uv run pytest` validates `data/export/` against the contract once it exists. Run it after every export.

## Commands (run from the repo root)

| Command | What it does |
|---|---|
| `npm run dev` | API on :8000 and web on :5173 |
| `npm run dev:mock` | Web only, on `web/public/mock/` |
| `npm run check` | Typecheck, ruff and pytest. **Run this before every commit.** |
| `npm run format` | Prettier and ruff format |
| `npm run gen:types` / `npm run gen:mock` | Regenerate the TS types / the mock data |
| `uv run python -m undercurrent.<area>.<module>` | Run a pipeline step |

## Git workflow

- Agents commit and push only when their human says so.
- Work on `main` in small commits. Run `git pull --rebase` before every push. Push at least hourly so others aren't working against stale code.
- Commit message format: `<area>: <what>`, for example `stories: track story ids across days`.
- Run `git status` before committing and stage only files you meant to change.
- Never commit `data/`, `.env`, or notebook outputs with large embedded data.
- Never force-push and never rewrite `main`.
- **Dependencies:** use only `uv add <pkg>` or `npm install -w web <pkg>`. Commit the lockfile (`uv.lock` / `package-lock.json`) straight away in its own commit and push it. If a lockfile conflicts, keep `main`'s version, then re-run `uv lock` / `npm install`. Check the installed libraries below before adding anything new.
- If two agents work on one laptop, give each its own `git worktree` so they don't edit the same checkout.

## Installed libraries (use these first)

- **Python 3.13:**
  - `httpx` and `websockets` for HTTP and streaming
  - `duckdb`, `pandas` and `pyarrow` for tables and Parquet
  - `numpy`
  - `sentence-transformers` for embeddings; the model name is `config.EMBEDDING_MODEL`
  - `faiss-cpu` for nearest-neighbour search
  - `scikit-learn` for HDBSCAN, logistic regression, `HistGradientBoostingClassifier`, metrics and calibration
  - `fastapi` and `uvicorn`
  - `pydantic`, `python-dotenv`, `tqdm`
  - Dev: `pytest`, `ruff`, `ipykernel`, `matplotlib`
- **Web:**
  - React 19 and TypeScript 7 (strict), on Vite 8
  - `react-globe.gl` with `three`
  - `@tanstack/react-query` for server data, `zustand` for UI state
  - Tailwind CSS 4
  - `recharts` for charts
  - `d3-scale` and `d3-scale-chromatic` for colour scales
  - `date-fns`
  - `topojson-client` and `world-atlas` for country shapes
  - `lucide-react` for icons

## Conventions

- **Python:** type hints; ruff for formatting and linting. Keep I/O at the edges and pure functions in the middle.
  - Fetch scripts take `--start/--end` or `--days`, skip files already on disk (so they can resume), and never download months of data by default.
  - Import `undercurrent.config` before `sentence_transformers`, so models are cached in `data/cache/`.
  - **Point-in-time correctness:** anything computed "as of day t" may only use data from day t or earlier. The forecast is worthless otherwise.
- **TypeScript:** function components. All server data goes through `web/src/api/client.ts` and react-query. Style with Tailwind classes.
- **Data:** `data/` is gitignored; see `data/README.md` for its layout. Share large files outside git.

## Keeping PLAN.md current

When you finish a task, tick it in **your own workstream's section** of `PLAN.md`, and add a line under "Notes" if something is worth knowing. Don't edit other sections; that keeps git merges clean. Add new decisions as rows at the end of the decisions table.

## Sandboxed agents

- **Cache errors:** if uv or npm can't write to `~/.cache` or `~/.npm` (Claude Code sandbox), prefix the command with `UV_CACHE_DIR=$TMPDIR/uv-cache` or `npm_config_cache=$TMPDIR/npm-cache`.
- **No local servers:** the Claude Code sandbox may block binding local ports. Verify the API with `fastapi.testclient.TestClient` instead of running a server.
- **No network:** Codex's default sandbox has no network access. Your human must run downloads, or enable network access for the session.
- **Hugging Face model download fails** with `OSStatus -26276` or a "CAS Client Error" (the Claude Code sandbox blocks the macOS keychain): set `SSL_CERT_FILE` to certifi's bundle (`uv run python -m certifi` prints the path) and `HF_HUB_DISABLE_XET=1`. Once the model is cached in `data/cache/`, set `HF_HUB_OFFLINE=1`.

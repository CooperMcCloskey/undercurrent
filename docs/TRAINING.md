# Training the forecast (the merge): instructions for whoever runs it

**One person, on one big machine.** One command trains the model on every finished section's stories and builds the globe's final data. It's `scripts/merge.sh`; the schedule is in `docs/DATA_COVERAGE.md` ("The final merge"):

| When (2026-11-08, UTC) | Run |
|---|---|
| **Now** | the **trial**: tests it end to end on the sections already finished, and gives the first real numbers |
| **07:00** | the **final** run, the same command: it adds every section finished since the trial |
| 08:00 | data freeze (submission is at 12:00) |

## 1. The machine

| | Minimum | Good |
|---|---|---|
| CPUs | 16 vCPUs | **32 or more**: the trees and the data join use every core |
| RAM | 32 GB | 64 GB or more |
| Free disk | **every section's articles plus 20%** (the script checks; expect 100–150 GB) | 300 GB |
| GPU | not needed for the trees | an NVIDIA GPU for the neural net (`MODELS="hgb gru"`) |
| System | Linux with `git`, `uv`, `gcloud`, `tmux` | a section server that already ran `section.sh setup` has all of them |

**Bucket access:** its `gcloud` login needs **read and write** access to `gs://undercurrent-data-cooper` (it uploads the result). Section servers already have it. A new machine: run `gcloud auth login --no-launch-browser`, then send Cooper the email; Cooper grants `roles/storage.objectAdmin` with `--condition=None`.

**Not Cooper's VM:** its 4 vCPUs would take over an hour.

## 2. Run it

On a section server, or anywhere the repo is at `~/undercurrent`:

```bash
git -C ~/undercurrent pull -q && bash ~/undercurrent/scripts/merge.sh start
```

With a GPU, add the neural net and its pretraining (adds roughly 10–30 minutes):

```bash
git -C ~/undercurrent pull -q && MODELS="hgb gru" PRETRAIN=proxy bash ~/undercurrent/scripts/merge.sh start
```

A fresh machine first needs the repo and tools:

```bash
sudo apt-get update -q && sudo apt-get install -y -q git tmux
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env
git clone -q https://github.com/CooperMcCloskey/undercurrent.git ~/undercurrent
```

`gcloud` comes from https://cloud.google.com/sdk/docs/install, or the setup step of `scripts/standalone/section.sh`.

**Watch it:** `bash ~/undercurrent/scripts/merge.sh status` (or `tail -f ~/undercurrent/logs/merge.log`).

**Stop it:** `bash ~/undercurrent/scripts/merge.sh stop`. Downloads are kept, so `start` carries on later.

It runs in tmux, so closing SSH doesn't stop it. Expect about **30–60 minutes** on 32 vCPUs, mostly download, the data join and the trees. The log prints a header before each step.

## 3. What it does, step by step

1. **Finished sections.** Lists `status/*.done` in the bucket. A section is in only once its owner has run `finish`.
2. **Space check.** Stops if there isn't room for the articles.
3. **Download** (only what changed since the last run): every finished section's story tables (`story_assignments`, `stories_daily`, `story_lineage`), plus all articles and daily totals, into `data/merged/`. It then prints the merged days as continuous stretches. Gaps are fine.
   Then **Wikipedia:** it copies A's `wiki_selection.parquet` and `wiki_entity_daily.parquet` from `gs://…/wiki/` and links each story to its people and organisations (`stories.wiki_entities`). If this fails, the log says so and the forecast runs without Wikipedia features. `WIKI=0` skips it.
4. **Train and backtest** (`forecast.run --source stories`):
   - **Rows:** one per story and day, for stories with at least 20 articles in their last 7 days. Each row has 23 features (recent counts and growth, number of domains and countries, tone, age, category, and the day's total news volume, plus Wikipedia reading and editing trends for the story's people and organisations) and the story's last 28 days.
   - **Targets:** the change in the story's 7-day article count 1, 3, 7 and 14 days later, in log2 units ("2× bigger" = +1).
   - **Models:** gradient-boosted quantile trees (`hgb`), one set per horizon, giving a middle estimate and an 80% range. With `gru`, a neural net over the 28-day history as well, averaged with the trees.
   - **Backtest:** walk-forward, never training on the days it's scored on. The **final test** is the last 90 days, with up to 3 earlier quarterly windows. A 28-day calibration block before each test widens or narrows the range so that "80%" really holds about 80% of outcomes.
   - **Writes** `forecasts.parquet` (every story and day), `model_metrics.json` (the model card) and `forecast_report.json` (the full backtest) to `data/merged/`.
5. **Export:** the globe's data, with forecasts, English titles, GDELT-theme categories and junk stories left out. Then it checks it against the contract (`pytest test_contracts.py`).
6. **Upload** `export-final.tgz` and the three forecast files (to `merged/`) to the bucket.

## 4. Read the results, and post them in the chat

Step 4 ends with a table like this (the numbers here are made up):

```
 horizon        n   blend     hgb  persist  moment.  age×cat  coverage   blend vs persistence (95% CI)
      1d   41,230   0.210   0.214    0.236    0.251    0.402      0.79         -0.026 [-0.031, -0.021]
      7d   38,915   0.512   0.520    0.604    0.640    0.701      0.81         -0.092 [-0.104, -0.080]
```

| Column | Meaning | Good |
|---|---|---|
| `blend`, `hgb` (and `gru`) | the model's typical miss, in log2 units (0.5 ≈ off by 1.4×) | **smaller than `persist`** |
| `persist` | the miss of "it stays the size it is", the honest bar | |
| `moment.`, `age×cat` | two other baselines: trend continues, and the typical change for a story of that age and category | the model should beat these too |
| `coverage` | how often the 80% range held the real outcome | **close to 0.80** (0.75–0.85 is fine) |
| `blend vs persistence (95% CI)` | the model's miss minus persistence's, with a confidence interval | **the whole interval below 0** |

- **A horizon whose interval is entirely below 0** beats persistence (`beats_persistence` in `forecast_report.json`). The model card offers it as a forecast; the others are greyed out.
- **Copy the whole table into the team chat.** It's the core of the HRT pitch, and it decides which horizons the globe and the emerging stories page can trust.
- **A short horizon that doesn't beat persistence** is normal: a story's size tomorrow is mostly its size today. The value is usually at 7 and 14 days.

## 5. When something goes wrong

| Message | Fix |
|---|---|
| `Can't read gs://undercurrent-data-cooper` | `gcloud auth login --no-launch-browser`, then ask Cooper for access (step 1) |
| `Not enough disk` | free space, or use a bigger machine |
| `No scored rows in the final test window` | too few days are merged. Wait for more sections to `finish`, then run again |
| The process is killed (out of memory) | run with `MODELS=hgb` only, close other jobs, or use a machine with more RAM |
| `pytest` fails after the export | it uploads anyway and `status` says so: paste the error to Cooper |
| The export failed, or it's too big or slow | **don't rerun `start`**: `START=2025-11-07 bash ~/undercurrent/scripts/merge.sh export` redoes only the export and upload (minutes), here with just the last year |
| It takes too long for the 07:00 run | `MODELS=hgb` (no net) is the fast path; the net is optional |
| The export step is slow | It now writes 750 stories a day (`TOP`), classifies each story's category from its headlines (loads the embedding model) and splits recurring stories into episodes. `EXPORT_OPTS="--no-title-categories --no-episodes" bash ~/undercurrent/scripts/merge.sh start` turns the last two off; `TOP=300` shrinks the output. If the classifier can't load, the export carries on with the tracker's categories and says so in the log. |

**Then:** post "merge done" with the table. Cooper loads `export-final.tgz` on the demo laptops (`docs/DATA_COVERAGE.md`, "The final merge").

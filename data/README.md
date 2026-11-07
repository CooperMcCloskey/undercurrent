# data/ (gitignored)

Nothing in here is committed except this file. Everything is regenerable from the pipeline.

| Folder | Written by | Contents |
|---|---|---|
| `raw/` | Ingest | Downloads exactly as fetched (GDELT zips, API JSON). Never edited. |
| `processed/` | Ingest, Stories, Forecast | Parquet / DuckDB tables, embeddings (`.npy`), model files. |
| `export/` | Stories, Forecast | JSON in the exact API shapes from `pipeline/undercurrent/contracts.py`. The API serves this folder. |
| `cache/` | everything | Hugging Face models (`HF_HOME` points here) and other caches. |

To share a dataset with teammates, upload the files (Google Drive or AirDrop), not git.

## On the cloud

For the multi-year run (see "Scaling up" in the main `README.md`), the bucket is the source of truth and a machine's `data/` is a cache. **Mirror this layout under the bucket root**, so the same paths work locally and in the cloud (`gsutil rsync` or `aws s3 sync`).

| Path | Written by | Contents |
|---|---|---|
| `processed/articles/day=YYYY-MM-DD/` | Ingest | Deduped articles, compact columns only (Parquet) |
| `processed/vectors/<source>/<YYYY-MM-DD>.npy` and `.parquet` | Vectors | fp16, L2-normalised vectors and their ids, one file per day |
| `processed/vectors/manifest.json` | Vectors | Model, dimension and text-cleaning version. If these change, every vector must be rebuilt |
| `processed/story_assignments.parquet`, `stories_daily.parquet` | Stories | The tracker's output: the small tables that training reads |
| `processed/wiki_*.parquet` | Ingest | Wikipedia tables |
| `export/` | Stories, Forecast | The JSON the web app reads |

Rules:

- **One file per day, and skip days that already exist.** Any machine can then be restarted.
- **Keep the bucket and every machine in the same region.** Traffic inside a region is usually free; downloading to a laptop is billed per GB.
- **Vectors and raw data stay in the cloud.** Laptops pull `export/` and small tables only.
- **Don't delete from the bucket while a run is in progress.** Add new versions instead.

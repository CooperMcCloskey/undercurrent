# data/ (gitignored)

Nothing in here is committed except this file. Everything is regenerable from the pipeline.

| Folder | Written by | Contents |
|---|---|---|
| `raw/` | Ingest | Downloads exactly as fetched (GDELT zips, API JSON). Never edited. |
| `processed/` | Ingest, Stories, Forecast | Parquet / DuckDB tables, embeddings (`.npy`), model files. |
| `export/` | Stories, Forecast | JSON in the exact API shapes from `pipeline/undercurrent/contracts.py`. The API serves this folder. |
| `cache/` | everything | Hugging Face models (`HF_HOME` points here) and other caches. |

To share a dataset with teammates, upload the files (Google Drive or AirDrop), not git.

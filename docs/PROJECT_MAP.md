# Project map

## Folders you normally use

| Folder | Purpose |
|---|---|
| `app/` | User-facing CLI and future API/web entrypoints. |
| `optimizer/` | Runtime optimizer, scoring, GPU ranking, simulation, and path definitions. |
| `knowledge/` | Validated JSON knowledge and normalized source-pack tables. |
| `data_sources/` | Original source files and repeatable extraction outputs. |
| `tools/` | Extraction, validation, training, audit, and maintenance scripts. |
| `tests/` | Unit and targeted integration tests. |
| `docs/` | User-facing project and optimizer documentation. |
| `reports/` | Coverage, validation, and source-pack reports intended for review. |
| `training_outputs/` | Latest metrics plus organized raw/state/build outputs. |
| `logs/` | Runtime, trainer, OCR, and debug logs. |
| `archive/` | Historical runs, legacy docs, and old AI prompts retained for traceability. |
| `tmp/` | Disposable rendered pages, OCR scratch data, and test caches. |

## Data flow

```text
data_sources/source_pack/
        |
        v
data_sources/extracted/  ->  knowledge/source_pack/
                                   |
                                   +-> knowledge/review_queue/
                                   +-> knowledge/gpu_tables/
                                   |
                                   v
                         optimizer/ -> app/
```

## Where to find specific things

- Source database PDF: `data_sources/source_pack/source_database_map.pdf`
- Original source images: `data_sources/source_pack/raw/`
- Extracted page text: `data_sources/extracted/text/source_pack/`
- OCR evidence and source manifest: `data_sources/extracted/ocr/`
- Clean source database: `knowledge/source_pack/source_database.json` and `.csv`
- Per-domain normalized tables: `knowledge/source_pack/tables/`
- Data schemas: `knowledge/schemas/`
- Manual corrections: `knowledge/source_pack/manual_corrections.json`
- Review queues: `knowledge/review_queue/`
- GPU tables: `knowledge/gpu_tables/source_pack/numeric_tables.json`
- Optimizer entrypoint: `optimizer/main.py`
- CUDA/shared ranker: `optimizer/preprune_ranker.py`
- App entrypoint: `app/cli.py`
- Latest trainer summary: `training_outputs/latest_summary.json`
- Detailed latest metrics: `training_outputs/latest_metrics.json`
- Coverage and validation reports: `reports/`
- Historical benchmark runs: `archive/training_runs/`

## Naming standard

- Python modules and generated files use `lower_snake_case`.
- Human-facing Markdown documents use clear uppercase names where appropriate.
- Raw source filenames are retained to preserve provenance.
- Unclear root filenames are renamed only when recorded in
  `data_sources/source_manifest.json`.
- `latest_*.json` is reserved for current user-facing run summaries.
- Raw streams use `.jsonl` and live under `training_outputs/raw/`.

## Compatibility

- `run_demo.py` redirects to `app.cli`.
- `optimizer.paths.existing_path()` supports read-only legacy fallbacks where needed.
- Source renames are recorded in `data_sources/source_manifest.json`.
- Historical artifacts are moved, not deleted.

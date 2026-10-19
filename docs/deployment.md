# Running and deploying the application

The application reads the Parquet marts of `data/marts/` and the classifier of
`models/`, both committed: it runs without rebuilding anything.

## Settings

| Variable | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | empty | Enables "Explain with AI" and "Write the briefing" for alerts and views that have no text generated in advance. Without it, explanations come from the texts generated in advance (`data/marts/ai_texts.json`) or from templates. |
| `GENERATION_MODEL` | `gemini-3.5-flash-lite` | Gemini model used for live explanations. |
| `PUBLIC_DEMO` | `false` | `true` keeps each visitor's decisions in memory, for their session only. Otherwise decisions are appended to `data/decisions/decisions.sqlite`. |

Variables can also be set in a `.env` file at the root of the repository
(ignored by git). Never commit an API key.

## Locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

## Streamlit Community Cloud

1. The repository must be public (or the Streamlit account must have access to it).
2. On [share.streamlit.io](https://share.streamlit.io), create an app from the
   repository, branch `main`, main file `app/streamlit_app.py`. In the advanced
   settings, choose Python 3.13.
3. In the secrets of the app, add:

   ```toml
   PUBLIC_DEMO = "true"
   # Optional: live explanations for the alerts without a text generated in advance.
   # GEMINI_API_KEY = "..."
   ```

   Streamlit exposes top-level secrets as environment variables, which the
   application reads.

For a public demo, a dedicated API key (from a separate Google Cloud project,
with its own quota) keeps the visitors' usage apart from personal use; the
texts generated in advance already cover the alerts with the highest priority
and the national briefing.

## Docker

```bash
docker build -t procurement-intelligence .
docker run -p 8501:8501 procurement-intelligence            # http://localhost:8501
docker run -p 8501:8501 -e GEMINI_API_KEY=... procurement-intelligence
```

The image contains the application, the marts and the classifier (about
150 MB of data); it runs in public demo mode. CI builds it and checks that the
application answers its health check.

## Rebuilding the data

```bash
pip install -e ".[app,build,dev]"
python -m procurement_intel build        # about 16 minutes on 2 CPU cores, 4 GB of memory at peak
python -m procurement_intel evaluate     # injected errors, about 3 minutes
python scripts/write_results.py          # docs/results.md
```

The build reads the frozen snapshot of `data/snapshot/` and keeps its
intermediate tables in `data/cache/warehouse.duckdb` (about 3.5 GB, ignored by
git). DuckDB uses at most 4 GB of memory (`DUCKDB_MEMORY_LIMIT`) and spills to
disk beyond. A new snapshot comes from the workflow "Build the data snapshot"
(`data-snapshot.yml`).

The AI texts of the public demo come from the workflow "Generate AI texts"
(`ai-texts.yml`), which needs the repository secret `GEMINI_API_KEY`, or
locally:

```bash
GEMINI_API_KEY=... python scripts/generate_ai_texts.py --per-category 40 --delay 4
```

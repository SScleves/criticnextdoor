# filmcat — your taste catalogue

Browse ~6,000 films that matter (IMDb's own data), mark what you've seen,
give each a tier + tags + a note. Export = your taste file for the recommender.

## Run it (10 minutes + download time)

```bash
cd filmcat
pip install -r requirements.txt
python setup.py            # downloads IMDb datasets (~300 MB), builds films.db
python app.py              # opens http://localhost:8000
```

Posters (optional, recommended). Free key: https://www.themoviedb.org/settings/api

```bash
export TMDB_API_KEY=your_key      # Windows: set TMDB_API_KEY=your_key
python enrich_tmdb.py             # ~25 min for 6k films, safe to stop and re-run
```

## Tuning the universe

`python setup.py --min-votes 50000` → fewer, more famous films (≈3k)
`python setup.py --min-votes 10000 --min-rating 5.5` → bigger, includes cult trash (≈12k)
Delete `films.db` before re-running with new filters. Your marks live in the same db,
so back it up (`cp films.db films.backup.db`) before deleting.

## Workflow

1. Filter: genre Horror, status "unmarked", sort by popularity.
2. Click a poster → seen / want / not seen.
3. If seen: tier, tags (click chips, type new ones), note. Ctrl+Enter saves.
4. "export my taste (CSV)" in the header → `my_taste.csv`. That's the recommender's input.

Tiers and the starter tags are in `setup.py` (TIERS, TAGS). Edit there before first run,
or add tags live in the app.

## Files

- `films.db` — everything (films + your marks). Back this up.
- `data/` — raw IMDb files, can be deleted after setup.

# filmcat — your taste, in your words, as training data

Browse ~6,600 films that matter (IMDb's data), mark what you've seen, give each a
tier, tags (+ why it worked / − what went wrong), a note in your own words, and
answer "which did you like more?" pairs. Everything you type is versioned and
exported as jsonl: that's the input for the recommender you'll build on it.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python setup.py            # IMDb datasets (~630 MB), builds films.db   (~8 min)
.venv/bin/python import_stack.py     # Plex watch history -> seen, Radarr library -> flag
.venv/bin/python app.py              # http://localhost:8000
```

Posters (optional). Free key: https://www.themoviedb.org/settings/api

```bash
TMDB_API_KEY=your_key .venv/bin/python enrich_tmdb.py     # ~25 min, safe to stop and re-run
```

## Workflow

Three modes in the header:

- **browse** — the grid. Filter, click a poster, write it up.
  Search matches title and director. If a film isn't there, search shows a
  "search all of IMDb" button: anything with ≥1000 votes can be added in one click.
- **triage** — one film at a time, keyboard only. `S` seen (opens the write-up),
  `W` want to see, `N` not seen, `→` skip, `←` back. Put the status filter on
  "unmarked" and sort by popularity, then just go.
- **compare** — two films you've seen, "which did you like more?" `←` `→` `↓` can't say.
  Mostly picks pairs inside the same tier, because that's where the order is unknown.
  This is the strongest ranking signal you can give a model, and the fastest to answer.

The write-up (seen films): tier · how often / when / how well you remember it ·
tags, where one click = `+` (this is why it worked) and two = `−` (this is what
went wrong) · the note. Write the note for a person, not for the machine.

## Where your words live

- `films.db` — everything, sqlite. `my` is the current state, `events` is the
  append-only history of every change, `pairs` are the comparisons.
- `exports/` — rewritten after every save: `marks.jsonl`, `pairs.jsonl`,
  `events.jsonl`, `vocab.jsonl`. If `exports/` is a git repo it auto-commits,
  and pushes if a remote is set. Make that remote **private** — the notes are yours.
- `/api/export.csv` and `/api/export.json` — the same data over HTTP.
- `restore.py` — puts `exports/marks.jsonl` back into a fresh `films.db`.

So: `films.db` can be rebuilt from IMDb + `exports/` any time. Back up `exports/`.

## Tuning the universe

`python setup.py --min-votes 50000` → fewer, more famous films (≈3k)
`python setup.py --min-votes 10000 --min-rating 5.5` → bigger, includes cult trash (≈12k)
Re-running refreshes ratings and crew in place; marks are never touched.

Tiers and the starter tags are in `setup.py` (TIERS, TAGS); add tags live in the app.

## Running as a service

`filmcat.service` is a systemd user unit. `cp filmcat.service ~/.config/systemd/user/ &&
systemctl --user enable --now filmcat`. It binds 0.0.0.0:8000, so it's reachable from
the phone on the LAN / meshnet. There is no login: keep it off the public internet.

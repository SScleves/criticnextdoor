# filmcat — your taste, in your words, as training data

**Open it:** https://sscleves.github.io/criticnextdoor/ (front door: picks home wifi or meshnet). The app itself runs on the media server at `http://192.168.1.8:8000`.

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

- **for you** — unseen films scored by what they share with what you tiered: director >
  TMDB themes > genre > decade, list membership and your want list as small boosts, what you
  hated counts against. Every card says *why*. It's an explainable baseline, not the model;
  it's the thing the real recommender replaces. Needs ≥3 tiered films.

## Lists and critic scores

```bash
.venv/bin/python lists.py imdb-top                            # IMDb Top 250, computed offline (same formula)
.venv/bin/python lists.py csv "TSPDT 1000" tspdt.csv          # any CSV: tt… ids, or Name,Year columns
.venv/bin/python lists.py url "S&S 2022" https://…            # a page with imdb.com/title/tt… links, in order
.venv/bin/python lists.py letterboxd ~/letterboxd-export      # YOUR export: watched/ratings/watchlist + lists/*.csv
.venv/bin/python lists.py show
```

Lists show up as a filter ("IMDb Top 250 · seen 40/250 · avg 6.2"), as a sort ("by list rank"),
on each card, and as a boost in *for you*. Letterboxd blocks automated reads of other people's
lists, so for those: your own export, or copy the list into a `Name,Year` CSV.

Rotten Tomatoes has no API; OMDb relays its score. Free key at https://www.omdbapi.com/apikey.aspx
(1000/day → the catalogue takes ~7 days; the $1/month tier does it in one go):

```bash
OMDB_API_KEY=xxx .venv/bin/python enrich_omdb.py              # resumes where it stopped
```

Adds 🍅 and Metacritic to cards, "by Rotten Tomatoes" to the sort, `rt`/`metacritic` to exports.

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

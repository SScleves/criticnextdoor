# Critic Next Door, phase 1: the catalogue

Your taste, in your words, as training data. Browse ~6,600 films that matter (IMDb's
data), mark what you've seen, give each a tier, order the films inside a tier, tag it
(+ why it worked / − what went wrong), label it with your own sub-genres, write it up in
a fixed shape, and answer "which did you like more?" pairs. Two curators, no login.
Everything you type is versioned and exported: that's the input for the recommender
(phase 2) and the swipe room (phase 3).

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

**Who's marking.** The switch next to the title (Santi / Paola) says whose marks you're
looking at and writing. Every mark, tag, tier, duel and note is stored per curator; the
other curator's mark shows on each card as a small purple chip. Labels (sub-genres) are a
property of the film and shared. To add a curator, add a row to `CURATORS` in `setup.py`.

**Lists as projects.** Pick a list in the filter (IMDb Top 250, your Letterboxd lists…)
and the bar above the grid shows, for every curator, how much of it is seen, tiered and
noted. Work through it in triage or browse under your own name; the consolidation is
automatic because the list is the same and the marks are per person.

Modes in the header (each has its own `#url`; a film's write-up is `#film/tt…`):

- **browse** — the grid. Filter, click a poster, write it up.
  Search matches title and director. If a film isn't there, search shows a
  "search all of IMDb" button: anything with ≥1000 votes can be added in one click.
- **triage** — one film at a time, keyboard only. `S` seen (opens the write-up),
  `W` want to see, `N` not seen, `→` skip, `←` back. Put the status filter on
  "unmarked" and sort by popularity, then just go.
- **compare** — two films you've seen, "which did you like more?" `←` `→` `↓` can't say.
  Mostly picks pairs inside the same tier, because that's where the order is unknown;
  "same tier only" and a tier filter narrow it. The strongest ranking signal you can
  give a model, and the fastest to answer.
- **ladder** — one tier, your films in it, drag (or `↑` `↓`) to put them in order. Films you
  haven't ordered sort by their duel win rate. Click a title to write it up.
- **progress** — per genre, decade, label, list and tier: seen / tiered / noted / unmarked.
  Click a row to browse exactly that. Quick links for "seen but not noted" and "seen but
  not tiered"; the status filter has them too.
- **tags** — rename, merge, regroup and delete tags and labels. A rename or merge rewrites
  every film that carries it.

**The write-up** opens on any poster and stays open while you go `Ctrl+←` / `Ctrl+→`
through the films of the current filter. It saves itself as you type ("saved" bottom
right); `Esc` or `Ctrl+Enter` closes it (and moves on, in triage). Keys `1`–`9` `0` `-` `=`
pick the tier when you're not in a text field; `/` jumps to the new-tag box.

- status (seen / want / not seen), tier, how often / when / how well you remember it
- **Why it reached me** — feeling and mechanism, not plot. The field the AI reads.
  "You know from minute one she's screwed" is worth more than a paragraph of plot.
- What it's about · Who it's for · Who should avoid it · Anything else
- tags: one click `+` why it worked, two `−` what went wrong, three off. Suggestions above
  the list come from tags you used on films with the same director, a shared label or
  shared themes.
- labels: your sub-genres (folk horror, home invasion, new french extremity…).

Write for a person (for Paola), not for the machine; the machine reads it better that way too.

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

- `films.db` — everything, sqlite. `marks` is the current state (one row per curator per
  film), `mark_tags` the tags on them, `film_labels` the labels, `pairs` the comparisons,
  `events` the append-only history of every change.
- `exports/` — rewritten after every save: `marks.jsonl`, `pairs.jsonl`, `events.jsonl`,
  `vocab.jsonl`, `marks.csv` (everyone), and per curator `marks_<id>.csv` and
  **`notes_<id>.md`**: every seen film as a fixed-shape markdown entry (tier, labels, tags,
  the four fields). That file is what phase 2 hands to the model. If `exports/` is a git
  repo it auto-commits, and pushes if a remote is set. Make that remote **private**.
- `/api/export.csv[?curator=santi]`, `/api/export.md?curator=santi`, `/api/export.json` —
  the same over HTTP (the export menu in the header).
- `restore.py` — puts `exports/marks.jsonl` back into a fresh `films.db`.

**Upgrading from the single-curator version.** Start the app once: it copies `films.db` to
`films.db.before-curators.<date>`, moves every row of the old `my` table to `marks` under
`santi` (tags included), gives the old pairs and events an owner, and keeps the old table as
`my_legacy`. Plex-imported "seen" marks land on `santi` too (`FILMCAT_IMPORT_CURATOR` changes that).

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

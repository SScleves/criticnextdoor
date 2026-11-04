# Critic Next Door, phase 1: the catalogue

**Open it anywhere:** https://sscleves.github.io/criticnextdoor/ — the full app, running in the
browser (see *Online edition*). On the home network the local server is faster and has posters
when enriched: `http://192.168.1.8:8000`, or the door at https://sscleves.github.io/criticnextdoor/local.html.

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

Posters, overviews, keywords, Rotten Tomatoes and Metacritic come from Radarr's metadata
service, no account needed (`enrich_tmdb.py` / `enrich_omdb.py` remain as alternatives):

```bash
.venv/bin/python enrich_radarr.py     # ~35 min, safe to stop and re-run; then ./publish.sh
```

## Workflow

**Who's marking.** The two names next to the title (Santi, amber · Paola, purple) say whose
marks you're looking at and writing; the whole header takes that person's colour. Every mark,
score, end-feeling, tag, duel and note is stored per curator; the other person's mark shows on
each card as a small chip in their colour. Labels (sub-genres) are a property of the film and
shared. To add a curator, add a row to `CURATORS` in `setup.py`.

**Who writes what.** Drag a poster onto a name: that film goes into that person's to-write
queue (the counter on the name, the ✎ badge on the card). "my queue" in the filters shows
your own; triage runs through it. Scoring the film, or writing any note, takes it out of the
queue. A queue is a to-do, not an opinion: both of you can still mark any film.

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
- **compare** — two films you've seen, "which did you like more?" `←` `→` `↓` can't say,
  then one line of *because…* (Enter saves, Esc skips the line). Mostly picks pairs at the
  same score, because that's where the order is unknown; "same score only" and a score
  filter narrow it.
- **ladder** — one score, your films at it, drag (or `↑` `↓`) to put them in order. Films you
  haven't ordered sort by their duel win rate. Click a title to write it up.
- **progress** — both of you side by side: a summary box each, then per genre, decade, label,
  list, scale step and end-feeling: seen / scored / felt / noted / to write / unmarked per
  person. Click a row to browse exactly that. Quick links for "seen, not noted", "seen, not
  scored", "seen, no end-feeling" and "my queue"; the status filter has them too.
- **words** — the vocabulary everything is written in: name the scale steps; rename, merge,
  regroup and delete end-feelings, tags and labels. A rename or merge rewrites every film
  that carries the word.

**The write-up** opens on any poster and stays open while you go `Ctrl+←` / `Ctrl+→`
through the films of the current filter. It saves itself as you type ("saved" bottom
right); `Esc` or `Ctrl+Enter` closes it (and moves on, in triage). Keys `1`–`9` `0` `-` `=`
pick the tier when you're not in a text field; `/` jumps to the new-tag box.

- status (seen / want / not seen; click the lit one again to clear it) · **score** on a −10…+10
  scale: the label is the hero ("Great. Again tonight."), the number is under it; slide, `−`/`+`,
  or "see all 21" and pick. Every number has a line (`SCALE_NAMES` in `setup.py`; edit them on the
  **words** page). Sign is the feeling: negative = wasted my time, 0 = nothing happened, positive
  = worth it; −5 is the one that's an action ("Trick your enemies into watching it").
- **♥ Favourite** and **🍿 Guilty pleasure** (with one tag: cheesy / nostalgia / so-bad-its-good /
  comfort / the-one-scene) are flags beside the score; they never change it. Cards show them.
- **end-feeling**: what it left you with when it ended, one or two from a fixed list
  (wrecked, unsettled, hollow, shaken, warm, giddy, cosy, moved, wired, satisfied, puzzled,
  cheated, numb, annoyed, delighted-by-the-mess), each at 1–3. The visitor's first question in
  phase 2 is "what did you feel at the end?", in these same words.
- how often / when / how well you remember it
- **Why it reached me** — feeling and mechanism, not plot. The field the AI reads.
  "You know from minute one she's screwed" is worth more than a paragraph of plot.
- What it's about · Who it's for · Who should avoid it · Anything else
- a counter under each text field says when a note is getting long for a note. It's a guide,
  not a limit: the fields are for feeling and mechanism; plot lives on TMDB already.
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

**Lists in the repo.** `lists/*.csv` (Position, Name, Year) are loaded by `python lists.py all`; the file
name is the list name. Today: AFI 100 (2007), Sight & Sound 2022 critics (full 250; 236 match), TSPDT 1000 (2025; 927 match),
WGA 101 Screenplays (top 50), Metacritic Must-See (top 500; 453 match, the rest are documentaries,
TV and shorts). To add one: paste the list page into a chat with Claude and ask for a `lists/` file, or drop a CSV in `lists/` (the Letterboxd snippet in `tools/`
makes one from any Letterboxd list page) and run `lists.py all` again.

Lists show up as a filter ("IMDb Top 250 · seen 40/250 · avg 6.2"), as a sort ("by list rank"),
on each card, and as a boost in *for you*. Letterboxd blocks automated reads of other people's
lists, so for those: your own export, or copy the list into a `Name,Year` CSV.

Rotten Tomatoes has no API; OMDb relays its score. Free key at https://www.omdbapi.com/apikey.aspx
(1000/day → the catalogue takes ~7 days; the $1/month tier does it in one go):

```bash
OMDB_API_KEY=xxx .venv/bin/python enrich_omdb.py              # resumes where it stopped
```

Adds 🍅 and Metacritic to cards, "by Rotten Tomatoes" to the sort, `rt`/`metacritic` to exports.

## Online edition (GitHub Pages)

GitHub Pages can't run `app.py`, so `docs/` holds the same app with the server ported to the
browser: `docs/shim.js` is `app.py` in JavaScript, running the same SQL on the same database
(`docs/data/films.db.gz`, SQLite in WASM via sql.js). Same page, same modes, same keys.

Your marks are private, so they are **not** in the public database. They live in the browser
(always, so a reload loses nothing) and, once you give the page a token, in a **private** GitHub
repo as `state.json`. That is what syncs laptops, and what the media server pulls.

One-time setup, by hand (these need your GitHub login):

1. Create a private repo, e.g. `SScleves/criticnextdoor-state`. Leave it empty.
2. Make a fine-grained token: github.com → Settings → Developer settings → Fine-grained tokens.
   Repository access: only that repo. Permission: Contents, read and write. Nothing else.
3. Open the Pages app, click the gear bottom right, paste repo and token, "save & sync".
   Repeat step 3 on each laptop. The token stays in that browser; it is sent only to api.github.com.

On the media server, `python sync_state.py` pulls `state.json`, merges it into `films.db`
(per mark the newest wins, pairs and events are unioned), pushes the merged file back and
refreshes `exports/`. Run it whenever you marked things elsewhere, or on a timer.

Rebuild and publish after the catalogue changes (setup, imports, lists, posters, scores):

```bash
.venv/bin/python build_static.py && git add docs && git commit -m "docs: rebuild" && git push
```

Pages serves `main`/`docs`, so merge or cherry-pick into `main` if you work on a branch.

## Where your words live

- `films.db` — everything, sqlite. `marks` is the current state (one row per curator per
  film, with `score`), `mark_tags` and `mark_feelings` the tags and end-feelings on them,
  `film_labels` the labels, `scale` the names of the 21 steps, `queue` who writes what,
  `pairs` the comparisons (with `reason`), `events` the append-only history of every change.
- `exports/` — rewritten after every save: `marks.jsonl`, `pairs.jsonl`, `events.jsonl`,
  `queue.jsonl`, `vocab.jsonl` (scale, feelings, tags, labels), `marks.csv` (everyone), and per curator `marks_<id>.csv` and
  **`notes_<id>.md`**: every seen film as a fixed-shape markdown entry (tier, labels, tags,
  the four fields). That file is what phase 2 hands to the model. If `exports/` is a git
  repo it auto-commits, and pushes if a remote is set. Make that remote **private**.
- `/api/export.csv[?curator=santi]`, `/api/export.md?curator=santi`, `/api/export.json` —
  the same over HTTP (the export menu in the header).
- `restore.py` — puts `exports/marks.jsonl` back into a fresh `films.db`.

**Labels to start from.** `setup.py` seeds ~80 labels in two groups, after Netflix's own category
grammar (tone × sub-genre × origin; origin we already have): sub-genres (home-invasion, folk-horror,
heist, coming-of-age, …) and tones (gritty, quirky, mind-bending, slow-burn, …). `python seed_labels.py`
then labels the obvious films from IMDb genres and TMDB keywords (run an enrich script first, or it
only has genres to go on). Those rows are `source='auto'`: dashed in the write-up, shipped with the
public catalogue, never counted as anyone's word. Leave one on when you write the film up and it
becomes yours; click it off and it's gone.

**Upgrading.** From the tier version: marks get their `score` from the tier name on first
start (my favourite → +10 … the worst shit ever → −10). From the single-curator version: Start the app once: it copies `films.db` to
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

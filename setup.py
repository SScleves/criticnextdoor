#!/usr/bin/env python3
"""
Step 1: build (or refresh) films.db from IMDb's official datasets.

Downloads title.basics, title.ratings, title.crew and name.basics (~630 MB)
and writes two tables:
  films  - the catalogue you browse (>= --min-votes votes, >= --min-rating)
  pool   - every other feature film with >= --pool-votes votes, so anything the
           filter left out can be added from inside the app, no network needed

Safe to re-run: film data (ratings, crew) is refreshed in place; your marks
(my / events / pairs) are never touched.

Usage:
    python setup.py
    python setup.py --min-votes 50000 --min-rating 6.5
"""
import argparse, csv, gzip, os, sqlite3, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://datasets.imdbws.com/"
FILES = ["title.basics.tsv.gz", "title.ratings.tsv.gz", "title.crew.tsv.gz", "name.basics.tsv.gz"]
DB = os.path.join(HERE, "films.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS films (
  tconst TEXT PRIMARY KEY,
  title TEXT, original_title TEXT, year INTEGER, decade INTEGER,
  runtime INTEGER, genres TEXT, rating REAL, votes INTEGER,
  directors TEXT, writers TEXT,
  tmdb_id INTEGER, poster TEXT, backdrop TEXT, overview TEXT,
  keywords TEXT, countries TEXT, language TEXT,
  in_library INTEGER DEFAULT 0,   -- 1 = Radarr has the file
  added TEXT                      -- how it got here: filter | pool | tautulli | radarr
);
CREATE TABLE IF NOT EXISTS pool (
  tconst TEXT PRIMARY KEY,
  title TEXT, original_title TEXT, year INTEGER, runtime INTEGER, genres TEXT,
  rating REAL, votes INTEGER, directors TEXT, writers TEXT
);
CREATE TABLE IF NOT EXISTS my (          -- current state, one row per film
  tconst TEXT PRIMARY KEY REFERENCES films(tconst),
  status TEXT,          -- seen | not | want
  tier TEXT,            -- see tiers table
  tags TEXT,            -- comma-separated; "-tag" means "this is what went wrong"
  note TEXT,            -- your words
  times TEXT,           -- once | few | many
  when_seen TEXT,       -- recent | years | kid
  memory TEXT,          -- clear | fuzzy
  source TEXT,          -- me | tautulli
  updated TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS events (      -- append-only: every change, forever
  id INTEGER PRIMARY KEY,
  ts TEXT DEFAULT (datetime('now')),
  kind TEXT,            -- mark | pair | add | import
  tconst TEXT,
  payload TEXT,         -- json
  source TEXT
);
CREATE TABLE IF NOT EXISTS pairs (       -- "which did you like more?" answers
  id INTEGER PRIMARY KEY,
  ts TEXT DEFAULT (datetime('now')),
  a TEXT, b TEXT,
  winner TEXT,          -- a or b's tconst; NULL = couldn't say
  skipped INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lists (       -- external top lists: IMDb Top 250, TSPDT, your Letterboxd lists...
  list TEXT, rank INTEGER, tconst TEXT,
  PRIMARY KEY (list, tconst)
);
CREATE INDEX IF NOT EXISTS idx_lists_tconst ON lists(tconst);
CREATE TABLE IF NOT EXISTS tiers (name TEXT PRIMARY KEY, score INTEGER, ord INTEGER);
CREATE TABLE IF NOT EXISTS tags (name TEXT PRIMARY KEY, grp TEXT);
CREATE INDEX IF NOT EXISTS idx_films_decade ON films(decade);
CREATE INDEX IF NOT EXISTS idx_films_rating ON films(rating);
CREATE INDEX IF NOT EXISTS idx_pool_title ON pool(title);
CREATE INDEX IF NOT EXISTS idx_events_tconst ON events(tconst);
"""

# columns added after the first release; ALTERed in when an older films.db lacks them
MIGRATIONS = {
    "films": ["directors TEXT", "writers TEXT", "in_library INTEGER DEFAULT 0", "added TEXT",
              "rt INTEGER", "metacritic INTEGER", "omdb_checked INTEGER DEFAULT 0"],
    "my": ["times TEXT", "when_seen TEXT", "memory TEXT", "source TEXT"],
}

TIERS = [  # (name, score, order)  score feeds the recommender, name is what you see
    ("my favourite", 10, 1), ("top tier", 9, 2), ("excellent", 8, 3),
    ("very good", 7, 4), ("good", 6, 5), ("ok", 4, 6),
    ("meh", 1, 7),
    ("bad but fun", -2, 8), ("guilty pleasure", -3, 9),
    ("really bad", -6, 10), ("awful", -8, 11), ("the worst shit ever", -10, 12),
]

TAGS = [  # starter vocabulary; add/edit in the app
    ("defenceless-from-start", "fear"), ("no-way-out-geography", "fear"),
    ("the-threat-is-in-the-family", "fear"), ("slow-dread", "fear"),
    ("jump-scares", "fear"), ("body-horror", "fear"), ("home-invasion", "fear"),
    ("nothing-is-explained", "structure"), ("slow-reveal", "structure"),
    ("twist", "structure"), ("one-location", "structure"), ("real-time", "structure"),
    ("funny-then-not", "tone"), ("no-jokes", "tone"), ("bleak", "tone"),
    ("cozy", "tone"), ("feel-good", "tone"), ("melancholic", "tone"),
    ("brutal", "intensity"), ("gentle", "intensity"), ("loud", "intensity"),
    ("sound-design-carries-it", "craft"), ("gorgeous-images", "craft"),
    ("great-script", "craft"), ("one-performance", "craft"),
    ("sunday-alone", "occasion"), ("friday-with-friends", "occasion"),
    ("date-night", "occasion"), ("hungover", "occasion"),
]


def migrate(con):
    """Create anything missing. Called by setup and by the app on start."""
    con.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for c in cols:
            if c.split()[0] not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {c}")
    con.executemany("INSERT OR IGNORE INTO tiers VALUES (?,?,?)", TIERS)
    con.executemany("INSERT OR IGNORE INTO tags VALUES (?,?)", TAGS)
    con.commit()


def download(name, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, name)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"  {name}: already here")
        return path
    print(f"  downloading {name} ...", end="", flush=True)
    urllib.request.urlretrieve(BASE + name, path)
    print(f" {os.path.getsize(path)//1_000_000} MB")
    return path


def read_tsv(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        yield from csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)


def nn(v):
    return None if v == "\\N" else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-votes", type=int, default=20000)
    ap.add_argument("--min-rating", type=float, default=6.0)
    ap.add_argument("--pool-votes", type=int, default=1000)
    ap.add_argument("--tsv-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()

    print("1/5 datasets")
    paths = {n: download(n, a.tsv_dir) for n in FILES}

    print("2/5 ratings")
    keep = {}
    for r in read_tsv(paths["title.ratings.tsv.gz"]):
        v = int(r["numVotes"]); s = float(r["averageRating"])
        if v >= a.pool_votes:
            keep[r["tconst"]] = (s, v)
    print(f"  {len(keep)} titles with >= {a.pool_votes} votes")

    print("3/5 basics (~2 minutes)")
    films, pool = {}, {}
    for r in read_tsv(paths["title.basics.tsv.gz"]):
        t = r["tconst"]
        if t not in keep or r["titleType"] != "movie" or r["startYear"] == "\\N":
            continue
        genres = nn(r["genres"]) or ""
        if genres == "Documentary":
            continue
        y = int(r["startYear"]); s, v = keep[t]
        row = dict(tconst=t, title=r["primaryTitle"], original_title=r["originalTitle"], year=y,
                   runtime=int(nn(r["runtimeMinutes"]) or 0) or None, genres=genres, rating=s, votes=v)
        pool[t] = row
        if v >= a.min_votes and s >= a.min_rating:
            films[t] = row
    print(f"  {len(films)} films pass the filter, {len(pool)} in the pool")

    print("4/5 crew + names (~4 minutes)")
    crew, need = {}, set()
    for r in read_tsv(paths["title.crew.tsv.gz"]):
        if r["tconst"] in pool:
            d = (nn(r["directors"]) or "").split(",")[:3]
            w = (nn(r["writers"]) or "").split(",")[:3]
            crew[r["tconst"]] = ([x for x in d if x], [x for x in w if x])
            need.update(crew[r["tconst"]][0]); need.update(crew[r["tconst"]][1])
    names = {}
    with gzip.open(paths["name.basics.tsv.gz"], "rt", encoding="utf-8") as f:
        next(f)
        for line in f:
            nconst, name = line.split("\t", 2)[:2]
            if nconst in need:
                names[nconst] = name
    for t, row in pool.items():
        d, w = crew.get(t, ([], []))
        row["directors"] = ", ".join(names[x] for x in d if x in names)
        row["writers"] = ", ".join(names[x] for x in w if x in names)
    print(f"  {len(names)} people")

    print("5/5 writing")
    con = sqlite3.connect(a.db)
    migrate(con)
    # films already in the db (added from the pool, Radarr, ...) get refreshed too
    existing = {r[0] for r in con.execute("SELECT tconst FROM films")}
    upsert = [dict(pool[t], added="pool") for t in existing if t in pool and t not in films]
    for t in films:
        films[t]["added"] = "filter"
    con.executemany("""INSERT INTO films (tconst,title,original_title,year,decade,runtime,genres,rating,votes,
                                          directors,writers,added)
        VALUES (:tconst,:title,:original_title,:year,:year/10*10,:runtime,:genres,:rating,:votes,:directors,:writers,:added)
        ON CONFLICT(tconst) DO UPDATE SET title=excluded.title, original_title=excluded.original_title,
          year=excluded.year, decade=excluded.decade, runtime=excluded.runtime, genres=excluded.genres,
          rating=excluded.rating, votes=excluded.votes, directors=excluded.directors, writers=excluded.writers,
          added=COALESCE(films.added, excluded.added)""",
        list(films.values()) + upsert)
    con.execute("DELETE FROM pool")
    con.executemany("""INSERT INTO pool (tconst,title,original_title,year,runtime,genres,rating,votes,directors,writers)
        VALUES (:tconst,:title,:original_title,:year,:runtime,:genres,:rating,:votes,:directors,:writers)""",
        list(pool.values()))
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM films").fetchone()[0]
    p = con.execute("SELECT COUNT(*) FROM pool").fetchone()[0]
    print(f"\ndone: {n} films in the catalogue, {p} more in the pool ({a.db})")
    print("next: python import_stack.py   (Tautulli watch history + Radarr library)")
    print("      python enrich_tmdb.py    (posters, optional)")
    print("then: python app.py            (http://localhost:8000)")


if __name__ == "__main__":
    main()

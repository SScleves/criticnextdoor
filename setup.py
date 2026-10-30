#!/usr/bin/env python3
"""
Step 1: build films.db from IMDb's official datasets.

Downloads title.basics + title.ratings (free, ~300 MB), keeps feature films
with enough votes to matter, and writes them to films.db.

Usage:
    python setup.py                 # default: movies with >= 20000 votes, rating >= 6.0
    python setup.py --min-votes 50000 --min-rating 6.5
    python setup.py --tsv-dir data  # reuse already-downloaded .tsv.gz files
"""
import argparse, csv, gzip, os, sqlite3, sys, urllib.request

BASE = "https://datasets.imdbws.com/"
FILES = ["title.basics.tsv.gz", "title.ratings.tsv.gz"]
DB = "films.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS films (
  tconst TEXT PRIMARY KEY,
  title TEXT, original_title TEXT, year INTEGER, decade INTEGER,
  runtime INTEGER, genres TEXT, rating REAL, votes INTEGER,
  tmdb_id INTEGER, poster TEXT, backdrop TEXT, overview TEXT,
  keywords TEXT, countries TEXT, language TEXT
);
CREATE TABLE IF NOT EXISTS my (
  tconst TEXT PRIMARY KEY REFERENCES films(tconst),
  status TEXT,          -- seen | not | want
  tier TEXT,            -- see tiers table
  tags TEXT,            -- comma-separated
  note TEXT,
  updated TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tiers (name TEXT PRIMARY KEY, score INTEGER, ord INTEGER);
CREATE TABLE IF NOT EXISTS tags (name TEXT PRIMARY KEY, grp TEXT);
CREATE INDEX IF NOT EXISTS idx_films_decade ON films(decade);
CREATE INDEX IF NOT EXISTS idx_films_rating ON films(rating);
"""

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-votes", type=int, default=20000)
    ap.add_argument("--min-rating", type=float, default=6.0)
    ap.add_argument("--tsv-dir", default="data")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()

    print("1/3 datasets")
    paths = {n: download(n, a.tsv_dir) for n in FILES}

    print("2/3 ratings (streaming)")
    keep = {}
    for r in read_tsv(paths["title.ratings.tsv.gz"]):
        v = int(r["numVotes"]); s = float(r["averageRating"])
        if v >= a.min_votes and s >= a.min_rating:
            keep[r["tconst"]] = (s, v)
    print(f"  {len(keep)} titles pass the vote/rating filter")

    print("3/3 basics (streaming, this is the big one: ~2 minutes)")
    rows = []
    for r in read_tsv(paths["title.basics.tsv.gz"]):
        t = r["tconst"]
        if t not in keep or r["titleType"] != "movie":
            continue
        if r["startYear"] == "\\N":
            continue
        y = int(r["startYear"])
        rt = None if r["runtimeMinutes"] == "\\N" else int(r["runtimeMinutes"])
        genres = "" if r["genres"] == "\\N" else r["genres"]
        if genres == "Documentary":
            continue
        rows.append((t, r["primaryTitle"], r["originalTitle"], y, y // 10 * 10,
                     rt, genres, keep[t][0], keep[t][1]))
    print(f"  {len(rows)} feature films")

    con = sqlite3.connect(a.db)
    con.executescript(SCHEMA)
    con.executemany("""INSERT OR IGNORE INTO films
        (tconst,title,original_title,year,decade,runtime,genres,rating,votes)
        VALUES (?,?,?,?,?,?,?,?,?)""", rows)
    con.executemany("INSERT OR IGNORE INTO tiers VALUES (?,?,?)", TIERS)
    con.executemany("INSERT OR IGNORE INTO tags VALUES (?,?)", TAGS)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM films").fetchone()[0]
    print(f"\ndone: {n} films in {a.db}")
    print("next: python enrich_tmdb.py   (posters, optional but worth it)")
    print("then: python app.py           (opens the catalogue on http://localhost:8000)")


if __name__ == "__main__":
    main()

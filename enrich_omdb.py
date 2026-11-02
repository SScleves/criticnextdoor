#!/usr/bin/env python3
"""
Step 2b (optional): Rotten Tomatoes + Metacritic scores via OMDb.

Rotten Tomatoes has no public API; OMDb (omdbapi.com) relays its score per film.
Free key: https://www.omdbapi.com/apikey.aspx  (1000 requests/day, so the full
catalogue takes ~7 days on the free tier; the $1/month tier does it in one run).
Safe to re-run: skips films already scored. Resumes where it stopped.

    OMDB_API_KEY=xxxx python enrich_omdb.py
    python enrich_omdb.py --key xxxx --limit 900
"""
import argparse, os, sqlite3, sys, time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://www.omdbapi.com/"


def ensure_columns(con):
    have = {r[1] for r in con.execute("PRAGMA table_info(films)")}
    for col in ("rt INTEGER", "metacritic INTEGER", "omdb_checked INTEGER DEFAULT 0"):
        if col.split()[0] not in have:
            con.execute(f"ALTER TABLE films ADD COLUMN {col}")
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("OMDB_API_KEY"))
    ap.add_argument("--db", default=os.path.join(HERE, "films.db"))
    ap.add_argument("--limit", type=int, default=0, help="stop after N films (free tier: ~950/day)")
    a = ap.parse_args()
    if not a.key:
        sys.exit("no OMDb key: export OMDB_API_KEY=... or --key ...")

    con = sqlite3.connect(a.db)
    ensure_columns(con)
    todo = [r[0] for r in con.execute("SELECT tconst FROM films WHERE omdb_checked=0 ORDER BY votes DESC")]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(todo)} films to score")

    s = requests.Session()
    done = 0
    for t in todo:
        try:
            d = s.get(API, params={"apikey": a.key, "i": t}, timeout=15).json()
        except Exception as e:
            print(f"  {t}: {e}"); time.sleep(2); continue
        if d.get("Response") == "False":
            if "limit" in d.get("Error", "").lower():
                print(f"daily limit reached after {done}; run again tomorrow"); break
            con.execute("UPDATE films SET omdb_checked=1 WHERE tconst=?", (t,)); continue
        rt = next((int(r["Value"].rstrip("%")) for r in d.get("Ratings", [])
                   if r["Source"] == "Rotten Tomatoes" and r["Value"].rstrip("%").isdigit()), None)
        mc = int(d["Metascore"]) if d.get("Metascore", "N/A").isdigit() else None
        con.execute("UPDATE films SET rt=?, metacritic=?, omdb_checked=1 WHERE tconst=?", (rt, mc, t))
        done += 1
        if done % 50 == 0:
            con.commit(); print(f"  {done}/{len(todo)}")
        time.sleep(0.1)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM films WHERE rt IS NOT NULL").fetchone()[0]
    print(f"done: {done} checked this run, {n} films have a Rotten Tomatoes score")


if __name__ == "__main__":
    main()

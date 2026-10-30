#!/usr/bin/env python3
"""
Step 2 (optional): add posters, overview, keywords and countries from TMDB.

Needs a free API key: https://www.themoviedb.org/settings/api  (choose "Developer").
Put it in the environment:  export TMDB_API_KEY=xxxx   (or pass --key)

Safe to re-run: it skips films that already have a poster.
~6000 films takes ~25 minutes (TMDB allows ~40 requests/second, we go gentler).
"""
import argparse, os, sqlite3, sys, time
import requests

API = "https://api.themoviedb.org/3"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("TMDB_API_KEY"))
    ap.add_argument("--db", default="films.db")
    ap.add_argument("--limit", type=int, default=0, help="stop after N films (for testing)")
    a = ap.parse_args()
    if not a.key:
        sys.exit("no TMDB key: export TMDB_API_KEY=... or --key ...")

    con = sqlite3.connect(a.db)
    todo = con.execute(
        "SELECT tconst FROM films WHERE poster IS NULL ORDER BY votes DESC").fetchall()
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(todo)} films to enrich")

    s = requests.Session()
    s.params = {"api_key": a.key}
    done = 0
    for (t,) in todo:
        try:
            r = s.get(f"{API}/find/{t}", params={"external_source": "imdb_id"}, timeout=15).json()
            hits = r.get("movie_results") or []
            if not hits:
                con.execute("UPDATE films SET poster='' WHERE tconst=?", (t,))
                continue
            m = hits[0]
            d = s.get(f"{API}/movie/{m['id']}",
                      params={"append_to_response": "keywords"}, timeout=15).json()
            kw = ",".join(k["name"] for k in d.get("keywords", {}).get("keywords", []))
            countries = ",".join(c["iso_3166_1"] for c in d.get("production_countries", []))
            con.execute("""UPDATE films SET tmdb_id=?, poster=?, backdrop=?, overview=?,
                           keywords=?, countries=?, language=? WHERE tconst=?""",
                        (m["id"], m.get("poster_path") or "", m.get("backdrop_path") or "",
                         d.get("overview", ""), kw, countries, d.get("original_language", ""), t))
        except Exception as e:
            print(f"  {t}: {e}")
            time.sleep(2)
            continue
        done += 1
        if done % 50 == 0:
            con.commit()
            print(f"  {done}/{len(todo)}")
        time.sleep(0.06)
    con.commit()
    print(f"done: {done} enriched")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Step 2: posters, overview, keywords, language, Rotten Tomatoes and Metacritic, from Radarr.

Radarr's lookup endpoint returns TMDB metadata plus critic scores through Radarr's own metadata
service, so no TMDB or OMDb account is needed. The Radarr API key is read from the stack's
config at run time (same as import_stack.py); nothing is printed.

Safe to re-run: skips films already looked up. ~6,700 films take ~35 minutes.
Afterwards: ./publish.sh  so the online edition gets the posters too.

    python enrich_radarr.py
    python enrich_radarr.py --limit 20        # try it
    python enrich_radarr.py --refresh         # re-fetch everything (new scores)
Env: MEDIA_STACK (default /opt/media/configs), RADARR_URL (default http://localhost:7878)
"""
import argparse, os, re, sqlite3, sys, time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.environ.get("MEDIA_STACK", "/opt/media/configs")
RADARR = os.environ.get("RADARR_URL", "http://localhost:7878")
TMDB_IMG = re.compile(r"^https?://image\.tmdb\.org/t/p/[^/]+")


def radarr_key():
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(os.path.join(STACK, "radarr", "config.xml"), encoding="utf-8").read())
    if not m:
        sys.exit("radarr ApiKey not found")
    return m.group(1)


def ensure_columns(con):
    have = {r[1] for r in con.execute("PRAGMA table_info(films)")}
    for col in ("rt INTEGER", "metacritic INTEGER", "omdb_checked INTEGER DEFAULT 0", "looked_up INTEGER DEFAULT 0"):
        if col.split()[0] not in have:
            con.execute(f"ALTER TABLE films ADD COLUMN {col}")
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "films.db"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true", help="re-fetch films already looked up")
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    ensure_columns(con)
    where = "" if a.refresh else "WHERE looked_up=0"
    todo = [r[0] for r in con.execute(f"SELECT tconst FROM films {where} ORDER BY votes DESC")]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(todo)} films to look up")

    s = requests.Session()
    s.headers["X-Api-Key"] = radarr_key()
    done, found, errors = 0, 0, 0
    for t in todo:
        try:
            r = s.get(f"{RADARR}/api/v3/movie/lookup", params={"term": f"imdb:{t}"}, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                print(f"  {t}: {r.status_code}, pausing"); time.sleep(10); continue
            hits = [m for m in r.json() if (m.get("imdbId") or "").lower() == t]
        except Exception as e:
            errors += 1; print(f"  {t}: {e}"); time.sleep(3); continue
        done += 1
        if not hits:
            con.execute("UPDATE films SET looked_up=1, poster=IFNULL(poster,'') WHERE tconst=?", (t,))
            continue
        m = hits[0]
        img = {i["coverType"]: TMDB_IMG.sub("", i.get("remoteUrl") or "") for i in m.get("images", []) if i.get("remoteUrl")}
        rat = m.get("ratings") or {}
        rt = (rat.get("rottenTomatoes") or {}).get("value") or None
        mc = (rat.get("metacritic") or {}).get("value") or None
        con.execute("""UPDATE films SET tmdb_id=?, poster=?, backdrop=?, overview=?, keywords=?, language=?,
                                        rt=?, metacritic=?, looked_up=1 WHERE tconst=?""",
                    (m.get("tmdbId"), img.get("poster", ""), img.get("fanart", ""), m.get("overview") or "",
                     ",".join(m.get("keywords") or []), (m.get("originalLanguage") or {}).get("name") or "",
                     int(rt) if rt else None, int(mc) if mc else None, t))
        found += 1
        if done % 50 == 0:
            con.commit(); print(f"  {done}/{len(todo)}")
        time.sleep(0.15)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM films WHERE poster<>''").fetchone()[0]
    r = con.execute("SELECT COUNT(*) FROM films WHERE rt IS NOT NULL").fetchone()[0]
    print(f"done: {done} looked up, {found} found, {errors} errors · {n} films have a poster, {r} have a Rotten Tomatoes score")


if __name__ == "__main__":
    main()

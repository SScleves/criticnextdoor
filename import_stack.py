#!/usr/bin/env python3
"""
Pull what the media stack already knows, so you don't type it:
  Tautulli -> watch history -> status=seen (source=tautulli) for films you haven't marked yet
  Radarr   -> library       -> films.in_library=1 (adds the film from the pool if it's missing)

API keys are read from the stack's own config files at run time; nothing is printed.

    python import_stack.py              # both
    python import_stack.py --no-radarr
Env: MEDIA_STACK (default /opt/media/configs), TAUTULLI_URL, RADARR_URL
"""
import argparse, datetime as dt, os, re, sqlite3, sys
from collections import Counter, defaultdict

import requests

from app import DB, add_from_pool, event, write_exports

STACK = os.environ.get("MEDIA_STACK", "/opt/media/configs")
TAUTULLI = os.environ.get("TAUTULLI_URL", "http://localhost:8181")
RADARR = os.environ.get("RADARR_URL", "http://localhost:7878")


def key_from(path, pattern):
    m = re.search(pattern, open(path, encoding="utf-8").read(), re.M)
    if not m:
        sys.exit(f"no api key found in {path}")
    return m.group(1).strip().strip('"')


def tautulli(con):
    key = key_from(os.path.join(STACK, "tautulli", "config.ini"), r"^api_key\s*=\s*(.+)$")
    s = requests.Session()

    def call(cmd, **params):
        r = s.get(f"{TAUTULLI}/api/v2", params={"apikey": key, "cmd": cmd, **params}, timeout=60).json()
        if r["response"]["result"] != "success":
            sys.exit(f"tautulli {cmd}: {r['response'].get('message')}")
        return r["response"]["data"]

    rows, start = [], 0
    while True:
        d = call("get_history", media_type="movie", length=1000, start=start)
        rows += d["data"]; start += 1000
        if start >= d["recordsFiltered"]:
            break
    watched = [r for r in rows if r.get("watched_status", 0) >= 1 or (r.get("percent_complete") or 0) >= 70]
    print(f"tautulli: {len(rows)} movie plays, {len(watched)} count as watched")

    plays, last = Counter(), {}
    for r in watched:
        k = r["rating_key"]; plays[k] += 1
        last[k] = max(last.get(k, 0), r.get("date") or 0)

    films = {r[0] for r in con.execute("SELECT tconst FROM films")}
    by_title = defaultdict(list)
    for t, title, year in con.execute("SELECT tconst, title, year FROM pool"):
        by_title[(title.lower(), year)].append(t)
    marked = {r[0] for r in con.execute("SELECT tconst FROM my")}

    titles = {r["rating_key"]: (r["title"], r.get("year")) for r in watched}
    imported, already, unmatched = 0, 0, []
    for rk, (title, year) in titles.items():
        tconst = None
        try:
            guids = call("get_metadata", rating_key=rk).get("guids") or []
            tconst = next((g.split("//")[1] for g in guids if g.startswith("imdb://")), None)
        except SystemExit:
            pass
        if not tconst:
            cands = by_title.get((title.lower(), int(year or 0)), [])
            tconst = cands[0] if len(cands) == 1 else None
        if not tconst:
            unmatched.append(f"{title} ({year})"); continue
        if tconst not in films:
            if not add_from_pool(con, tconst, "tautulli"):
                unmatched.append(f"{title} ({year}) {tconst} - not in pool"); continue
            films.add(tconst)
        if tconst in marked:
            already += 1; continue
        when = dt.datetime.fromtimestamp(last[rk]).strftime("%Y-%m-%d %H:%M:%S")
        times = "once" if plays[rk] == 1 else "few"
        con.execute("""INSERT INTO my (tconst,status,times,source,updated) VALUES (?,'seen',?,'tautulli',?)""",
                    (tconst, times, when))
        event(con, "import", tconst, {"status": "seen", "plays": plays[rk], "last_watched": when}, source="tautulli")
        marked.add(tconst); imported += 1
    con.commit()
    print(f"  {imported} marked seen, {already} already had a mark, {len(unmatched)} unmatched")
    if unmatched:
        path = os.path.join(os.path.dirname(DB), "exports", "unmatched_tautulli.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write("\n".join(unmatched) + "\n")
        print(f"  unmatched list: {path}")


def radarr(con):
    key = key_from(os.path.join(STACK, "radarr", "config.xml"), r"<ApiKey>([^<]+)</ApiKey>")
    movies = requests.get(f"{RADARR}/api/v3/movie", headers={"X-Api-Key": key}, timeout=60).json()
    have = [m for m in movies if m.get("hasFile") and m.get("imdbId")]
    print(f"radarr: {len(movies)} movies, {len(have)} with a file and an IMDb id")
    films = {r[0] for r in con.execute("SELECT tconst FROM films")}
    con.execute("UPDATE films SET in_library=0")
    flagged, added, missing = 0, 0, []
    for m in have:
        t = m["imdbId"]
        if t not in films:
            if add_from_pool(con, t, "radarr"):
                added += 1
            else:
                missing.append(f"{m['title']} ({m.get('year')}) {t}"); continue
        con.execute("UPDATE films SET in_library=1 WHERE tconst=?", (t,))
        flagged += 1
    con.commit()
    print(f"  {flagged} flagged in library ({added} added from the pool), {len(missing)} not in the pool at all")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tautulli", action="store_true")
    ap.add_argument("--no-radarr", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    if not a.no_tautulli:
        tautulli(con)
    if not a.no_radarr:
        radarr(con)
    write_exports()
    print("exports refreshed")


if __name__ == "__main__":
    main()

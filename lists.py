#!/usr/bin/env python3
"""
External "top" lists, matched to your catalogue. Films not in the catalogue are
added from the pool (added=list) so a list is never silently short.

    python lists.py imdb-top                          # IMDb-style Top 250, computed offline from the IMDb data you have
    python lists.py csv "TSPDT 1000" path.csv         # any CSV/TXT: a tt... id per row, or title + year columns
    python lists.py url "Sight & Sound 2022" https://… # a page whose HTML has imdb.com/title/tt… links, in order
    python lists.py letterboxd ~/letterboxd-export     # YOUR Letterboxd export: watched/ratings/watchlist + lists/*.csv
    python lists.py show                              # what's loaded, how much of each you've seen
    python lists.py drop "name"

Letterboxd does not allow automated reads of other people's lists. Use your own export
(Settings -> Import & Export), or copy a list into a CSV with Name,Year columns.
"""
import csv, glob, io, os, re, sqlite3, sys

from app import DB, add_from_pool, event, write_exports
from setup import migrate

TT = re.compile(r"tt\d{7,8}")
STARS_TO_TIER = {5: "my favourite", 4.5: "top tier", 4: "excellent", 3.5: "very good", 3: "good",
                 2.5: "ok", 2: "meh", 1.5: "really bad", 1: "awful", 0.5: "the worst shit ever"}


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    migrate(con)
    return con


def norm(s):
    """Lower-case, straight quotes, no punctuation: 'Schindler’s List' == \"Schindler's List\"."""
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s*[:\-–—]\s*", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


class Resolver:
    """title+year -> tconst against the pool (which contains the catalogue too).
    Exact normalised match first; then a same-year film whose IMDb title contains the given one
    ('The Empire Strikes Back' -> 'Star Wars: Episode V - The Empire Strikes Back')."""

    def __init__(self, con):
        self.by, self.by_year = {}, {}
        for t, title, orig, year, votes in con.execute("SELECT tconst,title,original_title,year,votes FROM pool ORDER BY votes DESC"):
            names = {norm(title), norm(orig or "")} - {""}
            for name in names:
                self.by.setdefault((name, year), []).append(t)
            self.by_year.setdefault(year, []).append((t, names))

    def __call__(self, title, year):
        title = norm(title)
        years = (year, year - 1, year + 1) if year else (None,)
        for y in years:
            c = self.by.get((title, y))
            if c:
                return c[0]  # pool is ordered by votes, so ties go to the better-known film
        if len(title) >= 6:
            for y in years:
                for t, names in self.by_year.get(y, []):
                    if any(title in n for n in names):
                        return t
        return None


def save(con, name, tconsts, note=""):
    tconsts = list(dict.fromkeys(t for t in tconsts if t))
    films = {r[0] for r in con.execute("SELECT tconst FROM films")}
    added, missing = 0, 0
    con.execute("DELETE FROM lists WHERE list=?", (name,))
    rank = 0
    for t in tconsts:
        if t not in films:
            if add_from_pool(con, t, "list"):
                added += 1; films.add(t)
            else:
                missing += 1; continue
        rank += 1
        con.execute("INSERT INTO lists (list,rank,tconst) VALUES (?,?,?)", (name, rank, t))
    event(con, "import", None, {"list": name, "items": rank, "added": added, "missing": missing}, source="list")
    con.commit()
    print(f"{name}: {rank} films{note} ({added} added from the pool, {missing} not in the pool at all)")


def imdb_top(con, n=250, min_votes=25000):
    """IMDb's Top 250 formula: WR = v/(v+m)*R + m/(v+m)*C, m = min votes, C = mean rating."""
    rows = con.execute("SELECT tconst, rating, votes FROM pool WHERE votes >= ?", (min_votes,)).fetchall()
    c = sum(r["rating"] for r in rows) / len(rows)
    rows.sort(key=lambda r: -(r["votes"] / (r["votes"] + min_votes) * r["rating"] + min_votes / (r["votes"] + min_votes) * c))
    save(con, "IMDb Top 250", [r["tconst"] for r in rows[:n]], " (computed from the IMDb dataset)")


def rows_from_csv(text):
    """Yield tconst (if any column has one) or (title, year) per data row. Tolerates Letterboxd's preamble."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"(?i)^(position,|name,|title,|imdb|tconst|const,|rank)", l)), 0)
    rd = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    for r in rd:
        vals = [v or "" for v in r.values()]
        m = next((TT.search(v) for v in vals if v and TT.search(v)), None)
        if m:
            yield m.group(0); continue
        title = next((r[k] for k in r if k and k.strip().lower() in ("name", "title", "film", "movie")), None)
        year = next((r[k] for k in r if k and k.strip().lower() in ("year", "release year")), None)
        if title:
            yield (title, int(year) if year and year.isdigit() else None)


def resolve_all(con, items):
    res, out, unmatched = None, [], []
    for it in items:
        if isinstance(it, str):
            out.append(it); continue
        res = res or Resolver(con)
        t = res(*it)
        (out.append(t) if t else unmatched.append(f"{it[0]} ({it[1]})"))
    if unmatched:
        print(f"  {len(unmatched)} not matched: " + "; ".join(unmatched[:8]) + (" …" if len(unmatched) > 8 else ""))
    return out


def from_csv(con, name, path):
    save(con, name, resolve_all(con, rows_from_csv(open(path, encoding="utf-8-sig").read())))


def from_url(con, name, url):
    import requests
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    ids = list(dict.fromkeys(TT.findall(r.text)))
    if not ids:
        sys.exit("no imdb.com/title/tt… links in that page")
    save(con, name, ids)


def letterboxd(con, folder):
    """Your own Letterboxd export (unzipped): watched -> seen, ratings -> tier, watchlist -> want, lists/*.csv -> lists."""
    res = Resolver(con)
    marked = {r[0]: r for r in con.execute("SELECT tconst, status, tier FROM my")}
    films = {r[0] for r in con.execute("SELECT tconst FROM films")}

    def film(title, year):
        t = res(title, year)
        if t and t not in films and add_from_pool(con, t, "letterboxd"):
            films.add(t)
        return t if t in films else None

    def mark(t, status, tier=None, when=None):
        cur = marked.get(t)
        if cur is None:
            con.execute("INSERT INTO my (tconst,status,tier,source,updated) VALUES (?,?,?,'letterboxd',COALESCE(?,datetime('now')))",
                        (t, status, tier, when))
            marked[t] = (t, status, tier)
            event(con, "import", t, {"status": status, "tier": tier}, source="letterboxd"); return 1
        if tier and cur["tier"] is None and cur["status"] == "seen":
            con.execute("UPDATE my SET tier=? WHERE tconst=?", (tier, t)); return 1
        return 0

    n = 0
    for fname, status in (("watched.csv", "seen"), ("ratings.csv", "seen"), ("watchlist.csv", "want")):
        p = os.path.join(folder, fname)
        if not os.path.exists(p):
            continue
        k = 0
        for r in csv.DictReader(open(p, encoding="utf-8-sig")):
            t = film(r.get("Name", ""), int(r["Year"]) if r.get("Year", "").isdigit() else None)
            if not t:
                continue
            tier = STARS_TO_TIER.get(float(r["Rating"])) if r.get("Rating") else None
            k += mark(t, status, tier, (r.get("Date") or None) and r["Date"] + " 12:00:00")
        print(f"{fname}: {k} marks"); n += k
    con.commit()
    for p in sorted(glob.glob(os.path.join(folder, "lists", "*.csv"))):
        name = "LB: " + os.path.splitext(os.path.basename(p))[0].replace("-", " ")
        from_csv(con, name, p)
    print(f"letterboxd: {n} marks imported (existing marks untouched)")


def show(con):
    for r in con.execute("""SELECT l.list, COUNT(*) n, SUM(m.status='seen') seen,
                                   ROUND(AVG(CASE WHEN m.status='seen' THEN t.score END),1) avg
                            FROM lists l LEFT JOIN my m ON m.tconst=l.tconst LEFT JOIN tiers t ON t.name=m.tier
                            GROUP BY l.list ORDER BY l.list"""):
        print(f"{r['list']:<32} {r['n']:>5} films   seen {r['seen'] or 0:>4}   your avg score {r['avg'] if r['avg'] is not None else '-'}")


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    con = connect()
    cmd = a[0]
    if cmd == "imdb-top":
        imdb_top(con)
    elif cmd == "csv" and len(a) == 3:
        from_csv(con, a[1], a[2])
    elif cmd == "url" and len(a) == 3:
        from_url(con, a[1], a[2])
    elif cmd == "letterboxd" and len(a) == 2:
        letterboxd(con, a[1])
    elif cmd == "drop" and len(a) == 2:
        con.execute("DELETE FROM lists WHERE list=?", (a[1],)); con.commit(); print("dropped", a[1])
    elif cmd == "show":
        show(con); return
    else:
        sys.exit(__doc__)
    write_exports()


if __name__ == "__main__":
    main()

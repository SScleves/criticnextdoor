#!/usr/bin/env python3
"""
External "top" lists, matched to your catalogue. Films not in the catalogue are
added from the pool (added=list) so a list is never silently short.

    python lists.py imdb-top                          # IMDb-style Top 250, computed offline from the IMDb data you have
    python lists.py csv "TSPDT 1000" path.csv         # any CSV/TXT: a tt... id per row, or title + year columns
    python lists.py url "Sight & Sound 2022" https://… # a page whose HTML has imdb.com/title/tt… links, in order
    python lists.py letterboxd ~/letterboxd-export     # YOUR Letterboxd export: watched/ratings/watchlist + lists/*.csv
    python lists.py all                               # every lists/*.csv in this repo (the list name is the file name)
    python lists.py decades                           # Horror · 1980s, Sci-Fi · 2010s …: top 40 per genre and decade, from IMDb
    python lists.py show                              # what's loaded, how much of each you've seen
    python lists.py drop "name"

Letterboxd does not allow automated reads of other people's lists. Use your own export
(Settings -> Import & Export), or copy a list into a CSV with Name,Year columns.
"""
import csv, glob, io, os, re, sqlite3, sys, unicodedata

from app import DB, add_from_pool, event, write_exports
from setup import DEFAULT_CURATOR, list_category, migrate

CURATOR = os.environ.get("FILMCAT_IMPORT_CURATOR", DEFAULT_CURATOR)

TT = re.compile(r"tt\d{7,8}")
STARS_TO_SCORE = {5: 10, 4.5: 9, 4: 8, 3.5: 7, 3: 6, 2.5: 4, 2: 1, 1.5: -6, 1: -8, 0.5: -10}   # Letterboxd stars -> the -10..10 scale


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    migrate(con)
    return con


def norm(s):
    """Lower-case, straight quotes, no punctuation: 'Schindler’s List' == \"Schindler's List\"."""
    s = re.sub(r"\s*\[\d{4}\]\s*$", "", s)                        # 'Psycho [1960]' (TSPDT)
    m = re.match(r"^(.*), (the|a|an|la|le|les|l'|il|el|los|las|der|die|das|un|une)$", s, re.I)
    if m:                                                            # 'Godfather, The' / "Atalante, L'" (library order)
        art = m.group(2); s = (art + ("" if art.endswith("'") else " ") + m.group(1))
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")   # Amélie == Amelie
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s*[:\-–—]\s*", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"colour", "color", s)
    s = re.sub(r"\bneighbour", "neighbor", s)
    s = s.replace("sunset blvd", "sunset boulevard").replace("cleo from 5 to 7", "cleo de 5 a 7")
    s = re.sub(r"^ali fear eats the soul$", "fear eats the soul", s)   # British lists vs IMDb's US titles: Three Colours: Red == Three Colors: Red
    return re.sub(r"\s+", " ", s).strip()


class Resolver:
    """title+year -> tconst against the pool (which contains the catalogue too).
    Exact normalised match first; then a same-year film whose IMDb title contains the given one
    ('The Empire Strikes Back' -> 'Star Wars: Episode V - The Empire Strikes Back')."""

    def __init__(self, con):
        self.by, self.by_year, self.by_name, self.by_sq = {}, {}, {}, {}
        rows = con.execute("""SELECT tconst,title,original_title,year,votes FROM pool
                              UNION SELECT tconst,title,original_title,year,votes FROM films ORDER BY votes DESC""")
        seen = set()
        for t, title, orig, year, votes in rows:
            if t in seen:
                continue
            seen.add(t)
            names = {norm(title), norm(orig or "")} - {""}
            for name in names:
                self.by.setdefault((name, year), []).append(t)
                self.by_name.setdefault(name, []).append(t)
                self.by_sq.setdefault(name.replace(" ", ""), []).append((t, year))   # 'Wall-E' == 'WALL·E'
            self.by_year.setdefault(year, []).append((t, names))

    def __call__(self, title, year):
        raw, title = title, norm(title)
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
        # the list carries a subtitle IMDb doesn't: 'Gett: The Trial of Viviane Amsalem', 'Gangs of Wasseypur – Part 1'
        head = re.split(r"\s*(?::|\s[–—-]\s)\s*", raw, maxsplit=1)[0]
        if head != raw and len(norm(head)) >= 4:
            for y in years:
                c = self.by.get((norm(head), y))
                if c:
                    return c[0]
        for t, y in self.by_sq.get(title.replace(" ", ""), []):
            if not year or y in years:
                return t
        if not year:   # no year given: the best-known film with exactly that title
            c = self.by_name.get(title) or (self.by_name.get(norm(head)) if head != raw else None)
            if c:
                return c[0]
        return None


def parse_lines(text):
    """
    Pasted text -> [(line, title, year) or (line, tconst, None)], in order. Takes: 'Title (1999)',
    'Title [1999]', 'Title, 1999', '12. Title 1999', CSV rows 'Position,Name,Year', IMDb links/ids,
    and pages where the year sits on its own line under each title. Rank-only lines are dropped.
    """
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l and not re.fullmatch(r"(?i)(position|rank|#)?\s*,?\s*(name|title)\s*,\s*year.*", l)]
    is_year = lambda l: bool(re.fullmatch(r"\(?(18[89]\d|19\d\d|20[0-3]\d)\)?", l))
    is_rank = lambda l: bool(re.fullmatch(r"#?\d{1,4}[.)]?", l)) and not is_year(l)
    years_alone = sum(map(is_year, lines))
    alternating = years_alone >= 0.4 * max(1, len(lines) - years_alone - sum(map(is_rank, lines)))
    out = []
    for l in lines:
        m = TT.search(l)
        if m:
            out.append((l, m.group(0), None)); continue
        if alternating and is_year(l) and out and out[-1][2] is None and not TT.fullmatch(out[-1][1]):
            out[-1] = (out[-1][0] + " " + l, out[-1][1], int(l.strip("()")))   # the year under its title
            continue                                                         # (else it's a title: '1917')
        if is_rank(l):
            continue
        c = re.match(r'^\d+\s*,\s*"?(.+?)"?\s*,\s*(\d{4})\b', l)                  # CSV: 1,"Title, The",1999
        if c:
            out.append((l, c.group(1), int(c.group(2)))); continue
        t = re.sub(r"^#?\d{1,4}\s*[.):\-–]\s+", "", l)                                 # '12. Title'
        y = None
        m = (re.match(r"^(.*?)\s*[(\[]\s*(\d{4})\s*[)\]]", t)                        # 'Title (1999) …'
             or re.match(r"^(.*?)\s*[,–—-]\s*(\d{4})$", t) or re.match(r"^(.*\S)\s+(\d{4})$", t))
        if m and 1888 <= int(m.group(2)) <= 2035:
            t, y = m.group(1).strip(" ,-–—"), int(m.group(2))
        if t:
            out.append((l, t, y))
    return out


def resolve_text(con, text, resolver=None):
    """parse_lines + Resolver: [{line, title, year, tconst}] for the in-app list editor."""
    res = resolver or Resolver(con)
    out = []
    for line, t, y in parse_lines(text):
        if TT.fullmatch(t):
            out.append({"line": line, "title": None, "year": None, "tconst": t}); continue
        out.append({"line": line, "title": t, "year": y, "tconst": res(t, y)})
    return out


def save(con, name, tconsts, note="", category=None, source=None):
    tconsts = list(dict.fromkeys(t for t in tconsts if t))
    con.execute("""INSERT INTO list_meta (list, category, made_by, source, deleted) VALUES (?,?,NULL,?,0)
                   ON CONFLICT(list) DO UPDATE SET category=excluded.category, source=excluded.source, deleted=0,
                                                   updated=datetime('now')""",
                (name, category or list_category(name), source or ("computed" if "computed" in note else "")))
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


GENRE_DECADES = ["Horror", "Sci-Fi", "Action", "Comedy", "Thriller", "Drama", "Animation"]


def genre_decades(con, n=40, min_votes=25000):
    """
    'Horror · 2010s' etc., computed from the IMDb data in the catalogue with the Top-250 weighting
    (few votes pull a film toward the mean), so a decade's best isn't just its loudest. One list per
    genre and decade from the 1960s on, top `n` each. Re-run after setup.py refreshes the ratings.
    """
    for g in GENRE_DECADES:
        for dec in range(1960, 2030, 10):
            rows = con.execute("""SELECT tconst FROM films WHERE decade=? AND (',' || genres || ',') LIKE ? AND votes>=?
                                  ORDER BY (votes*rating + 100000*6.9)/(votes+100000.0) DESC, votes DESC LIMIT ?""",
                               (dec, f"%,{g},%", min_votes, n)).fetchall()
            if len(rows) >= 10:
                save(con, f"{g} · {dec}s", [r[0] for r in rows], note=" (computed from IMDb, weighted)")


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
    marked = {r[0]: r for r in con.execute("SELECT tconst, status, score FROM marks WHERE curator=?", (CURATOR,))}
    films = {r[0] for r in con.execute("SELECT tconst FROM films")}

    def film(title, year):
        t = res(title, year)
        if t and t not in films and add_from_pool(con, t, "letterboxd"):
            films.add(t)
        return t if t in films else None

    def mark(t, status, score=None, when=None):
        cur = marked.get(t)
        if cur is None:
            con.execute("""INSERT INTO marks (curator,tconst,status,score,source,updated)
                           VALUES (?,?,?,?,'letterboxd',COALESCE(?,datetime('now')))""", (CURATOR, t, status, score, when))
            marked[t] = {"tconst": t, "status": status, "score": score}
            event(con, "import", t, {"status": status, "score": score}, source="letterboxd", curator=CURATOR); return 1
        if score is not None and cur["score"] is None and cur["status"] == "seen":
            con.execute("UPDATE marks SET score=? WHERE curator=? AND tconst=?", (score, CURATOR, t)); return 1
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
            score = STARS_TO_SCORE.get(float(r["Rating"])) if r.get("Rating") else None
            k += mark(t, status, score, (r.get("Date") or None) and r["Date"] + " 12:00:00")
        print(f"{fname}: {k} marks"); n += k
    con.commit()
    for p in sorted(glob.glob(os.path.join(folder, "lists", "*.csv"))):
        name = "LB: " + os.path.splitext(os.path.basename(p))[0].replace("-", " ")
        from_csv(con, name, p)
    print(f"letterboxd: {n} marks imported for {CURATOR} (existing marks untouched)")


def show(con):
    curators = [r[0] for r in con.execute("SELECT id FROM curators ORDER BY ord")]
    for r in con.execute("SELECT list, COUNT(*) n FROM lists GROUP BY list ORDER BY list"):
        cov = []
        for c in curators:
            s = con.execute("""SELECT SUM(m.status='seen'), ROUND(AVG(CASE WHEN m.status='seen' THEN m.score END),1)
                               FROM lists l JOIN marks m ON m.tconst=l.tconst AND m.curator=? WHERE l.list=?""", (c, r["list"])).fetchone()
            cov.append(f"{c}: seen {s[0] or 0}" + (f", avg {s[1]}" if s[1] is not None else ""))
        print(f"{r['list']:<28} {r['n']:>5} films   " + "   ".join(cov))


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
        con.execute("DELETE FROM lists WHERE list=?", (a[1],)); con.execute("DELETE FROM list_meta WHERE list=?", (a[1],))
        con.commit(); print("dropped", a[1])
    elif cmd == "decades":
        genre_decades(con)
    elif cmd == "all":
        for p in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lists", "*.csv"))):
            name = os.path.splitext(os.path.basename(p))[0]
            con.execute("DELETE FROM lists WHERE list=?", (name,))   # reload: a fixed list replaces the old one
            from_csv(con, name, p)
    elif cmd == "show":
        show(con); return
    else:
        sys.exit(__doc__)
    write_exports()


if __name__ == "__main__":
    main()

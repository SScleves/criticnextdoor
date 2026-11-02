#!/usr/bin/env python3
"""
Step 3: the catalogue.  python app.py  ->  http://localhost:8000

Every save updates `my` (current state), appends to `events` (history), and
rewrites exports/*.jsonl; if exports/ is a git repo it commits (and pushes when
a remote is set). Your words never live only in one sqlite file.

Env: FILMCAT_DB, FILMCAT_EXPORTS, FILMCAT_HOST (default 0.0.0.0),
     FILMCAT_PORT (8000), FILMCAT_NO_BROWSER=1 to not open a tab on start.
"""
import csv, io, json, os, random, sqlite3, subprocess, threading
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from setup import migrate

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("FILMCAT_DB", os.path.join(HERE, "films.db"))
EXPORTS = os.environ.get("FILMCAT_EXPORTS", os.path.join(HERE, "exports"))
app = FastAPI(title="filmcat")

FILM_COLS = ("f.tconst,f.title,f.year,f.runtime,f.genres,f.rating,f.votes,f.directors,f.writers,"
             "f.poster,f.overview,f.countries,f.keywords,f.in_library")
MY_COLS = "m.status,m.tier,m.tags,m.note,m.times,m.when_seen,m.memory,m.source,m.updated"


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def event(con, kind, tconst, payload, source="me"):
    con.execute("INSERT INTO events (kind,tconst,payload,source) VALUES (?,?,?,?)",
                (kind, tconst, json.dumps(payload, ensure_ascii=False), source))


def split_tags(s):
    tags = [t for t in (s or "").split(",") if t]
    return [t for t in tags if not t.startswith("-")], [t[1:] for t in tags if t.startswith("-")]


def mark_rows(con):
    sql = f"""SELECT {FILM_COLS},{MY_COLS},t.score FROM my m JOIN films f ON f.tconst=m.tconst
              LEFT JOIN tiers t ON t.name=m.tier ORDER BY m.updated"""
    for r in con.execute(sql):
        d = dict(r)
        d["tags_pos"], d["tags_neg"] = split_tags(d.pop("tags"))
        yield d


_export_lock = threading.Lock()


def write_exports():
    """marks / pairs / events / vocab as jsonl in EXPORTS, then git commit + push if set up."""
    with _export_lock:
        os.makedirs(EXPORTS, exist_ok=True)
        con = db()

        def dump(name, rows):
            with open(os.path.join(EXPORTS, name), "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

        dump("marks.jsonl", mark_rows(con))
        dump("pairs.jsonl", (dict(r) for r in con.execute("SELECT * FROM pairs ORDER BY id")))
        dump("events.jsonl", (dict(r) for r in con.execute("SELECT * FROM events ORDER BY id")))
        dump("vocab.jsonl", [{"tiers": [dict(r) for r in con.execute("SELECT * FROM tiers ORDER BY ord")],
                              "tags": [dict(r) for r in con.execute("SELECT * FROM tags ORDER BY grp,name")]}])
        if os.path.isdir(os.path.join(EXPORTS, ".git")):
            g = ["git", "-C", EXPORTS]
            subprocess.run(g + ["add", "-A"], capture_output=True)
            r = subprocess.run(g + ["commit", "-q", "-m", "marks"], capture_output=True)
            has_remote = subprocess.run(g + ["remote"], capture_output=True, text=True).stdout.strip()
            if r.returncode == 0 and has_remote:
                try:
                    subprocess.run(g + ["push", "-q"], capture_output=True, timeout=60)
                except subprocess.TimeoutExpired:
                    pass


def export_later():
    threading.Thread(target=write_exports, daemon=True).start()


@app.on_event("startup")
def startup():
    migrate(db())


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/api/meta")
def meta():
    con = db()
    genres = sorted({g for (gs,) in con.execute("SELECT genres FROM films") for g in gs.split(",") if g})
    decades = [r[0] for r in con.execute("SELECT DISTINCT decade FROM films ORDER BY decade")]
    tiers = [dict(r) for r in con.execute("SELECT * FROM tiers ORDER BY ord")]
    tags = [dict(r) for r in con.execute("SELECT * FROM tags ORDER BY grp, name")]
    stats = dict(con.execute("""SELECT
        (SELECT COUNT(*) FROM films) AS total,
        (SELECT COUNT(*) FROM my WHERE status='seen') AS seen,
        (SELECT COUNT(*) FROM my WHERE status='want') AS want,
        (SELECT COUNT(*) FROM my WHERE status='not') AS notseen,
        (SELECT COUNT(*) FROM my WHERE status='seen' AND note IS NOT NULL AND note<>'') AS noted,
        (SELECT COUNT(*) FROM my WHERE status='seen' AND tier IS NOT NULL) AS tiered,
        (SELECT COUNT(*) FROM pairs WHERE skipped=0) AS pairs,
        (SELECT COUNT(*) FROM films WHERE in_library=1) AS library
    """).fetchone())
    return {"genres": genres, "decades": decades, "tiers": tiers, "tags": tags, "stats": stats}


def where_clause(genre, decade, status, tier, tag, q, library):
    where, args = [], []
    if genre:
        where.append("(',' || f.genres || ',') LIKE ?"); args.append(f"%,{genre},%")
    if decade:
        where.append("f.decade = ?"); args.append(decade)
    if status == "unmarked":
        where.append("m.status IS NULL")
    elif status:
        where.append("m.status = ?"); args.append(status)
    if tier:
        where.append("m.tier = ?"); args.append(tier)
    if tag:
        where.append("(',' || IFNULL(m.tags,'') || ',') LIKE ?"); args.append(f"%,{tag},%")
    if q:
        where.append("(f.title LIKE ? OR f.original_title LIKE ? OR f.directors LIKE ?)"); args += [f"%{q}%"] * 3
    if library:
        where.append("f.in_library = 1")
    return ("WHERE " + " AND ".join(where)) if where else "", args


@app.get("/api/films")
def films(genre: str = "", decade: int = 0, status: str = "", tier: str = "",
          tag: str = "", q: str = "", library: int = 0, sort: str = "rating",
          offset: int = 0, limit: int = Query(60, le=500)):
    where, args = where_clause(genre, decade, status, tier, tag, q, library)
    order = {"rating": "f.rating DESC, f.votes DESC", "votes": "f.votes DESC",
             "year": "f.year DESC", "year_asc": "f.year ASC", "title": "f.title",
             "updated": "m.updated DESC", "random": "RANDOM()"}.get(sort, "f.rating DESC")
    con = db()
    rows = [dict(r) for r in con.execute(
        f"""SELECT {FILM_COLS},{MY_COLS} FROM films f LEFT JOIN my m ON m.tconst=f.tconst
            {where} ORDER BY {order} LIMIT ? OFFSET ?""", args + [limit, offset])]
    total = con.execute(f"SELECT COUNT(*) FROM films f LEFT JOIN my m ON m.tconst=f.tconst {where}",
                        args).fetchone()[0]
    return {"total": total, "films": rows}


class Mark(BaseModel):
    tconst: str
    status: str | None = None
    tier: str | None = None
    tags: list[str] = []
    note: str | None = None
    times: str | None = None
    when_seen: str | None = None
    memory: str | None = None


@app.post("/api/mark")
def mark(m: Mark):
    con = db()
    tags = ",".join(t for t in m.tags if t)
    con.execute("""INSERT INTO my (tconst,status,tier,tags,note,times,when_seen,memory,source,updated)
                   VALUES (?,?,?,?,?,?,?,?,'me',datetime('now'))
                   ON CONFLICT(tconst) DO UPDATE SET status=excluded.status, tier=excluded.tier,
                   tags=excluded.tags, note=excluded.note, times=excluded.times, when_seen=excluded.when_seen,
                   memory=excluded.memory, source='me', updated=datetime('now')""",
                (m.tconst, m.status, m.tier, tags, m.note, m.times, m.when_seen, m.memory))
    event(con, "mark", m.tconst, m.model_dump())
    con.commit()
    export_later()
    return {"ok": True}


class Tag(BaseModel):
    name: str
    grp: str = "custom"


@app.post("/api/tag")
def add_tag(t: Tag):
    name = t.name.strip().lower().replace(" ", "-").lstrip("-")
    con = db()
    con.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (name, t.grp))
    con.commit()
    return {"name": name}


# ---- compare: "which did you like more?" ----------------------------------

@app.get("/api/pair")
def pair():
    con = db()
    seen = [dict(r) for r in con.execute(
        f"SELECT {FILM_COLS},{MY_COLS} FROM films f JOIN my m ON m.tconst=f.tconst WHERE m.status='seen'")]
    if len(seen) < 2:
        return {"a": None, "b": None, "reason": "mark at least two films as seen first"}
    done = {tuple(sorted((r[0], r[1]))) for r in con.execute("SELECT a,b FROM pairs")}
    for _ in range(300):
        a = random.choice(seen)
        same = [f for f in seen if f["tier"] == a["tier"] and f["tconst"] != a["tconst"]]
        # mostly compare inside a tier (that's where the order is unknown), sometimes across
        cands = same if (a["tier"] and same and random.random() < 0.7) else [f for f in seen if f["tconst"] != a["tconst"]]
        b = random.choice(cands)
        if tuple(sorted((a["tconst"], b["tconst"]))) not in done:
            return {"a": a, "b": b}
    return {"a": None, "b": None, "reason": "you've compared every pair there is - mark more films as seen"}


class Pair(BaseModel):
    a: str
    b: str
    winner: str | None = None


@app.post("/api/pair")
def save_pair(p: Pair):
    if p.winner not in (None, p.a, p.b):
        raise HTTPException(400, "winner must be a, b or null")
    con = db()
    con.execute("INSERT INTO pairs (a,b,winner,skipped) VALUES (?,?,?,?)",
                (p.a, p.b, p.winner, 0 if p.winner else 1))
    event(con, "pair", p.winner or p.a, p.model_dump())
    con.commit()
    export_later()
    return {"pairs": con.execute("SELECT COUNT(*) FROM pairs WHERE skipped=0").fetchone()[0]}


# ---- pool: add a film the filter left out ----------------------------------

@app.get("/api/pool")
def pool(q: str = "", limit: int = Query(30, le=100)):
    con = db()
    rows = con.execute("""SELECT p.* FROM pool p LEFT JOIN films f ON f.tconst=p.tconst
                          WHERE f.tconst IS NULL AND (p.title LIKE ? OR p.original_title LIKE ? OR p.directors LIKE ?)
                          ORDER BY p.votes DESC LIMIT ?""", [f"%{q}%"] * 3 + [limit]).fetchall()
    return {"films": [dict(r) for r in rows]}


class Add(BaseModel):
    tconst: str
    added: str = "pool"


def add_from_pool(con, tconst, added="pool"):
    """Copy one pool row into films. Returns False if it isn't in the pool."""
    r = con.execute("SELECT * FROM pool WHERE tconst=?", (tconst,)).fetchone()
    if not r:
        return False
    con.execute("""INSERT OR IGNORE INTO films (tconst,title,original_title,year,decade,runtime,genres,rating,votes,
                                                directors,writers,added) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["tconst"], r["title"], r["original_title"], r["year"], r["year"] // 10 * 10, r["runtime"],
                 r["genres"], r["rating"], r["votes"], r["directors"], r["writers"], added))
    event(con, "add", tconst, {"added": added}, source=added)
    return True


@app.post("/api/add")
def add(a: Add):
    con = db()
    if not add_from_pool(con, a.tconst, a.added):
        raise HTTPException(404, "not in the pool")
    con.commit()
    r = con.execute(f"SELECT {FILM_COLS},{MY_COLS} FROM films f LEFT JOIN my m ON m.tconst=f.tconst WHERE f.tconst=?",
                    (a.tconst,)).fetchone()
    return dict(r)


# ---- export -----------------------------------------------------------------

CSV_COLS = ["tconst", "title", "year", "directors", "genres", "countries", "runtime", "rating", "status", "tier",
            "score", "tags_pos", "tags_neg", "times", "when_seen", "memory", "note", "source", "updated",
            "in_library", "keywords", "overview"]


@app.get("/api/export.csv")
def export_csv():
    """Everything you've marked (seen, want, not seen) with film features. The model's input."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    for d in mark_rows(db()):
        d["tags_pos"] = " ".join(d["tags_pos"]); d["tags_neg"] = " ".join(d["tags_neg"])
        w.writerow([d.get(c) for c in CSV_COLS])
    buf.seek(0)
    return StreamingResponse(buf, media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=my_taste.csv"})


@app.get("/api/export.json")
def export_json():
    con = db()
    return {"marks": list(mark_rows(con)),
            "pairs": [dict(r) for r in con.execute("SELECT * FROM pairs ORDER BY id")],
            "tiers": [dict(r) for r in con.execute("SELECT * FROM tiers ORDER BY ord")],
            "tags": [dict(r) for r in con.execute("SELECT * FROM tags ORDER BY grp,name")]}


if __name__ == "__main__":
    import uvicorn, webbrowser, threading as th
    host = os.environ.get("FILMCAT_HOST", "0.0.0.0")
    port = int(os.environ.get("FILMCAT_PORT", "8000"))
    if not os.environ.get("FILMCAT_NO_BROWSER"):
        th.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run(app, host=host, port=port)

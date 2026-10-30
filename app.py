#!/usr/bin/env python3
"""
Step 3: the catalogue.  python app.py  ->  http://localhost:8000
"""
import csv, io, os, sqlite3
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

DB = os.environ.get("FILMCAT_DB", "films.db")
HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="filmcat")


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


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
        (SELECT COUNT(*) FROM my WHERE status='seen' AND note IS NOT NULL AND note<>'') AS noted
    """).fetchone())
    return {"genres": genres, "decades": decades, "tiers": tiers, "tags": tags, "stats": stats}


@app.get("/api/films")
def films(genre: str = "", decade: int = 0, status: str = "", tier: str = "",
          tag: str = "", q: str = "", sort: str = "rating",
          offset: int = 0, limit: int = Query(60, le=500)):
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
        where.append("(f.title LIKE ? OR f.original_title LIKE ?)"); args += [f"%{q}%"] * 2
    order = {"rating": "f.rating DESC, f.votes DESC", "votes": "f.votes DESC",
             "year": "f.year DESC", "year_asc": "f.year ASC", "title": "f.title",
             "updated": "m.updated DESC"}.get(sort, "f.rating DESC")
    sql = f"""SELECT f.tconst,f.title,f.year,f.runtime,f.genres,f.rating,f.votes,f.poster,
                     f.overview,f.countries,f.keywords,
                     m.status,m.tier,m.tags,m.note
              FROM films f LEFT JOIN my m ON m.tconst=f.tconst
              {'WHERE ' + ' AND '.join(where) if where else ''}
              ORDER BY {order} LIMIT ? OFFSET ?"""
    con = db()
    rows = [dict(r) for r in con.execute(sql, args + [limit, offset])]
    total = con.execute(f"""SELECT COUNT(*) FROM films f LEFT JOIN my m ON m.tconst=f.tconst
              {'WHERE ' + ' AND '.join(where) if where else ''}""", args).fetchone()[0]
    return {"total": total, "films": rows}


class Mark(BaseModel):
    tconst: str
    status: str | None = None
    tier: str | None = None
    tags: list[str] = []
    note: str | None = None


@app.post("/api/mark")
def mark(m: Mark):
    con = db()
    con.execute("""INSERT INTO my (tconst,status,tier,tags,note,updated)
                   VALUES (?,?,?,?,?,datetime('now'))
                   ON CONFLICT(tconst) DO UPDATE SET status=excluded.status, tier=excluded.tier,
                   tags=excluded.tags, note=excluded.note, updated=datetime('now')""",
                (m.tconst, m.status, m.tier, ",".join(t for t in m.tags if t), m.note))
    con.commit()
    return {"ok": True}


class Tag(BaseModel):
    name: str
    grp: str = "custom"


@app.post("/api/tag")
def add_tag(t: Tag):
    name = t.name.strip().lower().replace(" ", "-")
    con = db()
    con.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (name, t.grp))
    con.commit()
    return {"name": name}


@app.get("/api/export.csv")
def export():
    """Your taste file. Only films marked seen, with tier/tags/note. Feed this to the recommender."""
    con = db()
    rows = con.execute("""SELECT f.tconst,f.title,f.year,f.genres,f.countries,f.runtime,
                                 f.rating AS imdb, m.tier, t.score, m.tags, m.note, f.keywords, f.overview
                          FROM my m JOIN films f ON f.tconst=m.tconst
                          LEFT JOIN tiers t ON t.name=m.tier
                          WHERE m.status='seen' ORDER BY t.score DESC, f.title""").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["imdb_id", "title", "year", "genres", "countries", "runtime", "imdb_rating",
                "tier", "score", "tags", "note", "tmdb_keywords", "overview"])
    for r in rows:
        w.writerow(list(r))
    buf.seek(0)
    return StreamingResponse(buf, media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=my_taste.csv"})


if __name__ == "__main__":
    import uvicorn, webbrowser, threading
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8000")).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)

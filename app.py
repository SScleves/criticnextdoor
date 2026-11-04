#!/usr/bin/env python3
"""
Critic Next Door, phase 1: the catalogue.  python app.py  ->  http://localhost:8000

Two curators (see setup.CURATORS), no login: every call says who is marking. Every save
updates `marks` (current state, one row per curator per film), appends to `events`, and
rewrites exports/*; if exports/ is a git repo it commits (and pushes when a remote is set).

Env: FILMCAT_DB, FILMCAT_EXPORTS, FILMCAT_HOST (default 0.0.0.0),
     FILMCAT_PORT (8000), FILMCAT_NO_BROWSER=1 to not open a tab on start.
"""
import csv, io, json, os, random, shutil, sqlite3, subprocess, threading, time
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from setup import migrate, DEFAULT_CURATOR

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("FILMCAT_DB", os.path.join(HERE, "films.db"))
EXPORTS = os.environ.get("FILMCAT_EXPORTS", os.path.join(HERE, "exports"))
app = FastAPI(title="critic next door")

FILM_COLS = ("f.tconst,f.title,f.year,f.decade,f.runtime,f.genres,f.rating,f.votes,f.directors,f.writers,"
             "f.poster,f.overview,f.countries,f.language,f.keywords,f.in_library,f.rt,f.metacritic,"
             "(SELECT group_concat(l.list || '#' || l.rank, '|') FROM lists l WHERE l.tconst=f.tconst) AS lists,"
             "(SELECT group_concat(fl.label, ',') FROM film_labels fl WHERE fl.tconst=f.tconst) AS labels,"
             "(SELECT group_concat(fl.label, ',') FROM film_labels fl WHERE fl.tconst=f.tconst AND fl.source='auto') AS labels_auto,"
             "(SELECT group_concat(mm.curator || ':' || IFNULL(mm.status,'') || ':' || IFNULL(mm.score,''), '|')"
             " FROM marks mm WHERE mm.tconst=f.tconst) AS all_marks,"
             "(SELECT group_concat(qq.curator, ',') FROM queue qq WHERE qq.tconst=f.tconst) AS queued")
# the current curator's mark. `tags` is rebuilt as the old comma string so the page and exports stay simple
MY_COLS = ("m.curator,m.status,m.score,(SELECT sc.name FROM scale sc WHERE sc.score=m.score) AS tier,m.rank,"
           "m.times,m.when_seen,m.memory,m.about,m.reached,m.for_whom,m.avoid,m.note,m.source,m.updated,"
           "(SELECT group_concat(CASE WHEN mt.polarity<0 THEN '-'||mt.tag ELSE mt.tag END, ',') FROM mark_tags mt"
           " WHERE mt.curator=m.curator AND mt.tconst=m.tconst) AS tags,"
           "(SELECT group_concat(mf.feeling || ':' || mf.intensity, ',') FROM mark_feelings mf"
           " WHERE mf.curator=m.curator AND mf.tconst=m.tconst) AS feelings")
NOTE_FIELDS = ("about", "reached", "for_whom", "avoid", "note")
NOTED_SQL = "(IFNULL(m.about,'')<>'' OR IFNULL(m.reached,'')<>'' OR IFNULL(m.for_whom,'')<>'' OR IFNULL(m.avoid,'')<>'' OR IFNULL(m.note,'')<>'')"


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def event(con, kind, tconst, payload, source="me", curator=None):
    con.execute("INSERT INTO events (kind,tconst,payload,source,curator) VALUES (?,?,?,?,?)",
                (kind, tconst, json.dumps(payload, ensure_ascii=False), source, curator))


def split_tags(s):
    tags = [t for t in (s or "").split(",") if t]
    return [t for t in tags if not t.startswith("-")], [t[1:] for t in tags if t.startswith("-")]


def parts(s, sep):
    return [x.strip() for x in (s or "").split(sep) if x.strip()]


def curators(con):
    return [dict(r) for r in con.execute("SELECT * FROM curators ORDER BY ord")]


def check_curator(con, c):
    if not con.execute("SELECT 1 FROM curators WHERE id=?", (c,)).fetchone():
        raise HTTPException(400, f"unknown curator {c!r}")
    return c


def has_note(d):
    return any((d.get(k) or "").strip() for k in NOTE_FIELDS)


def mark_rows(con, curator=None):
    sql = f"""SELECT {FILM_COLS},{MY_COLS} FROM marks m JOIN films f ON f.tconst=m.tconst
              {"WHERE m.curator=?" if curator else ""} ORDER BY m.curator, m.updated"""
    for r in con.execute(sql, (curator,) if curator else ()):
        d = dict(r)
        d["tags_pos"], d["tags_neg"] = split_tags(d.pop("tags"))
        d["labels"] = parts(d["labels"], ",")
        d["feelings"] = parts(d["feelings"], ",")   # "wrecked:3"
        yield d


def scale_name(con):
    """score -> label for people: the name if there is one, else the signed number."""
    return {r["score"]: (r["name"] or f"{r['score']:+d}") for r in con.execute("SELECT * FROM scale")}


# ---- exports: the product --------------------------------------------------

def notes_md(con, curator):
    """
    One markdown document per curator: every seen film they wrote about, in a fixed shape a
    model (or a person) can read without a schema. This is what phase 2 feeds to Claude.
    """
    name = con.execute("SELECT name FROM curators WHERE id=?", (curator,)).fetchone()
    names = scale_name(con)
    scale = ", ".join(f"{sc:+d} {names[sc]}" if names[sc] != f"{sc:+d}" else f"{sc:+d}" for sc in range(10, -11, -1))
    feel = ", ".join(f"{r['name']} ({r['es']})" if r["es"] else r["name"] for r in con.execute("SELECT * FROM feelings"))
    out = [f"# {name[0] if name else curator}'s film notes", "",
           f"Score runs from +10 (best) to -10 (worst): {scale}.",
           f"End-feeling is what the film left me with when it ended, intensity 1-3. The vocabulary: {feel}.",
           "Tags marked + are why the film worked for me; tags marked − are what went wrong.", ""]
    rows = [d for d in mark_rows(con, curator)
            if d["status"] == "seen" and (d["score"] is not None or has_note(d) or d["tags_pos"] or d["tags_neg"] or d["feelings"])]
    rows.sort(key=lambda d: (-(d["score"] if d["score"] is not None else -99), d["rank"] or 999, d["title"]))
    for d in rows:
        out.append(f"## {d['title']} ({d['year']})")
        sc = d["score"]
        line = [f"score: {sc:+d}" + (f" ({names[sc]})" if names.get(sc) != f"{sc:+d}" else "") if sc is not None else "score: none yet"]
        if d["rank"]: line.append(f"rank at that score: {d['rank']}")
        if d["directors"]: line.append(f"director: {d['directors']}")
        if d["genres"]: line.append(f"imdb genres: {d['genres']}")
        if d["labels"]: line.append("labels: " + ", ".join(d["labels"]))
        if d["runtime"]: line.append(f"{d['runtime']} min")
        if d["language"]: line.append(f"language: {d['language']}")
        out.append("- " + " · ".join(line))
        if d["feelings"]: out.append("- end-feeling: " + ", ".join(f"{f.split(':')[0]} {f.split(':')[1]}/3" for f in d["feelings"]))
        tags = [f"+{t}" for t in d["tags_pos"]] + [f"−{t}" for t in d["tags_neg"]]
        if tags: out.append("- tags: " + " ".join(tags))
        sits = [x for x in (d["times"], d["when_seen"], d["memory"]) if x]
        if sits: out.append("- seen: " + ", ".join(sits))
        for k, label in (("about", "What it's about"), ("reached", "Why it reached me"),
                         ("for_whom", "Who it's for"), ("avoid", "Who should avoid it"), ("note", "Also")):
            v = (d.get(k) or "").strip()
            if v: out.append(f"- {label}: {v}")
        out.append("")
    return "\n".join(out)


CSV_COLS = ["curator", "tconst", "title", "year", "directors", "genres", "labels", "countries", "language", "runtime",
            "rating", "rt", "metacritic", "lists", "status", "score", "tier", "rank", "feelings", "tags_pos", "tags_neg",
            "times", "when_seen", "memory", "about", "reached", "for_whom", "avoid", "note",
            "source", "updated", "in_library", "keywords", "overview"]


def csv_text(con, curator=None):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    for d in mark_rows(con, curator):
        d["tags_pos"] = " ".join(d["tags_pos"]); d["tags_neg"] = " ".join(d["tags_neg"]); d["labels"] = " ".join(d["labels"])
        d["feelings"] = " ".join(d["feelings"])
        w.writerow([d.get(c) for c in CSV_COLS])
    return buf.getvalue()


_export_lock = threading.Lock()


def write_exports():
    """marks / pairs / events / vocab as jsonl in EXPORTS, a csv and a notes.md per curator, then git."""
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
        dump("queue.jsonl", (dict(r) for r in con.execute("SELECT * FROM queue ORDER BY curator, added")))
        dump("vocab.jsonl", [{"scale": [dict(r) for r in con.execute("SELECT * FROM scale ORDER BY score DESC")],
                              "feelings": [dict(r) for r in con.execute("SELECT * FROM feelings")],
                              "tags": [dict(r) for r in con.execute("SELECT * FROM tags ORDER BY grp,name")],
                              "labels": [dict(r) for r in con.execute("SELECT * FROM labels ORDER BY grp,name")],
                              "curators": curators(con)}])
        with open(os.path.join(EXPORTS, "marks.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(csv_text(con))
        for c in curators(con):
            with open(os.path.join(EXPORTS, f"notes_{c['id']}.md"), "w", encoding="utf-8") as f:
                f.write(notes_md(con, c["id"]))
            with open(os.path.join(EXPORTS, f"marks_{c['id']}.csv"), "w", encoding="utf-8", newline="") as f:
                f.write(csv_text(con, c["id"]))
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
    # the one-time move from `my` to `marks` rewrites the db: keep a copy from before it
    con = db()
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='my'").fetchone():
        shutil.copy(DB, DB + ".before-curators." + time.strftime("%Y%m%d-%H%M%S"))
    migrate(con)


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/api/meta")
def meta(curator: str = DEFAULT_CURATOR):
    con = db()
    check_curator(con, curator)
    genres = sorted({g for (gs,) in con.execute("SELECT genres FROM films") for g in gs.split(",") if g})
    decades = [r[0] for r in con.execute("SELECT DISTINCT decade FROM films ORDER BY decade")]
    languages = [dict(r) for r in con.execute("SELECT language AS name, COUNT(*) AS n FROM films WHERE IFNULL(language,'')<>'' GROUP BY language ORDER BY n DESC")]
    scale = [dict(r) for r in con.execute("""SELECT sc.score, sc.name,
               (SELECT COUNT(*) FROM marks m WHERE m.score=sc.score AND m.curator=? AND m.status='seen') AS n
               FROM scale sc ORDER BY sc.score DESC""", (curator,))]
    feelings = [dict(r) for r in con.execute("""SELECT fe.name, fe.grp, fe.es,
               (SELECT COUNT(*) FROM mark_feelings mf WHERE mf.feeling=fe.name AND mf.curator=?) AS n,
               (SELECT COUNT(*) FROM mark_feelings mf WHERE mf.feeling=fe.name) AS n_all FROM feelings fe ORDER BY fe.rowid""", (curator,))]
    tags = [dict(r) for r in con.execute("""SELECT t.name, t.grp,
               (SELECT COUNT(*) FROM mark_tags mt WHERE mt.tag=t.name AND mt.curator=? AND mt.polarity>0) AS pos,
               (SELECT COUNT(*) FROM mark_tags mt WHERE mt.tag=t.name AND mt.curator=? AND mt.polarity<0) AS neg,
               (SELECT COUNT(*) FROM mark_tags mt WHERE mt.tag=t.name) AS n
               FROM tags t ORDER BY t.grp, t.name""", (curator, curator))]
    labels = [dict(r) for r in con.execute("""SELECT l.name, l.grp,
               (SELECT COUNT(*) FROM film_labels fl WHERE fl.label=l.name) AS n FROM labels l ORDER BY l.grp, l.name""")]
    stats = dict(con.execute(f"""SELECT
        (SELECT COUNT(*) FROM films) AS total,
        (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='seen') AS seen,
        (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='want') AS want,
        (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='not') AS notseen,
        (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='seen' AND {NOTED_SQL}) AS noted,
        (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='seen' AND score IS NOT NULL) AS tiered,
        (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='seen' AND EXISTS (SELECT 1 FROM mark_feelings mf WHERE mf.curator=m.curator AND mf.tconst=m.tconst)) AS felt,
        (SELECT COUNT(*) FROM queue WHERE curator=:c) AS queue,
        (SELECT COUNT(*) FROM pairs WHERE curator=:c AND skipped=0) AS pairs,
        (SELECT COUNT(*) FROM films WHERE in_library=1) AS library
    """, {"c": curator}).fetchone())
    queues = {c["id"]: con.execute("SELECT COUNT(*) FROM queue WHERE curator=?", (c["id"],)).fetchone()[0] for c in curators(con)}
    return {"curator": curator, "curators": curators(con), "genres": genres, "decades": decades, "languages": languages,
            "scale": scale, "feelings": feelings, "tags": tags, "labels": labels, "stats": stats, "queues": queues}


def where_clause(genre, decade, status, tier, tag, q, library, lst="", label="", feeling="", queue="", score="", lang=""):
    """queue = a curator id: only films in that person's to-write queue."""
    """Returns (join_sql, where_sql, args). The list join comes first, so its arg does too."""
    join, where, args = "", [], []
    if lst:
        join = "JOIN lists lr ON lr.tconst=f.tconst AND lr.list=?"; args.append(lst)
    if genre:
        where.append("(',' || f.genres || ',') LIKE ?"); args.append(f"%,{genre},%")
    if label:
        where.append("EXISTS (SELECT 1 FROM film_labels fl WHERE fl.tconst=f.tconst AND fl.label=?)"); args.append(label)
    if decade:
        where.append("f.decade = ?"); args.append(decade)
    if status == "unmarked":
        where.append("m.status IS NULL")
    elif status == "seen_nonote":
        where.append(f"m.status='seen' AND NOT {NOTED_SQL}")
    elif status == "seen_notier":
        where.append("m.status='seen' AND m.score IS NULL")
    elif status == "seen_nofeeling":
        where.append("m.status='seen' AND NOT EXISTS (SELECT 1 FROM mark_feelings mf WHERE mf.curator=m.curator AND mf.tconst=f.tconst)")
    elif status:
        where.append("m.status = ?"); args.append(status)
    if score != "" and score is not None:
        where.append("m.score = ?"); args.append(int(score))
    elif tier:   # old clients: a tier name
        where.append("m.score = (SELECT score FROM scale WHERE name=?)"); args.append(tier)
    if feeling:
        where.append("EXISTS (SELECT 1 FROM mark_feelings mf WHERE mf.curator=m.curator AND mf.tconst=f.tconst AND mf.feeling=?)"); args.append(feeling)
    if queue:
        where.append("EXISTS (SELECT 1 FROM queue qq WHERE qq.curator=? AND qq.tconst=f.tconst)"); args.append(queue)
    if tag:
        where.append("EXISTS (SELECT 1 FROM mark_tags mt WHERE mt.curator=m.curator AND mt.tconst=f.tconst AND mt.tag=?)"); args.append(tag)
    if q:
        where.append("(f.title LIKE ? OR f.original_title LIKE ? OR f.directors LIKE ?)"); args += [f"%{q}%"] * 3
    if lang:
        where.append("f.language = ?"); args.append(lang)
    if library:
        where.append("f.in_library = 1")
    return join, ("WHERE " + " AND ".join(where)) if where else "", args


def film_query(curator, join, where, order, args, limit=None, offset=0):
    """The curator's mark joins before the list join, so the args order is (curator, list, filters...)."""
    sql = f"""SELECT {FILM_COLS},{MY_COLS} FROM films f LEFT JOIN marks m ON m.tconst=f.tconst AND m.curator=? {join}
              {where} ORDER BY {order}"""
    a = [curator] + args
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"; a += [limit, offset]
    return sql, a


@app.get("/api/films")
def films(curator: str = DEFAULT_CURATOR, genre: str = "", decade: int = 0, status: str = "", tier: str = "", score: str = "",
          tag: str = "", label: str = "", feeling: str = "", queue: str = "", q: str = "", library: int = 0, lst: str = "", lang: str = "",
          sort: str = "rating", offset: int = 0, limit: int = Query(60, le=500)):
    con = db()
    check_curator(con, curator)
    if queue in ("1", "me"):
        queue = curator
    join, where, args = where_clause(genre, decade, status, tier, tag, q, library, lst, label, feeling, queue, score, lang)
    order = {"rating": "((f.votes*f.rating + 25000*6.9)/(f.votes+25000.0)) DESC, f.votes DESC", "rating_raw": "f.rating DESC, f.votes DESC", "votes": "f.votes DESC",
             "year": "f.year DESC", "year_asc": "f.year ASC", "title": "f.title",
             "updated": "m.updated DESC", "random": "RANDOM()",
             "rt": "f.rt DESC NULLS LAST, f.rating DESC",
             "tier": "m.score DESC NULLS LAST, m.rank ASC NULLS LAST, f.rating DESC",
             "queue": "(SELECT added FROM queue qq WHERE qq.curator=m.curator AND qq.tconst=f.tconst) ASC, f.rating DESC",
             "rank": "lr.rank" if lst else "f.rating DESC"}.get(sort, "f.rating DESC")
    sql, a = film_query(curator, join, where, order, args, limit, offset)
    rows = [dict(r) for r in con.execute(sql, a)]
    total = con.execute(f"SELECT COUNT(*) FROM films f LEFT JOIN marks m ON m.tconst=f.tconst AND m.curator=? {join} {where}",
                        [curator] + args).fetchone()[0]
    return {"total": total, "films": rows}


@app.get("/api/film/{tconst}")
def film(tconst: str, curator: str = DEFAULT_CURATOR):
    con = db()
    sql, a = film_query(curator, "", "WHERE f.tconst=?", "f.title", [tconst])
    r = con.execute(sql, a).fetchone()
    if not r:
        raise HTTPException(404, "no such film")
    return dict(r)


@app.get("/api/lists")
def lists():
    """Each loaded list, with how much of it every curator has done: the consolidated view of a list-as-project."""
    con = db()
    cs = curators(con)
    out = []
    for r in con.execute("SELECT list, COUNT(*) AS total FROM lists GROUP BY list ORDER BY list"):
        d = {"list": r["list"], "total": r["total"], "by": {}}
        for c in cs:
            s = con.execute(f"""SELECT SUM(m.status='seen') AS seen, SUM(m.status='want') AS want,
                                       SUM(m.status='seen' AND m.score IS NOT NULL) AS tiered,
                                       SUM(m.status='seen' AND {NOTED_SQL}) AS noted,
                                       ROUND(AVG(CASE WHEN m.status='seen' THEN m.score END), 1) AS avg_score,
                                       (SELECT COUNT(*) FROM queue qq JOIN lists l2 ON l2.tconst=qq.tconst AND l2.list=l.list WHERE qq.curator=m.curator) AS queued
                                FROM lists l JOIN marks m ON m.tconst=l.tconst AND m.curator=?
                                WHERE l.list=?""", (c["id"], r["list"])).fetchone()
            d["by"][c["id"]] = {k: (s[k] if k == "avg_score" else (s[k] or 0)) for k in s.keys()}
        out.append(d)
    return {"lists": out, "curators": cs}


# ---- "for you": a baseline taste match, to be replaced by the real model ---

@app.get("/api/recommend")
def recommend(curator: str = DEFAULT_CURATOR, limit: int = Query(60, le=200), lst: str = "", genre: str = ""):
    """
    Unseen films scored by what they share with the films you've tiered.
    Director > labels > TMDB keywords > genre > decade; a small boost for being on a list or your want list.
    Tier scores run -10..10, so what you hated counts against. Explainable on purpose: each
    film comes back with its `why`. This is the placeholder the real recommender replaces.
    """
    con = db()
    liked = con.execute(f"""SELECT {FILM_COLS}, m.score FROM marks m JOIN films f ON f.tconst=m.tconst
                            WHERE m.status='seen' AND m.curator=? AND m.score IS NOT NULL""", (curator,)).fetchall()
    if len(liked) < 3:
        return {"films": [], "reason": f"tier at least 3 films you've seen first ({len(liked)} so far). this is computed from your tiers."}
    n = len(liked)
    w_dir, w_kw, w_lab, g_sum, g_cnt, d_sum, d_cnt = (defaultdict(float) for _ in range(7))
    for f in liked:
        s = f["score"] / 10
        for d in parts(f["directors"], ","):
            w_dir[d] += s
        for k in parts(f["keywords"], ","):
            w_kw[k] += s
        for k in parts(f["labels"], ","):
            w_lab[k] += s
        for g in parts(f["genres"], ","):
            g_sum[g] += s; g_cnt[g] += 1
        d_sum[f["decade"]] += s; d_cnt[f["decade"]] += 1

    join, where, args = where_clause(genre, 0, "", "", "", "", 0, lst)
    where = (where + " AND " if where else "WHERE ") + "(m.status IS NULL OR m.status='want')"
    sql, a = film_query(curator, join, where, "f.rating DESC", args)
    out = []
    for f in con.execute(sql, a):
        sc, why = 0.0, []
        for d in parts(f["directors"], ","):
            if d in w_dir:
                sc += 3 * w_dir[d]; why.append(f"{'liked' if w_dir[d] > 0 else 'disliked'} dir. {d}")
        labs = [(w_lab[k], k) for k in parts(f["labels"], ",") if k in w_lab]
        if labs:
            sc += 2 * sum(w for w, _ in labs); why.append("labels " + ", ".join(k for _, k in labs[:3]))
        kws = sorted((w_kw[k], k) for k in parts(f["keywords"], ",") if k in w_kw)
        if kws:
            top = kws[-5:] if kws[-1][0] > 0 else kws[:5]
            sc += sum(w for w, _ in top); why.append("themes " + ", ".join(k for _, k in top[::-1][:3]))
        gs = [(g_sum[g] / g_cnt[g]) * min(1, g_cnt[g] / 5) for g in parts(f["genres"], ",") if g in g_cnt]
        if gs:
            sc += sum(gs) / len(gs)
        if f["decade"] in d_cnt:
            sc += 0.5 * (d_sum[f["decade"]] / d_cnt[f["decade"]]) * min(1, d_cnt[f["decade"]] / 5)
        if f["lists"]:
            sc += min(1, 0.3 * len(f["lists"].split("|"))); why.append("on " + f["lists"].split("|")[0].replace("#", " #"))
        if f["status"] == "want":
            sc += 0.5; why.append("on your want list")
        sc += f["rating"] / 100  # tiebreak
        d = dict(f); d["score"] = round(sc, 2); d["why"] = why[:4]
        out.append(d)
    out.sort(key=lambda x: -x["score"])
    return {"films": out[:limit], "based_on": n}


# ---- marks: the write-up ----------------------------------------------------

class Mark(BaseModel):
    curator: str = DEFAULT_CURATOR
    tconst: str
    status: str | None = None
    score: int | None = None          # -10..10
    tier: str | None = None           # old clients: a scale name; ignored when score is given
    feelings: list[str] = []          # "wrecked:3", at most two
    tags: list[str] = []              # "tag" = why it worked, "-tag" = what went wrong
    labels: list[str] | None = None   # film labels (shared between curators); None = leave as they are
    about: str | None = None
    reached: str | None = None
    for_whom: str | None = None
    avoid: str | None = None
    note: str | None = None
    times: str | None = None
    when_seen: str | None = None
    memory: str | None = None


@app.post("/api/mark")
def mark(m: Mark):
    con = db()
    check_curator(con, m.curator)
    if m.status not in (None, "seen", "want", "not"):
        raise HTTPException(400, "status must be seen, want, not or null")
    score = m.score
    if score is None and m.tier:
        r = con.execute("SELECT score FROM scale WHERE name=?", (m.tier,)).fetchone(); score = r["score"] if r else None
    if score is not None and not -10 <= score <= 10:
        raise HTTPException(400, "score must be between -10 and 10")
    if len(m.feelings) > 2:
        raise HTTPException(400, "two end-feelings at most: pick the ones that stayed")
    old = con.execute("SELECT score FROM marks WHERE curator=? AND tconst=?", (m.curator, m.tconst)).fetchone()
    keep_rank = bool(old and old["score"] == score)   # a score change drops the film out of its old ladder
    con.execute(f"""INSERT INTO marks (curator,tconst,status,score,tier,rank,times,when_seen,memory,about,reached,for_whom,avoid,note,source,updated)
                   VALUES (?,?,?,?,NULL,NULL,?,?,?,?,?,?,?,?,'me',datetime('now'))
                   ON CONFLICT(curator,tconst) DO UPDATE SET status=excluded.status, score=excluded.score, tier=NULL,
                   rank={"rank" if keep_rank else "NULL"},
                   times=excluded.times, when_seen=excluded.when_seen, memory=excluded.memory,
                   about=excluded.about, reached=excluded.reached, for_whom=excluded.for_whom, avoid=excluded.avoid,
                   note=excluded.note, source='me', updated=datetime('now')""",
                (m.curator, m.tconst, m.status, score, m.times, m.when_seen, m.memory,
                 m.about, m.reached, m.for_whom, m.avoid, m.note))
    con.execute("DELETE FROM mark_feelings WHERE curator=? AND tconst=?", (m.curator, m.tconst))
    for fe in m.feelings:
        name, _, inten = fe.partition(":")
        name = norm(name)
        if not name:
            continue
        inten = min(3, max(1, int(inten or 2)))
        con.execute("INSERT OR IGNORE INTO feelings VALUES (?,?,?)", (name, "custom", ""))
        con.execute("INSERT OR REPLACE INTO mark_feelings VALUES (?,?,?,?)", (m.curator, m.tconst, name, inten))
    con.execute("DELETE FROM queue WHERE curator=? AND tconst=?", (m.curator, m.tconst)) if m.status == "seen" and (score is not None or has_note(m.model_dump())) else None
    con.execute("DELETE FROM mark_tags WHERE curator=? AND tconst=?", (m.curator, m.tconst))
    for t in m.tags:
        pol, name = (-1, t[1:]) if t.startswith("-") else (1, t)
        name = norm(name)
        if not name:
            continue
        con.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (name, "custom"))
        con.execute("INSERT OR REPLACE INTO mark_tags VALUES (?,?,?,?)", (m.curator, m.tconst, name, pol))
    if m.labels is not None:
        set_labels(con, m.tconst, m.labels)
    event(con, "mark", m.tconst, m.model_dump(), curator=m.curator)
    con.commit()
    export_later()
    return film(m.tconst, m.curator)


def set_labels(con, tconst, labels):
    """The curator's label set for a film. An auto label the curator keeps becomes theirs; one they drop is removed."""
    con.execute("DELETE FROM film_labels WHERE tconst=?", (tconst,))
    for l in labels:
        l = norm(l)
        if not l:
            continue
        con.execute("INSERT OR IGNORE INTO labels VALUES (?,?)", (l, "custom"))
        con.execute("INSERT OR IGNORE INTO film_labels (tconst,label,source) VALUES (?,?,'me')", (tconst, l))


# ---- tags and labels: the vocabulary ----------------------------------------

def norm(name):
    return "-".join((name or "").strip().lower().split()).strip("-")


class Vocab(BaseModel):
    name: str
    grp: str = "custom"


class VocabEdit(BaseModel):
    name: str
    to: str | None = None       # rename (unused name) or merge (existing name)
    grp: str | None = None      # regroup


VOCAB = {  # kind -> (vocabulary table, usage table, usage column)
    "tag": ("tags", "mark_tags", "tag"),
    "label": ("labels", "film_labels", "label"),
    "feeling": ("feelings", "mark_feelings", "feeling"),
}


@app.post("/api/tag")
def add_tag(t: Vocab):
    return add_vocab("tag", t)


@app.post("/api/label")
def add_label(t: Vocab):
    return add_vocab("label", t)


@app.post("/api/feeling")
def add_feeling(t: Vocab):
    return add_vocab("feeling", t)


def add_vocab(kind, t):
    table, _, _ = VOCAB[kind]
    name = norm(t.name)
    if not name:
        raise HTTPException(400, "empty name")
    con = db()
    if kind == "feeling":
        con.execute("INSERT OR IGNORE INTO feelings VALUES (?,?,?)", (name, t.grp.strip() or "custom", ""))
    else:
        con.execute(f"INSERT OR IGNORE INTO {table} VALUES (?,?)", (name, t.grp.strip() or "custom"))
    con.commit()
    return {"name": name}


@app.post("/api/tag/edit")
def edit_tag(e: VocabEdit):
    return edit_vocab("tag", e)


@app.post("/api/label/edit")
def edit_label(e: VocabEdit):
    return edit_vocab("label", e)


@app.post("/api/feeling/edit")
def edit_feeling(e: VocabEdit):
    return edit_vocab("feeling", e)


class ScaleName(BaseModel):
    score: int
    name: str


@app.post("/api/scale")
def name_score(x: ScaleName):
    """Give a step of the -10..10 scale a name (or take it away). Films keep their number."""
    if not -10 <= x.score <= 10:
        raise HTTPException(400, "score must be between -10 and 10")
    con = db()
    con.execute("INSERT INTO scale VALUES (?,?) ON CONFLICT(score) DO UPDATE SET name=excluded.name", (x.score, x.name.strip()))
    event(con, "scale", "", {"score": x.score, "name": x.name.strip()}, source="vocab")
    con.commit()
    export_later()
    return {"score": x.score, "name": x.name.strip()}


class QueueItem(BaseModel):
    curator: str = DEFAULT_CURATOR   # whose queue
    tconst: str
    on: bool = True


@app.post("/api/queue")
def queue_set(x: QueueItem):
    """Put a film in someone's to-write queue (drag a poster onto their name), or take it out."""
    con = db()
    check_curator(con, x.curator)
    if x.on:
        con.execute("INSERT OR IGNORE INTO queue (curator, tconst) VALUES (?,?)", (x.curator, x.tconst))
    else:
        con.execute("DELETE FROM queue WHERE curator=? AND tconst=?", (x.curator, x.tconst))
    event(con, "queue", x.tconst, {"on": x.on}, curator=x.curator)
    con.commit()
    export_later()
    return {"queued": [r[0] for r in con.execute("SELECT curator FROM queue WHERE tconst=?", (x.tconst,))],
            "queues": {c["id"]: con.execute("SELECT COUNT(*) FROM queue WHERE curator=?", (c["id"],)).fetchone()[0] for c in curators(con)}}


def edit_vocab(kind, e):
    """rename (to = a new name), merge (to = an existing name), regroup (grp). Logged as an event."""
    table, usage, col = VOCAB[kind]
    con = db()
    name = norm(e.name)
    if not con.execute(f"SELECT 1 FROM {table} WHERE name=?", (name,)).fetchone():
        raise HTTPException(404, f"no {kind} {name!r}")
    if e.to:
        to = norm(e.to)
        if not to:
            raise HTTPException(400, "empty name")
        if to != name:
            exists = con.execute(f"SELECT 1 FROM {table} WHERE name=?", (to,)).fetchone()
            if not exists:
                con.execute(f"UPDATE {table} SET name=? WHERE name=?", (to, name))
            # merge: a mark that already carries `to` keeps that row; the duplicate is dropped
            con.execute(f"UPDATE OR IGNORE {usage} SET {col}=? WHERE {col}=?", (to, name))
            con.execute(f"DELETE FROM {usage} WHERE {col}=?", (name,))
            con.execute(f"DELETE FROM {table} WHERE name=?", (name,))
            event(con, kind, "", {"merge" if exists else "rename": [name, to]}, source="vocab")
            name = to
    if e.grp is not None:
        con.execute(f"UPDATE {table} SET grp=? WHERE name=?", (e.grp.strip() or "custom", name))
    con.commit()
    export_later()
    return {"name": name}


@app.delete("/api/tag/{name}")
def delete_tag(name: str):
    return delete_vocab("tag", name)


@app.delete("/api/label/{name}")
def delete_label(name: str):
    return delete_vocab("label", name)


@app.delete("/api/feeling/{name}")
def delete_feeling(name: str):
    return delete_vocab("feeling", name)


def delete_vocab(kind, name):
    table, usage, col = VOCAB[kind]
    con = db()
    n = con.execute(f"SELECT COUNT(*) FROM {usage} WHERE {col}=?", (name,)).fetchone()[0]
    if n:
        raise HTTPException(409, f"{kind} {name!r} is used {n} times: merge it into another one instead")
    con.execute(f"DELETE FROM {table} WHERE name=?", (name,))
    con.commit()
    return {"ok": True}


@app.get("/api/suggest")
def suggest(tconst: str, curator: str = DEFAULT_CURATOR, limit: int = 12):
    """
    Tags this curator used (as +) on films that share something with this one: director first,
    then a label, then two or more TMDB keywords. Each suggestion says where it comes from.
    """
    con = db()
    f = con.execute(f"SELECT {FILM_COLS} FROM films f WHERE f.tconst=?", (tconst,)).fetchone()
    if not f:
        raise HTTPException(404, "no such film")
    dirs, labs, kws = set(parts(f["directors"], ",")), set(parts(f["labels"], ",")), set(parts(f["keywords"], ","))
    score, why = defaultdict(float), defaultdict(set)
    rows = con.execute(f"""SELECT {FILM_COLS}, (SELECT group_concat(CASE WHEN mt.polarity<0 THEN '-'||mt.tag ELSE mt.tag END, ',')
                                                  FROM mark_tags mt WHERE mt.curator=m.curator AND mt.tconst=m.tconst) AS tags
                           FROM marks m JOIN films f ON f.tconst=m.tconst
                           WHERE m.curator=? AND m.status='seen' AND f.tconst<>?""", (curator, tconst))
    for r in rows:
        pos, _ = split_tags(r["tags"])
        if not pos:
            continue
        w, reason = 0, None
        rl = set(parts(r["labels"], ","))
        if dirs & set(parts(r["directors"], ",")):
            w, reason = 3, "same director"
        elif labs & rl:
            w, reason = 2, "label " + sorted(labs & rl)[0]
        else:
            shared = kws & set(parts(r["keywords"], ","))
            if len(shared) >= 2:
                w, reason = 1, "themes " + ", ".join(sorted(shared)[:2])
        if not w:
            continue
        for t in pos:
            score[t] += w; why[t].add(f"{reason}: {r['title']}")
    out = sorted(score, key=lambda t: -score[t])[:limit]
    return {"tags": [{"name": t, "score": score[t], "why": sorted(why[t])[:3]} for t in out]}


# ---- ladder: order inside a tier --------------------------------------------

def win_rates(con, curator):
    w, n = defaultdict(int), defaultdict(int)
    for r in con.execute("SELECT a,b,winner FROM pairs WHERE curator=? AND skipped=0", (curator,)):
        n[r["a"]] += 1; n[r["b"]] += 1
        if r["winner"]: w[r["winner"]] += 1
    return {t: (w[t], n[t]) for t in n}


@app.get("/api/ladder")
def ladder(score: int, curator: str = DEFAULT_CURATOR):
    """Films at one score: ranked ones first in rank order, then the rest by compare-mode win rate."""
    con = db()
    check_curator(con, curator)
    sql, a = film_query(curator, "", "WHERE m.status='seen' AND m.score=?", "f.title", [score])
    rows = [dict(r) for r in con.execute(sql, a)]
    wr = win_rates(con, curator)
    for d in rows:
        w, n = wr.get(d["tconst"], (0, 0))
        d["wins"], d["duels"] = w, n
        d["winrate"] = round(w / n, 2) if n else None
    rows.sort(key=lambda d: (d["rank"] is None, d["rank"] or 0,
                             -(d["winrate"] if d["winrate"] is not None else -1), -d["duels"], d["title"]))
    return {"score": score, "films": rows}


class Ladder(BaseModel):
    curator: str = DEFAULT_CURATOR
    score: int
    order: list[str]


@app.post("/api/ladder")
def save_ladder(l: Ladder):
    con = db()
    check_curator(con, l.curator)
    for i, t in enumerate(l.order, 1):
        con.execute("UPDATE marks SET rank=?, updated=datetime('now') WHERE curator=? AND tconst=? AND score=?", (i, l.curator, t, l.score))
    event(con, "ladder", "", {"score": l.score, "order": l.order}, curator=l.curator)
    con.commit()
    export_later()
    return {"ok": True}


# ---- compare: "which did you like more?" ----------------------------------

@app.get("/api/pair")
def pair(curator: str = DEFAULT_CURATOR, same_tier: int = 0, score: str = ""):
    con = db()
    check_curator(con, curator)
    where, args = "WHERE m.status='seen'", []
    if score != "":
        where += " AND m.score=?"; args.append(int(score))
    sql, a = film_query(curator, "", where, "f.title", args)
    seen = [dict(r) for r in con.execute(sql, a)]
    if len(seen) < 2:
        return {"a": None, "b": None, "reason": "mark at least two films as seen first"}
    done = {tuple(sorted((r[0], r[1]))) for r in con.execute("SELECT a,b FROM pairs WHERE curator=?", (curator,))}
    for _ in range(300):
        a = random.choice(seen)
        same = [f for f in seen if f["score"] == a["score"] and f["tconst"] != a["tconst"]]
        has = a["score"] is not None
        if same_tier and not (has and same):
            continue
        # mostly compare at the same score (that's where the order is unknown), sometimes across
        cands = same if (has and same and (same_tier or random.random() < 0.7)) else [f for f in seen if f["tconst"] != a["tconst"]]
        b = random.choice(cands)
        if tuple(sorted((a["tconst"], b["tconst"]))) not in done:
            return {"a": a, "b": b}
    return {"a": None, "b": None, "reason": "you've compared every pair there is - mark more films as seen"}


class Pair(BaseModel):
    curator: str = DEFAULT_CURATOR
    a: str
    b: str
    winner: str | None = None
    reason: str | None = None        # "because…": one line, in your words


@app.post("/api/pair")
def save_pair(p: Pair):
    if p.winner not in (None, p.a, p.b):
        raise HTTPException(400, "winner must be a, b or null")
    con = db()
    check_curator(con, p.curator)
    con.execute("INSERT INTO pairs (a,b,winner,skipped,curator,reason) VALUES (?,?,?,?,?,?)",
                (p.a, p.b, p.winner, 0 if p.winner else 1, p.curator, (p.reason or "").strip() or None))
    event(con, "pair", p.winner or p.a, p.model_dump(), curator=p.curator)
    con.commit()
    export_later()
    return {"pairs": con.execute("SELECT COUNT(*) FROM pairs WHERE curator=? AND skipped=0", (p.curator,)).fetchone()[0]}


# ---- progress: where the gaps are -----------------------------------------

@app.get("/api/progress")
def progress(curator: str = DEFAULT_CURATOR):
    """
    Per genre, decade, label, list, feeling and score step: total films and, for EVERY curator side by
    side, seen / scored / noted / felt / want / unmarked / queued. `curator` only picks the summary order.
    """
    con = db()
    check_curator(con, curator)
    cs = [c["id"] for c in curators(con)]
    agg = f"""SUM(m.status='seen') AS seen, SUM(m.status='want') AS want,
              SUM(m.status='seen' AND m.score IS NOT NULL) AS tiered,
              SUM(m.status='seen' AND {NOTED_SQL}) AS noted,
              SUM(m.status='seen' AND EXISTS (SELECT 1 FROM mark_feelings mf WHERE mf.curator=m.curator AND mf.tconst=f.tconst)) AS felt,
              SUM(m.status IS NULL) AS unmarked,
              SUM(EXISTS (SELECT 1 FROM queue qq WHERE qq.curator=? AND qq.tconst=f.tconst)) AS queued"""

    def table(key_sql, from_sql, group_sql, order_sql, extra_args=()):
        rows = {}
        for c in cs:
            for r in con.execute(f"SELECT {key_sql} AS key, COUNT(*) AS total, {agg} {from_sql} LEFT JOIN marks m ON m.tconst=f.tconst AND m.curator=? {group_sql} {order_sql}",
                                 (c, c, *extra_args)):
                d = rows.setdefault(r["key"], {"key": r["key"], "total": r["total"], "by": {}})
                d["by"][c] = {k: (r[k] or 0) for k in ("seen", "want", "tiered", "noted", "felt", "unmarked", "queued")}
        return list(rows.values())

    genres = sorted({g for (gs,) in con.execute("SELECT genres FROM films") for g in gs.split(",") if g})
    by_genre = []
    for g in genres:   # the genre is a LIKE parameter, so these go one by one
        d = {"key": g, "total": 0, "by": {}}
        for c in cs:
            r = con.execute(f"SELECT COUNT(*) AS total, {agg} FROM films f LEFT JOIN marks m ON m.tconst=f.tconst AND m.curator=? WHERE (',' || f.genres || ',') LIKE ?",
                            (c, c, f"%,{g},%")).fetchone()
            d["total"] = r["total"]; d["by"][c] = {k: (r[k] or 0) for k in ("seen", "want", "tiered", "noted", "felt", "unmarked", "queued")}
        by_genre.append(d)
    by_decade = table("f.decade", "FROM films f", "GROUP BY f.decade", "ORDER BY f.decade")
    by_label = table("fl.label", "FROM film_labels fl JOIN films f ON f.tconst=fl.tconst", "GROUP BY fl.label", "ORDER BY total DESC")
    by_list = table("l.list", "FROM lists l JOIN films f ON f.tconst=l.tconst", "GROUP BY l.list", "ORDER BY l.list")
    names = scale_name(con)
    by_score = []
    for sc in range(10, -11, -1):
        d = {"key": sc, "name": names[sc], "by": {}}
        for c in cs:
            r = con.execute("SELECT COUNT(*) AS n, SUM(rank IS NOT NULL) AS ranked FROM marks WHERE curator=? AND status='seen' AND score=?", (c, sc)).fetchone()
            d["by"][c] = {"n": r["n"] or 0, "ranked": r["ranked"] or 0}
        by_score.append(d)
    by_feeling = []
    for fe in con.execute("SELECT name, grp, es FROM feelings ORDER BY rowid"):
        d = {"key": fe["name"], "grp": fe["grp"], "es": fe["es"], "by": {}}
        for c in cs:
            r = con.execute("SELECT COUNT(*) AS n, SUM(intensity=3) AS strong FROM mark_feelings WHERE curator=? AND feeling=?", (c, fe["name"])).fetchone()
            d["by"][c] = {"n": r["n"] or 0, "strong": r["strong"] or 0}
        by_feeling.append(d)
    summary = {}
    for c in cs:
        summary[c] = dict(con.execute(f"""SELECT
            (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='seen') AS seen,
            (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='seen' AND score IS NOT NULL) AS tiered,
            (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='seen' AND {NOTED_SQL}) AS noted,
            (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='seen' AND IFNULL(m.reached,'')<>'') AS reached,
            (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='seen' AND EXISTS (SELECT 1 FROM mark_feelings mf WHERE mf.curator=m.curator AND mf.tconst=m.tconst)) AS felt,
            (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='seen' AND EXISTS (SELECT 1 FROM mark_tags mt WHERE mt.curator=m.curator AND mt.tconst=m.tconst)) AS tagged,
            (SELECT COUNT(*) FROM marks m WHERE curator=:c AND status='want') AS want,
            (SELECT COUNT(*) FROM queue WHERE curator=:c) AS queued,
            (SELECT COUNT(*) FROM pairs WHERE curator=:c AND skipped=0) AS pairs""", {"c": c}).fetchone())
    return {"curator": curator, "curators": curators(con), "summary": summary, "genre": by_genre, "decade": by_decade,
            "label": by_label, "list": by_list, "score": by_score, "feeling": by_feeling}


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
    curator: str = DEFAULT_CURATOR


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
    return film(a.tconst, a.curator)


# ---- export -----------------------------------------------------------------

@app.get("/api/export.csv")
def export_csv(curator: str = ""):
    """Everything marked (seen, want, not seen) with film features; all curators, or one. The model's input."""
    name = f"marks_{curator}.csv" if curator else "marks.csv"
    return StreamingResponse(io.StringIO(csv_text(db(), curator or None)), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={name}"})


@app.get("/api/export.md", response_class=PlainTextResponse)
def export_md(curator: str = DEFAULT_CURATOR):
    """The notes as one readable markdown document per curator: the shape a model reads best."""
    return notes_md(db(), curator)


@app.get("/api/export.json")
def export_json():
    con = db()
    return {"marks": list(mark_rows(con)),
            "pairs": [dict(r) for r in con.execute("SELECT * FROM pairs ORDER BY id")],
            "scale": [dict(r) for r in con.execute("SELECT * FROM scale ORDER BY score DESC")],
            "feelings": [dict(r) for r in con.execute("SELECT * FROM feelings")],
            "tags": [dict(r) for r in con.execute("SELECT * FROM tags ORDER BY grp,name")],
            "labels": [dict(r) for r in con.execute("SELECT * FROM labels ORDER BY grp,name")],
            "queue": [dict(r) for r in con.execute("SELECT * FROM queue ORDER BY curator, added")],
            "curators": curators(con)}


if __name__ == "__main__":
    import uvicorn, webbrowser, threading as th
    host = os.environ.get("FILMCAT_HOST", "0.0.0.0")
    port = int(os.environ.get("FILMCAT_PORT", "8000"))
    if not os.environ.get("FILMCAT_NO_BROWSER"):
        th.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run(app, host=host, port=port)

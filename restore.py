#!/usr/bin/env python3
"""
Put the marks back into a fresh films.db from exports/marks.jsonl (+ pairs.jsonl).
Run setup.py first so films/pool exist. Existing marks are left alone.

    python restore.py                 # from ./exports
    python restore.py /path/to/exports
"""
import json, os, sqlite3, sys

from app import DB, EXPORTS, add_from_pool
from setup import DEFAULT_CURATOR, migrate


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else EXPORTS
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    migrate(con)
    films = {r[0] for r in con.execute("SELECT tconst FROM films")}
    marked = {(r[0], r[1]) for r in con.execute("SELECT curator, tconst FROM marks")}
    n, skipped, missing = 0, 0, []
    for line in open(os.path.join(src, "marks.jsonl"), encoding="utf-8"):
        m = json.loads(line)
        t, c = m["tconst"], m.get("curator") or DEFAULT_CURATOR   # pre-curator exports have no curator
        if t not in films and not add_from_pool(con, t, "restore"):
            missing.append(f"{m['title']} ({m['year']}) {t}"); continue
        if (c, t) in marked:
            skipped += 1; continue
        con.execute("INSERT OR IGNORE INTO curators VALUES (?,?,99)", (c, c.title()))
        score = m.get("score")
        if score is None and m.get("tier"):   # pre-scale exports carry a tier name
            r = con.execute("SELECT score FROM scale WHERE name=?", (m["tier"],)).fetchone(); score = r[0] if r else None
        con.execute("""INSERT INTO marks (curator,tconst,status,score,rank,favourite,guilty,times,when_seen,memory,about,reached,for_whom,avoid,note,source,updated)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (c, t, m.get("status"), score, m.get("rank"), 1 if m.get("favourite") else 0, m.get("guilty"), m.get("times"), m.get("when_seen"), m.get("memory"),
                     m.get("about"), m.get("reached"), m.get("for_whom"), m.get("avoid"), m.get("note"),
                     m.get("source"), m.get("updated")))
        for fe in m.get("feelings", []):
            name, _, inten = fe.partition(":")
            con.execute("INSERT OR IGNORE INTO feelings VALUES (?,?,?)", (name, "custom", ""))
            con.execute("INSERT OR IGNORE INTO mark_feelings VALUES (?,?,?,?)", (c, t, name, int(inten or 2)))
        for tag in m.get("tags_pos", []):
            con.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (tag, "custom"))
            con.execute("INSERT OR IGNORE INTO mark_tags VALUES (?,?,?,1)", (c, t, tag))
        for tag in m.get("tags_neg", []):
            con.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (tag, "custom"))
            con.execute("INSERT OR IGNORE INTO mark_tags VALUES (?,?,?,-1)", (c, t, tag))
        for l in m.get("labels", []):
            con.execute("INSERT OR IGNORE INTO labels VALUES (?,?)", (l, "custom"))
            con.execute("INSERT OR IGNORE INTO film_labels (tconst,label,source) VALUES (?,?,'me')", (t, l))
        n += 1
    pairs = os.path.join(src, "pairs.jsonl")
    p = 0
    if os.path.exists(pairs) and con.execute("SELECT COUNT(*) FROM pairs").fetchone()[0] == 0:
        for line in open(pairs, encoding="utf-8"):
            r = json.loads(line)
            con.execute("INSERT INTO pairs (ts,a,b,winner,skipped,curator,reason) VALUES (?,?,?,?,?,?,?)",
                        (r["ts"], r["a"], r["b"], r["winner"], r["skipped"], r.get("curator") or DEFAULT_CURATOR, r.get("reason"))); p += 1
    qp = os.path.join(src, "queue.jsonl")
    if os.path.exists(qp):
        for line in open(qp, encoding="utf-8"):
            r = json.loads(line)
            con.execute("INSERT OR IGNORE INTO queue (curator,tconst,added) VALUES (?,?,?)", (r["curator"], r["tconst"], r.get("added")))
    vp = os.path.join(src, "vocab.jsonl")
    if os.path.exists(vp):
        v = json.loads(open(vp, encoding="utf-8").readline())
        for r in v.get("scale", []):
            con.execute("INSERT INTO scale VALUES (?,?) ON CONFLICT(score) DO UPDATE SET name=excluded.name", (r["score"], r["name"]))
    con.commit()
    print(f"restored {n} marks ({skipped} already there), {p} pairs, {len(missing)} films not found")
    for x in missing:
        print("  ", x)


if __name__ == "__main__":
    main()

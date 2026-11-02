#!/usr/bin/env python3
"""
Put your marks back into a fresh films.db from exports/marks.jsonl (+ pairs.jsonl).
Run setup.py first so films/pool exist. Existing marks are left alone.

    python restore.py                 # from ./exports
    python restore.py /path/to/exports
"""
import json, os, sqlite3, sys

from app import DB, EXPORTS, add_from_pool


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else EXPORTS
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    films = {r[0] for r in con.execute("SELECT tconst FROM films")}
    marked = {r[0] for r in con.execute("SELECT tconst FROM my")}
    n, skipped, missing = 0, 0, []
    for line in open(os.path.join(src, "marks.jsonl"), encoding="utf-8"):
        m = json.loads(line)
        t = m["tconst"]
        if t not in films and not add_from_pool(con, t, "restore"):
            missing.append(f"{m['title']} ({m['year']}) {t}"); continue
        if t in marked:
            skipped += 1; continue
        tags = ",".join(m.get("tags_pos", []) + ["-" + x for x in m.get("tags_neg", [])])
        con.execute("""INSERT INTO my (tconst,status,tier,tags,note,times,when_seen,memory,source,updated)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (t, m.get("status"), m.get("tier"), tags, m.get("note"), m.get("times"),
                     m.get("when_seen"), m.get("memory"), m.get("source"), m.get("updated")))
        n += 1
    pairs = os.path.join(src, "pairs.jsonl")
    p = 0
    if os.path.exists(pairs) and con.execute("SELECT COUNT(*) FROM pairs").fetchone()[0] == 0:
        for line in open(pairs, encoding="utf-8"):
            r = json.loads(line)
            con.execute("INSERT INTO pairs (ts,a,b,winner,skipped) VALUES (?,?,?,?,?)",
                        (r["ts"], r["a"], r["b"], r["winner"], r["skipped"])); p += 1
    con.commit()
    print(f"restored {n} marks ({skipped} already there), {p} pairs, {len(missing)} films not found")
    for x in missing:
        print("  ", x)


if __name__ == "__main__":
    main()

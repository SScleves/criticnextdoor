#!/usr/bin/env python3
"""
Sync marks between this machine and the private state repo the Pages edition writes to.

The browser edition keeps everyone's marks in <state repo>/state.json. This pulls that file,
merges it into films.db (per mark: newest wins; pairs/events/labels: union), writes the merged
state back, pushes, and refreshes exports/. Run it whenever you want the server to know what
you marked on another laptop, or put it on a timer.

    python sync_state.py                      # clone/pull, merge, push
    python sync_state.py --repo git@github.com:SScleves/criticnextdoor-state.git
Env: FILMCAT_STATE_REPO (ssh url), FILMCAT_STATE_DIR (default ./state)
"""
import argparse, json, os, sqlite3, subprocess, sys, time

from app import DB, add_from_pool, write_exports
from setup import migrate

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("FILMCAT_STATE_DIR", os.path.join(HERE, "state"))
REPO = os.environ.get("FILMCAT_STATE_REPO", "git@github.com:SScleves/criticnextdoor-state.git")
MARK_COLS = ["curator", "tconst", "status", "score", "tier", "rank", "times", "when_seen", "memory", "about", "reached", "for_whom", "avoid", "note", "source", "updated"]
STATE_V = 2   # v2 adds marks.score, marks.feelings, pairs.reason, queue, scale, feelings
now = lambda: time.strftime("%Y-%m-%d %H:%M:%S")


def git(*a, check=True):
    return subprocess.run(["git", "-C", STATE_DIR, *a], capture_output=True, text=True, check=check)


def collect(con):
    """The same shape docs/shim.js writes."""
    marks = []
    for r in con.execute(f"SELECT {','.join(MARK_COLS)} FROM marks"):
        d = dict(zip(MARK_COLS, r))
        d["tags"] = [("-" if p < 0 else "") + t for t, p in con.execute("SELECT tag, polarity FROM mark_tags WHERE curator=? AND tconst=?", (d["curator"], d["tconst"]))]
        d["feelings"] = [f"{f}:{i}" for f, i in con.execute("SELECT feeling, intensity FROM mark_feelings WHERE curator=? AND tconst=?", (d["curator"], d["tconst"]))]
        marks.append(d)
    fl = {}
    for t, labels in con.execute("SELECT tconst, group_concat(label, ',') FROM film_labels WHERE IFNULL(source,'me')='me' GROUP BY tconst"):
        fl[t] = {"labels": labels.split(","), "at": ""}
    return {"v": STATE_V, "ts": now(), "marks": marks, "film_labels": fl,
            "tags": [dict(zip(("name", "grp"), r)) for r in con.execute("SELECT name, grp FROM tags")],
            "labels": [dict(zip(("name", "grp"), r)) for r in con.execute("SELECT name, grp FROM labels")],
            "feelings": [dict(zip(("name", "grp", "es"), r)) for r in con.execute("SELECT name, grp, es FROM feelings")],
            "scale": [dict(zip(("score", "name"), r)) for r in con.execute("SELECT score, name FROM scale")],
            "queue": [dict(zip(("curator", "tconst", "added"), r)) for r in con.execute("SELECT curator, tconst, added FROM queue")],
            "pairs": [dict(zip(("ts", "a", "b", "winner", "skipped", "curator", "reason"), r)) for r in con.execute("SELECT ts,a,b,winner,skipped,curator,reason FROM pairs")],
            "events": [dict(zip(("ts", "kind", "tconst", "payload", "source", "curator"), r)) for r in con.execute("SELECT ts,kind,tconst,payload,source,curator FROM events")],
            "added": [dict(zip(("tconst", "added"), r)) for r in con.execute("SELECT tconst, added FROM films WHERE added IS NOT NULL AND added<>'filter'")]}


def merge(a, b):
    marks = {}
    for m in a.get("marks", []) + b.get("marks", []):
        k = (m["curator"], m["tconst"])
        if k not in marks or (m.get("updated") or "") > (marks[k].get("updated") or ""):
            marks[k] = m
    fl = dict(a.get("film_labels", {}))
    for t, v in b.get("film_labels", {}).items():
        if t not in fl or (v.get("at") or "") > (fl[t].get("at") or ""):
            fl[t] = v

    def uniq(rows, key):
        out = {}
        for r in rows:
            out[key(r)] = r
        return list(out.values())
    return {"v": STATE_V, "ts": now(), "marks": list(marks.values()), "film_labels": fl,
            "tags": uniq(a.get("tags", []) + b.get("tags", []), lambda t: t["name"]),
            "labels": uniq(a.get("labels", []) + b.get("labels", []), lambda t: t["name"]),
            "feelings": uniq(a.get("feelings", []) + b.get("feelings", []), lambda t: t["name"]),
            "scale": uniq(a.get("scale", []) + b.get("scale", []), lambda t: t["score"]),   # b (this machine) wins a name
            "queue": uniq(a.get("queue", []) + b.get("queue", []), lambda x: (x["curator"], x["tconst"])),
            "pairs": uniq(a.get("pairs", []) + b.get("pairs", []), lambda p: (p.get("curator"), p["a"], p["b"], p["ts"])),
            "events": uniq(a.get("events", []) + b.get("events", []), lambda e: (e["ts"], e["kind"], e.get("tconst"), e.get("curator"), e.get("payload"))),
            "added": uniq(a.get("added", []) + b.get("added", []), lambda x: x["tconst"])}


def apply(con, s):
    for x in s.get("added", []):
        add_from_pool(con, x["tconst"], x.get("added") or "pool")
    for t in ("marks", "mark_tags", "mark_feelings", "pairs", "events", "queue"):
        con.execute(f"DELETE FROM {t}")
    con.execute("DELETE FROM film_labels WHERE IFNULL(source,'me')='me'")
    for t in s.get("tags", []):
        con.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (t["name"], t.get("grp") or "custom"))
    for l in s.get("labels", []):
        con.execute("INSERT OR IGNORE INTO labels VALUES (?,?)", (l["name"], l.get("grp") or "custom"))
    for f in s.get("feelings", []):
        con.execute("INSERT OR IGNORE INTO feelings VALUES (?,?,?)", (f["name"], f.get("grp") or "custom", f.get("es") or ""))
    for x in s.get("scale", []):
        con.execute("INSERT INTO scale VALUES (?,?) ON CONFLICT(score) DO UPDATE SET name=excluded.name", (x["score"], x.get("name") or ""))
    for x in s.get("queue", []):
        con.execute("INSERT OR IGNORE INTO queue (curator,tconst,added) VALUES (?,?,?)", (x["curator"], x["tconst"], x.get("added") or now()))
    by_name = {r[0]: r[1] for r in con.execute("SELECT name, score FROM scale WHERE name<>''")}
    for m in s.get("marks", []):
        m = dict(m)
        if m.get("score") is None and m.get("tier"):   # a v1 mark: tier name -> score
            m["score"] = by_name.get(m["tier"])
        m["tier"] = None
        con.execute(f"INSERT OR IGNORE INTO marks ({','.join(MARK_COLS)}) VALUES ({','.join('?' * len(MARK_COLS))})", [m.get(c) for c in MARK_COLS])
        for t in m.get("tags", []):
            neg, name = t.startswith("-"), t.lstrip("-")
            con.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (name, "custom"))
            con.execute("INSERT OR IGNORE INTO mark_tags VALUES (?,?,?,?)", (m["curator"], m["tconst"], name, -1 if neg else 1))
        for fe in m.get("feelings", []):
            name, _, inten = fe.partition(":")
            con.execute("INSERT OR IGNORE INTO feelings VALUES (?,?,?)", (name, "custom", ""))
            con.execute("INSERT OR IGNORE INTO mark_feelings VALUES (?,?,?,?)", (m["curator"], m["tconst"], name, int(inten or 2)))
    for t, v in s.get("film_labels", {}).items():
        for l in v.get("labels", []):
            con.execute("INSERT OR IGNORE INTO labels VALUES (?,?)", (l, "custom"))
            con.execute("INSERT OR REPLACE INTO film_labels (tconst,label,source) VALUES (?,?,'me')", (t, l))
    for p in s.get("pairs", []):
        con.execute("INSERT INTO pairs (ts,a,b,winner,skipped,curator,reason) VALUES (?,?,?,?,?,?,?)", (p["ts"], p["a"], p["b"], p.get("winner"), p.get("skipped", 0), p.get("curator"), p.get("reason")))
    for e in s.get("events", []):
        con.execute("INSERT INTO events (ts,kind,tconst,payload,source,curator) VALUES (?,?,?,?,?,?)", (e["ts"], e["kind"], e.get("tconst"), e.get("payload"), e.get("source"), e.get("curator")))
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    a = ap.parse_args()
    if not os.path.isdir(os.path.join(STATE_DIR, ".git")):
        r = subprocess.run(["git", "clone", "-q", a.repo, STATE_DIR], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"could not clone {a.repo}:\n{r.stderr.strip()}\n(create the private repo on GitHub first, and make sure this machine's ssh key can reach it)")
    git("fetch", "-q", "origin")
    branch = (git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).stdout.strip() or "origin/main").split("/", 1)[1]
    if git("rev-parse", "--verify", f"origin/{branch}", check=False).returncode == 0:
        git("checkout", "-q", "-B", branch, f"origin/{branch}")
    else:
        git("checkout", "-q", "-B", branch)
    path = os.path.join(STATE_DIR, "state.json")
    remote = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    migrate(con)
    local = collect(con)
    merged = merge(remote, local)
    apply(con, merged)
    json.dump(merged, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    git("add", "state.json")
    if git("diff", "--cached", "--quiet", check=False).returncode:
        git("-c", "user.name=filmcat", "-c", "user.email=filmcat@localhost", "commit", "-q", "-m", "sync from media server")
        r = git("push", "-q", "-u", "origin", branch, check=False)
        print("pushed" if r.returncode == 0 else f"push failed: {r.stderr.strip()}")
    else:
        print("nothing new to push")
    write_exports()
    print(f"merged: {len(merged['marks'])} marks, {len(merged['pairs'])} pairs, {len(merged['events'])} events "
          f"(remote had {len(remote.get('marks', []))} marks, this machine {len(local['marks'])})")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Build docs/ = the GitHub Pages edition (https://sscleves.github.io/criticnextdoor/).

  docs/index.html       static/index.html with docs/shim.js in front of it (the API, in the browser)
  docs/data/films.db.gz the catalogue only: films, pool, lists, tiers, default tags, curators.
                        No marks, pairs, events, labels: those are private and live in the state repo.
  docs/local.html       the old front door (home wifi / meshnet buttons to the local server)

Run after setup.py, import_stack.py, lists.py or enrich_*.py changed the catalogue, then commit docs/.
    python build_static.py
"""
import gzip, hashlib, json, os, re, shutil, sqlite3, time

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
from setup import TAGS, LABELS, migrate  # noqa: E402


def main():
    os.makedirs(os.path.join(DOCS, "data"), exist_ok=True)
    tmp = os.path.join(DOCS, "data", "films.tmp.db")
    shutil.copy(os.path.join(HERE, "films.db"), tmp)
    con = sqlite3.connect(tmp)
    migrate(con)
    for t in ("marks", "mark_tags", "mark_feelings", "pairs", "events", "queue"):
        con.execute(f"DELETE FROM {t}")
    # lists made in the app are private: they travel in state.json, not in the public catalogue
    con.execute("DELETE FROM lists WHERE list IN (SELECT list FROM list_meta WHERE made_by IS NOT NULL)")
    con.execute("DELETE FROM list_meta WHERE made_by IS NOT NULL")
    # auto labels (seed_labels.py) describe the films and ship; curators' labels are private
    con.execute("DELETE FROM film_labels WHERE IFNULL(source,'me')<>'auto'")
    con.execute("DELETE FROM tags WHERE name NOT IN (%s)" % ",".join("?" * len(TAGS)), [t[0] for t in TAGS])
    con.execute("DELETE FROM labels WHERE name NOT IN (%s) AND name NOT IN (SELECT label FROM film_labels)" % ",".join("?" * len(LABELS)), [l[0] for l in LABELS])
    for t in [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'my%'")]:
        con.execute(f"DROP TABLE {t}")
    con.execute("UPDATE films SET added=NULL")   # the browser treats a non-null `added` as something it added itself
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM films").fetchone()[0]
    p = con.execute("SELECT COUNT(*) FROM pool").fetchone()[0]
    l = con.execute("SELECT COUNT(DISTINCT list) FROM lists").fetchone()[0]
    con.execute("VACUUM"); con.close()
    raw = open(tmp, "rb").read(); os.remove(tmp)
    with gzip.open(os.path.join(DOCS, "data", "films.db.gz"), "wb", compresslevel=9) as f:
        f.write(raw)
    json.dump({"built": time.strftime("%Y-%m-%d %H:%M"), "films": n, "pool": p, "lists": l, "bytes": len(raw)},
              open(os.path.join(DOCS, "data", "build.json"), "w"), indent=1)

    html = open(os.path.join(HERE, "static", "index.html"), encoding="utf-8").read()
    shim = open(os.path.join(DOCS, "shim.js"), encoding="utf-8").read()
    v = hashlib.sha1(shim.encode()).hexdigest()[:8]
    tag = f'<script src="shim.js?v={v}"></script>\n<script>'
    html, k = re.subn(r"<script>\s*\nconst IMG", tag + "\nconst IMG", html, count=1)
    if k != 1:
        raise SystemExit("could not find the app <script> in static/index.html")
    html = html.replace("<title>", "<title>", 1)
    open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8").write(html)
    # the match room: same shim in front; rooms and the bot answer 404 here, so the page falls back to one-phone mode
    m = open(os.path.join(HERE, "static", "match.html"), encoding="utf-8").read()
    m, k = re.subn(r"<script>\s*\nconst IMG", tag + "\nconst IMG", m, count=1)
    if k != 1:
        raise SystemExit("could not find the app <script> in static/match.html")
    open(os.path.join(DOCS, "match.html"), "w", encoding="utf-8").write(m)
    print(f"docs/: {n} films, {p} in the pool, {l} lists, db {len(raw)//1_000_000} MB -> {os.path.getsize(os.path.join(DOCS,'data','films.db.gz'))//1_000_000} MB gz, shim v{v}")


if __name__ == "__main__":
    main()

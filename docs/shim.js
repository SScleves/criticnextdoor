/*
 * Critic Next Door, static edition: the server, in the browser.
 *
 * GitHub Pages can't run app.py, so this file is app.py ported to JavaScript, running the same
 * SQL against the same database (data/films.db.gz, loaded into sql.js = SQLite compiled to WASM).
 * The page (index.html) is unchanged: it calls fetch("/api/..."), and this file answers.
 *
 * Your marks are private, so they are NOT in the public database. They live in:
 *   1. this browser (localStorage), always, so nothing is lost on reload;
 *   2. a private GitHub repo (state.json), when you give the page a token (gear, bottom right).
 *      That's what syncs between laptops and what the media server pulls (sync_state.py).
 * Merging is per mark, newest wins. Nobody but the token holder can read or write state.json.
 */
(() => {
  "use strict";

  const SQLJS = "https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.15.2/";
  const DB_URL = "data/films.db.gz";
  const CFG_KEY = "cnd_cfg", STATE_KEY = "cnd_state";
  const NOTE_FIELDS = ["about", "reached", "for_whom", "avoid", "note"];
  const NOTED_SQL = "(IFNULL(m.about,'')<>'' OR IFNULL(m.reached,'')<>'' OR IFNULL(m.for_whom,'')<>'' OR IFNULL(m.avoid,'')<>'' OR IFNULL(m.note,'')<>'')";
  const FILM_COLS = "f.tconst,f.title,f.year,f.decade,f.runtime,f.genres,f.rating,f.votes,f.directors,f.writers," +
    "f.poster,f.overview,f.countries,f.language,f.keywords,f.in_library,f.rt,f.metacritic," +
    "(SELECT group_concat(l.list || '#' || l.rank, '|') FROM lists l WHERE l.tconst=f.tconst) AS lists," +
    "(SELECT group_concat(fl.label, ',') FROM film_labels fl WHERE fl.tconst=f.tconst) AS labels," +
    "(SELECT group_concat(mm.curator || ':' || IFNULL(mm.status,'') || ':' || IFNULL(mm.tier,''), '|') FROM marks mm WHERE mm.tconst=f.tconst) AS all_marks";
  const MY_COLS = "m.curator,m.status,m.tier,m.rank,m.times,m.when_seen,m.memory,m.about,m.reached,m.for_whom,m.avoid," +
    "m.note,m.source,m.updated," +
    "(SELECT group_concat(CASE WHEN mt.polarity<0 THEN '-'||mt.tag ELSE mt.tag END, ',') FROM mark_tags mt WHERE mt.curator=m.curator AND mt.tconst=m.tconst) AS tags";
  const CSV_COLS = ["curator", "tconst", "title", "year", "directors", "genres", "labels", "countries", "language", "runtime",
    "rating", "rt", "metacritic", "lists", "status", "tier", "score", "rank", "tags_pos", "tags_neg",
    "times", "when_seen", "memory", "about", "reached", "for_whom", "avoid", "note",
    "source", "updated", "in_library", "keywords", "overview"];

  let db, DEFAULT_CURATOR = "santi", labelsAt = {}, status = "loading";
  const cfg = load(CFG_KEY, { repo: "SScleves/criticnextdoor-state", path: "state.json", token: "" });

  function load(k, d) { try { return Object.assign(d, JSON.parse(localStorage.getItem(k) || "{}")); } catch { return d; } }
  const now = () => new Date().toISOString().slice(0, 19).replace("T", " ");

  class HttpError extends Error { constructor(status, detail) { super(detail); this.status = status; } }

  // ---- sql helpers ------------------------------------------------------------
  function q(sql, params = []) {
    const st = db.prepare(sql); st.bind(params);
    const out = []; while (st.step()) out.push(st.getAsObject()); st.free(); return out;
  }
  const one = (sql, params) => q(sql, params)[0] || null;
  const val = (sql, params) => { const r = one(sql, params); return r ? Object.values(r)[0] : null; };
  const run = (sql, params = []) => db.run(sql, params);
  const parts = (s, sep) => (s || "").split(sep).map(x => x.trim()).filter(Boolean);
  const splitTags = s => { const t = parts(s, ","); return [t.filter(x => !x.startsWith("-")), t.filter(x => x.startsWith("-")).map(x => x.slice(1))]; };
  const norm = s => (s || "").trim().toLowerCase().split(/\s+/).join("-").replace(/^-+|-+$/g, "");
  const curators = () => q("SELECT * FROM curators ORDER BY ord");
  function checkCurator(c) { if (!one("SELECT 1 FROM curators WHERE id=?", [c])) throw new HttpError(400, `unknown curator '${c}'`); return c; }
  function event(kind, tconst, payload, source = "me", curator = null) {
    run("INSERT INTO events (ts,kind,tconst,payload,source,curator) VALUES (?,?,?,?,?,?)", [now(), kind, tconst, JSON.stringify(payload), source, curator]);
  }

  // ---- boot -----------------------------------------------------------------------
  const ready = (async () => {
    try {
      const [SQL, buf] = await Promise.all([
        new Promise((res, rej) => { const s = document.createElement("script"); s.src = SQLJS + "sql-wasm.js"; s.onload = () => res(window.initSqlJs({ locateFile: f => SQLJS + f })); s.onerror = rej; document.head.appendChild(s); }).then(p => p),
        fetch(DB_URL).then(async r => {
          if (!r.ok) throw new Error(`catalogue download failed (${r.status})`);
          const raw = new Uint8Array(await r.arrayBuffer());
          if (String.fromCharCode(...raw.slice(0, 15)) === "SQLite format 3") return raw;   // CDN already inflated it
          return new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer());
        }),
      ]);
      db = new SQL.Database(buf);
      DEFAULT_CURATOR = (one("SELECT id FROM curators ORDER BY ord LIMIT 1") || {}).id || "santi";
      let local = null;
      try { local = JSON.parse(localStorage.getItem(STATE_KEY) || "null"); } catch {}
      applyState(local && local.marks ? local : emptyState());
      status = cfg.token ? "syncing" : "local only";
      panel();
      if (cfg.token) await pull();
    } catch (e) { status = "error: " + e.message; panel(); throw e; }
  })();

  // ---- private state: collect / apply / merge ------------------------------------
  const emptyState = () => ({ v: 1, ts: now(), marks: [], film_labels: {}, tags: [], labels: [], pairs: [], events: [], added: [] });

  function collectState() {
    const marks = q(`SELECT ${MY_COLS}, m.tconst FROM marks m`).map(r => ({ ...r, tags: parts(r.tags, ",") }));
    const fl = {};
    for (const r of q("SELECT tconst, group_concat(label, ',') AS labels FROM film_labels GROUP BY tconst"))
      fl[r.tconst] = { labels: parts(r.labels, ","), at: labelsAt[r.tconst] || "" };
    for (const t in labelsAt) if (!fl[t]) fl[t] = { labels: [], at: labelsAt[t] };   // an emptied label set must travel too
    return {
      v: 1, ts: now(), marks, film_labels: fl,
      tags: q("SELECT name, grp FROM tags"), labels: q("SELECT name, grp FROM labels"),
      pairs: q("SELECT ts,a,b,winner,skipped,curator FROM pairs"),
      events: q("SELECT ts,kind,tconst,payload,source,curator FROM events"),
      added: q("SELECT tconst, added FROM films WHERE added IS NOT NULL AND added<>'filter'"),
    };
  }

  function applyState(s) {
    run("BEGIN");
    try {
      for (const a of s.added || []) addFromPool(a.tconst, a.added, false);
      run("DELETE FROM marks"); run("DELETE FROM mark_tags"); run("DELETE FROM film_labels"); run("DELETE FROM pairs"); run("DELETE FROM events");
      for (const t of s.tags || []) run("INSERT OR IGNORE INTO tags VALUES (?,?)", [t.name, t.grp || "custom"]);
      for (const l of s.labels || []) run("INSERT OR IGNORE INTO labels VALUES (?,?)", [l.name, l.grp || "custom"]);
      for (const m of s.marks || []) {
        run(`INSERT OR IGNORE INTO marks (curator,tconst,status,tier,rank,times,when_seen,memory,about,reached,for_whom,avoid,note,source,updated)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
          [m.curator, m.tconst, m.status, m.tier, m.rank, m.times, m.when_seen, m.memory, m.about, m.reached, m.for_whom, m.avoid, m.note, m.source, m.updated]);
        for (const t of m.tags || []) {
          const neg = t.startsWith("-"), name = neg ? t.slice(1) : t;
          run("INSERT OR IGNORE INTO tags VALUES (?,?)", [name, "custom"]);
          run("INSERT OR IGNORE INTO mark_tags VALUES (?,?,?,?)", [m.curator, m.tconst, name, neg ? -1 : 1]);
        }
      }
      labelsAt = {};
      for (const [t, v] of Object.entries(s.film_labels || {})) {
        labelsAt[t] = v.at || "";
        for (const l of v.labels || []) { run("INSERT OR IGNORE INTO labels VALUES (?,?)", [l, "custom"]); run("INSERT OR IGNORE INTO film_labels VALUES (?,?)", [t, l]); }
      }
      for (const p of s.pairs || []) run("INSERT INTO pairs (ts,a,b,winner,skipped,curator) VALUES (?,?,?,?,?,?)", [p.ts, p.a, p.b, p.winner, p.skipped, p.curator]);
      for (const e of s.events || []) run("INSERT INTO events (ts,kind,tconst,payload,source,curator) VALUES (?,?,?,?,?,?)", [e.ts, e.kind, e.tconst, e.payload, e.source, e.curator]);
      run("COMMIT");
    } catch (e) { run("ROLLBACK"); throw e; }
  }

  function mergeState(a, b) {
    // newest wins per mark and per film's label set; everything else is a union
    const marks = {};
    for (const m of [...(a.marks || []), ...(b.marks || [])]) { const k = m.curator + "|" + m.tconst; if (!marks[k] || (m.updated || "") > (marks[k].updated || "")) marks[k] = m; }
    const fl = { ...(a.film_labels || {}) };
    for (const [t, v] of Object.entries(b.film_labels || {})) if (!fl[t] || (v.at || "") > (fl[t].at || "")) fl[t] = v;
    const uniq = (rows, key) => { const seen = new Map(); for (const r of rows) seen.set(key(r), r); return [...seen.values()]; };
    return {
      v: 1, ts: now(), marks: Object.values(marks), film_labels: fl,
      tags: uniq([...(a.tags || []), ...(b.tags || [])], t => t.name), labels: uniq([...(a.labels || []), ...(b.labels || [])], t => t.name),
      pairs: uniq([...(a.pairs || []), ...(b.pairs || [])], p => [p.curator, p.a, p.b, p.ts].join("|")),
      events: uniq([...(a.events || []), ...(b.events || [])], e => [e.ts, e.kind, e.tconst, e.curator, e.payload].join("|")),
      added: uniq([...(a.added || []), ...(b.added || [])], x => x.tconst),
    };
  }

  // ---- GitHub: the private repo ----------------------------------------------------
  const gh = () => `https://api.github.com/repos/${cfg.repo}/contents/${cfg.path}`;
  const headers = () => ({ Authorization: "Bearer " + cfg.token, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" });
  const b64 = s => btoa(unescape(encodeURIComponent(s)));
  const unb64 = s => decodeURIComponent(escape(atob(s.replace(/\n/g, ""))));

  async function remoteGet() {
    const r = await fetch(gh(), { headers: headers(), cache: "no-store" });
    if (r.status === 404) return { state: null, sha: null };
    if (!r.ok) throw new Error(r.status === 401 ? "token rejected" : r.status === 403 ? "no access to " + cfg.repo : `GitHub ${r.status}`);
    const j = await r.json();
    return { state: JSON.parse(unb64(j.content)), sha: j.sha };
  }

  async function remotePut(state, sha) {
    const body = { message: "marks", content: b64(JSON.stringify(state)), ...(sha ? { sha } : {}) };
    const r = await fetch(gh(), { method: "PUT", headers: { ...headers(), "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 409 || r.status === 422) return false;   // someone else wrote first: caller merges and retries
    if (!r.ok) throw new Error(r.status === 401 ? "token rejected" : r.status === 403 ? "token can't write " + cfg.repo : `GitHub ${r.status}`);
    return true;
  }

  let pushTimer = null, pushing = false, pending = false;

  async function pull() {
    try {
      const { state: remote } = await remoteGet();
      if (remote) { const merged = mergeState(remote, collectState()); applyState(merged); saveLocal(merged); }
      status = "synced " + new Date().toLocaleTimeString(); panel();
      if (!remote) schedulePush();   // first device: seed the repo
      document.dispatchEvent(new Event("cnd:refresh"));
    } catch (e) { status = "sync error: " + e.message; panel(); }
  }

  async function push() {
    if (pushing) { pending = true; return; }
    pushing = true; status = "saving to GitHub…"; panel();
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        const { state: remote, sha } = await remoteGet();
        let mine = collectState();
        if (remote) { mine = mergeState(remote, mine); applyState(mine); saveLocal(mine); }
        if (await remotePut(mine, sha)) break;
      }
      status = "synced " + new Date().toLocaleTimeString();
    } catch (e) { status = "sync error: " + e.message; }
    pushing = false; panel();
    if (pending) { pending = false; schedulePush(); }
  }

  const saveLocal = s => { try { localStorage.setItem(STATE_KEY, JSON.stringify(s)); } catch (e) { status = "browser storage full"; } };
  function persist() { saveLocal(collectState()); if (cfg.token) schedulePush(); else { status = "saved in this browser only"; panel(); } }
  function schedulePush() { clearTimeout(pushTimer); pushTimer = setTimeout(push, 1500); }

  // ---- the API, endpoint by endpoint (same order as app.py) ----------------------
  function hasNote(d) { return NOTE_FIELDS.some(k => (d[k] || "").trim()); }

  function* markRows(curator) {
    const rows = q(`SELECT ${FILM_COLS},${MY_COLS},t.score FROM marks m JOIN films f ON f.tconst=m.tconst
                    LEFT JOIN tiers t ON t.name=m.tier ${curator ? "WHERE m.curator=?" : ""} ORDER BY m.curator, m.updated`, curator ? [curator] : []);
    for (const r of rows) { const [p, n] = splitTags(r.tags); delete r.tags; r.tags_pos = p; r.tags_neg = n; r.labels = parts(r.labels, ","); yield r; }
  }

  function notesMd(curator) {
    const name = (one("SELECT name FROM curators WHERE id=?", [curator]) || {}).name || curator;
    const out = [`# ${name}'s film notes`, "",
      "Tiers from best to worst: " + q("SELECT name FROM tiers ORDER BY ord").map(r => r.name).join(", ") + ".",
      "Tags marked + are why the film worked for me; tags marked − are what went wrong.", ""];
    const ords = Object.fromEntries(q("SELECT name, ord FROM tiers").map(r => [r.name, r.ord]));
    const rows = [...markRows(curator)].filter(d => d.status === "seen" && (d.tier || hasNote(d) || d.tags_pos.length || d.tags_neg.length));
    rows.sort((x, y) => (ords[x.tier] ?? 99) - (ords[y.tier] ?? 99) || (x.rank || 999) - (y.rank || 999) || x.title.localeCompare(y.title));
    for (const d of rows) {
      out.push(`## ${d.title} (${d.year})`);
      const line = [d.tier ? `tier: ${d.tier}` : "tier: none yet"];
      if (d.rank) line.push(`rank in tier: ${d.rank}`);
      if (d.directors) line.push(`director: ${d.directors}`);
      if (d.genres) line.push(`imdb genres: ${d.genres}`);
      if (d.labels.length) line.push("labels: " + d.labels.join(", "));
      if (d.runtime) line.push(`${d.runtime} min`);
      if (d.language) line.push(`language: ${d.language}`);
      out.push("- " + line.join(" · "));
      const tags = [...d.tags_pos.map(t => "+" + t), ...d.tags_neg.map(t => "−" + t)];
      if (tags.length) out.push("- tags: " + tags.join(" "));
      const sits = [d.times, d.when_seen, d.memory].filter(Boolean);
      if (sits.length) out.push("- seen: " + sits.join(", "));
      for (const [k, label] of [["about", "What it's about"], ["reached", "Why it reached me"], ["for_whom", "Who it's for"], ["avoid", "Who should avoid it"], ["note", "Also"]]) {
        const v = (d[k] || "").trim(); if (v) out.push(`- ${label}: ${v}`);
      }
      out.push("");
    }
    return out.join("\n");
  }

  function csvText(curator) {
    const cell = v => { v = v == null ? "" : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
    const lines = [CSV_COLS.join(",")];
    for (const d of markRows(curator)) {
      d.tags_pos = d.tags_pos.join(" "); d.tags_neg = d.tags_neg.join(" "); d.labels = d.labels.join(" ");
      lines.push(CSV_COLS.map(c => cell(d[c])).join(","));
    }
    return lines.join("\n") + "\n";
  }

  function meta(p) {
    const c = checkCurator(p.get("curator") || DEFAULT_CURATOR);
    const genres = [...new Set(q("SELECT genres FROM films").flatMap(r => parts(r.genres, ",")))].sort();
    const decades = q("SELECT DISTINCT decade FROM films ORDER BY decade").map(r => r.decade);
    const tiers = q("SELECT * FROM tiers ORDER BY ord");
    const tags = q(`SELECT t.name, t.grp,
        (SELECT COUNT(*) FROM mark_tags mt WHERE mt.tag=t.name AND mt.curator=? AND mt.polarity>0) AS pos,
        (SELECT COUNT(*) FROM mark_tags mt WHERE mt.tag=t.name AND mt.curator=? AND mt.polarity<0) AS neg,
        (SELECT COUNT(*) FROM mark_tags mt WHERE mt.tag=t.name) AS n FROM tags t ORDER BY t.grp, t.name`, [c, c]);
    const labels = q("SELECT l.name, l.grp, (SELECT COUNT(*) FROM film_labels fl WHERE fl.label=l.name) AS n FROM labels l ORDER BY l.grp, l.name");
    const stats = one(`SELECT
        (SELECT COUNT(*) FROM films) AS total,
        (SELECT COUNT(*) FROM marks m WHERE curator=?1 AND status='seen') AS seen,
        (SELECT COUNT(*) FROM marks m WHERE curator=?1 AND status='want') AS want,
        (SELECT COUNT(*) FROM marks m WHERE curator=?1 AND status='not') AS notseen,
        (SELECT COUNT(*) FROM marks m WHERE curator=?1 AND status='seen' AND ${NOTED_SQL}) AS noted,
        (SELECT COUNT(*) FROM marks m WHERE curator=?1 AND status='seen' AND tier IS NOT NULL) AS tiered,
        (SELECT COUNT(*) FROM pairs WHERE curator=?1 AND skipped=0) AS pairs,
        (SELECT COUNT(*) FROM films WHERE in_library=1) AS library`, [c]);
    return { curator: c, curators: curators(), genres, decades, tiers, tags, labels, stats };
  }

  function whereClause(p) {
    const g = k => p.get(k) || "";
    let join = ""; const where = [], args = [];
    if (g("lst")) { join = "JOIN lists lr ON lr.tconst=f.tconst AND lr.list=?"; args.push(g("lst")); }
    if (g("genre")) { where.push("(',' || f.genres || ',') LIKE ?"); args.push(`%,${g("genre")},%`); }
    if (g("label")) { where.push("EXISTS (SELECT 1 FROM film_labels fl WHERE fl.tconst=f.tconst AND fl.label=?)"); args.push(g("label")); }
    if (+g("decade")) { where.push("f.decade = ?"); args.push(+g("decade")); }
    const st = g("status");
    if (st === "unmarked") where.push("m.status IS NULL");
    else if (st === "seen_nonote") where.push(`m.status='seen' AND NOT ${NOTED_SQL}`);
    else if (st === "seen_notier") where.push("m.status='seen' AND m.tier IS NULL");
    else if (st) { where.push("m.status = ?"); args.push(st); }
    if (g("tier")) { where.push("m.tier = ?"); args.push(g("tier")); }
    if (g("tag")) { where.push("EXISTS (SELECT 1 FROM mark_tags mt WHERE mt.curator=m.curator AND mt.tconst=f.tconst AND mt.tag=?)"); args.push(g("tag")); }
    if (g("q")) { where.push("(f.title LIKE ? OR f.original_title LIKE ? OR f.directors LIKE ?)"); args.push(`%${g("q")}%`, `%${g("q")}%`, `%${g("q")}%`); }
    if (+g("library")) where.push("f.in_library = 1");
    return { join, where: where.length ? "WHERE " + where.join(" AND ") : "", args };
  }

  function filmQuery(curator, join, where, order, args, limit = null, offset = 0) {
    let sql = `SELECT ${FILM_COLS},${MY_COLS} FROM films f LEFT JOIN marks m ON m.tconst=f.tconst AND m.curator=? ${join} ${where} ORDER BY ${order}`;
    const a = [curator, ...args];
    if (limit !== null) { sql += " LIMIT ? OFFSET ?"; a.push(limit, offset); }
    return q(sql, a);
  }

  function films(p) {
    const c = checkCurator(p.get("curator") || DEFAULT_CURATOR), lst = p.get("lst") || "";
    const { join, where, args } = whereClause(p);
    const order = {
      rating: "f.rating DESC, f.votes DESC", votes: "f.votes DESC", year: "f.year DESC", year_asc: "f.year ASC", title: "f.title",
      updated: "m.updated DESC", random: "RANDOM()", rt: "f.rt DESC NULLS LAST, f.rating DESC",
      tier: "(SELECT ord FROM tiers t WHERE t.name=m.tier) ASC NULLS LAST, m.rank ASC NULLS LAST, f.rating DESC",
      rank: lst ? "lr.rank" : "f.rating DESC",
    }[p.get("sort") || "rating"] || "f.rating DESC";
    const limit = Math.min(+(p.get("limit") || 60), 500), offset = +(p.get("offset") || 0);
    const rows = filmQuery(c, join, where, order, args, limit, offset);
    const total = val(`SELECT COUNT(*) FROM films f LEFT JOIN marks m ON m.tconst=f.tconst AND m.curator=? ${join} ${where}`, [c, ...args]);
    return { total, films: rows };
  }

  function film(tconst, curator) {
    const r = filmQuery(curator, "", "WHERE f.tconst=?", "f.title", [tconst])[0];
    if (!r) throw new HttpError(404, "no such film");
    return r;
  }

  function lists() {
    const cs = curators(), out = [];
    for (const r of q("SELECT list, COUNT(*) AS total FROM lists GROUP BY list ORDER BY list")) {
      const d = { list: r.list, total: r.total, by: {} };
      for (const c of cs) {
        const s = one(`SELECT SUM(m.status='seen') AS seen, SUM(m.status='want') AS want, SUM(m.status='seen' AND m.tier IS NOT NULL) AS tiered,
                       SUM(m.status='seen' AND ${NOTED_SQL}) AS noted, ROUND(AVG(CASE WHEN m.status='seen' THEN t.score END), 1) AS avg_score
                       FROM lists l JOIN marks m ON m.tconst=l.tconst AND m.curator=? LEFT JOIN tiers t ON t.name=m.tier WHERE l.list=?`, [c.id, r.list]);
        d.by[c.id] = Object.fromEntries(Object.entries(s).map(([k, v]) => [k, k === "avg_score" ? v : (v || 0)]));
      }
      out.push(d);
    }
    return { lists: out, curators: cs };
  }

  function recommend(p) {
    const c = checkCurator(p.get("curator") || DEFAULT_CURATOR), limit = Math.min(+(p.get("limit") || 60), 200);
    const liked = q(`SELECT ${FILM_COLS}, t.score FROM marks m JOIN films f ON f.tconst=m.tconst JOIN tiers t ON t.name=m.tier WHERE m.status='seen' AND m.curator=?`, [c]);
    if (liked.length < 3) return { films: [], reason: `tier at least 3 films you've seen first (${liked.length} so far). this is computed from your tiers.` };
    const n = liked.length, wd = {}, wk = {}, wl = {}, gs = {}, gc = {}, ds = {}, dc = {};
    const add = (o, k, v) => { o[k] = (o[k] || 0) + v; };
    for (const f of liked) {
      const s = f.score / 10;
      for (const d of parts(f.directors, ",")) add(wd, d, s);
      for (const k of parts(f.keywords, ",")) add(wk, k, s);
      for (const k of parts(f.labels, ",")) add(wl, k, s);
      for (const g of parts(f.genres, ",")) { add(gs, g, s); add(gc, g, 1); }
      add(ds, f.decade, s); add(dc, f.decade, 1);
    }
    const sub = new URLSearchParams(); if (p.get("genre")) sub.set("genre", p.get("genre")); if (p.get("lst")) sub.set("lst", p.get("lst"));
    let { join, where, args } = whereClause(sub);
    where = (where ? where + " AND " : "WHERE ") + "(m.status IS NULL OR m.status='want')";
    const out = [];
    for (const f of filmQuery(c, join, where, "f.rating DESC", args)) {
      let sc = 0; const why = [];
      for (const d of parts(f.directors, ",")) if (d in wd) { sc += 3 * wd[d]; why.push(`${wd[d] > 0 ? "liked" : "disliked"} dir. ${d}`); }
      const labs = parts(f.labels, ",").filter(k => k in wl);
      if (labs.length) { sc += 2 * labs.reduce((a, k) => a + wl[k], 0); why.push("labels " + labs.slice(0, 3).join(", ")); }
      const kws = parts(f.keywords, ",").filter(k => k in wk).map(k => [wk[k], k]).sort((a, b) => a[0] - b[0]);
      if (kws.length) { const top = kws[kws.length - 1][0] > 0 ? kws.slice(-5) : kws.slice(0, 5); sc += top.reduce((a, [w]) => a + w, 0); why.push("themes " + top.slice().reverse().slice(0, 3).map(x => x[1]).join(", ")); }
      const g = parts(f.genres, ",").filter(x => x in gc).map(x => (gs[x] / gc[x]) * Math.min(1, gc[x] / 5));
      if (g.length) sc += g.reduce((a, b) => a + b, 0) / g.length;
      if (f.decade in dc) sc += 0.5 * (ds[f.decade] / dc[f.decade]) * Math.min(1, dc[f.decade] / 5);
      if (f.lists) { sc += Math.min(1, 0.3 * f.lists.split("|").length); why.push("on " + f.lists.split("|")[0].replace("#", " #")); }
      if (f.status === "want") { sc += 0.5; why.push("on your want list"); }
      sc += f.rating / 100;
      out.push({ ...f, score: Math.round(sc * 100) / 100, why: why.slice(0, 4) });
    }
    out.sort((a, b) => b.score - a.score);
    return { films: out.slice(0, limit), based_on: n };
  }

  function setLabels(tconst, labels) {
    run("DELETE FROM film_labels WHERE tconst=?", [tconst]);
    for (let l of labels) { l = norm(l); if (!l) continue; run("INSERT OR IGNORE INTO labels VALUES (?,?)", [l, "custom"]); run("INSERT OR IGNORE INTO film_labels VALUES (?,?)", [tconst, l]); }
    labelsAt[tconst] = now();
  }

  function mark(m) {
    const c = checkCurator(m.curator || DEFAULT_CURATOR);
    if (![null, undefined, "seen", "want", "not"].includes(m.status)) throw new HttpError(400, "status must be seen, want, not or null");
    const old = one("SELECT tier FROM marks WHERE curator=? AND tconst=?", [c, m.tconst]);
    const keepRank = !!(old && old.tier === (m.tier ?? null));
    run(`INSERT INTO marks (curator,tconst,status,tier,rank,times,when_seen,memory,about,reached,for_whom,avoid,note,source,updated)
         VALUES (?,?,?,?,NULL,?,?,?,?,?,?,?,?,'me',?)
         ON CONFLICT(curator,tconst) DO UPDATE SET status=excluded.status, tier=excluded.tier, rank=${keepRank ? "rank" : "NULL"},
         times=excluded.times, when_seen=excluded.when_seen, memory=excluded.memory, about=excluded.about, reached=excluded.reached,
         for_whom=excluded.for_whom, avoid=excluded.avoid, note=excluded.note, source='me', updated=excluded.updated`,
      [c, m.tconst, m.status ?? null, m.tier ?? null, m.times ?? null, m.when_seen ?? null, m.memory ?? null,
        m.about ?? null, m.reached ?? null, m.for_whom ?? null, m.avoid ?? null, m.note ?? null, now()]);
    run("DELETE FROM mark_tags WHERE curator=? AND tconst=?", [c, m.tconst]);
    for (const t of m.tags || []) {
      const neg = t.startsWith("-"), name = norm(neg ? t.slice(1) : t); if (!name) continue;
      run("INSERT OR IGNORE INTO tags VALUES (?,?)", [name, "custom"]);
      run("INSERT OR REPLACE INTO mark_tags VALUES (?,?,?,?)", [c, m.tconst, name, neg ? -1 : 1]);
    }
    if (m.labels != null) setLabels(m.tconst, m.labels);
    event("mark", m.tconst, m, "me", c);
    persist();
    return film(m.tconst, c);
  }

  const VOCAB = { tag: ["tags", "mark_tags", "tag"], label: ["labels", "film_labels", "label"] };
  function addVocab(kind, t) {
    const name = norm(t.name); if (!name) throw new HttpError(400, "empty name");
    run(`INSERT OR IGNORE INTO ${VOCAB[kind][0]} VALUES (?,?)`, [name, (t.grp || "").trim() || "custom"]);
    persist(); return { name };
  }
  function editVocab(kind, e) {
    const [table, usage, col] = VOCAB[kind]; let name = norm(e.name);
    if (!one(`SELECT 1 FROM ${table} WHERE name=?`, [name])) throw new HttpError(404, `no ${kind} '${name}'`);
    if (e.to) {
      const to = norm(e.to); if (!to) throw new HttpError(400, "empty name");
      if (to !== name) {
        const exists = one(`SELECT 1 FROM ${table} WHERE name=?`, [to]);
        if (!exists) run(`UPDATE ${table} SET name=? WHERE name=?`, [to, name]);
        run(`UPDATE OR IGNORE ${usage} SET ${col}=? WHERE ${col}=?`, [to, name]);
        run(`DELETE FROM ${usage} WHERE ${col}=?`, [name]);
        run(`DELETE FROM ${table} WHERE name=?`, [name]);
        if (kind === "label") for (const r of q("SELECT DISTINCT tconst FROM film_labels WHERE label=?", [to])) labelsAt[r.tconst] = now();
        event(kind, "", { [exists ? "merge" : "rename"]: [name, to] }, "vocab");
        name = to;
      }
    }
    if (e.grp != null) run(`UPDATE ${table} SET grp=? WHERE name=?`, [(e.grp || "").trim() || "custom", name]);
    persist(); return { name };
  }
  function deleteVocab(kind, name) {
    const [table, usage, col] = VOCAB[kind];
    const n = val(`SELECT COUNT(*) FROM ${usage} WHERE ${col}=?`, [name]);
    if (n) throw new HttpError(409, `${kind} '${name}' is used ${n} times: merge it into another one instead`);
    run(`DELETE FROM ${table} WHERE name=?`, [name]); persist(); return { ok: true };
  }

  function suggest(p) {
    const c = p.get("curator") || DEFAULT_CURATOR, tconst = p.get("tconst"), limit = +(p.get("limit") || 12);
    const f = one(`SELECT ${FILM_COLS} FROM films f WHERE f.tconst=?`, [tconst]);
    if (!f) throw new HttpError(404, "no such film");
    const dirs = new Set(parts(f.directors, ",")), labs = new Set(parts(f.labels, ",")), kws = new Set(parts(f.keywords, ","));
    const score = {}, why = {};
    const rows = q(`SELECT ${FILM_COLS}, (SELECT group_concat(CASE WHEN mt.polarity<0 THEN '-'||mt.tag ELSE mt.tag END, ',') FROM mark_tags mt WHERE mt.curator=m.curator AND mt.tconst=m.tconst) AS tags
                    FROM marks m JOIN films f ON f.tconst=m.tconst WHERE m.curator=? AND m.status='seen' AND f.tconst<>?`, [c, tconst]);
    for (const r of rows) {
      const [pos] = splitTags(r.tags); if (!pos.length) continue;
      let w = 0, reason = null;
      const rl = parts(r.labels, ","), shared = parts(r.keywords, ",").filter(k => kws.has(k)).sort();
      if (parts(r.directors, ",").some(d => dirs.has(d))) { w = 3; reason = "same director"; }
      else if (rl.some(l => labs.has(l))) { w = 2; reason = "label " + rl.filter(l => labs.has(l)).sort()[0]; }
      else if (shared.length >= 2) { w = 1; reason = "themes " + shared.slice(0, 2).join(", "); }
      if (!w) continue;
      for (const t of pos) { score[t] = (score[t] || 0) + w; (why[t] ||= new Set()).add(`${reason}: ${r.title}`); }
    }
    const out = Object.keys(score).sort((a, b) => score[b] - score[a]).slice(0, limit);
    return { tags: out.map(t => ({ name: t, score: score[t], why: [...why[t]].sort().slice(0, 3) })) };
  }

  function winRates(c) {
    const w = {}, n = {};
    for (const r of q("SELECT a,b,winner FROM pairs WHERE curator=? AND skipped=0", [c])) { n[r.a] = (n[r.a] || 0) + 1; n[r.b] = (n[r.b] || 0) + 1; if (r.winner) w[r.winner] = (w[r.winner] || 0) + 1; }
    return t => [w[t] || 0, n[t] || 0];
  }
  function ladder(p) {
    const c = checkCurator(p.get("curator") || DEFAULT_CURATOR), tier = p.get("tier") || "";
    const rows = filmQuery(c, "", "WHERE m.status='seen' AND m.tier=?", "f.title", [tier]), wr = winRates(c);
    for (const d of rows) { const [w, n] = wr(d.tconst); d.wins = w; d.duels = n; d.winrate = n ? Math.round(100 * w / n) / 100 : null; }
    rows.sort((x, y) => (x.rank == null) - (y.rank == null) || (x.rank || 0) - (y.rank || 0) || ((y.winrate ?? -1) - (x.winrate ?? -1)) || y.duels - x.duels || x.title.localeCompare(y.title));
    return { tier, films: rows };
  }
  function saveLadder(l) {
    const c = checkCurator(l.curator || DEFAULT_CURATOR);
    l.order.forEach((t, i) => run("UPDATE marks SET rank=?, updated=? WHERE curator=? AND tconst=? AND tier=?", [i + 1, now(), c, t, l.tier]));
    event("ladder", "", { tier: l.tier, order: l.order }, "me", c); persist(); return { ok: true };
  }

  function pair(p) {
    const c = checkCurator(p.get("curator") || DEFAULT_CURATOR), sameTier = +(p.get("same_tier") || 0), tier = p.get("tier") || "";
    const seen = filmQuery(c, "", "WHERE m.status='seen'" + (tier ? " AND m.tier=?" : ""), "f.title", tier ? [tier] : []);
    if (seen.length < 2) return { a: null, b: null, reason: "mark at least two films as seen first" };
    const done = new Set(q("SELECT a,b FROM pairs WHERE curator=?", [c]).map(r => [r.a, r.b].sort().join("|")));
    const pick = arr => arr[Math.floor(Math.random() * arr.length)];
    for (let i = 0; i < 300; i++) {
      const a = pick(seen), same = seen.filter(f => f.tier === a.tier && f.tconst !== a.tconst);
      if (sameTier && !(a.tier && same.length)) continue;
      const cands = (a.tier && same.length && (sameTier || Math.random() < 0.7)) ? same : seen.filter(f => f.tconst !== a.tconst);
      const b = pick(cands);
      if (!done.has([a.tconst, b.tconst].sort().join("|"))) return { a, b };
    }
    return { a: null, b: null, reason: "you've compared every pair there is - mark more films as seen" };
  }
  function savePair(p) {
    if (![null, undefined, p.a, p.b].includes(p.winner)) throw new HttpError(400, "winner must be a, b or null");
    const c = checkCurator(p.curator || DEFAULT_CURATOR);
    run("INSERT INTO pairs (ts,a,b,winner,skipped,curator) VALUES (?,?,?,?,?,?)", [now(), p.a, p.b, p.winner ?? null, p.winner ? 0 : 1, c]);
    event("pair", p.winner || p.a, p, "me", c); persist();
    return { pairs: val("SELECT COUNT(*) FROM pairs WHERE curator=? AND skipped=0", [c]) };
  }

  function progress(p) {
    const c = checkCurator(p.get("curator") || DEFAULT_CURATOR);
    const agg = `COUNT(*) AS total, SUM(m.status='seen') AS seen, SUM(m.status='want') AS want, SUM(m.status='seen' AND m.tier IS NOT NULL) AS tiered,
                 SUM(m.status='seen' AND ${NOTED_SQL}) AS noted, SUM(m.status IS NULL) AS unmarked`;
    const base = "FROM films f LEFT JOIN marks m ON m.tconst=f.tconst AND m.curator=?";
    const genres = [...new Set(q("SELECT genres FROM films").flatMap(r => parts(r.genres, ",")))].sort();
    const byGenre = genres.map(g => one(`SELECT ? AS key, ${agg} ${base} WHERE (',' || f.genres || ',') LIKE ?`, [g, c, `%,${g},%`]));
    const byDecade = q(`SELECT f.decade AS key, ${agg} ${base} GROUP BY f.decade ORDER BY f.decade`, [c]);
    const byLabel = q(`SELECT fl.label AS key, ${agg} FROM film_labels fl JOIN films f ON f.tconst=fl.tconst LEFT JOIN marks m ON m.tconst=f.tconst AND m.curator=? GROUP BY fl.label ORDER BY total DESC`, [c]);
    const byList = q(`SELECT l.list AS key, ${agg} FROM lists l JOIN films f ON f.tconst=l.tconst LEFT JOIN marks m ON m.tconst=f.tconst AND m.curator=? GROUP BY l.list ORDER BY l.list`, [c]);
    const tiers = q(`SELECT t.name AS key, t.ord, COUNT(m.tconst) AS total, SUM(m.rank IS NOT NULL) AS ranked FROM tiers t
                     LEFT JOIN marks m ON m.tier=t.name AND m.curator=? AND m.status='seen' GROUP BY t.name ORDER BY t.ord`, [c]);
    for (const rows of [byGenre, byDecade, byLabel, byList, tiers]) for (const d of rows) for (const k in d) if (d[k] == null && k !== "key") d[k] = 0;
    return { curator: c, genre: byGenre, decade: byDecade, label: byLabel, list: byList, tier: tiers };
  }

  function pool(p) {
    const s = `%${p.get("q") || ""}%`, limit = Math.min(+(p.get("limit") || 30), 100);
    return { films: q(`SELECT p.* FROM pool p LEFT JOIN films f ON f.tconst=p.tconst WHERE f.tconst IS NULL AND (p.title LIKE ? OR p.original_title LIKE ? OR p.directors LIKE ?) ORDER BY p.votes DESC LIMIT ?`, [s, s, s, limit]) };
  }
  function addFromPool(tconst, added = "pool", log = true) {
    const r = one("SELECT * FROM pool WHERE tconst=?", [tconst]); if (!r) return false;
    run(`INSERT OR IGNORE INTO films (tconst,title,original_title,year,decade,runtime,genres,rating,votes,directors,writers,added) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
      [r.tconst, r.title, r.original_title, r.year, Math.floor(r.year / 10) * 10, r.runtime, r.genres, r.rating, r.votes, r.directors, r.writers, added]);
    if (log) event("add", tconst, { added }, added);
    return true;
  }
  function add(a) {
    if (!addFromPool(a.tconst, a.added || "pool")) throw new HttpError(404, "not in the pool");
    persist(); return film(a.tconst, a.curator || DEFAULT_CURATOR);
  }

  function exportJson() {
    return { marks: [...markRows()], pairs: q("SELECT * FROM pairs ORDER BY id"), tiers: q("SELECT * FROM tiers ORDER BY ord"),
      tags: q("SELECT * FROM tags ORDER BY grp,name"), labels: q("SELECT * FROM labels ORDER BY grp,name"), curators: curators() };
  }

  // ---- router -----------------------------------------------------------------------
  async function route(method, path, p, body) {
    await ready;
    const seg = path.split("/").filter(Boolean);           // ["api", ...]
    const r = seg.slice(1).join("/");
    if (method === "GET") {
      if (r === "meta") return meta(p);
      if (r === "films") return films(p);
      if (seg[1] === "film" && seg[2]) return film(seg[2], p.get("curator") || DEFAULT_CURATOR);
      if (r === "lists") return lists();
      if (r === "recommend") return recommend(p);
      if (r === "suggest") return suggest(p);
      if (r === "ladder") return ladder(p);
      if (r === "pair") return pair(p);
      if (r === "progress") return progress(p);
      if (r === "pool") return pool(p);
      if (r === "export.csv") return { __text: csvText(p.get("curator") || null), type: "text/csv", name: p.get("curator") ? `marks_${p.get("curator")}.csv` : "marks.csv" };
      if (r === "export.md") return { __text: notesMd(p.get("curator") || DEFAULT_CURATOR), type: "text/markdown", name: `notes_${p.get("curator") || DEFAULT_CURATOR}.md` };
      if (r === "export.json") return exportJson();
    }
    if (method === "POST") {
      if (r === "mark") return mark(body);
      if (r === "tag" || r === "label") return addVocab(r, body);
      if (r === "tag/edit" || r === "label/edit") return editVocab(seg[1], body);
      if (r === "ladder") return saveLadder(body);
      if (r === "pair") return savePair(body);
      if (r === "add") return add(body);
    }
    if (method === "DELETE" && (seg[1] === "tag" || seg[1] === "label") && seg[2]) return deleteVocab(seg[1], decodeURIComponent(seg[2]));
    throw new HttpError(404, `no route ${method} ${path}`);
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const u = new URL(typeof input === "string" ? input : input.url, location.href);
    if (!u.pathname.startsWith("/api/")) return realFetch(input, init);
    try {
      const body = init.body ? JSON.parse(init.body) : null;
      const out = await route((init.method || "GET").toUpperCase(), u.pathname, u.searchParams, body);
      if (out && out.__text != null) return new Response(out.__text, { status: 200, headers: { "Content-Type": out.type } });
      return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
    } catch (e) {
      const st = e instanceof HttpError ? e.status : 500;
      if (st === 500) console.error(e);
      return new Response(JSON.stringify({ detail: e.message }), { status: st, statusText: e.message, headers: { "Content-Type": "application/json" } });
    }
  };

  // export links are plain <a href="/api/export…">: make them downloads
  document.addEventListener("click", async e => {
    const a = e.target.closest("a[href]"); if (!a) return;
    const u = new URL(a.getAttribute("href"), location.href); if (!u.pathname.startsWith("/api/")) return;
    e.preventDefault();
    const out = await route("GET", u.pathname, u.searchParams, null);
    const text = out.__text != null ? out.__text : JSON.stringify(out, null, 1);
    const blob = new Blob([text], { type: out.type || "application/json" }), url = URL.createObjectURL(blob);
    const l = document.createElement("a"); l.href = url; l.download = out.name || "export.json"; document.body.appendChild(l); l.click(); l.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }, true);

  // ---- the gear: repo, token, status --------------------------------------------------
  function panel() {
    let el = document.getElementById("cnd-gear");
    if (!el) {
      el = document.createElement("div"); el.id = "cnd-gear";
      el.innerHTML = `<style>
        #cnd-gear{position:fixed;right:12px;bottom:12px;z-index:50;font:13px system-ui,sans-serif}
        #cnd-gear button.g{background:#1b1b1f;color:#e8e6e1;border:1px solid #2a2a30;border-radius:20px;padding:6px 12px;cursor:pointer}
        #cnd-gear .box{display:none;position:absolute;right:0;bottom:36px;width:min(360px,92vw);background:#1b1b1f;border:1px solid #2a2a30;border-radius:10px;padding:14px;color:#e8e6e1;box-shadow:0 8px 30px rgba(0,0,0,.5)}
        #cnd-gear.open .box{display:block}
        #cnd-gear label{display:block;color:#8d8a84;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin:10px 0 3px}
        #cnd-gear input{width:100%;box-sizing:border-box;background:#121214;color:#e8e6e1;border:1px solid #2a2a30;border-radius:6px;padding:6px 8px;font:inherit}
        #cnd-gear .row{display:flex;gap:8px;margin-top:12px} #cnd-gear .row button{flex:1;background:#121214;color:#e8e6e1;border:1px solid #2a2a30;border-radius:6px;padding:7px;cursor:pointer}
        #cnd-gear .row button.p{background:#e5b53d;color:#111;border-color:#e5b53d;font-weight:600}
        #cnd-gear p{color:#8d8a84;font-size:12px;margin:8px 0 0;line-height:1.4} #cnd-gear a{color:#8d8a84}
      </style>
      <div class="box">
        <b>Where your marks are saved</b>
        <p>Always in this browser. To sync between laptops and to the media server, give the page a GitHub token for your <b>private</b> state repo.</p>
        <label>private repo</label><input id="cnd-repo" placeholder="owner/repo">
        <label>fine-grained token · contents: read &amp; write on that repo only</label><input id="cnd-token" type="password" placeholder="github_pat_…" autocomplete="off">
        <div class="row"><button class="p" id="cnd-save">save &amp; sync</button><button id="cnd-clear">forget token</button></div>
        <p>Make the token at github.com → Settings → Developer settings → Fine-grained tokens. Pick only the state repo, permission Contents: Read and write. It never leaves this browser except to api.github.com.</p>
        <p><a href="local.html">on the home network? open the local server instead</a></p>
      </div><button class="g"></button>`;
      document.body.appendChild(el);
      el.querySelector("button.g").onclick = () => el.classList.toggle("open");
      el.querySelector("#cnd-repo").value = cfg.repo; el.querySelector("#cnd-token").value = cfg.token;
      el.querySelector("#cnd-save").onclick = async () => {
        cfg.repo = el.querySelector("#cnd-repo").value.trim(); cfg.token = el.querySelector("#cnd-token").value.trim();
        localStorage.setItem(CFG_KEY, JSON.stringify(cfg)); el.classList.remove("open");
        if (cfg.token) await pull();
      };
      el.querySelector("#cnd-clear").onclick = () => { cfg.token = ""; localStorage.setItem(CFG_KEY, JSON.stringify(cfg)); el.querySelector("#cnd-token").value = ""; status = "local only"; panel(); };
    }
    const b = el.querySelector("button.g");
    b.textContent = (status.startsWith("synced") ? "● " : status.startsWith("sync error") || status.startsWith("error") ? "▲ " : "○ ") + status;
    b.style.color = status.startsWith("synced") ? "#4caf7d" : status.includes("error") ? "#d9534f" : "#8d8a84";
  }
  document.addEventListener("DOMContentLoaded", panel);
  // after a pull merged remote marks in, the page's lists are stale: reload the current view
  document.addEventListener("cnd:refresh", () => { try { if (typeof loadMeta === "function") loadMeta().then(() => typeof load === "function" && load()); } catch {} });
})();

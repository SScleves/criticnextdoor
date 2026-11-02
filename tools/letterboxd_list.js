// Copy a Letterboxd list into a CSV that lists.py understands.
//
// Letterboxd renders lists in the browser and refuses scripts, so this runs *in* the browser:
//   1. open the list's /detail/ view, e.g. https://letterboxd.com/official/list/letterboxds-top-500-films/detail/
//   2. DevTools (F12) -> Console -> paste this file -> Enter
//   3. a "Name,Year" CSV is copied to your clipboard (and printed); save it as list.csv
//   4. python lists.py csv "Letterboxd Top 500" list.csv
//
// Reads every page of the list (100 films per page). Personal use of a page you can see.
(async () => {
  const scrape = doc => [...doc.querySelectorAll("article.list-detailed-entry")].map(a => {
    const title = (a.querySelector("h2 a")?.innerText || "").trim();
    const t = a.innerText.replace(/\s+/g, " ");
    const year = (t.slice(t.indexOf(title) + title.length).match(/\b(18|19|20)\d{2}\b/) || [])[0] || "";
    return { title, year };
  });
  const base = location.pathname.replace(/\/page\/\d+\/?$/, "/").replace(/\/?$/, "/");
  const all = scrape(document);
  const last = Math.max(1, ...[...document.querySelectorAll(".paginate-pages a, .paginate-page a")].map(a => +a.innerText).filter(Boolean));
  for (let p = 2; p <= last; p++) {
    const html = await fetch(`${base}page/${p}/`).then(r => r.text());
    all.push(...scrape(new DOMParser().parseFromString(html, "text/html")));
  }
  const q = s => '"' + s.replace(/"/g, '""') + '"';
  const csv = "Name,Year\n" + all.map(x => q(x.title) + "," + x.year).join("\n") + "\n";
  try { await navigator.clipboard.writeText(csv); console.log(`${all.length} films copied to clipboard`); }
  catch { console.log(csv); console.log(`${all.length} films (clipboard blocked - copy the text above)`); }
})();

#!/usr/bin/env python3
"""
First-pass labels for the obvious films, so the catalogue isn't blank before anyone sits down.

Rules only: IMDb genres and TMDB keywords (enrich_tmdb.py / enrich_radarr.py fill `keywords`;
without them only the genre rules fire). Every label this writes carries source='auto', so it
never counts as a curator's word and never overwrites one. Re-run any time: it removes its own
old rows first and leaves 'me' rows alone.

    python seed_labels.py            # write
    python seed_labels.py --dry      # just count
"""
import re, sqlite3, sys

from app import DB
from setup import migrate

# label -> TMDB keywords (any match). Keyword matching is whole-phrase, case-insensitive, against the comma list.
BY_KEYWORD = {
    "home-invasion": ["home invasion"], "slasher": ["slasher"], "serial-killer": ["serial killer"],
    "psychological-horror": ["psychological horror"], "folk-horror": ["folk horror", "pagan", "cult", "rural horror"],
    "creature-feature": ["monster", "creature", "giant monster", "kaiju"], "found-footage": ["found footage"],
    "haunted-house": ["haunted house", "haunting", "ghost"], "possession": ["possession", "exorcism", "demonic possession"],
    "zombie": ["zombie", "zombie apocalypse"], "vampire": ["vampire"],
    "coming-of-age": ["coming of age"], "courtroom": ["courtroom", "trial", "lawyer", "court case"],
    "political-thriller": ["political thriller", "conspiracy", "political corruption"], "spy": ["spy", "espionage", "secret agent", "cia", "kgb"],
    "heist": ["heist", "robbery", "bank robbery"], "detective": ["detective", "private detective", "police detective"],
    "murder-mystery": ["murder mystery", "whodunit"], "whodunit": ["whodunit"], "gangster": ["gangster", "mafia", "organized crime", "mob"],
    "revenge": ["revenge", "vengeance"], "survival": ["survival", "stranded", "wilderness"], "disaster": ["disaster", "earthquake", "tsunami", "volcano"],
    "martial-arts": ["martial arts", "kung fu", "karate"], "superhero": ["superhero", "marvel comics", "dc comics", "based on comic"],
    "time-travel": ["time travel", "time loop"], "alien": ["alien", "alien invasion", "extraterrestrial"], "space": ["space", "outer space", "space travel", "astronaut"],
    "dystopia": ["dystopia"], "post-apocalyptic": ["post-apocalyptic future", "post-apocalyptic", "apocalypse"], "cyberpunk": ["cyberpunk"],
    "fantasy-quest": ["quest", "sword and sorcery", "magic"], "showbiz": ["hollywood", "filmmaking", "show business", "actress", "actor"],
    "biopic": ["biography", "based on true story", "based on a true story"], "satire": ["satire"], "black-comedy": ["black comedy", "dark comedy"],
    "romcom": ["romantic comedy"], "road-movie": ["road trip", "road movie"], "buddy": ["buddy", "friendship"],
    "animal-tale": ["dog", "cat", "horse", "animal"], "period-drama": ["period drama", "19th century", "18th century", "victorian era"],
    "teen": ["teenager", "high school", "teen"], "experimental": ["experimental", "surreal", "surrealism"], "cult-classic": ["cult film"],
    "anthology": ["anthology"], "mockumentary": ["mockumentary"],
    "gory": ["gore", "splatter"], "violent": ["violence", "brutality", "ultra violence"], "steamy": ["erotica", "erotic", "sexuality"],
    "mind-bending": ["mind-bending", "nonlinear timeline", "unreliable narrator", "dream"],
}
# label -> IMDb genres (all must be present)
BY_GENRE = {
    "war": ["War"], "western": ["Western"], "musical": ["Musical"], "sports": ["Sport"], "biopic": ["Biography"],
    "romcom": ["Romance", "Comedy"], "black-comedy": ["Comedy", "Crime"],
}


def main():
    dry = "--dry" in sys.argv
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    migrate(con)
    con.execute("DELETE FROM film_labels WHERE source='auto'")
    n, films, with_kw = 0, 0, 0
    for f in con.execute("SELECT tconst, genres, keywords FROM films"):
        genres = set((f["genres"] or "").split(","))
        kws = {k.strip().lower() for k in (f["keywords"] or "").split(",") if k.strip()}
        with_kw += bool(kws)
        labels = {l for l, g in BY_GENRE.items() if all(x in genres for x in g)}
        labels |= {l for l, ks in BY_KEYWORD.items() if kws & {k.lower() for k in ks}}
        if not labels:
            continue
        films += 1
        for l in labels:
            con.execute("INSERT OR IGNORE INTO labels VALUES (?,?)", (l, "subgenre"))
            con.execute("INSERT OR IGNORE INTO film_labels (tconst,label,source) VALUES (?,?,'auto')", (f["tconst"], l)); n += 1
    if dry:
        con.rollback()
    else:
        con.commit()
    print(f"{'would write' if dry else 'wrote'} {n} auto labels on {films} films ({with_kw} films have TMDB keywords; run enrich_tmdb.py or enrich_radarr.py first for more)")


if __name__ == "__main__":
    main()

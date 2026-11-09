"""
The match room's server side: rooms (two or more phones agreeing on a film) and the bot.

Mounted into app.py with two lines. Rooms live in memory and in state/rooms.json so a restart
keeps them; they are small (answers, a deck of ~20 films, swipes). The deck itself is computed
by the first member's browser with the same engine the solo mode uses (static/match.html), so
there is one engine, not two. The bot runs the Claude CLI already logged in on this machine with
the curators' notes as its reading; no API key.

Endpoints:
  GET  /match                          the page
  POST /api/room            {name}     -> {code}
  GET  /api/room/{code}                -> the room
  POST /api/room/{code}/join    {name}
  POST /api/room/{code}/answers {name, answers}
  POST /api/room/{code}/deck    {deck}          first one in wins; later posts are ignored
  POST /api/room/{code}/swipe   {name, tconst, yes}
  POST /api/bot             {question, names?} -> {answer}
"""
import json, os, random, string, subprocess, threading, time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
ROOMS_FILE = os.path.join(HERE, "state", "rooms.json")
EXPORTS = os.environ.get("FILMCAT_EXPORTS", os.path.join(HERE, "exports"))
router = APIRouter()
_lock = threading.Lock()


def _load():
    try:
        return json.load(open(ROOMS_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


ROOMS = _load()


def _save():
    os.makedirs(os.path.dirname(ROOMS_FILE), exist_ok=True)
    json.dump(ROOMS, open(ROOMS_FILE, "w", encoding="utf-8"), ensure_ascii=False)


def _room(code):
    r = ROOMS.get(code.upper())
    if not r:
        raise HTTPException(404, "no such room")
    return r


def _code():
    while True:
        c = "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(4))
        if c not in ROOMS:
            return c


@router.get("/match")
@router.get("/match.html")
def match_page():
    return FileResponse(os.path.join(HERE, "static", "match.html"))


class Name(BaseModel):
    name: str


class Answers(BaseModel):
    name: str
    answers: dict


class Deck(BaseModel):
    deck: list


class Swipe(BaseModel):
    name: str
    tconst: str
    yes: bool


@router.post("/api/room")
def create_room(n: Name):
    with _lock:
        # rooms older than a day are gone
        for c in [c for c, r in ROOMS.items() if time.time() - r["created"] > 86400]:
            del ROOMS[c]
        code = _code()
        ROOMS[code] = {"code": code, "created": time.time(), "members": [{"name": n.name.strip() or "guest", "answers": None}],
                       "deck": None, "swipes": {}, "match": None}
        _save()
        return ROOMS[code]


@router.get("/api/room/{code}")
def get_room(code: str):
    return _room(code)


@router.post("/api/room/{code}/join")
def join(code: str, n: Name):
    with _lock:
        r = _room(code)
        name = n.name.strip() or "guest"
        if not any(m["name"] == name for m in r["members"]):
            if r["deck"]:
                raise HTTPException(409, "this room already has its deck; start a new one")
            r["members"].append({"name": name, "answers": None})
        _save()
        return r


@router.post("/api/room/{code}/answers")
def answers(code: str, a: Answers):
    with _lock:
        r = _room(code)
        m = next((m for m in r["members"] if m["name"] == a.name), None)
        if not m:
            raise HTTPException(404, "not in this room")
        m["answers"] = a.answers
        _save()
        return r


@router.post("/api/room/{code}/deck")
def set_deck(code: str, d: Deck):
    with _lock:
        r = _room(code)
        if r["deck"] is None and d.deck:
            r["deck"] = d.deck
            _save()
        return r


@router.post("/api/room/{code}/swipe")
def swipe(code: str, s: Swipe):
    with _lock:
        r = _room(code)
        r["swipes"].setdefault(s.name, {})[s.tconst] = s.yes
        if r["match"] is None and s.yes:
            if all(r["swipes"].get(m["name"], {}).get(s.tconst) for m in r["members"]):
                r["match"] = s.tconst
        _save()
        return r


# ---- the bot: Claude, reading the curators' notes -----------------------------

class Ask(BaseModel):
    question: str
    names: list[str] = []


BOT_RULES = """You are the critic next door: a friend who has watched a lot and keeps notes. You are helping
someone pick a film for tonight. Below are the curators' own notes on films they've seen (their words,
their scale from -10 to 10, their end-feelings and labels). Answer ONLY from these notes and the
catalogue vocabulary; if the notes don't cover it, say so plainly and suggest how to narrow it down.
Suggest at most three films. For each: title (year), one line on why, quoting or paraphrasing the
curator's own words and naming the curator. Short. No lists of ten. No plot summaries."""


def _notes(limit=60000):
    out = []
    for f in sorted(os.listdir(EXPORTS)) if os.path.isdir(EXPORTS) else []:
        if f.startswith("notes_") and f.endswith(".md"):
            out.append(open(os.path.join(EXPORTS, f), encoding="utf-8").read())
    text = "\n\n".join(out)
    return text[:limit] + ("\n\n[notes truncated]" if len(text) > limit else "")


@router.post("/api/bot")
def bot(a: Ask):
    q = a.question.strip()
    if not q:
        raise HTTPException(400, "ask something")
    who = f"The people asking: {', '.join(a.names)}.\n" if a.names else ""
    prompt = f"{BOT_RULES}\n\n{who}\n=== CURATORS' NOTES ===\n{_notes()}\n=== END NOTES ===\n\nQuestion: {q}"
    try:
        r = subprocess.run(["claude", "-p", "--output-format", "text"], input=prompt, capture_output=True,
                           text=True, timeout=120, cwd=os.path.join(HERE, "state") if os.path.isdir(os.path.join(HERE, "state")) else HERE)
    except FileNotFoundError:
        raise HTTPException(503, "the claude CLI is not installed on this machine")
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "the bot took too long")
    if r.returncode != 0:
        raise HTTPException(502, (r.stderr or "bot failed").strip()[:300])
    return {"answer": r.stdout.strip()}

#!/usr/bin/env python3
"""
ParlayLabs Bar Ticker
---------------------
Scrolls every game on today (NFL, NBA, college football, college hoops, NHL, MLB)
with live scores and pre-game odds, plus ParlayLabs parlay results, across a chain of
HUB75 LED panels on a Raspberry Pi.

- Lions / Pistons / MSU touchdowns, wins and close games flash a full-screen alert.
- A control page on your home Wi-Fi (http://ticker.local) lets you pick what shows:
  scroll everything, stay on one game, a picture or GIF, or your own message.

Run on the Pi:      sudo python3 ticker.py
Try it on a PC:     python3 ticker.py --sim --demo        (control page at http://localhost:8080)
Scroll GIF:         python3 ticker.py --preview
Alert GIF:          python3 ticker.py --test-alert td     (td / win / close)
"""
import argparse, colorsys, io, json, math, os, random, time, threading, urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageSequence

HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------ settings
PANELS        = 4          # 64x32 panels chained left-to-right (4 x P5 = ~4.2 ft)
ROWS, COLS    = 32, 64
W             = COLS * PANELS
SCROLL_SPEED  = 40         # pixels per second
REFRESH_SEC   = 5          # default refresh for the whole board (changeable on the phone page, 5-60 s)
FAST_SEC      = 5          # leagues where my team (or the game you picked) is live: always this fast
ALERT_SECONDS = 10         # how long an alert takes over the ticker
WEB_PORT      = 80         # control page: http://ticker.local (set hostname "ticker" in Pi Imager)
FONT_PATH     = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_SIZE     = 20
SETTINGS_FILE = os.path.join(HERE, "settings.json")
IMAGE_FILE    = os.path.join(HERE, "custom_image")

# My teams, per league (DET in the NHL/MLB is the Red Wings/Tigers, so leagues matter)
#   (league, ESPN abbreviation): (name on the alert, main color, second color)
MY_TEAMS = {
    ("NFL", "DET"): ("LIONS",    (0, 118, 182), (190, 195, 200)),
    ("NBA", "DET"): ("PISTONS",  (200, 16, 46), (29, 66, 138)),
    ("CFB", "MSU"): ("SPARTANS", (0, 150, 70),  (255, 255, 255)),
    ("CBB", "MSU"): ("SPARTANS", (0, 150, 70),  (255, 255, 255)),
}
# "Close game" = late in the game and within this many points
CLOSE = {
    "NFL": dict(period=4, clock=8 * 60, margin=8),   # 4th quarter, under 8:00, one score
    "CFB": dict(period=4, clock=8 * 60, margin=8),
    "NBA": dict(period=4, clock=5 * 60, margin=6),   # 4th quarter, under 5:00
    "CBB": dict(period=2, clock=5 * 60, margin=6),   # 2nd half, under 5:00
    "NHL": dict(period=3, clock=10 * 60, margin=1),  # 3rd period, under 10:00, one goal (or OT)
    "MLB": dict(period=7, clock=None, margin=2),     # 7th inning or later, within 2 runs
}
CLOSE_GAMES = "@close"     # put in game_ids to follow every close game automatically
LIVE_GAMES  = "@live"      # follow every game in progress ("@live:NFL" for one league)
MY_GAMES    = "@mine"      # follow the Lions, Pistons and MSU
LINGER_SEC  = 15 * 60      # live games stay on this long after they end

def late(g):
    rule = CLOSE.get(g["league"])
    if not rule or g["state"] != "in":
        return False
    if g["period"] > rule["period"] or rule["clock"] is None:
        return g["period"] >= rule["period"]
    return g["period"] == rule["period"] and g["clock"] <= rule["clock"]

def is_close(g):
    """Late in the game and within one score (see CLOSE)."""
    return late(g) and abs(g["ascore"] - g["hscore"]) <= CLOSE[g["league"]]["margin"]

PARLAYLABS = "https://callis11.github.io/parlay-lab"
PL_REFRESH = 600           # check ParlayLabs every 10 minutes
PL_SPORTS  = {"NFL": "", "NBA": "/nba", "CFB": "/cfb", "CBB": "/cbb"}

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUES = [  # (label, espn path, color)
    ("NFL", "football/nfl",                      (0, 120, 255)),
    ("NBA", "basketball/nba",                    (255, 90, 0)),
    ("CFB", "football/college-football",         (0, 200, 90)),
    ("CBB", "basketball/mens-college-basketball",(160, 80, 255)),
    ("NHL", "hockey/nhl",                        (200, 200, 200)),
    ("MLB", "baseball/mlb",                      (255, 40, 40)),
]
MOTIONS = ["scroll", "fade", "flash", "slide"]       # how the scores move: crawl, or one item at a time
EFFECTS = ["steady", "scroll", "blink", "rainbow", "fireworks"]          # message styles
SCROLL_CHOICES = ["NFL", "NBA", "CFB", "CBB", "NHL", "MLB", "PL"]   # PL = ParlayLabs results
CFB_TOP25_ONLY = True      # college slates are huge; scroll only ranked teams + my teams

WHITE, GOLD, GREEN, RED, GRAY = (255,255,255), (255,190,0), (0,230,80), (255,40,40), (130,130,130)
ODDS_COLOR, DOT = (0, 200, 200), ("  •  ", (60, 60, 60))

# ------------------------------------------------------------------ control state (set from the phone)
class Control:
    KEYS = {"mode": "scroll", "leagues": [], "game_ids": [], "rotate": 10, "message": "GO LIONS!", "color": "#ffbe00",
            "brightness": 60, "tv_delay": 0, "alerts": True, "fit": "contain",
            "refresh": REFRESH_SEC, "odds": ["ou", "ml"], "odds_on": True,
            "msg_in_scroll": False, "effect": "steady",
            "group": "league", "days": "week", "pl_parts": ["record", "parlays", "props"],
            "speed": SCROLL_SPEED, "motion": "scroll", "hold": 5,
            "pl_count": 5, "pl_sports": [], "prop_types": [], "game_motion": "slide", "ranks": True,
            "game_speed": SCROLL_SPEED}

    def __init__(self):
        self.__dict__.update(self.KEYS)
        self.frames = []            # picture / GIF frames: [(Image, ms), ...]
        self.last_frame = Image.new("RGB", (W, ROWS))
        self.version = 0            # bumps on every change so the display redraws
        try:
            self.update(json.load(open(SETTINGS_FILE)), save=False)
        except (OSError, ValueError):
            pass
        if os.path.exists(IMAGE_FILE):
            try:
                self.set_image(open(IMAGE_FILE, "rb").read(), save=False)
            except Exception:
                pass
        if self.mode == "image" and not self.frames:
            self.mode = "scroll"

    def update(self, d, save=True):
        if d.get("game_id") and "game_ids" not in d:      # older single-game setting
            d = dict(d, game_ids=[d["game_id"]])
        for k, v in d.items():
            if k in self.KEYS:
                setattr(self, k, v)
        self.game_ids = [str(i) for i in (self.game_ids or [])][:40]
        self.rotate = max(3, min(60, int(self.rotate)))
        self.leagues = [l for l in (self.leagues or []) if l in SCROLL_CHOICES]   # empty = everything
        self.odds = [o for o in (self.odds or []) if o in ("ou", "ml", "spread")]
        self.msg_in_scroll = bool(self.msg_in_scroll)
        self.odds_on = bool(self.odds_on)
        self.group = "time" if self.group == "time" else "league"
        self.days = "today" if self.days == "today" else "week"
        self.pl_parts = [x for x in (self.pl_parts or []) if x in ("record", "parlays", "props")]
        self.speed = max(10, min(120, int(self.speed)))
        self.motion = self.motion if self.motion in MOTIONS else "scroll"
        self.hold = max(2, min(15, int(self.hold)))
        self.ranks = bool(self.ranks)
        SHOW_RANKS["on"] = self.ranks
        self.game_motion = self.game_motion if self.game_motion in ("cut", "fade", "flash", "slide", "push", "scroll") else "slide"
        self.game_speed = max(10, min(120, int(self.game_speed)))
        self.pl_count = max(1, min(10, int(self.pl_count)))
        self.pl_sports = [x for x in (self.pl_sports or []) if x in PL_SPORTS]      # empty = all sports
        self.prop_types = [x for x in (self.prop_types or []) if x in PROP_TYPES]  # empty = all types
        if self.effect not in EFFECTS:
            self.effect = "steady"

    def show_odds(self):
        """The odds to show right now: the picks, unless the master switch is off."""
        return self.odds if self.odds_on else []
        self.brightness = max(5, min(100, int(self.brightness)))
        self.tv_delay = max(0, min(120, int(self.tv_delay)))
        self.refresh = max(5, min(60, int(self.refresh)))
        self.version += 1
        if save:
            json.dump({k: getattr(self, k) for k in self.KEYS}, open(SETTINGS_FILE, "w"))

    def set_image(self, data, fit=None, save=True):
        fit = fit or self.fit
        src = Image.open(io.BytesIO(data))
        frames = []
        for i, fr in enumerate(ImageSequence.Iterator(src)):
            if i >= 300:
                break
            frames.append((fit_image(fr.convert("RGB"), fit), max(30, fr.info.get("duration", 100))))
        self.frames = frames
        if save:
            open(IMAGE_FILE, "wb").write(data)
            self.update({"mode": "image", "fit": fit})

    def as_dict(self):
        return {k: getattr(self, k) for k in self.KEYS}

def fit_image(img, fit):
    out = Image.new("RGB", (W, ROWS))
    if fit == "fill":                            # crop to fill the whole strip
        s = max(W / img.width, ROWS / img.height)
        r = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
        out.paste(r, ((W - r.width) // 2, (ROWS - r.height) // 2))
    else:                                        # fit inside, black bars
        s = min(W / img.width, ROWS / img.height)
        r = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
        out.paste(r, ((W - r.width) // 2, (ROWS - r.height) // 2))
    return out

def hex_rgb(h):
    h = (h or "#ffffff").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return WHITE

# ------------------------------------------------------------------ data
def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "parlaylabs-ticker"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def clock_secs(txt):
    try:
        m, s = (txt or "0:00").split(":")
        return int(m) * 60 + float(s)
    except ValueError:
        return 0

def parse_game(label, ev):
    comp = ev["competitions"][0]
    teams = {c["homeAway"]: c for c in comp["competitors"]}
    a, h = teams["away"], teams["home"]
    st = ev.get("status") or comp.get("status") or {}
    odds = (comp.get("odds") or [{}])[0]
    return dict(
        id=f"{label}-{ev['id']}", league=label,
        away=a["team"]["abbreviation"], home=h["team"]["abbreviation"],
        ascore=_score(a), hscore=_score(h),
        state=st["type"]["state"], detail=st["type"]["shortDetail"],
        period=st.get("period", 0), clock=clock_secs(st.get("displayClock")),
        ranked=any(c.get("curatedRank", {}).get("current", 99) <= 25 for c in (a, h)),
        arank=_rank(a), hrank=_rank(h),
        spread=odds.get("details"), total=odds.get("overUnder"),
        mla=moneyline(odds, "away"), mlh=moneyline(odds, "home"),
        start=start_time(ev.get("date")),
    )

def _score(c):
    v = c.get("score")
    if isinstance(v, dict):                      # team schedules give {"value": 21.0, "displayValue": "21"}
        v = v.get("value")
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0

# ESPN team ids, for looking up a team's next game when it isn't on today's board
TEAM_IDS = {("NFL", "DET"): 8, ("NBA", "DET"): 8, ("CFB", "MSU"): 127, ("CBB", "MSU"): 127}
LEAGUE_PATHS = {l: p for l, p, _ in LEAGUES}
NEXT_REFRESH = 30 * 60     # look up my teams' next games every 30 minutes

def date_label(ts):
    """'SUN 11/9 1:00 PM' (just the time for today)."""
    t = datetime.fromtimestamp(ts)
    if t.date() == datetime.now().date():
        return t.strftime("%-I:%M %p")
    return t.strftime("%a %-m/%-d %-I:%M %p").upper()

def fetch_next_game(league, team_id):
    """A team's next game that hasn't finished, from its ESPN schedule. Then that day's scoreboard,
    which has the odds once books post them. Returns a game dict or None."""
    path = LEAGUE_PATHS[league]
    sched = get_json(f"{ESPN}/{path}/teams/{team_id}/schedule")
    now, nxt = time.time(), None
    for ev in sched.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        state = ((ev.get("status") or comp.get("status") or {}).get("type") or {}).get("state")
        if state != "post" and start_time(ev.get("date")) > now - 6 * 3600:
            nxt = ev
            break
    if not nxt:
        return None
    game = parse_game(league, nxt)
    try:                                          # same game from the scoreboard, with odds if posted
        day = datetime.fromtimestamp(game["start"]).strftime("%Y%m%d")
        extra = "&groups=50" if league == "CBB" else ""
        board = get_json(f"{ESPN}/{path}/scoreboard?dates={day}{extra}")
        for ev in board.get("events", []):
            if str(ev.get("id")) == str(nxt.get("id")):
                game = parse_game(league, ev)
                break
    except Exception:
        pass
    if game["state"] == "pre" and game.get("start"):
        game["detail"] = date_label(game["start"])
    game["next"] = True
    return game

def start_time(iso):
    """ESPN start time ("2026-11-01T18:00Z") as seconds since 1970, or 0."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0

def moneyline(odds, side):
    """ESPN moneyline for one team, as text like "-142" / "+120", or None."""
    try:
        v = (odds.get(f"{side}TeamOdds") or {}).get("moneyLine")
        if v is None:
            ml = (odds.get("moneyline") or {}).get(side) or {}
            v = (ml.get("close") or ml.get("open") or {}).get("odds")
        if v in (None, "", "OFF", "EVEN"):
            return "EVEN" if v == "EVEN" else None
        v = int(float(str(v).replace("+", "")))
        return f"+{v}" if v > 0 else str(v)
    except (TypeError, ValueError):
        return None

def _rank(c):
    r = (c.get("curatedRank") or {}).get("current", 99)
    return r if isinstance(r, int) and 1 <= r <= 25 else None

SHOW_RANKS = {"on": True}  # the phone's "College rankings" switch

def team(g, side):
    """Team name for the ticker, with its AP Top 25 rank in front for college games: "#12 MSU"."""
    r = g.get("arank" if side == "away" else "hrank")
    return f"#{r} {g[side]}" if r and SHOW_RANKS["on"] else g[side]

def mine(g):
    """Returns (abbr, team-info) if one of my teams is in this game."""
    for side in ("away", "home"):
        key = (g["league"], g[side])
        if key in MY_TEAMS:
            return g[side], MY_TEAMS[key]
    return None, None

def fetch_league(label, path):
    data = get_json(f"{ESPN}/{path}/scoreboard")
    games = []
    for ev in data.get("events", []):
        try:
            games.append(parse_game(label, ev))
        except (KeyError, IndexError, ValueError):
            pass
    return games

def short_time(detail):
    return detail.split(" - ")[-1].replace(" EST", "").replace(" EDT", "")

def odds_text(g, picks):
    """The odds the phone has switched on, for a game that hasn't started."""
    out = []
    if "ml" in picks and g.get("mla") and g.get("mlh"):
        out.append(f"{g['away']} {g['mla']} {g['home']} {g['mlh']}")
    if "ou" in picks and g.get("total"):
        out.append(f"O/U {g['total']}")
    if "spread" in picks and g.get("spread"):
        out.append(g["spread"])
    return "  ".join(out)

def is_today(g):
    return g["state"] == "in" or (g.get("start") and
           datetime.fromtimestamp(g["start"]).date() == datetime.now().date())

def slot_label(ts):
    """Start time slot like "SUN 1:00 PM" (just "1:00 PM" for today)."""
    t = datetime.fromtimestamp(ts)
    clock = t.strftime("%-I:%M %p")
    return clock if t.date() == datetime.now().date() else f"{t:%a} {clock}".upper()

def game_text(g, odds):
    """One game as scroll segments (without the league or time header)."""
    gold = bool(mine(g)[0])
    if g["state"] == "pre":
        out = [(f"{team(g, 'away')} @ {team(g, 'home')}", GOLD if gold else GRAY)]
        line = odds_text(g, odds)
        if line:
            out.append((f"  {line}", ODDS_COLOR))
        return out
    col = GOLD if gold else (WHITE if g["state"] == "in" else (180, 180, 180))
    return [(f"{team(g, 'away')} {g['ascore']}  {team(g, 'home')} {g['hscore']}  {g['detail']}", col)]

def visible_games(games, only=None, days="week"):
    out = []
    for g in games:
        if only and g["league"] not in only:
            continue
        if g["league"] in ("CFB", "CBB") and CFB_TOP25_ONLY and not (g["ranked"] or mine(g)[0]):
            continue
        if days == "today" and not is_today(g):
            continue
        out.append(g)
    return out

def time_segments(games, only=None, odds=("ou", "ml"), days="week"):
    """Every game grouped by when it plays: LIVE first, then each start time in order, then FINAL.
    Each game gets a small league tag since leagues are mixed together."""
    lg_color = {l: c for l, _, c in LEAGUES}
    games = visible_games(games, only, days)
    live  = [g for g in games if g["state"] == "in"]
    pre   = sorted((g for g in games if g["state"] == "pre"), key=lambda g: (g.get("start") or 0, g["league"]))
    final = sorted((g for g in games if g["state"] == "post"), key=lambda g: -(g.get("start") or 0))
    groups = [("LIVE", RED, live)]
    for g in pre:
        label = slot_label(g["start"]) if g.get("start") else short_time(g["detail"]).upper()
        if groups[-1][0] != label:
            groups.append((label, (0, 180, 255), []))
        groups[-1][2].append(g)
    groups.append(("FINAL", (150, 150, 150), final))
    segs = []
    for label, color, gs in groups:
        if not gs:
            continue
        segs.append((f"  {label}  ", color, "hdr"))
        for g in gs:
            segs.append((f"{g['league']} ", lg_color[g["league"]]))
            segs += game_text(g, odds)
            segs.append(DOT)
    return segs

def score_segments(games, only=None, odds=("ou", "ml"), days="week"):
    """Scrolling text for every game: live/final scores, and odds for games not started."""
    segs = []
    for label, _, color in LEAGUES:
        if only and label not in only:
            continue
        lg = [g for g in visible_games(games, None, days) if g["league"] == label]
        lg.sort(key=lambda g: {"in": 0, "pre": 1, "post": 2}[g["state"]])   # live games first
        if not lg:
            continue
        segs.append((f"  {label}  ", color, "hdr"))
        for g in lg:
            gold = bool(mine(g)[0])
            if g["state"] == "pre":
                segs.append((f"{team(g, 'away')} @ {team(g, 'home')} {short_time(g['detail'])}", GOLD if gold else GRAY))
                line = odds_text(g, odds)
                if line:
                    segs.append((f"  {line}", ODDS_COLOR))
            else:
                col = GOLD if gold else (WHITE if g["state"] == "in" else (180, 180, 180))
                segs.append((f"{team(g, 'away')} {g['ascore']}  {team(g, 'home')} {g['hscore']}  {g['detail']}", col))
            segs.append(DOT)
    return segs

# ---- ParlayLabs ----------------------------------------------------------------
TOP_GROUPS = ("Best of the week", "Lions parlays", "Pistons parlays", "Spartans parlays", "Michigan State parlays")
SHORT_WORDS = [("receiving yds", "rec yds"), ("rushing yds", "rush yds"), ("passing yds", "pass yds"),
               ("passing TDs", "pass TDs"), ("receptions", "rec"), ("reception", "rec"), ("anytime TD", "TD")]
PROP_LABELS = {"Anytime TD": "TD", "Receptions": "rec", "Receiving yards": "rec yds", "Rushing yards": "rush yds",
               "Passing yards": "pass yds", "Passing TDs": "pass TDs", "Points": "pts", "Rebounds": "reb",
               "Assists": "ast", "Threes": "3PM", "Steals": "stl", "Blocks": "blk",
               "Rush + rec yards": "rush+rec yds", "Pts + reb + ast": "PRA"}
# prop types you can pick on the phone (ParlayLabs label names)
PROP_TYPES = ["Anytime TD", "Receptions", "Receiving yards", "Rushing yards", "Rush + rec yards", "Passing yards",
              "Passing TDs", "Points", "Rebounds", "Assists", "Threes", "Pts + reb + ast"]
MY_ABBRS = {"NFL": {"DET"}, "NBA": {"DET"}, "CFB": {"MSU"}, "CBB": {"MSU"}}

def american(dec):
    if dec >= 2:
        return f"+{round((dec - 1) * 100)}"
    return f"-{round(100 / (dec - 1))}" if dec > 1 else "EVEN"

def leg_dec(a):
    return 1 + a / 100 if a > 0 else 1 + 100 / -a

def initial(name):
    """'Jahmyr Gibbs' -> 'J. Gibbs' (saves room on the strip)."""
    parts = name.split(" ", 1)
    return f"{parts[0][0]}. {parts[1]}" if len(parts) == 2 and len(parts[0]) > 2 else name

def short_leg(leg):
    txt = leg.get("n", "")
    if leg.get("kind") == "p" and leg.get("player") and txt.startswith(leg["player"]):
        txt = initial(leg["player"]) + txt[len(leg["player"]):]
    for a, b in SHORT_WORDS:
        txt = txt.replace(a, b)
    return txt

def top_parlays(slips, now=None):
    """This week's featured parlays from ParlayLabs, still open: one list of scroll segments per parlay."""
    now_ms = (time.time() if now is None else now) * 1000
    picks = [s for s in slips if s.get("status") == "open" and s.get("group") in TOP_GROUPS
             and any(_leg_ts(l) * 1000 > now_ms for l in s.get("legs", []))]
    out = []
    for s in picks:
        dec = 1.0
        for l in s["legs"]:
            dec *= leg_dec(l["a"])
        out.append([(f"{s['name']} {american(dec)}: ", WHITE),
                    (" · ".join(short_leg(l) for l in s["legs"]), (200, 200, 200)),
                    (f"  $10 pays ${10 * dec:,.0f}", GREEN), DOT])
    return out

def _leg_ts(leg):
    return start_time(leg.get("time")) if leg.get("time") else time.time() + 1

def over_candidates(data, odds, now=None):
    """Every player over for games that haven't started, priced -250 to +150 (worth showing)."""
    starts = {g["id"]: start_time(g.get("commence_time")) for g in (odds or {}).get("lines", [])}
    now, out = (time.time() if now is None else now), []
    for pr in data.get("props", []):
        if starts.get(pr.get("event"), now + 1) <= now:
            continue
        for L in pr.get("ladder", []):
            prices = L.get("prices") or {}
            if "p" not in L or not prices:
                continue
            price = max(prices.values())
            if -250 <= price <= 150:
                out.append({"player": pr["player"], "team": pr.get("team"), "label": pr["label"], "td": pr.get("td"),
                            "line": L["line"], "price": price, "p": L["p"], "hits": L.get("hits", 0), "n": L.get("n")})
    return out

def top_overs(cands, sport, types=(), limit=5):
    """Best over per player (in the prop types picked, all if none), most likely first.
    My teams' players go first (up to 2), in gold."""
    best = {}
    for c in cands:
        if types and c["label"] not in types:
            continue
        if c["player"] not in best or c["p"] > best[c["player"]]["p"]:
            best[c["player"]] = c
    ranked = sorted(best.values(), key=lambda c: -c["p"])
    mine_first = [c for c in ranked if c["team"] in MY_ABBRS.get(sport, ())][:2]
    segs = []
    for c in (mine_first + [c for c in ranked if c not in mine_first])[:limit]:
        label = PROP_LABELS.get(c["label"], c["label"].lower())
        line = "" if c["td"] else f"{math.floor(c['line']) + 1}+ "
        segs.append((f"{initial(c['player'])} {line}{label}", GOLD if c["team"] in MY_ABBRS.get(sport, ()) else WHITE))
        segs.append((f" {'+' if c['price'] > 0 else ''}{c['price']}", ODDS_COLOR))
        if c["n"]:
            segs.append((f"  hit {c['hits']}/{c['n']}", GRAY))
        segs.append(DOT)
    return segs

def parlaylabs(loader=None, sports=None, now=None):
    """For each sport: {"sport", "record", "parlays", "props"} scroll segments from the ParlayLabs site.
    The phone picks which parts show."""
    loader, out = loader or get_json, []
    for sport, sub in (sports or PL_SPORTS).items():
        try:
            slips = list(loader(f"{PARLAYLABS}{sub}/picks.json")["slips"].values())
        except Exception:
            continue
        block = {"sport": sport, "record": [], "parlays": [], "overs": []}
        done = [s for s in slips if s.get("status") in ("won", "lost")]
        if done:
            won = [s for s in done if s["status"] == "won"]
            # every pick is a $10 reference bet: win pays 10*decimal odds, loss is -10
            profit = sum(10 * s["dec"] - 10 for s in won) - 10 * (len(done) - len(won))
            rec = block["record"]
            rec.append((f"{len(won)}-{len(done)-len(won)}  ", WHITE))
            rec.append((f"{'+' if profit >= 0 else '-'}${abs(profit):.0f}  ", GREEN if profit >= 0 else RED))
            for s in reversed(sorted(done, key=lambda s: s.get("graded", 0))[-3:]):
                hit = s["status"] == "won"
                rec.append((f"{s['name']} {'WON' if hit else 'LOST'}", GREEN if hit else RED))
                if hit:
                    rec.append((f" ${10*s['dec']:.0f}", GREEN))
                rec.append(DOT)
            live = [s for s in slips if s.get("status") == "open"]
            if live:
                rec.append((f"{len(live)} parlays live  ", (0, 180, 255)))
        block["parlays"] = top_parlays(slips, now=now)
        try:
            block["overs"] = over_candidates(loader(f"{PARLAYLABS}{sub}/data.json"),
                                             loader(f"{PARLAYLABS}{sub}/odds.json"), now=now)
        except Exception:
            block["overs"] = []
        if block["record"] or block["parlays"] or block["overs"]:
            out.append(block)
    return out

def pl_segments(blocks, ctl):
    """ParlayLabs part of the scroll, using the phone's picks: which parts, how many, which sports, which prop types."""
    segs = []
    for b in blocks:
        if ctl.pl_sports and b["sport"] not in ctl.pl_sports:
            continue
        body = []
        if "record" in ctl.pl_parts:
            body += b["record"]
        if "parlays" in ctl.pl_parts and b["parlays"]:
            body += [("TOP PARLAYS  ", (0, 180, 255), "sub")] + [sg for one in b["parlays"][:ctl.pl_count] for sg in one]
        if "props" in ctl.pl_parts:
            ov = top_overs(b["overs"], b["sport"], ctl.prop_types, ctl.pl_count)
            if ov:
                body += [("TOP OVERS  ", (0, 180, 255), "sub")] + ov
        if body:
            segs += [(f"  PARLAYLABS {b['sport']}  ", GOLD, "hdr")] + body
    return segs

# ------------------------------------------------------------------ alerts
class Alert:
    def __init__(self, kind, game, abbr, team):
        self.kind, self.game, self.abbr, self.at = kind, game, abbr, time.time()
        self.name, self.c1, self.c2 = team
        g = game
        self.score = f"{team(g, 'away')} {g['ascore']}  {team(g, 'home')} {g['hscore']}  {g['detail']}"
        self.words = {"td":    ["TOUCHDOWN!", f"{self.name}!"],
                      "win":   [f"{self.name}", "WIN!"],
                      "close": ["CLOSE GAME", self.name]}[kind]

class AlertWatcher:
    """Compares each refresh with the last one and queues alerts for my teams."""
    def __init__(self):
        self.prev, self.close_shown, self.queue = {}, set(), []

    def check(self, games):
        for g in games:
            abbr, team = mine(g)
            if not abbr:
                continue
            old = self.prev.get(g["id"])
            self.prev[g["id"]] = g
            if old is None:                       # first time we've seen it: no alert
                continue
            my_now = g["ascore"] if g["away"] == abbr else g["hscore"]
            my_old = old["ascore"] if old["away"] == abbr else old["hscore"]
            opp    = g["hscore"] if g["away"] == abbr else g["ascore"]
            # touchdown: football score jumped 6+ (TD, or TD + extra point between refreshes)
            if g["league"] in ("NFL", "CFB") and my_now - my_old >= 6:
                self.queue.append(Alert("td", g, abbr, team))
            # win: game just went final with my team ahead
            if old["state"] == "in" and g["state"] == "post" and my_now > opp:
                self.queue.append(Alert("win", g, abbr, team))
            # close game: late and within the margin; once per game, re-armed if it opens up
            is_late = late(g)
            if is_late and abs(my_now - opp) <= CLOSE[g["league"]]["margin"]:
                if g["id"] not in self.close_shown:
                    self.close_shown.add(g["id"])
                    self.queue.append(Alert("close", g, abbr, team))
            elif is_late:
                self.close_shown.discard(g["id"])

# ------------------------------------------------------------------ drawing
FONT = ImageFont.truetype(FONT_PATH, FONT_SIZE)
_fonts = {}
def font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(FONT_PATH, size)
    return _fonts[size]

def fit_font(text, max_w=W - 8, start=28):
    for size in range(start, 9, -1):
        if font(size).getlength(text) <= max_w:
            return font(size)
    return font(10)

def centered(d, text, f, color, cx=W // 2, cy=ROWS // 2):
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    d.text((cx - (r - l) // 2 - l, cy - (b - t) // 2 - t), text, font=f, fill=color)

def alert_frame(al, t, rng=random.Random(1)):
    """One frame of an alert, t seconds in. First 6 s: flashing words. Last 4 s: the score."""
    img = Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    amber = (255, 170, 0)
    if t < 6:
        flash = int(t * 4) % 2 == 0                      # 4 flashes a second
        word = al.words[int(t) % 2]                      # swap words every second
        bg, fg = (al.c1, WHITE) if flash else ((0, 0, 0), al.c1)
        if al.kind == "close":
            bg, fg = (amber, (0, 0, 0)) if flash else ((0, 0, 0), amber)
        d.rectangle([0, 0, W, ROWS], fill=bg)
        if al.kind == "win":                             # fireworks behind the words
            fireworks(d, t)
        centered(d, word, fit_font(word), fg)
    else:
        pulse = int(t * 2) % 2 == 0                      # slow border pulse
        d.rectangle([0, 0, W - 1, ROWS - 1], outline=al.c1 if pulse else al.c2, width=2)
        centered(d, al.score, fit_font(al.score, start=20), amber if al.kind == "close" else GOLD)
    return img

def game_frame(g, rotate=0, odds=("ou", "ml")):
    """A single game, held on screen: away on the left, home on the right, clock in the middle.
    Before kickoff: moneyline next to each team, start time and O/U (or spread) in the middle."""
    img = Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    pre = g["state"] == "pre"
    ml = pre and "ml" in odds and g.get("mla") and g.get("mlh")
    away, home = team(g, "away"), team(g, "home")
    left  = (f"{away} {g['mla']}" if ml else away) if pre else f"{away} {g['ascore']}"
    right = (f"{g['mlh']} {home}" if ml else home) if pre else f"{g['hscore']} {home}"
    mid1  = short_time(g["detail"]) if pre else g["detail"]
    mid2  = ""
    if pre:
        mid2 = (f"O/U {g['total']}" if "ou" in odds and g.get("total") else "") or \
               (g.get("spread") or "" if "spread" in odds else "")
    for size in range(24, 9, -1):                        # shrink until everything fits
        big, small = font(size), font(max(9, size // 2 + 1))
        parts = mid1.split(" ", 1) if " " in mid1 and not mid2 else [mid1]
        mid_w = max([small.getlength(x) for x in parts] + [small.getlength(mid2)])
        if big.getlength(left) + big.getlength(right) + mid_w + 16 <= W:
            break
    my = mine(g)[0]
    col = lambda team: GOLD if team == my else WHITE
    lead_a = not pre and g["ascore"] > g["hscore"]
    lead_h = not pre and g["hscore"] > g["ascore"]
    lw = big.getlength(left)
    centered(d, left,  big, col(g["away"]), cx=4 + lw // 2)
    centered(d, right, big, col(g["home"]), cx=W - 4 - big.getlength(right) // 2)
    if lead_a: d.rectangle([4, ROWS - 3, 4 + lw, ROWS - 2], fill=GREEN)
    if lead_h: d.rectangle([W - 4 - big.getlength(right), ROWS - 3, W - 4, ROWS - 2], fill=GREEN)
    live = g["state"] == "in"
    gap_l, gap_r = 4 + lw, W - 4 - big.getlength(right)
    mx = int((gap_l + gap_r) / 2)                        # middle of the space between the teams
    gap = gap_r - gap_l - 6
    if mid2:
        centered(d, mid1, small, ODDS_COLOR, cx=mx, cy=9)
        centered(d, mid2, small, ODDS_COLOR, cx=mx, cy=23)
    else:
        parts = mid1.split(" ", 1) if small.getlength(mid1) > gap and " " in mid1 else [mid1]
        for i, p in enumerate(parts):
            cy = ROWS // 2 if len(parts) == 1 else (9 if i == 0 else 23)
            centered(d, p, small, RED if live else GRAY, cx=mx, cy=cy)
    if rotate:                                           # thin bar along the top = time until the next game
        d.line([(0, 0), (int(W * (time.time() % rotate) / rotate), 0)], fill=(50, 50, 80))
    return img

# ---- message effects -----------------------------------------------------------
FIREWORK_COLORS = [(255, 60, 60), (255, 190, 0), (0, 200, 255), (0, 230, 80), (255, 80, 220), (255, 255, 255)]

def blink_off(t):
    return t % 0.6 > 0.42                                   # on 0.42 s, off 0.18 s

def rainbow(i, t=0.0):
    r, g, b = colorsys.hsv_to_rgb(((i * 25 + t * 140) % 360) / 360, 1, 1)
    return int(r * 255), int(g * 255), int(b * 255)

def fireworks(d, t, x0=0, x1=W):
    """Bursts of sparks between x0 and x1. Each burst is worked out from the clock, so nothing to keep track of."""
    period, life = 0.2, 1.2
    x0, x1 = max(0, x0), min(W, x1)
    if x1 - x0 < 20:
        return
    for n in range(int((t - life) / period), int(t / period) + 1):
        age = t - n * period
        if not 0 <= age <= life:
            continue
        r = random.Random(n * 7919)
        cx, cy = r.uniform(x0 + 6, x1 - 6), r.uniform(4, ROWS - 6)
        base = r.choice(FIREWORK_COLORS)
        fade = 1 - (age / life) ** 2                        # stays bright, then drops off
        col = tuple(int(c * fade) for c in base)
        if age < 0.1:                                       # the pop
            d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(255, 255, 255))
            continue
        size = 2 if age < 0.7 else 1
        for k in range(22):
            a = 2 * math.pi * k / 22 + n
            dist = 34 * age * (0.7 + 0.3 * ((k * 37 + n) % 5) / 4)
            px = cx + math.cos(a) * dist
            py = cy + math.sin(a) * dist * 0.6 + 9 * age * age   # flatter for the short strip, then fall
            if -1 <= px < W and -1 <= py < ROWS:
                px, py = int(px), int(py)
                d.rectangle([px, py, px + size - 1, py + size - 1], fill=col)

def draw_fx_text(d, xy, text, f, color, effect, t, still_rainbow=False):
    """Text with the chosen style. Returns its width."""
    w = f.getlength(text)
    if effect == "blink" and blink_off(t):
        return w
    outline = dict(stroke_width=1, stroke_fill=(0, 0, 0)) if effect == "fireworks" else {}
    if effect == "rainbow":
        x, y = xy
        for i, ch in enumerate(text):
            d.text((x, y), ch, font=f, fill=rainbow(i, 0 if still_rainbow else t))
            x += f.getlength(ch)
    else:
        d.text(xy, text, font=f, fill=color, **outline)
    return w

def text_strip(segs, stamp=True, hide_msg=False):
    """Draws segments onto one long image; we scroll a window across it.
    A segment can be (text, color) or (text, color, "msg") for your message. Where the message
    sits is kept in img.info["spans"] so effects can find it while it scrolls by."""
    if stamp:
        segs = [(f"  {datetime.now():%-I:%M}  ", (0, 180, 255))] + (segs or [("No games right now", GRAY)])
    width = sum(int(FONT.getlength(sg[0])) for sg in segs) + 4
    img = Image.new("RGB", (max(width, 1), ROWS))
    d = ImageDraw.Draw(img)
    x, y, spans = 0, (ROWS - FONT_SIZE) // 2 - 2, []
    for sg in segs:
        text, color = sg[0], sg[1]
        is_msg = len(sg) > 2 and sg[2] == "msg"
        w = int(FONT.getlength(text))
        if is_msg:
            spans.append((x, x + w))
        if not (is_msg and hide_msg):
            if color == "rainbow":
                draw_fx_text(d, (x, y), text, FONT, None, "rainbow", 0, still_rainbow=True)
            else:
                d.text((x, y), text, font=FONT, fill=color)
        x += w
    img.info["spans"] = spans
    return img

def message_frame(ctl, t, x):
    """Your message on its own, in its style. Returns (frame, scrolling?, width)."""
    txt, col, fx = (ctl.message or " ").strip() or " ", hex_rgb(ctl.color), ctl.effect
    img = Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    f = fit_font(txt, start=26)
    scrolling = fx == "scroll" or f.size < 16
    if scrolling:
        f = FONT
    if fx == "fireworks":
        fireworks(d, t)
    l, top, r, b = d.textbbox((0, 0), txt, font=f)
    y = (ROWS - (b - top)) // 2 - top
    px = x if scrolling else (W - (r - l)) // 2 - l
    w = draw_fx_text(d, (px, y), txt, f, col, fx, t)
    return img, scrolling, w

# ---- one-at-a-time cards (Fade / Flash / Slide up) --------------------------------
def make_cards(segs):
    """Splits the scroll into cards: a small label on top (league, time slot, ParlayLabs part)
    and one game, parlay, prop or message underneath."""
    cards, label, sub, cur = [], None, None, []
    def flush():
        nonlocal cur
        body = [sg for sg in cur if sg[0].strip()]
        if body:
            lab = label if not sub else (f"{label[0]} · {sub[0]}" if label else sub[0], sub[1])
            cards.append({"label": lab, "segs": body, "msg": False})
        cur = []
    cards.append({"label": None, "segs": [(datetime.now().strftime("%-I:%M"), (0, 180, 255))], "msg": False})
    for sg in segs:
        tag = sg[2] if len(sg) > 2 else None
        if tag == "hdr":
            flush(); label, sub = (sg[0].strip(), sg[1]), None
        elif tag == "sub":
            flush(); sub = (sg[0].strip(), sg[1])
        elif tag == "msg":
            flush(); cards.append({"label": None, "segs": [(sg[0].strip(), sg[1])], "msg": True})
        elif sg == DOT or sg[0].strip() == "•":
            flush()
        else:
            cur.append(sg)
    flush()
    return cards

LABEL_FONT = 10

def card_size(card):
    """Main-line font size that fits, or None if it has to crawl across."""
    txt = "".join(sg[0] for sg in card["segs"]).strip()
    start = 24 if card["label"] is None else 19
    for size in range(start, 10, -1):
        if font(size).getlength(txt) <= W - 6:
            return size
    return None

def card_length(card, ctl):
    size = card_size(card)
    if size:
        return ctl.hold
    txt = "".join(sg[0] for sg in card["segs"]).strip()
    return max(ctl.hold, (W + FONT.getlength(txt)) / ctl.speed)

def card_frame(card, t, ctl):
    """Draws one card, t seconds after it came up."""
    img = Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    size = card_size(card)
    f = font(size) if size else FONT
    texts = [(sg[0], sg[1]) for sg in card["segs"]]
    texts[0] = (texts[0][0].lstrip(), texts[0][1]); texts[-1] = (texts[-1][0].rstrip(), texts[-1][1])
    width = sum(f.getlength(tx) for tx, _ in texts)
    if card["label"]:
        lt, lc = card["label"]
        d.text((3, 0), lt.upper(), font=font(LABEL_FONT), fill=lc)
        top, h = 11, ROWS - 11
    else:
        top, h = 0, ROWS
    fx = ctl.effect if card["msg"] else "steady"
    if fx == "fireworks":
        fireworks(d, t)
    x = (W - width) / 2 if size else W - t * ctl.speed
    l, tt, r, b = d.textbbox((0, 0), "Ag", font=f)
    y = top + (h - (b - tt)) // 2 - tt
    for tx, col in texts:
        if col == "rainbow":
            draw_fx_text(d, (x, y), tx, f, None, "rainbow", t)
        else:
            draw_fx_text(d, (x, y), tx, f, col, fx, t)
        x += f.getlength(tx)
    return img

def with_motion(img, t, length, motion):
    """Fade / Flash / Slide-up / Slide-left in and out around a card or game."""
    tr = 0.3
    if motion == "push":                                 # comes in from the right, leaves to the left
        tr = 0.4
        if t < tr:
            dx = int(W * (1 - t / tr))
        elif t > length - tr:
            dx = -int(W * (t - (length - tr)) / tr)
        else:
            return img
        out = Image.new("RGB", (W, ROWS)); out.paste(img, (dx, 0))
        return out
    if motion == "fade":
        k = max(0.0, min(1.0, t / tr, (length - t) / tr))
        return ImageEnhance.Brightness(img).enhance(k) if k < 1 else img
    if motion == "flash":
        if t < 0.45 and int(t / 0.075) % 2:              # blinks in 3 times
            return Image.new("RGB", (W, ROWS))
        return img
    if motion == "slide":
        if t < tr:
            dy = int(ROWS * (1 - t / tr))
        elif t > length - tr:
            dy = -int(ROWS * (t - (length - tr)) / tr)
        else:
            return img
        out = Image.new("RGB", (W, ROWS)); out.paste(img, (0, dy))
        return out
    return img

# ------------------------------------------------------------------ feed
class Feed:
    """Pulls data in a background thread so the display never stutters."""
    def __init__(self, ctl, demo=False):
        self.ctl, self.demo, self.pl, self.games = ctl, demo, [], []
        self.last_ok, self.fails, self.last_error = time.time(), 0, ""
        self.last_state, self.ended = {}, {}       # to know when a game just finished
        self.force = False                         # set by the refresh button
        self.next_games = {}                       # my teams' next games when they're not on today's board
        self.strip = text_strip([("Loading…", GRAY)])
        self.strip_off = self.strip_mask = None
        self.cards = []
        self.watcher = AlertWatcher()
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        """Hot leagues (my team or the pinned game live) every FAST_SEC, everything else every ctl.refresh."""
        by_league, last, last_pl, last_next = {}, {}, 0, 0
        while True:
            now = time.time()
            if self.force:                               # refresh button: skip the waits this time
                self.force, last, last_pl, last_next = False, {}, 0, 0
            if self.demo:
                self.games, self.pl = DEMO_GAMES, demo_parlaylabs()
            else:
                hot = self.hot_leagues()
                for label, path, _ in LEAGUES:
                    wait = min(FAST_SEC, self.ctl.refresh) if label in hot else self.ctl.refresh
                    if now - last.get(label, 0) >= wait:
                        try:
                            by_league[label] = fetch_league(label, path)
                            self.last_ok, self.fails = time.time(), 0
                        except Exception as e:
                            self.fails += 1
                            self.last_error = str(e)
                        last[label] = now
                self.games = [g for l, _, _ in LEAGUES for g in by_league.get(l, [])]
                self.watcher.check(self.games)
                self.note_finishes()
                if now - last_pl > PL_REFRESH:    # ParlayLabs files are big (~8 MB) and change slowly
                    self.pl, last_pl = parlaylabs() or self.pl, now
                if now - last_next > NEXT_REFRESH:
                    self.update_next_games(); last_next = now
            self.build_strip()
            time.sleep(1)

    def build_strip(self):
        ctl = self.ctl
        only = ctl.leagues                       # leagues picked on the phone (empty = everything)
        pl = pl_segments(self.pl, ctl) if not only or "PL" in only else []
        build = time_segments if ctl.group == "time" else score_segments
        segs = pl + build(self.games, only, ctl.show_odds(), ctl.days)
        if ctl.msg_in_scroll and ctl.message.strip():
            # your message comes around before each league (and before ParlayLabs)
            col = "rainbow" if ctl.effect == "rainbow" else hex_rgb(ctl.color)
            note = [(f"   {ctl.message.strip()}   ", col, "msg"), DOT]
            mixed = []
            for seg in segs:
                if len(seg) > 2 and seg[2] == "hdr":
                    mixed += note
                mixed.append(seg)
            segs = mixed or note
        self.strip = text_strip(segs)
        self.cards = make_cards(segs)
        # blink: a copy with the message blanked out, swapped in for the "off" moments
        self.strip_off = text_strip(segs, hide_msg=True) if ctl.msg_in_scroll and ctl.effect == "blink" else None
        # fireworks: which pixels are text, so sparks can go behind the words
        self.strip_mask = (self.strip.convert("L").point(lambda v: 255 if v > 0 else 0)
                           if ctl.msg_in_scroll and ctl.effect == "fireworks" else None)

    def note_finishes(self):
        now = time.time()
        for g in self.games:
            if g["state"] == "post" and self.last_state.get(g["id"]) == "in":
                self.ended[g["id"]] = now
            self.last_state[g["id"]] = g["state"]

    def linger_left(self, g):
        """Seconds a just-finished game stays on with Live games (0 if not)."""
        if g["state"] != "post" or g["id"] not in self.ended:
            return 0
        return max(0, int(LINGER_SEC - (time.time() - self.ended[g["id"]])))

    def hot_leagues(self):
        ids = self.ctl.game_ids
        follow_all = CLOSE_GAMES in ids or LIVE_GAMES in ids                       # every live league is hot
        follow = set()                                                            # or just these leagues
        for i in ids:
            if i.startswith((LIVE_GAMES + ":", CLOSE_GAMES + ":")):
                follow |= set(i.split(":", 1)[1].split(","))
        return {g["league"] for g in self.games
                if g["state"] == "in" and (mine(g)[0] or g["id"] in ids or follow_all or g["league"] in follow)}

    def picked(self):
        """Games chosen on the phone. "Close games" expands to every close game right now; "Live games"
        to every game in progress plus ones that ended in the last 15 minutes."""
        out = []
        for gid in self.ctl.game_ids:
            base, _, lgs = gid.partition(":")             # "@live:NFL,CFB" -> just those leagues
            lgs = set(lgs.split(",")) if lgs else None
            ok = lambda g: not lgs or g["league"] in lgs
            if base == CLOSE_GAMES:
                out += [g for g in self.games if is_close(g) and ok(g) and g not in out]
            elif base == LIVE_GAMES:
                out += [g for g in self.games if g not in out and ok(g)
                        and (g["state"] == "in" or self.linger_left(g))]
            elif gid == MY_GAMES:
                out += [g for g in self.my_games() if g not in out]
            elif (g := self.game(gid)) and g not in out:
                out.append(g)
        return out

    def my_games(self):
        """One game for every one of my teams, always: the game it's playing now (or today, or that just
        ended), otherwise its next game with the day, date, time and odds once they're posted."""
        out = []
        for (league, abbr) in MY_TEAMS:
            theirs = [g for g in self.games if g["league"] == league and abbr in (g["away"], g["home"])]
            now = [g for g in theirs if g["state"] == "in" or self.linger_left(g)
                   or (g["state"] == "pre" and is_today(g))]
            upcoming = sorted((g for g in theirs if g["state"] == "pre"), key=lambda g: g.get("start") or 0)
            g = (now or upcoming or [self.next_games.get((league, abbr))])[0]
            if g and g not in out:
                if g["state"] == "pre" and not is_today(g) and g.get("start"):
                    g = dict(g, detail=date_label(g["start"]))   # show the date for later games
                out.append(g)
        order = {"in": 0, "post": 1, "pre": 2}
        return sorted(out, key=lambda g: (order[g["state"]], g.get("start") or 0))

    def update_next_games(self):
        """Looks up the next game for any of my teams that isn't on the current board."""
        for key, tid in TEAM_IDS.items():
            league, abbr = key
            if any(g["league"] == league and abbr in (g["away"], g["home"]) and g["state"] != "post"
                   for g in self.games):
                continue
            try:
                g = fetch_next_game(league, tid)
                if g:
                    self.next_games[key] = g
            except Exception:
                pass

    def game(self, gid):
        return next((g for g in self.games + list(self.next_games.values()) if g["id"] == gid), None)

    def next_alert(self):
        q = self.watcher.queue
        if not self.ctl.alerts:
            q.clear()
            return None
        if q and time.time() - q[0].at >= self.ctl.tv_delay:   # hold it to line up with the TV
            return q.pop(0)
        return None

def demo_parlaylabs():
    """Uses the sample ParlayLabs files in demo/ (a snapshot of the site) when testing without internet."""
    def load(url):
        return json.load(open(os.path.join(HERE, "demo", url.rsplit("/", 1)[-1])))
    try:
        return parlaylabs(loader=load, sports={"NFL": ""}, now=0)   # snapshot: ignore start times
    except Exception:
        return []

def fake(league, away, home, a, h, state, detail, spread=None, total=None, period=4, clock=180, mla=None, mlh=None,
         hours=0, arank=None, hrank=None):
    return dict(id=f"{league}-{away}{home}", league=league, away=away, home=home, ascore=a, hscore=h,
                state=state, detail=detail, period=period, clock=clock, ranked=True, spread=spread, total=total,
                mla=mla, mlh=mlh, start=_demo_start(hours), arank=arank, hrank=hrank)

def _demo_start(hours):
    """Sample start times: on the hour, `hours` from now."""
    t = time.time() + hours * 3600
    return t - t % 1800

DEMO_GAMES = [
    fake("NFL", "MIN", "DET", 17, 24, "in", "4th 3:12", hours=-3),
    fake("NFL", "CHI", "GB", 10, 14, "in", "2nd 0:45", period=2, hours=-1),
    fake("NFL", "KC", "BUF", 0, 0, "pre", "Sun 4:25 PM EST", "BUF -2.5", 51.5, mla="+120", mlh="-142", hours=26),
    fake("NFL", "PHI", "DAL", 0, 0, "pre", "Sun 4:25 PM EST", "DAL +3", 47.5, mla="-150", mlh="+128", hours=26),
    fake("NFL", "NYJ", "MIA", 20, 23, "post", "Final", hours=-5),
    fake("NBA", "DET", "BOS", 0, 0, "pre", "7:30 PM EST", "BOS -6.5", 224.5, mla="+225", mlh="-275", hours=4),
    fake("CFB", "MSU", "IOWA", 21, 17, "in", "3rd 9:58", period=3, hours=-2, hrank=22),
    fake("CBB", "MSU", "DUKE", 0, 0, "pre", "Tue 9:00 PM EST", "DUKE -3", 148.5, mla="+130", mlh="-155", hours=72, arank=15, hrank=6),
]

# ------------------------------------------------------------------ control page / API
STATIC_FILES = {"icon-32.png": "image/png", "icon-180.png": "image/png", "icon-192.png": "image/png",
                "icon-512.png": "image/png", "manifest.json": "application/manifest+json"}

def make_handler(ctl, feed):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self.send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
            elif path.lstrip("/") in STATIC_FILES:                # icon + home-screen bookmark files
                name = path.lstrip("/")
                self.send(200, open(os.path.join(HERE, name), "rb").read(), STATIC_FILES[name])
            elif path == "/api/state":
                games = [dict(g, mine=bool(mine(g)[0]), close=is_close(g), linger=feed.linger_left(g))
                         for g in feed.games]
                ids = {g["id"] for g in games}
                games += [dict(g, mine=True, close=False, linger=0) for g in feed.my_games()
                          if g["id"] not in ids]    # my teams' next games further out
                stale = time.time() - feed.last_ok           # nothing from ESPN for a while = likely blocked
                feed_ok = feed.demo or stale < max(90, 6 * ctl.refresh)
                self.send(200, dict(ctl.as_dict(), games=games, has_image=bool(ctl.frames),
                                    feed_ok=feed_ok, feed_error=feed.last_error,
                                    last_ok=int(stale)))
            elif path == "/api/frame.png":
                buf = io.BytesIO(); ctl.last_frame.save(buf, "PNG")
                self.send(200, buf.getvalue(), "image/png")
            else:
                self.send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0]
            n = int(self.headers.get("Content-Length") or 0)
            if n > 15_000_000:
                return self.send(413, {"error": "file too big (15 MB max)"})
            body = self.rfile.read(n)
            try:
                if path == "/api/state":
                    ctl.update(json.loads(body or b"{}"))
                elif path == "/api/refresh":                 # the phone's refresh button: pull everything now
                    feed.force = True
                elif path == "/api/image":
                    fit = "fill" if "fit=fill" in self.path else "contain"
                    ctl.set_image(body, fit)
                else:
                    return self.send(404, {"error": "not found"})
            except Exception as e:
                return self.send(400, {"error": str(e)})
            self.send(200, ctl.as_dict())
    return H

def start_web(ctl, feed, port):
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(ctl, feed))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"control page on port {port}")

# ------------------------------------------------------------------ display loop
class SimMatrix:
    """Stands in for the LED panels when testing on a computer."""
    brightness = 60
    def CreateFrameCanvas(self): return self
    def SetImage(self, img, x=0, y=0): self.img, self.x = img, x
    def Clear(self): pass
    def SwapOnVSync(self, c): return c

def open_matrix(ctl):
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
    o = RGBMatrixOptions()
    o.rows, o.cols, o.chain_length = ROWS, COLS, PANELS
    o.hardware_mapping = "adafruit-hat"   # Adafruit RGB Matrix Bonnet
    o.brightness = ctl.brightness
    o.gpio_slowdown = 4                   # Pi 4 needs this; stops flicker
    o.drop_privileges = False
    return RGBMatrix(options=o)

def run_display(ctl, feed, matrix):
    canvas = matrix.CreateFrameCanvas()
    frame = Image.new("RGB", (W, ROWS))

    def show(img, x=0):
        nonlocal canvas
        if matrix.brightness != ctl.brightness:
            matrix.brightness = ctl.brightness
        frame.paste((0, 0, 0), (0, 0, W, ROWS))
        frame.paste(img, (x, 0))
        canvas.SetImage(frame)
        canvas = matrix.SwapOnVSync(canvas)
        ctl.last_frame = frame.copy() if int(time.time() * 4) % 2 == 0 else ctl.last_frame

    x, seen, img_i = W, -1, 0
    card_i, card_t0 = 0, time.time()
    gx, gstrip, gkey = W, None, None              # the picked games as one crawling strip (Scroll style)
    while True:
        al = feed.next_alert()
        if al:                                    # takeover, then go back to whatever was showing
            start = time.time()
            while (t := time.time() - start) < ALERT_SECONDS:
                show(alert_frame(al, t)); time.sleep(1 / 30)
            x = W
        if ctl.version != seen:                   # something changed on the phone
            seen, x, img_i = ctl.version, W, 0
            card_i, card_t0 = 0, time.time()
            feed.build_strip()                    # league picks may have changed

        mode = ctl.mode
        picked = feed.picked() if mode == "game" else []
        if picked and ctl.game_motion == "scroll":  # picked games crawl across like a mini ticker
            lg_color = {l: c for l, _, c in LEAGUES}
            segs = []
            for g in picked:
                segs += [(f"{g['league']} ", lg_color[g["league"]])] + game_text(g, ctl.show_odds()) + [DOT]
            key = tuple(sg[0] for sg in segs)
            if key != gkey:                       # scores changed: redraw the strip, keep its place
                gstrip, gkey = text_strip(segs, stamp=False), key
            show(gstrip, gx)
            step = 1 if ctl.game_speed <= 50 else 2
            gx -= step
            if gx < -gstrip.width:
                gx = W
            time.sleep(step / ctl.game_speed)
        elif picked:                              # one game holds; several take turns
            now = time.time()
            g = picked[int(now // ctl.rotate) % len(picked)]
            img = game_frame(g, ctl.rotate if len(picked) > 1 else 0, ctl.show_odds())
            if len(picked) > 1 and ctl.game_motion != "cut":
                img = with_motion(img, now % ctl.rotate, ctl.rotate, ctl.game_motion)
                show(img); time.sleep(1 / 40)     # smooth while changing games
            else:
                show(img); time.sleep(0.25)
        elif mode == "image" and ctl.frames:
            img, ms = ctl.frames[img_i % len(ctl.frames)]
            show(img); img_i += 1; time.sleep(ms / 1000)
        elif mode == "message":
            img, scrolling, w = message_frame(ctl, time.time(), x)
            show(img)
            step = 1 if ctl.speed <= 50 else 2
            if scrolling:
                x = W if x < -w else x - step
            time.sleep(step / ctl.speed)
        elif ctl.motion != "scroll" and feed.cards:   # one item at a time
            cards = feed.cards
            card = cards[card_i % len(cards)]
            length, t = card_length(card, ctl), time.time() - card_t0
            if t >= length:
                card_i, card_t0 = (card_i + 1) % len(cards), time.time()
                continue
            show(with_motion(card_frame(card, t, ctl), t, length, ctl.motion))
            time.sleep(1 / 40)
        else:                                     # scroll everything
            strip, t = feed.strip, time.time()
            if feed.strip_off is not None and blink_off(t) and feed.strip_off.width == strip.width:
                strip = feed.strip_off
            mask = feed.strip_mask
            on_screen = [(a + x, b + x) for a, b in strip.info.get("spans", []) if a + x < W and b + x > 0]
            if mask is not None and on_screen and mask.size == strip.size:
                layer = Image.new("RGB", (W, ROWS))
                dl = ImageDraw.Draw(layer)
                for a, b in on_screen:
                    fireworks(dl, t, a, b)
                layer.paste(strip, (x, 0), mask)
                show(layer)
            else:
                show(strip, x)
            step = 1 if ctl.speed <= 50 else 2
            x -= step
            if x < -strip.width:
                x = W
            time.sleep(step / ctl.speed)

# ------------------------------------------------------------------ test helpers
def save_gif(frames, name, ms):
    big = [f.resize((W * 3, ROWS * 3), Image.NEAREST) for f in frames]
    big[0].save(name, save_all=True, append_images=big[1:], duration=ms, loop=0)
    print("wrote", name)

def run_preview(feed, seconds=45):
    time.sleep(1)
    strip, frames = feed.strip, []
    for x in range(W, W - SCROLL_SPEED * seconds, -2):
        f = Image.new("RGB", (W, ROWS)); f.paste(strip, (x, 0)); frames.append(f)
    save_gif(frames, "preview.gif", 50)

def run_test_alert(kind):
    games = {"td":    fake("NFL", "MIN", "DET", 17, 24, "in", "4th 3:12"),
             "win":   fake("NFL", "MIN", "DET", 17, 31, "post", "Final"),
             "close": fake("NBA", "DET", "BOS", 101, 99, "in", "4th 2:31")}
    g = games[kind]
    al = Alert(kind, g, "DET", MY_TEAMS[(g["league"], "DET")])
    save_gif([alert_frame(al, i / 20) for i in range(ALERT_SECONDS * 20)], f"alert-{kind}.gif", 50)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="no LED panels: run the control page and a pretend display")
    ap.add_argument("--demo", action="store_true", help="use local picks.json + fake games")
    ap.add_argument("--port", type=int, help="control page port (default 80, or 8080 with --sim)")
    ap.add_argument("--preview", action="store_true", help="write preview.gif")
    ap.add_argument("--test-alert", choices=["td", "win", "close"], help="write an alert GIF")
    args = ap.parse_args()
    if args.test_alert:
        run_test_alert(args.test_alert)
    else:
        ctl = Control()
        feed = Feed(ctl, demo=args.demo or args.preview)
        if args.preview:
            run_preview(feed)
        else:
            start_web(ctl, feed, args.port or (8080 if args.sim else WEB_PORT))
            run_display(ctl, feed, SimMatrix() if args.sim else open_matrix(ctl))

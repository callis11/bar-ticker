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
import argparse, colorsys, io, json, math, os, random, tempfile, time, threading, urllib.parse, urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageSequence
import pictures

HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------ settings
PANELS        = 5          # 128x64 P2.5 panels chained left-to-right (5 x 320 mm = ~5.25 ft)
ROWS, COLS    = 64, 128
W             = COLS * PANELS   # 640 x 64 pixels
K             = ROWS // 32      # drawing scale (sizes below were first worked out for a 32-tall strip)
SCROLL_SPEED  = 80         # pixels per second (P2.5 pixels are small: 80 px/s is a relaxed crawl)
REFRESH_SEC   = 5          # default refresh for the whole board (changeable on the phone page, 5-60 s)
FAST_SEC      = 5          # leagues where my team (or the game you picked) is live: always this fast
ALERT_SECONDS = 10         # how long an alert takes over the ticker
WEB_PORT      = 80         # control page: http://ticker.local (set hostname "ticker" in Pi Imager)
BONNET        = "triple"   # "triple" = Adafruit Triple LED Matrix Bonnet #6358, "single" = RGB Matrix Bonnet #3211
# Which Bonnet output drives each panel, left to right as you face the ticker:
#   (output 1-3, place in that output's chain starting at 0, mounted upside down?)
# Split over the Triple Bonnet's 3 outputs, each output only feeds 1-2 panels, so the picture refreshes
# ~2.5x faster than one long chain of 5 (smoother, no flicker on phone cameras).
# Run `sudo python3 ticker.py --test-panels` once it's built: each panel shows its number, so a mix-up is easy to fix here.
PANEL_MAP     = [(1, 0, False), (1, 1, False),      # output 1 -> panels 1 and 2
                 (2, 0, False), (2, 1, False),      # output 2 -> panels 3 and 4
                 (3, 0, False)]                     # output 3 -> panel 5
if BONNET == "single":                              # one output: all 5 in a row
    PANEL_MAP = [(1, i, False) for i in range(PANELS)]
FONT_PATH     = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_SIZE     = 36         # one-line text (headers, ParlayLabs, messages)
LINE_SIZE     = 25         # each line of a two-line game (away team on top, home underneath)
SETTINGS_FILE = os.path.join(HERE, "settings.json")
IMAGE_FILE    = os.path.join(HERE, "custom_image")      # the single picture from before the library (imported once)
PIC_DIR       = os.path.join(HERE, "pictures")          # your picture library (pictures, GIFs, videos)
PIC_EVERY     = [0, -1, 2, 5, 10]                       # pictures in the ticker: off / after each lap / every N minutes

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
PIN_CHOICES = ["off", "mine", "live", "picked"]   # split screen: what stays on the top row
TOP_H      = ROWS // 2 - 1                       # top row height when split (then a divider line, then the bottom row)
BOTTOM_H   = ROWS // 2
MOTIONS = ["scroll", "cut", "fade", "flash", "slide", "push"]   # crawl, or one item at a time
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
            "game_speed": SCROLL_SPEED, "msg_on_image": False, "pin": "off",
            # pictures: which ones (empty = all), one played on its own, seconds each, order, change style,
            # how often one pops up in the ticker, LED color boost
            "pic_ids": [], "pic_solo": "", "pic_secs": 10, "pic_order": "inorder", "pic_fx": "fade",
            "pic_every": 0, "pic_boost": True}

    def __init__(self):
        self.__dict__.update(self.KEYS)
        self.lib = pictures.Library(PIC_DIR, W, ROWS)
        self.last_frame = Image.new("RGB", (W, ROWS))
        self.version = 0            # bumps on every change so the display redraws
        try:
            self.update(json.load(open(SETTINGS_FILE)), save=False)
        except (OSError, ValueError):
            pass
        if os.path.exists(IMAGE_FILE):                 # picture from before the library: becomes its first entry
            try:
                it = self.lib.import_legacy(IMAGE_FILE, self.fit)
                if self.mode == "image":
                    self.pic_solo = it["id"]
            except Exception:
                pass
        if self.mode == "image" and not self.lib.ready_ids():
            self.mode = "scroll"

    def update(self, d, save=True):
        if d.get("game_id") and "game_ids" not in d:      # older single-game setting
            d = dict(d, game_ids=[d["game_id"]])
        for k, v in d.items():
            if k in self.KEYS:
                setattr(self, k, v)
        self.game_ids = [str(i) for i in (self.game_ids or [])][:40]
        self.rotate = max(3, min(60, int(self.rotate)))
        if self.mode == "game":                   # Scroll all + Games are one Ticker now
            self.mode = "scroll"
        self.leagues = [l for l in (self.leagues or []) if l in SCROLL_CHOICES and l != "PL"]  # sports; empty = all
        self.odds = [o for o in (self.odds or []) if o in ("ou", "ml", "spread")]
        self.msg_in_scroll = bool(self.msg_in_scroll)
        self.odds_on = bool(self.odds_on)
        self.msg_on_image = bool(self.msg_on_image)
        self.group = "time" if self.group == "time" else "league"
        self.days = "today" if self.days == "today" else "week"
        self.pl_parts = [x for x in (self.pl_parts or []) if x in ("record", "parlays", "props")]
        self.speed = max(20, min(200, int(self.speed)))
        self.motion = self.motion if self.motion in MOTIONS else "scroll"
        self.hold = max(2, min(15, int(self.hold)))
        self.ranks = bool(self.ranks)
        self.pin = self.pin if self.pin in PIN_CHOICES else "off"
        self.pic_ids = [str(i) for i in (self.pic_ids or [])][:200]
        self.pic_solo = str(self.pic_solo or "")
        self.pic_secs = max(3, min(120, int(self.pic_secs)))
        self.pic_order = "shuffle" if self.pic_order == "shuffle" else "inorder"
        self.pic_fx = self.pic_fx if self.pic_fx in ("fade", "slide", "cut") else "fade"
        self.pic_every = int(self.pic_every) if int(self.pic_every) in PIC_EVERY else 0
        self.pic_boost = bool(self.pic_boost)
        SHOW_RANKS["on"] = self.ranks
        self.game_motion = self.game_motion if self.game_motion in ("cut", "fade", "flash", "slide", "push", "scroll") else "slide"
        self.game_speed = max(20, min(200, int(self.game_speed)))
        self.pl_count = max(1, min(10, int(self.pl_count)))
        self.pl_sports = [x for x in (self.pl_sports or []) if x in PL_SPORTS]      # empty = all sports
        self.prop_types = [x for x in (self.prop_types or []) if x in PROP_TYPES]  # empty = all types
        if self.effect not in EFFECTS:
            self.effect = "steady"
        self.brightness = max(5, min(100, int(self.brightness)))
        self.tv_delay = max(0, min(120, int(self.tv_delay)))
        self.refresh = max(5, min(60, int(self.refresh)))
        self.version += 1
        if save:
            json.dump({k: getattr(self, k) for k in self.KEYS}, open(SETTINGS_FILE, "w"))

    def show_odds(self):
        """The odds to show right now: the picks, unless the master switch is off."""
        return self.odds if self.odds_on else []

    def playlist(self):
        """Pictures to show: the one you're playing on its own, else the ones you checked (none checked = all)."""
        ready = self.lib.ready_ids()
        if self.pic_solo in ready:
            return [self.pic_solo]
        return [i for i in self.pic_ids if i in ready] or ready

    def as_dict(self):
        return {k: getattr(self, k) for k in self.KEYS}

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
    sit = comp.get("situation") or {}               # football, while it's live: down, distance, ball
    poss = str(sit.get("possession") or "")
    ids = lambda c: {str(c.get("id") or ""), str((c.get("team") or {}).get("id") or "")} - {""}
    return dict(
        down=sit.get("shortDownDistanceText") or "", spot=sit.get("possessionText") or "",
        redzone=bool(sit.get("isRedZone")),
        poss="away" if poss and poss in ids(a) else "home" if poss and poss in ids(h) else "",
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

def game_seg(g, odds):
    """One game in the scroll: drawn as a two-line block (see game_block)."""
    return ("", None, "game", g, tuple(odds))

def visible_games(games, only=None, days="week", raw=False):
    if raw:                                       # games you picked on the phone: show them as they are
        return list(games)
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

def time_segments(games, only=None, odds=("ou", "ml"), days="week", raw=False):
    """Every game grouped by when it plays: LIVE first, then each start time in order, then FINAL.
    Each game gets a small league tag since leagues are mixed together."""
    lg_color = {l: c for l, _, c in LEAGUES}
    games = visible_games(games, only, days, raw)
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
            segs.append(game_seg(g, odds))
            segs.append(DOT)
    return segs

def score_segments(games, only=None, odds=("ou", "ml"), days="week", raw=False):
    """Scrolling text for every game: live/final scores, and odds for games not started."""
    segs = []
    for label, _, color in LEAGUES:
        if only and label not in only:
            continue
        lg = [g for g in visible_games(games, None, days, raw) if g["league"] == label]
        lg.sort(key=lambda g: {"in": 0, "pre": 1, "post": 2}[g["state"]])   # live games first
        if not lg:
            continue
        segs.append((f"  {label}  ", color, "hdr"))
        for g in lg:
            segs.append(game_seg(g, odds))
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
    def __init__(self, kind, game, abbr, info):
        self.kind, self.game, self.abbr, self.at = kind, game, abbr, time.time()
        self.name, self.c1, self.c2 = info
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

def fit_font(text, max_w=W - 8, start=28 * K):
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
    else:                                                # the score, big, with a pulsing team-color border
        img = game_frame(al.game)
        d = ImageDraw.Draw(img)
        pulse = int(t * 2) % 2 == 0
        d.rectangle([0, 0, W - 1, ROWS - 1], outline=al.c1 if pulse else al.c2, width=2 * K)
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
    mid2  = downs(g)                                     # live football: the down under the clock
    if pre:
        mid2 = (f"O/U {g['total']}" if "ou" in odds and g.get("total") else "") or \
               (g.get("spread") or "" if "spread" in odds else "")
    for size in range(24 * K, 9, -1):                    # shrink until everything fits
        big, small = font(size), font(max(9, size // 2 + 1))
        parts = mid1.split(" ", 1) if " " in mid1 and not mid2 else [mid1]
        mid_w = max([small.getlength(x) for x in parts] + [small.getlength(mid2)])
        if big.getlength(left) + big.getlength(right) + mid_w + 20 * K + (30 * K if g.get("poss") else 0) <= W:
            break
    my = mine(g)[0]
    col = lambda team: GOLD if team == my else WHITE
    lead_a = not pre and g["ascore"] > g["hscore"]
    lead_h = not pre and g["hscore"] > g["ascore"]
    lw = big.getlength(left)
    centered(d, left,  big, col(g["away"]), cx=4 + lw // 2)
    centered(d, right, big, col(g["home"]), cx=W - 4 - big.getlength(right) // 2)
    if lead_a: d.rectangle([4, ROWS - 3 * K, 4 + lw, ROWS - K - 1], fill=GREEN)
    if lead_h: d.rectangle([W - 4 - big.getlength(right), ROWS - 3 * K, W - 4, ROWS - K - 1], fill=GREEN)
    live = g["state"] == "in"
    gap_l, gap_r = 4 + lw, W - 4 - big.getlength(right)
    mx = int((gap_l + gap_r) / 2)                        # middle of the space between the teams
    gap = gap_r - gap_l - 6
    top, bot = int(ROWS * 0.29), int(ROWS * 0.71)
    if mid2 and live:
        centered(d, mid1, small, RED, cx=mx, cy=top)
        centered(d, mid2, small, down_color(g), cx=mx, cy=bot)
    elif mid2:
        centered(d, mid1, small, ODDS_COLOR, cx=mx, cy=top)
        centered(d, mid2, small, ODDS_COLOR, cx=mx, cy=bot)
    else:
        parts = mid1.split(" ", 1) if small.getlength(mid1) > gap and " " in mid1 else [mid1]
        for i, p in enumerate(parts):
            cy = ROWS // 2 if len(parts) == 1 else (top if i == 0 else bot)
            centered(d, p, small, RED if live else GRAY, cx=mx, cy=cy)
    if live and g.get("poss"):                           # little football next to the team with the ball
        bs = big.size / 22
        if g["poss"] == "away":
            draw_ball(d, gap_l + 5, ROWS / 2, bs)
        else:
            draw_ball(d, gap_r - 5 - 11 * bs, ROWS / 2, bs)
    if rotate:                                           # thin bar along the top = time until the next game
        d.rectangle([0, 0, int(W * (time.time() % rotate) / rotate), K - 1], fill=(50, 50, 80))
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
    if x1 - x0 < 20 * K:
        return
    for n in range(int((t - life) / period), int(t / period) + 1):
        age = t - n * period
        if not 0 <= age <= life:
            continue
        r = random.Random(n * 7919)
        cx, cy = r.uniform(x0 + 6 * K, x1 - 6 * K), r.uniform(4 * K, ROWS - 6 * K)
        base = r.choice(FIREWORK_COLORS)
        fade = 1 - (age / life) ** 2                        # stays bright, then drops off
        col = tuple(int(c * fade) for c in base)
        if age < 0.1:                                       # the pop
            d.ellipse([cx - 3 * K, cy - 3 * K, cx + 3 * K, cy + 3 * K], fill=(255, 255, 255))
            continue
        size = (2 if age < 0.7 else 1) * K
        for k in range(22):
            a = 2 * math.pi * k / 22 + n
            dist = 34 * K * age * (0.7 + 0.3 * ((k * 37 + n) % 5) / 4)
            px = cx + math.cos(a) * dist
            py = cy + math.sin(a) * dist * 0.6 + 9 * K * age * age   # flatter for the short strip, then fall
            if -1 <= px < W and -1 <= py < ROWS:
                px, py = int(px), int(py)
                d.rectangle([px, py, px + size - 1, py + size - 1], fill=col)

def draw_fx_text(d, xy, text, f, color, effect, t, still_rainbow=False, outline_px=0):
    """Text with the chosen style. Returns its width. outline_px puts a black edge around it (over pictures)."""
    w = f.getlength(text)
    if effect == "blink" and blink_off(t):
        return w
    px = outline_px or (1 if effect == "fireworks" else 0)
    outline = dict(stroke_width=px, stroke_fill=(0, 0, 0)) if px else {}
    if effect == "rainbow":
        x, y = xy
        for i, ch in enumerate(text):
            d.text((x, y), ch, font=f, fill=rainbow(i, 0 if still_rainbow else t), **outline)
            x += f.getlength(ch)
    else:
        d.text(xy, text, font=f, fill=color, **outline)
    return w

STATUS_SIZE = 21          # clock / odds column of a two-line game
HDR_SIZE    = 28          # league / time-slot labels (black text on a colored box)
SUB_SIZE    = 21          # "TOP PARLAYS" style labels, stacked on two lines

def text_at(d, x, cy, text, f, color, **kw):
    """Draws text with its middle at height cy (caps and digits line up from one line to the next)."""
    l, t, r, b = f.getbbox("0A")
    d.text((x, cy - (b - t) / 2 - t), text, font=f, fill=color, **kw)
    return f.getlength(text)

BALL, LACE = (150, 75, 30), (255, 255, 255)
REDZONE = (255, 90, 60)

def downs(g):
    """'3rd & 7 · DET 35' for a live football game ('' otherwise, e.g. between quarters)."""
    if g["state"] != "in" or not g.get("down"):
        return ""
    return f"{g['down']} · {g['spot']}" if g.get("spot") else g["down"]

def down_color(g):
    return REDZONE if g.get("redzone") else WHITE

def draw_ball(d, x, cy, size=1.0):
    """Little football: marks the team that has the ball."""
    w, h = 11 * size, 7 * size
    d.ellipse([x, cy - h / 2, x + w, cy + h / 2], fill=BALL)
    d.line([(x + w * 0.3, cy), (x + w * 0.7, cy)], fill=LACE, width=max(1, round(size)))
    return w

def game_status(g, odds):
    """Right-hand column of a two-line game: [(text, color), ...] (one or two lines)."""
    if g["state"] == "pre":
        line2 = ((f"O/U {g['total']}" if "ou" in odds and g.get("total") else "") or
                 (g.get("spread") or "" if "spread" in odds else ""))
        first = short_time(g["detail"])
        if not line2 and " " in first and len(first) > 8:          # "Sun 4:25 PM" -> "Sun" / "4:25 PM"
            day, rest = first.split(" ", 1)
            return [(day.upper(), (170, 170, 170)), (rest, (170, 170, 170))]
        return [(first, (170, 170, 170))] + ([(line2, ODDS_COLOR)] if line2 else [])
    if g["state"] == "in":
        d = g["detail"]
        if downs(g):                                                  # football: clock on top, the down underneath
            return [(d, RED), (downs(g), down_color(g))]
        parts = d.split(" ", 1) if " " in d else [d]                # "4th 3:12" -> "4th" / "3:12"
        return [(p, RED) for p in parts]
    return [(g["detail"], (150, 150, 150))]

def game_block(g, odds=("ou", "ml")):
    """One game as a two-line block for the 64-tall strip:
         | DET  24 |  4th
         | MIN  17 |  3:12
    League-colored edge on the left. Before kickoff: moneylines where the scores go, start time and O/U on the right."""
    f, fs = font(LINE_SIZE), font(STATUS_SIZE)
    pre, my = g["state"] == "pre", mine(g)[0]
    show_ml = pre and "ml" in odds and g.get("mla") and g.get("mlh")
    rows = []
    for side, sc, ml in (("away", "ascore", "mla"), ("home", "hscore", "mlh")):
        other = g["hscore"] if side == "away" else g["ascore"]
        if g[side] == my:
            col = GOLD
        elif pre or g[sc] >= other:
            col = WHITE
        else:
            col = (150, 150, 150)                                   # trailing / lost: dimmer
        right = (g[ml] if show_ml else "") if pre else str(g[sc])
        rows.append((team(g, side), right, col, ODDS_COLOR if pre else col))
    status = game_status(g, odds)
    nw = max(f.getlength(r[0]) for r in rows)
    sw = max(f.getlength(r[1]) for r in rows)
    stw = max(fs.getlength(t) for t, _ in status)
    ball_col = g["state"] == "in" and g.get("poss")
    x_name = 26 if ball_col else 10                                  # room for the little football
    x_score_r = x_name + nw + (14 + sw if sw else 0)
    x_div = x_score_r + 12
    x_stat = x_div + 12
    width = int(x_stat + stw + 8)
    img = Image.new("RGB", (width, ROWS))
    d = ImageDraw.Draw(img)
    lg = {l: c for l, _, c in LEAGUES}.get(g["league"], GRAY)
    d.rectangle([0, 4, 3, ROWS - 5], fill=lg)
    cys = (ROWS * 0.25, ROWS * 0.75)
    for (name, right, col, rcol), cy, side in zip(rows, cys, ("away", "home")):
        if ball_col and g["poss"] == side:
            draw_ball(d, 9, cy, 1.3)
        text_at(d, x_name, cy, name, f, col)
        if right:
            text_at(d, x_score_r - f.getlength(right), cy, right, f, rcol)
    d.line([(x_div, 8), (x_div, ROWS - 9)], fill=(55, 55, 55))
    ys = [ROWS / 2] if len(status) == 1 else cys
    for (txt, col), cy in zip(status, ys):
        text_at(d, x_stat, cy, txt, fs, col)
    return img

HALF_SIZE = 20          # text size on a half-height row (split screen)

def game_line(g, odds=("ou", "ml"), h=BOTTOM_H, size=HALF_SIZE):
    """One game on a single line, for a half-height row: | MIN 17  DET 24  4th 3:12
    Before kickoff: | KC @ BUF  4:25 PM  KC +120 BUF -142  O/U 51.5"""
    f, fs = font(size), font(max(10, size - 4))
    pre, my = g["state"] == "pre", mine(g)[0]
    parts = []                                            # (text, color, font)
    def tcol(side, sc, other):
        if g[side] == my: return GOLD
        if pre or g[sc] >= other: return WHITE
        return (150, 150, 150)
    if pre:
        parts += [(team(g, "away"), tcol("away", "ascore", 0), f), (" @ ", GRAY, f), (team(g, "home"), tcol("home", "hscore", 0), f),
                  ("  " + short_time(g["detail"]), (170, 170, 170), fs)]
        line = odds_text(g, odds)
        if line:
            parts.append(("  " + line, ODDS_COLOR, fs))
    else:
        ball = lambda side: [("@BALL", None, f)] if g["state"] == "in" and g.get("poss") == side else []
        parts += ball("away") + [(f"{team(g, 'away')} {g['ascore']}", tcol("away", "ascore", g["hscore"]), f), ("  ", WHITE, f)] \
                 + ball("home") + [(f"{team(g, 'home')} {g['hscore']}", tcol("home", "hscore", g["ascore"]), f),
                                   ("  " + g["detail"], RED if g["state"] == "in" else (150, 150, 150), fs)]
        if downs(g):
            parts.append(("  " + downs(g), down_color(g), fs))
    bsize = size / 22
    plen = lambda t, pf: 11 * bsize + 5 if t == "@BALL" else pf.getlength(t)
    width = int(9 + sum(plen(t, pf) for t, _, pf in parts) + 6)
    img = Image.new("RGB", (width, h))
    d = ImageDraw.Draw(img)
    lg = {l: c for l, _, c in LEAGUES}.get(g["league"], GRAY)
    d.rectangle([0, 3, 2, h - 4], fill=lg)
    x = 9
    for t, c, pf in parts:
        if t == "@BALL":
            x += draw_ball(d, x, h / 2, bsize) + 5
        else:
            x += text_at(d, x, h / 2, t, pf, c)
    return img

def pinned_frame(games, t, ctl):
    """Top row of the split screen: the held games, as many as fit side by side.
    More than fit: pages of them, turning every few seconds (the "Each item stays" time)."""
    img = Image.new("RGB", (W, TOP_H))
    if not games:
        return img
    odds = ctl.show_odds()
    gap = 28
    for size in (22, 20, 18, 16):                         # biggest text that fits them all at once, else pages at 20
        pics = [game_line(g, odds, TOP_H, size) for g in games]
        if sum(p.width for p in pics) + gap * (len(pics) - 1) <= W:
            break
    else:
        pics = [game_line(g, odds, TOP_H, 20) for g in games]
    pages, cur, w = [], [], 0
    for p in pics:
        if cur and w + gap + p.width > W:
            pages.append(cur); cur, w = [], 0
        w += (gap if cur else 0) + p.width
        cur.append(p)
    pages.append(cur)
    page = pages[int(t / max(2, ctl.hold)) % len(pages)]
    total = sum(p.width for p in page) + gap * (len(page) - 1)
    x = max(0, (W - total) // 2)
    d = ImageDraw.Draw(img)
    for i, p in enumerate(page):
        if i:
            d.line([(x - gap // 2, 6), (x - gap // 2, TOP_H - 7)], fill=(60, 60, 60))
        img.paste(p.crop((0, 0, min(p.width, W - x), TOP_H)), (x, 0))
        x += p.width + gap
    if len(pages) > 1:                                    # little dots: which page this is
        for i in range(len(pages)):
            d.rectangle([W - 6 - (len(pages) - i) * 5, TOP_H - 3, W - 4 - (len(pages) - i) * 5, TOP_H - 2],
                        fill=WHITE if pages[i] is page else (70, 70, 70))
    return img

def hdr_block(text, color, h=ROWS):
    """League / time-slot / ParlayLabs label: black words on a colored box."""
    k = h / ROWS
    f, txt = font(int(HDR_SIZE * k) + (2 if h < ROWS else 0)), text.strip()
    tw, pad = f.getlength(txt), int(11 * k)
    img = Image.new("RGB", (int(tw + 4 * pad), h))
    d = ImageDraw.Draw(img)
    m = int(12 * k)
    d.rounded_rectangle([pad, m, pad + tw + 2 * pad, h - m - 1], radius=max(3, int(6 * k)), fill=color)
    text_at(d, 2 * pad, h / 2, txt, f, (0, 0, 0))
    return img

def sub_block(text, color, h=ROWS):
    """Small label stacked on two lines ("TOP" / "PARLAYS") so it takes little room (one line on a half-height row)."""
    if h < ROWS:
        f = font(15)
        img = Image.new("RGB", (int(f.getlength(text.strip()) + 14), h))
        text_at(ImageDraw.Draw(img), 4, h / 2, text.strip(), f, color)
        return img
    f, words = font(SUB_SIZE), text.split()
    if len(words) > 1:
        best = min(range(1, len(words)), key=lambda i: max(f.getlength(" ".join(words[:i])), f.getlength(" ".join(words[i:]))))
        lines = [" ".join(words[:best]), " ".join(words[best:])]
    else:
        lines = words or [""]
    w = max(f.getlength(x) for x in lines)
    img = Image.new("RGB", (int(w + 18), ROWS))
    d = ImageDraw.Draw(img)
    ys = [ROWS / 2] if len(lines) == 1 else (ROWS * 0.3, ROWS * 0.7)
    for ln, cy in zip(lines, ys):
        text_at(d, 4, cy, ln, f, color)
    return img

def seg_kind(sg):
    return sg[2] if len(sg) > 2 else None

def seg_width(sg):
    k = seg_kind(sg)
    if k == "game":
        return game_block(sg[3], sg[4]).width
    if k == "hdr":
        return hdr_block(sg[0], sg[1]).width
    if k == "sub":
        return sub_block(sg[0], sg[1]).width
    return int(FONT.getlength(sg[0]))

def text_strip(segs, stamp=True, hide_msg=False, h=ROWS):
    """Draws segments onto one long 64-tall image; we scroll a window across it.
    A segment is (text, color), or tagged: (text, color, "hdr"/"sub"/"msg") or ("", None, "game", game, odds).
    Games are two-line blocks; headers are colored boxes; everything else is one big line.
    Where your message sits is kept in img.info["spans"] so effects can find it while it scrolls by."""
    if stamp:
        segs = [(f"  {datetime.now():%-I:%M}  ", (0, 180, 255))] + (segs or [("No games right now", GRAY)])
    F = FONT if h == ROWS else font(HALF_SIZE)
    pieces = []
    for sg in segs:
        k = seg_kind(sg)
        if k == "game":
            pieces.append((sg, game_block(sg[3], sg[4]) if h == ROWS else game_line(sg[3], sg[4], h)))
        elif k == "hdr":
            pieces.append((sg, hdr_block(sg[0], sg[1], h)))
        elif k == "sub":
            pieces.append((sg, sub_block(sg[0], sg[1], h)))
        else:
            pieces.append((sg, None))
    width = sum(p.width if p is not None else int(F.getlength(sg[0])) for sg, p in pieces) + 4
    img = Image.new("RGB", (max(width, 1), h))
    d = ImageDraw.Draw(img)
    x, spans = 0, []
    for sg, pic in pieces:
        if pic is not None:
            img.paste(pic, (x, 0))
            x += pic.width
            continue
        text, color = sg[0], sg[1]
        is_msg = seg_kind(sg) == "msg"
        w = int(F.getlength(text))
        if is_msg:
            spans.append((x, x + w))
        if not (is_msg and hide_msg):
            if color == "rainbow":
                l, t, r, b = F.getbbox("0A")
                draw_fx_text(d, (x, h / 2 - (b - t) / 2 - t), text, F, None, "rainbow", 0, still_rainbow=True)
            else:
                text_at(d, x, h / 2, text, F, color)
        x += w
    img.info["spans"] = spans
    return img

def split_two(words, measure):
    """Best place to break a list of words into two lines (the longer line as short as possible)."""
    if len(words) < 2:
        return None
    return min(range(1, len(words)), key=lambda i: max(measure(words[:i]), measure(words[i:])))

def message_layout(txt):
    """How your message fits: (font, [lines]) when it sits still, or None when it has to scroll.
    Short messages get one big line; longer ones wrap onto two lines before falling back to scrolling."""
    f = fit_font(txt, start=26 * K)
    if f.size >= 15 * K:
        return f, [txt]
    words = txt.split()
    i = split_two(words, lambda ws: font(28).getlength(" ".join(ws)))
    if i:
        lines = [" ".join(words[:i]), " ".join(words[i:])]
        for size in range(14 * K, 9 * K - 1, -1):
            if all(font(size).getlength(ln) <= W - 8 for ln in lines):
                return font(size), lines
    return None

def message_frame(ctl, t, x, background=None):
    """Your message in its style, on black or over a picture. Returns (frame, scrolling?, width)."""
    txt, col, fx = (ctl.message or " ").strip() or " ", hex_rgb(ctl.color), ctl.effect
    img = background.copy() if background is not None else Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    lay = None if fx == "scroll" else message_layout(txt)
    scrolling = lay is None
    f, lines = (FONT, [txt]) if scrolling else lay
    if fx == "fireworks":
        fireworks(d, t)
    outline = 2 * K if background is not None else 0
    ys = [ROWS / 2] if len(lines) == 1 else (ROWS * 0.27, ROWS * 0.73)
    l, top, r, b = f.getbbox("0A")
    w = 0
    for ln, cy in zip(lines, ys):
        lw = f.getlength(ln)
        px = x if scrolling else (W - lw) / 2
        w = max(w, draw_fx_text(d, (px, cy - (b - top) / 2 - top), ln, f, col, fx, t, outline_px=outline))
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

LABEL_FONT = 8 * K
LABEL_H    = LABEL_FONT + 2

def card_tokens(card):
    """The card's words with their colors, so long cards can wrap onto two lines."""
    import re
    toks = [(w, col) for txt, col, *_ in card["segs"] for w in re.findall(r"\S+\s*|\s+", txt)]
    while toks and not toks[0][0].strip():
        toks.pop(0)
    if toks:
        toks[0] = (toks[0][0].lstrip(), toks[0][1])
        toks[-1] = (toks[-1][0].rstrip(), toks[-1][1])
    return toks

def _line_w(toks, f):
    return sum(f.getlength(w) for w, _ in toks)

def card_layout(card):
    """(font size, lines) for a card that fits on screen (one big line, or two lines), or (None, [all]) to crawl."""
    toks = card_tokens(card)
    h = ROWS - (LABEL_H if card["label"] else 0)
    for size in range((24 if card["label"] is None else 19) * K, 11 * K - 1, -1):
        if _line_w(toks, font(size)) <= W - 6:
            return size, [toks]
    if sum(1 for w, _ in toks if w.strip()) > 1:
        # prefer breaking between parlay legs ("·") or where the color changes, then anywhere
        nice = [i for i in range(1, len(toks)) if toks[i - 1][0].rstrip().endswith(("·", ":")) or toks[i - 1][1] != toks[i][1]]
        for cuts in (nice, range(1, len(toks))):
            if not cuts:
                continue
            cut = min(cuts, key=lambda i: max(_line_w(toks[:i], font(20)), _line_w(toks[i:], font(20))))
            lines = [toks[:cut], toks[cut:]]
            lines[0][-1:] = [(lines[0][-1][0].rstrip().rstrip("·").rstrip(), lines[0][-1][1])]
            lines[1][:1] = [(lines[1][0][0].lstrip(), lines[1][0][1])]
            for size in range(h // 2 - 2, 15, -1):
                if all(_line_w(ln, font(size)) <= W - 6 for ln in lines):
                    return size, lines
    return None, [toks]

def card_size(card):
    """Font size that fits (one or two lines), or None if it has to crawl across."""
    return card_layout(card)[0]

def card_length(card, ctl):
    if card.get("game"):
        return ctl.hold
    size, lines = card_layout(card)
    if size:
        return ctl.hold
    return max(ctl.hold, (W + _line_w(lines[0], FONT)) / ctl.speed)

def card_frame(card, t, ctl):
    """Draws one card, t seconds after it came up."""
    if card.get("game"):
        return game_frame(card["game"], 0, ctl.show_odds())
    img = Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    size, lines = card_layout(card)
    f = font(size) if size else FONT
    if card["label"]:
        lt, lc = card["label"]
        d.text((3 * K, 0), lt.upper(), font=font(LABEL_FONT), fill=lc)
        top = LABEL_H
    else:
        top = 0
    h = ROWS - top
    fx = ctl.effect if card["msg"] else "steady"
    if fx == "fireworks":
        fireworks(d, t)
    ys = [top + h / 2] if len(lines) == 1 else (top + h * 0.27, top + h * 0.73)
    l, tt, r, b = f.getbbox("0A")
    for toks, cy in zip(lines, ys):
        x = (W - _line_w(toks, f)) / 2 if size else W - t * ctl.speed
        y = cy - (b - tt) / 2 - tt
        for tx, col in toks:
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
        self.pinned, self.bottom, self.bottom_off = [], None, None   # split screen
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

    def shown_games(self):
        """Games on the ticker: the ones picked on the phone (incl. My teams / Live / Close),
        or every game in the chosen sports when nothing is picked."""
        ctl = self.ctl
        if ctl.game_ids:
            return self.picked(), True
        return visible_games(self.games, ctl.leagues, ctl.days), True

    def build_strip(self):
        ctl = self.ctl
        games, raw = self.shown_games()
        pl = pl_segments(self.pl, ctl)
        build = time_segments if ctl.group == "time" else score_segments
        game_segs = build(games, None, ctl.show_odds(), "week", raw)
        segs = game_segs + pl                     # games first, then ParlayLabs
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
        self.build_split(pl, build)
        self.cards = self.make_ticker_cards(pl, games, segs)
        # blink: a copy with the message blanked out, swapped in for the "off" moments
        self.strip_off = text_strip(segs, hide_msg=True) if ctl.msg_in_scroll and ctl.effect == "blink" else None
        # fireworks: which pixels are text, so sparks can go behind the words
        self.strip_mask = (self.strip.convert("L").point(lambda v: 255 if v > 0 else 0)
                           if ctl.msg_in_scroll and ctl.effect == "fireworks" else None)

    def pinned_games(self):
        """Games held on the top row (split screen), or [] for the normal full-height ticker."""
        ctl, now = self.ctl, []
        live = lambda g: g["state"] == "in" or self.linger_left(g)
        if ctl.pin == "mine":
            now = [g for g in self.my_games() if live(g)]
        elif ctl.pin == "live":
            now = [g for g in self.games if live(g) and (not ctl.leagues or g["league"] in ctl.leagues)]
            now.sort(key=lambda g: (not mine(g)[0], not is_close(g), g["league"]))
        elif ctl.pin == "picked" and ctl.game_ids:
            now = self.picked()
        return now

    def build_split(self, pl, build):
        """Bottom row of the split screen: everything that isn't held on top, on one line."""
        ctl = self.ctl
        self.pinned = self.pinned_games()
        if not self.pinned:
            self.bottom = self.bottom_off = None
            return
        ids = {g["id"] for g in self.pinned}
        games, raw = self.shown_games()
        rest = [g for g in games if g["id"] not in ids]
        if not rest:                                     # you picked only the held games: show the rest of your sports
            rest, raw = [g for g in visible_games(self.games, ctl.leagues, ctl.days) if g["id"] not in ids], False
        segs = build(rest, None, ctl.show_odds(), "week", raw) + pl
        if ctl.msg_in_scroll and ctl.message.strip():
            col = "rainbow" if ctl.effect == "rainbow" else hex_rgb(ctl.color)
            note = [(f"   {ctl.message.strip()}   ", col, "msg"), DOT]
            mixed = []
            for seg in segs:
                if len(seg) > 2 and seg[2] == "hdr":
                    mixed += note
                mixed.append(seg)
            segs = mixed or note
        self.bottom = text_strip(segs, h=BOTTOM_H)
        self.bottom_off = text_strip(segs, hide_msg=True, h=BOTTOM_H) if ctl.msg_in_scroll and ctl.effect == "blink" else None

    def make_ticker_cards(self, pl, games, segs):
        """One-at-a-time cards: clock, your message, ParlayLabs items, then each game as a big scoreboard."""
        ctl = self.ctl
        pl_cards = make_cards(pl)                # clock + ParlayLabs, as text cards
        cards = pl_cards[:1]
        if ctl.msg_in_scroll and ctl.message.strip():
            col = "rainbow" if ctl.effect == "rainbow" else hex_rgb(ctl.color)
            cards.append({"label": None, "segs": [(ctl.message.strip(), col)], "msg": True})
        order = {"in": 0, "pre": 1, "post": 2}
        if ctl.group == "time":
            games = sorted(games, key=lambda g: (order[g["state"]],
                           (g.get("start") or 0) * (-1 if g["state"] == "post" else 1)))
        else:
            lg_order = {l: i for i, (l, _, _) in enumerate(LEAGUES)}
            games = sorted(games, key=lambda g: (lg_order.get(g["league"], 9), order[g["state"]], g.get("start") or 0))
        cards += [{"label": None, "segs": [], "msg": False, "game": g} for g in games]
        return cards + pl_cards[1:]              # games first, then ParlayLabs

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
         hours=0, arank=None, hrank=None, down="", spot="", poss="", redzone=False):
    return dict(id=f"{league}-{away}{home}", league=league, away=away, home=home, ascore=a, hscore=h,
                state=state, detail=detail, period=period, clock=clock, ranked=True, spread=spread, total=total,
                mla=mla, mlh=mlh, start=_demo_start(hours), arank=arank, hrank=hrank,
                down=down, spot=spot, poss=poss, redzone=redzone)

def _demo_start(hours):
    """Sample start times: on the hour, `hours` from now."""
    t = time.time() + hours * 3600
    return t - t % 1800

DEMO_GAMES = [
    fake("NFL", "MIN", "DET", 17, 24, "in", "4th 3:12", hours=-3, down="3rd & 7", spot="DET 35", poss="home"),
    fake("NFL", "CHI", "GB", 10, 14, "in", "2nd 0:45", period=2, hours=-1, down="1st & Goal", spot="CHI 8", poss="home", redzone=True),
    fake("NFL", "KC", "BUF", 0, 0, "pre", "Sun 4:25 PM EST", "BUF -2.5", 51.5, mla="+120", mlh="-142", hours=26),
    fake("NFL", "PHI", "DAL", 0, 0, "pre", "Sun 4:25 PM EST", "DAL +3", 47.5, mla="-150", mlh="+128", hours=26),
    fake("NFL", "NYJ", "MIA", 20, 23, "post", "Final", hours=-5),
    fake("NBA", "DET", "BOS", 0, 0, "pre", "7:30 PM EST", "BOS -6.5", 224.5, mla="+225", mlh="-275", hours=4),
    fake("CFB", "MSU", "IOWA", 21, 17, "in", "3rd 9:58", period=3, hours=-2, hrank=22, down="2nd & 4", spot="IOWA 41", poss="away"),
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
                self.send(200, dict(ctl.as_dict(), games=games, has_image=bool(ctl.lib.ready_ids()),
                                    pictures=ctl.lib.list(), pic_version=ctl.lib.version,
                                    feed_ok=feed_ok, feed_error=feed.last_error,
                                    last_ok=int(stale)))
            elif path == "/api/frame.png":
                buf = io.BytesIO(); ctl.last_frame.save(buf, "PNG")
                self.send(200, buf.getvalue(), "image/png")
            elif path == "/api/pictures":
                self.send(200, ctl.lib.list())
            elif path.startswith("/api/pictures/") and path.endswith(".png"):
                # a 640 x 64 preview of one picture as the ticker shows it; ?fit=&zoom=&fx=&fy=&t= try settings without saving
                q = dict(kv.split("=", 1) for kv in self.path.split("?", 1)[1].split("&") if "=" in kv) if "?" in self.path else {}
                pid = path[len("/api/pictures/"):-4]
                try:
                    it = ctl.lib.get(pid)
                    view = None
                    if it and any(k in q for k in ("zoom", "fx", "fy")):
                        z, fx, fy = pictures.view_of(it)
                        view = (q.get("zoom", z), q.get("fx", fx), q.get("fy", fy))
                    r = ctl.lib.rendered(pid, ctl.pic_boost, q.get("fit"), view)
                except Exception as e:
                    return self.send(400, {"error": str(e)})
                if r is None:
                    return self.send(404, {"error": "not ready"})
                t = float(q.get("t", 0) or 0)
                img = r.frame(t if r.pan is None else 0, W, ROWS) if r.pan is None else pictures.pan_window(r.pan, t or 5, 10, W, ROWS)
                buf = io.BytesIO(); img.save(buf, "PNG")
                self.send(200, buf.getvalue(), "image/png")
            else:
                self.send(404, {"error": "not found"})

        def upload(self, n):
            """Saves an uploaded file straight to disk in pieces (videos can be 100+ MB)."""
            if n > pictures.MAX_UPLOAD:
                self.rfile.read(n)
                raise ValueError("that file is too big (250 MB max)")
            fd, tmp = tempfile.mkstemp(dir=PIC_DIR, suffix=".upload")
            with os.fdopen(fd, "wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk); left -= len(chunk)
            return tmp

        def do_POST(self):
            path = self.path.split("?")[0]
            n = int(self.headers.get("Content-Length") or 0)
            q = dict(kv.split("=", 1) for kv in self.path.split("?", 1)[1].split("&") if "=" in kv) if "?" in self.path else {}
            if path in ("/api/pictures", "/api/image"):     # add a picture, GIF or video to the library
                try:
                    tmp = self.upload(n)
                    name = urllib.parse.unquote(q.get("name", "")) or "Picture"
                    fit = q.get("fit") if q.get("fit") in pictures.FITS else "fill"
                    it = ctl.lib.add_file(tmp, name, self.headers.get("Content-Type", ""), fit)
                    if path == "/api/image" or q.get("show") == "1":      # show it right away
                        ctl.update({"mode": "image", "pic_solo": it["id"]})
                    else:
                        ctl.update({})
                except Exception as e:
                    return self.send(400, {"error": str(e)})
                return self.send(200, it)
            if n > 2_000_000:
                return self.send(413, {"error": "too big"})
            body = self.rfile.read(n)
            try:
                if path == "/api/state":
                    ctl.update(json.loads(body or b"{}"))
                elif path == "/api/refresh":                 # the phone's refresh button: pull everything now
                    feed.force = True
                elif path.startswith("/api/pictures/") and path.endswith("/delete"):
                    pid = path.split("/")[3]
                    ctl.lib.delete(pid)
                    ctl.update({"pic_ids": [i for i in ctl.pic_ids if i != pid],
                                "pic_solo": "" if ctl.pic_solo == pid else ctl.pic_solo,
                                "mode": "scroll" if ctl.mode == "image" and not ctl.lib.ready_ids() else ctl.mode})
                elif path.startswith("/api/pictures/"):      # change a picture's fit / zoom / position / name
                    ctl.lib.edit(path.split("/")[3], json.loads(body or b"{}"))
                    ctl.update({})
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
def speed_step(speed):
    """Pixels to move per frame: 1 at slow speeds (smoothest), more when fast so the Pi keeps up."""
    return 1 if speed <= 60 else 2 if speed <= 130 else 3

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
    o.rows, o.cols = ROWS, COLS
    o.chain_length = max(spot for _, spot, _ in PANEL_MAP) + 1
    o.parallel = max(out for out, _, _ in PANEL_MAP)
    # Triple Bonnet: output 1 uses the library's standard pinout. All 5 panels chain off output 1.
    # Single Bonnet: "adafruit-hat" pinout (solder its "E" jumper for 64-tall panels).
    o.hardware_mapping = "regular" if BONNET == "triple" else "adafruit-hat"
    o.brightness = ctl.brightness
    o.gpio_slowdown = 4                   # Pi 4 needs this (use 2 on a Pi 3); stops glitches
    if o.chain_length > 3:                # one long chain: trade a few color steps for refresh speed
        o.pwm_bits = 9
    o.drop_privileges = False
    return RGBMatrix(options=o)

def to_panels(frame):
    """The 640x64 picture cut up for the LED library: each output's chain is one 64-row band,
    stacked top to bottom (output 1, 2, 3), with each panel's slice placed where PANEL_MAP says."""
    chain = max(spot for _, spot, _ in PANEL_MAP) + 1
    outs = max(out for out, _, _ in PANEL_MAP)
    if outs == 1 and chain == PANELS and not any(f for _, _, f in PANEL_MAP) and \
            [spot for _, spot, _ in PANEL_MAP] == list(range(PANELS)):
        return frame                                         # simple single chain: nothing to move
    out = Image.new("RGB", (COLS * chain, ROWS * outs))
    for i, (o, spot, flip) in enumerate(PANEL_MAP):
        piece = frame.crop((i * COLS, 0, (i + 1) * COLS, ROWS))
        if flip:
            piece = piece.rotate(180)
        out.paste(piece, (spot * COLS, (o - 1) * ROWS))
    return out

def test_panels_frame():
    """Big number on each panel (1 = far left) in its own color, with a frame around it."""
    img = Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    cols = [(255, 40, 40), (255, 190, 0), (0, 230, 80), (0, 180, 255), (200, 80, 255)]
    for i in range(PANELS):
        c = cols[i % len(cols)]
        d.rectangle([i * COLS, 0, (i + 1) * COLS - 1, ROWS - 1], outline=c, width=2)
        d.polygon([(i * COLS + 4, 4), (i * COLS + 20, 4), (i * COLS + 4, 20)], fill=c)   # top-left corner mark
        centered(d, str(i + 1), font(52), c, cx=i * COLS + COLS // 2, cy=ROWS // 2)
    return img

def run_test_panels(ctl):
    """Shows the panel numbers until Ctrl-C (stop the ticker service first)."""
    matrix = open_matrix(ctl)
    canvas = matrix.CreateFrameCanvas()
    canvas.SetImage(to_panels(test_panels_frame()))
    matrix.SwapOnVSync(canvas)
    print("Each panel should show 1-5 from left to right, upright, with the corner mark at top left. Ctrl-C to stop.")
    while True:
        time.sleep(1)

def run_display(ctl, feed, matrix):
    canvas = matrix.CreateFrameCanvas()
    frame = Image.new("RGB", (W, ROWS))

    def show(img, x=0):
        nonlocal canvas
        if matrix.brightness != ctl.brightness:
            matrix.brightness = ctl.brightness
        frame.paste((0, 0, 0), (0, 0, W, ROWS))
        frame.paste(img, (x, 0))
        canvas.SetImage(to_panels(frame))
        canvas = matrix.SwapOnVSync(canvas)
        ctl.last_frame = frame.copy() if int(time.time() * 4) % 2 == 0 else ctl.last_frame

    x, seen, img_i = W, -1, 0
    card_i, card_t0 = 0, time.time()
    player = pictures.Player(ctl.lib, W, ROWS)          # Picture mode: one picture or the slideshow
    inter, lap, last_pic, pic_n = None, False, time.time(), 0   # pictures popping up in the ticker
    inter_last = None
    while True:
        al = feed.next_alert()
        if al:                                    # takeover, then go back to whatever was showing
            start = time.time()
            while (t := time.time() - start) < ALERT_SECONDS:
                show(alert_frame(al, t)); time.sleep(1 / 30)
            x, inter, last_pic = W, None, time.time()
        if ctl.version != seen:                   # something changed on the phone
            seen, x, img_i = ctl.version, W, 0
            card_i, card_t0 = 0, time.time()
            inter, lap, last_pic = None, False, time.time()
            feed.build_strip()                    # league picks may have changed

        mode = ctl.mode
        shuffle = ctl.pic_order == "shuffle"

        # ---- a picture popping up in the ticker (after each lap, or every few minutes)
        if mode == "scroll" and inter is None and ctl.pic_every and ctl.lib.ready_ids():
            due = lap if ctl.pic_every < 0 else time.time() - last_pic >= ctl.pic_every * 60
            if due:
                ids = ctl.playlist()
                pid = random.choice([i for i in ids if i != inter_last] or ids) if shuffle and len(ids) > 1 \
                    else ids[pic_n % len(ids)]
                pic_n += 1
                inter = {"id": pid, "start": time.time(), "from": frame.copy()}
            lap = False
        if inter is not None:
            r = ctl.lib.rendered_async(inter["id"], ctl.pic_boost)
            now = time.time()
            if r is None:                         # still being prepared: keep the ticker going meanwhile
                if now - inter["start"] > 20:
                    inter, last_pic = None, now
            else:
                if "t0" not in inter:
                    inter["t0"] = now
                t = now - inter["t0"]
                dur = player.duration(r, ctl.pic_secs)
                if t >= dur:
                    inter_last, inter, last_pic, x = inter["id"], None, now, x
                else:
                    img = r.frame(t, W, ROWS, period=max(6, ctl.pic_secs))
                    if t < 0.5:                   # fade in from the ticker
                        img = Image.blend(inter["from"], img, t / 0.5)
                    elif dur - t < 0.4:           # fade out to black, then the ticker picks up where it was
                        img = Image.blend(img, Image.new("RGB", (W, ROWS)), 1 - (dur - t) / 0.4)
                    show(img)
                    time.sleep(1 / 30)
                    continue

        if mode == "image" and ctl.lib.ready_ids():
            player.set_playlist(ctl.playlist(), shuffle)
            img = player.frame(ctl.pic_secs, ctl.pic_fx, ctl.pic_boost, shuffle) or Image.new("RGB", (W, ROWS))
            if ctl.msg_on_image and (ctl.message or "").strip():   # message over the picture
                img, scrolling, w = message_frame(ctl, time.time(), x, background=img)
                step = speed_step(ctl.speed)
                if scrolling:
                    x = W if x < -w else x - step
                show(img); time.sleep(step / ctl.speed)
            else:
                show(img); time.sleep(1 / 30)
        elif mode == "message":
            img, scrolling, w = message_frame(ctl, time.time(), x)
            show(img)
            step = speed_step(ctl.speed)
            if scrolling:
                x = W if x < -w else x - step
            time.sleep(step / ctl.speed)
        elif feed.bottom is not None and feed.pinned:  # split: held scores on top, the rest crawls underneath
            t = time.time()
            bottom = feed.bottom
            if feed.bottom_off is not None and blink_off(t) and feed.bottom_off.width == bottom.width:
                bottom = feed.bottom_off
            img = Image.new("RGB", (W, ROWS))
            img.paste(pinned_frame(feed.pinned, t, ctl), (0, 0))
            ImageDraw.Draw(img).line([(0, TOP_H), (W, TOP_H)], fill=(35, 35, 50))
            if x < -bottom.width or x > W:
                x, lap = W, True
            img.paste(bottom, (x, ROWS - BOTTOM_H))
            show(img)
            step = speed_step(ctl.speed)
            x -= step
            time.sleep(step / ctl.speed)
        elif ctl.motion != "scroll" and feed.cards:   # one item at a time
            cards = feed.cards
            card = cards[card_i % len(cards)]
            length, t = card_length(card, ctl), time.time() - card_t0
            if t >= length:
                card_i, card_t0 = (card_i + 1) % len(cards), time.time()
                lap = lap or card_i == 0
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
            step = speed_step(ctl.speed)
            x -= step
            if x < -strip.width:
                x, lap = W, True
            time.sleep(step / ctl.speed)

# ------------------------------------------------------------------ test helpers
def save_gif(frames, name, ms):
    big = [f.resize((W * 2, ROWS * 2), Image.NEAREST) for f in frames]
    big[0].save(name, save_all=True, append_images=big[1:], duration=ms, loop=0)
    print("wrote", name)

def run_preview(feed, seconds=45):
    time.sleep(1)
    strip, frames = feed.strip, []
    for x in range(W, W - SCROLL_SPEED * seconds, -4):
        f = Image.new("RGB", (W, ROWS)); f.paste(strip, (x, 0)); frames.append(f)
    save_gif(frames, "preview.gif", 50)          # 4 px every 50 ms = 80 px/s

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
    ap.add_argument("--test-panels", action="store_true", help="number each panel 1-5 to check the wiring")
    args = ap.parse_args()
    if args.test_panels:
        run_test_panels(Control())
    elif args.test_alert:
        run_test_alert(args.test_alert)
    else:
        ctl = Control()
        if args.demo and not ctl.lib.items:          # sample pictures, GIFs and a video to try the Picture tab
            folder = os.path.join(HERE, "demo", "pics")
            for name in sorted(os.listdir(folder)):
                tmp = tempfile.mkstemp(dir=PIC_DIR, suffix=".upload")[1]
                import shutil; shutil.copy(os.path.join(folder, name), tmp)
                ctl.lib.add_file(tmp, name, "video/mp4" if name.endswith(".mp4") else "")
        feed = Feed(ctl, demo=args.demo or args.preview)
        if args.preview:
            run_preview(feed)
        else:
            start_web(ctl, feed, args.port or (8080 if args.sim else WEB_PORT))
            run_display(ctl, feed, SimMatrix() if args.sim else open_matrix(ctl))

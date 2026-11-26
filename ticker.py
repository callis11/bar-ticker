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
import argparse, colorsys, io, json, math, os, random, re, subprocess, sys, tempfile, time, threading, urllib.parse, urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageSequence
import pictures

HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------ settings
TIMEZONE      = "America/Detroit"   # game times and the clock always show in this time zone, whatever the Pi is set to
def _panel_cfg():
    """(panel count, Pi end at the right?, panels upside down?) from panels.txt next to ticker.py, e.g. `2 right`.
    The number is how many 128x64 panels are wired up (each is 320 mm); everything is laid out and centered for it.
    Add `right` if the Pi's ribbon plugs into the RIGHT-hand panel (as you face the front) and the others chain off
    it toward the left. Add `flip` if the panels come up upside down. TICKER_PANELS=3 also works (number only).
    Updates from GitHub never touch panels.txt. No file = 5 panels, Pi end at the left."""
    words = (os.environ.get("TICKER_PANELS") or "").split()
    if not words:
        try:
            words = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "panels.txt")).read().lower().split()
        except OSError:
            words = []
    n = next((int(w) for w in words if w.isdigit()), 5)
    # "map 2:1 2:2 1:1 1:2 3:1x": port:place for each of the 5 panel spots, left to right; x = that panel is switched off
    # (not there yet / taken out). The ticker uses only the panels that are on, side by side.
    wiring = []
    for w in words[words.index("map") + 1:] if "map" in words else []:
        try:
            o, sp = w.rstrip("fx").split(":")
            wiring.append((int(o), int(sp) - 1, "f" in w[-2:], not w.endswith("x")))
        except ValueError:
            break
    custom = [(o, sp, fl) for o, sp, fl, on in wiring[:5] if on]
    if custom:
        n = len(custom)
    return max(1, min(5, n)), "right" in words, "flip" in words, custom[:5], wiring[:5]
PANELS, PI_AT_RIGHT, PANELS_FLIPPED, CUSTOM_MAP, WIRING = _panel_cfg()   # 128x64 P2.5 panels chained left-to-right
ROWS, COLS    = 64, 128
W             = COLS * PANELS   # 640 x 64 pixels
K             = ROWS // 32      # drawing scale (sizes below were first worked out for a 32-tall strip)
SCROLL_SPEED  = 80         # pixels per second (P2.5 pixels are small: 80 px/s is a relaxed crawl)
REFRESH_SEC   = 5          # default refresh for the whole board (changeable on the phone page, 5-60 s)
FAST_SEC      = 5          # leagues where my team (or the game you picked) is live: always this fast
ALERT_SECONDS = 10         # how long an alert takes over the ticker
WEB_PORT      = 80         # control page: http://ticker.local (set hostname "ticker" in Pi Imager)
GPIO_SLOWDOWN = 8          # higher = slower, cleaner signal to the panels (Pi 4: 4-6). Raise it if you see sparkles/noise
PWM_BITS      = 8          # color depth 1-11. Lower = faster refresh, so no smear/flicker when your eyes or a camera move
BONNET        = "triple"   # "triple" = Adafruit Triple LED Matrix Bonnet #6358, "single" = RGB Matrix Bonnet #3211
# Which Bonnet output drives each panel, left to right as you face the ticker:
#   (output 1-3, place in that output's chain starting at 0, mounted upside down?)
# Split over the Triple Bonnet's 3 outputs, each output only feeds 1-2 panels, so the picture refreshes
# ~2.5x faster than one long chain of 5 (smoother, no flicker on phone cameras).
# Run `sudo python3 ticker.py --test-panels` once it's built: each panel shows its number, so a mix-up is easy to fix here.
PANEL_MAP     = [(1, 0, False), (1, 1, False),      # output 1 -> panels 1 and 2
                 (2, 0, False), (2, 1, False),      # output 2 -> panels 3 and 4
                 (3, 0, False)]                     # output 3 -> panel 5
if BONNET == "single":                              # one output: all of them in a row
    PANEL_MAP = [(1, i, False) for i in range(PANELS)]
PANEL_MAP = PANEL_MAP[:PANELS]                      # fewer panels wired up: use the first ones
if PI_AT_RIGHT:
    # Wired from the right (what's built): port 1 = the two far-right panels, port 2 = the middle one, port 3 = the two
    # far-left. In each pair the right-hand panel plugs into the bonnet and the left one chains off it. Panels are added
    # from the right, so with 2, 3, 4 panels the ports fill in this order. Listed here right to left:
    FROM_RIGHT = [(1, 0, False), (1, 1, False), (2, 0, False), (3, 0, False), (3, 1, False)]
    PANEL_MAP = FROM_RIGHT[:PANELS][::-1] if BONNET != "single" else [(1, i, False) for i in range(PANELS)][::-1]
if CUSTOM_MAP:                                      # set on the phone page (Wiring): exactly what's plugged in where
    PANEL_MAP = CUSTOM_MAP
if PANELS_FLIPPED:
    PANEL_MAP = [(o, sp, not fl) for o, sp, fl in PANEL_MAP]
FONT_PATH     = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
os.environ["TZ"] = TIMEZONE
if hasattr(time, "tzset"):
    time.tzset()
FONT_SIZE     = 36         # one-line text (headers, ParlayLabs, messages)
LINE_SIZE     = 25         # each line of a two-line game (away team on top, home underneath)
SETTINGS_FILE = os.path.join(HERE, "settings.json")
ALERTS_FILE = os.path.join(HERE, "alerts", "alerts.ticker")   # alerts you sent to GitHub from the laptop page
ALERTS_LOADED = os.path.join(HERE, "alerts-loaded.json")      # which version of that file is already on here
ALERTS_URL = "https://raw.githubusercontent.com/callis11/bar-ticker/main/alerts/alerts.ticker"
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
WX_DEFAULT_PLACE = "Somerset Center, Michigan"   # weather town (change it on the phone: town name or ZIP)
DEFAULT_STOCKS = ["AAPL", "NVDA", "TSLA"]          # "My stocks" (change on the phone)
BIG_INDEXES = ["^GSPC", "^DJI", "^IXIC"]           # "The big ones": S&P 500, Dow, Nasdaq
DEFAULT_CRYPTO = ["BTC-USD", "ETH-USD", "SOL-USD"]
STOCK_ALERT_CHOICES = (2, 3, 5)                   # "moved this many % today" choices for the stock-mover alert

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
            "group": "league", "days": "week", "pl_parts": ["record", "parlays", "props"], "pl_on": False,
            "speed": SCROLL_SPEED, "motion": "scroll", "hold": 5,
            "pl_count": 5, "pl_sports": [], "prop_types": [], "game_motion": "slide", "ranks": True,
            "sports_on": True, "stocks_on": True, "stocks": list(DEFAULT_STOCKS), "wx_on": True, "wx_place": WX_DEFAULT_PLACE, "wx_parts": ["now", "forecast"],
            "wx_places": [WX_DEFAULT_PLACE], "wx_days": 3, "wx_mode": "scroll", "wx_secs": 8, "wx_every": -1,
            # each section picks its own style: "scroll" (in the crawl) or a held one (fade/flash/cut/slide/push), one item at a time
            "sports_style": None, "pl_style": None, "stock_style": None, "wx_style": None, "sports_every": -1, "pl_every": -1,
            "stock_sets": ["big"], "crypto": list(DEFAULT_CRYPTO), "stock_mode": "scroll", "stock_secs": 8, "stock_every": -1,
            "wx_alerts": True, "wx_alert_level": "warnings", "stock_alerts": True, "stock_alert_pct": 3,
            "game_speed": SCROLL_SPEED, "msg_on_image": False, "pin": "off",
            # pictures: which ones (empty = all), one played on its own, seconds each, order, change style,
            # how often one pops up in the ticker, LED color boost
            "pic_ids": [], "pic_solo": "", "pic_secs": 10, "pic_order": "inorder", "pic_fx": "fade",
            "pic_every": 0, "pic_boost": True, "look": "classic",
            "shows": [{"id": "golions", "name": "Go Lions", "in_ticker": False, "c1": "#0076b6", "c2": "#ffffff",
                       "steps": [{"kind": "words", "text": "GO LIONS!", "fx": "steady", "bg": "color", "secs": 5}]}],
            "show_id": "", "show_every": -1, "logos": True,
            # your own alert looks, per team and kind: {"NFL|DET|td": {...}} (see alert_theme)
            "themes": {},
            # favorite teams (gold, My teams, alerts): starts with the Lions, Pistons and MSU; add any team from the phone
            "favorites": [{"league": lg, "abbr": ab, "name": nm, "c1": "#%02x%02x%02x" % c1, "c2": "#%02x%02x%02x" % c2}
                          for (lg, ab), (nm, c1, c2) in MY_TEAMS.items()]}

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
        self.warm_themes()

    def update(self, d, save=True):
        if d.get("wx_place") and "wx_places" not in d:    # older single-town setting
            d = dict(d, wx_places=[d["wx_place"]])
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
        self.look = set_look(self.look)
        set_logos(self.logos)
        self.pic_ids = [str(i) for i in (self.pic_ids or [])][:200]
        self.pic_solo = str(self.pic_solo or "")
        self.pic_secs = max(3, min(120, int(self.pic_secs)))
        self.pic_order = "shuffle" if self.pic_order == "shuffle" else "inorder"
        self.pic_fx = self.pic_fx if self.pic_fx in ("fade", "slide", "cut") else "fade"
        self.pic_every = int(self.pic_every) if int(self.pic_every) in PIC_EVERY else 0
        self.shows = clean_shows(self.shows)
        self.show_every = int(self.show_every) if int(self.show_every) in PIC_EVERY else -1
        if self.mode == "show" and not any(sh["id"] == self.show_id for sh in self.shows):
            self.mode = "scroll"
        self.pic_boost = bool(self.pic_boost)
        self.favorites = apply_favorites(self.favorites)      # before themes: themes belong to favorite teams
        self.themes = clean_themes(self.themes)
        SHOW_RANKS["on"] = self.ranks
        self.game_motion = self.game_motion if self.game_motion in ("cut", "fade", "flash", "slide", "push", "scroll") else "slide"
        self.game_speed = max(20, min(200, int(self.game_speed)))
        self.pl_count = max(1, min(10, int(self.pl_count)))
        self.sports_on, self.stocks_on, self.wx_on = bool(self.sports_on), bool(self.stocks_on), bool(self.wx_on)
        self.stocks = clean_symbols(self.stocks)
        places = []
        for pl_ in (self.wx_places or []):
            pl_ = str(pl_).strip()[:60]
            if pl_ and pl_.lower() not in [q.lower() for q in places]:
                places.append(pl_)
        self.wx_places = places[:WX_MAX_PLACES] or [(str(self.wx_place or "").strip() or WX_DEFAULT_PLACE)[:60]]
        self.wx_place = self.wx_places[0]                 # first town: also used for the severe-weather alerts
        self.wx_days = 5 if str(self.wx_days) == "5" else 3
        self.stock_sets = [x for x in STOCK_SETS if x in (self.stock_sets or [])]
        self.crypto = clean_symbols(self.crypto)
        legacy = self.motion if self.motion in MOTIONS else "scroll"          # the old single "How it moves" choice
        for kind in ("sports", "pl", "stock", "wx"):
            st = getattr(self, kind + "_style")
            if st not in MOTIONS:                                              # not chosen yet: carry over the old setting
                old = getattr(self, kind + "_mode", "scroll")
                st = ("fade" if legacy == "scroll" else legacy) if old in ("screen", "both") else legacy
            setattr(self, kind + "_style", st)
            e = int(getattr(self, kind + "_every"))
            setattr(self, kind + "_every", e if e in SCREEN_EVERY else -1)
        for kind in ("wx", "stock"):
            setattr(self, kind + "_secs", min(SCREEN_SECS, key=lambda v: abs(v - int(getattr(self, kind + "_secs")))))
        self.wx_parts = [x for x in (self.wx_parts or []) if x in WX_PARTS]
        self.wx_alerts, self.stock_alerts = bool(self.wx_alerts), bool(self.stock_alerts)
        self.wx_alert_level = "watches" if self.wx_alert_level == "watches" else "warnings"
        try:
            self.stock_alert_pct = min(STOCK_ALERT_CHOICES, key=lambda v: abs(v - float(self.stock_alert_pct)))
        except (TypeError, ValueError):
            self.stock_alert_pct = 3
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

    def theme(self, key):
        """An alert theme: yours if you made one, otherwise the classic look."""
        league, abbr, kind = key.split("|")
        return dict(default_theme(league, abbr, kind), **self.themes.get(key, {}))

    def warm_themes(self):
        """Get the alert GIFs ready ahead of time, so a touchdown never waits on them."""
        for th in [{"steps": sh["steps"]} for sh in self.shows]:    # shows' pictures too
            for st in th["steps"]:
                sz = (int(round(W * st["w"] / (2 if st["side"] == "both" else 1))), ROWS) if st["kind"] == "side" else None
                for pid in (st.get("pic"), st.get("pic2")):
                    if pid:
                        self.lib.rendered_async(pid, self.pic_boost, sz)
        for key, th in self.themes.items():
            full = self.theme(key)
            size = (side_width(full), ROWS) if full["style"] == "side" else None
            for pid in th.get("pics", []):
                self.lib.rendered_async(pid, self.pic_boost, size)
            for st in th.get("steps") or []:               # built alerts: each step's pictures at their size
                sz = None
                if st["kind"] == "side":
                    sz = (int(round(W * st["w"] / (2 if st["side"] == "both" else 1))), ROWS)
                for pid in (st.get("pic"), st.get("pic2")):
                    if pid and st["kind"] != "score":
                        self.lib.rendered_async(pid, self.pic_boost, sz)

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
    lp = sit.get("lastPlay") or {}                  # the play that just happened (sacks, picks, field goals...)
    lp_team = str((lp.get("team") or {}).get("id") or "")
    return dict(
        play_id=str(lp.get("id") or ""), play_type=str((lp.get("type") or {}).get("text") or ""),
        play_text=str(lp.get("text") or ""), play_yds=lp.get("statYardage"),
        play_team="away" if lp_team and lp_team in ids(a) else "home" if lp_team and lp_team in ids(h) else "",
        down=sit.get("shortDownDistanceText") or "", spot=sit.get("possessionText") or "",
        redzone=bool(sit.get("isRedZone")),
        poss="away" if poss and poss in ids(a) else "home" if poss and poss in ids(h) else "",
        id=f"{label}-{ev['id']}", league=label,
        away=a["team"]["abbreviation"], home=h["team"]["abbreviation"],
        aname=str(a["team"].get("name") or a["team"]["abbreviation"]), hname=str(h["team"].get("name") or h["team"]["abbreviation"]),
        acolor=_hexrgb(a["team"].get("color")), hcolor=_hexrgb(h["team"].get("color")),
        alogo=str(a["team"].get("logo") or ""), hlogo=str(h["team"].get("logo") or ""),
        aid=str(a["team"].get("id") or ""), hid=str(h["team"].get("id") or ""),
        aalt=_hexrgb(a["team"].get("alternateColor")), halt=_hexrgb(h["team"].get("alternateColor")),
        ascore=_score(a), hscore=_score(h),
        state=st["type"]["state"], detail=st["type"]["shortDetail"],
        period=st.get("period", 0), clock=clock_secs(st.get("displayClock")),
        ranked=any(c.get("curatedRank", {}).get("current", 99) <= 25 for c in (a, h)),
        arank=_rank(a), hrank=_rank(h),
        spread=odds.get("details"), total=odds.get("overUnder"),
        mla=moneyline(odds, "away"), mlh=moneyline(odds, "home"),
        start=start_time(ev.get("date")),
    )

def _hexrgb(h):
    try:
        h = str(h or "").lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else None
    except ValueError:
        return None

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
_team_lists = {}

def league_teams(league):
    """Every team in a league from ESPN: [{abbr, name, nickname, c1, c2}] (also fills in TEAM_IDS). Cached."""
    if league not in _team_lists:
        data = get_json(f"{ESPN}/{LEAGUE_PATHS[league]}/teams?limit=1000")
        out = []
        for t in (((data.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []):
            t = t.get("team") or {}
            ab = t.get("abbreviation")
            if not ab:
                continue
            TEAM_IDS.setdefault((league, ab), t.get("id"))
            out.append({"abbr": ab, "name": t.get("displayName") or ab, "nickname": t.get("name") or t.get("shortDisplayName") or ab,
                        "c1": "#" + (t.get("color") or "ffbe00"), "c2": "#" + (t.get("alternateColor") or "ffffff")})
        _team_lists[league] = sorted(out, key=lambda x: x["name"])
    return _team_lists[league]
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
    if not ctl.pl_on:                                    # ParlayLabs switched off on the phone
        return segs
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

# ------------------------------------------------------------------ stocks + weather
# Both are free feeds with no account or key: Open-Meteo for weather (and finding your town), Yahoo Finance's
# public chart feed for quotes (delayed ~15 min, like most free quotes). They mix into the scroll after ParlayLabs.
WX_API     = "https://api.open-meteo.com/v1/forecast"
GEO_API    = "https://geocoding-api.open-meteo.com/v1/search"
YAHOO      = "https://query1.finance.yahoo.com/v8/finance/chart/"
WX_REFRESH = 600           # weather every 10 minutes
STOCK_OPEN_SEC, STOCK_CLOSED_SEC = 60, 900     # quotes every minute while the market is open, every 15 min otherwise
WX_FALLBACK = (41.98, -84.38)                  # only used if the town lookup can't be reached for the default place
STOCK_NAMES = {"^GSPC": "S&P 500", "^DJI": "DOW", "^IXIC": "NASDAQ", "^RUT": "RUSSELL", "^VIX": "VIX"}
STOCKS_COLOR, WX_COLOR = (0, 210, 140), (120, 190, 255)
LIGHT, RAIN_BLUE, SNOW_BLUE, STORM = (200, 200, 200), (70, 150, 255), (170, 225, 255), (255, 120, 40)
WX_PARTS = ("now", "forecast")
WX_MAX_PLACES = 4
STOCK_SETS = ("big", "mine", "movers", "penny", "crypto")   # what the stock part shows (any mix)
SHOW_MODES = ("scroll", "screen", "both")          # in the scroll, held on screen for a few seconds, or both
SCREEN_SECS = (4, 6, 8, 12, 20)                    # how long each screen stays
SCREEN_EVERY = (-1, 2, 5, 10)                      # how often screens pop up: after each lap / every N minutes
STOCK_MOVER_SEC, STOCK_MOVER_CLOSED_SEC = 300, 1800
# "Top movers" = the biggest % movers today among these big names (plus your own stocks), not the whole market
MOVER_UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "V", "LLY", "WMT", "XOM", "UNH",
                  "MA", "COST", "HD", "NFLX", "AMD", "ORCL", "CRM", "BAC", "KO", "PEP", "DIS", "INTC", "BA", "PLTR", "UBER", "GM", "F"]

# "Penny stocks" = whichever of these low-priced, heavily traded names are under $5 right now (a short list, not the whole market)
PENNY_UNIVERSE = ["SNDL", "NOK", "BB", "PLUG", "FCEL", "GPRO", "OPEN", "LCID", "CLOV", "AMC", "TLRY", "SIRI", "BBAI", "RIG", "NIO",
                  "TELL", "ZOM", "CIFR", "BTG", "HIVE", "WULF", "NU", "SOFI", "PSNY", "GRAB"]
PENNY_MAX = 5.0

def yahoo_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) ParlayLabsTicker"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def clean_symbols(lst):
    """Stock symbols as typed on the phone: upper case, letters/numbers/. - = and a leading ^ for indexes."""
    out = []
    for s in lst or []:
        s = str(s).strip().upper()
        if re.fullmatch(r"\^?[A-Z0-9.\-=]{1,10}", s) and s not in out:
            out.append(s)
    return out[:12]

def market_open(now=None):
    """US stock market hours (with a little slack either side), Mon-Fri, Eastern time."""
    from zoneinfo import ZoneInfo
    t = datetime.fromtimestamp(now or time.time(), ZoneInfo("America/New_York"))
    return t.weekday() < 5 and (9, 0) <= (t.hour, t.minute) < (16, 15)

def fetch_stock(sym, loader=None):
    j = (loader or yahoo_json)(f"{YAHOO}{urllib.parse.quote(sym)}?range=1d&interval=1d")
    m = j["chart"]["result"][0]["meta"]
    price = float(m["regularMarketPrice"])
    prev = float(m.get("previousClose") or m.get("chartPreviousClose") or price)
    chg = price - prev
    return {"sym": sym, "name": STOCK_NAMES.get(sym, sym[:-4] if sym.endswith("-USD") else sym.lstrip("^")), "price": price, "chg": chg,
            "pct": 100 * chg / prev if prev else 0.0}

def fetch_stocks(symbols, loader=None, old=None):
    """(quotes in the phone's order, symbols that failed). A symbol that fails keeps its last quote, if it had one."""
    quotes, bad = [], []
    for sym in symbols:
        try:
            quotes.append(fetch_stock(sym, loader))
        except Exception:
            bad.append(sym)
            if old and sym in old:
                quotes.append(old[sym])
    return quotes, bad

def fmt_price(p):
    return f"{p:,.2f}" if p >= 1 else f"{p:.4f}"

def wanted_symbols(ctl):
    """Every symbol the picked sets need quotes for (movers are fetched separately)."""
    out = []
    if "big" in ctl.stock_sets:
        out += BIG_INDEXES
    if "mine" in ctl.stock_sets:
        out += ctl.stocks
    if "crypto" in ctl.stock_sets:
        out += ctl.crypto
    return list(dict.fromkeys(out))

def pick_movers(quotes, n=3):
    """Biggest gainers first, then the biggest losers."""
    ups = sorted([q for q in quotes if q["pct"] > 0], key=lambda q: -q["pct"])[:n]
    downs = sorted([q for q in quotes if q["pct"] < 0], key=lambda q: q["pct"])[:n]
    return ups + downs

def stock_groups(feed, ctl):
    """[(label, color, quotes)] for what's picked on the phone: the big indexes, my stocks, top movers, crypto.
    Quotes that have gone stale are left out rather than showing old prices."""
    if not ctl.stocks_on:
        return []
    now, data = time.time(), feed.stocks
    live = data and (feed.demo or not ((data["open"] or "crypto" in ctl.stock_sets) and now - data["at"] > 600))
    by = {q["sym"]: q for q in data["quotes"]} if live else {}
    closed = " · CLOSED" if live and not data["open"] else ""
    pick = lambda syms: [by[x] for x in syms if x in by]
    groups = []
    if "big" in ctl.stock_sets:
        groups.append((f"MARKETS{closed}", STOCKS_COLOR, pick(BIG_INDEXES)))
    if "mine" in ctl.stock_sets:
        groups.append((f"STOCKS{closed}", STOCKS_COLOR, pick(ctl.stocks)))
    mv = feed.movers
    if "movers" in ctl.stock_sets and mv and (feed.demo or now - mv["at"] < 7200):
        groups.append((f"TOP MOVERS{closed if live else ''}", (255, 170, 60), pick_movers(mv["quotes"])))
    pn = feed.penny
    if "penny" in ctl.stock_sets and pn and (feed.demo or now - pn["at"] < 7200):
        cheap = [q for q in pn["quotes"] if 0 < q["price"] < PENNY_MAX]
        groups.append((f"PENNY STOCKS{closed if live else ''}", (255, 215, 80), pick_movers(cheap, 4)))
    if "crypto" in ctl.stock_sets:
        groups.append(("CRYPTO", (255, 150, 40), pick(ctl.crypto)))
    return [g for g in groups if g[2]]

def stock_segments(groups, ctl):
    """Stock part of the scroll: a label per group, then AAPL 227.52 +0.55% for each (green up, red down)."""
    if ctl.stock_style != "scroll":
        return []
    segs = []
    for label, color, quotes in groups:
        segs.append((f"  {label}  ", color, "hdr"))
        for q in quotes:
            pct = round(q["pct"], 2)
            col = GREEN if pct > 0 else RED if pct < 0 else GRAY
            segs += [(f"{q['name']} ", WHITE), (f"{fmt_price(q['price'])} ", LIGHT),
                     (f"{'+' if pct >= 0 else '-'}{abs(pct):.2f}%", col), DOT]
    return segs

# WMO weather codes (what Open-Meteo reports) -> words and a color
def wx_text(code):
    table = {0: ("Clear", GOLD), 1: ("Mostly clear", GOLD), 2: ("Partly cloudy", LIGHT), 3: ("Cloudy", (170, 170, 170)),
             45: ("Fog", GRAY), 48: ("Fog", GRAY), 51: ("Drizzle", RAIN_BLUE), 53: ("Drizzle", RAIN_BLUE),
             55: ("Drizzle", RAIN_BLUE), 56: ("Icy drizzle", SNOW_BLUE), 57: ("Icy drizzle", SNOW_BLUE),
             61: ("Light rain", RAIN_BLUE), 63: ("Rain", RAIN_BLUE), 65: ("Heavy rain", RAIN_BLUE),
             66: ("Freezing rain", SNOW_BLUE), 67: ("Freezing rain", SNOW_BLUE), 71: ("Light snow", SNOW_BLUE),
             73: ("Snow", SNOW_BLUE), 75: ("Heavy snow", SNOW_BLUE), 77: ("Snow grains", SNOW_BLUE),
             80: ("Showers", RAIN_BLUE), 81: ("Showers", RAIN_BLUE), 82: ("Heavy showers", RAIN_BLUE),
             85: ("Snow showers", SNOW_BLUE), 86: ("Snow showers", SNOW_BLUE),
             95: ("Thunderstorms", STORM), 96: ("Thunderstorms", STORM), 99: ("Thunderstorms", STORM)}
    return table.get(code, ("", LIGHT))

def temp_color(t):
    return SNOW_BLUE if t <= 32 else WHITE if t < 80 else (255, 170, 60) if t < 95 else RED

def compass(deg):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((deg % 360) / 45 + 0.5) % 8]

def place_label(place):
    return (place.split(",")[0].strip() or place).upper()[:18]

def locate(place, loader=None):
    """Town name ("Somerset Center, Michigan") or ZIP code -> {"lat", "lon", "title"} via Open-Meteo's place search."""
    loader = loader or get_json
    name, _, hint = place.partition(",")
    hint = hint.strip().lower()
    res = loader(f"{GEO_API}?name={urllib.parse.quote(name.strip())}&count=10&language=en&format=json").get("results") or []
    if not res:
        raise ValueError(f"couldn't find “{place}”")
    def score(r):                       # the state/region you typed, then US places
        regions = " ".join(str(r.get(k, "")) for k in ("admin1", "admin2", "country", "country_code")).lower()
        return (bool(hint) and hint in regions, r.get("country_code") == "US")
    r = max(res, key=score)             # max keeps the first (most relevant) result on ties
    title = ", ".join(x for x in (r.get("name"), r.get("admin1")) if x)
    return {"lat": r["latitude"], "lon": r["longitude"], "title": title}

def fetch_weather(loc, loader=None):
    q = (f"?latitude={loc['lat']}&longitude={loc['lon']}"
         "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m"
         "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
         f"&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone={urllib.parse.quote(TIMEZONE, safe='')}&forecast_days=6")
    j = (loader or get_json)(WX_API + q)
    c, d = j["current"], j["daily"]
    def day(i):
        return {"name": datetime.strptime(d["time"][i], "%Y-%m-%d").strftime("%a").upper(),
                "code": d["weather_code"][i], "hi": round(d["temperature_2m_max"][i]), "lo": round(d["temperature_2m_min"][i]),
                "rain": round(d["precipitation_probability_max"][i] or 0)}
    today = day(0)
    return {"temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]), "code": c["weather_code"],
            "wind": round(c["wind_speed_10m"]), "wdir": compass(c["wind_direction_10m"]),
            "hi": today["hi"], "lo": today["lo"], "rain": today["rain"], "days": [day(i) for i in range(1, len(d["time"]))]}

def wx_segments(wxs, ctl):
    """Weather part of the scroll: for each town, where, now (temp + sky), the details, then the next 3 or 5 days."""
    if not ctl.wx_on or ctl.wx_style != "scroll":
        return []
    segs, many = [], len(wxs) > 1
    for place, wx in wxs.items():
        label = place_label(place)
        if "now" in ctl.wx_parts:
            desc, dc = wx_text(wx["code"])
            segs += [(f"{label} ", WHITE), ("", None, "wx", wx["code"]), (f"{wx['temp']}°  ", temp_color(wx["temp"])), (desc, dc), DOT]
            det = [(f"H {wx['hi']}° L {wx['lo']}°  ", LIGHT)]
            if wx["rain"] >= 20:
                det.append((f"Rain {wx['rain']}%  ", RAIN_BLUE))
            if abs(wx["feels"] - wx["temp"]) >= 5:
                det.append((f"Feels {wx['feels']}°  ", LIGHT))
            det.append((f"Wind {wx['wdir']} {wx['wind']}", GRAY))
            segs += det + [DOT]
        if "forecast" in ctl.wx_parts:
            for i, dy in enumerate(wx["days"][:ctl.wx_days]):
                desc, dc = wx_text(dy["code"])
                if i == 0 and (many or "now" not in ctl.wx_parts):
                    segs.append((f"{label}  ", WHITE))
                segs += [(f"{dy['name']} ", (0, 180, 255)), ("", None, "wx", dy["code"]), (f"{dy['hi']}°/{dy['lo']}°  ", WHITE), (desc, dc)]
                if dy["rain"] >= 30:
                    segs.append((f"  {dy['rain']}%", RAIN_BLUE))
                segs.append(DOT)
    return [(f"  WEATHER  ", WX_COLOR, "hdr")] + segs if segs else []

def extra_segments(feed, ctl):
    """Stocks + weather for the scroll (and the one-at-a-time cards)."""
    return stock_segments(stock_groups(feed, ctl), ctl) + wx_segments(feed.wxs_fresh(), ctl)

# ---- weather icons: drawn big then shrunk so the edges are smooth on the LEDs (same shapes as the demo page)
SUN_C, CLOUD_C, RAINCLOUD_C, STORMCLOUD_C = (255, 190, 0), (200, 200, 200), (150, 156, 168), (105, 110, 122)
RAIN_C, SNOW_C, FOG_C, BOLT_C = (70, 150, 255), (225, 242, 255), (160, 160, 160), (255, 210, 0)

def wx_kind(code):
    if code == 0: return "clear"
    if code in (1, 2): return "partly"
    if code in (45, 48): return "fog"
    if code in (51, 53, 55, 61, 63, 65, 80, 81, 82): return "rain"
    if code in (56, 57, 66, 67): return "sleet"
    if code in (71, 73, 75, 77, 85, 86): return "snow"
    if code in (95, 96, 99): return "storm"
    return "cloud"

_icons = {}
def wx_icon(kind, size):
    """RGBA icon on a 48-unit grid: clear, partly, cloud, fog, rain, sleet, snow, storm, plus up / down / warn for alerts."""
    if (kind, size) in _icons:
        return _icons[(kind, size)]
    SS = 4
    n = size * SS
    u = n / 48.0
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    circ = lambda cx, cy, r, fill: d.ellipse([(cx - r) * u, (cy - r) * u, (cx + r) * u, (cy + r) * u], fill=fill)
    def line(x1, y1, x2, y2, fill, w=2.6):
        d.line([x1 * u, y1 * u, x2 * u, y2 * u], fill=fill, width=max(1, int(w * u)))
    def sun(cx, cy, r, r0, r1):
        for i in range(8):
            a = i * math.pi / 4
            line(cx + math.cos(a) * r0, cy + math.sin(a) * r0, cx + math.cos(a) * r1, cy + math.sin(a) * r1, SUN_C)
        circ(cx, cy, r, SUN_C)
    def cloud(dx, dy, fill):
        circ(17 + dx, 28 + dy, 8, fill); circ(27 + dx, 23 + dy, 10, fill); circ(37 + dx, 29 + dy, 7, fill)
        d.rounded_rectangle([(11 + dx) * u, (27 + dy) * u, (43 + dx) * u, (37 + dy) * u], radius=5 * u, fill=fill)
    if kind == "clear":
        sun(24, 24, 9, 13, 20)
    elif kind == "partly":
        sun(17, 17, 6, 9, 13); cloud(2, 6, CLOUD_C)
    elif kind == "fog":
        for i, y in enumerate((14, 23, 32, 41)):
            line(8 + (i % 2) * 4, y, 38 + (i % 2) * 4, y, FOG_C, 3.4)
    elif kind in ("rain", "sleet"):
        cloud(0, -6, RAINCLOUD_C)
        for x in ((16, 25, 34) if kind == "rain" else (16, 34)):
            line(x + 3, 33, x, 41, RAIN_C)
        if kind == "sleet":
            circ(25, 37, 2.4, SNOW_C)
    elif kind == "snow":
        cloud(0, -6, RAINCLOUD_C)
        for x, y in ((15, 37), (24, 33), (33, 37), (20, 43), (29, 43)):
            circ(x, y, 2.4, SNOW_C)
    elif kind == "storm":
        cloud(0, -6, STORMCLOUD_C)
        d.polygon([(x * u, y * u) for x, y in ((27, 29), (19, 39), (24, 39), (21, 47), (32, 35), (26, 35))], fill=BOLT_C)
    elif kind in ("up", "down"):
        pts = ((24, 8), (43, 40), (5, 40)) if kind == "up" else ((24, 40), (43, 8), (5, 8))
        d.polygon([(x * u, y * u) for x, y in pts], fill=(255, 255, 255))
    elif kind == "warn":
        d.polygon([(x * u, y * u) for x, y in ((24, 5), (46, 43), (2, 43))], fill=BOLT_C)
        d.rounded_rectangle([22 * u, 17 * u, 26 * u, 31 * u], radius=2 * u, fill=(0, 0, 0))
        circ(24, 36, 2.4, (0, 0, 0))
    else:                                                  # cloud
        cloud(0, 0, CLOUD_C)
    out = im.resize((size, size), Image.LANCZOS)
    _icons[(kind, size)] = out
    return out

# ---- weather / stock screens: held on the whole strip for a few seconds (instead of crawling)
HEAD_H = 20
RULE   = (35, 35, 50)

def stock_per_screen():
    """How many quotes fit side by side on this strip (6 on the full 5 panels, 2 on two panels)."""
    return max(2, min(6, W // 104))

def fit_size(text, max_w, start, lo=11):
    for size in range(start, lo - 1, -1):
        if font(size).getlength(text) <= max_w:
            return size
    return lo

def runs_width(runs, f):
    return sum(f.getlength(t) for t, _ in runs)

def draw_runs(d, x, cy, runs, f):
    for t, col in runs:
        d.text((x, cy), t, font=f, fill=col, anchor="lm")
        x += f.getlength(t)

def screen_header(d, text, color, short=None):
    f = font(16)
    for t in [text] + (short or []):                      # a shorter strip: drop words until it fits
        if f.getlength(t) <= W - 14:
            break
    d.text((8, HEAD_H // 2), t, font=f, fill=color, anchor="lm")
    d.line([(0, HEAD_H), (W, HEAD_H)], fill=RULE)

def wx_days_per_screen():
    return max(2, min(5, W // 75))

def wx_screen_frame(wx, kind, days=3, start=0):
    """'Right now' or the next 3/5 days for one town, laid out across the whole strip."""
    img = Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    place = place_label(wx["place"])
    top, bh = HEAD_H + 2, ROWS - HEAD_H - 2
    if kind == "now":
        screen_header(d, f"WEATHER  ·  {place}  ·  NOW", WX_COLOR, [f"{place}  ·  NOW", place])
        desc, dc = wx_text(wx["code"])
        temp = f"{wx['temp']}°"
        parts = [[(f"H {wx['hi']}° L {wx['lo']}°", LIGHT)]]
        if wx["rain"] >= 20:
            parts.append([(f"Rain {wx['rain']}%", RAIN_BLUE)])
        if abs(wx["feels"] - wx["temp"]) >= 5:
            parts.append([(f"Feels {wx['feels']}°", LIGHT)])
        parts.append([(f"Wind {wx['wdir']} {wx['wind']}", GRAY)])
        for sc in (1.0, 0.85, 0.72, 0.6):                 # a shorter strip: fewer details first, then smaller
            isz = int((bh - 2) * sc)
            ft, fd, fs = font(int(36 * sc)), font(int(20 * sc)), font(max(10, int(15 * sc)))
            while True:
                det = [r for i, pt in enumerate(parts) for r in (pt if i == 0 else [("     ", LIGHT)] + pt)]
                right_w = max(fd.getlength(desc), runs_width(det, fs))
                total = isz + 16 + ft.getlength(temp) + 26 + right_w
                if total <= W - 12 or len(parts) == 1:
                    break
                parts.pop()                               # wind goes first, then feels-like, then rain
            if total <= W - 12:
                break
        x = max(4, (W - total) / 2)
        ic = wx_icon(wx_kind(wx["code"]), isz)
        img.paste(ic, (int(x), top + (bh - isz) // 2), ic)
        x += isz + 16
        d.text((x, top + bh / 2), temp, font=ft, fill=temp_color(wx["temp"]), anchor="lm")
        x += ft.getlength(temp) + 26
        d.text((x, top + 9), desc, font=fd, fill=dc, anchor="lm")
        draw_runs(d, x, top + 30, det, fs)
    else:
        shown = wx["days"][:days][start:start + wx_days_per_screen()]
        n = len(shown)
        screen_header(d, f"WEATHER  ·  {place}  ·  NEXT {min(days, len(wx['days']))} DAYS", WX_COLOR,
                      [f"{place}  ·  NEXT {min(days, len(wx['days']))} DAYS", place])
        colw, big = W / n, n <= 3 and W / n >= 190
        isz = 38 if big else 30 if colw >= 105 else 0       # narrow columns: no icons, just the day and temps
        for i, dy in enumerate(shown):
            cx = i * colw
            if i:
                d.line([(cx, top + 2), (cx, ROWS - 3)], fill=RULE)
            temps = [(f"{dy['hi']}°", WHITE), ("/", GRAY), (f"{dy['lo']}°", (150, 175, 210))]
            tsz = fit_size("".join(t for t, _ in temps), colw - isz - (34 if isz else 14), 22 if big else 19)
            ftm, fnm, frn = font(tsz), font(16 if big else 15), font(14)
            rain_w = frn.getlength(f"  {dy['rain']}%")
            show_rain = dy["rain"] >= 30 and fnm.getlength(dy["name"]) + rain_w <= colw - 12     # narrow column: skip the rain %
            tw = max(runs_width(temps, ftm), fnm.getlength(dy["name"]) + (rain_w if show_rain else 0))
            x = cx + (colw - (isz + (8 if isz else 0) + tw)) / 2
            if isz:
                ic = wx_icon(wx_kind(dy["code"]), isz)
                img.paste(ic, (int(x), top + (bh - isz) // 2), ic)
                x += isz + 8
            d.text((x, top + 10), dy["name"], font=fnm, fill=(0, 180, 255), anchor="lm")
            if show_rain:
                d.text((x + fnm.getlength(dy["name"]), top + 10), f"  {dy['rain']}%", font=frn, fill=RAIN_BLUE, anchor="lm")
            draw_runs(d, x, top + 31, temps, ftm)
    return img

def stock_screen_frame(data):
    """One group of quotes (markets, my stocks, top movers, crypto) side by side: name and change on top, price below."""
    label, color, quotes = data
    img = Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    screen_header(d, label.strip(), color)
    top, n = HEAD_H + 2, len(quotes)
    colw = W / n
    for i, q in enumerate(quotes):
        pct = round(q["pct"], 2)
        col = GREEN if pct > 0 else RED if pct < 0 else GRAY
        ptxt = f"{'+' if pct >= 0 else '-'}{abs(pct):.2f}%"
        x0, x1 = i * colw + 10, (i + 1) * colw - 10
        if i:
            d.line([(i * colw, top + 2), (i * colw, ROWS - 3)], fill=RULE)
        s1 = fit_size(q["name"] + "   " + ptxt, x1 - x0, 17 if n <= 4 else 14)
        f1 = font(s1)
        d.text((x0, top + 10), q["name"], font=f1, fill=WHITE, anchor="lm")
        d.text((x1, top + 10), ptxt, font=f1, fill=col, anchor="rm")
        price = fmt_price(q["price"])
        d.text((x0, top + 31), price, font=font(fit_size(price, x1 - x0, 24 if n <= 4 else 21)), fill=(215, 215, 215), anchor="lm")
    return img


def wx_piece(code, h=ROWS):
    """Weather icon as a strip piece (black behind it) for the scroll."""
    size = int(h * 0.8)
    img = Image.new("RGB", (size + 14, h))
    ic = wx_icon(wx_kind(code), size)
    img.paste(ic, (7, (h - size) // 2), ic)
    return img

# ---- notices: full-screen takeovers for severe weather and big stock moves
NWS_API       = "https://api.weather.gov/alerts/active"
WXA_REFRESH   = 120                                  # check the National Weather Service every 2 minutes
WXA_REFLASH   = ("Tornado Warning", "Severe Thunderstorm Warning", "Flash Flood Warning")   # flash again every 15 min while active
WXA_REFLASH_SEC = 900
NOTICE_SECONDS = 12
WINTERY = ("Winter", "Blizzard", "Snow", "Ice", "Freez", "Cold", "Chill", "Frost")

def nws_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ParlayLabsTicker (github.com/callis11/bar-ticker)",
                                               "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def fetch_wx_alerts(loc, level="warnings", loader=None):
    """Active National Weather Service alerts for the town: warnings (and watches if asked), most urgent first."""
    j = (loader or nws_json)(f"{NWS_API}?point={loc['lat']:.4f},{loc['lon']:.4f}")
    out = []
    for f in j.get("features", []):
        p = f.get("properties", {})
        ev = (p.get("event") or "").strip()
        if p.get("status") not in (None, "Actual"):                    # skip drills and tests
            continue
        if not (ev.endswith(("Warning", "Emergency")) or (level == "watches" and ev.endswith("Watch"))):
            continue
        out.append({"id": p.get("id") or f.get("id") or ev, "event": ev, "area": p.get("areaDesc", ""),
                    "ends": p.get("ends") or p.get("expires") or ""})
    return sorted(out, key=lambda a: (a["event"].endswith("Watch"), a["event"] not in WXA_REFLASH))

def until_text(iso):
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return ""
    clock = f"{dt:%-I:%M %p}"
    return f"until {clock}" if dt.date() == datetime.now().astimezone().date() else f"until {dt:%a} {clock}"

def wx_alert_color(ev):
    """Background color for the takeover."""
    if ev.endswith("Watch"): return (190, 120, 0)
    if any(w in ev for w in WINTERY): return (0, 90, 190)
    if "Thunderstorm" in ev: return (210, 95, 0)
    return (190, 0, 0)

def wx_alert_text_color(ev):
    """Color for the same alert in the scroll (on black)."""
    if ev.endswith("Watch"): return (255, 190, 0)
    if any(w in ev for w in WINTERY): return SNOW_BLUE
    if "Thunderstorm" in ev: return (255, 140, 40)
    return RED

class Notice:
    """A takeover that isn't a team alert: a big title, a detail line (scrolls if long), a color and an icon."""
    def __init__(self, kind, title, detail, c1, icon=None, secs=NOTICE_SECONDS):
        self.kind, self.title, self.detail, self.c1, self.icon, self.at = kind, title, detail, c1, icon, time.time()
        w, avail = font(19).getlength(detail), W - 90
        self.secs = max(secs, min(28, (avail + w) / 70 + 2)) if w > avail else secs

def wx_notice(a):
    area = ", ".join(x.strip() for x in a["area"].split(";")[:2] if x.strip())
    detail = "  ·  ".join(x for x in (until_text(a["ends"]), area) if x)
    return Notice("wx", a["event"].upper(), detail, wx_alert_color(a["event"]), "warn", 14)

def stock_notice(q):
    up = q["pct"] > 0
    word = f"{'+' if up else '-'}{abs(q['pct']):.1f}%"
    return Notice("stock", f"{q['name']}  {word}",
                  f"{q['name']} is {'up' if up else 'down'} {abs(q['pct']):.1f}% today  ·  now {q['price']:,.2f}",
                  (0, 125, 50) if up else (175, 20, 20), "up" if up else "down", 10)

def notice_frame(nt, t):
    """The takeover: flashes for the first couple of seconds, then holds; a long detail line scrolls."""
    flash = t < 2.4 and int(t * 5) % 2 == 1
    bg = (0, 0, 0) if flash else nt.c1
    img = Image.new("RGB", (W, ROWS), bg)
    d = ImageDraw.Draw(img)
    x0, col = 10, nt.c1 if flash else WHITE
    if nt.icon:
        sz = 52
        ic = wx_icon(nt.icon, sz)
        img.paste(ic, (10, (ROWS - sz) // 2), ic)
        x0 = 10 + sz + 12
    avail = W - x0 - 10
    centered(d, nt.title, fit_font(nt.title, avail, 30), col, x0 + avail // 2, 20)
    f2 = fit_font(nt.detail, avail, 19)
    w2 = f2.getlength(nt.detail)
    if w2 <= avail:
        centered(d, nt.detail, f2, col, x0 + avail // 2, 47)
    else:
        f2 = font(19)
        w2 = f2.getlength(nt.detail)
        layer = Image.new("RGB", (avail, 26), bg)
        sx = avail - (max(0.0, t - 1.0) * 70) % (avail + w2)
        centered(ImageDraw.Draw(layer), nt.detail, f2, col, int(sx + w2 / 2), 13)
        img.paste(layer, (x0, 34))
    return img

def wx_alert_segments(feed, ctl):
    """Active warnings at the very front of the scroll, red, so they're the first thing you see."""
    alerts = feed.alerts_now()
    if not ctl.wx_alerts or not alerts:
        return []
    segs = [("  WEATHER ALERT  ", RED, "hdr")]
    for a in alerts:
        segs += [(a["event"].upper(), wx_alert_text_color(a["event"])), (f"  {until_text(a['ends'])}", LIGHT), DOT]
    return segs

# ------------------------------------------------------------------ alerts
# ---- alert themes: your own look for each team's touchdown / red zone / win / close-game alert
ALERT_KINDS = {"NFL": ["td", "sack", "int", "fumble", "fg", "big", "redzone", "win", "close"], "CFB": ["td", "sack", "int", "fumble", "fg", "big", "redzone", "win", "close"],
               "NBA": ["win", "close"], "CBB": ["win", "close"], "NHL": ["win", "close"], "MLB": ["win", "close"]}

def apply_favorites(favs):
    """Makes the phone's favorite-team list the one everything uses (gold games, My teams, alerts)."""
    clean, seen = [], set()
    for f in favs or []:
        try:
            lg, ab = str(f["league"]).upper(), str(f["abbr"]).upper().strip()[:6]
        except (KeyError, TypeError):
            continue
        if lg not in ALERT_KINDS or not ab or (lg, ab) in seen:
            continue
        seen.add((lg, ab))
        name = (str(f.get("name") or ab).strip().upper() or ab)[:14]
        clean.append({"league": lg, "abbr": ab, "name": name,
                      "c1": f.get("c1") if isinstance(f.get("c1"), str) and len(f["c1"]) == 7 else "#ffbe00",
                      "c2": f.get("c2") if isinstance(f.get("c2"), str) and len(f["c2"]) == 7 else "#ffffff"})
    MY_TEAMS.clear()
    for f in clean[:16]:
        MY_TEAMS[(f["league"], f["abbr"])] = (f["name"], hex_rgb(f["c1"]), hex_rgb(f["c2"]))
    return clean[:16]
THEME_STYLES = ["switch", "over", "side", "pics", "words", "steps"]   # GIF <-> words / words on the GIF / side by side / GIF / words / build my own
STEP_KINDS = ["pic", "words", "over", "side", "score"]   # build my own: picture / words / words on a picture / side by side / the score
MAX_STEPS = 20

def clean_step(st):
    """One step of a built alert, checked: what it shows, which picture(s), the words, how long."""
    if not isinstance(st, dict) or st.get("kind") not in STEP_KINDS:
        return None
    out = {"kind": st["kind"], "secs": max(0.5, min(15.0, round(float(st.get("secs") or 2) * 2) / 2)),
           "fade": bool(st.get("fade"))}
    for k in ("pic", "pic2"):
        if st.get(k): out[k] = str(st[k])[:40]
    if st.get("text") is not None: out["text"] = str(st["text"]).strip()[:40]
    out["fx"] = st["fx"] if st.get("fx") in WORD_FX else "flash"
    out["bg"] = st["bg"] if st.get("bg") in ("color", "black") else "color"
    out["side"] = st["side"] if st.get("side") in ("left", "right", "both") else "left"
    out["w"] = min(SIDE_W, key=lambda v: abs(v - float(st.get("w") or 0.4)))
    out["cmode"] = "custom" if st.get("cmode") == "custom" else "same"   # this step's own colors, or the alert's / show's
    for c, d in (("tc", "#ffffff"), ("bc", "#0076b6")):
        out[c] = st[c] if isinstance(st.get(c), str) and len(st[c]) == 7 and st[c].startswith("#") else d
    return out

def clean_shows(shows):
    """Your shows (Shows tab): a name, steps like a built alert (no score), colors, and whether it's in the ticker."""
    out, ids = [], set()
    for sh in (shows if isinstance(shows, list) else [])[:30]:
        if not isinstance(sh, dict):
            continue
        sid = str(sh.get("id") or "")[:20] or f"s{len(out)}"
        if sid in ids:
            continue
        ids.add(sid)
        steps = [c for c in (clean_step(x) for x in (sh.get("steps") or [])[:MAX_STEPS]) if c and c["kind"] != "score"]
        c = lambda k, d: sh[k] if isinstance(sh.get(k), str) and len(sh[k]) == 7 and sh[k].startswith("#") else d
        out.append({"id": sid, "name": str(sh.get("name") or "Show").strip()[:24] or "Show", "steps": steps,
                    "in_ticker": bool(sh.get("in_ticker")), "c1": c("c1", "#0076b6"), "c2": c("c2", "#ffffff")})
    return out

def show_len(sh):
    return max(1.0, sum(st["secs"] for st in sh["steps"])) if sh.get("steps") else 1.0

def show_frame(sh, t, lib, boost=True):
    """One frame of a show, t seconds in (it loops)."""
    if not sh.get("steps"):
        return Image.new("RGB", (W, ROWS))
    th = {"style": "steps", "steps": sh["steps"], "c1": sh["c1"], "c2": sh["c2"], "every": 1.5, "words": [], "custom_cols": True,
          "text_rgb": hex_rgb(sh["c2"]), "bg_rgb": hex_rgb(sh["c1"]),
          "word_fx": "flash", "word_bg": "color", "side": "left", "side_w": 0.4, "side_full": False,
            "color_mode": "team", "word_col": "#ffffff", "bg_col": "#0076b6"}
    ready = set(lib.ready_ids()) if lib else set()
    return built_frame(None, t % show_len(sh), th, lib, boost, ready, hex_rgb(sh["c1"]), hex_rgb(sh["c2"]), "")

def alert_len(th):
    """How long an alert runs: built ones add up their steps."""
    if th.get("style") == "steps" and th.get("steps"):
        return max(1.0, min(60.0, sum(st["secs"] for st in th["steps"])))
    return th["secs"]
WORD_FX = ["flash", "scroll", "slide", "pop", "blink", "rainbow", "fade", "steady"]   # how the words come in
WORD_BG = ["color", "black", "gif"]                 # behind the words: team color / black / the GIF still playing
SIDE_W = [0.3, 0.4, 0.5]                            # side by side: share of the strip the picture gets
SCROLL_PX = 260                                     # pixels a second for words scrolling across
ANY_CLOSE = "ANY|ALL|close"                         # the close-game alert for every other game
ANY_INFO = ("CLOSE GAME", (255, 170, 0), (255, 255, 255))   # GIF <-> words back and forth / words on the GIF / GIF only / classic

def rgb_hex(c):
    return "#%02x%02x%02x" % tuple(c)

def default_words(name, kind):
    return {"td": ["TOUCHDOWN!", f"{name}!"], "redzone": ["RED ZONE", f"{name}!"],
            "win": [name, "WIN!"], "close": ["CLOSE GAME", name],
            "sack": ["SACK!", f"{name} D!"], "int": ["INTERCEPTION!", f"{name}!"], "fumble": ["FUMBLE!", f"{name} BALL!"],
            "fg": ["FIELD GOAL!", f"{name}!"], "big": ["BIG PLAY!", f"{name}!"]}[kind]

def default_theme(league, abbr, kind):
    if league == "ANY":                              # every game: flash, the score, back to the ticker
        return {"on": True, "style": "words", "pics": [], "words": ["CLOSE GAME", "{score}"], "every": 1.5,
                "secs": 8, "score": True, "c1": "#ffaa00", "c2": "#ffffff",
                "word_fx": "flash", "word_bg": "color", "side": "left", "side_w": 0.4, "side_full": False,
            "color_mode": "team", "word_col": "#ffffff", "bg_col": "#0076b6"}
    name, c1, c2 = MY_TEAMS[(league, abbr)]
    return {"on": True, "style": "switch", "pics": [], "words": default_words(name, kind), "every": 1.5,
            "secs": ALERT_SECONDS, "score": True, "c1": rgb_hex(c1), "c2": rgb_hex(c2),
            "word_fx": "flash", "word_bg": "color", "side": "left", "side_w": 0.4, "side_full": False,
            "color_mode": "team", "word_col": "#ffffff", "bg_col": rgb_hex(c1)}

def clean_themes(themes):
    out = {}
    for key, th in (themes or {}).items():
        try:
            league, abbr, kind = key.split("|")
        except ValueError:
            continue
        if key != ANY_CLOSE and ((league, abbr) not in MY_TEAMS or kind not in ALERT_KINDS.get(league, [])) \
                or not isinstance(th, dict):
            continue
        t = {}
        if "on" in th: t["on"] = bool(th["on"])
        if th.get("style") in THEME_STYLES: t["style"] = th["style"]
        if "pics" in th: t["pics"] = [str(i) for i in (th["pics"] or [])][:12]
        if "words" in th:
            words = [str(w).strip()[:24] for w in (th["words"] or []) if str(w).strip()][:4]
            if words: t["words"] = words
        if "every" in th: t["every"] = max(0.5, min(5.0, float(th["every"])))
        if "secs" in th: t["secs"] = max(4, min(30, int(th["secs"])))
        if "score" in th: t["score"] = bool(th["score"])
        if th.get("word_fx") in WORD_FX: t["word_fx"] = th["word_fx"]
        if th.get("word_bg") in WORD_BG: t["word_bg"] = th["word_bg"]
        if th.get("side") in ("left", "right", "both"): t["side"] = th["side"]
        if "side_full" in th: t["side_full"] = bool(th["side_full"])
        if th.get("color_mode") in ("team", "custom"): t["color_mode"] = th["color_mode"]
        for c in ("word_col", "bg_col"):
            if isinstance(th.get(c), str) and len(th[c]) == 7 and th[c].startswith("#"): t[c] = th[c]
        if isinstance(th.get("steps"), list):
            t["steps"] = [c for c in (clean_step(x) for x in th["steps"][:MAX_STEPS]) if c]
        if "side_w" in th:
            t["side_w"] = min(SIDE_W, key=lambda v: abs(v - float(th["side_w"])))
        for c in ("c1", "c2"):
            if isinstance(th.get(c), str) and len(th[c]) == 7 and th[c].startswith("#"): t[c] = th[c]
        out[key] = t
    return out

class Alert:
    def __init__(self, kind, game, abbr, info):
        self.kind, self.game, self.abbr, self.at = kind, game, abbr, time.time()
        self.name, self.c1, self.c2 = info
        self.key = f"{game['league']}|{abbr}|{kind}" if abbr else ANY_CLOSE
        g = game
        self.score = f"{team(g, 'away')} {g['ascore']}  {team(g, 'home')} {g['hscore']}  {g['detail']}"
        self.words = default_words(self.name, kind)

BIG_PLAY_YARDS = {"NFL": 25, "CFB": 30}

def play_alert(g, old):
    """What ESPN's newest play was, and which side it's good for: ("sack", "home") / (None, None)."""
    t = (g.get("play_type") or "").lower()
    other = lambda s: {"away": "home", "home": "away"}.get(s, "")
    offense = old.get("poss") or g.get("play_team") or ""   # who had the ball before the play
    if "sack" in t:
        return "sack", other(offense)
    if "interception" in t:
        return "int", other(offense)
    if "fumble" in t and ("opponent" in t or "return" in t):
        return "fumble", other(offense)
    if "field goal good" in t:
        return "fg", offense or g.get("play_team")
    try:
        yds = int(g.get("play_yds") or 0)
    except (TypeError, ValueError):
        yds = 0
    if ("rush" in t or "reception" in t or t == "pass") and "touchdown" not in t and yds >= BIG_PLAY_YARDS.get(g["league"], 25):
        return "big", offense or g.get("play_team")
    return None, None

class AlertWatcher:
    """Compares each refresh with the last one and queues alerts for my teams."""
    def __init__(self):
        self.prev, self.close_shown, self.queue = {}, set(), []
        self.rz_at = {}                                  # game id -> when its last red-zone alert fired
        self.seen_any = set()

    def held_alerts(self, g, old):
        """A game held on the top row that isn't one of my teams: touchdowns, big plays, red zone and the win,
        for whichever side it happens to, in that team's own colors."""
        def info(side):
            c1, _ = team_colors(g, side)
            alt = g.get("aalt" if side == "away" else "halt")
            c2 = alt if alt and alt != c1 and _lum(alt) >= 28 else WHITE
            return ((g.get("aname" if side == "away" else "hname") or g[side]).upper(), c1, c2)
        foot = g["league"] in ("NFL", "CFB")
        for side, sc in (("away", "ascore"), ("home", "hscore")):
            if foot and g[sc] - old[sc] >= 6:
                self.queue.append(Alert("td", g, g[side], info(side)))
            rz_now = g["state"] == "in" and g.get("redzone") and g.get("poss") == side
            rz_old = old.get("redzone") and old.get("poss") == side
            if foot and rz_now and not rz_old and time.time() - self.rz_at.get(g["id"], 0) > 120:
                self.rz_at[g["id"]] = time.time()
                self.queue.append(Alert("redzone", g, g[side], info(side)))
        if foot and g.get("play_id") and g["play_id"] != old.get("play_id"):
            kind, who = play_alert(g, old)
            if kind in ("sack", "int", "fumble", "fg", "big") and who in ("away", "home"):
                self.queue.append(Alert(kind, g, g[who], info(who)))
        if old["state"] == "in" and g["state"] == "post" and g["ascore"] != g["hscore"]:
            win = "away" if g["ascore"] > g["hscore"] else "home"
            self.queue.append(Alert("win", g, g[win], info(win)))

    def check(self, games, ctl=None, held=()):
        for g in games:
            abbr, team = mine(g)
            if not abbr and g["id"] in held:              # the game you're holding on top: its own alerts
                old = self.prev.get(g["id"])
                self.prev[g["id"]] = g
                if old is not None:
                    self.held_alerts(g, old)
                continue
            if not abbr:
                if g["id"] not in self.seen_any:            # first look (e.g. just switched on): no alert
                    self.seen_any.add(g["id"])
                    if is_close(g):
                        self.close_shown.add(g["id"])
                    continue
                # any game in your sports getting tight late (your own teams have their own close alert)
                if ctl and is_close(g) and g["id"] not in self.close_shown and \
                        (not ctl.leagues or g["league"] in ctl.leagues) and ctl.theme(ANY_CLOSE)["on"]:
                    self.close_shown.add(g["id"])
                    self.queue.append(Alert("close", g, None, ANY_INFO))
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
            # red zone: my team just got the ball inside the 20 (once per trip; ignore flicker for 2 min)
            side = "away" if g["away"] == abbr else "home"
            rz_now = g["state"] == "in" and g.get("redzone") and g.get("poss") == side
            rz_old = old.get("redzone") and old.get("poss") == side
            if g["league"] in ("NFL", "CFB") and rz_now and not rz_old and time.time() - self.rz_at.get(g["id"], 0) > 120:
                self.rz_at[g["id"]] = time.time()
                self.queue.append(Alert("redzone", g, abbr, team))
            # big plays from ESPN's last play: sack / interception / fumble recovery / field goal / long gain
            if g["league"] in ("NFL", "CFB") and g.get("play_id") and g["play_id"] != old.get("play_id"):
                kind, who = play_alert(g, old)
                if kind and who == side:
                    self.queue.append(Alert(kind, g, abbr, team))
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
# ---- looks: how the whole ticker is drawn (picked on the phone, Ticker tab → Look)
LOOKS = ("classic", "broadcast", "team", "stadium", "big")
FACES = {"classic": FONT_PATH,
         "broadcast": os.path.join(HERE, "fonts", "BarlowCondensed-Bold.ttf"),   # tall narrow TV-style letters
         "big": os.path.join(HERE, "fonts", "BarlowCondensed-Bold.ttf"),
         "team": os.path.join(HERE, "fonts", "Oswald-SemiBold.ttf"),
         "stadium": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"}  # old scoreboard numbers
AMBER, AMBER_DIM = (255, 165, 0), (150, 90, 0)
LOOK = "classic"

def set_look(name):
    global LOOK
    LOOK = name if name in LOOKS else "classic"
    if not os.path.exists(FACES[LOOK]):
        print("look: missing font", FACES[LOOK])
    return LOOK

_fonts = {}
def font(size):
    path = FACES.get(LOOK, FONT_PATH)
    key = (path, size)
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(path, size)
        except OSError:
            _fonts[key] = ImageFont.truetype(FONT_PATH, size)
    return _fonts[key]
FONT = ImageFont.truetype(FONT_PATH, FONT_SIZE)

def fit_font(text, max_w=W - 8, start=28 * K):
    for size in range(start, 9, -1):
        if font(size).getlength(text) <= max_w:
            return font(size)
    return font(10)

def centered(d, text, f, color, cx=W // 2, cy=ROWS // 2):
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    d.text((cx - (r - l) // 2 - l, cy - (b - t) // 2 - t), text, font=f, fill=color)

def score_frame(al, t, c1, c2):
    """End of an alert: the score, big, with a pulsing team-color border."""
    img = game_frame(al.game)
    pulse = int(t * 2) % 2 == 0
    ImageDraw.Draw(img).rectangle([0, 0, W - 1, ROWS - 1], outline=c1 if pulse else c2, width=2 * K)
    return img

def side_width(th):
    """Width of the picture on each side (with pictures on both sides, the size is split between them)."""
    return int(round(W * th.get("side_w", 0.4) / (2 if th.get("side") == "both" else 1)))

def word_phase_len(th, word, region_w):
    """How long one word gets: the switch time, or long enough to scroll all the way across."""
    if th.get("word_fx") == "scroll":
        return max(th["every"], (region_w + font(28 * K).getlength(word)) / SCROLL_PX)
    return th["every"]

def full_len(th):
    return max(2.0, th["every"])                         # side by side, then the GIF full screen this long

def phase_at(th, t, words, region_w):
    """Which part of the alert is on at time t: ("pic" | "words" | "over" | "side" | "full", word #, pic #, start, length)."""
    style, start, i = th["style"], 0.0, 0
    while True:
        if style == "switch":
            kind, wi, pi = ("pic" if i % 2 == 0 else "words"), i // 2, i // 2
        elif style == "side" and th.get("side_full"):  # side by side, fade to the GIF full screen, back again
            kind, wi, pi = ("side" if i % 2 == 0 else "full"), i // 2, i // 2
        else:
            kind, wi, pi = {"pics": "pic", "words": "words", "over": "over", "side": "side"}[style], i, i
        length = th["every"] if kind == "pic" else full_len(th) if kind == "full" else word_phase_len(th, words[wi % len(words)], region_w)
        if t < start + length or i > 500:
            return kind, wi, pi, start, length
        start += length
        i += 1

def draw_words(img, x0, x1, word, th, kind, u, length, t, c1):
    """Draws one word into the strip between x0 and x1, the way the theme says it comes in.
    u = seconds into this word's turn, length = how long the turn lasts."""
    w, fx, bg = x1 - x0, th.get("word_fx", "flash"), th.get("word_bg", "color")
    custom = bool(th.get("custom_cols"))                   # your own word / background colors (else team colors)
    hot = th.get("bg_rgb") if custom else (255, 170, 0) if kind == "close" else (255, 30, 30) if kind == "redzone" else c1
    on = int(t * 4) % 2 == 0                               # 4 flashes a second
    d = ImageDraw.Draw(img)
    if bg == "color":
        d.rectangle([x0, 0, x1 - 1, ROWS - 1], fill=hot if (fx != "flash" or on) else (0, 0, 0))
    elif bg == "black":
        d.rectangle([x0, 0, x1 - 1, ROWS - 1], fill=(0, 0, 0))
    if kind == "win" and bg != "gif":
        fireworks(d, t, x0, x1)
    if fx == "blink" and blink_off(u):
        return img
    dark = th.get("text_rgb") if custom else (0, 0, 0) if kind == "close" else WHITE
    if fx == "flash":
        col = (dark if on else hot) if bg == "color" else ((dark if on else WHITE) if custom else (hot if on else WHITE))
    else:
        col = dark if bg == "color" or custom else (hot if bg == "black" else WHITE)
    f = font(28 * K) if fx == "scroll" else fit_font(word, w - 8)   # scrolling words stay full size, however long
    if fx == "pop":                                        # grows in with a little bounce
        p = min(1.0, u / 0.35)
        k = 0.3 + 0.7 * p + 0.12 * math.sin(math.pi * p)
        f = font(max(10, int(f.size * min(1.12, k))))
    tw = f.getlength(word)
    layer = Image.new("RGBA", (w, ROWS), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    x = (w - tw) / 2
    if fx == "scroll":                                     # right edge to off the left
        x = w - (w + tw) * min(1.0, u / length)
    elif fx == "slide":                                    # slides in, holds, slides out
        p = u / length
        if p < 0.25:
            x += (w + tw) / 2 * (1 - p / 0.25) ** 3           # eases to a stop in the middle
        elif p > 0.85:
            x -= (w + tw) / 2 * ((p - 0.85) / 0.15) ** 2
    l, tp, r, b = ld.textbbox((0, 0), word, font=f)
    y = (ROWS - (b - tp)) / 2 - tp
    stroke = 3 if bg == "gif" else 0
    if fx == "rainbow":                                    # letter by letter
        cx = x
        for n, ch in enumerate(word):
            ld.text((cx, y), ch, font=f, fill=rainbow(n, t) + (255,), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
            cx += f.getlength(ch)
    else:
        ld.text((x, y), word, font=f, fill=tuple(col) + (255,), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    if fx == "fade":
        a = min(1.0, u / 0.3, (length - u) / 0.3)
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * max(0.0, a))))
    region = img.crop((x0, 0, x1, ROWS)).convert("RGBA")
    region.alpha_composite(layer)
    img.paste(region.convert("RGB"), (x0, 0))
    return img

def alert_frame(al, t, th=None, lib=None, boost=True):
    """One frame of an alert, t seconds in, in its theme's style:
       switch: your GIF, then the words, back and forth     over: the words coming across your GIF
       side:   your picture on one side (or both), the words in the rest; can fade to the GIF full screen and back
       pics:   just your GIFs                                words: just the words
    How the words come in (flash, scroll, slide, pop, blink, rainbow, fade) and what's behind them
    (team color, black, or the GIF still playing) are part of the theme too.
    The last few seconds show the score (unless the theme turns that off)."""
    if th is None:
        lg = al.game["league"]
        th = default_theme("ANY", "ALL", "close") if al.key == ANY_CLOSE else \
            default_theme(lg, al.abbr, al.kind) if (lg, al.abbr) in MY_TEAMS else \
            dict(default_theme("ANY", "ALL", "close"), words=al.words, c1=rgb_hex(al.c1), c2=rgb_hex(al.c2), secs=ALERT_SECONDS)
    th = dict(default_theme("ANY", "ALL", "close"), **th)  # older saved themes: fill in the new settings
    if th.get("color_mode") == "custom":                 # your own word and background colors everywhere there are words
        th = dict(th, custom_cols=True, text_rgb=hex_rgb(th.get("word_col") or "#ffffff"), bg_rgb=hex_rgb(th.get("bg_col") or th["c1"]))
    c1, c2 = hex_rgb(th["c1"]), hex_rgb(th["c2"])
    g = al.game                                          # {score} / {clock} / {league} fill in from the game
    short = f"{g['away']} {g['ascore']}  {g['home']} {g['hscore']}"
    words = [w.replace("{score}", short).replace("{clock}", g["detail"]).replace("{league}", g["league"]).replace("{play}", g.get("play_text") or "")
             for w in (th["words"] or al.words)] or ["!"]
    ready = set(lib.ready_ids()) if lib else set()
    if th["style"] == "steps" and th.get("steps"):
        return built_frame(al, t, th, lib, boost, ready, c1, c2, short)
    secs = th["secs"]
    tail = (4 if secs >= 8 else 2) if th["score"] else 0
    if t >= secs - tail:
        return score_frame(al, t, c1, c2)
    pics = [p for p in th["pics"] if p in ready]
    if not pics:                                         # no picture (yet): just the words, on the team color
        th = dict(th, style="words", word_bg="black" if th["word_bg"] == "black" else "color")
    side = th["style"] == "side"
    pw = side_width(th) if side else 0
    words_x0, words_x1 = ((pw, W - pw) if th["side"] == "both" else (pw, W) if th["side"] == "left" else (0, W - pw)) if side else (0, W)
    kind, wi, pi, start, length = phase_at(th, t, words, words_x1 - words_x0)
    word = words[wi % len(words)]

    def picture(size=None, n=None):
        r = lib.rendered_async(pics[(pi if n is None else n) % len(pics)], boost, size) if pics else None
        if r is None:
            return None
        return r.frame(t, *(size or (W, ROWS)), period=6).copy()

    nside = 2 if th["side"] == "both" else 1           # fading to full screen: the last one you picked is the full-screen GIF
    full_id = pics[-1] if side and th.get("side_full") and len(pics) > nside else None
    pool = [p for p in pics if p != full_id] or pics

    def side_by_side(wi, pi, u, length):
        img = Image.new("RGB", (W, ROWS))
        spots = [(0, 0), (W - pw, 1)] if th["side"] == "both" else [(0 if th["side"] == "left" else W - pw, 0)]
        for x, nxt in spots:                             # picture words picture: two picked = one each side
            pic = picture((pw, ROWS), pics.index(pool[(pi + nxt) % len(pool)]))
            if pic is not None:
                img.paste(pic, (x, 0))
        bg = th["word_bg"] if th["word_bg"] != "gif" else "color"
        return draw_words(img, words_x0, words_x1, words[wi % len(words)], dict(th, word_bg=bg), al.kind, u, length, t, c1)

    img = Image.new("RGB", (W, ROWS))
    if kind == "pic":
        return picture() or draw_words(img, 0, W, word, dict(th, word_bg="color"), al.kind, t - start, length, t, c1)
    if kind == "side":
        return side_by_side(wi, pi, t - start, length)
    if kind == "full":                                   # the GIF full screen, fading in from and back to side by side
        full = picture(None, pics.index(full_id) if full_id else pi + nside)
        if full is None:
            return side_by_side(wi, pi, length, length)
        u, fade = t - start, min(0.5, length / 3)
        rw = words_x1 - words_x0
        if u < fade:
            n = word_phase_len(th, words[wi % len(words)], rw)
            return Image.blend(side_by_side(wi, pi, n, n), full, u / fade)
        if u > length - fade:
            n = word_phase_len(th, words[(wi + 1) % len(words)], rw)
            return Image.blend(full, side_by_side(wi + 1, pi + 1, 0, n), (u - (length - fade)) / fade)
        return full
    if kind == "over" or th["word_bg"] == "gif":           # words coming across the GIF, which keeps playing
        base = picture()
        if base is not None:
            return draw_words(base, 0, W, word, dict(th, word_bg="gif"), al.kind, t - start, length, t, c1)
        th = dict(th, word_bg="color")
    return draw_words(img, 0, W, word, th, al.kind, t - start, length, t, c1)

def built_frame(al, t, th, lib, boost, ready, c1, c2, short):
    """Build my own: the steps play in order, each for its own time; a step can fade in from the one before.
    Shows use it too, with no game (al is None)."""
    g = al.game if al else None
    kind_of = al.kind if al else "show"
    def fill(txt):
        txt = (txt or "").replace("{time}", time.strftime("%I:%M").lstrip("0"))
        if g is None:
            return txt
        return txt.replace("{score}", short).replace("{clock}", g["detail"]).replace("{league}", g["league"]).replace("{play}", g.get("play_text") or "")

    def picture(pid, size=None):
        if not pid or pid not in ready:
            return None
        r = lib.rendered_async(pid, boost, size)
        return None if r is None else r.frame(t, *(size or (W, ROWS)), period=6).copy()

    def step(st, u):
        kind, secs, text = st["kind"], st["secs"], fill(st.get("text"))
        look = dict(th, word_fx=st["fx"], word_bg=st["bg"])
        if st.get("cmode") == "custom":                    # this step's own colors
            look.update(custom_cols=True, text_rgb=hex_rgb(st["tc"]), bg_rgb=hex_rgb(st["bc"]))
        if kind == "score":
            return score_frame(al, t, c1, c2) if al else Image.new("RGB", (W, ROWS))
        img = Image.new("RGB", (W, ROWS))
        if kind == "pic":
            return picture(st.get("pic")) or img
        if kind == "over":
            base = picture(st.get("pic"))
            if base is not None:
                return draw_words(base, 0, W, text, dict(look, word_bg="gif"), kind_of, u, secs, t, c1) if text else base
            return draw_words(img, 0, W, text, look, kind_of, u, secs, t, c1) if text else img
        if kind == "side":
            both = st["side"] == "both"
            pw = int(round(W * st["w"] / (2 if both else 1)))
            spots = [(0, st.get("pic")), (W - pw, st.get("pic2") or st.get("pic"))] if both else \
                    [(0 if st["side"] == "left" else W - pw, st.get("pic"))]
            for x, pid in spots:
                pic = picture(pid, (pw, ROWS))
                if pic is not None:
                    img.paste(pic, (x, 0))
            x0, x1 = (pw, W - pw) if both else (pw, W) if st["side"] == "left" else (0, W - pw)
            return draw_words(img, x0, x1, text, look, kind_of, u, secs, t, c1) if text else img
        return draw_words(img, 0, W, text or "!", look, kind_of, u, secs, t, c1)

    steps, start = th["steps"], 0.0
    for i, st in enumerate(steps):
        if t < start + st["secs"] or i == len(steps) - 1:
            u = min(t - start, st["secs"])
            frame = step(st, u)
            fade = min(0.5, st["secs"] / 3)
            if st.get("fade") and i and u < fade:          # fade in from where the step before ended
                prev = steps[i - 1]
                return Image.blend(step(prev, prev["secs"]), frame, u / fade)
            return frame
        start += st["secs"]

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
    LS = 56 if logos_ready(g) else 0                     # team logos at both ends
    for size in range(24 * K, 9, -1):                    # shrink until everything fits
        big, small = font(size), font(max(9, size // 2 + 1))
        parts = mid1.split(" ", 1) if " " in mid1 and not mid2 else [mid1]
        mid_w = max([small.getlength(x) for x in parts] + [small.getlength(mid2)])
        if big.getlength(left) + big.getlength(right) + mid_w + 20 * K + (30 * K if g.get("poss") else 0) + 2 * (LS + 6 if LS else 0) <= W:
            break
    if LS:
        paste_logo(img, team_logo(g, "away", LS), 4, ROWS / 2, LS)
        paste_logo(img, team_logo(g, "home", LS), W - 4 - LS, ROWS / 2, LS)
    E = LS + 6 if LS else 0                              # where the names start / end
    my = mine(g)[0]
    col = lambda team: GOLD if team == my else WHITE
    lead_a = not pre and g["ascore"] > g["hscore"]
    lead_h = not pre and g["hscore"] > g["ascore"]
    lw, rw = big.getlength(left), big.getlength(right)
    # the team that's ahead: its name and score sit on a block of its team color (no underline)
    ca, cr = col(g["away"]), col(g["home"])
    if lead_a:
        fill, ca = team_colors(g, "away")
        d.rounded_rectangle([4 + E - 4, 5 * K, 4 + E + lw + 4, ROWS - 5 * K], radius=3 * K, fill=fill)
    if lead_h:
        fill, cr = team_colors(g, "home")
        d.rounded_rectangle([W - 4 - E - rw - 4, 5 * K, W - 4 - E + 4, ROWS - 5 * K], radius=3 * K, fill=fill)
    centered(d, left,  big, ca, cx=4 + E + lw // 2)
    centered(d, right, big, cr, cx=W - 4 - E - rw // 2)
    live = g["state"] == "in"
    gap_l, gap_r = 4 + E + lw, W - 4 - E - big.getlength(right)
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

# ---- team logos (from ESPN's own logo pictures; downloaded once, kept in logos/ on the Pi, never put on GitHub)
LOGO_DIR = os.path.join(HERE, "logos")
SHOW_LOGOS = True
_logo_src, _logo_sized, _logo_lock = {}, {}, threading.Lock()
ESPN_LOGO_LEAGUE = {"NFL": "nfl", "NBA": "nba", "NHL": "nhl", "MLB": "mlb", "CFB": "ncaa", "CBB": "ncaa"}

def set_logos(on):
    global SHOW_LOGOS
    SHOW_LOGOS = bool(on)

def logo_url(g, side):
    url = g.get("alogo" if side == "away" else "hlogo")
    if url:
        return url
    lg = ESPN_LOGO_LEAGUE.get(g["league"])
    if lg == "ncaa":
        tid = g.get("aid" if side == "away" else "hid")
        return f"https://a.espncdn.com/i/teamlogos/ncaa/500/{tid}.png" if tid else ""
    return f"https://a.espncdn.com/i/teamlogos/{lg}/500/{g[side].lower()}.png" if lg else ""

def _fetch_logo(url):
    """Background download; the dark-background version first (made for black screens), then the normal one."""
    import hashlib
    os.makedirs(LOGO_DIR, exist_ok=True)
    path = os.path.join(LOGO_DIR, hashlib.sha1(url.encode()).hexdigest()[:16] + ".png")
    img = None
    try:
        if os.path.exists(path):
            img = Image.open(path).convert("RGBA")
        else:
            for u in ([url.replace("/500/", "/500-dark/")] if "/500/" in url else []) + [url]:
                try:
                    data = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "bar-ticker"}), timeout=15).read()
                    img = Image.open(io.BytesIO(data)).convert("RGBA")
                    break
                except Exception:
                    continue
            if img is not None:
                img.thumbnail((160, 160), Image.LANCZOS)
                img.save(path)
    except Exception as e:
        print("logo:", e)
    with _logo_lock:
        _logo_src[url] = img or False

def team_logo(g, side, size):
    """The team's logo fitted in a size x size box (RGBA), or None (logos off / not downloaded yet)."""
    if not SHOW_LOGOS:
        return None
    url = logo_url(g, side)
    if not url:
        return None
    with _logo_lock:
        src = _logo_src.get(url)
        if src is None:                                   # not asked for yet: get it in the background
            _logo_src[url] = "loading"
            threading.Thread(target=_fetch_logo, args=(url,), daemon=True).start()
            return None
    if not isinstance(src, Image.Image):
        return None
    key = (url, size)
    if key not in _logo_sized:
        im = src.copy()
        bbox = im.getbbox()                               # trim empty edges so the logo fills its box
        if bbox:
            im = im.crop(bbox)
        im.thumbnail((size, size), Image.LANCZOS)
        im = ImageEnhance.Color(im).enhance(1.25)         # a bit punchier on LEDs
        _logo_sized[key] = im
    return _logo_sized[key]

def paste_logo(img, logo, x, cy, size):
    """Pastes a logo centered in a size-wide slot at x, centered on height cy."""
    if logo is not None:
        img.paste(logo, (int(x + (size - logo.width) / 2), int(cy - logo.height / 2)), logo)

def logos_ready(g):
    return SHOW_LOGOS and team_logo(g, "away", 8) is not None and team_logo(g, "home", 8) is not None

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
    LS = 28                                                          # logo size (each row is 32 tall)
    logo = logos_ready(g)
    x_logo = 26 if ball_col else 10                                  # room for the little football
    x_name = x_logo + (LS + 6 if logo else 0)
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
        if logo:
            paste_logo(img, team_logo(g, side, LS), x_logo, cy, LS)
        text_at(d, x_name, cy, name, f, col)
        if right:
            text_at(d, x_score_r - f.getlength(right), cy, right, f, rcol)
    d.line([(x_div, 8), (x_div, ROWS - 9)], fill=(55, 55, 55))
    ys = [ROWS / 2] if len(status) == 1 else cys
    for (txt, col), cy in zip(status, ys):
        text_at(d, x_stat, cy, txt, fs, col)
    return img

BC_GOLD = (240, 185, 40)

def look_color(color, is_dot=False):
    """Colors of plain text in the scroll for the look you picked."""
    if is_dot:
        return {"broadcast": BC_GOLD, "stadium": AMBER_DIM, "big": (90, 90, 90)}.get(LOOK, color)
    if LOOK == "stadium" and isinstance(color, tuple) and color != RED:
        return AMBER
    if LOOK == "broadcast" and color == (0, 180, 255):  # the clock and time labels: gold instead of blue
        return BC_GOLD
    return color

def line_piece(parts, pad=14):
    """Draws a game on one line from parts: (text, color, font) / ("ball", side-size) / ("live",) / ("gap", px)."""
    widths = []
    for p in parts:
        widths.append(p[1] if p[0] == "gap" else 16 if p[0] == "ball" else 14 if p[0] == "live" else p[3] + 8 if p[0] == "logo"
                      else p[2].getlength(p[0]))
    img = Image.new("RGB", (int(sum(widths) + 2 * pad), ROWS))
    d = ImageDraw.Draw(img)
    x = pad
    for p, w in zip(parts, widths):
        if p[0] == "ball":
            draw_ball(d, x + 1, ROWS / 2, p[1])
        elif p[0] == "live":
            d.ellipse([x + 2, ROWS / 2 - 4, x + 10, ROWS / 2 + 4], fill=RED)
        elif p[0] == "logo":
            paste_logo(img, team_logo(p[1], p[2], p[3]), x, ROWS / 2, p[3])
        elif p[0] != "gap":
            text_at(d, x, ROWS / 2, p[0], p[2], p[1])
        x += w
    return img

def one_line_status(g, odds):
    """[(text, color)] after the teams on a one-line game: FINAL / 4TH 3:12 · 3RD & 7 · DET 35 / SUN 4:25 PM  O/U 51.5"""
    if g["state"] == "pre":
        out = [(short_time(g["detail"]).upper(), (175, 175, 175))]
        extra = (f"O/U {g['total']}" if "ou" in odds and g.get("total") else "") or (g.get("spread") or "" if "spread" in odds else "")
        return out + ([(extra, (150, 150, 150))] if extra else [])
    if g["state"] == "in":
        return [(g["detail"].upper(), None)] + ([(downs(g).upper(), down_color(g))] if downs(g) else [])
    return [(g["detail"].upper(), None)]

def game_broadcast(g, odds):
    """TV style, one line:  CLE 27 @ TEN 24 FINAL   (gold status, the team with the ball gets a little football)"""
    f, fs = font(38), font(32)
    pre, my = g["state"] == "pre", mine(g)[0]
    show_ml = pre and "ml" in odds and g.get("mla") and g.get("mlh")
    parts = []
    for side, sc, ml in (("away", "ascore", "mla"), ("home", "hscore", "mlh")):
        if side == "home":
            parts += [("gap", 10), ("@", (150, 150, 150), fs), ("gap", 10)]
        if g["state"] == "in" and g.get("poss") == side:
            parts += [("ball", 1.3), ("gap", 4)]
        if logos_ready(g):
            parts.append(("logo", g, side, 40))
        parts.append((team(g, side), BC_GOLD if g[side] == my else WHITE, f))
        right = (g[ml] if show_ml else "") if pre else str(g[sc])
        if right:
            other = g["hscore"] if side == "away" else g["ascore"]
            col = (150, 150, 150) if pre else WHITE if g[sc] >= other or g["state"] == "in" else (170, 170, 170)
            parts += [("gap", 9), (right, col, f if not pre else fs)]
    parts.append(("gap", 16))
    if g["state"] == "in":
        parts += [("live",), ("gap", 2)]
    for i, (txt, col) in enumerate(one_line_status(g, odds)):
        parts += ([("gap", 12), ("·", (110, 110, 110), fs), ("gap", 12)] if i else []) + [(txt, col or BC_GOLD, fs)]
    return line_piece(parts)

def game_big(g, odds):
    """Big and bold, readable from across the room:  DET 24  MIN 17  4TH 3:12"""
    f, fs = font(52), font(34)
    pre, my = g["state"] == "pre", mine(g)[0]
    parts = []
    for side, sc in (("away", "ascore"), ("home", "hscore")):
        if side == "home":
            parts.append(("gap", 26))
        if logos_ready(g):
            parts.append(("logo", g, side, 50))
        parts.append((team(g, side), GOLD if g[side] == my else WHITE, f))
        if not pre:
            other = g["hscore"] if side == "away" else g["ascore"]
            parts += [("gap", 12), (str(g[sc]), (255, 225, 60) if g[sc] > other else WHITE, f)]
    parts.append(("gap", 22))
    status = one_line_status(g, odds)[0]
    parts.append((status[0], RED if g["state"] == "in" else (170, 170, 170), fs))
    return line_piece(parts, pad=18)

def _lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

def team_colors(g, side):
    """(fill, text) for a team's color pill; falls back to the league color."""
    c = g.get("acolor" if side == "away" else "hcolor") or DEMO_COLORS.get(g[side])
    alt = g.get("aalt" if side == "away" else "halt")
    if not c or _lum(c) < 28:                        # nearly black shows as nothing on LEDs: use the other color
        c = alt if alt and _lum(alt) >= 28 else c
    if not c or _lum(c) < 28:
        c = {l: col for l, _, col in LEAGUES}.get(g["league"], GRAY)
    return c, ((0, 0, 0) if _lum(c) > 150 else WHITE)

def game_team(g, odds):
    """Team colors: each team in a pill of its own color, score next to it, time on the right (two lines)."""
    f, fp, fs = font(LINE_SIZE), font(LINE_SIZE - 3), font(STATUS_SIZE)
    pre, my = g["state"] == "pre", mine(g)[0]
    show_ml = pre and "ml" in odds and g.get("mla") and g.get("mlh")
    names = [team(g, s) for s in ("away", "home")]
    pw = max(fp.getlength(n) for n in names) + 18
    rights = [(g["mla"] if show_ml else "") if pre else str(g["ascore"]), (g["mlh"] if show_ml else "") if pre else str(g["hscore"])]
    sw = max(f.getlength(r) for r in rights)
    status = game_status(g, odds)
    stw = max(fs.getlength(t) for t, _ in status)
    logo = logos_ready(g)
    x_pill = 8 + (34 if logo else 0)
    x_score_r = x_pill + pw + 12 + sw
    ball = g["state"] == "in" and g.get("poss")
    x_stat = x_score_r + (32 if ball else 18)
    img = Image.new("RGB", (int(x_stat + stw + 12), ROWS))
    d = ImageDraw.Draw(img)
    for i, (side, cy) in enumerate((("away", ROWS * 0.25), ("home", ROWS * 0.75))):
        fill, tc = team_colors(g, side)
        if logo:
            paste_logo(img, team_logo(g, side, 28), 6, cy, 28)
        d.rounded_rectangle([x_pill, cy - 13, x_pill + pw, cy + 12], radius=6, fill=fill,
                            outline=GOLD if g[side] == my else None, width=2)
        text_at(d, x_pill + (pw - fp.getlength(names[i])) / 2, cy, names[i], fp, tc)
        if rights[i]:
            other = g["hscore"] if side == "away" else g["ascore"]
            col = ODDS_COLOR if pre else WHITE if g["ascore" if side == "away" else "hscore"] >= other else (150, 150, 150)
            text_at(d, x_score_r - f.getlength(rights[i]), cy, rights[i], f, col)
        if g["state"] == "in" and g.get("poss") == side:
            draw_ball(d, x_score_r + 8, cy, 1.1)
    ys = [ROWS / 2] if len(status) == 1 else (ROWS * 0.25, ROWS * 0.75)
    for (txt, col), cy in zip(status, ys):
        text_at(d, x_stat, cy, txt, fs, col)
    return img

def game_stadium(g, odds):
    """Old stadium scoreboard: amber letters, each score in its own box, live clock in red."""
    f, fs = font(LINE_SIZE - 1), font(STATUS_SIZE - 2)
    pre = g["state"] == "pre"
    show_ml = pre and "ml" in odds and g.get("mla") and g.get("mlh")
    names = [team(g, s) for s in ("away", "home")]
    rights = [(g["mla"] if show_ml else "") if pre else str(g["ascore"]), (g["mlh"] if show_ml else "") if pre else str(g["hscore"])]
    nw = max(f.getlength(n) for n in names)
    bw = max([f.getlength(r) for r in rights] + [f.getlength("00")]) + 10
    status = [(t, RED if g["state"] == "in" and i == 0 else AMBER_DIM if g["state"] != "in" else AMBER) for i, (t, _) in enumerate(game_status(g, odds))]
    stw = max(fs.getlength(t) for t, _ in status)
    x_box = 10 + nw + 10
    x_stat = x_box + bw + 14
    img = Image.new("RGB", (int(x_stat + stw + 12), ROWS))
    d = ImageDraw.Draw(img)
    for i, cy in enumerate((ROWS * 0.25, ROWS * 0.75)):
        text_at(d, 10, cy, names[i], f, AMBER)
        if rights[i] or not pre:
            d.rectangle([x_box, cy - 13, x_box + bw, cy + 12], outline=AMBER_DIM, width=1)
            text_at(d, x_box + (bw - f.getlength(rights[i])) / 2, cy, rights[i], f, AMBER)
    ys = [ROWS / 2] if len(status) == 1 else (ROWS * 0.25, ROWS * 0.75)
    for (txt, col), cy in zip(status, ys):
        text_at(d, x_stat, cy, txt, fs, col)
    return img

def game_piece(g, odds=("ou", "ml")):
    """One game in the scroll, drawn in the look you picked."""
    return {"broadcast": game_broadcast, "big": game_big, "team": game_team, "stadium": game_stadium}.get(LOOK, game_block)(g, odds)

# colors for the made-up demo games (real games get them from ESPN)
DEMO_COLORS = {"MIN": (79, 38, 131), "DET": (0, 118, 182), "CHI": (11, 22, 42), "GB": (24, 48, 40), "KC": (227, 24, 55),
               "BUF": (0, 51, 141), "PHI": (0, 76, 84), "DAL": (0, 53, 148), "NYJ": (18, 87, 64), "MIA": (0, 142, 151),
               "BOS": (0, 122, 51), "MSU": (24, 69, 59), "IOWA": (255, 205, 0), "DUKE": (0, 48, 135)}

HALF_SIZE = 20          # text size on a half-height row (split screen)

def game_line(g, odds=("ou", "ml"), h=BOTTOM_H, size=HALF_SIZE, bar=True):
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
        lg_ = (lambda side: [("@LOGO" + side, None, f)]) if logos_ready(g) else (lambda side: [])
        parts += lg_("away") + [(team(g, "away"), tcol("away", "ascore", 0), f), (" @ ", GRAY, f)] + lg_("home") + \
                 [(team(g, "home"), tcol("home", "hscore", 0), f),
                  ("  " + short_time(g["detail"]), (170, 170, 170), fs)]
        line = odds_text(g, odds)
        if line:
            parts.append(("  " + line, ODDS_COLOR, fs))
    else:
        ball = lambda side: [("@BALL", None, f)] if g["state"] == "in" and g.get("poss") == side else []
        if logos_ready(g):
            ball = (lambda b: lambda side: b(side) + [("@LOGO" + side, None, f)])(ball)
        parts += ball("away") + [(f"{team(g, 'away')} {g['ascore']}", tcol("away", "ascore", g["hscore"]), f), ("  ", WHITE, f)] \
                 + ball("home") + [(f"{team(g, 'home')} {g['hscore']}", tcol("home", "hscore", g["ascore"]), f),
                                   ("  " + g["detail"], RED if g["state"] == "in" else (150, 150, 150), fs)]
        if downs(g):
            parts.append(("  " + downs(g), down_color(g), fs))
    bsize = size / 22
    LH = h - 6                                            # logo size on a half-height row
    plen = lambda t, pf: 11 * bsize + 5 if t == "@BALL" else LH + 4 if t.startswith("@LOGO") else pf.getlength(t)
    width = int((9 if bar else 3) + sum(plen(t, pf) for t, _, pf in parts) + 6)
    img = Image.new("RGB", (width, h))
    d = ImageDraw.Draw(img)
    lg = {l: c for l, _, c in LEAGUES}.get(g["league"], GRAY)
    if bar:                                               # league-color bar in front (the crawl; not the held top row)
        d.rectangle([0, 3, 2, h - 4], fill=lg)
    x = 9 if bar else 3
    for t, c, pf in parts:
        if t == "@BALL":
            x += draw_ball(d, x, h / 2, bsize) + 5
        elif t.startswith("@LOGO"):
            paste_logo(img, team_logo(g, t[5:], LH), x, h / 2, LH)
            x += LH + 4
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
        pics = [game_line(g, odds, TOP_H, size, bar=False) for g in games]
        if sum(p.width for p in pics) + gap * (len(pics) - 1) <= W:
            break
    else:
        pics = [game_line(g, odds, TOP_H, 20, bar=False) for g in games]
    # a short strip: a game wider than the whole screen shrinks to fit (never clipped), centered
    pics = [p if p.width <= W - 8 else p.resize((W - 8, max(1, round(p.height * (W - 8) / p.width))), Image.LANCZOS) for p in pics]
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
        img.paste(p.crop((0, 0, min(p.width, W - x), TOP_H)), (x, (TOP_H - p.height) // 2 if p.height < TOP_H else 0))
        x += p.width + gap
    if len(pages) > 1:                                    # little dots: which page this is
        for i in range(len(pages)):
            d.rectangle([W - 6 - (len(pages) - i) * 5, TOP_H - 3, W - 4 - (len(pages) - i) * 5, TOP_H - 2],
                        fill=WHITE if pages[i] is page else (70, 70, 70))
    return img

def hdr_block(text, color, h=ROWS):
    """League / time-slot / ParlayLabs label: black words on a colored box."""
    if LOOK == "broadcast":                          # TV style: just the label in gold with a thin gold line under it
        f, txt = font(int(26 * h / ROWS) + 2), text.strip()
        tw = f.getlength(txt)
        img = Image.new("RGB", (int(tw + 36), h))
        d = ImageDraw.Draw(img)
        text_at(d, 18, h / 2 - 2, txt, f, BC_GOLD)
        d.rectangle([18, int(h / 2 + f.size * 0.45), int(18 + tw), int(h / 2 + f.size * 0.45) + 1], fill=BC_GOLD)
        return img
    if LOOK == "stadium":
        color = AMBER if color != RED else RED
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
        return game_piece(sg[3], sg[4]).width
    if k == "hdr":
        return hdr_block(sg[0], sg[1]).width
    if k == "sub":
        return sub_block(sg[0], sg[1]).width
    if k == "wx":
        return wx_piece(sg[3]).width
    return int(font(FONT_SIZE).getlength(sg[0]))

def text_strip(segs, stamp=True, hide_msg=False, h=ROWS):
    """Draws segments onto one long 64-tall image; we scroll a window across it.
    A segment is (text, color), or tagged: (text, color, "hdr"/"sub"/"msg") or ("", None, "game", game, odds).
    Games are two-line blocks; headers are colored boxes; everything else is one big line.
    Where your message sits is kept in img.info["spans"] so effects can find it while it scrolls by."""
    if stamp:
        segs = [(f"  {datetime.now():%-I:%M}  ", (0, 180, 255))] + (segs or [("No games right now", GRAY)])
    F = font(FONT_SIZE) if h == ROWS else font(HALF_SIZE)
    pieces = []
    for sg in segs:
        k = seg_kind(sg)
        if k == "game":
            pieces.append((sg, game_piece(sg[3], sg[4]) if h == ROWS else game_line(sg[3], sg[4], h)))
        elif k == "hdr":
            pieces.append((sg, hdr_block(sg[0], sg[1], h)))
        elif k == "sub":
            pieces.append((sg, sub_block(sg[0], look_color(sg[1]), h)))
        elif k == "wx":
            pieces.append((sg, wx_piece(sg[3], h)))
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
        text, color = sg[0], look_color(sg[1], sg == DOT)
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
    f, lines = (font(FONT_SIZE), [txt]) if scrolling else lay
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
    cards, label, sub, cur, icon = [], None, None, [], None
    def flush():
        nonlocal cur, icon
        body = [sg for sg in cur if sg[0].strip()]
        if body:
            lab = label if not sub else (f"{label[0]} · {sub[0]}" if label else sub[0], sub[1])
            card = {"label": lab, "segs": body, "msg": False}
            if icon is not None:                         # weather icon: drawn at the left of the card
                card["icon"] = icon
            cards.append(card)
        cur, icon = [], None
    cards.append({"label": None, "segs": [(datetime.now().strftime("%-I:%M"), (0, 180, 255))], "msg": False})
    for sg in segs:
        tag = sg[2] if len(sg) > 2 else None
        if tag == "hdr":
            flush(); label, sub = (sg[0].strip(), sg[1]), None
        elif tag == "sub":
            flush(); sub = (sg[0].strip(), sg[1])
        elif tag == "msg":
            flush(); cards.append({"label": None, "segs": [(sg[0].strip(), sg[1])], "msg": True})
        elif tag == "wx":
            icon = sg[3]
        elif sg == DOT or sg[0].strip() == "•":
            flush()
        else:
            cur.append(sg)
    flush()
    return cards

LABEL_FONT = 8 * K
LABEL_H    = LABEL_FONT + 2
ICON_W     = 54                                      # room for a weather icon at the left of a card

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
    avail = W - 6 - (ICON_W if card.get("icon") is not None else 0)
    h = ROWS - (LABEL_H if card["label"] else 0)
    for size in range((24 if card["label"] is None else 19) * K, 11 * K - 1, -1):
        if _line_w(toks, font(size)) <= avail:
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
                if all(_line_w(ln, font(size)) <= avail for ln in lines):
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
    return max(ctl.hold, (W + _line_w(lines[0], font(FONT_SIZE))) / ctl.speed)

def card_frame(card, t, ctl):
    """Draws one card, t seconds after it came up."""
    if card.get("game"):
        return game_frame(card["game"], 0, ctl.show_odds())
    img = Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    size, lines = card_layout(card)
    f = font(size) if size else font(FONT_SIZE)
    if card["label"]:
        lt, lc = card["label"]
        d.text((3 * K, 0), lt.upper(), font=font(LABEL_FONT), fill=lc)
        top = LABEL_H
    else:
        top = 0
    h = ROWS - top
    x0 = 0
    if card.get("icon") is not None:
        sz = h - 4
        ic = wx_icon(wx_kind(card["icon"]), sz)
        img.paste(ic, (8, top + (h - sz) // 2), ic)
        x0 = ICON_W
    fx = ctl.effect if card["msg"] else "steady"
    if fx == "fireworks":
        fireworks(d, t)
    ys = [top + h / 2] if len(lines) == 1 else (top + h * 0.27, top + h * 0.73)
    l, tt, r, b = f.getbbox("0A")
    for toks, cy in zip(lines, ys):
        x = x0 + (W - x0 - _line_w(toks, f)) / 2 if size else W - t * ctl.speed
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
        self.sports_cards, self.pl_cards, self.warn = [], [], []
        self.stocks, self.movers, self.penny = None, None, None      # latest quotes / today's top movers (see extras_loop)
        self.wxs, self.wx_statuses = {}, {}        # latest weather per town
        self.stock_bad, self.wx_status, self.wx_loc = [], "", {}
        self.screens_alone = False                 # nothing else is on, so weather/stock screens just stay up
        self._strip_sig = None                     # what the strip was last drawn from (redrawn only when it changes)
        self.extra_kick = False
        self.notices = []                          # takeovers waiting to play (severe weather, big stock moves)
        self.wxa, self.wxa_at, self.wxa_seen, self.wxa_status = [], 0, {}, ""
        self.wxa_test = None                       # (alert, until) while the phone's test button is showing a pretend alert
        self.stock_fired = set()                   # (symbol, up?, day) already flashed
        self.watcher = AlertWatcher()
        threading.Thread(target=self.loop, daemon=True).start()
        threading.Thread(target=self.extras_loop, daemon=True).start()   # own thread: a slow quote never stalls the scores

    def loop(self):
        """Hot leagues (my team or the pinned game live) every FAST_SEC, everything else every ctl.refresh."""
        by_league, last, last_pl, last_next = {}, {}, 0, 0
        while True:
            now = time.time()
            if self.force:                               # refresh button: skip the waits this time
                self.force, last, last_pl, last_next = False, {}, 0, 0
                self.extra_kick = True
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
                self.watcher.check(self.games, self.ctl, {g["id"] for g in self.pinned_games()})
                self.note_finishes()
                if now - last_pl > PL_REFRESH:    # ParlayLabs files are big (~8 MB) and change slowly
                    self.pl, last_pl = parlaylabs() or self.pl, now
                if now - last_next > NEXT_REFRESH:
                    self.update_next_games(); last_next = now
            self.build_strip()
            time.sleep(1)

    def extras_loop(self):
        """Stocks and weather, in their own thread. Only fetches what's switched on, and again when you change
        the symbols or the town."""
        last_s = last_w = last_a = last_m = last_p = 0
        key_s = key_w = key_a = None
        while True:
            ctl, now = self.ctl, time.time()
            if self.extra_kick:
                self.extra_kick, last_s, last_w, last_a, last_m, last_p = False, 0, 0, 0, 0, 0
            ks, kw = (ctl.stocks_on, tuple(wanted_symbols(ctl)), "movers" in ctl.stock_sets, "penny" in ctl.stock_sets), (ctl.wx_on, tuple(ctl.wx_places))
            if ks != key_s:
                key_s, last_s, last_m, last_p = ks, 0, 0, 0
            if kw != key_w:
                key_w, last_w = kw, 0
            ka = (ctl.wx_alerts, ctl.wx_place, ctl.wx_alert_level)
            if ka != key_a:
                key_a, last_a = ka, 0
            if self.demo:
                self.stocks, self.movers, self.penny = demo_stocks(ctl), demo_movers(), demo_penny()
                self.wxs = {pl_: demo_weather(pl_) for pl_ in ctl.wx_places}
                self.wx_statuses = {pl_: "sample weather" for pl_ in ctl.wx_places}
            else:
                if ctl.stocks_on and wanted_symbols(ctl) and now - last_s >= (STOCK_OPEN_SEC if market_open() or "crypto" in ctl.stock_sets else STOCK_CLOSED_SEC):
                    self.update_stocks(); last_s = time.time()
                if ctl.stocks_on and "movers" in ctl.stock_sets and now - last_m >= (STOCK_MOVER_SEC if market_open() else STOCK_MOVER_CLOSED_SEC):
                    self.update_movers(); last_m = time.time()
                if ctl.stocks_on and "penny" in ctl.stock_sets and now - last_p >= (STOCK_MOVER_SEC if market_open() else STOCK_MOVER_CLOSED_SEC):
                    self.update_penny(); last_p = time.time()
                if ctl.wx_on and now - last_w >= WX_REFRESH:
                    self.update_weather(); last_w = time.time()
                if ctl.wx_alerts and now - last_a >= WXA_REFRESH:
                    self.update_wx_alerts(); last_a = time.time()
            time.sleep(1)

    def update_stocks(self):
        old = {q["sym"]: q for q in (self.stocks or {}).get("quotes", [])}
        syms = wanted_symbols(self.ctl)
        quotes, self.stock_bad = fetch_stocks(syms, old=old)
        if quotes:                                   # "at" only moves when something fresh came in (old quotes go stale)
            fresh = len(self.stock_bad) < len(syms)
            self.stocks = {"quotes": quotes, "open": market_open(),
                           "at": time.time() if fresh else (self.stocks or {}).get("at", time.time())}
            self.check_movers(quotes)

    def update_movers(self):
        """Today's biggest movers among the big names (plus your own stocks): quotes every few minutes."""
        syms = list(dict.fromkeys(MOVER_UNIVERSE + [x for x in self.ctl.stocks if not x.startswith("^")]))
        quotes, _ = fetch_stocks(syms)
        if quotes:
            self.movers = {"quotes": quotes, "at": time.time()}

    def update_penny(self):
        """Quotes for the penny-stock list, plus any of your own stocks: the under-$5 ones are picked when it's drawn."""
        syms = list(dict.fromkeys(PENNY_UNIVERSE + [x for x in self.ctl.stocks if not x.startswith("^")]))
        quotes, _ = fetch_stocks(syms)
        if quotes:
            self.penny = {"quotes": quotes, "at": time.time()}

    def check_movers(self, quotes):
        """Flash once per symbol, per direction, per day when it's moved the chosen % while the market is open."""
        ctl = self.ctl
        if not ctl.stock_alerts or not market_open():
            return
        day = datetime.now().strftime("%Y-%m-%d")
        for q in quotes:
            if q["sym"] in self.stock_bad or q["sym"].endswith("-USD") or abs(q["pct"]) < ctl.stock_alert_pct:
                continue
            key = (q["sym"], q["pct"] > 0, day)
            if key not in self.stock_fired:
                self.stock_fired.add(key)
                self.notices.append(stock_notice(q))

    def locate_cached(self, place):
        loc = self.wx_loc.get(place)
        if not loc:
            try:
                loc = locate(place)
            except Exception:                          # lookup failed: fine for the default town, an error for any other
                if place != WX_DEFAULT_PLACE:
                    raise
                loc = {"lat": WX_FALLBACK[0], "lon": WX_FALLBACK[1], "title": place}
            self.wx_loc[place] = loc
        return loc

    def update_wx_alerts(self):
        """National Weather Service warnings for the town: a takeover when a new one appears (tornado, severe
        thunderstorm and flash flood warnings flash again every 15 min while they last)."""
        ctl, now = self.ctl, time.time()
        try:
            alerts = fetch_wx_alerts(self.locate_cached(ctl.wx_place), ctl.wx_alert_level)
        except Exception as e:
            self.wxa_status = f"error: {e}"
            return
        self.wxa, self.wxa_at, self.wxa_status = alerts, now, "ok"
        for a in alerts:
            last = self.wxa_seen.get(a["id"])
            if last is None or (a["event"] in WXA_REFLASH and now - last >= WXA_REFLASH_SEC):
                self.wxa_seen[a["id"]] = now
                self.notices.append(wx_notice(a))
        live = {a["id"] for a in alerts}
        for k in [k for k in self.wxa_seen if k not in live]:
            del self.wxa_seen[k]                        # it ended: a new one with the same name flashes again

    def alerts_now(self):
        """Warnings to show in the scroll: the real ones (if the last check is recent), or the phone's test one."""
        out = list(self.wxa) if time.time() - self.wxa_at < 1800 else []
        if self.wxa_test and time.time() < self.wxa_test[1]:
            out.insert(0, self.wxa_test[0])
        return out

    def next_notice(self):
        return self.notices.pop(0) if self.notices else None

    def update_weather(self):
        places = list(self.ctl.wx_places)
        data, statuses = {}, {}
        for place in places:
            try:
                loc = self.locate_cached(place)
                data[place] = dict(fetch_weather(loc), place=place, at=time.time())
                statuses[place] = loc["title"]
            except Exception as e:
                statuses[place] = f"error: {e}"
                if place in self.wxs:
                    data[place] = self.wxs[place]           # keep the last good one (it's hidden after 3 hours)
        self.wxs, self.wx_statuses = data, statuses
        self.wx_status = statuses.get(places[0], "")

    def wxs_fresh(self):
        """Weather per town in the phone's order, leaving out any that is 3+ hours old."""
        now = time.time()
        return {p: self.wxs[p] for p in self.ctl.wx_places if p in self.wxs and (self.demo or now - self.wxs[p]["at"] <= 3 * 3600)}

    def screen_list(self, kind=None):
        """Held items, one at a time: ("sports"|"pl", card), ("stock", (label, color, quotes)), ("wx", (weather, "now"|"forecast", start)).
        Only for the sections whose style isn't Scroll."""
        ctl, out = self.ctl, []
        if kind in (None, "sports") and ctl.sports_on and ctl.sports_style != "scroll":
            out += [("sports", c) for c in self.sports_cards]
        if kind in (None, "pl") and ctl.pl_style != "scroll":
            out += [("pl", c) for c in self.pl_cards]
        if kind in (None, "stock") and ctl.stocks_on and ctl.stock_style != "scroll":
            for label, color, quotes in stock_groups(self, ctl):
                n = stock_per_screen()
                for i in range(0, len(quotes), n):
                    out.append(("stock", (label, color, quotes[i:i + n])))
        if kind in (None, "wx") and ctl.wx_on and ctl.wx_style != "scroll":
            for wx in self.wxs_fresh().values():
                if "now" in ctl.wx_parts:
                    out.append(("wx", (wx, "now", 0)))
                if "forecast" in ctl.wx_parts:
                    per = wx_days_per_screen()
                    for i in range(0, min(ctl.wx_days, len(wx["days"])), per):      # a short strip shows the days in pages
                        out.append(("wx", (wx, "forecast", i)))
        return out

    def shown_games(self):
        """Games on the ticker: the ones picked on the phone (incl. My teams / Live / Close),
        or every game in the chosen sports when nothing is picked."""
        ctl = self.ctl
        if not ctl.sports_on:                            # scores switched off on the phone
            return [], True
        if ctl.game_ids:
            return self.picked(), True
        return visible_games(self.games, ctl.leagues, ctl.days), True

    def build_strip(self):
        ctl = self.ctl
        games, raw = self.shown_games()
        self.warn = wx_alert_segments(self, ctl)                        # active warnings go first
        pl_core = pl_segments(self.pl, ctl)
        pl = (pl_core if ctl.pl_style == "scroll" else []) + extra_segments(self, ctl)      # ParlayLabs, then stocks, then weather
        build = time_segments if ctl.group == "time" else score_segments
        game_segs = build(games, None, ctl.show_odds(), "week", raw) if ctl.sports_style == "scroll" else []
        segs = self.warn + game_segs + pl         # warnings, games, then ParlayLabs, stocks, weather
        self.sports_cards = self.game_cards(games)
        self.pl_cards = make_cards(pl_core)[1:]                          # the clock card isn't one of them
        # nothing in the scroll but there are held items: they just keep cycling (no going back to a blank ticker)
        self.screens_alone = bool(self.screen_list()) and not segs and not (ctl.msg_in_scroll and ctl.message.strip())
        if not segs and not (ctl.sports_on or ctl.pl_on or ctl.stocks_on or ctl.wx_on):
            segs = [("Everything is switched off - turn something on from your phone", GRAY)]
        elif not segs and not self.screens_alone:
            segs = [("Waiting for the next update…", GRAY)]
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
        # Drawing the strip is the heavy part and this runs every second: skip it when nothing it's drawn from changed,
        # otherwise the redraw steals time from the display thread and the scroll hitches once a second.
        try:
            logos = len(os.listdir(LOGO_DIR)) if os.path.isdir(LOGO_DIR) else 0
        except OSError:
            logos = 0
        sig = (repr(segs), repr(self.sports_cards), repr(self.pl_cards), repr(self.pinned_games()), ctl.version, datetime.now().strftime("%H%M"), LOOK, logos)
        if sig == self._strip_sig:
            return
        self._strip_sig = sig
        self.strip = text_strip(segs)
        self.build_split(pl, build)
        # blink: a copy with the message blanked out, swapped in for the "off" moments
        self.strip_off = text_strip(segs, hide_msg=True) if ctl.msg_in_scroll and ctl.effect == "blink" else None
        # fireworks: which pixels are text, so sparks can go behind the words
        self.strip_mask = (self.strip.convert("L").point(lambda v: 255 if v > 0 else 0)
                           if ctl.msg_in_scroll and ctl.effect == "fireworks" else None)

    def pinned_games(self):
        """Games held on the top row (split screen), or [] for the normal full-height ticker."""
        ctl, now = self.ctl, []
        if not ctl.sports_on:
            return now
        live = lambda g: g["state"] == "in" or self.linger_left(g)
        if ctl.pin == "mine":
            now = [g for g in self.my_games() if live(g)]
        elif ctl.pin == "live":
            now = [g for g in self.games if live(g) and (not ctl.leagues or g["league"] in ctl.leagues)]
            now.sort(key=lambda g: (not mine(g)[0], not is_close(g), g["league"]))
        elif ctl.pin == "picked" and ctl.game_ids:
            # the games you tapped in the list go on top; My teams / Live / Close only add to the crawl underneath
            # (if you only turned those buttons on, they go on top instead)
            tapped = [g for g in (self.game(i) for i in ctl.game_ids if not i.startswith("@")) if g]
            now = tapped or self.picked()
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
        if not rest:                                     # you picked only the held games: the rest of your sports crawls underneath
            rest, raw = [g for g in visible_games(self.games, ctl.leagues, ctl.days) if g["id"] not in ids], False
        segs = self.warn + (build(rest, None, ctl.show_odds(), "week", raw) if ctl.sports_style == "scroll" else []) + pl
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

    def game_cards(self, games):
        """Each game as a big scoreboard card, in the same order as the scroll."""
        order = {"in": 0, "pre": 1, "post": 2}
        if self.ctl.group == "time":
            games = sorted(games, key=lambda g: (order[g["state"]],
                           (g.get("start") or 0) * (-1 if g["state"] == "post" else 1)))
        else:
            lg_order = {l: i for i, (l, _, _) in enumerate(LEAGUES)}
            games = sorted(games, key=lambda g: (lg_order.get(g["league"], 9), order[g["state"]], g.get("start") or 0))
        return [{"label": None, "segs": [], "msg": False, "game": g} for g in games]

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
        for key in list(MY_TEAMS):
            league, abbr = key
            if any(g["league"] == league and abbr in (g["away"], g["home"]) and g["state"] != "post"
                   for g in self.games):
                continue
            try:
                if key not in TEAM_IDS:                  # a team you added: find its ESPN id
                    league_teams(league)
                tid = TEAM_IDS.get(key)
                if not tid:
                    continue
                g = fetch_next_game(league, tid)
                if g:
                    self.next_games[key] = g
            except Exception:
                pass

    def game(self, gid):
        return next((g for g in self.games + list(self.next_games.values()) if g["id"] == gid), None)

    TEST_PLAYS = {"sack": "Sacked at {opp} 28 for -7 yards", "int": "Intercepted at {me} 40, returned 22 yards",
                  "fumble": "Fumble recovered by {me} at {opp} 31", "fg": "48 yard field goal is GOOD",
                  "big": "Pass complete for 41 yards to the {opp} 12"}

    def test_alert(self, team_key, kind):
        """The phone's Test button: a pretend alert for that team, shown right away."""
        if team_key == "WX|TEST":                       # pretend tornado warning: takeover now, and in the scroll for a minute
            soon = (datetime.now().astimezone() + timedelta(minutes=45)).isoformat()
            a = {"id": "test", "event": "Tornado Warning", "area": "Your county (test)", "ends": soon}
            self.wxa_test = (a, time.time() + 60)
            self.notices.insert(0, wx_notice(a))
            return
        if team_key == "STOCK|TEST":
            self.notices.insert(0, stock_notice({"sym": "NVDA", "name": "NVDA", "price": 181.23, "pct": -4.2}))
            return
        if team_key == "ANY|ALL":
            al = Alert("close", fake("NBA", "LAL", "BOS", 101, 99, "in", "4th 1:12"), None, ANY_INFO)
            al.at = 0
            self.watcher.queue.insert(0, al)
            return
        league, abbr = team_key.split("|")
        info = MY_TEAMS[(league, abbr)]
        opp = {"NFL": "MIN", "CFB": "IOWA", "NBA": "BOS", "CBB": "DUKE", "NHL": "TOR", "MLB": "CLE"}[league]
        if opp == abbr:
            opp = {"NFL": "GB", "CFB": "OSU", "NBA": "NY", "CBB": "UK", "NHL": "MTL", "MLB": "NYY"}[league]
        a, h, clock = {"NBA": (99, 101, "4th 2:31"), "CBB": (68, 70, "2nd 2:31"), "NHL": (2, 3, "3rd 4:12"),
                       "MLB": (4, 5, "Bot 9th")}.get(league, (17, 24, "4th 3:12"))   # (them, you): you're ahead
        g = fake(league, opp, abbr, a, h, "post" if kind == "win" else "in",
                 "Final" if kind == "win" else clock,
                 down="1st & Goal" if kind == "redzone" else "", spot=f"{opp} 9" if kind == "redzone" else "",
                 poss="home" if kind == "redzone" else "", redzone=kind == "redzone")
        g["play_text"] = self.TEST_PLAYS.get(kind, "").format(opp=opp, me=abbr)
        al = Alert(kind, g, abbr, info)
        al.at = 0                                        # skip the TV delay
        self.watcher.queue.insert(0, al)

    def next_alert(self):
        q = self.watcher.queue
        if not self.ctl.alerts:                              # the Team alerts switch: everything off
            q.clear()
            return None
        if not self.ctl.sports_on:                           # scores switched off: your favorite teams still alert, no matter what
            q[:] = [a for a in q if (a.game["league"], a.abbr) in MY_TEAMS]
        if q and time.time() - q[0].at >= self.ctl.tv_delay:   # hold it to line up with the TV
            return q.pop(0)
        return None

def demo_stocks(ctl):
    """Pretend quotes for --demo / --sim (no internet needed)."""
    sample = {"^GSPC": (6812.34, 0.42), "^DJI": (47921.10, 0.18), "^IXIC": (23104.55, -0.31), "AAPL": (278.52, 0.84),
              "NVDA": (181.23, -1.62), "TSLA": (412.80, 2.37), "MSFT": (487.10, 0.55), "BTC-USD": (97250.40, 1.12),
              "ETH-USD": (3412.75, -0.64), "SOL-USD": (241.18, 3.05), "DOGE-USD": (0.3842, -2.2)}
    quotes = []
    for sym in wanted_symbols(ctl):
        price, pct = sample.get(sym, (100.0, 0.0))
        quotes.append({"sym": sym, "name": STOCK_NAMES.get(sym, sym[:-4] if sym.endswith("-USD") else sym.lstrip("^")),
                       "price": price, "chg": price * pct / 100, "pct": pct})
    return {"quotes": quotes, "open": True, "at": time.time()}

def demo_movers():
    sample = [("NVDA", 181.23, -4.2), ("TSLA", 412.80, 5.6), ("AMD", 164.30, 3.9), ("INTC", 24.10, -3.1), ("PLTR", 81.50, 6.8),
              ("META", 601.20, -1.9), ("AAPL", 278.52, 0.84)]
    return {"quotes": [{"sym": x, "name": x, "price": pr, "chg": pr * pc / 100, "pct": pc} for x, pr, pc in sample], "at": time.time()}

def demo_penny():
    sample = [("SNDL", 1.62, 7.4), ("PLUG", 2.31, 5.1), ("NOK", 4.85, 2.2), ("BB", 3.9, -0.8), ("FCEL", 3.1, -4.6), ("AMC", 2.9, -3.2)]
    return {"quotes": [{"sym": x, "name": x, "price": pr, "chg": pr * pc / 100, "pct": pc} for x, pr, pc in sample], "at": time.time()}

def demo_weather(place):
    days = [("WED", 61, 41, 30, 60), ("THU", 71, 33, 25, 70), ("FRI", 2, 36, 24, 10), ("SAT", 3, 38, 27, 20), ("SUN", 0, 44, 31, 0)]
    return {"temp": 34, "feels": 27, "code": 3, "wind": 12, "wdir": "NW", "hi": 39, "lo": 28, "rain": 40, "place": place,
            "at": time.time(), "days": [{"name": n, "code": c, "hi": h, "lo": l, "rain": r} for n, c, h, l, r in days]}

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
STATIC_FILES = {"fonts/BarlowCondensed-Bold.ttf": "font/ttf", "fonts/Oswald-SemiBold.ttf": "font/ttf", "icon-32.png": "image/png", "icon-180.png": "image/png", "icon-192.png": "image/png",
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
                # the Pi's copy says "Bar Ticker Pi" on the home screen and tab; the shared demo page keeps "Bar Ticker"
                page = open(os.path.join(HERE, "index.html"), "rb").read()
                for tag in (b'apple-mobile-web-app-title" content="Bar Ticker"', b'application-name" content="Bar Ticker"', b"<title>Bar Ticker</title>"):
                    page = page.replace(tag, tag.replace(b"Bar Ticker", b"Bar Ticker Pi"))
                self.send(200, page, "text/html; charset=utf-8")
            elif path.lstrip("/") in STATIC_FILES:                # icon + home-screen bookmark files
                name = path.lstrip("/")
                body = open(os.path.join(HERE, name), "rb").read()
                if name == "manifest.json":
                    body = body.replace(b'"Bar Ticker"', b'"Bar Ticker Pi"')
                self.send(200, body, STATIC_FILES[name])
            elif path == "/api/wiring":
                rows = [[o, sp + 1, on] for o, sp, _, on in WIRING] or [[o, sp + 1, True] for o, sp, _ in PANEL_MAP]
                for o, sp in [(2, 1), (2, 2), (1, 1), (1, 2), (3, 1), (3, 2)]:      # always 5 spots: fill in the rest, off
                    if len(rows) >= 5:
                        break
                    if not any(r[0] == o and r[1] == sp for r in rows):
                        rows.append([o, sp, False])
                self.send(200, {"map": rows[:5], "flip": PANELS_FLIPPED})
            elif path == "/api/state":
                games = [dict(g, mine=bool(mine(g)[0]), close=is_close(g), linger=feed.linger_left(g))
                         for g in feed.games]
                ids = {g["id"] for g in games}
                games += [dict(g, mine=True, close=False, linger=0) for g in feed.my_games()
                          if g["id"] not in ids]    # my teams' next games further out
                stale = time.time() - feed.last_ok           # nothing from ESPN for a while = likely blocked
                feed_ok = feed.demo or stale < max(90, 6 * ctl.refresh)
                teams = [{"key": f"{lg}|{ab}", "league": lg, "abbr": ab, "name": nm, "c1": rgb_hex(c1), "c2": rgb_hex(c2),
                          "kinds": ALERT_KINDS[lg],
                          "themes": {k: ctl.theme(f"{lg}|{ab}|{k}") for k in ALERT_KINDS[lg]}}
                         for (lg, ab), (nm, c1, c2) in MY_TEAMS.items()]
                teams.insert(0, {"key": "ANY|ALL", "league": "ANY", "abbr": "ALL", "name": "EVERY GAME", "c1": "#ffaa00",
                                 "c2": "#ffffff", "kinds": ["close"], "themes": {"close": ctl.theme(ANY_CLOSE)}})
                self.send(200, dict(ctl.as_dict(), games=games, has_image=bool(ctl.lib.ready_ids()), alert_teams=teams,
                                    pictures=ctl.lib.list(), pic_version=ctl.lib.version,
                                    feed_ok=feed_ok, feed_error=feed.last_error,
                                    wx_status=feed.wx_status, wx_statuses=feed.wx_statuses, stock_bad=feed.stock_bad,
                                    wxa_status=feed.wxa_status, wxa_now=[a["event"] for a in feed.alerts_now()],
                                    last_ok=int(stale)))
            elif path == "/api/frame.png":
                buf = io.BytesIO(); ctl.last_frame.save(buf, "PNG")
                self.send(200, buf.getvalue(), "image/png")
            elif path == "/api/pictures":
                self.send(200, ctl.lib.list())
            elif path == "/api/teams":                       # every team in a league, for "Add a team"
                lg = (self.path.split("league=", 1)[1].split("&")[0] if "league=" in self.path else "NFL").upper()
                try:
                    self.send(200, league_teams(lg))
                except Exception as e:
                    self.send(502, {"error": f"couldn't get the {lg} teams from ESPN ({e})"})
            elif path.startswith("/api/pictures/") and path.endswith("/file"):
                # the picture itself (GIF / photo / shrunk video), for "Save my alerts" on a laptop
                it = ctl.lib.get(path[len("/api/pictures/"):-len("/file")])
                if not it or it.get("status") != "ready":
                    return self.send(404, {"error": "not found"})
                ext = os.path.splitext(it["file"])[1].lower()
                ctype = {".gif": "image/gif", ".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4"}.get(ext, "application/octet-stream")
                with open(ctl.lib.path(it), "rb") as fh:
                    self.send(200, fh.read(), ctype)
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
                elif path in ("/api/update", "/api/restart"):   # the phone's Update / Restart buttons
                    here, msg = os.path.dirname(os.path.abspath(__file__)), "Restarting"
                    if path == "/api/update":
                        before = subprocess.run(["git", "-c", "safe.directory=*", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True).stdout.strip()
                        r = subprocess.run(["git", "-c", "safe.directory=*", "pull", "--ff-only", "-q"], cwd=here, capture_output=True, text=True, timeout=90)
                        if r.returncode:
                            return self.send(500, {"error": "Update failed: " + ((r.stderr or r.stdout).strip()[-160:] or "git pull")})
                        after = subprocess.run(["git", "-c", "safe.directory=*", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True).stdout.strip()
                        msg = "Updated, restarting" if before != after else "Already up to date, restarting"
                    def restart():
                        time.sleep(1.0)                       # let the answer reach the phone first
                        if subprocess.run(["systemctl", "restart", "ticker"], capture_output=True).returncode:
                            os.execv(sys.executable, [sys.executable] + sys.argv)      # not running as a service: restart in place
                    threading.Thread(target=restart, daemon=True).start()
                    return self.send(200, {"restarting": True, "msg": msg})
                elif path == "/api/wiring":                  # Wiring on the phone page: save panels.txt, restart
                    d = json.loads(body or b"{}")
                    rows = [(int(r[0]), int(r[1]), bool(r[2]) if len(r) > 2 else True) for r in d["map"]][:5]
                    on = [(o, sp) for o, sp, o_n in rows if o_n]
                    if not on:
                        return self.send(400, {"error": "Turn at least one panel on"})
                    if any(not (1 <= o <= 3 and 1 <= sp <= 2) for o, sp, _ in rows) or len(set(on)) != len(on):
                        return self.send(400, {"error": "Each panel that's on needs its own port + place (port 1-3, 1st or 2nd)"})
                    with open(os.path.join(HERE, "panels.txt"), "w") as f:
                        f.write(f"{len(on)} map " + " ".join(f"{o}:{sp}" + ("" if o_n else "x") for o, sp, o_n in rows)
                                + (" flip" if d.get("flip") else "") + "\n")
                    def restart_w():
                        time.sleep(1.0)
                        if subprocess.run(["systemctl", "restart", "ticker"], capture_output=True).returncode:
                            os.execv(sys.executable, [sys.executable] + sys.argv)
                    threading.Thread(target=restart_w, daemon=True).start()
                    return self.send(200, {"restarting": True, "msg": "Wiring saved, restarting"})
                elif path == "/api/panel-check":             # label every panel with its Bonnet port for 60 s
                    ctl.port_test_until = 0 if getattr(ctl, "port_test_until", 0) > time.time() else time.time() + 60
                    return self.send(200, {"on": ctl.port_test_until > time.time(),
                                           "panels_txt": f"{PANELS}{' right' if PI_AT_RIGHT else ''}{' flip' if PANELS_FLIPPED else ''}",
                                           "map": PANEL_MAP})
                elif path == "/api/test-alert":              # preview an alert theme on the ticker
                    d = json.loads(body or b"{}")
                    feed.test_alert(d["team"], d["kind"])
                elif path == "/api/load-github":             # "Load from GitHub": get alerts/alerts.ticker right now
                    try:
                        data = urllib.request.urlopen(ALERTS_URL + f"?t={int(time.time())}", timeout=30).read()
                    except Exception as e:
                        return self.send(502, {"error": f"couldn't get it from GitHub ({e})"})
                    n, p = load_alerts_pack(ctl, data)
                    return self.send(200, {"themes": n, "pictures": p})
                elif path == "/api/theme":                   # save one alert theme: {"key": "NFL|DET|td", "theme": {...}}
                    d = json.loads(body or b"{}")
                    themes = dict(ctl.themes)
                    themes[d["key"]] = dict(themes.get(d["key"], {}), **(d.get("theme") or {}))
                    if d.get("reset"):
                        themes.pop(d["key"], None)
                    ctl.update({"themes": themes})
                    ctl.warm_themes()
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
class Pacer:
    """Whole pixels to move since the last frame, from the real clock. Sleeping a fixed time per frame drifts and
    jitters (the loop's own work isn't counted), which shows up as stutter; this never loses time, so the crawl
    moves exactly `speed` pixels per second, one pixel at a time."""
    def __init__(self):
        self.t, self.frac = time.monotonic(), 0.0
    def step(self, speed):
        now = time.monotonic()
        dt, self.t = now - self.t, now
        if dt > 0.25:                      # the scroll wasn't running (alert, picture, ...): start again smoothly
            self.frac = 0.0
            return 0
        self.frac += dt * speed
        n = int(self.frac)
        self.frac -= n
        return n

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
    try:
        from rgbmatrix import RGBMatrix, RGBMatrixOptions
    except ImportError:                                  # LED library not installed yet: run anyway, the page preview still works
        print("LED library (rgbmatrix) not found; running without the LEDs. Install it with README step 3.")
        return SimMatrix()
    o = RGBMatrixOptions()
    o.rows, o.cols = ROWS, COLS
    o.chain_length = max(spot for _, spot, _ in PANEL_MAP) + 1
    o.parallel = max(out for out, _, _ in PANEL_MAP)
    # Triple Bonnet: the library's standard pinout, 3 outputs in parallel.
    # Single Bonnet: "adafruit-hat" pinout (solder its "E" jumper for 64-tall panels).
    o.hardware_mapping = "regular" if BONNET == "triple" else "adafruit-hat"
    o.brightness = ctl.brightness
    o.gpio_slowdown = GPIO_SLOWDOWN       # stops glitches/sparkle on the panels
    o.pwm_bits = PWM_BITS                 # fewer color steps = much faster refresh (128x64 panels need it)
    try:
        o.pwm_dither_bits = 1             # a bit more refresh, the eye can't see the difference
    except AttributeError:                # older LED library: skip it
        pass
    o.drop_privileges = False
    try:
        return RGBMatrix(options=o)
    except Exception as e:                # a setting the LED library didn't like: fall back to safe defaults
        print("LED setup failed with tuned settings:", e, "- retrying with defaults")
        try:
            o2 = RGBMatrixOptions()
            o2.rows, o2.cols, o2.chain_length, o2.parallel = o.rows, o.cols, o.chain_length, o.parallel
            o2.hardware_mapping, o2.brightness, o2.gpio_slowdown = o.hardware_mapping, ctl.brightness, 4
            o2.drop_privileges = False
            return RGBMatrix(options=o2)
        except Exception as e2:           # still no LEDs: keep the control page running anyway
            print("LED setup failed:", e2, "- running without the LEDs")
            return SimMatrix()

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

def port_test_image():
    """Panel check: skips the panel order completely. Every spot on every Bonnet output gets its own label
    ("PORT 2" + "1st" / "2nd" in the chain), so whatever each panel shows is exactly where its ribbon goes.
    A panel that stays dark here has no signal (ribbon, IN/OUT, power) - it's not the panel order."""
    chain = max(spot for _, spot, _ in PANEL_MAP) + 1
    outs = max(out for out, _, _ in PANEL_MAP)
    img = Image.new("RGB", (COLS * chain, ROWS * outs))
    d = ImageDraw.Draw(img)
    cols = [(255, 60, 60), (0, 220, 90), (60, 160, 255)]
    for o in range(outs):
        for sp in range(chain):
            c, x0, y0 = cols[o % 3], sp * COLS, o * ROWS
            d.rectangle([x0, y0, x0 + COLS - 1, y0 + ROWS - 1], outline=c, width=2)
            centered(d, f"PORT {o + 1}", font(30), c, cx=x0 + COLS // 2, cy=y0 + 22)
            centered(d, ("1st", "2nd", "3rd")[sp] if sp < 3 else f"#{sp + 1}", font(22), (255, 255, 255),
                     cx=x0 + COLS // 2, cy=y0 + 48)
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
        if time.time() < getattr(ctl, "port_test_until", 0):   # "Panel check" button on the phone page
            canvas.SetImage(port_test_image())
        else:
            canvas.SetImage(to_panels(frame))
        canvas = matrix.SwapOnVSync(canvas)
        now_ = time.monotonic()
        if now_ - show.last_copy >= 0.25:           # the phone's live preview only needs a few frames a second
            show.last_copy, ctl.last_frame = now_, frame.copy()

    show.last_copy = 0.0
    x, seen, img_i = W, -1, 0
    card_i, card_t0 = 0, time.time()
    player = pictures.Player(ctl.lib, W, ROWS)          # Picture mode: one picture or the slideshow
    inter, lap, last_pic, pic_n = None, False, time.time(), 0   # pictures popping up in the ticker
    inter_last = None
    show_t0, show_n, show_inter = time.time(), 0, None  # Shows: on their own, or popping up in the ticker
    pacer = Pacer()
    scr_inter = None                                    # weather / stock screens held on the strip for a few seconds
    KINDS = ("sports", "pl", "stock", "wx")
    scr_state = {k: {"last": time.time(), "lap": False} for k in KINDS}

    def item_len(kind, data):
        return ctl.stock_secs if kind == "stock" else ctl.wx_secs if kind == "wx" else card_length(data, ctl)

    def item_frame(kind, data, t):
        if kind == "stock": return stock_screen_frame(data)
        if kind == "wx": return wx_screen_frame(data[0], data[1], ctl.wx_days, data[2])
        return card_frame(data, t, ctl)

    def mark_lap():
        for st in scr_state.values():
            st["lap"] = True

    while True:
        nt = feed.next_notice()
        if nt:                                    # severe weather / big stock move: takeover, then back to what was showing
            start = time.time()
            while (t := time.time() - start) < nt.secs:
                show(notice_frame(nt, t)); time.sleep(1 / 30)
            x, inter, last_pic = W, None, time.time()
        al = feed.next_alert()
        th = ctl.theme(al.key) if al and (al.key == ANY_CLOSE or (al.game["league"], al.abbr) in MY_TEAMS) else None
        if al and (th is None or th["on"]):       # takeover, then go back to whatever was showing
            start, secs = time.time(), alert_len(th) if th else ALERT_SECONDS
            while (t := time.time() - start) < secs:
                show(alert_frame(al, t, th, ctl.lib, ctl.pic_boost)); time.sleep(1 / 30)
            x, inter, last_pic = W, None, time.time()
        if ctl.version != seen:                   # something changed on the phone
            seen, x, img_i = ctl.version, W, 0
            card_i, card_t0 = 0, time.time()
            inter, lap, last_pic = None, False, time.time()
            show_t0, show_inter, scr_inter = time.time(), None, None
            feed.build_strip()                    # league picks may have changed

        mode = ctl.mode
        shuffle = ctl.pic_order == "shuffle"

        # ---- weather / stock screens: held for a few seconds, then back to the ticker (or they just stay if nothing else is on)
        if mode == "scroll" and show_inter is None and inter is None:
            if scr_inter is None:
                if feed.screens_alone:
                    scr_inter = {"alone": True, "i": 0, "start": time.time(), "from": frame.copy(), "queue": []}
                else:
                    for kind in KINDS:
                        queue = feed.screen_list(kind)
                        every, st = getattr(ctl, kind + "_every"), scr_state[kind]
                        if queue and (st["lap"] if every < 0 else time.time() - st["last"] >= every * 60):
                            st["lap"] = False
                            scr_inter = {"alone": False, "i": 0, "start": time.time(), "from": frame.copy(), "queue": queue}
                            break
            if scr_inter is not None:
                if scr_inter["alone"]:
                    scr_inter["queue"] = feed.screen_list()
                queue = scr_inter["queue"]
                if not queue or (scr_inter["alone"] and not feed.screens_alone) or (not scr_inter["alone"] and scr_inter["i"] >= len(queue)):
                    for st in scr_state.values():
                        st["last"], st["lap"] = time.time(), False
                    scr_inter = None
                else:
                    kind, data = queue[scr_inter["i"] % len(queue)]
                    t = time.time() - scr_inter["start"]
                    length = item_len(kind, data)
                    if t >= length:
                        scr_inter["i"] += 1
                        scr_inter["start"], scr_inter["from"] = time.time(), frame.copy()
                        continue
                    show(with_motion(item_frame(kind, data, t), t, length, getattr(ctl, kind + "_style")))
                    time.sleep(1 / 40)
                    continue

        # ---- a show popping up in the ticker (after each lap, or every few minutes); they take turns
        tick_shows = [sh for sh in ctl.shows if sh["in_ticker"] and sh["steps"]]
        if mode == "scroll" and show_inter is None and inter is None and ctl.show_every and tick_shows:
            due = lap if ctl.show_every < 0 else time.time() - last_pic >= ctl.show_every * 60
            if due:
                show_inter = {"show": tick_shows[show_n % len(tick_shows)], "start": time.time(), "from": frame.copy()}
                show_n += 1
                lap = False
        if show_inter is not None:
            t = time.time() - show_inter["start"]
            sh = show_inter["show"]
            if t >= show_len(sh):
                show_inter, last_pic = None, time.time()
            else:
                img = show_frame(sh, t, ctl.lib, ctl.pic_boost)
                if t < 0.4:                       # fade in from the ticker
                    img = Image.blend(show_inter["from"], img, t / 0.4)
                show(img); time.sleep(1 / 30)
                continue
        if mode == "show":                        # a show on its own, looping
            sh = next((z for z in ctl.shows if z["id"] == ctl.show_id), None)
            if sh:
                show(show_frame(sh, time.time() - show_t0, ctl.lib, ctl.pic_boost)); time.sleep(1 / 30)
                continue

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
            n = pacer.step(ctl.speed)
            if n == 0:
                time.sleep(0.002)
                continue
            x -= n
            img = Image.new("RGB", (W, ROWS))
            img.paste(pinned_frame(feed.pinned, t, ctl), (0, 0))
            if x < -bottom.width or x > W:
                x, lap = W, True
                mark_lap()
            img.paste(bottom, (x, ROWS - BOTTOM_H))
            show(img)
        else:                                     # scroll everything
            n = pacer.step(ctl.speed)
            if n == 0:
                time.sleep(0.002)
                continue
            x -= n
            if x < -feed.strip.width:
                x, lap = W, True
                mark_lap()
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
    if kind in ("wx", "stock"):                   # severe-weather and stock-mover takeovers
        soon = (datetime.now().astimezone() + timedelta(minutes=45)).isoformat()
        nt = wx_notice({"id": "t", "event": "Tornado Warning", "area": "Hillsdale; Lenawee", "ends": soon}) if kind == "wx"             else stock_notice({"sym": "NVDA", "name": "NVDA", "price": 181.23, "pct": -4.2})
        save_gif([notice_frame(nt, i / 20) for i in range(int(nt.secs * 20))], f"alert-{kind}.gif", 50)
        return
    games = {"td":    fake("NFL", "MIN", "DET", 17, 24, "in", "4th 3:12"),
             "win":   fake("NFL", "MIN", "DET", 17, 31, "post", "Final"),
             "close": fake("NBA", "DET", "BOS", 101, 99, "in", "4th 2:31")}
    g = games[kind]
    al = Alert(kind, g, "DET", MY_TEAMS[(g["league"], "DET")])
    save_gif([alert_frame(al, i / 20) for i in range(ALERT_SECONDS * 20)], f"alert-{kind}.gif", 50)

# ---- alerts made on the laptop: "Send to GitHub" puts alerts/alerts.ticker in the repo, the Pi loads it
def load_alerts_pack(ctl, data):
    """Adds the teams, pictures and alert looks from an alerts file. Safe to run again: pictures it
    already brought over are reused, looks are replaced with the file's."""
    import base64, hashlib
    pack = json.loads(data)
    if pack.get("kind") != "bar-ticker-alerts":
        raise ValueError("not a Bar Ticker alerts file")
    try:
        done = json.load(open(ALERTS_LOADED))
    except Exception:
        done = {}
    favs = list(ctl.favorites)
    for t in pack.get("favorites") or []:
        if not any(f["league"] == t.get("league") and f["abbr"] == t.get("abbr") for f in favs):
            favs.append(t)
    ctl.update({"favorites": favs})
    pmap, have = done.get("map", {}), set(i["id"] for i in ctl.lib.list())
    exts = {"image/gif": ".gif", "image/png": ".png", "image/jpeg": ".jpg", "video/mp4": ".mp4", "video/quicktime": ".mov"}
    for m in pack.get("pictures") or []:
        if pmap.get(m["id"]) in have:
            continue
        head, _, b64 = m["data"].partition(",")
        mime = head[5:].split(";")[0]
        tmp = tempfile.mkstemp(dir=PIC_DIR, suffix=".upload")[1]
        with open(tmp, "wb") as fh:
            fh.write(base64.b64decode(b64))
        try:
            it = ctl.lib.add_file(tmp, (m.get("name") or "Picture") + exts.get(mime, ""), mime, m.get("fit") if m.get("fit") in pictures.FITS else "fill")
            ctl.lib.edit(it["id"], {k: m[k] for k in ("fit", "zoom", "fx", "fy") if m.get(k) is not None})
            pmap[m["id"]] = it["id"]
        except Exception as e:
            print("alerts file: skipped a picture:", e)
    themes = dict(ctl.themes)
    for key, th in (pack.get("themes") or {}).items():
        themes[key] = dict(th, pics=[pmap[i] for i in th.get("pics") or [] if i in pmap])
    ctl.update({"themes": themes})
    ctl.warm_themes()
    done.update(map=pmap, hash=hashlib.sha1(data if isinstance(data, bytes) else data.encode()).hexdigest(), saved=pack.get("saved"))
    with open(ALERTS_LOADED, "w") as fh:
        json.dump(done, fh)
    return len(pack.get("themes") or {}), len(pack.get("pictures") or [])

def watch_alerts_file(ctl):
    """Loads alerts/alerts.ticker whenever a new one shows up (the Pi pulls from GitHub every 2 minutes)."""
    import hashlib
    while True:
        try:
            if os.path.exists(ALERTS_FILE):
                data = open(ALERTS_FILE, "rb").read()
                try:
                    old = json.load(open(ALERTS_LOADED)).get("hash")
                except Exception:
                    old = None
                if hashlib.sha1(data).hexdigest() != old:
                    n, p = load_alerts_pack(ctl, data)
                    print(f"loaded {n} alert looks and {p} pictures from GitHub")
        except Exception as e:
            print("alerts file:", e)
        time.sleep(30)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="no LED panels: run the control page and a pretend display")
    ap.add_argument("--demo", action="store_true", help="use local picks.json + fake games")
    ap.add_argument("--port", type=int, help="control page port (default 80, or 8080 with --sim)")
    ap.add_argument("--preview", action="store_true", help="write preview.gif")
    ap.add_argument("--test-alert", choices=["td", "win", "close", "wx", "stock"], help="write an alert GIF")
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
            threading.Thread(target=watch_alerts_file, args=(ctl,), daemon=True).start()
            start_web(ctl, feed, args.port or (8080 if args.sim else WEB_PORT))
            run_display(ctl, feed, SimMatrix() if args.sim else open_matrix(ctl))

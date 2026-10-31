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
import argparse, io, json, os, random, time, threading, urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image, ImageDraw, ImageFont, ImageSequence

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
}

PARLAYLABS = "https://callis11.github.io/parlay-lab"
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
SCROLL_CHOICES = ["NFL", "NBA", "CFB", "CBB", "NHL", "MLB", "PL"]   # PL = ParlayLabs results
CFB_TOP25_ONLY = True      # college slates are huge; scroll only ranked teams + my teams

WHITE, GOLD, GREEN, RED, GRAY = (255,255,255), (255,190,0), (0,230,80), (255,40,40), (130,130,130)
ODDS_COLOR, DOT = (0, 200, 200), ("  •  ", (60, 60, 60))

# ------------------------------------------------------------------ control state (set from the phone)
class Control:
    KEYS = {"mode": "scroll", "leagues": [], "game_ids": [], "rotate": 10, "message": "GO LIONS!", "color": "#ffbe00",
            "brightness": 60, "tv_delay": 0, "alerts": True, "fit": "contain",
            "refresh": REFRESH_SEC}

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
    st = ev["status"]
    odds = (comp.get("odds") or [{}])[0]
    return dict(
        id=f"{label}-{ev['id']}", league=label,
        away=a["team"]["abbreviation"], home=h["team"]["abbreviation"],
        ascore=int(a.get("score") or 0), hscore=int(h.get("score") or 0),
        state=st["type"]["state"], detail=st["type"]["shortDetail"],
        period=st.get("period", 0), clock=clock_secs(st.get("displayClock")),
        ranked=any(c.get("curatedRank", {}).get("current", 99) <= 25 for c in (a, h)),
        spread=odds.get("details"), total=odds.get("overUnder"),
    )

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

def score_segments(games, only=None):
    """Scrolling text for every game: live/final scores, and odds for games not started."""
    segs = []
    for label, _, color in LEAGUES:
        if only and label not in only:
            continue
        lg = [g for g in games if g["league"] == label]
        if label in ("CFB", "CBB") and CFB_TOP25_ONLY:
            lg = [g for g in lg if g["ranked"] or mine(g)[0]]
        lg.sort(key=lambda g: {"in": 0, "pre": 1, "post": 2}[g["state"]])   # live games first
        if not lg:
            continue
        segs.append((f"  {label}  ", color))
        for g in lg:
            gold = bool(mine(g)[0])
            if g["state"] == "pre":
                segs.append((f"{g['away']} @ {g['home']} {short_time(g['detail'])}", GOLD if gold else GRAY))
                line = "  ".join(x for x in (g["spread"], f"O/U {g['total']}" if g["total"] else None) if x)
                if line:
                    segs.append((f"  {line}", ODDS_COLOR))
            else:
                col = GOLD if gold else (WHITE if g["state"] == "in" else (180, 180, 180))
                segs.append((f"{g['away']} {g['ascore']}  {g['home']} {g['hscore']}  {g['detail']}", col))
            segs.append(DOT)
    return segs

def parlaylabs(loader=None, sports=None):
    """Season record + latest graded slips from ParlayLabs picks.json files."""
    loader, segs = loader or get_json, []
    for sport, sub in (sports or PL_SPORTS).items():
        try:
            slips = list(loader(f"{PARLAYLABS}{sub}/picks.json")["slips"].values())
        except Exception:
            continue
        done = [s for s in slips if s.get("status") in ("won", "lost")]
        if not done:
            continue
        won = [s for s in done if s["status"] == "won"]
        # every pick is a $10 reference bet: win pays 10*decimal odds, loss is -10
        profit = sum(10 * s["dec"] - 10 for s in won) - 10 * (len(done) - len(won))
        segs.append((f"  PARLAYLABS {sport}  ", GOLD))
        segs.append((f"{len(won)}-{len(done)-len(won)}  ", WHITE))
        segs.append((f"{'+' if profit >= 0 else '-'}${abs(profit):.0f}  ", GREEN if profit >= 0 else RED))
        for s in reversed(sorted(done, key=lambda s: s.get("graded", 0))[-3:]):
            hit = s["status"] == "won"
            segs.append((f"{s['name']} {'WON' if hit else 'LOST'}", GREEN if hit else RED))
            if hit:
                segs.append((f" ${10*s['dec']:.0f}", GREEN))
            segs.append(DOT)
        live = [s for s in slips if s.get("status") == "open"]
        if live:
            segs.append((f"{len(live)} parlays live  ", (0, 180, 255)))
    return segs

# ------------------------------------------------------------------ alerts
class Alert:
    def __init__(self, kind, game, abbr, team):
        self.kind, self.game, self.abbr, self.at = kind, game, abbr, time.time()
        self.name, self.c1, self.c2 = team
        g = game
        self.score = f"{g['away']} {g['ascore']}  {g['home']} {g['hscore']}  {g['detail']}"
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
            rule = CLOSE[g["league"]]
            late = g["state"] == "in" and (g["period"] > rule["period"] or
                   (g["period"] == rule["period"] and g["clock"] <= rule["clock"]))
            if late and abs(my_now - opp) <= rule["margin"]:
                if g["id"] not in self.close_shown:
                    self.close_shown.add(g["id"])
                    self.queue.append(Alert("close", g, abbr, team))
            elif late:
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
        centered(d, word, fit_font(word), fg)
        if al.kind == "win":                             # confetti
            for _ in range(60):
                d.point((rng.randrange(W), rng.randrange(ROWS)),
                        fill=rng.choice([GOLD, WHITE, al.c2, GREEN]))
    else:
        pulse = int(t * 2) % 2 == 0                      # slow border pulse
        d.rectangle([0, 0, W - 1, ROWS - 1], outline=al.c1 if pulse else al.c2, width=2)
        centered(d, al.score, fit_font(al.score, start=20), amber if al.kind == "close" else GOLD)
    return img

def game_frame(g, rotate=0):
    """A single game, held on screen: away on the left, home on the right, clock in the middle."""
    img = Image.new("RGB", (W, ROWS))
    d = ImageDraw.Draw(img)
    pre = g["state"] == "pre"
    left  = g["away"] if pre else f"{g['away']} {g['ascore']}"
    right = g["home"] if pre else f"{g['hscore']} {g['home']}"
    mid1  = short_time(g["detail"]) if pre else g["detail"]
    mid2  = (g["spread"] or "") if pre else ""
    for size in range(24, 11, -1):                       # shrink until everything fits
        big, small = font(size), font(max(10, size // 2 + 1))
        mid_w = max(small.getlength(mid1), small.getlength(mid2))
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
    if mid2:
        centered(d, mid1, small, ODDS_COLOR, cy=9)
        centered(d, mid2, small, ODDS_COLOR, cy=23)
    else:
        parts = mid1.split(" ", 1) if small.getlength(mid1) > 70 and " " in mid1 else [mid1]
        for i, p in enumerate(parts):
            cy = ROWS // 2 if len(parts) == 1 else (9 if i == 0 else 23)
            centered(d, p, small, RED if live else GRAY, cy=cy)
    if rotate:                                           # thin bar along the top = time until the next game
        d.line([(0, 0), (int(W * (time.time() % rotate) / rotate), 0)], fill=(50, 50, 80))
    return img

def text_strip(segs, stamp=True):
    """Draws segments onto one long image; we scroll a window across it."""
    if stamp:
        segs = [(f"  {datetime.now():%-I:%M}  ", (0, 180, 255))] + (segs or [("No games right now", GRAY)])
    width = sum(int(FONT.getlength(t)) for t, _ in segs) + 4
    img = Image.new("RGB", (max(width, 1), ROWS))
    d = ImageDraw.Draw(img)
    x, y = 0, (ROWS - FONT_SIZE) // 2 - 2
    for text, color in segs:
        d.text((x, y), text, font=FONT, fill=color)
        x += int(FONT.getlength(text))
    return img

def message_image(ctl):
    txt, col = ctl.message or " ", hex_rgb(ctl.color)
    f = fit_font(txt, start=26)
    if f.getlength(txt) <= W - 8 and f.size >= 16:      # fits: hold it in the middle
        img = Image.new("RGB", (W, ROWS)); centered(ImageDraw.Draw(img), txt, f, col)
        return img, False
    return text_strip([("     " + txt + "     ", col)], stamp=False), True

# ------------------------------------------------------------------ feed
class Feed:
    """Pulls data in a background thread so the display never stutters."""
    def __init__(self, ctl, demo=False):
        self.ctl, self.demo, self.pl, self.games = ctl, demo, [], []
        self.last_ok, self.fails, self.last_error = time.time(), 0, ""
        self.strip = text_strip([("Loading…", GRAY)])
        self.watcher = AlertWatcher()
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        """Hot leagues (my team or the pinned game live) every FAST_SEC, everything else every ctl.refresh."""
        by_league, last, last_pl = {}, {}, 0
        while True:
            now = time.time()
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
                if now - last_pl > 120:           # ParlayLabs only needs a pull every ~2 min
                    self.pl, last_pl = parlaylabs() or self.pl, now
            self.build_strip()
            time.sleep(1)

    def build_strip(self):
        only = self.ctl.leagues                  # leagues picked on the phone (empty = everything)
        pl = self.pl if not only or "PL" in only else []
        self.strip = text_strip(pl + score_segments(self.games, only))

    def hot_leagues(self):
        return {g["league"] for g in self.games
                if g["state"] == "in" and (mine(g)[0] or g["id"] in self.ctl.game_ids)}

    def game(self, gid):
        return next((g for g in self.games if g["id"] == gid), None)

    def next_alert(self):
        q = self.watcher.queue
        if not self.ctl.alerts:
            q.clear()
            return None
        if q and time.time() - q[0].at >= self.ctl.tv_delay:   # hold it to line up with the TV
            return q.pop(0)
        return None

def demo_parlaylabs():
    try:
        return parlaylabs(loader=lambda url: json.load(open(os.path.join(HERE, "picks.json"))), sports={"NFL": ""})
    except Exception:
        return []

def fake(league, away, home, a, h, state, detail, spread=None, total=None, period=4, clock=180):
    return dict(id=f"{league}-{away}{home}", league=league, away=away, home=home, ascore=a, hscore=h,
                state=state, detail=detail, period=period, clock=clock, ranked=True, spread=spread, total=total)

DEMO_GAMES = [
    fake("NFL", "MIN", "DET", 17, 24, "in", "4th 3:12"),
    fake("NFL", "CHI", "GB", 10, 14, "in", "2nd 0:45", period=2),
    fake("NFL", "KC", "BUF", 0, 0, "pre", "Sun 4:25 PM EST", "BUF -2.5", 51.5),
    fake("NFL", "NYJ", "MIA", 20, 23, "post", "Final"),
    fake("NBA", "DET", "BOS", 0, 0, "pre", "7:30 PM EST", "BOS -6.5", 224.5),
    fake("CFB", "MSU", "IOWA", 21, 17, "in", "3rd 9:58", period=3),
    fake("CBB", "MSU", "DUKE", 0, 0, "pre", "Tue 9:00 PM EST", "DUKE -3", 148.5),
]

# ------------------------------------------------------------------ control page / API
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
                self.send(200, open(os.path.join(HERE, "control.html"), "rb").read(), "text/html; charset=utf-8")
            elif path == "/api/state":
                games = [dict(g, mine=bool(mine(g)[0])) for g in feed.games]
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

    x, seen, img_i, msg = W, -1, 0, None
    while True:
        al = feed.next_alert()
        if al:                                    # takeover, then go back to whatever was showing
            start = time.time()
            while (t := time.time() - start) < ALERT_SECONDS:
                show(alert_frame(al, t)); time.sleep(1 / 30)
            x = W
        if ctl.version != seen:                   # something changed on the phone
            seen, x, img_i, msg = ctl.version, W, 0, None
            feed.build_strip()                    # league picks may have changed

        mode = ctl.mode
        picked = [g for g in map(feed.game, ctl.game_ids) if g] if mode == "game" else []
        if picked:                                # one game holds; several take turns
            g = picked[int(time.time() // ctl.rotate) % len(picked)]
            show(game_frame(g, ctl.rotate if len(picked) > 1 else 0)); time.sleep(0.25)
        elif mode == "image" and ctl.frames:
            img, ms = ctl.frames[img_i % len(ctl.frames)]
            show(img); img_i += 1; time.sleep(ms / 1000)
        elif mode == "message":
            if msg is None:
                msg = message_image(ctl)
            img, scrolls = msg
            if scrolls:
                show(img, x); x = W if x < -img.width else x - 1; time.sleep(1 / SCROLL_SPEED)
            else:
                show(img); time.sleep(0.25)
        else:                                     # scroll everything
            strip = feed.strip
            show(strip, x)
            x -= 1
            if x < -strip.width:
                x = W
            time.sleep(1 / SCROLL_SPEED)

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

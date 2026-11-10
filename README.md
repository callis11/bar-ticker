# ParlayLabs Bar Ticker — Build Plan

A ~5.25 ft x 6.3 in full-color LED ticker (640 x 64 pixels, five P2.5 panels) for above the bar.
It scrolls live scores, odds and ParlayLabs results, flashes alerts for the Lions, Pistons and MSU, and is controlled from your phone.
With 64 pixels of height, games show as **two lines** (away team on top, home underneath, clock and odds on the right), and long messages and parlays wrap onto two lines instead of crawling.

## Shopping list (~$190–200)

| Part | Qty | ~Price | Notes |
|---|---|---|---|
| P2.5 128x64 HUB75 panel, 320 x 160 mm, 1/32 scan, **ICN2037** chip (AliExpress) | 5 | ~$68 + shipping | **Ordered.** 5 x 320 mm = 1600 mm (5.25 ft). All 5 from the same batch |
| Raspberry Pi 4 Model B, **1 GB** | 1 | ~$35 | The 1 GB and 2 GB Pi 4 kept their old price through the 2026 RAM price hikes; the ticker doesn't need more. A Pi 3B+ (~$35) also works (set `gpio_slowdown = 2`). Skip the Pi 5 (the LED library doesn't support it properly) and the Zero 2 W (too slow for 5 big panels) |
| Adafruit **Triple** LED Matrix Bonnet (#6358) | 1 | ~$10 | **Bought.** Plugs onto the Pi and drives the panels; has level shifters, no soldering. Works with the Pi 4 (code is set for it: `BONNET = "triple"`). The single RGB Matrix Bonnet (#3211) also works (`BONNET = "single"`, solder its E jumper) |
| 5 V 30 A power supply (150 W, e.g. Mean Well LRS-150-5) + 3-prong cord | 1 | ~$25 | **Bought** (cord: have it). Powers the panels (up to ~4 A each on full white; far less for a ticker). Make sure it's **5 V** out and 110 V in |
| USB-C pigtail cable (USB-C male to bare red/black wire, 3 A) | 1 | ~$6–8 | **Bought.** Powers the Pi from the same 5 V supply as the panels, so no separate Pi adapter |
| Multimeter | 1 | — | **Have it.** To set the supply to 5.1–5.2 V and check the wiring |
| 32 GB microSD card | 1 | ~$8 | |
| 18 AWG red/black wire + Wago lever nuts | 1 | ~$10 | Each panel comes with its own ribbon cable and power pigtail |
| Longer HUB75 ribbon cables (16-pin, 1 m) | 3 | ~$6–10 | **Ordered.** From the Bonnet (behind the middle) out to panels 1, 3 and 5. The short cables that come with the panels link 1→2 and 3→4 |
| 1x4 board or aluminum flat bar to mount on | 1 | ~$10 | |
| Smoked or black acrylic front (optional) | 1 | ~$15 | Makes it look like a real sports ticker. The frame you can cut on the CNC |

Backup plan if a panel batch ever comes with a chip the Pi can't drive (ICN2053, ICN2153, FM6363, FM6373, DP3246): a Colorlight 5A-75B receiving card, fed by the Pi over Ethernet.

## Wiring
1. Check the Triple Bonnet's **Address E** jumper is set to the **8th pin** (where HUB75E panels like ours have E). With the single Bonnet, solder its E jumper instead.
2. Ribbon cables, split over the Triple Bonnet's 3 outputs so each output only feeds 1–2 panels (about 2.5x faster refresh than one long chain = smoother, no flicker on phone cameras). Panels are numbered 1–5 left to right as you face the ticker; mount the Pi behind panel 3, and mount every panel the same way up (arrows on the back point from IN to OUT):
   - **Output 1** → panel 1 IN (long cable), panel 1 OUT → panel 2 IN
   - **Output 2** → panel 3 IN, panel 3 OUT → panel 4 IN
   - **Output 3** → panel 5 IN (long cable)
   - Then stop the ticker (`sudo systemctl stop ticker`) and run `sudo python3 ticker.py --test-panels`: each panel should show its number 1–5, upright, corner mark at top left. If any are out of order or upside down, send Claude a photo and it fixes `PANEL_MAP` in ticker.py (no rewiring needed)
3. Run 5 V and GND from the 30 A supply to **every** panel's power pigtail, not through the chain.
4. Power the Pi from the same supply with the USB-C pigtail: red to **+V**, black to **−V/COM**, a short wire straight from the supply's terminals. Turn the supply's **V-ADJ** screw so a multimeter reads **5.1–5.2 V** (the Pi 4 complains of low voltage at a plain 5.0 V; the panels are fine at 5.2 V). Never power the Pi through its GPIO pins. One wall plug runs everything, and the Pi boots straight into the ticker when it's switched on.

## Software setup (on the Pi)
```bash
# 1. Flash Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager.
#    In its settings: hostname "ticker", username "pi", turn on Wi-Fi (your home network) and SSH.
# 2. Install the LED library (Adafruit's script: pick "Bonnet", then "Quality")
curl https://raw.githubusercontent.com/adafruit/Raspberry-Pi-Installer-Scripts/main/rgb-matrix.sh > rgb-matrix.sh
sudo bash rgb-matrix.sh
# 3. Get the ticker and turn it on (it starts by itself on every boot after this)
sudo apt install -y git
git clone https://github.com/callis11/bar-ticker.git /home/pi/bar-ticker
sudo bash /home/pi/bar-ticker/pi/install.sh
```

**Automatic updates:** the Pi checks this GitHub repo every 2 minutes. When Claude pushes a change, the Pi pulls it and restarts the ticker by itself. Your phone settings (`settings.json`) and uploaded picture stay on the Pi and aren't touched by updates.

Handy commands on the Pi: `sudo systemctl restart ticker` (restart), `journalctl -u ticker -f` (see what it's doing), `journalctl -u ticker-update` (update history).

## Phone control page
On your home Wi-Fi, open **http://ticker.local** on your phone. In Safari, tap Share → **Add to Home Screen**: it saves as **Bar Ticker** with its own LED-scoreboard icon and opens like an app.
- **Refresh button** (↻, top right): the ticker pulls every score and ParlayLabs right away instead of waiting for its next check, and the page updates
- **Live preview** of what's on the ticker right now
- Three tabs: **Ticker**, **Picture**, **Message**
- **Ticker** (games + ParlayLabs in one place):
  - **Games**: sport chips (tap any mix, or All sports). With nothing picked, the ticker shows **every game** in those sports. Narrow it with **My teams**, **Live games**, **Close games** (on/off toggles you can combine), **Select all**, or by tapping individual games; **Clear** goes back to every game. The list shows the **top 5** (my teams, close, live, soonest, finals) with **Show all ▾** to see the rest; checked games always stay visible
    - **My teams** always shows all four (Lions, Pistons, MSU football, MSU basketball): each one's game if it's on, today or just ended (kept 15 min), otherwise its **next game** with opponent, day, date, time and odds once books post them (looked up from each team's ESPN schedule every 30 min; tagged NEXT)
    - **Live games** follows every game in progress in your sports, adds games as they start and keeps each on 15 min after it ends ("ON 12 MIN")
    - **Close games** follows games that are late and within one score in your sports (football 4th qtr under 8:00 within 8, NBA 4th qtr under 5:00 within 6, college hoops 2nd half under 5:00 within 6, NHL 3rd period under 10:00 within 1, MLB 7th inning on within 2); red CLOSE tag in the list
    - **Order**: By league, or By start time (LIVE, then each slot like "SUN 1:00 PM", then FINAL). **Days**: Today or This week
  - **ParlayLabs**: Record, Top parlays and/or Top overs (all off = left out), mixed in after the games. Pick how many (top 3/5/8), which sports and which prop types
  - **Odds**: Show odds switch, plus Moneyline, Over/Under and Spread picks, for games that haven't started
  - **How it moves**: **Scroll** (the classic crawl) or one item at a time with **Cut**, **Fade**, **Flash**, **Slide up** or **Slide left**. In one-at-a-time styles, games show as a big scoreboard and ParlayLabs items as a card with a small label on top. **Speed** slider for crawling, and **Each item stays** 3, 5, 8 or 15 sec
- **Picture**: send a photo or GIF from your phone (GIFs animate). Pick "Fit whole picture" or "Fill the strip"
- **Message**: type anything, pick a color and a style (Steady, Scroll, Blink, Rainbow, Fireworks), then where it shows, in any mix: **on its own**, **with the ticker** (comes around between leagues) and/or **over the picture** (on top of your photo or GIF with a black outline). Typing a new message updates it everywhere it's showing
- **Brightness**, **TV delay** (holds alerts so they line up with your TV), **score update speed**, **team alerts on/off** and **college rankings on/off**
- Your choices are saved, so the ticker comes back the same way after a power cut
- It only works on your home network. Reaching it from away would need something like Tailscale on the Pi

**Try it before the Pi arrives:** open the page from GitHub Pages (https://callis11.github.io/bar-ticker/, can also be added to the home screen) and it runs in demo mode with a pretend ticker drawn on the page. Every button works, there are test buttons for the touchdown, win and close-game alerts, and it shows today's real games when ESPN allows it. You can also run `python3 ticker.py --sim --demo` on a computer and open http://localhost:8080.

## What the script does now
- Scrolls every game today in the NFL, NBA, college football, college hoops (ranked teams + MSU), NHL and MLB, using ESPN's free scoreboard feed. Everything refreshes every 5 seconds by default. The Lions, Pistons, MSU and any games you picked always refresh every 5 seconds while live; the rest can be slowed on the phone page (5–60 sec). If ESPN stops answering for a while, the phone page shows a red warning so you know to slow it down
- Each game is a two-line block with a league-colored edge: away team and score on top, home underneath (leader bright, trailing team dimmer), and the clock (live, red), start time and O/U or spread (upcoming), or Final on the right. Before kickoff the moneylines sit where the scores go. Leagues and time slots show as colored labels
- Live games come first, then upcoming games with the odds you picked, then final scores
- Pulls from your ParlayLabs site every 10 minutes, for each sport. On the phone you pick any mix of:
  - **Record**: wins-losses, $10-bet profit, the last 3 graded parlays, how many are still live
  - **Top parlays**: this week's Best of the week and Lions parlays, each with its odds, its legs and what $10 pays
  - **Top overs**: the best player overs for games that haven't started (most likely first, priced -250 to +150, best line per player), with the best price and how often he's hit it lately. Lions/Pistons/MSU players go first, in gold
  - Also pick **how many** (top 3, 5 or 8), **which sports** (NFL, NBA, College FB, College BB, or all) and **which prop types** (TD, receptions, rec yds, rush yds, rush+rec, pass yds, pass TDs, points, rebounds, assists, threes, PRA, or all)
- Lions, Pistons and Michigan State games show in gold
- College football and basketball teams in the AP Top 25 show their rank in front, like **#12 MSU 21  #8 IOWA 17**, in the scroll, held games, one-at-a-time cards and the phone's games list (from ESPN, updates each week)
- **Team alerts:** the whole ticker flashes for 10 seconds, then goes back to whatever was showing (scroll, game, picture or message)
  - Touchdown (Lions, MSU football): flashes TOUCHDOWN! / LIONS! in team colors, then the score
  - Win (Lions, Pistons, MSU football and basketball): flashes the team name and WIN! with fireworks, then the final score
  - Close game: flashes CLOSE GAME in amber once a game gets tight late. Football: 4th quarter, under 8:00, within 8 points. NBA: 4th quarter, under 5:00, within 6. College hoops: 2nd half, under 5:00, within 6
- Test without LEDs: `python3 ticker.py --preview` (scroll) or `python3 ticker.py --test-alert td` (also `win`, `close`). Sample GIFs are in `previews/`

## Ideas for next scripts
- Leg tracker: show each live parlay leg turning green or red as games go final
- Team logo animations and short GIF clips between rotations (on a 640 x 64 strip, videos get cropped to a 10:1 band, so loops built for this shape look better)
- Two-line ParlayLabs in the scroll: parlay name and payout on top, legs underneath
- Night mode: dim or turn off at closing time

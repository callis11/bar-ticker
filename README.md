# ParlayLabs Bar Ticker — Build Plan

A ~5.25 ft x 6.3 in full-color LED ticker (640 x 64 pixels, five P2.5 panels) for above the bar.
It scrolls live scores, odds and ParlayLabs results, flashes alerts for the Lions, Pistons and MSU, and is controlled from your phone.
With 64 pixels of height, games show as **two lines** (away team on top, home underneath, clock and odds on the right), and long messages and parlays wrap onto two lines instead of crawling.

## Shopping list (~$190–200)

| Part | Qty | ~Price | Notes |
|---|---|---|---|
| P2.5 128x64 HUB75 panel, 320 x 160 mm, 1/32 scan, **ICN2037** chip (AliExpress) | 5 | ~$68 + shipping | **Ordered.** 5 x 320 mm = 1600 mm (5.25 ft). All 5 from the same batch |
| Raspberry Pi 4 Model B, **1 GB** | 1 | ~$35 | **Bought.** The 1 GB and 2 GB Pi 4 kept their old price through the 2026 RAM price hikes; the ticker doesn't need more. A Pi 3B+ (~$35) also works (set `gpio_slowdown = 2`). Skip the Pi 5 (the LED library doesn't support it properly) and the Zero 2 W (too slow for 5 big panels) |
| Adafruit **Triple** LED Matrix Bonnet (#6358) | 1 | ~$10 | **Bought.** Plugs onto the Pi and drives the panels; has level shifters, no soldering. Works with the Pi 4 (code is set for it: `BONNET = "triple"`). The single RGB Matrix Bonnet (#3211) also works (`BONNET = "single"`, solder its E jumper) |
| 5 V 30 A power supply (150 W, e.g. Mean Well LRS-150-5) + 3-prong cord | 1 | ~$25 | **Bought** (cord: have it). Powers the panels (up to ~4 A each on full white; far less for a ticker). Make sure it's **5 V** out and 110 V in |
| USB-C pigtail cable (USB-C male to bare red/black wire, 3 A) | 1 | ~$6–8 | **Bought.** Powers the Pi from the same 5 V supply as the panels, so no separate Pi adapter |
| Multimeter | 1 | — | **Have it.** To set the supply to 5.1–5.2 V and check the wiring |
| 32 GB microSD card | 1 | ~$8 | **Bought.** |
| 18 AWG red/black wire + Wago lever nuts | 1 | — | **Have it.** Each panel comes with its own ribbon cable and power pigtail |
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

**Automatic updates:** the Pi checks this GitHub repo every 2 minutes. When Claude pushes a change, the Pi pulls it and restarts the ticker by itself. Your phone settings (`settings.json`) and your picture library (`pictures/`) stay on the Pi and aren't touched by updates.

Handy commands on the Pi: `sudo systemctl restart ticker` (restart), `journalctl -u ticker -f` (see what it's doing), `journalctl -u ticker-update` (update history).

## Phone control page
On your home Wi-Fi, open **http://ticker.local** on your phone. In Safari, tap Share → **Add to Home Screen**: it saves as **Bar Ticker** with its own LED-scoreboard icon and opens like an app.
- **Refresh button** (↻, top right): the ticker pulls every score and ParlayLabs right away instead of waiting for its next check, and the page updates
- **Live preview** of what's on the ticker right now
- Four tabs: **Ticker**, **Picture**, **Message**, **Alerts**
- **Ticker** (games + ParlayLabs in one place):
  - **Games**: sport chips (tap any mix, or All sports). With nothing picked, the ticker shows **every game** in those sports. Narrow it with **My teams**, **Live games**, **Close games** (on/off toggles you can combine), **Select all**, or by tapping individual games; **Clear** goes back to every game. The list shows the **top 5** (my teams, close, live, soonest, finals) with **Show all ▾** to see the rest; checked games always stay visible
    - **My teams** always shows all four (Lions, Pistons, MSU football, MSU basketball): each one's game if it's on, today or just ended (kept 15 min), otherwise its **next game** with opponent, day, date, time and odds once books post them (looked up from each team's ESPN schedule every 30 min; tagged NEXT)
    - **Live games** follows every game in progress in your sports, adds games as they start and keeps each on 15 min after it ends ("ON 12 MIN")
    - **Close games** follows games that are late and within one score in your sports (football 4th qtr under 8:00 within 8, NBA 4th qtr under 5:00 within 6, college hoops 2nd half under 5:00 within 6, NHL 3rd period under 10:00 within 1, MLB 7th inning on within 2); red CLOSE tag in the list
    - **Order**: By league, or By start time (LIVE, then each slot like "SUN 1:00 PM", then FINAL). **Days**: Today or This week
  - **ParlayLabs**: Record, Top parlays and/or Top overs (all off = left out), mixed in after the games. Pick how many (top 3/5/8), which sports and which prop types
  - **Odds**: Show odds switch, plus Moneyline, Over/Under and Spread picks, for games that haven't started
  - **Look**: how the whole ticker is drawn:
    - **Classic**: two lines per game, league boxes
    - **Broadcast**: TV style, one line per game in tall narrow letters (**CLE 27 @ TEN 24 FINAL**), gold clock and dots, gold league labels
    - **Team colors**: each team in a pill of its own colors (from ESPN)
    - **Stadium**: old amber scoreboard, scores in boxes, live clock in red
    - **Big & bold**: one line, extra big, readable from across the room

    Fonts: Barlow Condensed and Oswald (free, SIL Open Font License, in `fonts/`)
  - **How it moves**: **Scroll** (the classic crawl) or one item at a time with **Cut**, **Fade**, **Flash**, **Slide up** or **Slide left**. In one-at-a-time styles, games show as a big scoreboard and ParlayLabs items as a card with a small label on top. **Speed** slider for crawling, and **Each item stays** 3, 5, 8 or 15 sec
  - **Hold on top** (split screen): **Off**, **My teams**, **Live games** or **Picked games**. Those games stay put on the top row (one line each: teams, scores, clock; several side by side, taking turns if they don't all fit) while everything else crawls along the bottom row on one line. When none of them are live it goes back to the full-height ticker by itself. My teams and Live games keep a game on 15 min after it ends
- **Picture**: your own picture library, saved on the Pi
  - **Add** photos, GIFs and phone videos (several at once). Videos (MP4/MOV, up to 250 MB) are shrunk once on the Pi with ffmpeg (first 20 seconds, no sound), which takes about a minute, then play as a loop
  - **Edit** each one: **Whole picture** (black bars), **Fill** (with **Zoom** from the whole picture up to 4×, **Left ↔ Right** and **Up ↕ Down** sliders and a live preview; **Reset** puts it back) or **Slow pan** (fills the strip and glides across the whole photo and back). Tall phone photos start on Slow pan; little pixel-art GIFs stay crisp and blocky. **Show this now** plays just that one; **Delete** removes it
  - **Slideshow**: check the ones you want (none unchecked = all, and new ones join automatically), then **Play slideshow**. Each stays 5 s, 10 s, 20 s or 1 min (GIFs and videos play all the way through), in order or shuffled, changing with a fade, slide or cut
  - **In the ticker**: a checked picture pops up **after each lap** of the ticker, or every 2, 5 or 10 minutes, for the slideshow time, then the scores pick up where they left off
  - **LED color boost**: a little more color, contrast and sharpness so photos pop on LEDs (on by default)
- **Message**: type anything, pick a color and a style (Steady, Scroll, Blink, Rainbow, Fireworks), then where it shows, in any mix: **on its own**, **with the ticker** (comes around between leagues) and/or **over the picture** (on top of your photo or GIF with a black outline). Typing a new message updates it everywhere it's showing
- **Alerts** tab: your favorite teams and their alerts
  - **Every game**: a close-game alert for any game in your Ticker sports that gets close in the last few minutes (same rules as Close games): flashes CLOSE GAME, shows that game's score, then the ticker scrolls again. Once per game; your own teams use their own close-game alert. Style it like any alert; words can use **{score}**, **{clock}** and **{league}**
  - **Your teams**: starts with the Lions, Pistons, MSU football and MSU basketball. **+ Add a team** picks any NFL, NBA, college, NHL or MLB team from ESPN's list (name and colors fill in by themselves; type the team code if ESPN can't be reached). Favorite teams show in gold, are always in **My teams** (with their next game), and get alerts. **Remove** takes one off
  - Each team's alerts: **Touchdown** and **Red zone** (football), **Win** and **Close game** (every sport), each with its own look: on/off, **pictures and GIFs** (pick as many as you like, or add several new ones at once right there; they take turns in the order you tap them), a **style** (**GIF ↔ words** switches back and forth between your GIF and the words; **Words on the GIF**; **Side by side**, your picture or GIF on the left, the right or **both sides** (picture, words, picture; pick two for a different one on each side) at small, medium or half size with the words in the rest, and **Fade to a full-screen GIF in between** (words in the middle, then the whole ticker fades into a GIF, then back; the last GIF you pick is the full-screen one); **GIF only**; **Words only**), **how the words come in** (**Flash**, **Scroll across**, **Slide in**, **Pop**, **Blink**, **Rainbow**, **Fade**), **what's behind them** (**Team color**, **Black**, or for GIF ↔ words **The GIF**: the GIF plays, then the words come across it while it keeps going, over and over for the whole alert), your own **words** (up to 3) and **two colors**, **switch every** 1–3 s, **alert lasts** 6–20 s, and **end on the score**
  - **Edit how it looks**: tap a picked picture's ✎ to fit, zoom and slide it, same controls as the Picture tab (it changes that picture everywhere it shows)
  - **Test it on the ticker** plays that alert right away (no TV delay); **Classic look** puts it back
  - **Send my alerts to the ticker (GitHub)** (on the laptop / GitHub Pages page): saves all your teams and alert looks, with their pictures and GIFs, as `alerts/alerts.ticker` in this repo. The Pi checks GitHub every 2 minutes and loads them by itself (no restart; pictures it already has aren't doubled). One-time setup: paste a GitHub fine-grained token for **bar-ticker** with **Contents: Read and write** (kept only in that browser). On the Pi's page the button is **Load the alerts I sent from my laptop**, to grab them right away
  - **Save and move your alerts**: **Download my alerts** saves all your teams and alert looks, with their pictures and GIFs, in one `.ticker` file. **Load an alerts file** adds them to this ticker (new teams are added, pictures it already has aren't doubled). Make them on a laptop, then load the file on http://ticker.local. Also a handy backup
- **Brightness**, **TV delay** (holds alerts so they line up with your TV), **score update speed**, **team alerts on/off** and **college rankings on/off**
- Your choices are saved, so the ticker comes back the same way after a power cut
- It only works on your home network. Reaching it from away would need something like Tailscale on the Pi

**On a laptop or computer:** the same page works on a computer screen: wider, with the ticker preview staying in view while you scroll and the alert editor in two columns (pictures on the left, style, words and timing on the right). In demo mode on GitHub Pages, what you make (alert looks, your teams and the pictures and GIFs you add) is kept in that browser, so it's still there next time. Tap **Send my alerts to the ticker (GitHub)** and the Pi loads them by itself within a few minutes (or use **Download my alerts** and **Load an alerts file** on http://ticker.local).

**Try it before the Pi arrives:** open the page from GitHub Pages (https://callis11.github.io/bar-ticker/, can also be added to the home screen) and it runs in demo mode with a pretend ticker drawn on the page. Every button works (the Picture tab comes with sample pictures, GIFs and a video, and you can add your own; they stay only until you reload), there are test buttons for the touchdown, win and close-game alerts, and it shows today's real games when ESPN allows it. You can also run `python3 ticker.py --sim --demo` on a computer and open http://localhost:8080.

## What the script does now
- Scrolls every game today in the NFL, NBA, college football, college hoops (ranked teams + MSU), NHL and MLB, using ESPN's free scoreboard feed. Everything refreshes every 5 seconds by default. The Lions, Pistons, MSU and any games you picked always refresh every 5 seconds while live; the rest can be slowed on the phone page (5–60 sec). If ESPN stops answering for a while, the phone page shows a red warning so you know to slow it down
- Each game is a two-line block with a league-colored edge: away team and score on top, home underneath (leader bright, trailing team dimmer), and the clock (live, red), start time and O/U or spread (upcoming), or Final on the right. Before kickoff the moneylines sit where the scores go. Leagues and time slots show as colored labels
- Live games come first, then upcoming games with the odds you picked, then final scores
- Pulls from your ParlayLabs site every 10 minutes, for each sport. On the phone you pick any mix of:
  - **Record**: wins-losses, $10-bet profit, the last 3 graded parlays, how many are still live
  - **Top parlays**: this week's Best of the week and Lions parlays, each with its odds, its legs and what $10 pays
  - **Top overs**: the best player overs for games that haven't started (most likely first, priced -250 to +150, best line per player), with the best price and how often he's hit it lately. Lions/Pistons/MSU players go first, in gold
  - Also pick **how many** (top 3, 5 or 8), **which sports** (NFL, NBA, College FB, College BB, or all) and **which prop types** (TD, receptions, rec yds, rush yds, rush+rec, pass yds, pass TDs, points, rebounds, assists, threes, PRA, or all)
- **Football downs**: live NFL and college games show the down and distance and where the ball is (**3rd & 7 · DET 35**) under the clock, with a little football next to the team that has it, everywhere a game shows (the scroll, the big one-at-a-time scoreboard, Hold on top and the phone's games list). Red zone turns it orange-red. It updates with the score (every 5 seconds for the Lions and MSU)
- Lions, Pistons and Michigan State games show in gold
- College football and basketball teams in the AP Top 25 show their rank in front, like **#12 MSU 21  #8 IOWA 17**, in the scroll, held games, one-at-a-time cards and the phone's games list (from ESPN, updates each week)
- **Team alerts** (for every favorite team, in the look you set on the Alerts tab): the whole ticker takes over for 10 seconds (or your length), then goes back to whatever was showing (scroll, game, picture or message). Your GIFs are made ready ahead of time so an alert never waits on them
  - Touchdown (football): flashes TOUCHDOWN! / LIONS! in team colors, or your GIFs and words, then the score
  - Red zone (football): when your team gets the ball inside the 20; once per trip (not again for 2 minutes even if ESPN flickers)
  - Win (every sport): flashes the team name and WIN! with fireworks, then the final score
  - Close game: flashes CLOSE GAME in amber once a game gets tight late. Football: 4th quarter, under 8:00, within 8 points. NBA: 4th quarter, under 5:00, within 6. College hoops: 2nd half, under 5:00, within 6
- Test without LEDs: `python3 ticker.py --preview` (scroll) or `python3 ticker.py --test-alert td` (also `win`, `close`). Sample GIFs are in `previews/`

## Upgrade for later: twice as tall (640 x 128, 5.25 ft x 12.6 in)
Two rows of five panels: your game held big on top, the whole ticker crawling underneath at today's size, alerts and GIFs twice as tall (a 5:1 picture). Not started; when you're ready, tell Claude and it adds the tall layout and the Colorlight output.
- **Parts (~$120):** 5 more P2.5 128x64 panels from the same seller, ICN2037 chip again (~$68 + shipping); a **Colorlight 5A-75B** receiving card (~$24, seen on AliExpress for $24.21 in the 11.11 sale); a second 5 V 30 A supply for the top row (~$25); a taller backer board
- **How it works:** the Pi draws the picture and sends it over an Ethernet cable to the 5A-75B, which drives the panels on its 8 outputs (steady, flicker-free refresh at any size; the Pi stays on Wi-Fi for scores). One-time setup of the card in Colorlight's free LEDVISION program on a Windows PC: 128x64 panels, 1/32 scan, ICN2037. The Triple Bonnet isn't used then
- **Code to add:** a `ROWS = 128` layout (two full-size rows) and a Colorlight output in ticker.py. Looks, alerts, pictures and the phone page stay the same. Needs one tune-up round once the card is here
- Building the 5-panel version first wastes nothing; the top row can be added any time

## Ideas for next scripts
- Leg tracker: show each live parlay leg turning green or red as games go final
- Goal / home run alerts for hockey and baseball teams
- Two-line ParlayLabs in the scroll: parlay name and payout on top, legs underneath
- Night mode: dim or turn off at closing time

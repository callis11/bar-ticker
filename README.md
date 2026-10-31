# ParlayLabs Bar Ticker — Build Plan

A ~4.2 ft x 6.3 in full-color LED ticker (256 x 32 pixels) for above the bar.
It scrolls live scores, odds and ParlayLabs results, flashes alerts for the Lions, Pistons and MSU, and is controlled from your phone.

## Shopping list (~$230–250)

| Part | Qty | ~Price | Notes |
|---|---|---|---|
| Waveshare RGB-Matrix-P5-64x32 (HUB75) | 4 | $88–110 | 320 x 160 mm each, so 4 = 1280 mm (4.2 ft). 3 panels = 3.1 ft if you want it cheaper |
| Raspberry Pi 4 Model B (2 GB) | 1 | ~$55–65 | Pi 4 is the safest pick for the LED library. A Pi 5 works with Adafruit's newer library |
| Adafruit RGB Matrix Bonnet (#3211) | 1 | ~$20 | Plugs onto the Pi and drives the panels |
| 5 V 20 A power supply (100 W, e.g. Mean Well LRS-100-5) | 1 | ~$20–25 | Powers the panels. Each panel can draw up to 4 A on full white |
| Official Pi USB-C power supply | 1 | ~$10 | The Pi gets its own supply |
| 32 GB microSD card | 1 | ~$10 | |
| 18 AWG red/black wire + Wago lever nuts | 1 | ~$10 | Each panel comes with its own ribbon cable and power pigtail |
| Smoked or black acrylic front (optional) | 1 | ~$15 | Makes it look like a real sports ticker. The frame you can cut on the CNC |

## Wiring
1. Chain the panels with the ribbon cables: Bonnet → panel 1 IN, panel 1 OUT → panel 2 IN, and so on.
2. Run 5 V and GND from the 20 A supply to **every** panel's power pigtail, not through the chain.
3. Power the Pi from its own USB-C supply.

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
- Team logo animations and short GIF clips between rotations (on a 256 x 32 strip, full videos get cropped to a thin band, so loops built for this shape look better)
- Night mode: dim or turn off at closing time

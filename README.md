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
- **Scroll all**: every game with scores, plus ParlayLabs (pick Record, Top parlays and/or Top overs). Pick any mix of NFL, NBA, college football, college hoops, NHL, MLB and ParlayLabs, or Everything
- **How it moves**: Scroll (the classic crawl), or one item at a time with **Fade**, **Flash** or **Slide up**, each with a small label on top (league, time slot or ParlayLabs part). A Speed slider (Slow to Very fast) sets the crawl speed, and for one-at-a-time you pick how long each item stays (3, 5 or 8 sec, 5 to start). Long items like parlays crawl across inside their card
- **Order**: By league, or **By start time** (LIVE first, then each kickoff/tip-off slot like "SUN 1:00 PM" with every game in it, then FINAL)
- **Days**: Today only, or This week (NFL and college football list the whole week)
- **Odds**: a Show odds switch, plus Moneyline, Over/Under and Spread picks (Moneyline and Over/Under on by default). Odds show for games that haven't started, in the scroll and when you hold a game
- **Games**: tap one game to hold it, or several and the ticker takes turns (switch every 5, 10 or 20 sec). Quick buttons: Select all, My teams, **Live games**, **Close games**, Clear. My teams are listed first. **Live games** follows every game in progress (only one league if a league filter is picked, e.g. "Live NFL"), adds new games the moment they start, and keeps each one on for 15 minutes after it ends (the list shows "ON 12 MIN"). **Close games** follows every close game in every league by itself (late and within one score: football 4th qtr under 8:00 within 8, NBA 4th qtr under 5:00 within 6, college hoops 2nd half under 5:00 within 6, NHL 3rd period under 10:00 within 1 goal, MLB 7th inning on within 2 runs). It adds games as they tighten, drops them when they open up or end, and scrolls everything else when nothing's close. Close games get a red CLOSE tag in the list
- **Picture**: send a photo or GIF from your phone (GIFs animate). Pick "Fit whole picture" or "Fill the strip"
- **Message**: type anything, pick a color and a style: Steady, Scroll, Blink, Rainbow or Fireworks. Show it on its own, or mix it into the scores scroll so it comes around before each league (the style works both ways)
- **Brightness**, **TV delay** (holds alerts so they line up with your TV), **score update speed** and **team alerts on/off**
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
- **Team alerts:** the whole ticker flashes for 10 seconds, then goes back to whatever was showing (scroll, game, picture or message)
  - Touchdown (Lions, MSU football): flashes TOUCHDOWN! / LIONS! in team colors, then the score
  - Win (Lions, Pistons, MSU football and basketball): flashes the team name and WIN! with fireworks, then the final score
  - Close game: flashes CLOSE GAME in amber once a game gets tight late. Football: 4th quarter, under 8:00, within 8 points. NBA: 4th quarter, under 5:00, within 6. College hoops: 2nd half, under 5:00, within 6
- Test without LEDs: `python3 ticker.py --preview` (scroll) or `python3 ticker.py --test-alert td` (also `win`, `close`). Sample GIFs are in `previews/`

## Ideas for next scripts
- Leg tracker: show each live parlay leg turning green or red as games go final
- Team logo animations and short GIF clips between rotations (on a 256 x 32 strip, full videos get cropped to a thin band, so loops built for this shape look better)
- Night mode: dim or turn off at closing time

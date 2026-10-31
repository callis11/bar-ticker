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
On your home Wi-Fi, open **http://ticker.local** on your phone. In Safari, tap Share → Add to Home Screen so it opens like an app.
- **Live preview** of what's on the ticker right now
- **Scroll all**: every game with scores and odds, plus ParlayLabs
- **One game**: tap any game and the ticker holds that scoreboard; my teams are listed first
- **Picture**: send a photo or GIF from your phone (GIFs animate). Pick "Fit whole picture" or "Fill the strip"
- **Message**: type anything, pick a color
- **Brightness**, **TV delay** (holds alerts so they line up with your TV), **score update speed** and **team alerts on/off**
- Your choices are saved, so the ticker comes back the same way after a power cut
- It only works on your home network. Reaching it from away would need something like Tailscale on the Pi

**Try it before the Pi arrives:** open the page from GitHub Pages (https://callis11.github.io/bar-ticker/) and it runs in demo mode with a pretend ticker drawn on the page. Every button works, there are test buttons for the touchdown, win and close-game alerts, and it shows today's real games when ESPN allows it. You can also run `python3 ticker.py --sim --demo` on a computer and open http://localhost:8080.

## What the script does now
- Scrolls every game today in the NFL, NBA, college football, college hoops (ranked teams + MSU), NHL and MLB, using ESPN's free scoreboard feed. Everything refreshes every 5 seconds by default. The Lions, Pistons, MSU and your pinned game always refresh every 5 seconds while live; the rest can be slowed on the phone page (5–60 sec). If ESPN stops answering for a while, the phone page shows a red warning so you know to slow it down
- Live games come first, then upcoming games with the spread and over/under, then final scores
- Pulls ParlayLabs `picks.json` for each sport: record, $10-bet profit, the last 3 graded parlays, and how many are still live
- Lions, Pistons and Michigan State games show in gold
- **Team alerts:** the whole ticker flashes for 10 seconds, then goes back to whatever was showing (scroll, game, picture or message)
  - Touchdown (Lions, MSU football): flashes TOUCHDOWN! / LIONS! in team colors, then the score
  - Win (Lions, Pistons, MSU football and basketball): flashes the team name and WIN! with confetti, then the final score
  - Close game: flashes CLOSE GAME in amber once a game gets tight late. Football: 4th quarter, under 8:00, within 8 points. NBA: 4th quarter, under 5:00, within 6. College hoops: 2nd half, under 5:00, within 6
- Test without LEDs: `python3 ticker.py --preview` (scroll) or `python3 ticker.py --test-alert td` (also `win`, `close`). Sample GIFs are in `previews/`

## Ideas for next scripts
- Leg tracker: show each live parlay leg turning green or red as games go final
- Team logo animations and short GIF clips between rotations (on a 256 x 32 strip, full videos get cropped to a thin band, so loops built for this shape look better)
- Night mode: dim or turn off at closing time

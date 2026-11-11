#!/bin/bash
# Pulls new ticker code from GitHub and restarts the ticker if anything changed.
# Runs every 2 minutes from ticker-update.timer.
cd /home/pi/bar-ticker || exit 1
before=$(git rev-parse HEAD)
git pull --ff-only -q || exit 1
after=$(git rev-parse HEAD)
# anything new the ticker needs (ffmpeg turns phone videos into ticker loops)
command -v ffmpeg >/dev/null || apt-get install -y -q ffmpeg
if [ "$before" != "$after" ]; then
    # alerts you send from the laptop page (alerts/) load by themselves; no restart for those
    if git diff --name-only "$before" "$after" | grep -qv '^alerts/'; then
        echo "updated $before -> $after, restarting ticker"
        systemctl restart ticker
    else
        echo "new alerts from GitHub $before -> $after (the ticker loads them by itself)"
    fi
fi

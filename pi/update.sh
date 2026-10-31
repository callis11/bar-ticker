#!/bin/bash
# Pulls new ticker code from GitHub and restarts the ticker if anything changed.
# Runs every 2 minutes from ticker-update.timer.
cd /home/pi/bar-ticker || exit 1
before=$(git rev-parse HEAD)
git pull --ff-only -q || exit 1
after=$(git rev-parse HEAD)
if [ "$before" != "$after" ]; then
    echo "updated $before -> $after, restarting ticker"
    systemctl restart ticker
fi

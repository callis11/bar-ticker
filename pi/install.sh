#!/bin/bash
# One-time setup on the Pi, after the LED library is installed:
#   git clone https://github.com/callis11/bar-ticker.git /home/pi/bar-ticker
#   sudo bash /home/pi/bar-ticker/pi/install.sh
set -e
apt-get install -y python3-pil fonts-dejavu git ffmpeg
cp /home/pi/bar-ticker/pi/ticker.service        /etc/systemd/system/
cp /home/pi/bar-ticker/pi/ticker-update.service /etc/systemd/system/
cp /home/pi/bar-ticker/pi/ticker-update.timer   /etc/systemd/system/
git config --global --add safe.directory /home/pi/bar-ticker
systemctl daemon-reload
systemctl enable --now ticker.service ticker-update.timer
echo "Ticker is running and checks GitHub for updates every 2 minutes."
echo "Control page: http://ticker.local"

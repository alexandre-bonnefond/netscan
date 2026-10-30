#!/usr/bin/env bash

# Installs netscan: links ~/.local/bin/netscan to this repo, checks the tools it uses.
# Safe to run again. No sudo needed (the missing apt packages are only listed).

set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
BIN="$HOME/.local/bin"

mkdir -p "$BIN"
ln -sfn "$REPO/bin/netscan" "$BIN/netscan"
echo "linked $BIN/netscan"

missing=()
for tool in ip:iproute2 ping:iputils-ping ssh:openssh-client rofi:rofi kitty:kitty \
  notify-send:libnotify-bin avahi-resolve:avahi-utils; do
  command -v "${tool%%:*}" >/dev/null || missing+=("${tool#*:}")
done
[ -f /usr/share/ieee-data/oui.csv ] || missing+=(ieee-data)
if [ ${#missing[@]} -gt 0 ]; then
  echo "missing packages: sudo apt install ${missing[*]}"
fi

"""Rofi menu of the scanned devices, Enter opens SSH in a terminal.

- one tab per subnet, plus 'All', Ctrl+Tab / Ctrl+Shift+Tab or a click switch the tabs
- the 'Refresh' row or Ctrl+R scans again and reopens the menu on the same tab
- each tab is a rofi script mode: rofi runs 'netscan rofi-tab KEY' to list its rows, and
  again with ROFI_RETV=1 and ROFI_INFO=<ip> when a row is picked (10 for Ctrl+R). The scan
  is kept in a cache file shared by the tabs. The picked action is saved and runs once
  rofi is closed: rofi lists every tab once at start, so a scan from inside one tab would
  leave the others stale, and the user menu could not open while rofi is up.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from html import escape
from pathlib import Path

from . import scan, sshconfig

ROFI_DIR = Path.home() / ".config" / "rofi"
THEME = Path(__file__).resolve().parent.parent / "rofi" / "netscan.rasi"  # imports ubuntu-setup's menu-list.rasi
RUN_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
CACHE = RUN_DIR / "netscan.json"
PICKED = RUN_DIR / "netscan-picked"
ICON_SSH = ""  # FontAwesome 4 terminal
ICON_NONE = ""  # circle-o
ICON_REFRESH = ""
ALL = "All"
NOTIFY_ID = "string:x-canonical-private-synchronous:netscan"
MONO = "DejaVu Sans Mono 10"
PALETTE = Path.home() / ".config" / "ubuntu-setup" / "palette" / "rofi.rasi"  # active dark/light palette


def notify(message, urgency="low", timeout=3000):
    subprocess.run(["notify-send", "-u", urgency, "-t", str(timeout), "Netscan", message, "-h", NOTIFY_ID],
                   stderr=subprocess.DEVNULL)


def rofi_args(prompt, message="", lines=8, width=360):
    """rofi -dmenu with the shared i3 menu list theme of ubuntu-setup when it is there."""
    args = ["rofi", "-dmenu", "-i", "-p", prompt]
    theme = ROFI_DIR / "menu-list.rasi"
    if theme.exists():
        args += ["-theme", str(theme)]
    args += ["-theme-str", f"window {{ width: {width}px; }} listview {{ lines: {lines}; }}"]
    if message:
        args += ["-mesg", message]
    return args


# ---------- scan cache, shared by the tabs ----------

def refresh():
    notify("Scanning the local networks…", timeout=10000)
    subnets = scan.subnets()
    devices = scan.scan(subnets)
    data = {"time": time.time(), "subnets": [asdict(s) for s in subnets], "devices": [asdict(d) for d in devices]}
    CACHE.write_text(json.dumps(data))
    notify(f"{len(devices)} devices found", timeout=1000)
    return subnets, devices


def load():
    data = json.loads(CACHE.read_text())
    return (data["time"], [scan.Subnet(**s) for s in data["subnets"]],
            [scan.Device(**d) for d in data["devices"]])


# ---------- tabs ----------

def tab_name(subnet):
    kind = "Wi-Fi" if subnet.wireless else "Ethernet"
    return f"{kind} {subnet.network}"


def in_tab(device, key, subnets):
    return key == ALL or any(s.network == key and s.iface == device.iface for s in subnets)


def cut(text, width):
    return text if len(text) <= width else text[: width - 1] + "…"


def chip_color():
    """Text color of the active palette: the IP chip is a light veil of it, readable in dark and
    light, on normal and selected rows."""
    try:
        match = re.search(r"pal-fg:\s*(#[0-9A-Fa-f]{6})", PALETTE.read_text())
    except OSError:
        match = None
    return match.group(1) if match else "#888888"


def row(device, name_width, chip):
    """Icon, name and IP (in a chip) in padded monospace columns, then the vendor."""
    icon = ICON_SSH if device.ssh_open else ICON_NONE
    name = cut(device.name, name_width).ljust(name_width) if device.name else "?".ljust(name_width)
    pad = " " * (16 - len(device.ip))  # outside the chip, so it fits the IP and the vendors line up
    return (f"<span font_desc='FontAwesome 12'>{icon}</span>   "
            f"<span font_desc='{MONO}' weight='bold'>{escape(name)}</span>  "
            f"<span font_desc='{MONO}' background='{chip}' bgalpha='14%'> {device.ip} </span>"
            f"<span font_desc='{MONO}'>{pad}</span>"
            f"<span alpha='60%'>{escape(scan.short_vendor(device.vendor))}</span>"
            f"\0info\x1f{device.ip}")


def print_tab(key):
    scanned, subnets, devices = load()
    devices = [d for d in devices if in_tab(d, key, subnets)]
    name_width = min(max([len(d.name) for d in devices] + [8]), 24)
    chip = chip_color()
    print("\0markup-rows\x1ftrue")
    print("\0no-custom\x1ftrue")
    print("\0use-hot-keys\x1ftrue")  # Ctrl+R (kb-custom-1) reaches the script
    print(f"\0message\x1f{len(devices)} devices, scanned at {time.strftime('%H:%M:%S', time.localtime(scanned))}"
          f"   ·   {ICON_SSH} SSH open   ·   Ctrl+R = refresh   ·   Ctrl+Tab = next tab")
    print(f"<span font_desc='FontAwesome 12'>{ICON_REFRESH}</span>   Refresh\0info\x1frefresh")
    for device in devices:
        print(row(device, name_width, chip))


def tab(key):
    """One tab, called by rofi."""
    retv = os.environ.get("ROFI_RETV", "0")
    info = os.environ.get("ROFI_INFO", "")
    if retv == "0":
        print_tab(key)
    elif retv == "10" or (retv == "1" and info == "refresh"):
        PICKED.write_text(f"refresh\t{key}")  # no rows: rofi closes
    elif retv == "1":
        PICKED.write_text(f"ssh\t{info}")
    return 0


# ---------- ssh ----------

def ask_user(device, users):
    """User for a device not in ~/.ssh/config: pick a known one or type a new one."""
    label = device.name or device.ip
    message = f"New device {label} ({device.ip}, {device.vendor or device.mac}): its user is saved in ~/.ssh/config"
    args = rofi_args(f"User for {label}", message, lines=max(len(users), 1), width=560)
    out = subprocess.run(args, input="\n".join(users), capture_output=True, text=True).stdout
    return out.strip()


def open_ssh(target):
    """SSH in a new terminal, which stays open when ssh fails so the error can be read."""
    script = f'ssh {shlex.quote(target)} || {{ printf "\\nssh failed, Enter closes "; read -r _; }}'
    subprocess.Popen(["kitty", "--title", f"ssh {target}", "sh", "-c", script], start_new_session=True)


def connect(device, ssh_config=sshconfig.DEFAULT_PATH):
    if not device.ssh_open:
        notify(f"SSH port {device.ssh_port} is closed on {device.name or device.ip}", "normal")
        return
    if device.ssh_host:
        open_ssh(device.ssh_host)
        return
    hosts = sshconfig.load(ssh_config)
    user = ask_user(device, sshconfig.known_users(hosts))
    if not user:
        return
    alias = sshconfig.free_alias(device.name, device.ip, hosts)
    sshconfig.append(alias, device.ip, user, comment=f"{device.vendor or 'unknown vendor'}, {device.mac}",
                     path=ssh_config)
    notify(f"Added Host {alias} ({user}@{device.ip}) to ~/.ssh/config")
    open_ssh(alias)


# ---------- the menu ----------

def show_menu(subnets, devices, show_key):
    """Open rofi on the tab `show_key`, return the picked (action, value) or None."""
    exe = shlex.quote(str(Path(sys.argv[0]).resolve()))
    tabs = [(ALL, ALL)] + [(tab_name(s), s.network) for s in subnets]
    show = next((name for name, key in tabs if key == show_key), ALL)
    modes = ",".join(f"{name}:{exe} rofi-tab {shlex.quote(key)}" for name, key in tabs)
    args = ["rofi", "-modes", modes, "-show", show, "-kb-custom-1", "Control+r",
            "-theme-str", f"listview {{ lines: {min(max(len(devices) + 1, 6), 15)}; }}"]
    if (ROFI_DIR / "menu-list.rasi").exists():
        args[1:1] = ["-theme", str(THEME)]

    PICKED.unlink(missing_ok=True)
    subprocess.run(args)
    if not PICKED.exists():
        return None
    action, _, value = PICKED.read_text().partition("\t")
    PICKED.unlink()
    return action, value


def run():
    show_key = ALL
    while True:
        subnets, devices = refresh()
        if not subnets:
            notify("No local network (ethernet or Wi-Fi) is up", "normal")
            return 1
        picked = show_menu(subnets, devices, show_key)
        if not picked:
            return 0
        action, value = picked
        if action == "refresh":
            show_key = value
            continue
        device = next((d for d in devices if d.ip == value), None)
        if device:
            connect(device)
        return 0

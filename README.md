# netscan

Scan the local networks from i3, pick a device in a rofi menu, and SSH into it in a
terminal. Made for drones on the lab network, but works for any device.

Status: **steps 1–3 done** (scan, rofi menu, SSH). The original plan (in French) is
`~/Downloads/Outil de scan réseau pour drones - Plan du projet.md`.

## Install

```bash
~/git/netscan/install.sh   # links ~/.local/bin/netscan, lists missing apt packages
```

and in ubuntu-setup's i3 `main.conf`:

```
bindsym $mod2+n exec --no-startup-id ~/.local/bin/netscan rofi
```

## Use

| Command | Does |
|---|---|
| `netscan` / `netscan list` | scan and print a table (IP, MAC, name and where it comes from, SSH, vendor) |
| `netscan list --json` | the same as JSON, for scripts |
| `netscan rofi` | scan (a notification shows it is running), pick a device, Enter opens `ssh` in kitty |

The menu has one tab per subnet (e.g. `Wi-Fi 192.168.12.0/24`, `Ethernet 192.168.11.0/24`) plus
`All`, switched with Ctrl+Tab / Ctrl+Shift+Tab or a click, like ubuntu-setup's cheatsheet. The
`Refresh` row (or Ctrl+R) scans again and reopens the menu on the same tab. `` marks devices whose
SSH port answers. A device of `~/.ssh/config` opens with `ssh <Host>`. A new device asks for the user (the users already in the config are suggested), then
appends a block like this one and connects:

```
# added by netscan on 2026-10-30 (Raspberry Pi (Trading) Ltd, 2c:cf:67:00:00:01)
Host uav3
    HostName 192.168.11.23
    User mrs
```

The `Host` is the mDNS / DNS name of the device when it has one and is not taken, else its IP.
A failed `ssh` keeps the terminal open to read the error.

Tests: `python3 -m unittest discover tests`.

## Layout

- `netscan/scan.py`: interfaces and subnets, ping sweep, `ip neigh`, vendor, names, SSH port.
  No display code, `scan.scan()` returns a list of `Device`.
- `netscan/sshconfig.py`: reads the `Host` blocks of `~/.ssh/config`, appends new ones.
- `netscan/menu.py`: rofi menus and the terminal. Each tab is a rofi script mode
  (`netscan rofi-tab KEY`) reading the last scan from `$XDG_RUNTIME_DIR/netscan.json`.
- `rofi/netscan.rasi`: the theme, ubuntu-setup's `menu-list.rasi` with tabs (rofi's default
  theme when ubuntu-setup is not installed).
- `netscan/cli.py`, `bin/netscan`: command line.

## Known limits

- A device that drops ping and was already in the neighbour table as `STALE` is not listed: the
  kernel waits 5 s before re-checking it, so it cannot be told apart from a device that left.
  Devices that drop ping but are new to the table are found (they answer ARP).
- `ieee-data` on Ubuntu 24.04 dates from 2022: newer prefixes (e.g. Raspberry Pi's `2c:cf:67`)
  have no vendor.
- ssh keeps the first value it finds for each option: a `Host *` block with a `User` placed before
  the appended blocks wins over their `User`.
- `Include` lines of the ssh config are not followed.

## Principles

- Python 3, standard library only (plus tools already on Ubuntu: `ip`, `ping`, `ssh`, `rofi`).
- Rootless: no sudo, no raw sockets, no capabilities.
- The scan is separate from the display (rofi now, maybe a Textual table later).
- Standalone repo, hooked into [ubuntu-setup](../ubuntu-setup) with one line: an i3
  binding (and later an i3blocks block and cheatsheet rows).
- Never rewrite the user's files: `~/.ssh/config` only gets new `Host` blocks appended.

## Decisions

- **Scan without root**: ping every address of the subnet in parallel (ICMP is allowed
  for users on Ubuntu), then read IP + MAC from the kernel's neighbour table (`ip neigh`).
  Scapy / ARP would need root.
- **Interfaces**: only the real ones (ethernet, Wi-Fi), skip virtual ones (`lo`, `docker*`,
  `br-*`, `veth*`, `tailscale*`, `tun*`). Refuse subnets larger than /22 (too slow to sweep).
- **Vendor**: from the MAC prefix, with the IEEE list of the `ieee-data` package
  (`/usr/share/ieee-data/oui.csv`), no download or API.
- **Names**: `~/.ssh/config` (`Host` / `HostName`), `/etc/hosts`, reverse DNS, mDNS
  (`avahi-resolve -a`) when available.
- **SSH**: offered only when port 22 answers. User, port and key come from `~/.ssh/config`.
  For an unknown device, a second rofi menu asks for the user, then a `Host` block is
  appended to `~/.ssh/config`. No `/etc/hosts` editing in v1 (needs root).

## Plan

1. Scan: interfaces and subnets, ping sweep, `ip neigh`, vendor, names.
2. Rofi menu: one row per device (name if known, IP, vendor), opened by an i3 shortcut.
3. SSH: open a terminal (kitty) with `ssh` to the picked device, ask for the user and
   remember it for unknown devices.
4. Shortcuts: port scan of a device, open its web interface, fetch logs with `scp`, run
   one command on several devices (one tmux pane per device with synchronized input).
5. Later: alert when a device appears or disappears, CPU and temperature over SSH, Wi-Fi
   signal in dBm, a bar block with the number of devices.

## License

MIT, see `LICENSE`.

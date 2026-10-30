# netscan

Scan the local networks from i3, pick a device in a rofi menu, and SSH into it in a
terminal. Made for drones on the lab network, but works for any device.

Status: **planning**. The original plan (in French) is
`~/Downloads/Outil de scan réseau pour drones - Plan du projet.md`.

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

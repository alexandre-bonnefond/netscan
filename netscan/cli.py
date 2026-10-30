"""netscan [list [--json] | rofi]"""

import argparse
import sys

from . import menu, scan


def print_table(devices):
    rows = [("IP", "MAC", "NAME", "SSH", "VENDOR")]
    for d in devices:
        name = f"{d.name} ({d.name_source})" if d.name else ""
        ssh = (d.ssh_user + "@" if d.ssh_user else "") + "open" if d.ssh_open else ""
        rows.append((d.ip, d.mac, name, ssh, scan.short_vendor(d.vendor, 40)))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[-1])


def main(argv=None):
    parser = argparse.ArgumentParser(prog="netscan", description="Scan the local networks, SSH into a device.")
    sub = parser.add_subparsers(dest="command", metavar="{list,rofi}")
    ls = sub.add_parser("list", help="print the devices (default)")
    ls.add_argument("--json", action="store_true", help="JSON instead of a table")
    sub.add_parser("rofi", help="pick a device in rofi and SSH into it")
    tab = sub.add_parser("rofi-tab")  # one tab of the rofi menu, run by rofi
    tab.add_argument("key")
    tab.add_argument("selected", nargs="?")  # text of the picked row, rofi adds it (ROFI_INFO is used)
    args = parser.parse_args(argv)

    if args.command == "rofi":
        return menu.run()
    if args.command == "rofi-tab":
        return menu.tab(args.key)

    nets = scan.subnets()
    if not nets:
        scan.warn("no local network (ethernet or Wi-Fi) is up")
        return 1
    for s in nets:
        print(f"# {s.network} on {s.iface} (this machine: {s.address})", file=sys.stderr)
    devices = scan.scan(nets)
    if getattr(args, "json", False):
        print(scan.to_json(devices))
    else:
        print_table(devices)
    return 0

"""Find the devices on the local networks, without root.

Ping every address of each subnet in parallel, then read IP + MAC from the
kernel's neighbour table, which the pings filled. Then vendor from the MAC
prefix, names from ~/.ssh/config, /etc/hosts, reverse DNS and mDNS, and whether
the SSH port answers.
"""

import csv
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from pathlib import Path

from . import sshconfig

VIRTUAL_PREFIXES = ("lo", "docker", "br-", "veth", "tailscale", "tun", "tap", "virbr", "wg", "zt")
MIN_PREFIXLEN = 22  # larger subnets take too long to sweep
IEEE_DIR = Path("/usr/share/ieee-data")
# longest prefixes first: MA-S (36 bits), MA-M (28 bits), MA-L (24 bits)
IEEE_FILES = (("oui36.csv", 9), ("mam.csv", 7), ("oui.csv", 6))
# entries the kernel could not resolve, or that are not really there
DEAD_STATES = {"FAILED", "INCOMPLETE", "NOARP"}


@dataclass
class Subnet:
    iface: str
    address: str  # our own address on it
    network: str
    wireless: bool = False


@dataclass
class Device:
    ip: str
    mac: str
    iface: str
    ping: bool = False
    vendor: str = ""
    name: str = ""
    name_source: str = ""  # ssh, hosts, mdns, dns
    ssh_host: str = ""  # alias in ~/.ssh/config
    ssh_user: str = ""
    ssh_port: int = 22
    ssh_open: bool = False

    @property
    def ip_key(self):
        return ipaddress.ip_address(self.ip)


def warn(message):
    print(f"netscan: {message}", file=sys.stderr)


def run_json(*cmd):
    return json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout or "[]")


# ---------- subnets ----------

def is_wireless(iface):
    return Path(f"/sys/class/net/{iface}/wireless").exists()


def subnets_from(addr_json, warn=warn, is_wireless=is_wireless):
    """IPv4 subnets of the real interfaces that are up, from `ip -j addr`."""
    result = []
    for link in addr_json:
        name = link.get("ifname", "")
        if name.startswith(VIRTUAL_PREFIXES) or link.get("link_type") != "ether":
            continue
        if "UP" not in link.get("flags", []) or link.get("operstate") == "DOWN":
            continue
        for addr in link.get("addr_info", []):
            if addr.get("family") != "inet":
                continue
            iface = ipaddress.ip_interface(f"{addr['local']}/{addr['prefixlen']}")
            if iface.network.prefixlen < MIN_PREFIXLEN:
                warn(f"skipping {iface.network} on {name}: larger than /{MIN_PREFIXLEN}")
                continue
            result.append(Subnet(name, str(iface.ip), str(iface.network), is_wireless(name)))
    return result


def subnets():
    return subnets_from(run_json("ip", "-j", "addr", "show"))


# ---------- ping sweep + neighbour table ----------

def ping(ip, timeout=1):
    cmd = ["ping", "-c", "1", "-n", "-q", "-W", str(timeout), ip]
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def ping_all(hosts, timeout, workers):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        answers = pool.map(lambda ip: ping(ip, timeout), hosts)
        return {ip for ip, ok in zip(hosts, answers) if ok}


def sweep(subnet, workers=256, timeout=1, retry_timeout=2):
    """Addresses of the subnet that answered a ping (also fills the neighbour table).

    On Wi-Fi, the addresses that did not answer get a second ping with a longer timeout:
    devices in power save often miss the first one (the access point holds it until
    they wake up).
    """
    network = ipaddress.ip_network(subnet.network)
    hosts = [str(ip) for ip in network.hosts() if str(ip) != subnet.address]
    answered = ping_all(hosts, timeout, workers)
    if subnet.wireless:
        answered |= ping_all([ip for ip in hosts if ip not in answered], retry_timeout, workers)
    return answered


def neighbours_from(neigh_json, subnet, answered):
    """Devices of the subnet in `ip -j neigh`.

    A device is kept when it answered the ping, or when the kernel just resolved
    it (REACHABLE: it answered ARP but drops ping). STALE / DELAY entries that
    did not answer are left out, they may be devices that are gone.
    """
    network = ipaddress.ip_network(subnet.network)
    devices = {}
    for entry in neigh_json:
        ip, mac = entry.get("dst", ""), entry.get("lladdr", "")
        states = set(entry.get("state", []))
        if not mac or states & DEAD_STATES:
            continue
        try:
            if ipaddress.ip_address(ip) not in network:
                continue
        except ValueError:
            continue
        alive = ip in answered
        if alive or states & {"REACHABLE", "PERMANENT"}:
            devices[ip] = Device(ip=ip, mac=mac.lower(), iface=subnet.iface, ping=alive)
    return list(devices.values())


def neighbours(subnet, answered):
    return neighbours_from(run_json("ip", "-j", "neigh", "show", "dev", subnet.iface), subnet, answered)


# ---------- vendor ----------

def is_random_mac(mac):
    """Locally administered (randomised by phones, laptops, some containers)."""
    return bool(int(mac.split(":")[0], 16) & 0x02)


def vendors(macs, ieee_dir=IEEE_DIR):
    """{mac: vendor} from the IEEE lists of the ieee-data package, longest prefix first."""
    wanted = {}
    for mac in macs:
        hex_mac = mac.replace(":", "").upper()
        for _, length in IEEE_FILES:
            wanted.setdefault(hex_mac[:length], set()).add(mac)
    found = {}
    for filename, length in IEEE_FILES:
        try:
            with open(Path(ieee_dir) / filename, newline="", encoding="utf-8", errors="replace") as f:
                for row in csv.reader(f):
                    if len(row) < 3 or len(row[1]) != length or row[1] not in wanted:
                        continue
                    for mac in wanted[row[1]]:
                        found.setdefault(mac, row[2].strip())  # files go from longest prefix
        except FileNotFoundError:
            continue
    for mac in macs:
        if mac not in found and is_random_mac(mac):
            found[mac] = "(random MAC)"
    return found


# ---------- names ----------

def etc_hosts(path="/etc/hosts"):
    """{ip: first name} and {name: ip} from /etc/hosts."""
    by_ip, by_name = {}, {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return by_ip, by_name
    for line in lines:
        fields = line.split("#", 1)[0].split()
        if len(fields) < 2:
            continue
        ip, names = fields[0], fields[1:]
        by_ip.setdefault(ip, names[0])
        for name in names:
            by_name.setdefault(name, ip)
    return by_ip, by_name


def command_output(cmd, timeout):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def reverse_dns(ip, timeout=2):
    """Name from DNS, with a timeout (socket.gethostbyaddr has none)."""
    fields = command_output(["getent", "hosts", ip], timeout).split()
    return fields[1] if len(fields) > 1 else ""


def mdns(ip, timeout=2):
    fields = command_output(["avahi-resolve", "-a", ip], timeout).split()
    return fields[1] if len(fields) > 1 else ""


def clean_name(name):
    """Short display name: no .local, and systemd's synthesized _gateway as gateway."""
    name = re.sub(r"\.local\.?$", "", name)
    return "gateway" if name == "_gateway" else name


def resolve_names(devices, ssh_hosts, hosts_by_ip, hosts_by_name, workers=64):
    has_avahi = shutil.which("avahi-resolve") is not None
    ssh_by_ip = sshconfig.by_ip(ssh_hosts, hosts_by_name)

    def lookup(device):
        if device.ip in ssh_by_ip:
            host = ssh_by_ip[device.ip]
            device.ssh_host, device.ssh_user, device.ssh_port = host.alias, host.user, host.port
            device.name, device.name_source = host.alias, "ssh"
        elif device.ip in hosts_by_ip:
            device.name, device.name_source = hosts_by_ip[device.ip], "hosts"
        else:
            name = mdns(device.ip) if has_avahi else ""
            if name:
                device.name, device.name_source = clean_name(name), "mdns"
            else:
                name = reverse_dns(device.ip)
                if name:
                    device.name, device.name_source = clean_name(name), "dns"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lookup, devices))


# ---------- ssh port ----------

def port_open(ip, port, timeout=0.7):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_ssh(devices, workers=64):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for device, ok in zip(devices, pool.map(lambda d: port_open(d.ip, d.ssh_port), devices)):
            device.ssh_open = ok


# ---------- all together ----------

def scan(nets=None, ssh_config=sshconfig.DEFAULT_PATH):
    """Devices on `nets` (default: all the real local networks), sorted by IP."""
    devices = []
    for subnet in subnets() if nets is None else nets:
        answered = sweep(subnet)
        devices += neighbours(subnet, answered)
    by_vendor = vendors({d.mac for d in devices})
    for device in devices:
        device.vendor = by_vendor.get(device.mac, "")
    hosts_by_ip, hosts_by_name = etc_hosts()
    resolve_names(devices, sshconfig.load(ssh_config), hosts_by_ip, hosts_by_name)
    check_ssh(devices)
    return sorted(devices, key=lambda d: d.ip_key)


def to_json(devices):
    return json.dumps([asdict(d) for d in devices], indent=2)


def short_vendor(vendor, width=28):
    """Vendor without the legal suffixes, cut to `width`."""
    vendor = re.sub(r"[,.]?\s+(co\.?,?\s*)?(ltd|inc|corp|corporation|gmbh|limited|llc|s\.?a\.?|b\.?v\.?)\.?$", "",
                    vendor, flags=re.IGNORECASE)
    return vendor if len(vendor) <= width else vendor[: width - 1] + "…"

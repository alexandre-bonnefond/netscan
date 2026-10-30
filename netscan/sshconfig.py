"""Read the Host blocks of ~/.ssh/config and append new ones (never rewrite)."""

import datetime
import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path.home() / ".ssh" / "config"


@dataclass
class SshHost:
    alias: str  # first pattern of the Host line without wildcards
    hostname: str = ""
    user: str = ""
    port: int = 22
    patterns: list = field(default_factory=list)


def parse(text):
    """Host blocks of an ssh config, in file order. Match blocks end a Host block."""
    hosts = []
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(\S+?)\s*(?:=\s*|\s+)(.*)", line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if key == "host":
            patterns = value.split()
            alias = next((p for p in patterns if not any(c in p for c in "*?!")), "")
            current = SshHost(alias=alias, patterns=patterns)
            hosts.append(current)
        elif key == "match":
            current = None
        elif current is not None:
            value = value.strip('"')
            if key == "hostname":
                current.hostname = value
            elif key == "user":
                current.user = value
            elif key == "port" and value.isdigit():
                current.port = int(value)
    return hosts


def load(path=DEFAULT_PATH):
    try:
        return parse(Path(path).read_text())
    except FileNotFoundError:
        return []


def by_ip(hosts, names=None):
    """{ip: SshHost} for the blocks whose HostName (or alias) is an IP, or a name in `names` ({name: ip})."""
    names = names or {}
    result = {}
    for host in hosts:
        if not host.alias:
            continue
        target = host.hostname or host.alias
        try:
            ip = str(ipaddress.ip_address(target))
        except ValueError:
            ip = names.get(target)
        if ip and ip not in result:
            result[ip] = host
    return result


def known_users(hosts):
    """Users of the existing blocks, most used first, to suggest for a new device."""
    counts = {}
    for host in hosts:
        if host.user:
            counts[host.user] = counts.get(host.user, 0) + 1
    return sorted(counts, key=lambda u: -counts[u])


def free_alias(name, ip, hosts):
    """Alias for a new block: the device name if it is a valid unused alias, else the IP."""
    taken = {p for host in hosts for p in host.patterns}
    name = re.sub(r"\.local$", "", name or "")
    if name and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) and name not in taken:
        return name
    return ip


def append(alias, ip, user, comment="", path=DEFAULT_PATH):
    """Append a Host block at the end of the file (created with mode 600 if missing)."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    date = datetime.date.today().isoformat()
    block = f"# added by netscan on {date}"
    if comment:
        block += f" ({comment})"
    block += f"\nHost {alias}\n    HostName {ip}\n    User {user}\n"
    sep = ""
    if existing:
        sep = "\n" if existing.endswith("\n") else "\n\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(sep + block)

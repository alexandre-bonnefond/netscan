"""python3 -m unittest discover tests"""

import os
import tempfile
import unittest
from pathlib import Path

from netscan import scan, sshconfig

SSH_CONFIG = """\
Host *
    ServerAliveInterval 30

# lab drones
Host uav1 drone1
    HostName 192.168.11.21
    User mrs
Host uav2
    HostName=uav2.lab
    User = mrs
    Port 2222
Host 192.168.11.30
    User root
Match host foo
    User ignored
Host github.com
    User git
"""


class SshConfigTest(unittest.TestCase):

    def test_parse(self):
        hosts = sshconfig.parse(SSH_CONFIG)
        self.assertEqual([h.alias for h in hosts], ["", "uav1", "uav2", "192.168.11.30", "github.com"])
        uav2 = hosts[2]
        self.assertEqual((uav2.hostname, uav2.user, uav2.port), ("uav2.lab", "mrs", 2222))
        self.assertEqual(hosts[3].user, "root")  # Match block did not override it

    def test_by_ip(self):
        hosts = sshconfig.parse(SSH_CONFIG)
        result = sshconfig.by_ip(hosts, {"uav2.lab": "192.168.11.22"})
        self.assertEqual({ip: h.alias for ip, h in result.items()},
                         {"192.168.11.21": "uav1", "192.168.11.22": "uav2", "192.168.11.30": "192.168.11.30"})

    def test_known_users(self):
        self.assertEqual(sshconfig.known_users(sshconfig.parse(SSH_CONFIG)), ["mrs", "root", "git"])

    def test_free_alias(self):
        hosts = sshconfig.parse(SSH_CONFIG)
        self.assertEqual(sshconfig.free_alias("uav3.local", "10.0.0.3", hosts), "uav3")
        self.assertEqual(sshconfig.free_alias("drone1", "10.0.0.3", hosts), "10.0.0.3")  # taken
        self.assertEqual(sshconfig.free_alias("", "10.0.0.3", hosts), "10.0.0.3")
        self.assertEqual(sshconfig.free_alias("bad name", "10.0.0.3", hosts), "10.0.0.3")

    def test_append_keeps_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            path.write_text("Host a\n    User x")  # no final newline
            sshconfig.append("uav3", "10.0.0.3", "mrs", "Raspberry Pi, 2c:cf:67:00:00:01", path)
            text = path.read_text()
            self.assertTrue(text.startswith("Host a\n    User x\n\n# added by netscan on "))
            self.assertTrue(text.endswith("\nHost uav3\n    HostName 10.0.0.3\n    User mrs\n"))
            self.assertEqual(sshconfig.by_ip(sshconfig.parse(text))["10.0.0.3"].user, "mrs")

    def test_append_creates_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ssh" / "config"
            sshconfig.append("10.0.0.3", "10.0.0.3", "root", path=path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertTrue(path.read_text().startswith("# added by netscan"))


ADDR_JSON = [
    {"ifname": "lo", "flags": ["LOOPBACK", "UP"], "link_type": "loopback",
     "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
    {"ifname": "wlp2s0", "flags": ["UP"], "operstate": "UP", "link_type": "ether",
     "addr_info": [{"family": "inet", "local": "192.168.12.10", "prefixlen": 24},
                   {"family": "inet6", "local": "fe80::1", "prefixlen": 64}]},
    {"ifname": "enp1s0", "flags": ["NO-CARRIER", "UP"], "operstate": "DOWN", "link_type": "ether",
     "addr_info": [{"family": "inet", "local": "10.0.0.2", "prefixlen": 24}]},
    {"ifname": "docker0", "flags": ["UP"], "operstate": "UP", "link_type": "ether",
     "addr_info": [{"family": "inet", "local": "172.17.0.1", "prefixlen": 16}]},
    {"ifname": "tailscale0", "flags": ["UP"], "operstate": "UNKNOWN", "link_type": "none",
     "addr_info": [{"family": "inet", "local": "100.64.0.1", "prefixlen": 32}]},
    {"ifname": "eth1", "flags": ["UP"], "operstate": "UP", "link_type": "ether",
     "addr_info": [{"family": "inet", "local": "10.1.0.5", "prefixlen": 16}]},
    {"ifname": "eth2", "flags": ["UP"], "operstate": "UP", "link_type": "ether",
     "addr_info": [{"family": "inet", "local": "10.2.1.5", "prefixlen": 22}]},
]


class ScanTest(unittest.TestCase):

    def test_subnets(self):
        warnings = []
        nets = scan.subnets_from(ADDR_JSON, warn=warnings.append, is_wireless=lambda name: name.startswith("wl"))
        self.assertEqual([(s.iface, s.address, s.network, s.wireless) for s in nets],
                         [("wlp2s0", "192.168.12.10", "192.168.12.0/24", True),
                          ("eth2", "10.2.1.5", "10.2.0.0/22", False)])
        self.assertEqual(len(warnings), 1)
        self.assertIn("10.1.0.0/16", warnings[0])

    def test_neighbours(self):
        subnet = scan.Subnet("wlp2s0", "192.168.12.10", "192.168.12.0/24")
        neigh = [
            {"dst": "192.168.12.1", "lladdr": "68:7F:F0:00:00:01", "state": ["REACHABLE"]},  # answered ARP only
            {"dst": "192.168.12.5", "lladdr": "aa:bb:cc:00:00:05", "state": ["DELAY"]},  # answered ping
            {"dst": "192.168.12.6", "lladdr": "aa:bb:cc:00:00:06", "state": ["STALE"]},  # maybe gone
            {"dst": "192.168.12.7", "state": ["FAILED"]},
            {"dst": "192.168.12.8", "lladdr": "aa:bb:cc:00:00:08", "state": ["INCOMPLETE"]},
            {"dst": "10.0.0.9", "lladdr": "aa:bb:cc:00:00:09", "state": ["REACHABLE"]},  # other subnet
            {"dst": "fe80::1", "lladdr": "aa:bb:cc:00:00:0a", "router": None, "state": ["REACHABLE"]},
        ]
        devices = scan.neighbours_from(neigh, subnet, answered={"192.168.12.5"})
        self.assertEqual([(d.ip, d.mac, d.ping) for d in devices],
                         [("192.168.12.1", "68:7f:f0:00:00:01", False), ("192.168.12.5", "aa:bb:cc:00:00:05", True)])

    def test_vendors(self):
        with tempfile.TemporaryDirectory() as tmp:
            header = "Registry,Assignment,Organization Name,Organization Address\n"
            Path(tmp, "oui.csv").write_text(header + 'MA-L,2CCF67,"Raspberry Pi (Trading) Ltd",x\nMA-L,70B3D5,IEEE,x\n')
            Path(tmp, "oui36.csv").write_text(header + "MA-S,70B3D5F2F,TELEPLATFORMS,x\n")
            macs = {"2c:cf:67:00:00:01", "70:b3:d5:f2:f0:01", "70:b3:d5:00:00:01", "0a:00:00:00:00:01",
                    "00:00:00:00:00:01"}
            self.assertEqual(scan.vendors(macs, tmp), {
                "2c:cf:67:00:00:01": "Raspberry Pi (Trading) Ltd",
                "70:b3:d5:f2:f0:01": "TELEPLATFORMS",  # longest prefix wins
                "70:b3:d5:00:00:01": "IEEE",
                "0a:00:00:00:00:01": "(random MAC)",
            })

    def test_etc_hosts(self):
        with tempfile.NamedTemporaryFile("w", suffix="hosts") as f:
            f.write("127.0.0.1 localhost\n# 10.0.0.1 commented\n192.168.11.21 uav1 uav1.lab  # drone\n")
            f.flush()
            by_ip, by_name = scan.etc_hosts(f.name)
        self.assertEqual(by_ip, {"127.0.0.1": "localhost", "192.168.11.21": "uav1"})
        self.assertEqual(by_name["uav1.lab"], "192.168.11.21")

    def test_names(self):
        self.assertEqual(scan.clean_name("tars.local"), "tars")
        self.assertEqual(scan.clean_name("_gateway"), "gateway")
        self.assertEqual(scan.short_vendor("TP-Link Corporation Limited"), "TP-Link Corporation")
        self.assertEqual(scan.short_vendor("Raspberry Pi (Trading) Ltd"), "Raspberry Pi (Trading)")
        self.assertEqual(scan.short_vendor("ASUSTek COMPUTER INC."), "ASUSTek COMPUTER")


if __name__ == "__main__":
    unittest.main()

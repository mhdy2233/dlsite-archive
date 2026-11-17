"""Japanese egress via VPN Gate, isolated in its own network namespace.

Why a namespace: the runner must keep talking to GitHub while scraping. A plain
`redirect-gateway` openvpn would rewrite the host default route and could strand
the job. Everything here is confined to netns `dlsitevpn`; the host routing
table is never touched.
"""
import base64
import csv
import io
import os
import subprocess
import time
import urllib.request

CSV_URLS = [
    "https://www.vpngate.net/api/iphone/",
    "http://www.vpngate.net/api/iphone/",
]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"

NETNS = "dlsitevpn"
VETH_H, VETH_N = "veth-dl0", "veth-dl1"
HOST_IP, NS_IP = "10.201.0.1", "10.201.0.2"
SUBNET = "10.201.0.0/24"
OVPN_PATH = "/var/lib/dlsite-vpn/current.ovpn"
OVPN_LOG = "/var/lib/dlsite-vpn/openvpn.log"


def log(msg):
    print(f"[vpn] {msg}", flush=True)


def sh(cmd, check=False):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> {p.returncode} {p.stderr[:200]}")
    return p


def wan_iface():
    p = sh(["ip", "route", "show", "default"])
    for line in p.stdout.splitlines():
        f = line.split()
        if "dev" in f:
            return f[f.index("dev") + 1]
    return "eth0"


def setup_netns():
    os.makedirs(os.path.dirname(OVPN_PATH), exist_ok=True)
    os.makedirs(f"/etc/netns/{NETNS}", exist_ok=True)
    with open(f"/etc/netns/{NETNS}/resolv.conf", "w") as f:
        f.write("nameserver 1.1.1.1\nnameserver 8.8.8.8\n")

    teardown_netns()
    for cmd in (
        ["ip", "netns", "add", NETNS],
        ["ip", "link", "add", VETH_H, "type", "veth", "peer", "name", VETH_N],
        ["ip", "link", "set", VETH_N, "netns", NETNS],
        ["ip", "addr", "add", f"{HOST_IP}/24", "dev", VETH_H],
        ["ip", "link", "set", VETH_H, "up"],
        ["ip", "netns", "exec", NETNS, "ip", "link", "set", "lo", "up"],
        ["ip", "netns", "exec", NETNS, "ip", "link", "set", VETH_N, "name", "eth0"],
        ["ip", "netns", "exec", NETNS, "ip", "addr", "add", f"{NS_IP}/24", "dev", "eth0"],
        ["ip", "netns", "exec", NETNS, "ip", "link", "set", "eth0", "up"],
        ["ip", "netns", "exec", NETNS, "ip", "route", "add", "default", "via", HOST_IP],
    ):
        sh(["sudo"] + cmd, check=True)

    wan = wan_iface()
    sh(["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"], check=False)
    sh(["sudo", "iptables", "-t", "nat", "-A", "POSTROUTING",
        "-s", SUBNET, "-o", wan, "-j", "MASQUERADE"], check=False)
    sh(["sudo", "iptables", "-A", "FORWARD", "-s", SUBNET, "-j", "ACCEPT"], check=False)
    sh(["sudo", "iptables", "-A", "FORWARD", "-d", SUBNET, "-j", "ACCEPT"], check=False)
    log(f"netns {NETNS} up ({HOST_IP} <-> {NS_IP}, nat via {wan})")


def teardown_netns():
    sh(["sudo", "pkill", "-f", f"openvpn --config {OVPN_PATH}"])
    time.sleep(1)
    sh(["sudo", "ip", "netns", "del", NETNS])
    sh(["sudo", "ip", "link", "del", VETH_H])
    sh(["sudo", "iptables", "-t", "nat", "-D", "POSTROUTING",
        "-s", SUBNET, "-j", "MASQUERADE"])
    sh(["sudo", "iptables", "-D", "FORWARD", "-s", SUBNET, "-j", "ACCEPT"])
    sh(["sudo", "iptables", "-D", "FORWARD", "-d", SUBNET, "-j", "ACCEPT"])


def ns_run(cmd, timeout=30):
    """Run a command inside the namespace."""
    return subprocess.run(["sudo", "ip", "netns", "exec", NETNS] + cmd,
                          capture_output=True, text=True, timeout=timeout)


def tun_up():
    return ns_run(["ip", "link", "show", "tun0"]).returncode == 0


def fetch_nodes(country="JP"):
    raw = None
    for url in CSV_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read()
            if raw and len(raw) > 50_000:
                break
        except Exception as e:
            log(f"fetch failed {url}: {type(e).__name__}")
    if not raw:
        raise RuntimeError("cannot reach vpngate api (try a proxy for this step)")
    text = raw.decode("utf-8", "replace")
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("*")]
    nodes = []
    for row in csv.DictReader(io.StringIO("\n".join(lines))):
        if (row.get("CountryShort") or "").strip().upper() != country:
            continue
        b64 = row.get("OpenVPN_ConfigData_Base64") or ""
        if not b64:
            continue
        try:
            cfg = base64.b64decode(b64).decode("utf-8", "replace")
        except Exception:
            continue
        proto, port, remote = "udp", None, None
        for line in cfg.splitlines():
            f = line.split()
            if f and f[0].lower() == "proto" and len(f) > 1:
                proto = f[1].lower()
            elif f and f[0].lower() == "remote" and len(f) > 2:
                remote, port = f[1], f[2]
        if not remote:
            continue
        nodes.append({
            "ip": row.get("IP") or remote, "port": port, "proto": proto,
            "ping": int(row.get("Ping") or 9999),
            "speed": int(row.get("Speed") or 0),
            "config": cfg,
        })
    nodes.sort(key=lambda n: (0 if n["proto"].startswith("tcp") else 1,
                              n["ping"], -n["speed"]))
    return nodes


def write_config(node, path=OVPN_PATH):
    keep, in_block = [], False
    skip = ("block-outside-dns", "dhcp-option", "route-method", "register-dns",
            "windows-driver", "redirect-gateway", "route ", "pull-filter")
    for line in node["config"].splitlines():
        s, low = line.strip(), line.strip().lower()
        if in_block:
            keep.append(line)
            if low.startswith("</"):
                in_block = False
            continue
        if low.startswith("<"):
            keep.append(line)
            if not low.startswith("</"):
                in_block = True
            continue
        if not s:
            continue
        if low.startswith("proto "):
            parts = low.split()
            keep.append({"tcp": "proto tcp-client", "udp": "proto udp"}
                        .get(parts[1] if len(parts) > 1 else "", s))
            continue
        if low.startswith(skip) or s.startswith("#") or s.startswith(";"):
            continue
        keep.append(s)
    keep += ["tls-client", 'pull-filter ignore "dhcp-option"',
             "redirect-gateway def1",
             "data-ciphers AES-128-CBC:AES-256-CBC:AES-128-GCM",
             "auth-nocache", "nobind", "connect-retry-max 1",
             "connect-timeout 12", "server-poll-timeout 15", "verb 3", "mute 5"]
    with open(path, "w") as f:
        f.write("\n".join(keep) + "\n")
    return path


def egress():
    p = ns_run(["curl", "-4", "-sS", "--max-time", "15", "https://ipinfo.io/json"])
    if p.returncode != 0:
        return False, {"error": (p.stderr or "curl failed").strip()[:120]}
    try:
        import json as _j
        d = _j.loads(p.stdout)
        return True, {"ip": d.get("ip"), "country": d.get("country"),
                      "city": d.get("city"), "org": d.get("org")}
    except Exception as e:
        return False, {"error": f"{type(e).__name__}"}


def connect(min_speed_mbps=1, tries=6, ready_timeout=40):
    setup_netns()
    nodes = fetch_nodes()
    if not nodes:
        raise RuntimeError("no JP nodes returned by vpngate")
    log(f"{len(nodes)} JP candidates, trying up to {tries}")
    for node in nodes[:tries]:
        if node["speed"] and node["speed"] < min_speed_mbps * 1_000_000 / 8:
            continue
        sh(["sudo", "pkill", "-f", f"openvpn --config {OVPN_PATH}"])
        time.sleep(1)
        for dst in ("0.0.0.0/1", "128.0.0.0/1"):
            while ns_run(["ip", "route", "del", dst]).returncode == 0:
                pass
        sh(["sudo", "ip", "netns", "exec", NETNS, "ip", "link", "del", "tun0"])
        path = write_config(node)
        log(f"trying {node['ip']}:{node['port']} {node['proto']} ping={node['ping']}ms")
        p = sh(["sudo", "ip", "netns", "exec", NETNS, "openvpn",
                "--config", path, "--daemon", "--log", OVPN_LOG])
        if p.returncode != 0:
            log(f"  openvpn refused: {p.stderr.strip()[:160]}")
            continue
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            time.sleep(3)
            if not tun_up():
                continue
            ok, info = egress()
            if ok and info.get("country") == "JP":
                log(f"  connected: {info['ip']} {info.get('city')} {info.get('org')}")
                return node, info
        log("  handshake/egress timeout")
    teardown_netns()
    raise RuntimeError("no working JP node")


def stop():
    teardown_netns()
    log("netns removed")


if __name__ == "__main__":
    node, info = connect()
    print(node["ip"], info)

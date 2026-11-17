#!/usr/bin/env python3
"""Bring up N independent VPN Gate tunnels, one netns + one SOCKS5 each.

Why: a single VPN Gate node caps around 5 Mbps (measured: host has 675 Mbps,
one tunnel gives 0.66 MB/s). Each extra tunnel is a separate Japanese IP with
its own quota, so throughput scales ~linearly.

  sudo python3 scripts/tunnels.py up --count 3
  python3 scripts/tunnels.py status
  sudo python3 scripts/tunnels.py down

Prints the SOCKS endpoints to feed `run.py --proxy a,b,c`.
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vpngate as vg  # noqa: E402

RUNDIR = "/tmp/dlsite-tunnels"
JPVPN = "/opt/jpvpn/jpvpn.py"
STATE = os.path.join(RUNDIR, "tunnels.json")


def sh(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def ns(i):
    return f"dlsite{i}"


def addrs(i):
    return f"10.31.{i}.1", f"10.31.{i}.2", f"10.31.{i}.0/24"


def port(i):
    return 1090 + i


def teardown(i):
    n = ns(i)
    _, ns_ip, subnet = addrs(i)
    sh(["sudo", "pkill", "-f", f"openvpn --config {RUNDIR}/{i}.ovpn"])
    sh(["sudo", "pkill", "-f", f"socks --host {ns_ip}"])
    time.sleep(0.5)
    sh(["sudo", "ip", "netns", "del", n])
    sh(["sudo", "ip", "link", "del", f"veth-dl{i}a"])
    sh(["sudo", "iptables", "-t", "nat", "-D", "POSTROUTING", "-s", subnet,
        "-o", vg.wan_iface(), "-j", "MASQUERADE"])
    sh(["sudo", "iptables", "-D", "FORWARD", "-s", subnet, "-j", "ACCEPT"])
    sh(["sudo", "iptables", "-D", "FORWARD", "-d", subnet, "-j", "ACCEPT"])


def setup_netns(i):
    n = ns(i)
    host_ip, ns_ip, subnet = addrs(i)
    vh, vn = f"veth-dl{i}a", f"veth-dl{i}b"
    teardown(i)
    steps = [
        ["ip", "netns", "add", n],
        ["ip", "link", "add", vh, "type", "veth", "peer", "name", vn],
        ["ip", "link", "set", vn, "netns", n],
        ["ip", "addr", "add", f"{host_ip}/24", "dev", vh],
        ["ip", "link", "set", vh, "up"],
        ["ip", "netns", "exec", n, "ip", "link", "set", "lo", "up"],
        ["ip", "netns", "exec", n, "ip", "link", "set", vn, "name", "eth0"],
        ["ip", "netns", "exec", n, "ip", "addr", "add", f"{ns_ip}/24", "dev", "eth0"],
        ["ip", "netns", "exec", n, "ip", "link", "set", "eth0", "up"],
        ["ip", "netns", "exec", n, "ip", "route", "add", "default", "via", host_ip],
    ]
    for c in steps:
        p = sh(["sudo"] + c)
        if p.returncode != 0 and "File exists" not in p.stderr:
            raise RuntimeError(f"{' '.join(c)}: {p.stderr.strip()[:120]}")

    sh(["sudo", "mkdir", "-p", f"/etc/netns/{n}"])
    rc = os.path.join(RUNDIR, f"{i}.resolv")
    with open(rc, "w") as f:
        f.write("nameserver 1.1.1.1\nnameserver 8.8.8.8\n")
    sh(["sudo", "cp", rc, f"/etc/netns/{n}/resolv.conf"])

    sh(["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"])
    sh(["sudo", "iptables", "-t", "nat", "-A", "POSTROUTING", "-s", subnet,
        "-o", vg.wan_iface(), "-j", "MASQUERADE"])
    sh(["sudo", "iptables", "-A", "FORWARD", "-s", subnet, "-j", "ACCEPT"])
    sh(["sudo", "iptables", "-A", "FORWARD", "-d", subnet, "-j", "ACCEPT"])


def ns_egress(i, timeout=15):
    _, ns_ip, _ = addrs(i)
    p = sh(["sudo", "ip", "netns", "exec", ns(i), "curl", "-4", "-sS",
            "--max-time", str(timeout), "https://ipinfo.io/json"], timeout=timeout + 10)
    if p.returncode != 0:
        return None
    try:
        d = json.loads(p.stdout)
        return d if d.get("country") == "JP" else None
    except Exception:
        return None


def start_socks(i):
    n = ns(i)
    _, ns_ip, _ = addrs(i)
    log = open(os.path.join(RUNDIR, f"{i}.socks.log"), "w")
    subprocess.Popen(
        ["sudo", "ip", "netns", "exec", n, "python3", JPVPN, "socks",
         "--host", ns_ip, "--port", str(port(i))],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(1.5)


def bring_up(i, node, ready_timeout=25):
    n = ns(i)
    os.makedirs(RUNDIR, exist_ok=True)
    setup_netns(i)
    cfg = os.path.join(RUNDIR, f"{i}.ovpn")
    vg.write_config(node, cfg)
    p = sh(["sudo", "ip", "netns", "exec", n, "openvpn", "--config", cfg,
            "--daemon", "--log", os.path.join(RUNDIR, f"{i}.openvpn.log")])
    if p.returncode != 0:
        print(f"  [{i}] openvpn refused: {p.stderr.strip()[:120]}", flush=True)
        return None
    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        time.sleep(2)
        if sh(["pgrep", "-f", f"openvpn --config {cfg}"]).returncode != 0:
            return None
        if sh(["sudo", "ip", "netns", "exec", n, "ip", "link", "show", "tun0"]).returncode != 0:
            continue
        info = ns_egress(i)
        if info:
            start_socks(i)
            return info
    return None


def cmd_up(args):
    os.makedirs(RUNDIR, exist_ok=True)
    nodes = vg.fetch_nodes()
    if not nodes:
        print("no JP nodes")
        return 1
    print(f"{len(nodes)} JP candidates available", flush=True)
    st = {"tunnels": []}
    if os.path.exists(STATE):
        try:
            st = json.load(open(STATE))
        except Exception:
            st = {"tunnels": []}
    known = {t["index"]: t for t in st.get("tunnels", [])}
    used = {t["node"] for t in st.get("tunnels", [])}
    for i in range(args.start_index, args.start_index + args.count):
        if i in known and ns_egress(i, timeout=8):
            print(f"[{i}] already up: {known[i]['endpoint']}", flush=True)
            continue
        ok = None
        tried = 0
        for node in nodes:
            if node["ip"] in used:
                continue
            tried += 1
            if tried > args.tries_per_tunnel:
                break
            print(f"[{i}] trying {node['ip']}:{node['port']} {node['proto']} "
                  f"ping={node['ping']}ms", flush=True)
            info = bring_up(i, node)
            if info:
                used.add(node["ip"])
                _, ns_ip, _ = addrs(i)
                ep = f"socks5h://{ns_ip}:{port(i)}"
                ok = {"index": i, "node": node["ip"], "port": node["port"],
                      "proto": node["proto"], "endpoint": ep, **info}
                print(f"[{i}] UP {ep} -> {info['ip']} {info.get('city')}", flush=True)
                break
            teardown(i)
        if ok:
            known[i] = ok
        else:
            print(f"[{i}] could not establish a tunnel", flush=True)

    st["tunnels"] = [known[k] for k in sorted(known)]
    with open(STATE, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    eps = [t["endpoint"] for t in st["tunnels"]]
    print("\nPROXIES=" + ",".join(eps) if eps else "\nno tunnel is up")
    return 0 if eps else 1


def cmd_status(args):
    if not os.path.exists(STATE):
        print("no tunnels recorded")
        return 1
    st = json.load(open(STATE))
    alive = 0
    for t in st["tunnels"]:
        i = t["index"]
        up = sh(["sudo", "ip", "link", "show", "tun0"]).returncode == 0
        tun = sh(["sudo", "ip", "netns", "exec", ns(i), "ip", "link", "show", "tun0"]).returncode == 0
        info = ns_egress(i, timeout=10) if tun else None
        mark = "ok " if info else "DEAD"
        if info:
            alive += 1
        print(f"[{i}] {mark} {t['endpoint']:<30} node={t['node']} "
              f"egress={(info or {}).get('ip', '-')}")
    print(f"\n{alive}/{len(st['tunnels'])} tunnels alive")
    return 0


def cmd_down(args):
    if os.path.exists(STATE):
        st = json.load(open(STATE))
        for t in st["tunnels"]:
            teardown(t["index"])
    for i in range(1, 10):
        teardown(i)
    if os.path.exists(STATE):
        os.remove(STATE)
    print("all tunnels down")
    return 0


def cmd_watch(args):
    """Keep N tunnels alive: probe, rebuild dead ones, repeat forever."""
    os.makedirs(RUNDIR, exist_ok=True)
    while True:
        st = {"tunnels": []}
        if os.path.exists(STATE):
            try:
                st = json.load(open(STATE))
            except Exception:
                pass
        by_index = {t["index"]: t for t in st.get("tunnels", [])}
        nodes = None
        changed = False
        for i in range(1, args.count + 1):
            if ns_egress(i, timeout=8):
                print(f"[{i}] ok", flush=True)
                continue
            print(f"[{i}] dead -> rebuilding", flush=True)
            if nodes is None:
                nodes = vg.fetch_nodes()
            used = {t["node"] for t in by_index.values() if t["index"] != i}
            ok = None
            for node in nodes[: args.tries_per_tunnel]:
                if node["ip"] in used:
                    continue
                info = bring_up(i, node)
                if info:
                    _, ns_ip, _ = addrs(i)
                    ok = {"index": i, "node": node["ip"], "port": node["port"],
                          "proto": node["proto"],
                          "endpoint": f"socks5h://{ns_ip}:{port(i)}", **info}
                    print(f"[{i}] UP {ok['endpoint']} -> {info['ip']} "
                          f"{info.get('city')}", flush=True)
                    break
                teardown(i)
            if ok:
                by_index[i] = ok
                changed = True
            else:
                print(f"[{i}] rebuild failed", flush=True)
        if changed:
            st["tunnels"] = [by_index[k] for k in sorted(by_index)]
            with open(STATE, "w") as f:
                json.dump(st, f, ensure_ascii=False, indent=1)
        time.sleep(args.interval)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up")
    up.add_argument("--count", type=int, default=3)
    up.add_argument("--start-index", type=int, default=1,
                    help="first tunnel index; use >1 to add tunnels without touching existing ones")
    up.add_argument("--tries-per-tunnel", type=int, default=8)
    up.set_defaults(func=cmd_up)
    stp = sub.add_parser("status")
    stp.set_defaults(func=cmd_status)
    w = sub.add_parser("watch")
    w.add_argument("--count", type=int, default=3)
    w.add_argument("--interval", type=int, default=120)
    w.add_argument("--tries-per-tunnel", type=int, default=8)
    w.set_defaults(func=cmd_watch)
    dn = sub.add_parser("down")
    dn.set_defaults(func=cmd_down)
    a = ap.parse_args()
    sys.exit(a.func(a))

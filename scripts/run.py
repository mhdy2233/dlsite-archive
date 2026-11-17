#!/usr/bin/env python3
"""Entry point: bring up a JP egress (optional), collect new works, archive them.

  python3 scripts/run.py --limit 50                       # CI: vpngate tunnel
  python3 scripts/run.py --limit 50 --proxy socks5h://127.0.0.1:1081   # local
  python3 scripts/run.py --limit 5 --no-vpn --proxy ...   # quick test
"""
import argparse
import concurrent.futures as cf
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import scraper  # noqa: E402

DATA = os.path.join(ROOT, "data")
WORKS = os.path.join(DATA, "works")
IMAGES = os.path.join(ROOT, "site", "images")
STATE = os.path.join(DATA, "state.json")


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"done": [], "runs": 0}


def save_state(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, help="how many new works to archive")
    ap.add_argument("--proxy", default=None, help="socks5h://127.0.0.1:1081 for local runs")
    ap.add_argument("--no-vpn", action="store_true", help="skip vpngate setup")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-pages", type=int, default=120)
    args = ap.parse_args()

    os.makedirs(WORKS, exist_ok=True)
    os.makedirs(IMAGES, exist_ok=True)
    st = load_state()
    done = set(st["done"])
    scraper.log(f"already archived: {len(done)} works")

    vpn = None
    netns = None
    if not args.no_vpn:
        import vpngate
        vpn = vpngate
        netns = vpngate.NETNS
        node, info = vpngate.connect()
        scraper.log(f"egress: {info.get('ip')} {info.get('city')}")

    try:
        ids, page, total_pages = scraper.collect_ids(
            args.limit, proxy=args.proxy, existing=done, max_pages=args.max_pages,
            netns=netns)
        scraper.log(f"{len(ids)} new ids to fetch (listing page {page}/{total_pages})")

        def work_one(pid):
            t0 = time.time()
            work, err = scraper.fetch_work(pid, proxy=args.proxy, netns=netns)
            if not work:
                return pid, None, err
            try:
                scraper.save_images(work, IMAGES, proxy=args.proxy, netns=netns)
            except Exception as e:
                work["image_error"] = f"{type(e).__name__}: {e}"
            with open(os.path.join(WORKS, pid + ".json"), "w") as f:
                json.dump(work, f, ensure_ascii=False, indent=1)
            return pid, work, f"{time.time() - t0:.1f}s"

        ok = fail = 0
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            for pid, work, msg in ex.map(work_one, ids):
                if work:
                    ok += 1
                    done.add(pid)
                    n = len(work["local_images"].get("samples", []))
                    scraper.log(f"  {pid} ok  imgs={n + 1} ({msg}) {work['title'][:38]}")
                else:
                    fail += 1
                    scraper.log(f"  {pid} FAIL {msg}")

        st = {
            "done": sorted(done),
            "runs": st.get("runs", 0) + 1,
            "last_run": int(time.time()),
            "last_ok": ok,
            "last_fail": fail,
        }
        save_state(st)
        scraper.log(f"done: {ok} ok / {fail} failed, total archived {len(done)}")
        return 0 if ok else 1
    finally:
        if vpn and not args.no_vpn:
            vpn.stop()
            scraper.log("tunnel down")


if __name__ == "__main__":
    sys.exit(main())

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
IMAGES = os.path.join(ROOT, "docs", "images")
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
    ap.add_argument("--proxy", default=None,
                    help="socks5h://127.0.0.1:1081 for local runs, comma-separated for multiple egress IPs")
    ap.add_argument("--no-vpn", action="store_true", help="skip vpngate setup")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--with-images", action="store_true",
                    help="download official images into docs/images (default: CDN links only)")
    ap.add_argument("--max-pages", type=int, default=120)
    ap.add_argument("--start-page", type=int, default=1,
                    help="listing page to start from (older pages = backfill)")
    ap.add_argument("--no-stop", action="store_true",
                    help="keep paging even when a page is entirely archived")
    args = ap.parse_args()

    os.makedirs(WORKS, exist_ok=True)
    os.makedirs(IMAGES, exist_ok=True)
    proxies = [p.strip() for p in (args.proxy or "").split(",") if p.strip()] or [None]
    if len(proxies) > 1:
        scraper.log(f"egress pool: {len(proxies)} endpoints, round-robin")
    st = load_state()
    done = set(st["done"])
    scraper.log(f"already archived: {len(done)} works")

    vpn = None
    netns = None
    mode = None
    if not args.no_vpn:
        import vpngate
        vpn = vpngate
        node, info, mode = vpngate.connect()
        netns = vpngate.NETNS if mode == "netns" else None
        scraper.log(f"egress [{mode}]: {info.get('ip')} {info.get('city')}")

    try:
        ids, page, total_pages = scraper.collect_ids(
            args.limit, existing=done, max_pages=args.max_pages,
            netns=netns, start_page=args.start_page, proxies=proxies,
            stop_on_old=(args.start_page == 1 and not args.no_stop))
        scraper.log(f"{len(ids)} new ids to fetch (listing page {page}/{total_pages})")

        def work_one(item):
            idx, pid = item
            t0 = time.time()
            work, err = None, "no attempt"
            # a tunnel can drop mid-run: retry on another endpoint from the pool
            for attempt in range(min(len(proxies), 3)):
                proxy = proxies[(idx + attempt) % len(proxies)]
                work, err = scraper.fetch_work(pid, proxy=proxy, netns=netns)
                if work:
                    break
                time.sleep(1)
            if not work:
                return pid, None, err
            if args.with_images:
                try:
                    scraper.save_images(work, IMAGES, proxy=proxy, netns=netns)
                except Exception as e:
                    work["image_error"] = f"{type(e).__name__}: {e}"
            with open(os.path.join(WORKS, pid + ".json"), "w") as f:
                json.dump(work, f, ensure_ascii=False, indent=1)
            return pid, work, f"{time.time() - t0:.1f}s"

        ok = fail = 0
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            for pid, work, msg in ex.map(work_one, enumerate(ids)):
                if work:
                    ok += 1
                    done.add(pid)
                    local = (work.get("local_images") or {}).get("samples")
                    tag = f"imgs={len(local) + 1}" if local else "cdn"
                    scraper.log(f"  {pid} ok  {tag} ({msg}) {work['title'][:38]}")
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
        # "nothing new to archive" is a normal, successful outcome
        return 0 if fail == 0 else 1
    finally:
        if vpn and not args.no_vpn:
            vpn.stop(mode == "netns")
            scraper.log("tunnel down")


if __name__ == "__main__":
    sys.exit(main())

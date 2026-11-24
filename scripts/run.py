#!/usr/bin/env python3
"""Entry point: bring up a JP egress (optional), collect new works, archive them.

  python3 scripts/run.py --category game  --limit 50
  python3 scripts/run.py --category audio --limit 50 --start-page 2
  python3 scripts/run.py --limit 10 --no-vpn --proxy socks5h://127.0.0.1:1081
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
STATE = os.path.join(DATA, "state.json")
API = os.path.join(ROOT, "docs", "api")


def works_dir(category):
    return os.path.join(API, category, "works")


def load_state():
    """{"game": {"done": [...]}, "audio": {...}}  (migrates the old flat shape)"""
    if not os.path.exists(STATE):
        return {}
    with open(STATE) as f:
        st = json.load(f)
    if "done" in st:                      # legacy single-category file
        return {"game": st}
    return st


def save_state(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, STATE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="game", choices=list(scraper.CATEGORIES))
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--proxy", default=None,
                    help="socks5h://127.0.0.1:1081, comma-separated for a pool")
    ap.add_argument("--no-vpn", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--with-images", action="store_true",
                    help="download official images (default: CDN links only)")
    ap.add_argument("--max-pages", type=int, default=120)
    ap.add_argument("--start-page", type=int, default=1)
    ap.add_argument("--no-stop", action="store_true")
    args = ap.parse_args()

    cat = args.category
    out_dir = works_dir(cat)
    os.makedirs(out_dir, exist_ok=True)
    proxies = [p.strip() for p in (args.proxy or "").split(",") if p.strip()] or [None]
    if len(proxies) > 1:
        scraper.log(f"egress pool: {len(proxies)} endpoints, round-robin")

    st = load_state()
    cst = st.setdefault(cat, {"done": [], "runs": 0})
    done = set(cst.get("done", []))
    failed = set(cst.get("failed", []))
    scraper.log(f"[{cat}] already archived: {len(done)} works"
                f"{f' / {len(failed)} pending retry' if failed else ''}")

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
        ids = []
        if failed and args.start_page == 1 and not args.no_stop:
            ids = sorted(failed)[:args.limit]
            scraper.log(f"[{cat}] retrying {len(ids)} previously failed works first")
        if len(ids) < args.limit:
            more, page, total_pages = scraper.collect_ids(
                args.limit - len(ids), existing=done | set(ids),
                max_pages=args.max_pages, netns=netns,
                start_page=args.start_page, proxies=proxies, category=cat,
                stop_on_old=(args.start_page == 1 and not args.no_stop))
            ids += more
        scraper.log(f"[{cat}] {len(ids)} ids to fetch")

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
                    scraper.save_images(work, os.path.join(ROOT, "docs", "images"),
                                        proxy=proxies[idx % len(proxies)], netns=netns)
                except Exception as e:
                    work["image_error"] = f"{type(e).__name__}: {e}"
            with open(os.path.join(out_dir, pid + ".json"), "w", encoding="utf-8") as f:
                json.dump(work, f, ensure_ascii=False, separators=(",", ":"))
            return pid, work, f"{time.time() - t0:.1f}s"

        ok = fail = 0
        failed_now = []
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            for pid, work, msg in ex.map(work_one, enumerate(ids)):
                if work:
                    ok += 1
                    done.add(pid)
                    failed.discard(pid)
                    local = (work.get("local_images") or {}).get("samples")
                    tag = f"imgs={len(local) + 1}" if local else "cdn"
                    scraper.log(f"  {pid} ok  {tag} ({msg}) {work['title'][:38]}")
                else:
                    fail += 1
                    failed_now.append(pid)
                    failed.add(pid)
                    scraper.log(f"  {pid} FAIL {msg}")

        cst.update({
            "done": sorted(done),
            "failed": sorted(failed),
            "runs": cst.get("runs", 0) + 1,
            "last_run": int(time.time()),
            "last_ok": ok,
            "last_fail": fail,
        })
        save_state(st)
        scraper.log(f"[{cat}] done: {ok} ok / {fail} failed, total {len(done)}")
        # "nothing new to archive" is a normal, successful outcome
        return 0 if fail == 0 else 1
    finally:
        if vpn and not args.no_vpn:
            vpn.stop(mode == "netns")
            scraper.log("tunnel down")


if __name__ == "__main__":
    sys.exit(main())

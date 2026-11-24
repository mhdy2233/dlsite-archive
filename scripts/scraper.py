"""DLsite scraper: listing pages, work detail pages, official images.

All HTTP goes through `curl` so a SOCKS/HTTP proxy can be used locally
(`--proxy socks5h://127.0.0.1:1081`) while CI just goes direct through the
VPN tunnel.
"""
import json
import os
import re
import subprocess
import time

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://www.dlsite.com"
LIST_TPL = {
    "game": (BASE + "/maniax/fsr/=/work_category%5B0%5D/doujin"
             "/work_type_category%5B0%5D/game/order%5B0%5D/release_d"
             "/page/{page}/"),
    "audio": (BASE + "/maniax/fsr/=/work_category%5B0%5D/doujin"
              "/work_type_category%5B0%5D/audio/order%5B0%5D/release_d"
              "/page/{page}/"),
}
CATEGORIES = {
    "game": {"label": "同人ゲーム", "code": "game"},
    "audio": {"label": "ボイス・ASMR", "code": "audio"},
}
WORK_TPL = BASE + "/maniax/work/=/product_id/{pid}.html"
IMG = "https://img.dlsite.jp"

AGE_MAP = {1: "all", 2: "r15", 3: "r18"}


def log(msg):
    print(f"[scrape] {msg}", flush=True)


def _curl(netns=None):
    """curl prefix; scrapes run inside the vpn namespace in CI."""
    if netns:
        return ["sudo", "ip", "netns", "exec", netns, "curl"]
    return ["curl"]


def http_get(url, proxy=None, timeout=60, retries=3, netns=None):
    cmd = _curl(netns) + ["-4", "-sS", "-L", "--max-time", str(timeout), "-A", UA,
                          "-H", "Accept-Language: ja"]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(url)
    last = b""
    for attempt in range(retries):
        p = subprocess.run(cmd, capture_output=True)
        last = p.stdout or b""
        if p.returncode == 0 and last:
            return last
        if p.returncode == 28 and len(last) > 100_000:
            return last                      # slow node, page is still complete
        time.sleep(2 * (attempt + 1))
    return last


def download(url, dest, proxy=None, timeout=90, netns=None):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return True
    cmd = _curl(netns) + ["-4", "-sS", "-L", "--max-time", str(timeout), "-A", UA,
                          "-H", "Referer: https://www.dlsite.com/"]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd += ["-o", dest + ".part", url]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode == 0 and os.path.exists(dest + ".part") \
            and os.path.getsize(dest + ".part") > 1000:
        os.replace(dest + ".part", dest)
        return True
    if os.path.exists(dest + ".part"):
        os.remove(dest + ".part")
    return False


# ---------------------------------------------------------------- listing

def list_page(page, proxy=None, netns=None, category="game"):
    tpl = LIST_TPL.get(category) or LIST_TPL["game"]
    html = http_get(tpl.format(page=page), proxy=proxy,
                    netns=netns).decode("utf-8", "replace")
    ids = []
    for m in re.finditer(r'data-list_item_product_id="(RJ\d+)"', html):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    total_pages = 1
    m = re.search(r'/page/(\d+)/', html)
    if m:
        total_pages = max(int(x) for x in re.findall(r'/page/(\d+)/', html))
    return ids, total_pages, html


def collect_ids(want, proxy=None, start_page=1, max_pages=200, existing=(),
                netns=None, stop_on_old=True, proxies=None, category="game"):
    """Walk listing pages until `want` unseen ids are collected.

    stop_on_old=True is the incremental mode: a full page of already-archived ids
    means we have caught up. Backfills pass False to keep walking older pages.
    `proxies` rotates the egress per page (a single `proxy` still works).
    """
    plist = proxies or ([proxy] if proxy else [None])
    out, page, total = [], start_page, None
    last_page = start_page + max_pages - 1   # max_pages counts pages walked, not an absolute cap
    misses = 0
    while len(out) < want and page <= last_page:
        # one dead endpoint must not abort the walk: try a few before giving up
        ids, total_pages, html = [], None, ""
        for attempt in range(min(len(plist), 3)):
            p = plist[(page - start_page + attempt) % len(plist)]
            ids, total_pages, html = list_page(page, proxy=p, netns=netns, category=category)
            if ids:
                break
            time.sleep(2)
        total = total_pages
        if not ids:
            log(f"page {page}: no ids (blocked or end)")
            misses += 1
            if misses >= 2:
                break
            page += 1
            time.sleep(3)
            continue
        misses = 0
        fresh = [i for i in ids if i not in existing and i not in out]
        out.extend(fresh)
        log(f"page {page}/{total}: {len(ids)} ids, {len(fresh)} new (total {len(out)})")
        if not fresh:
            if stop_on_old:
                log("all ids already archived - stopping")
                break
            log(f"page {page}: known ids only, continuing backfill")
        page += 1
        time.sleep(1.5)
    return out[:want], page, total


# ---------------------------------------------------------------- detail

def _s(v, default=""):
    return v if isinstance(v, str) else (default if v is None else str(v))


def _abs(u):
    if not u:
        return None
    return ("https:" + u) if u.startswith("//") else u


def normalize(p, pid):
    maker = p.get("maker") or {}
    wtype = p.get("work_type") or {}
    price = p.get("currency_price") or {}
    official = p.get("currency_official_price") or {}
    rating = p.get("rating") or {}
    review = p.get("review") or {}
    main = p.get("image_main") or {}
    samples = p.get("image_samples") or []
    genres = [{"key": g.get("genre_key"), "name": g.get("genre_name")}
              for g in (p.get("genres") or []) if isinstance(g, dict)]

    # official images: full-size main + samples, plus the official 240 thumb
    main_full = _abs(main.get("original_url"))
    thumb = _abs(main.get("resized_url"))
    sample_full = [_abs(s.get("original_url")) for s in samples if s.get("original_url")]
    sample_thumb = [_abs(s.get("resized_url")) for s in samples if s.get("resized_url")]

    age_raw = p.get("age_category")
    return {
        "id": pid,
        "title": _s(p.get("work_name")),
        "title_romaji": _s(p.get("work_name_romaji")),
        "circle": {
            "id": maker.get("maker_id"),
            "name": _s(maker.get("maker_name")),
            "url": maker.get("url"),
        },
        "type": {
            "code": wtype.get("work_type_value"),
            "name": _s(wtype.get("work_type_name")),
            "category": wtype.get("work_type_category_id"),
        },
        "category": _s(p.get("work_category")),
        "age": AGE_MAP.get(age_raw, _s(age_raw)),
        "sex": p.get("sex_category"),
        "price": {
            "jpy": price.get("JPY"),
            "official_jpy": official.get("JPY"),
            "currencies": {k: v for k, v in price.items() if k != "JPY"},
        },
        "point": p.get("point"),
        "release": p.get("release_date"),
        "file": {
            "type": _s(p.get("file_type")),
            "size": p.get("contents_file_size") or p.get("file_size"),
        },
        "tags": genres,
        "languages": p.get("work_languages") or [],
        "translation": p.get("translation_info") or {},
        "language_editions": p.get("language_editions") or [],
        "rating": {
            "average": rating.get("average"),
            "count": rating.get("count") if isinstance(rating.get("count"), int) else None,
        },
        "review_count": review.get("count"),
        "dl_count": p.get("dl_count"),
        "wishlist_count": p.get("wishlist_count"),
        "ranking": p.get("ranking") or [],
        "images": {
            "main": main_full,
            "thumb": thumb,
            "samples": sample_full,
            "sample_thumbs": sample_thumb,
            "alt": main.get("alt_name"),
        },
        "introductions": p.get("introductions") or [],
        "description": _s(p.get("description")),
        "short_introduction": _s(p.get("short_introduction")),
        "options": p.get("options") or [],
        "url": p.get("url") or WORK_TPL.format(pid=pid),
        "site_id": p.get("site_id"),
        "fetched_at": int(time.time()),
    }


def fetch_work(pid, proxy=None, netns=None):
    from devalue import extract_payload, find_product
    html = http_get(WORK_TPL.format(pid=pid), proxy=proxy,
                    netns=netns).decode("utf-8", "replace")
    if len(html) < 100_000:
        return None, "short page"
    payload = extract_payload(html)
    if not payload:
        return None, "no nuxt payload"
    p = find_product(payload)
    if not p:
        return None, "no product object"
    return normalize(p, pid), None


def save_images(work, img_root, proxy=None, max_samples=6, netns=None):
    """Store the official full-size images under img_root/<id>/."""
    pid = work["id"]
    d = os.path.join(img_root, pid)
    imgs = work["images"]
    saved = {"main": None, "thumb": None, "samples": []}
    if imgs.get("main"):
        dst = os.path.join(d, "main" + os.path.splitext(imgs["main"])[1])
        if download(imgs["main"], dst, proxy, netns=netns):
            saved["main"] = os.path.relpath(dst, img_root).replace(os.sep, "/")
    if imgs.get("thumb"):
        dst = os.path.join(d, "thumb" + os.path.splitext(imgs["thumb"])[1])
        if download(imgs["thumb"], dst, proxy, netns=netns):
            saved["thumb"] = os.path.relpath(dst, img_root).replace(os.sep, "/")
    for i, url in enumerate((imgs.get("samples") or [])[:max_samples], 1):
        dst = os.path.join(d, f"smp{i}" + os.path.splitext(url)[1])
        if download(url, dst, proxy, netns=netns):
            saved["samples"].append(os.path.relpath(dst, img_root).replace(os.sep, "/"))
    work["local_images"] = saved
    return saved

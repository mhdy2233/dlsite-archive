#!/usr/bin/env python3
"""Build the static site payload for every category.

Layout:
  docs/api/<cat>/works/<RJ>.json   full detail (written by run.py, source of truth)
  docs/api/<cat>/index.json        card index (search + grid)
  docs/api/<cat>/meta.json         facets (types / tags / languages)
  docs/api/categories.json         category list for the UI switcher
"""
import json
import os
import re
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import scraper  # noqa: E402

SITE = os.path.join(ROOT, "docs")
API = os.path.join(SITE, "api")
CATEGORIES = scraper.CATEGORIES


def log(m):
    print(f"[build] {m}", flush=True)


def search_text(w, limit=140):
    """Flatten the intro/description into one searchable string."""
    parts = []
    if w.get("short_introduction"):
        parts.append(w["short_introduction"])
    desc = w.get("description")
    if desc and desc not in parts:
        parts.append(desc)
    for sec in (w.get("introductions") or []):
        if sec.get("title"):
            parts.append(sec["title"])
        for b in (sec.get("body") or []):
            if b.get("intro"):
                parts.append(b["intro"])
    txt = " ".join(parts)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit]


def card(w, cat):
    imgs = w.get("local_images") or {}
    thumb = imgs.get("thumb") or imgs.get("main")
    if thumb:
        thumb = "images/" + thumb
    else:
        # no local copy: hotlink DLsite's own CDN (verified: no referer checks)
        thumb = w["images"].get("thumb") or w["images"].get("main") or ""
        if thumb.startswith("//"):
            thumb = "https:" + thumb
    return {
        "i": w["id"],
        "t": w["title"],
        "rm": (w.get("title_romaji") or "")[:70],
        "d": search_text(w),
        "c": w["circle"]["name"],
        "ci": w["circle"]["id"],
        "ty": w["type"]["name"],
        "tc": w["type"]["code"],
        "p": w["price"]["jpy"],
        "po": w["price"]["official_jpy"],
        "r": w["rating"]["average"],
        "rc": w["rating"]["count"],
        "dl": w["dl_count"],
        "th": thumb,
        "rel": w["release"],
        "g": [t["name"] for t in w["tags"]],
        "age": w["age"],
        "lg": w["languages"],
        "fs": w["file"]["size"],
        "cat": cat,
    }


def build_category(cat):
    works_dir = os.path.join(API, cat, "works")
    if not os.path.isdir(works_dir):
        log(f"[{cat}] no works directory - skipped")
        return None
    cards, types, tags, langs = [], Counter(), Counter(), Counter()
    files = [f for f in os.listdir(works_dir) if f.endswith(".json")]
    for fn in files:
        try:
            with open(os.path.join(works_dir, fn), encoding="utf-8") as f:
                w = json.load(f)
        except Exception:
            continue
        cards.append(card(w, cat))
        types[w["type"]["name"]] += 1
        for t in w["tags"]:
            tags[t["name"]] += 1
        for lg in w["languages"]:
            langs[lg] += 1

    cards.sort(key=lambda c: c["rel"] or 0, reverse=True)
    with open(os.path.join(API, cat, "index.json"), "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(API, cat, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "category": cat,
            "label": CATEGORIES[cat]["label"],
            "count": len(cards),
            "updated": int(time.time()),
            "types": [{"name": k, "n": v} for k, v in types.most_common()],
            "tags": [{"name": k, "n": v} for k, v in tags.most_common(160)],
            "langs": [{"name": k, "n": v} for k, v in langs.most_common()],
        }, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[{cat}] {len(cards)} works, {len(types)} types, {len(tags)} tags")
    return {"cat": cat, "label": CATEGORIES[cat]["label"], "count": len(cards)}


def main():
    os.makedirs(API, exist_ok=True)
    summary = []
    for cat in CATEGORIES:
        info = build_category(cat)
        if info:
            summary.append(info)

    with open(os.path.join(API, "categories.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": int(time.time()), "categories": summary},
                  f, ensure_ascii=False, separators=(",", ":"))
    log(f"categories: {[(c['cat'], c['count']) for c in summary]}")

    # SPA fallback: /RJ01530415 has no file on disk, so GitHub Pages serves 404.html
    src = os.path.join(SITE, "index.html")
    if os.path.exists(src):
        with open(src, encoding="utf-8") as f:
            html = f.read()
        if "<base " not in html:
            html = html.replace("<head>", '<head>\n<base href="/">', 1)
        with open(os.path.join(SITE, "404.html"), "w", encoding="utf-8") as f:
            f.write(html)
        log("wrote 404.html (SPA fallback for /RJxxxxx)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

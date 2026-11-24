#!/usr/bin/env python3
"""Flatten HTML inside `introductions[*].body[*].intro` to plain text.

Why: the intro HTML carries the bulk of each work's JSON (60% for voice works)
and pushes the published site over the 1 GB Pages limit. All the *text* is kept
— only tags/attributes are dropped, and block boundaries become newlines.

Idempotent: a record with no tags left is skipped, so it is safe to run before
every build.

  python3 scripts/compress_intros.py            # all categories
  python3 scripts/compress_intros.py --cat audio
"""
import argparse
import glob
import html as html_lib
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "docs", "api")

BLOCK_END = re.compile(r"</(p|div|h[1-6]|li|tr)\s*>", re.I)
BR = re.compile(r"<br\s*/?>", re.I)
TAG = re.compile(r"<[^>]+>")
MANY_NL = re.compile(r"\n{3,}")
MANY_SP = re.compile(r"[ \t]{2,}")


def strip_html(s):
    if not isinstance(s, str) or "<" not in s:
        return s
    t = BR.sub("\n", s)
    t = BLOCK_END.sub("\n\n", t)
    t = TAG.sub("", t)
    t = html_lib.unescape(t)
    t = MANY_SP.sub(" ", t)
    t = MANY_NL.sub("\n\n", t)
    return t.strip()


def compress_work(w):
    """Return True if anything changed."""
    changed = False
    for sec in (w.get("introductions") or []):
        if isinstance(sec.get("title"), str) and "<" in sec["title"]:
            sec["title"] = strip_html(sec["title"])
            changed = True
        for b in (sec.get("body") or []):
            intro = b.get("intro")
            if isinstance(intro, str) and "<" in intro:
                b["intro"] = strip_html(intro)
                changed = True
    desc = w.get("description")
    if isinstance(desc, str) and "<" in desc:
        w["description"] = strip_html(desc)
        changed = True
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", default=None, help="only this category")
    args = ap.parse_args()

    cats = [args.cat] if args.cat else sorted(os.listdir(API))
    total = touched = 0
    before = after = 0
    for cat in cats:
        wd = os.path.join(API, cat, "works")
        if not os.path.isdir(wd):
            continue
        n = c = 0
        for fn in os.listdir(wd):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(wd, fn)
            sz = os.path.getsize(p)
            with open(p, encoding="utf-8") as f:
                w = json.load(f)
            if compress_work(w):
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(w, f, ensure_ascii=False, separators=(",", ":"))
                after += os.path.getsize(p)
                c += 1
            else:
                after += sz
            before += sz
            n += 1
        print(f"[{cat}] {c}/{n} works compressed", flush=True)
        total += n
        touched += c
    print(f"total {touched}/{total} compressed | "
          f"{before/1024/1024:.0f} MB -> {after/1024/1024:.0f} MB "
          f"({(1-after/before)*100 if before else 0:.0f}% smaller)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

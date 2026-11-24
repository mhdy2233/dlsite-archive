#!/usr/bin/env python3
"""Build one SQLite file holding every archived work, for publishing via Releases.

  python3 scripts/build_db.py            -> dist/dlsite-archive.db

The DB is the "offline" copy: one row per work with the hot columns plus the
full JSON, and an FTS5 index (trigram tokenizer, so Japanese substring search
works) over title / circle / tags / description.
"""
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "docs", "api")
OUT = os.path.join(ROOT, "dist")

SCHEMA = """
CREATE TABLE works (
  id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  title TEXT,
  title_romaji TEXT,
  circle_id TEXT,
  circle_name TEXT,
  type_code TEXT,
  type_name TEXT,
  age TEXT,
  price_jpy INTEGER,
  price_official_jpy INTEGER,
  release INTEGER,
  file_type TEXT,
  file_size INTEGER,
  rating REAL,
  rating_count INTEGER,
  dl_count INTEGER,
  wishlist_count INTEGER,
  review_count INTEGER,
  languages TEXT,
  tags TEXT,
  description TEXT,
  url TEXT,
  fetched_at INTEGER,
  raw TEXT
);
CREATE INDEX idx_works_cat  ON works(category);
CREATE INDEX idx_works_rel  ON works(release DESC);
CREATE INDEX idx_works_dl   ON works(dl_count DESC);
CREATE INDEX idx_works_circ ON works(circle_id);
CREATE VIRTUAL TABLE works_fts USING fts5(
  id UNINDEXED, title, title_romaji, circle_name, tags, description
);
"""


def log(m):
    print(f"[db] {m}", flush=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    db_path = os.path.join(OUT, "dlsite-archive.db")
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            os.remove(p)

    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")

    total = 0
    for cat in sorted(os.listdir(API)):
        wd = os.path.join(API, cat, "works")
        if not os.path.isdir(wd):
            continue
        n = 0
        for fn in sorted(os.listdir(wd)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(wd, fn), encoding="utf-8") as f:
                    w = json.load(f)
            except Exception:
                continue
            tags = " ".join(t.get("name", "") for t in (w.get("tags") or []))
            desc = (w.get("description") or "")[:2000]
            con.execute(
                "INSERT OR REPLACE INTO works VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (w["id"], cat, w.get("title"), w.get("title_romaji"),
                 (w.get("circle") or {}).get("id"), (w.get("circle") or {}).get("name"),
                 (w.get("type") or {}).get("code"), (w.get("type") or {}).get("name"),
                 w.get("age"),
                 (w.get("price") or {}).get("jpy"), (w.get("price") or {}).get("official_jpy"),
                 w.get("release"),
                 (w.get("file") or {}).get("type"), (w.get("file") or {}).get("size"),
                 (w.get("rating") or {}).get("average"), (w.get("rating") or {}).get("count"),
                 w.get("dl_count"), w.get("wishlist_count"), w.get("review_count"),
                 json.dumps(w.get("languages") or [], ensure_ascii=False),
                 tags, desc, w.get("url"), w.get("fetched_at"),
                 json.dumps(w, ensure_ascii=False, separators=(",", ":")))
            )
            con.execute(
                "INSERT INTO works_fts (id, title, title_romaji, circle_name, tags, description) "
                "VALUES (?,?,?,?,?,?)",
                (w["id"], w.get("title") or "", w.get("title_romaji") or "",
                 (w.get("circle") or {}).get("name") or "", tags, desc))
            n += 1
        con.commit()
        log(f"[{cat}] {n} works")
        total += n

    con.execute("INSERT OR REPLACE INTO works_fts (works_fts) VALUES ('optimize')")
    con.commit()
    con.execute("VACUUM")
    con.close()

    size = os.path.getsize(db_path) / 1024 / 1024
    log(f"total {total} works -> {db_path} ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

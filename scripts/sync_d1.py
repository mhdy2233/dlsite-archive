#!/usr/bin/env python3
"""Push newly archived works from the repo into Cloudflare D1.

Run by the archive workflow right after the scraper finishes. Only works whose
detail JSON changed since the last successful sync are touched, so a normal run
writes a few dozen rows instead of re-importing everything.

Talks to the D1 HTTP API directly (no wrangler.toml needed in this repo).

  python3 scripts/sync_d1.py             # sync anything newer than the marker
  python3 scripts/sync_d1.py --all       # force a full re-sync
  python3 scripts/sync_d1.py --dry-run   # show the SQL without sending it
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(ROOT, 'docs', 'api')
MARKER = os.path.join(ROOT, 'data', 'd1_synced.json')

ACC = os.environ.get('CLOUDFLARE_ACCOUNT_ID', 'a33c9d386f3cd7221306077a08ffaba9')
DB = os.environ.get('D1_DATABASE_ID', '029f81ce-164a-491a-8208-cc45915463a4')
TOKEN = os.environ.get('CLOUDFLARE_API_TOKEN', '')
ENDPOINT = f'https://api.cloudflare.com/client/v4/accounts/{ACC}/d1/database/{DB}/query'

COLS = ['id', 'cat', 'title', 'title_romaji', 'circle', 'circle_id', 'type',
        'type_code', 'age', 'price', 'price_orig', 'rating', 'review_count',
        'dl_count', 'wishlist', 'release', 'file_size', 'languages', 'tags',
        'body', 'thumb']

SRC = {'i': 'id', 't': 'title', 'rm': 'title_romaji', 'c': 'circle',
       'ci': 'circle_id', 'ty': 'type', 'tc': 'type_code', 'age': 'age',
       'p': 'price', 'po': 'price_orig', 'r': 'rating', 'rc': 'review_count',
       'dl': 'dl_count', 'rel': 'release', 'fs': 'file_size',
       'lg': 'languages', 'g': 'tags', 'd': 'body', 'th': 'thumb'}


def q(v):
    if v is None:
        return 'NULL'
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def row_values(work, cat):
    """Map a detail JSON (nested) onto the flat `works` columns."""
    circle = work.get('circle') or {}
    typ = work.get('type') or {}
    price = work.get('price') or {}
    rating = work.get('rating') or {}
    fileinfo = work.get('file') or {}
    imgs = work.get('images') or {}

    def pick(obj, key):
        return obj.get(key) if isinstance(obj, dict) else None

    return [
        work.get('id'),                                        # id
        cat,                                                   # cat
        work.get('title'),                                     # title
        work.get('title_romaji') or None,                      # title_romaji
        circle.get('name') if isinstance(circle, dict) else circle,   # circle
        circle.get('id') if isinstance(circle, dict) else None,       # circle_id
        typ.get('name') if isinstance(typ, dict) else typ,     # type
        typ.get('code') if isinstance(typ, dict) else None,    # type_code
        work.get('age'),                                       # age
        pick(price, 'jpy'),                                    # price
        pick(price, 'official_jpy'),                           # price_orig
        pick(rating, 'average'),                               # rating
        work.get('review_count') or pick(rating, 'count'),      # review_count
        work.get('dl_count'),                                  # dl_count
        work.get('wishlist_count'),                            # wishlist
        work.get('release'),                                   # release
        pick(fileinfo, 'size'),                                # file_size
        json.dumps(work.get('languages') or [], ensure_ascii=False),
        json.dumps(work.get('tags') or [], ensure_ascii=False),
        work.get('short_introduction') or '',                   # body
        imgs.get('thumb') or '',                                # thumb
    ]


def d1(sql, retries=3):
    body = json.dumps({'sql': sql}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(ENDPOINT, data=body, method='POST', headers={
            'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
            if d.get('success'):
                return d
            errs = d.get('errors') or []
            print(f'  d1 error: {errs}', file=sys.stderr)
            if any('rate' in str(e).lower() or 'limit' in str(e).lower() for e in errs):
                time.sleep(2 ** attempt * 3)
                continue
            return d
        except urllib.error.HTTPError as e:
            print(f'  HTTP {e.code}: {e.read()[:200]}', file=sys.stderr)
            time.sleep(2 ** attempt * 2)
    return None


def collect(since, force):
    found = []
    for cat in ('game', 'audio'):
        wd = os.path.join(API_DIR, cat, 'works')
        if not os.path.isdir(wd):
            continue
        for p in glob.glob(os.path.join(wd, '*.json')):
            if not force and os.path.getmtime(p) <= since:
                continue
            found.append((cat, p))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()

    since = 0
    if not a.all and os.path.exists(MARKER):
        try:
            since = json.load(open(MARKER)).get('ts', 0)
        except Exception:
            since = 0

    files = collect(since, a.all)
    if a.limit:
        files = files[:a.limit]
    when = time.strftime('%F %T', time.localtime(since)) if since else 'never'
    print(f'sync: {len(files)} changed works (marker: {when})')
    if not files:
        return 0

    ids = []
    statements = []
    for cat, path in files:
        try:
            w = json.load(open(path, encoding='utf-8'))
        except Exception as e:
            print(f'  skip {os.path.basename(path)}: {e}', file=sys.stderr)
            continue
        wid = w.get('id')
        if not wid:
            continue
        statements.append(
            f'INSERT OR REPLACE INTO works ({",".join(COLS)}) VALUES ('
            + ','.join(q(v) for v in row_values(w, cat)) + ');')
        ids.append(wid)

    if not ids:
        print('sync: nothing usable')
        return 0

    id_list = ','.join(q(i) for i in ids)
    statements.append(
        'INSERT INTO works_fts (rowid, title, title_romaji, circle, tags, body) '
        f'SELECT rowid, title, title_romaji, circle, tags, body FROM works WHERE id IN ({id_list});')
    statements += [
        "DELETE FROM stats;",
        "INSERT INTO stats (cat,kind,name,n) SELECT cat,'total','',count(*) FROM works GROUP BY cat;",
        "INSERT INTO stats (cat,kind,name,n) SELECT cat,'type',type,count(*) FROM works GROUP BY cat,type;",
        "INSERT INTO stats (cat,kind,name,n) SELECT cat,'age',age,count(*) FROM works GROUP BY cat,age;",
    ]

    sql_path = os.path.join(ROOT, 'data', 'd1_sync.sql')
    with open(sql_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(statements) + '\n')
    print(f'sync: {len(ids)} works, {len(statements)} statements -> data/d1_sync.sql')

    if a.dry_run:
        return 0
    if not TOKEN:
        print('sync: CLOUDFLARE_API_TOKEN missing', file=sys.stderr)
        return 1

    failed = 0
    for i, sql in enumerate(statements, 1):
        if not d1(sql):
            failed += 1
        if i % 25 == 0:
            print(f'  {i}/{len(statements)}', flush=True)
    if failed:
        print(f'sync: {failed} statements failed', file=sys.stderr)
        return 1

    json.dump({'ts': time.time(), 'count': len(ids)}, open(MARKER, 'w'))
    print(f'sync: done, marker updated ({len(ids)} works)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

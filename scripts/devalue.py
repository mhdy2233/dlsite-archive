"""Parser for Nuxt 3 `__NUXT_DATA__` payloads (devalue flat-array format).

DLsite work pages embed the whole product object in this payload, which is far
more reliable than scraping the DOM. Format notes:

  * the payload is a flat array; slot 0 is the root
  * members of containers are integer *indexes* into that array
  * whatever lives at an index is a literal (str/num/bool/null) or a container
  * a literal number must NOT be re-treated as an index -- doing so is the
    classic off-by-one that silently corrupts nested objects
"""
import json
import re

SPECIAL_REF = {
    "ShallowReactive", "Reactive", "Raw", "Ref", "ShallowRef",
    "ShallowReactiveValue", "EmptyRef", "EmptyShallowRef",
}
NEG = {-1: None, -2: None, -3: float("nan"), -4: float("inf"),
       -5: float("-inf"), -6: 0.0}

NUXT_RE = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)


def parse_devalue(root):
    cache = {}
    in_progress = set()

    def at(i):
        if isinstance(i, bool) or not isinstance(i, int):
            return i
        if i < 0:
            return NEG.get(i)
        if i in cache:
            return cache[i]
        if i in in_progress or i >= len(root):
            return None
        in_progress.add(i)
        out = value(root[i])
        in_progress.discard(i)
        cache[i] = out
        return out

    def value(v):
        if isinstance(v, list):
            if len(v) >= 2 and isinstance(v[0], str):
                tag = v[0]
                if tag in SPECIAL_REF:
                    return at(v[1])
                if tag == "Date":
                    return at(v[1])
                if tag == "Set":
                    return [at(x) for x in v[1:]]
                if tag == "Map":
                    return {str(at(v[i])): at(v[i + 1]) for i in range(1, len(v) - 1, 2)}
                if tag == "RegExp":
                    return f"/{v[1]}/{v[2] if len(v) > 2 else ''}"
                if tag == "BigInt":
                    return str(v[1])
                if tag in ("Object", "null"):
                    return at(v[1]) if len(v) > 1 else {}
            return [at(x) for x in v]
        if isinstance(v, dict):
            return {k: at(x) for k, x in v.items()}
        return v

    return at(0)


def extract_payload(html):
    m = NUXT_RE.search(html)
    if not m:
        return None
    try:
        return parse_devalue(json.loads(m.group(1)))
    except Exception:
        return None


def find_product(obj):
    """Return the first dict in the payload that looks like a DLsite product."""
    seen = set()

    def rec(o, depth=0):
        if depth > 14 or id(o) in seen:
            return None
        if isinstance(o, dict):
            if "product_id" in o and "work_name" in o:
                return o
            seen.add(id(o))
            for v in o.values():
                r = rec(v, depth + 1)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = rec(v, depth + 1)
                if r:
                    return r
        return None

    return rec(obj)

#!/usr/bin/env python3
"""Player library: saved army lists and model collections.

Lives outside any repository so personal data is never committed:
$WH40K_HOME or ~/.wh40k
  lists/<date>-<faction>-<points>-<title>.json    validated lists (check_list.py format)
  collections/<faction>.json                       {"faction", "units": {name: models}, "notes": [...]}

Usage:
  library.py save-list LIST.json            # validate with check_list and store it (refuses lists with errors)
  library.py lists [--faction X] [--limit N]  # saved lists, newest first
  library.py show <file or text>            # print a saved list (formatted) and its path
  library.py collection-set <faction> "Unit=models,..." [--note TEXT ...] [--replace]
  library.py collection <faction>           # print the saved collection and the --owned string
  library.py collections                    # factions with a saved collection

optimize.py accepts `--owned saved` to use the faction's saved collection.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bsdata  # noqa: E402

HOME = Path(os.environ.get("WH40K_HOME") or Path.home() / ".wh40k")
LISTS = HOME / "lists"
COLLECTIONS = HOME / "collections"


def slug(s):
    s = re.sub(r"[^\w]+", "-", (s or "").lower(), flags=re.UNICODE).strip("-")
    return s[:60] or "list"


def faction_name(query):
    return bsdata.load(query)["faction"]


def faction_slug(query):
    return slug(faction_name(query))


# ----------------------------------------------------------------------------
# Lists
# ----------------------------------------------------------------------------

def save_list(path):
    import check_list
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    res = check_list.check(spec)
    d, total, pts_limit, errors = res[0], res[8], res[2], res[9]
    if errors:
        raise SystemExit("Not saved: the list has errors:\n  " + "\n  ".join(errors))
    spec = {k: v for k, v in spec.items() if not k.startswith("_")}
    spec["_saved"] = time.strftime("%Y-%m-%d %H:%M")
    spec["_faction"] = d["faction"]
    spec["_total"] = total
    spec["_limit"] = pts_limit
    spec["_data_revision"] = d.get("data_revision")
    LISTS.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug(d['faction'].split(' - ')[-1])}-{pts_limit}-{slug(spec.get('title'))}.json"
    out = LISTS / name
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def saved_lists(faction=None):
    if not LISTS.exists():
        return []
    fname = faction_name(faction) if faction else None
    rows = []
    for p in sorted(LISTS.glob("*.json"), reverse=True):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if fname and s.get("_faction") != fname:
            continue
        rows.append((p, s))
    return rows


def find_list(query):
    p = Path(query)
    if p.exists():
        return p
    for cand in (LISTS / query, LISTS / f"{query}.json"):
        if cand.exists():
            return cand
    q = query.lower()
    hits = [p for p, s in saved_lists() if q in p.name.lower() or q in (s.get("title") or "").lower()]
    if not hits:
        raise SystemExit(f"No saved list matches '{query}'")
    return hits[0]  # newest


# ----------------------------------------------------------------------------
# Collections
# ----------------------------------------------------------------------------

def collection_path(faction):
    return COLLECTIONS / f"{faction_slug(faction)}.json"


def load_collection(faction):
    p = collection_path(faction)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def owned_map(faction):
    """{unit name lower: models} for optimize.py, or None if nothing is saved."""
    c = load_collection(faction)
    return {k.lower(): int(v) for k, v in c["units"].items()} if c else None


def set_collection(faction, spec, notes=(), replace=False):
    d = bsdata.load(faction)
    units = {u["name"].lower(): u["name"] for u in d["units"]}
    cur = (load_collection(faction) or {}) if not replace else {}
    have = dict(cur.get("units", {}))
    problems = []
    for part in spec.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            problems.append(f"'{part.strip()}': use Unit=models")
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        name = units.get(k.lower())
        if not name:
            sub = [n for lk, n in units.items() if k.lower() in lk]
            if len(sub) == 1:
                name = sub[0]
            else:
                problems.append(f"'{k}': " + (f"ambiguous ({', '.join(sub[:5])})" if sub else "no such datasheet"))
                continue
        n = int(v)
        if n <= 0:
            have.pop(name, None)
        else:
            have[name] = n
    if problems:
        raise SystemExit("Collection not saved:\n  " + "\n  ".join(problems))
    out = {"faction": d["faction"], "updated": time.strftime("%Y-%m-%d %H:%M"),
           "units": dict(sorted(have.items())),
           "notes": list(dict.fromkeys(list(cur.get("notes", [])) + list(notes)))}
    COLLECTIONS.mkdir(parents=True, exist_ok=True)
    p = collection_path(faction)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return p, out


def owned_string(c):
    return ",".join(f"{k}={v}" for k, v in c["units"].items())


# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("save-list"); s.add_argument("file")
    s = sp.add_parser("lists"); s.add_argument("--faction"); s.add_argument("--limit", type=int, default=20)
    s = sp.add_parser("show"); s.add_argument("query")
    s = sp.add_parser("collection-set"); s.add_argument("faction"); s.add_argument("units")
    s.add_argument("--note", action="append", default=[]); s.add_argument("--replace", action="store_true")
    s = sp.add_parser("collection"); s.add_argument("faction")
    sp.add_parser("collections")
    a = ap.parse_args()

    if a.cmd == "save-list":
        print(f"Saved: {save_list(a.file)}")
    elif a.cmd == "lists":
        rows = saved_lists(a.faction)[: a.limit]
        if not rows:
            print(f"No saved lists in {LISTS}")
        for p, s in rows:
            print(f"{s.get('_saved', '?')} | {s.get('_faction', s.get('faction'))} | {s.get('battle_size')} "
                  f"{s.get('_total')}/{s.get('_limit')} | {', '.join(s.get('detachments', []))} | "
                  f"{s.get('title') or '-'} | {p}")
    elif a.cmd == "show":
        import check_list
        p = find_list(a.query)
        spec = json.loads(p.read_text(encoding="utf-8"))
        print(f"File: {p}  (saved {spec.get('_saved', '?')})\n")
        print(check_list.render(spec, check_list.check(spec)))
    elif a.cmd == "collection-set":
        p, c = set_collection(a.faction, a.units, a.note, a.replace)
        print(f"Saved: {p}")
        print(json.dumps(c, ensure_ascii=False, indent=1))
    elif a.cmd == "collection":
        c = load_collection(a.faction)
        if not c:
            print(f"No saved collection for {faction_name(a.faction)} ({collection_path(a.faction)})")
            return
        print(f"File: {collection_path(a.faction)}  (updated {c.get('updated')})")
        for k, v in c["units"].items():
            print(f"  {k}: {v}")
        for n in c.get("notes", []):
            print(f"  note: {n}")
        print(f"\n--owned \"{owned_string(c)}\"   (or: --owned saved)")
    elif a.cmd == "collections":
        if not COLLECTIONS.exists() or not any(COLLECTIONS.glob("*.json")):
            print(f"No saved collections in {COLLECTIONS}")
        for p in sorted(COLLECTIONS.glob("*.json")):
            c = json.loads(p.read_text(encoding="utf-8"))
            print(f"{c.get('faction')} | {len(c['units'])} datasheets | updated {c.get('updated')} | {p}")


if __name__ == "__main__":
    main()

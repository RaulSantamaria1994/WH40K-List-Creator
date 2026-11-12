#!/usr/bin/env python3
"""Player library: model collections only (army lists are never saved).

Lives outside any repository so personal data is never committed:
$WH40K_HOME or ~/.wh40k
  collections/<faction>.json                       {"faction", "units": {name: models}, "notes": [...]}

Usage:
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
COLLECTIONS = HOME / "collections"


def slug(s):
    s = re.sub(r"[^\w]+", "-", (s or "").lower(), flags=re.UNICODE).strip("-")
    return s[:60] or "list"


def faction_name(query):
    return bsdata.load(query)["faction"]


def faction_slug(query):
    return slug(faction_name(query))


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
        try:
            n = int(v)
        except ValueError:
            problems.append(f"'{part.strip()}': models must be a whole number")
            continue
        if n <= 0:
            have.pop(name, None)
        else:
            have[name] = n
    if problems:
        raise SystemExit("Collection not saved:\n  " + "\n  ".join(problems))
    out = {"faction": d["faction"], "updated": time.strftime("%Y-%m-%d %H:%M"),
           "units": dict(sorted(have.items())),
           "notes": [n for n in dict.fromkeys(clean_note(n) for n in list(cur.get("notes", [])) + list(notes)) if n][-MAX_NOTES:]}
    COLLECTIONS.mkdir(parents=True, exist_ok=True)
    p = collection_path(faction)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return p, out


MAX_NOTES, MAX_NOTE_LEN = 20, 200


def clean_note(text):
    """Notes are re-read by the agent in later sessions: keep them short, plain and inert."""
    t = re.sub(r"\s+", " ", bsdata.sanitize_text(str(text))[0]).strip()
    return t[:MAX_NOTE_LEN]


def owned_string(c):
    return ",".join(f"{k}={v}" for k, v in c["units"].items())


# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("collection-set"); s.add_argument("faction"); s.add_argument("units")
    s.add_argument("--note", action="append", default=[]); s.add_argument("--replace", action="store_true")
    s = sp.add_parser("collection"); s.add_argument("faction")
    sp.add_parser("collections")
    a = ap.parse_args()

    if a.cmd == "collection-set":
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
            print(f"  note: {clean_note(n)}")  # the file lives in the user's home and could have been edited
        print(f"\n--owned \"{owned_string(c)}\"   (or: --owned saved)")
    elif a.cmd == "collections":
        if not COLLECTIONS.exists() or not any(COLLECTIONS.glob("*.json")):
            print(f"No saved collections in {COLLECTIONS}")
        for p in sorted(COLLECTIONS.glob("*.json")):
            c = json.loads(p.read_text(encoding="utf-8"))
            print(f"{c.get('faction')} | {len(c['units'])} datasheets | updated {c.get('updated')} | {p}")


if __name__ == "__main__":
    main()

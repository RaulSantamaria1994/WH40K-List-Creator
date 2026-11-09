#!/usr/bin/env python3
"""Download, compile and query Warhammer 40,000 11th edition data from BSData.

Data source: https://github.com/BSData/wh40k-11e (BattleScribe JSON catalogues).

The raw catalogues are large (~50 MB) and deeply nested. This script turns one
faction into a compact JSON file (units, points by unit size, profiles,
weapons, abilities, leader targets, copy limits, detachments, enhancements)
that is cheap to read and easy for the other scripts to consume.

Usage:
  bsdata.py sync                         # clone or update the data cache
  bsdata.py factions                     # list playable factions
  bsdata.py build <faction>              # compile faction -> JSON (prints path)
  bsdata.py units <faction> [--legends] [--allies] [--grep TEXT]
  bsdata.py unit <faction> <unit name>   # full detail of one or more units (comma separated)
  bsdata.py detachments <faction> [--full]
  bsdata.py limits                       # army construction limits by battle size

Cache dir: $WH40K_DATA_DIR or ~/.cache/wh40k-11e
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REPO_URL = "https://github.com/BSData/wh40k-11e.git"
ZIP_URL = "https://codeload.github.com/BSData/wh40k-11e/zip/refs/heads/main"
COMPILER_VERSION = 11

PTS = "51b2-306e-1021-d207"
DETACHMENT_POINTS = "82ae-1066-5107-6ae0"
ENHANCEMENT_SLOTS = "f759-1bc4-cb3a-f0d2"

# Catalogues that, when imported into another faction, provide allied units
# rather than core units of that faction.
ALLY_SOURCES = {
    "Imperium - Agents of the Imperium": "agents of the imperium",
    "Imperium - Imperial Knights - Library": "imperial knights",
    "Library - Titans": "titan",
    "Unaligned Forces": "unaligned",
    "Chaos - Chaos Knights Library": "chaos knights",
    "Chaos - Daemons Library": "daemons",
    "Chaos - Chaos Daemons Library": "daemons",
}

SKIP_GROUPS = {"crusade", "weapon modifications", "enhancements", "battle scars",
               "battle honours", "crusade relics", "weapon enhancements", "weapon upgrades",
               "crusade relic upgrades", "weapon abilities", "battle traits", "crusade upgrades"}

# ----------------------------------------------------------------------------
# Cache / sync
# ----------------------------------------------------------------------------

def cache_dir() -> Path:
    d = Path(os.environ.get("WH40K_DATA_DIR", Path.home() / ".cache" / "wh40k-11e"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def repo_dir() -> Path:
    return cache_dir() / "repo"


def compiled_dir() -> Path:
    d = cache_dir() / "compiled"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log(msg):
    print(msg, file=sys.stderr)


def sync(force=False, max_age_hours=12):
    """Clone or update the BSData repo. Falls back to a zip download without git."""
    rd = repo_dir()
    stamp = cache_dir() / "last_sync"
    if rd.exists() and not force and stamp.exists():
        age = time.time() - stamp.stat().st_mtime
        if age < max_age_hours * 3600:
            return rd
    git = shutil.which("git")
    ok = False
    if git:
        try:
            if (rd / ".git").exists():
                subprocess.run([git, "-C", str(rd), "fetch", "--depth", "1", "origin", "main"],
                               check=True, capture_output=True, timeout=300)
                subprocess.run([git, "-C", str(rd), "reset", "--hard", "origin/main"],
                               check=True, capture_output=True, timeout=120)
            else:
                if rd.exists():
                    shutil.rmtree(rd)
                subprocess.run([git, "clone", "--depth", "1", REPO_URL, str(rd)],
                               check=True, capture_output=True, timeout=600)
            ok = True
        except Exception as e:  # network or git problem -> try zip
            _log(f"git sync failed ({e}); trying zip download")
    if not ok:
        try:
            with urllib.request.urlopen(ZIP_URL, timeout=300) as r:
                data = r.read()
            tmp = cache_dir() / "repo_tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
            inner = next(tmp.iterdir())
            if rd.exists():
                shutil.rmtree(rd)
            inner.rename(rd)
            shutil.rmtree(tmp, ignore_errors=True)
            ok = True
        except Exception as e:
            if rd.exists():
                _log(f"WARNING: could not update data ({e}); using cached copy")
                return rd
            raise SystemExit(f"ERROR: could not download BSData ({e}). Check network access to github.com.")
    stamp.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    # compiled files depend on the data revision: invalidate them
    for f in compiled_dir().glob("*.json"):
        f.unlink()
    return rd


def data_revision():
    rd = repo_dir()
    if (rd / ".git").exists() and shutil.which("git"):
        try:
            out = subprocess.run(["git", "-C", str(rd), "log", "-1", "--format=%h %cs"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
            return out
        except Exception:
            pass
    stamp = cache_dir() / "last_sync"
    return "zip " + (stamp.read_text() if stamp.exists() else "?")


# ----------------------------------------------------------------------------
# Loading catalogues
# ----------------------------------------------------------------------------

class Catalogue:
    def __init__(self, path: Path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.kind = next(iter(raw))
        self.c = raw[self.kind]
        self.name = self.c["name"]
        self.id = self.c["id"]
        self.path = path
        self.library = bool(self.c.get("library"))
        self.entries = {}
        self.groups = {}
        self.profiles = {}
        self.rules = {}
        self.info_groups = {}
        self.categories = {}
        for e in self.c.get("sharedSelectionEntries", []):
            self.entries[e["id"]] = e
        for e in self.c.get("sharedSelectionEntryGroups", []):
            self.groups[e["id"]] = e
        for p in self.c.get("sharedProfiles", []):
            self.profiles[p["id"]] = p
        for r in self.c.get("sharedRules", []):
            self.rules[r["id"]] = r
        for g in self.c.get("sharedInfoGroups", []):
            self.info_groups[g["id"]] = g
        for ce in self.c.get("categoryEntries", []):
            self.categories[ce["id"]] = ce["name"]


class World:
    """All catalogues, with id resolution scoped to a faction's link graph."""

    def __init__(self, rd: Path):
        self.cats = {}
        self.by_name = {}
        self.gs = None
        for p in sorted(rd.glob("*.json")):
            try:
                cat = Catalogue(p)
            except Exception:
                continue
            if cat.kind == "gameSystem":
                self.gs = cat
            else:
                self.cats[cat.id] = cat
                self.by_name[cat.name] = cat
        if self.gs is None:
            raise SystemExit("ERROR: game system file not found in data repo")

    def playable(self):
        return sorted(c.name for c in self.cats.values() if not c.library)

    ALIASES = {"craftworlds": "aeldari", "eldar": "aeldari", "dark eldar": "drukhari",
               "guard": "astra militarum", "imperial guard": "astra militarum", "sisters": "sororitas",
               "admech": "mechanicus", "tau": "t'au", "gsc": "genestealer", "votann": "votann",
               "squats": "votann", "csm": "chaos space marines", "nids": "tyranids"}

    def find_faction(self, query):
        # "Space Marines (Ultramarines)" -> try the most specific part first
        inner = re.findall(r"\(([^)]+)\)", query)
        outer = re.sub(r"\([^)]*\)", "", query).strip()
        for q in inner + [outer]:
            try:
                return self._find_faction(q)
            except SystemExit:
                continue
        return self._find_faction(query)

    def _find_faction(self, query):
        q = query.lower().strip()
        q = self.ALIASES.get(q, q)
        names = self.playable()
        def seg(n):
            return n.split(" - ")[-1].lower()
        for n in names:
            if n.lower() == q or seg(n) == q:
                return self.by_name[n]
        cands = [n for n in names if q in n.lower()]
        if len(cands) == 1:
            return self.by_name[cands[0]]
        if cands:
            # prefer the shortest final segment containing the query
            cands.sort(key=lambda n: (not seg(n).startswith(q), len(n)))
            return self.by_name[cands[0]]
        # token match
        toks = set(re.findall(r"\w+", q))
        scored = sorted(names, key=lambda n: (-len(toks & set(re.findall(r"\w+", n.lower()))), len(n)))
        if scored and toks & set(re.findall(r"\w+", scored[0].lower())):
            return self.by_name[scored[0]]
        raise SystemExit(f"Faction '{query}' not found. Available: " + "; ".join(names))

    def link_order(self, cat):
        """BFS over catalogueLinks: returns list of (catalogue, imports_root_entries)."""
        order = [(cat, True)]
        seen = {cat.id}
        queue = [(cat, True)]
        while queue:
            cur, root = queue.pop(0)
            for l in cur.c.get("catalogueLinks", []):
                tgt = self.cats.get(l.get("targetId"))
                if not tgt or tgt.id in seen:
                    continue
                seen.add(tgt.id)
                imp = bool(l.get("importRootEntries")) and root
                order.append((tgt, imp))
                queue.append((tgt, imp))
        return order


class Resolver:
    def __init__(self, world: World, faction: Catalogue):
        self.world = world
        self.order = world.link_order(faction)
        self.scope = [c for c, _ in self.order] + [world.gs]
        self.category_names = {}
        for c in reversed(self.scope):
            self.category_names.update(c.categories)

    def _find(self, attr, tid):
        for c in self.scope:
            d = getattr(c, attr)
            if tid in d:
                return d[tid], c
        # last resort: any catalogue
        for c in self.world.cats.values():
            d = getattr(c, attr)
            if tid in d:
                return d[tid], c
        return None, None

    def entry(self, tid):
        e, c = self._find("entries", tid)
        if e is None:
            e, c = self._find("groups", tid)
        return e, c

    def profile(self, tid):
        return self._find("profiles", tid)[0]

    def rule(self, tid):
        return self._find("rules", tid)[0]

    def info_group(self, tid):
        return self._find("info_groups", tid)[0]


# ----------------------------------------------------------------------------
# Helpers for the entry tree
# ----------------------------------------------------------------------------

def cost(e, type_id=PTS):
    for c in e.get("costs", []) or []:
        if c.get("typeId") == type_id:
            return c.get("value", 0) or 0
    return 0


def sel_constraint(e, ctype, scopes=("parent",)):
    for cn in e.get("constraints", []) or []:
        if cn.get("field") == "selections" and cn.get("type") == ctype and cn.get("scope") in scopes:
            return cn.get("value")
    return None


def merged(link, target):
    """Overlay an entryLink on its target (link constraints/modifiers/costs take part)."""
    if link is None:
        return target
    m = dict(target)
    for k in ("constraints", "modifiers", "modifierGroups", "categoryLinks", "costs"):
        if link.get(k):
            m[k] = list(target.get(k, []) or []) + list(link[k])
    if link.get("hidden"):
        m["hidden"] = True
    return m


def children(e, res):
    """Yield (child_entry, kind) resolving entryLinks."""
    for x in e.get("selectionEntries", []) or []:
        yield x
    for x in e.get("selectionEntryGroups", []) or []:
        yield x
    for l in e.get("entryLinks", []) or []:
        tgt, _ = res.entry(l.get("targetId"))
        if tgt is not None:
            yield merged(l, tgt)


def chars(p):
    return {c["name"]: (c.get("$text") or "").strip() for c in p.get("characteristics", []) or []}


def clean(s, n=None):
    s = re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()
    if n and len(s) > n:
        s = s[: n - 1] + "…"
    return s


def profiles_of(e, res):
    out = list(e.get("profiles", []) or [])
    for il in e.get("infoLinks", []) or []:
        if il.get("type") == "profile":
            p = res.profile(il.get("targetId"))
            if p:
                out.append(p)
    return out


def rule_names(e, res):
    names = []
    for r in e.get("rules", []) or []:
        names.append(r["name"])
    for il in e.get("infoLinks", []) or []:
        if il.get("type") == "rule":
            nm = il.get("name")
            for m in il.get("modifiers", []) or []:
                if m.get("field") == "name" and m.get("type") in ("set",):
                    nm = m.get("value")
                if m.get("field") == "name" and m.get("type") == "append":
                    nm = f"{nm} {m.get('value')}"
            names.append(nm)
    return names


# ----------------------------------------------------------------------------
# Points evaluation by model count
# ----------------------------------------------------------------------------

CMP = {
    "atLeast": lambda a, b: a >= b,
    "greaterThan": lambda a, b: a > b,
    "atMost": lambda a, b: a <= b,
    "lessThan": lambda a, b: a < b,
    "equalTo": lambda a, b: a == b,
    "notEqualTo": lambda a, b: a != b,
}


def eval_cond(c, n, model_ids):
    """Return True/False if the condition only depends on model count, else None."""
    if c.get("field") != "selections":
        return None
    child = c.get("childId")
    f = CMP.get(c.get("type"))
    if child not in ("model",) and child not in model_ids:
        # Condition on some other optional entry (wargear, attached model...).
        # Price the default unit: assume that entry is not selected (count 0).
        if f and child and child != "any" and c.get("scope") not in ("roster", "force", "primary-catalogue"):
            return f(0, c.get("value", 0))
        return None
    if not f:
        return None
    return f(n, c.get("value", 0))


def eval_group(g, n, model_ids):
    if g.get("localConditionGroups"):
        return None
    results = [eval_cond(c, n, model_ids) for c in g.get("conditions", []) or []]
    results += [eval_group(x, n, model_ids) for x in g.get("conditionGroups", []) or []]
    if any(r is None for r in results) or not results:
        return None
    return all(results) if g.get("type", "and") == "and" else any(results)


def copy_surcharges(mods):
    """11e: extra copies of the same unit cost more. Pattern = localConditionGroups."""
    out = []
    for m in mods:
        if m.get("field") != PTS:
            continue
        for g in m.get("conditionGroups", []) or []:
            for lg in g.get("localConditionGroups", []) or []:
                if any(c.get("type") == "before" for c in lg.get("conditions", []) or []):
                    out.append({"from_copy": int(lg.get("value", 1)) + 1,
                                "extra_pts": m.get("value"), "op": m.get("type")})
    return out


def all_mods(e):
    mods = list(e.get("modifiers", []) or [])
    for g in e.get("modifierGroups", []) or []:
        mods += g.get("modifiers", []) or []
    return mods


def points_by_size(unit, res, model_entries, min_m, max_m):
    base = cost(unit)
    mods = all_mods(unit)
    model_ids = {m["id"] for m in model_entries}
    # conditions may count models through the group that contains them
    model_ids |= {i for m in model_entries for i in m.get("_ancestors", [])}
    per_model = [m["pts"] for m in model_entries if m.get("pts")]
    per_model_cost = min(per_model) if per_model else 0
    tiers = {}
    notes = []
    for n in range(max(1, min_m), max(min_m, max_m) + 1):
        v = base + per_model_cost * n
        for m in mods:
            if m.get("field") != PTS:
                continue
            conds = m.get("conditions", []) or []
            groups = m.get("conditionGroups", []) or []
            if not conds and not groups:
                applies = True
            else:
                rs = [eval_cond(c, n, model_ids) for c in conds] + [eval_group(g, n, model_ids) for g in groups]
                if any(r is None for r in rs):
                    continue  # depends on something else (copies, wargear, detachment)
                applies = all(rs)
            if not applies or m.get("repeats"):
                continue
            t, val = m.get("type"), m.get("value", 0)
            if t == "set":
                v = val
            elif t == "increment":
                v += val
            elif t == "decrement":
                v -= val
            elif t == "multiply":
                v *= val
        tiers[n] = v
    # other point modifiers we could not evaluate (wargear/detachment dependent)
    for m in mods:
        if m.get("field") != PTS:
            continue
        if any(lg for g in m.get("conditionGroups", []) or [] for lg in g.get("localConditionGroups", []) or []):
            continue
        names = [c.get("childName") for c in m.get("conditions", []) or [] if c.get("childName") and c.get("childId") not in model_ids and c.get("childId") != "model"]
        if names:
            notes.append(f"{m.get('type')} {m.get('value')} pts if: {', '.join(n for n in names if n)}")
    return tiers, notes


def compress_tiers(tiers):
    """{n: pts} -> list of {models: '10', pts: 85} keeping the sizes worth buying.

    Within a band of equal cost only the largest size is interesting (more models
    for the same points), but we also keep the minimum size."""
    out = []
    items = sorted(tiers.items())
    i = 0
    while i < len(items):
        j = i
        while j + 1 < len(items) and items[j + 1][1] == items[i][1]:
            j += 1
        lo, hi = items[i][0], items[j][0]
        out.append({"models": f"{lo}" if lo == hi else f"{lo}-{hi}", "best_size": hi, "pts": items[i][1]})
        i = j + 1
    return out


# ----------------------------------------------------------------------------
# Unit compilation
# ----------------------------------------------------------------------------

def collect(unit, res):
    """Walk a unit tree collecting models, weapons, abilities, wargear options."""
    models = []
    weapons = {}
    abilities = {}
    unit_profiles = []
    rules = set()
    options = []
    transport = None
    enhancements = []

    def add_profiles(e, carrier):
        nonlocal transport
        for p in profiles_of(e, res):
            tn = p.get("typeName")
            ch = chars(p)
            if tn == "Unit":
                key = (p["name"], tuple(ch.items()))
                if key not in [(u["name"], tuple(u["stats"].items())) for u in unit_profiles]:
                    unit_profiles.append({"name": p["name"], "stats": {k: v for k, v in ch.items() if k != "Base"}, "base": ch.get("Base", "")})
            elif tn in ("Ranged Weapons", "Melee Weapons"):
                w = weapons.setdefault(p["name"], {"name": p["name"], "type": "ranged" if tn.startswith("Ranged") else "melee",
                                                    **{k: v for k, v in ch.items()}, "carried_by": set()})
                if carrier:
                    w["carried_by"].add(carrier)
            elif tn == "Transport":
                transport = clean(ch.get("Capacity", ""), 400)
            elif tn in ("Abilities",) or tn not in ("Unit",):
                desc = clean(" ".join(ch.values()), 500)
                abilities.setdefault(p["name"], desc)

    def walk(e, carrier, depth, path):
        if depth > 12:
            return
        name = e.get("name", "")
        if name.lower() in SKIP_GROUPS:
            # but remember enhancements living here
            if name.lower() == "enhancements":
                enhancements.append(e)
            return
        etype = e.get("type")
        if etype == "model":
            mn = sel_constraint(e, "min") or 0
            mx = sel_constraint(e, "max")
            # the nearest enclosing group that constrains how many models it holds
            grp = next((g for g in path[1:] if ("type" not in g or g.get("type") == "selectionEntryGroup")
                        and (sel_constraint(g, "max") or sel_constraint(g, "min"))), None)
            models.append({"id": e["id"], "name": name, "min": mn, "max": mx, "pts": cost(e) if depth > 0 else 0, "_group": grp,
                           "_ancestors": [g.get("id") for g in path[1:] if g.get("id")]})
            carrier = name
        add_profiles(e, carrier)
        for r in rule_names(e, res):
            rules.add(r)
        if etype == "upgrade" and cost(e) and depth > 0:
            options.append({"name": name, "pts": cost(e), "on": carrier,
                            "enhancement_slot": bool(cost(e, ENHANCEMENT_SLOTS))})
        kids = list(children(e, res))
        is_group = "type" not in e or e.get("type") == "selectionEntryGroup"
        if is_group and kids and depth > 0:
            choice_names = [k.get("name") for k in kids if k.get("name", "").lower() not in SKIP_GROUPS]
            mx = sel_constraint(e, "max")
            mn = sel_constraint(e, "min")
            if choice_names and not any(k.get("type") == "model" for k in kids):
                options.append({"group": name, "on": carrier, "min": mn, "max": mx, "choices": choice_names[:20]})
        for k in kids:
            walk(k, carrier, depth + 1, path + [e])

    walk(unit, None, 0, [])
    return models, weapons, abilities, unit_profiles, sorted(r for r in rules if r), options, transport, enhancements


def model_count_range(unit, models):
    if unit.get("type") == "model" and not models:
        return 1, 1
    groups = {}
    loose_min, loose_max = 0, 0
    for m in models:
        g = m["_group"]
        if g is not None:
            groups[g["id"]] = g
        else:
            loose_min += m["min"] or 0
            loose_max += m["max"] if m["max"] is not None else (m["min"] or 1)
    gmin = sum(sel_constraint(g, "min") or 0 for g in groups.values())
    gmax = sum(sel_constraint(g, "max") or 0 for g in groups.values())
    mn, mx = loose_min + gmin, loose_max + gmax
    if mn == 0 and mx == 0:
        return (1, 1)
    return max(1, mn), max(mn, mx, 1)


def leader_targets(abilities):
    txt = abilities.get("Leader")
    if not txt:
        return []
    m = re.search(r"following units?:\s*(.*)", txt, re.I)
    if not m:
        return []
    part = re.split(r"\.\s|You can attach|Note", m.group(1))[0]
    items = [clean(x).strip(" .■") for x in re.split(r",|■|\n|•", part)]
    return [i.title() if i.isupper() else i for i in items if i and len(i) < 60]


def compile_unit(unit, link, res, source, faction_cat, ally_source):
    u = merged(link, unit)
    models, weapons, abilities, unit_profiles, rules, options, transport, enh = collect(u, res)
    mn, mx = model_count_range(u, models)
    tiers, pnotes = points_by_size(u, res, models, mn, mx)
    cats = []
    for cl in u.get("categoryLinks", []) or []:
        cats.append(cl.get("name") or res.category_names.get(cl.get("targetId"), ""))
    cats = [c for c in dict.fromkeys(cats) if c]
    factions = [c.replace("Faction: ", "") for c in cats if c.startswith("Faction: ")]
    keywords = [c for c in cats if not c.startswith("Faction: ") and not c.startswith("Allies:")]
    max_copies = None
    for cn in (u.get("constraints", []) or []):
        if cn.get("field") == "selections" and cn.get("type") == "max" and cn.get("scope") in ("force", "roster"):
            v = cn.get("value")
            max_copies = v if max_copies is None else min(max_copies, v)
    name = u["name"]
    role = "Epic Hero" if "Epic Hero" in keywords else "Character" if "Character" in keywords else \
        "Battleline" if "Battleline" in keywords else "Dedicated Transport" if "Dedicated Transport" in keywords else \
        "Fortification" if "Fortification" in keywords else "Other"
    return {
        "name": name,
        "id": unit["id"],
        "source": source,
        "ally": ally_source,
        "legends": "[Legends]" in name,
        "crucible": "[Crucible]" in name,
        "hidden": bool(u.get("hidden")),
        "role": role,
        "keywords": keywords,
        "faction_keywords": factions,
        "models": {"min": mn, "max": mx, "types": [{"name": m["name"], "min": m["min"], "max": m["max"]} for m in models]},
        "points": compress_tiers(tiers),
        "points_notes": pnotes[:6],
        "copy_surcharge": copy_surcharges(all_mods(u)),
        "max_copies": max_copies,
        "profiles": unit_profiles,
        "weapons": [dict(w, carried_by=sorted(w["carried_by"])) for w in weapons.values()],
        "abilities": abilities,
        "core_rules": rules,
        "leader_of": leader_targets(abilities),
        "transport": transport,
        "options": options[:40],
    }


# ----------------------------------------------------------------------------
# Detachments & enhancements
# ----------------------------------------------------------------------------

def rule_text(e, res):
    out = []
    for r in e.get("rules", []) or []:
        out.append({"name": r["name"], "text": clean(r.get("description", ""), 1500)})
    for il in e.get("infoLinks", []) or []:
        if il.get("type") == "rule":
            r = res.rule(il.get("targetId"))
            if r:
                out.append({"name": r["name"], "text": clean(r.get("description", ""), 1500)})
        elif il.get("type") == "profile":
            p = res.profile(il.get("targetId"))
            if p:
                out.append({"name": p["name"], "text": clean(" ".join(chars(p).values()), 1500)})
        elif il.get("type") == "infoGroup":
            g = res.info_group(il.get("targetId"))
            if g:
                for r in rule_text(g, res):
                    out.append(r)
    for p in e.get("profiles", []) or []:
        out.append({"name": p["name"], "text": clean(" ".join(chars(p).values()), 1500)})
    for g in e.get("infoGroups", []) or []:
        out += rule_text(g, res)
    return out


def _faction_cond(c, fac_ids):
    """Evaluate 'primary-catalogue' conditions; None if not about the faction."""
    if c.get("scope") != "primary-catalogue":
        return None
    t = c.get("type")
    if t == "instanceOf":
        return c.get("childId") in fac_ids
    if t == "notInstanceOf":
        return c.get("childId") not in fac_ids
    return None


def _faction_group(g, fac_ids):
    rs = [_faction_cond(c, fac_ids) for c in g.get("conditions", []) or []]
    rs += [_faction_group(x, fac_ids) for x in g.get("conditionGroups", []) or []]
    if not rs or any(r is None for r in rs):
        return None
    return all(rs) if g.get("type", "and") == "and" else any(rs)


def hidden_for_faction(e, fac_ids):
    """True if the entry is hidden because the army's main catalogue is another one
    (e.g. Blood Angels detachments when building plain Space Marines)."""
    for m in all_mods(e):
        if m.get("field") != "hidden" or m.get("type") != "set" or not m.get("value"):
            continue
        r = _faction_group(m, fac_ids)
        if r:
            return True
    return False


def find_detachments(roots, res, fac_ids=()):
    dets = []
    seen = set()
    for link, e, src in roots:
        if e.get("name") != "Detachment":
            continue
        def walk(x, depth=0):
            if depth > 6:
                return
            for k in children(x, res):
                dp = cost(k, DETACHMENT_POINTS)
                if k.get("type") == "upgrade" and (dp or any(c.get("typeId") == DETACHMENT_POINTS for c in k.get("costs", []) or [])) and k["id"] not in seen:
                    seen.add(k["id"])
                    if hidden_for_faction(k, fac_ids):
                        continue
                    dets.append({
                        "name": k["name"],
                        "id": k["id"],
                        "detachment_points": dp,
                        "source": src.name,
                        "tags": [cl.get("name") for cl in k.get("categoryLinks", []) or [] if cl.get("name")],
                        "rules": rule_text(k, res),
                        "enhancements": [],
                    })
                else:
                    walk(k, depth + 1)
        walk(e)
    return dets


def condition_ids(e):
    ids = set()
    def g(x):
        for c in x.get("conditions", []) or []:
            if c.get("childId"):
                ids.add(c["childId"])
        for cg in x.get("conditionGroups", []) or []:
            g(cg)
    for m in all_mods(e):
        g(m)
    return ids


def attach_enhancements(units_raw, dets, res):
    by_id = {d["id"]: d for d in dets}
    enh = {}
    for unit_name, groups in units_raw:
        for grp in groups:
            gids = condition_ids(grp)
            def walk(x, inherited, depth=0):
                if depth > 6:
                    return
                for k in children(x, res):
                    ids = inherited | condition_ids(k)
                    if k.get("type") == "upgrade" and (cost(k, ENHANCEMENT_SLOTS) or cost(k)):
                        rec = enh.setdefault(k["name"], {
                            "name": k["name"], "pts": cost(k),
                            "text": clean(" ".join(" ".join(chars(p).values()) for p in profiles_of(k, res)), 600),
                            "detachments": set(), "units": set(),
                            "max_in_army": None,
                        })
                        rec["units"].add(unit_name)
                        for i in ids:
                            if i in by_id:
                                rec["detachments"].add(by_id[i]["name"])
                        for cn in k.get("constraints", []) or []:
                            if cn.get("field") == "selections" and cn.get("type") == "max" and cn.get("scope") in ("force", "roster"):
                                rec["max_in_army"] = cn.get("value")
                    else:
                        walk(k, ids, depth + 1)
            walk(grp, gids)
    unassigned = []
    for rec in enh.values():
        rec["units"] = sorted(rec["units"])
        if not rec["detachments"]:
            unassigned.append(rec)
        for dn in rec["detachments"]:
            for d in dets:
                if d["name"] == dn:
                    d["enhancements"].append({k: v for k, v in rec.items() if k != "detachments"})
    return [{k: (sorted(v) if isinstance(v, set) else v) for k, v in r.items()} for r in unassigned]


# ----------------------------------------------------------------------------
# Army limits
# ----------------------------------------------------------------------------

def army_limits(world):
    """Read battle sizes and roster limits from the game system."""
    gs = world.gs.c
    force = gs["forceEntries"][0]
    cons = {c["id"]: c for c in force.get("constraints", [])}
    out = {
        "battle_sizes": {"Incursion": 1000, "Strike Force": 2000, "Onslaught": 3000},
        "detachment_points": {},
        "enhancements": {},
        "notes": [],
    }
    dp_id = next((i for i, c in cons.items() if c["field"] == DETACHMENT_POINTS), None)
    en_id = next((i for i, c in cons.items() if c["field"] == ENHANCEMENT_SLOTS), None)
    base_dp = cons[dp_id]["value"] if dp_id else None
    base_en = cons[en_id]["value"] if en_id else None
    dp = {"Incursion": base_dp, "Strike Force": base_dp, "Onslaught": base_dp}
    en = {"Incursion": base_en, "Strike Force": base_en, "Onslaught": base_en}
    def names_in(m):
        txt = json.dumps(m)
        return [n for n in ("Incursion", "Strike Force", "Onslaught") if n in txt]
    for m in force.get("modifiers", []):
        if m.get("field") == dp_id and m.get("type") == "set":
            txt = json.dumps(m)
            for n in names_in(m):
                if n == "Incursion" and "3DP Detachment" in txt:
                    out["notes"].append(f"Incursion: Detachment Points limit becomes {m['value']} if you take a 3DP detachment")
                    continue
                dp[n] = m["value"]
        if m.get("field") == en_id and m.get("type") == "set":
            conds = m.get("conditions", []) or []
            negated = [c for c in conds if c.get("type") == "equalTo" and c.get("value") == 0]
            if negated:
                excluded = [n for n in ("Incursion", "Strike Force", "Onslaught") if n in json.dumps(negated)]
                for n in ("Incursion", "Strike Force", "Onslaught"):
                    if n not in excluded:
                        en[n] = m["value"]
            else:
                for n in names_in(m):
                    en[n] = m["value"]
    out["detachment_points"] = dp
    out["enhancements"] = en
    char = next((c for c in force.get("categoryLinks", []) if c.get("name") == "Character"), None)
    if char and any(c.get("type") == "min" for c in char.get("constraints", [])):
        out["notes"].append("At least 1 CHARACTER is required (to be your Warlord)")
    return out


# ----------------------------------------------------------------------------
# Build faction
# ----------------------------------------------------------------------------

def build(faction_query, force=False):
    rd = sync()
    world = World(rd)
    fac = world.find_faction(faction_query)
    slug = re.sub(r"[^a-z0-9]+", "-", fac.name.lower()).strip("-")
    out = compiled_dir() / f"{slug}.json"
    if out.exists() and not force:
        try:
            if json.loads(out.read_text()).get("compiler_version") == COMPILER_VERSION:
                return out
        except Exception:
            pass
    res = Resolver(world, fac)
    roots = []
    for cat, imp in res.order:
        if not imp:
            continue
        for l in cat.c.get("entryLinks", []) or []:
            tgt, _ = res.entry(l.get("targetId"))
            if tgt is not None:
                roots.append((l, tgt, cat))
        for e in cat.c.get("selectionEntries", []) or []:
            roots.append((None, e, cat))

    fac_tokens = set(re.findall(r"\w+", fac.name.lower())) - {"imperium", "chaos", "aeldari", "library", "the", "of"}
    fac_ids = {fac.id}
    units = []
    seen = set()
    enh_sources = []
    for link, e, cat in roots:
        if e.get("type") not in ("unit", "model") or e["id"] in seen:
            continue
        seen.add(e["id"])
        ally = None
        if cat.name in ALLY_SOURCES and cat.id != fac.id:
            src_tokens = set(re.findall(r"\w+", cat.name.lower())) - {"imperium", "chaos", "library", "the", "of", "aeldari"}
            if not (fac_tokens & src_tokens):
                ally = ALLY_SOURCES[cat.name]
        if hidden_for_faction(merged(link, e), fac_ids):
            continue
        try:
            cu = compile_unit(e, link, res, cat.name, fac, ally)
        except RecursionError:
            continue
        units.append(cu)
        _, _, _, _, _, _, _, enh = collect(merged(link, e), res)
        if enh:
            enh_sources.append((cu["name"], enh))

    dets = find_detachments(roots, res, fac_ids)
    unassigned = attach_enhancements(enh_sources, dets, res)
    data = {
        "compiler_version": COMPILER_VERSION,
        "faction": fac.name,
        "data_revision": data_revision(),
        "source": "https://github.com/BSData/wh40k-11e",
        "limits": army_limits(world),
        "detachments": dets,
        "unassigned_enhancements": unassigned,
        "units": sorted(units, key=lambda u: u["name"]),
    }
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return out


def load(faction_query):
    return json.loads(build(faction_query).read_text())


# ----------------------------------------------------------------------------
# Presentation
# ----------------------------------------------------------------------------

def pts_str(u):
    return " / ".join(f"{t['models']}m:{t['pts']}" for t in u["points"])


def stat_str(p):
    s = p["stats"]
    inv = s.get("InSv")
    return f"M{s.get('M','?')} T{s.get('T','?')} Sv{s.get('Sv','?')}{' Inv'+inv if inv else ''} W{s.get('W','?')} Ld{s.get('LD','?')} OC{s.get('OC','?')}"


def cmd_units(a):
    d = load(a.faction)
    print(f"# {d['faction']}  (data {d['data_revision']})")
    print("name | role | models | pts | stats | max | surcharge | keywords")
    for u in d["units"]:
        if u["hidden"] and not a.legends:
            continue
        if u["legends"] and not a.legends:
            continue
        if u["crucible"] and not a.legends:
            continue
        if u["ally"] and not a.allies:
            continue
        if a.grep and a.grep.lower() not in json.dumps(u).lower():
            continue
        if not u["points"] or all(t["pts"] == 0 for t in u["points"]):
            continue
        st = stat_str(u["profiles"][0]) if u["profiles"] else ""
        sur = ",".join(f"#{s['from_copy']}+:+{s['extra_pts']}" for s in u["copy_surcharge"])
        kw = ",".join(k for k in u["keywords"] if k not in (u["name"],))[:90]
        tag = f" [ally:{u['ally']}]" if u["ally"] else ""
        print(f"{u['name']}{tag} | {u['role']} | {u['models']['min']}-{u['models']['max']} | {pts_str(u)} | {st} | {u['max_copies'] or '-'} | {sur} | {kw}")


def cmd_unit(a):
    d = load(a.faction)
    wanted = [w.strip().lower() for w in a.name.split(",") if w.strip()]
    for w in wanted:
        hits = [u for u in d["units"] if u["name"].lower() == w] or [u for u in d["units"] if w in u["name"].lower()]
        if not hits:
            print(f"!! no unit matching '{w}'")
            continue
        for u in hits[:3]:
            print(f"\n## {u['name']}  ({u['role']}{', ally: '+u['ally'] if u['ally'] else ''}{', LEGENDS' if u['legends'] else ''})")
            print(f"Points: {pts_str(u)}" + (f"  | extra copies: " + ", ".join(f"copy #{s['from_copy']}+ costs +{s['extra_pts']}" for s in u['copy_surcharge']) if u['copy_surcharge'] else ""))
            if u["points_notes"]:
                print("Point notes: " + "; ".join(u["points_notes"]))
            print(f"Models: {u['models']['min']}-{u['models']['max']}  " + ", ".join(f"{m['name']}({m['min']}-{m['max']})" for m in u['models']['types']))
            print(f"Max copies: {u['max_copies']}")
            for p in u["profiles"]:
                print(f"Profile {p['name']}: {stat_str(p)}  base {p['base']}")
            print("Keywords: " + ", ".join(u["keywords"]) + " | Faction: " + ", ".join(u["faction_keywords"]))
            for wpn in u["weapons"]:
                skill = wpn.get("BS") or wpn.get("WS")
                print(f"  W {wpn['type']:6} {wpn['name']}: R{wpn.get('Range','')} A{wpn.get('A')} {'BS' if wpn['type']=='ranged' else 'WS'}{skill} S{wpn.get('S')} AP{wpn.get('AP')} D{wpn.get('D')} [{wpn.get('Keywords','')}]  ({', '.join(wpn['carried_by'])})")
            for k, v in u["abilities"].items():
                print(f"  A {k}: {v}")
            if u["core_rules"]:
                print("  Core: " + ", ".join(u["core_rules"]))
            if u["leader_of"]:
                print("  Leads: " + ", ".join(u["leader_of"]))
            if u["transport"]:
                print("  Transport: " + u["transport"])
            for o in u["options"]:
                if "group" in o:
                    print(f"  O [{o['group']}] ({o.get('min')}-{o.get('max')}) on {o.get('on')}: " + " | ".join(o["choices"]))
                else:
                    print(f"  O +{o['pts']}pts {o['name']} on {o.get('on')}")


def units_str(units):
    if len(units) > 8:
        return ""  # broadly available: the restriction is in the text ("X model only")
    return "  -> only on: " + ", ".join(units)


def cmd_detachments(a):
    d = load(a.faction)
    lim = d["limits"]
    print(f"# Detachments for {d['faction']}  (data {d['data_revision']})")
    print(f"Detachment Points by battle size: {lim['detachment_points']}; enhancements: {lim['enhancements']}; " + "; ".join(lim["notes"]))
    for det in d["detachments"]:
        src = "" if det.get("source") == d["faction"] else f"  (from {det.get('source')})"
        print(f"\n## {det['name']}  [{det['detachment_points']} DP]  tags: {', '.join(det['tags'])}{src}")
        for r in det["rules"]:
            txt = r["text"] if a.full else clean(r["text"], 350)
            print(f"  Rule {r['name']}: {txt}")
        for e in det["enhancements"]:
            txt = e["text"] if a.full else clean(e["text"], 200)
            print(f"  Enh {e['name']} ({e['pts']} pts, max {e['max_in_army']}): {txt}  {units_str(e['units'])}")
    if d["unassigned_enhancements"]:
        print(f"\n({len(d['unassigned_enhancements'])} other enhancements are not tied to any of these detachments "
              "- usually allied-faction or Crusade items; ignore them for matched play)")


def cmd_limits(a):
    rd = sync()
    print(json.dumps(army_limits(World(rd)), indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("sync"); s.add_argument("--force", action="store_true")
    sp.add_parser("factions")
    s = sp.add_parser("build"); s.add_argument("faction"); s.add_argument("--force", action="store_true")
    s = sp.add_parser("units"); s.add_argument("faction"); s.add_argument("--legends", action="store_true"); s.add_argument("--allies", action="store_true"); s.add_argument("--grep")
    s = sp.add_parser("unit"); s.add_argument("faction"); s.add_argument("name")
    s = sp.add_parser("detachments"); s.add_argument("faction"); s.add_argument("--full", action="store_true")
    sp.add_parser("limits")
    a = ap.parse_args()
    if a.cmd == "sync":
        rd = sync(force=a.force, max_age_hours=0 if a.force else 12)
        print(f"Data at {rd} ({data_revision()})")
    elif a.cmd == "factions":
        print("\n".join(World(sync()).playable()))
    elif a.cmd == "build":
        print(build(a.faction, force=a.force))
    elif a.cmd == "units":
        cmd_units(a)
    elif a.cmd == "unit":
        cmd_unit(a)
    elif a.cmd == "detachments":
        cmd_detachments(a)
    elif a.cmd == "limits":
        cmd_limits(a)


if __name__ == "__main__":
    sys.setrecursionlimit(10000)
    try:
        main()
    except BrokenPipeError:  # e.g. piped into `head`
        sys.stderr.close()

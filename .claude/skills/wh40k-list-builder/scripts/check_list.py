#!/usr/bin/env python3
"""Price, validate and pretty-print an 11th edition army list.

Input: a JSON file (or '-' for stdin) like

{
  "faction": "necrons",
  "battle_size": "Strike Force",          # Incursion | Strike Force | Onslaught
  "points_limit": 2000,                   # optional, defaults to battle size
  "detachments": ["Awakened Dynasty"],
  "force_disposition": "Take and Hold",   # optional
  "units": [
    {"name": "Overlord", "models": 1, "enhancement": "Veil of Darkness",
     "wargear": "Voidscythe", "attached_to": "Lychguard#1", "warlord": true},
    {"name": "Lychguard", "models": 10, "label": "Lychguard#1"},
    {"name": "Necron Warriors", "models": 20}
  ]
}

"attached_to" points at another entry's "label" (or its unit name when unique).
"wargear" is free text printed in the list. Options that cost points in the data
(e.g. "Rupture cannon" +20) are charged when their name appears in it; prefix a
count for several ("2x Macro plasma incinerator"). Paid options available on a unit
but not named are listed as a note so you can decide.

Counts in "wargear" (and in an optional metrics "loadout") are also checked against the
datasheet's limits for that unit size: model types ("Berzerker w/ eviscerator and bolt
pistol"), shared pools (Kasrkin: 4 special weapons in total) and weapons ("Chainfist":
1 per 5 models). Write how many models carry each option, e.g.
  "wargear": "4x Khornate eviscerator, 2x Plasma pistol"
or give the models per type explicitly:
  "composition": {"Berzerker w/ eviscerator and bolt pistol": 4}
`bsdata.py unit` prints these caps as "L" lines.

Output: the formatted list plus a list of problems (errors and warnings).
Exit code 1 if there are errors.

Usage:  check_list.py list.json|- [--json] [--metrics [--mods SPEC]]   ('-' reads the list from stdin)
"""
import argparse
import json
import re
import sys

for _s in (sys.stdin, sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bsdata  # noqa: E402
import loadout as loadout_rules  # noqa: E402

DISPOSITIONS = {"Take and Hold", "Disruption", "Purge the Foe", "Priority Assets", "Reconnaissance"}
# Unit Limit by battle size (doubled for BATTLELINE). Onslaught is not confirmed in
# public previews; 3 is assumed - say so to the user if it matters.
UNIT_LIMIT = {"Incursion": 2, "Strike Force": 3, "Onslaught": 3}


def _norm(s):
    s = re.sub(r"[‐‑‒–—]", "-", s.lower())
    return re.sub(r"\s+", " ", re.sub(r"[*^]", "", s)).strip()


def _singular(w):
    return w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def enhancement_restriction(text):
    """Parse the 'X model only' prefix of an enhancement text.

    Returns (alternatives, excluded) where alternatives is a list of keyword
    phrases (any one must match) and excluded is a list of keyword phrases the
    bearer must not have; or None when the text has no such restriction.
    """
    if not text:
        return None
    m = re.match(r"\s*(.{1,160}?)\bonly\b", text)
    if not m or not re.search(r"[*^]|[A-Z]{3,}", m.group(1)):
        return None
    prefix = m.group(1)
    # "(excluding X models)" can sit before or right after "only"
    tail = re.match(r"\s*\(excluding[^)]*\)", text[m.end():])
    scope = prefix + (tail.group(0) if tail else "")
    excluded = [_norm(x) for x in re.findall(r"excluding\s+([^)]*)\)", scope)]
    prefix = re.sub(r"\(excluding[^)]*\)", " ", prefix)
    prefix = re.sub(r"with the .*? ability", " ", prefix)
    phrase = _norm(re.sub(r"\bmodels?\b", " ", prefix))
    if not phrase:
        return None
    alts = [a.strip() for a in phrase.split("/") if a.strip()]
    excluded = [_norm(re.sub(r"\bmodels?\b", " ", e)) for e in excluded]
    return alts, excluded


def _phrase_matches(phrase, kws):
    """Consume the phrase greedily with the unit's keywords (longest first).

    Returns (matched, leftover_words)."""
    words = phrase.split()
    i, left = 0, []
    while i < len(words):
        for j in range(len(words), i, -1):
            cand = " ".join(words[i:j])
            if cand in kws or _singular(cand) in kws:
                i = j
                break
        else:
            left.append(words[i])
            i += 1
    return not left, left


def _unit_kws(unit):
    return {_norm(k) for k in unit["keywords"] + unit.get("faction_keywords", []) + [unit["name"]]}


def check_enhancement_bearer(unit, enh, faction_keywords, faction_units=()):
    """Returns (error, warning) for an enhancement's 'X model only' restriction.

    If no unit of the faction meets the restriction in the data, the keyword must
    come from a rule (e.g. a detachment granting CHARACTER), so it only warns."""
    r = enhancement_restriction(enh.get("text"))
    if not r:
        return None, None
    alts, excluded = r
    kws = _unit_kws(unit)
    unknown = []
    for alt in alts:
        ok, left = _phrase_matches(alt, kws)
        if ok:
            break
        # the phrase is made of real keywords of this faction -> the unit just lacks them
        if not _phrase_matches(alt, faction_keywords)[0]:
            unknown.append(alt)
    else:
        if unknown:
            return None, f"{enh['name']} on {unit['name']}: could not verify the restriction '{' / '.join(alts)}' — check it"
        need = " / ".join(a.upper() for a in alts)
        if faction_units and not any(_phrase_matches(a, _unit_kws(x))[0] for x in faction_units for a in alts):
            return None, (f"{enh['name']} on {unit['name']}: no datasheet has the keywords {need}; they must come "
                          "from a rule (e.g. the detachment) — check the bearer qualifies")
        return f"{unit['name']} cannot take {enh['name']}: it is restricted to {' / '.join(a.upper() for a in alts)} models", None
    for ex in excluded:
        if _phrase_matches(ex, kws)[0]:
            return f"{unit['name']} cannot take {enh['name']}: {ex.upper()} models are excluded", None
    return None, None


def _lead_name(s):
    """Datasheet name without a trailing [Legends] / [Crucible] tag, lower case."""
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", s.lower()).strip()


def leads_body(leads, body_name, all_names):
    """Can a Leader whose 'can be attached to' list is `leads` join the unit `body_name`?

    Entries that are real datasheet names must match exactly ('Intercessor Squad' does not
    cover 'Heavy Intercessor Squad'); other entries (keywords, name prefixes) match loosely."""
    b = _lead_name(body_name)
    for l in leads:
        l = _lead_name(l)
        if l == b or (l not in all_names and (l in b or b in l)):
            return True
    return False


def norm_size(s):
    s = (s or "Strike Force").lower()
    if "incur" in s or s.startswith("1"):
        return "Incursion"
    if "onsl" in s or s.startswith("3"):
        return "Onslaught"
    return "Strike Force"


def find_unit(units, name):
    n = name.lower().strip()
    exact = [u for u in units if u["name"].lower() == n]
    if exact:
        # a datasheet can appear twice (e.g. a hidden legacy copy): prefer the visible one
        exact.sort(key=lambda u: bool(u.get("hidden")))
        return exact[0], []
    sub = [u for u in units if n in u["name"].lower()]
    if len(sub) == 1:
        return sub[0], []
    return None, [u["name"] for u in sub[:6]]


def price(unit, models):
    for t in unit["points"]:
        lo, _, hi = t["models"].partition("-")
        if int(lo) <= models <= int(hi or lo):
            return t["pts"], None
    sizes = ", ".join(t["models"] for t in unit["points"])
    return None, f"{unit['name']}: {models} models is not a legal size (legal: {sizes})"


def wargear_cost(unit, text):
    """Charge paid options whose names appear in the wargear text."""
    opts = {o["name"]: o["pts"] for o in unit.get("options", []) if o.get("pts") and "name" in o}
    cnt = named_counts(text, opts)
    return sum(opts[k] * c for k, c in cnt.items()), set(cnt)


def named_counts(text, names):
    """{name: count} for the names that appear in free text. A count is written
    before the name ("4x Khornate eviscerator", "2 x Plasma pistol"); no count means 1.
    Longer names win, so 'Flamer' does not match inside 'Heavy flamer'."""
    low = (text or "").lower()
    out = {}
    for nm in sorted(set(names), key=len, reverse=True):
        pat = re.compile(r"(?:(\d+)\s*x\s*)?(?<![\w-])" + re.escape(nm.lower()) + r"(?:e?s)?(?![\w-])")
        for m in pat.finditer(low):
            out[nm] = out.get(nm, 0) + int(m.group(1) or 1)
        low = pat.sub(lambda m: " " * len(m.group(0)), low)
    return out


def _at(v, n, lo):
    if isinstance(v, list):
        return v[max(0, min(len(v) - 1, n - lo))]
    return v


class _Caps:
    """Model-type and weapon caps of one unit at one size, from its compiled limits."""

    def __init__(self, unit, n):
        L = unit["limits"]
        self.unit, self.n, self.lo = unit, n, L["sizes"][0]
        self.mt, self.weapons = L["models"], L["weapons"]
        self.uncertain = set(L.get("uncertain", []))
        self.mins = {t: self.at(v["min"]) or 0 for t, v in self.mt.items()}
        self.maxs = {t: self.at(v["max"]) for t, v in self.mt.items()}
        self.pools = {p: (self.at(v["max"]), v["members"]) for p, v in L["pools"].items()}
        # the data's unit structure may not match this size (some units compile as 1 model)
        self.ok = sum(self.mins.values()) <= n and L["sizes"][0] <= n <= L["sizes"][1]

    def at(self, v):
        return _at(v, self.n, self.lo)

    def eff(self, t):
        cap = self.maxs[t] if self.maxs[t] is not None else self.n
        return max(0, min(cap, self.n - sum(v for o, v in self.mins.items() if o != t)))

    def weapon(self, wname):
        """Most copies of this weapon the unit can have, or None if unknown."""
        w, n = self.weapons[wname], self.n
        car = w["carriers"]
        pms = [self.at(pm) for pm in car.values()]
        if not pms or any(pm is None for pm in pms):
            return None
        per = max(pms)
        typed = [t for t in car if t in self.mt]
        if typed:
            bound = sum(self.eff(t) for t in typed)
            bound = min(bound, n - sum(v for o, v in self.mins.items() if o not in car))
            for _, (pmax, members) in self.pools.items():
                if pmax is not None and any(t in members for t in typed):
                    bound = min(bound, pmax + sum(self.eff(t) for t in typed if t not in members))
            cap = bound * per
        else:
            cap = per * (n if self.unit["name"] in car or not self.mt else 1)
        um = self.at(w["unit_max"])
        return min(cap, um) if um is not None else cap


def wargear_caps(unit, n):
    """({model type: max}, {pool: (max, members)}, {weapon: max}, uncertain names) for
    a unit of n models, or None when the data cannot tell. Used by bsdata.py unit."""
    if not unit.get("limits"):
        return None
    c = _Caps(unit, n)
    if not c.ok:
        return None
    return ({t: c.eff(t) for t in c.mt}, c.pools, {w: c.weapon(w) for w in c.weapons}, c.uncertain)


def wargear_limits_check(unit, models, text="", composition=None, loadout=None):
    """Check model-type counts and weapon counts against the datasheet's limits
    for this unit size. Returns (errors, warnings)."""
    if not unit.get("limits"):
        return [], []
    errs, warns = [], []
    C = _Caps(unit, models)
    n, mt, weapons, mins, pools = models, C.mt, C.weapons, C.mins, C.pools
    if not C.ok:
        return [], ([f"{unit['name']}: wargear limits not checked (unit structure in the data does not match "
                     f"{n} model{'s' if n != 1 else ''}); check the datasheet"] if text or composition or loadout else [])

    def report(name, msg):
        unsure = name in C.uncertain
        (warns if unsure else errs).append(
            f"{unit['name']}: {msg}" + (" (the data has conditions that may raise this limit; check the datasheet)"
                                        if unsure else ""))

    # explicit composition {"model type": count}; else model types named in the text
    explicit = bool(composition)
    if not composition and text:
        composition = named_counts(text, [t for t in mt if t.lower() != unit["name"].lower()])
    counts = named_counts(text, weapons) if text else {}
    for wname, c in (loadout or {}).items():
        base = next((w for w in sorted(weapons, key=len, reverse=True)
                     if wname.lower() == w.lower() or wname.lower().startswith(w.lower() + " ")), None)
        if base:
            counts[base] = max(counts.get(base, 0), c)
    # weapons that only one model type carries imply that many models of that type
    if not explicit:
        composition = dict(composition or {})
        for wname, c in counts.items():
            typed = [t for t in weapons[wname]["carriers"] if t in mt]
            pm = C.at(weapons[wname]["carriers"].get(typed[0])) if len(typed) == 1 else None
            if pm:
                composition[typed[0]] = max(composition.get(typed[0], 0), -(-c // pm))
    if composition:
        given = {}
        for t, c in composition.items():
            key = next((k for k in mt if k.lower() == t.lower()), None) or \
                next((k for k in mt if t.lower() in k.lower()), None)
            if not key:
                errs.append(f"{unit['name']}: no model type '{t}' (types: {', '.join(mt)})")
                continue
            given[key] = given.get(key, 0) + c
        for key, c in given.items():
            if c > C.eff(key):
                report(key, f"{c}x {key} but at most {C.eff(key)} in a unit of {n}")
        for p, (pmax, members) in pools.items():
            s = sum(given.get(m, 0) for m in members)
            if pmax is not None and s > pmax:
                report(p, f"{s} models from '{p}' ({', '.join(m for m in members if given.get(m))}) but at most {pmax}")
        total = sum(given.values())
        room = n - sum(v for t, v in mins.items() if t not in given)
        if explicit and total > n:
            errs.append(f"{unit['name']}: composition adds up to {total} models, unit has {n}")
        elif total > room:
            errs.append(f"{unit['name']}: {total} models with these loadouts "
                        f"({', '.join(f'{c}x {t}' for t, c in given.items())}) but only {room} fit in a unit of {n} "
                        f"after the required models")

    # weapons named in the wargear text / loadout
    for wname, c in counts.items():
        cap = C.weapon(wname)
        if cap is not None and c > cap:
            report(wname, f"{c}x {wname} but at most {cap} in a unit of {n} model{'s' if n != 1 else ''}")
    errs += per_n_check(unit, n, counts, composition, errs)
    return errs, warns


def per_n_check(unit, n, weapon_counts, type_counts, already=()):
    """BSData "Max X <item> per N models" error rules, applied at any unit size (the
    data often encodes them only for some sizes, e.g. below 20 models)."""
    if not unit.get("tree"):
        return []
    out = []
    counts = {k.lower(): v for k, v in (weapon_counts or {}).items()}
    for k, v in (type_counts or {}).items():
        counts[k.lower()] = counts.get(k.lower(), 0) + v
    for r in loadout_rules.per_n_rules(unit):
        have = sum(counts.get(x.lower(), 0) for x in r["names"])
        lim = (n // r["per"]) * r["max"] + r.get("other_carriers", 0)
        if have > lim and not any(any(x.lower() in e.lower() for x in r["names"]) for e in already):
            out.append(f"{unit['name']}: {have}x {' / '.join(sorted(r['names']))} but '{r['message']}' allows {lim} in a unit of {n}")
    return out


def surcharge(unit, copy_index):
    """copy_index is 1-based."""
    extra = 0
    for s in unit.get("copy_surcharge", []):
        if copy_index >= s["from_copy"]:
            extra = max(extra, s["extra_pts"])
    return extra


def check(spec):
    d = bsdata.load(spec["faction"])
    size = norm_size(spec.get("battle_size"))
    lim = d["limits"]
    size_limit = lim["battle_sizes"][size]
    pts_limit = spec.get("points_limit") or size_limit
    errors, warnings = [], []
    if pts_limit > size_limit:
        errors.append(f"points_limit {pts_limit} exceeds the {size_limit} pts of a {size} game")
    dets_by_name = {x["name"].lower(): x for x in d["detachments"]}

    # --- detachments
    chosen = []
    for dn in spec.get("detachments", []):
        det = dets_by_name.get(dn.lower()) or next((x for x in d["detachments"] if dn.lower() in x["name"].lower()), None)
        if not det:
            errors.append(f"Detachment '{dn}' not found for {d['faction']}. Options: " + ", ".join(x["name"] for x in d["detachments"]))
        else:
            chosen.append(det)
    if not chosen:
        errors.append("No detachment chosen")
    dp_total = sum(x["detachment_points"] for x in chosen)
    dp_limit = lim["detachment_points"].get(size)
    if dp_limit is not None and dp_total > dp_limit:
        if len(chosen) == 1:
            warnings.append(f"Single detachment costs {dp_total} DP > {dp_limit} DP for {size}: allowed only under the "
                            "'one lone detachment is always allowed' rule (check current errata / event pack)")
        else:
            errors.append(f"Detachment Points: {dp_total} > {dp_limit} allowed in {size}")
    tag_count = Counter(t for x in chosen for t in x["tags"] if t not in DISPOSITIONS)
    for t, c in tag_count.items():
        if c > 1:
            errors.append(f"Two detachments share the unique tag '{t}'")
    dispositions = sorted({t for x in chosen for t in x["tags"] if t in DISPOSITIONS})
    disp = spec.get("force_disposition")
    if disp and disp not in dispositions:
        errors.append(f"Force Disposition '{disp}' is not granted by the chosen detachments ({', '.join(dispositions)})")
    if not disp and len(dispositions) > 1:
        warnings.append(f"Choose one Force Disposition: {', '.join(dispositions)}")
    faction_kws = {_norm(k) for x in d["units"] for k in x["keywords"] + x.get("faction_keywords", []) + [x["name"]]}
    chosen_names = {x["name"].lower() for x in chosen}
    allowed_enh = {}
    for det in chosen:
        for e in det["enhancements"]:
            allowed_enh[e["name"].lower()] = (e, det["name"])

    # --- units
    rows = []
    copies = Counter()
    labels = {}
    total = 0
    for i, entry in enumerate(spec.get("units", [])):
        u, cands = find_unit(d["units"], entry["name"])
        if not u:
            errors.append(f"Unit '{entry['name']}' not found" + (f" (did you mean: {', '.join(cands)}?)" if cands else ""))
            continue
        comp = entry.get("composition")
        comp_v = None
        if isinstance(comp, list) and u.get("tree"):
            comp_v = loadout_rules.validate(u, comp, catalogue=d["faction"])
        models = entry.get("models") or (comp_v["models"] if comp_v else None) or \
            (u["points"][0]["best_size"] if u["points"] else 1)
        p, err = price(u, models)
        if err:
            errors.append(err)
            p = 0
        copies[u["name"]] += 1
        extra = surcharge(u, copies[u["name"]])
        enh_pts = 0
        enh_name = entry.get("enhancement")
        if enh_name:
            hit = allowed_enh.get(enh_name.lower()) or next(((e, dn) for k, (e, dn) in allowed_enh.items() if enh_name.lower() in k), None)
            unit_opt = next((o for o in u.get("options", []) if o.get("enhancement_slot") and o.get("name", "").lower() == enh_name.lower()), None)
            if not hit and unit_opt:
                # datasheet upgrade that uses an Enhancement slot (e.g. Norn upgrades)
                hit = ({"name": unit_opt["name"], "pts": unit_opt["pts"], "units": [], "max_in_army": 1}, "datasheet")
            if not hit:
                errors.append(f"Enhancement '{enh_name}' is not available in the chosen detachments")
            else:
                e, dn = hit
                allowed_enh.setdefault(e["name"].lower(), hit)
                enh_pts = e["pts"]
                enh_name = e["name"]
                if e["units"] and u["name"] not in e["units"]:
                    errors.append(f"{u['name']} cannot take enhancement {e['name']}")
                elif dn != "datasheet":
                    err, warn = check_enhancement_bearer(u, e, faction_kws, d["units"])
                    if err:
                        errors.append(err)
                    if warn:
                        warnings.append(warn)
                is_upgrade = (e.get("max_in_army") or 1) > 1 or "upgrade" in (e.get("text") or "").lower()
                if dn != "datasheet" and not is_upgrade and "Character" not in u["keywords"]:
                    warnings.append(f"{e['name']} on {u['name']}: only Upgrade enhancements go on non-CHARACTER units; "
                                    "the data allows it but check the detachment text")
        gear_pts, gear_names = wargear_cost(u, entry.get("wargear") or "")
        lo_spec = entry.get("loadout")
        loadout = {}
        for part in (lo_spec or "").split(","):
            m = re.match(r"\s*(?:(\d+)\s*x\s*)?(.+?)\s*$", part, re.I)
            if m and m.group(2):
                loadout[m.group(2).lower()] = int(m.group(1) or 1)
        if comp_v is not None:
            # full datasheet rules (model types, choices, per-N limits, fixed compositions)
            v = comp_v
            if entry.get("models") and entry["models"] != v["models"]:
                errors.append(f"{u['name']}: 'models' is {entry['models']} but the composition has {v['models']}")
            errors += [f"{u['name']} (wargear): {e}" for e in v["errors"]]
            warnings += [f"{u['name']} (wargear): {w}" for w in v["warnings"]]
            if not u.get("loadout_verified", True):
                warnings.append(f"{u['name']}: the data's options for this unit could not be fully confirmed; check the datasheet")
            names = {o.lower() for ln in comp for o in ln.get("options", [])}
            gear_pts = v["wargear_pts"]
            gear_names = {o["name"] for o in u.get("options", []) if o.get("name", "").lower() in names}
        else:
            lerr, lwarn = wargear_limits_check(u, models, entry.get("wargear") or "", comp, loadout)
            errors += lerr
            warnings += lwarn
        gear_slots = [o["name"] for o in u.get("options", []) if o.get("enhancement_slot") and o["name"] in gear_names]
        if gear_slots and enh_name:
            errors.append(f"{u['name']}: {', '.join(gear_slots)} uses an Enhancement slot, so the unit cannot also take {enh_name}")
        elif gear_slots:
            enh_name = gear_slots[0]
            gear_pts -= next(o["pts"] for o in u["options"] if o.get("name") == enh_name)
            enh_pts = next(o["pts"] for o in u["options"] if o.get("name") == enh_name)
            allowed_enh.setdefault(enh_name.lower(), ({"name": enh_name, "pts": enh_pts, "units": [], "max_in_army": 1}, "datasheet"))
        # datasheet upgrades tied to a detachment ("<Detachment> Enhancements" groups)
        for o in u.get("options", []):
            if o.get("enhancement_slot") and o.get("name") in gear_names | {enh_name}:
                grp = next((g["group"] for g in u["options"] if o["name"] in g.get("choices", [])), "")
                det_name = re.sub(r"\s*Enhancements$", "", grp).strip().lower()
                if det_name and det_name in dets_by_name and det_name not in chosen_names:
                    errors.append(f"{u['name']}: {o['name']} needs the {grp.replace(' Enhancements', '')} detachment")
        # paid options worth a reminder: skip enhancement-slot upgrades (optional) and
        # alternatives in a pick-one group where another choice is already named
        wtext = (entry.get("wargear") or "").lower()
        taken_groups = {g["group"] for g in u.get("options", []) if g.get("choices") and g.get("max") == 1
                        and any(c.lower() in wtext for c in g["choices"])}
        paid = sorted({o["name"] for o in u.get("options", []) if o.get("pts") and o["name"] not in gear_names
                       and not o.get("enhancement_slot")
                       and not any(o["name"] in g.get("choices", []) and g["group"] in taken_groups for g in u["options"])})
        if paid:
            warnings.append(f"{u['name']}: paid options not in its wargear text: " + ", ".join(
                f"{n} (+{next(o['pts'] for o in u['options'] if o.get('name') == n)})" for n in paid))
        cost = p + extra + enh_pts + gear_pts
        total += cost
        label = entry.get("label") or u["name"]
        row = {"i": i, "unit": u, "models": models, "base": p, "extra": extra, "enh": enh_name, "enh_pts": enh_pts, "gear_pts": gear_pts,
               "cost": cost, "label": label, "entry": entry}
        rows.append(row)
        labels.setdefault(label.lower(), []).append(row)

    # unit limits
    ulimit = UNIT_LIMIT[size]
    for name, c in copies.items():
        u = next(r["unit"] for r in rows if r["unit"]["name"] == name)
        lim_u = ulimit * (2 if "Battleline" in u["keywords"] else 1)
        if "Epic Hero" in u["keywords"]:
            lim_u = 1
        if u.get("max_copies"):
            lim_u = min(lim_u, u["max_copies"]) if u["max_copies"] >= 1 else lim_u
        if c > lim_u:
            errors.append(f"{name} x{c} exceeds the limit of {lim_u} in {size}")
        if u["legends"]:
            warnings.append(f"{name} is a Legends unit (usually not allowed in matched play events)")
        if u.get("crucible"):
            warnings.append(f"{name} is a Crucible unit (not for matched play)")
        if u.get("hidden"):
            warnings.append(f"{name} is flagged hidden in the data (not normally selectable); verify it is legal for this army")
        if u["ally"]:
            warnings.append(f"{name} is an allied unit ({u['ally']}): check the allied-units rules of your detachment/faction")

    # enhancements
    enh_rows = [r for r in rows if r["enh"]]
    enh_counter = Counter(r["enh"] for r in enh_rows)
    slots = 0
    for en, c in enh_counter.items():
        e = allowed_enh.get(en.lower(), (None,))[0]
        max_in_army = (e or {}).get("max_in_army") or 1
        upgrade = max_in_army > 1
        if c > max_in_army:
            errors.append(f"Enhancement {en} taken {c} times (max {max_in_army})")
        slots += 1 if upgrade else c
    enh_limit = lim["enhancements"].get(size)
    if enh_limit is not None and slots > enh_limit:
        errors.append(f"Enhancements: {slots} slots used > {enh_limit} allowed in {size}")
    for r in enh_rows:
        if "Epic Hero" in r["unit"]["keywords"]:
            errors.append(f"{r['unit']['name']} is an Epic Hero and cannot take enhancements")

    # characters & attachments
    if not any("Character" in r["unit"]["keywords"] for r in rows):
        errors.append("The army needs at least one CHARACTER (Warlord)")
    warlords = [r for r in rows if r["entry"].get("warlord")]
    if len(warlords) > 1:
        errors.append("More than one unit is marked as Warlord: " + ", ".join(r["label"] for r in warlords))
    for r in warlords:
        if "Character" not in r["unit"]["keywords"]:
            errors.append(f"{r['unit']['name']} is marked as Warlord but is not a CHARACTER")
    if rows and not warlords:
        warnings.append('No unit is marked as Warlord (add "warlord": true to a CHARACTER)')
    all_names = {_lead_name(x["name"]) for x in d["units"]}
    attached = defaultdict(list)
    attached_ids = set()
    for r in rows:
        tgt = r["entry"].get("attached_to")
        if not tgt:
            if "Support" in r["unit"]["keywords"] and "Character" in r["unit"]["keywords"]:
                errors.append(f"{r['unit']['name']} is a SUPPORT character: it must be attached to a bodyguard unit (attached_to)")
            continue
        if "Character" not in r["unit"]["keywords"]:
            errors.append(f"{r['unit']['name']} is not a CHARACTER: only Leader/Support characters can be attached to another unit")
            continue
        hits = labels.get(str(tgt).lower())
        if not hits:
            errors.append(f"{r['label']}: attached_to '{tgt}' does not match any unit label")
            continue
        if len(hits) > 1:
            errors.append(f"{r['label']}: attached_to '{tgt}' matches {len(hits)} units; give each one a unique \"label\"")
            continue
        body = hits[0]
        if body is r:
            errors.append(f"{r['label']} cannot be attached to itself")
            continue
        leads = r["unit"].get("leader_of", [])
        if not leads:
            warnings.append(f"{r['unit']['name']}: the data does not list which units it can lead; "
                            f"check on its datasheet that it can join {body['unit']['name']}")
        elif not leads_body(leads, body["unit"]["name"], all_names):
            errors.append(f"{r['unit']['name']} cannot be attached to {body['unit']['name']} (can lead: {', '.join(leads)})")
        kind = "Support" if "Support" in r["unit"]["keywords"] else "Leader"
        attached[body["label"]].append((kind, r))
        attached_ids.add(id(r))
    for lbl in attached:
        if id(labels[lbl.lower()][0]) in attached_ids:
            errors.append(f"{lbl} is itself attached to another unit, so it cannot also be a bodyguard")
    for lbl, lst in attached.items():
        kinds = Counter(k for k, _ in lst)
        for k, c in kinds.items():
            if c > 1:
                errors.append(f"{lbl} has {c} {k} characters attached (max 1 Leader and 1 Support)")
        enhanced = [r for _, r in lst if r["enh"]]
        if len(enhanced) > 1:
            errors.append(f"{lbl}: {' and '.join(r['label'] for r in enhanced)} both carry an Enhancement; "
                          "only one CHARACTER in a unit can have an Enhancement")

    if total > pts_limit:
        errors.append(f"Points: {total} > {pts_limit}")
    elif pts_limit - total > 50:
        warnings.append(f"{pts_limit - total} points unspent")

    return d, size, pts_limit, chosen, dispositions, dp_total, dp_limit, rows, total, errors, warnings, attached, slots, enh_limit


def render(spec, res):
    d, size, pts_limit, chosen, dispositions, dp_total, dp_limit, rows, total, errors, warnings, attached, slots, enh_limit = res
    out = []
    title = spec.get("title") or f"{d['faction'].split(' - ')[-1]} - {size}"
    out.append(f"{title} ({total}/{pts_limit} pts)")
    out.append(f"Faction: {d['faction']}")
    out.append(f"Battle size: {size} | Detachments ({dp_total}/{dp_limit} DP): " + ", ".join(f"{x['name']} [{x['detachment_points']}]" for x in chosen))
    disp = spec.get("force_disposition") or (dispositions[0] if len(dispositions) == 1 else " / ".join(dispositions))
    out.append(f"Force Disposition: {disp} | Enhancements: {slots}/{enh_limit}")
    out.append("")
    attached_ids = {id(r) for lst in attached.values() for _, r in lst}

    def line(r, indent=""):
        u = r["unit"]
        parts = [f"{indent}{u['name']}" + (f" x{r['models']}" if r["models"] > 1 else "") + f" ({r['cost']} pts)"]
        extras = []
        if r["entry"].get("warlord"):
            extras.append("Warlord")
        if r["enh"]:
            extras.append(f"Enhancement: {r['enh']} (+{r['enh_pts']})")
        if r["extra"]:
            extras.append(f"repeat-copy surcharge +{r['extra']}")
        if r.get("gear_pts"):
            extras.append(f"paid wargear +{r['gear_pts']}")
        if r["entry"].get("wargear"):
            extras.append(r["entry"]["wargear"])
        if extras:
            parts.append(f"{indent}  • " + "; ".join(extras))
        return parts

    groups = [("CHARACTERS", lambda u: "Character" in u["keywords"]),
              ("BATTLELINE", lambda u: "Battleline" in u["keywords"]),
              ("DEDICATED TRANSPORTS", lambda u: "Dedicated Transport" in u["keywords"]),
              ("OTHER UNITS", lambda u: True)]
    used = set()
    for title_g, pred in groups:
        sel = [r for r in rows if id(r) not in used and id(r) not in attached_ids and pred(r["unit"])]
        if not sel:
            continue
        out.append(title_g)
        for r in sel:
            used.add(id(r))
            out += line(r, "  ")
            for kind, ch in attached.get(r["label"], []):
                out.append(f"    + {kind}: {ch['unit']['name']} ({ch['cost']} pts)" + (f" — {ch['enh']}" if ch["enh"] else "") + (" — Warlord" if ch["entry"].get("warlord") else ""))
        out.append("")
    # attached characters are shown under their bodyguard; list them too for completeness
    if attached_ids:
        out.append("(Attached characters are listed under their bodyguard units)")
        out.append("")
    out.append(f"TOTAL: {total} / {pts_limit} pts  —  data: BSData wh40k-11e {d['data_revision']}")
    out.append("")
    if errors:
        out.append("ERRORS:")
        out += [f"  ✗ {e}" for e in errors]
    if warnings:
        out.append("WARNINGS:")
        out += [f"  ! {w}" for w in warnings]
    if not errors:
        out.append("✓ Legal by the checks in this script (points, DP, unique tags, unit limits, enhancements, attachments)")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--metrics", action="store_true", help="also print the army totals (metrics.py --compare) if the list is legal")
    ap.add_argument("--mods", action="append", default=[], help="with --metrics: rule modifiers, as in metrics.py")
    a = ap.parse_args()
    spec = json.load(sys.stdin if a.file == "-" else open(a.file, encoding="utf-8"))
    res = check(spec)
    if a.json:
        d, size, pts_limit, chosen, dispositions, dp_total, dp_limit, rows, total, errors, warnings, attached, slots, enh_limit = res
        print(json.dumps({"total": total, "limit": pts_limit, "dp": [dp_total, dp_limit], "enhancement_slots": [slots, enh_limit],
                          "errors": errors, "warnings": warnings,
                          "units": [{"name": r["unit"]["name"], "models": r["models"], "cost": r["cost"]} for r in rows]}, indent=1))
    else:
        print(render(spec, res))
    if a.metrics and not res[9]:
        import metrics
        print()
        metrics.compare_specs([(spec, "-")], 0.7, a.mods)
    sys.exit(1 if res[9] else 0)


if __name__ == "__main__":
    main()

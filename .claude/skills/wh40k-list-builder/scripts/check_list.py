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

Output: the formatted list plus a list of problems (errors and warnings).
Exit code 1 if there are errors.

Usage:  check_list.py list.json [--json]
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bsdata  # noqa: E402

DISPOSITIONS = {"Take and Hold", "Disruption", "Purge the Foe", "Priority Assets", "Reconnaissance"}
# Unit Limit by battle size (doubled for BATTLELINE). Onslaught is not confirmed in
# public previews; 3 is assumed - say so to the user if it matters.
UNIT_LIMIT = {"Incursion": 2, "Strike Force": 3, "Onslaught": 3}


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
    total, names = 0, set()
    low = text.lower()
    for o in unit.get("options", []):
        if not o.get("pts") or "name" not in o or o["name"] in names:
            continue
        nm = o["name"].lower()
        idx = low.find(nm)
        if idx < 0:
            continue
        m = re.search(r"(\d+)\s*x\s*$", low[:idx])
        n = int(m.group(1)) if m else 1
        total += o["pts"] * n
        names.add(o["name"])
    return total, names


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
    pts_limit = spec.get("points_limit") or lim["battle_sizes"][size]
    errors, warnings = [], []
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
        models = entry.get("models") or (u["points"][0]["best_size"] if u["points"] else 1)
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
            if not hit:
                errors.append(f"Enhancement '{enh_name}' is not available in the chosen detachments")
            else:
                e, dn = hit
                enh_pts = e["pts"]
                enh_name = e["name"]
                if e["units"] and u["name"] not in e["units"]:
                    errors.append(f"{u['name']} cannot take enhancement {e['name']}")
        gear_pts, gear_names = wargear_cost(u, entry.get("wargear") or "")
        paid = sorted({o["name"] for o in u.get("options", []) if o.get("pts") and o["name"] not in gear_names})
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
    attached = defaultdict(list)
    for r in rows:
        tgt = r["entry"].get("attached_to")
        if not tgt:
            if "Support" in r["unit"]["keywords"] and "Character" in r["unit"]["keywords"]:
                errors.append(f"{r['unit']['name']} is a SUPPORT character: it must be attached to a bodyguard unit (attached_to)")
            continue
        hits = labels.get(str(tgt).lower())
        if not hits:
            errors.append(f"{r['label']}: attached_to '{tgt}' does not match any unit label")
            continue
        body = hits[0]
        leads = [x.lower() for x in r["unit"].get("leader_of", [])]
        if leads and not any(l in body["unit"]["name"].lower() or body["unit"]["name"].lower() in l for l in leads):
            errors.append(f"{r['unit']['name']} cannot be attached to {body['unit']['name']} (can lead: {', '.join(r['unit']['leader_of'])})")
        kind = "Support" if "Support" in r["unit"]["keywords"] else "Leader"
        attached[body["label"]].append((kind, r))
    for lbl, lst in attached.items():
        kinds = Counter(k for k, _ in lst)
        for k, c in kinds.items():
            if c > 1:
                errors.append(f"{lbl} has {c} {k} characters attached (max 1 Leader and 1 Support)")

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
    sys.exit(1 if res[9] else 0)


if __name__ == "__main__":
    main()

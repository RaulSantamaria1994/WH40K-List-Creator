#!/usr/bin/env python3
"""Mathhammer metrics for 40k units compiled by bsdata.py.

For every unit it estimates, at a given unit size:
  * expected damage (wounds dealt, capped per target model) of ranged and melee
    weapons against five reference targets,
  * durability: how many attacks of three reference weapon profiles are needed
    to destroy the whole unit,
  * objective control, movement, and all of the above per 100 points.

These are *estimates* for comparing units, not a battle simulator. Assumptions:
attacker is in half range (Rapid Fire / Melta count half their bonus), guns under
24" are discounted (x0.95 at 18", x0.85 at 12", x0.75 below) for firing less often,
melee is weighted 0.7 and further discounted for slow units in the aggregate scores, stationary
bonuses (Heavy) and re-rolls from abilities/detachments are ignored unless given
with --mods, Blast uses
the reference target's model count, and by default each model uses its best
ranged and its best melee weapon (use --loadout for multi-weapon platforms).

Usage:
  metrics.py <faction> [--units "A,B"] [--size N] [--per100] [--sort KEY] [--json]
             [--loadout "Unit=3xWeapon,1xOther"] [--allies] [--legends]
  metrics.py --compare list1.json list2.json ... [--mods SPEC]
                                                  # totals per army list (check_list.py format;
                                                  # optional per-unit "loadout": "3xGun,1xFist",
                                                  # list-level and per-unit "mods")
  --mods "[KEYWORD:] token; token"  rule modifiers (detachment rules, auras, leaders):
      hit+N wound+N ap+N dmg+N attacks+N lethal sustainedX dev critN
      reroll_hit1 reroll_hit reroll_wound1 reroll_wound oc+N fnpN dr1 invulnN
      token@filters limits it to ranged/melee and/or target keywords (infantry, vehicle, monster)
      e.g. --mods "lethal@vehicle,monster"  --mods "MONSTER: oc+2"  --mods "TYRANIDS: sustained1@infantry"
  sort keys: horde, elite, tank, durability, oc, value
"""
import argparse
import json
import math
import re
import sys

for _s in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bsdata  # noqa: E402

TARGETS = {
    # name: (T, Sv, Inv, W per model, models, keywords)
    "horde": dict(T=3, Sv=5, Inv=None, W=1, n=10, kw={"INFANTRY"}, label="Horde (T3 5+ 1W x10)"),
    "meq": dict(T=4, Sv=3, Inv=None, W=2, n=5, kw={"INFANTRY"}, label="MEQ (T4 3+ 2W x5)"),
    "teq": dict(T=5, Sv=2, Inv=4, W=3, n=5, kw={"INFANTRY"}, label="TEQ (T5 2+/4++ 3W x5)"),
    "vehicle": dict(T=10, Sv=3, Inv=None, W=12, n=1, kw={"VEHICLE"}, label="Vehicle (T10 3+ 12W)"),
    "monster": dict(T=12, Sv=2, Inv=5, W=22, n=1, kw={"MONSTER", "VEHICLE", "TITANIC"}, label="Big (T12 2+/5++ 22W)"),
}

REF_ATTACKS = {
    # name: (S, AP, D)  hitting on 3+
    "small_arms": (5, -1, 1.0),
    "heavy": (9, -2, 3.0),
    "anti_tank": (12, -3, 4.5),
}


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------

def dice_avg(s):
    """'D6+2' -> 5.5, '2D6' -> 7, '3' -> 3, 'D3' -> 2."""
    if s is None:
        return 0.0
    s = str(s).strip().upper().replace(" ", "")
    if s in ("", "-", "N/A"):
        return 0.0
    total = 0.0
    for term in re.findall(r"[+-]?[^+-]+", s):
        sign = -1 if term.startswith("-") else 1
        term = term.lstrip("+-")
        m = re.fullmatch(r"(\d*)D(\d+)", term)
        if m:
            n = int(m.group(1) or 1)
            total += sign * n * (int(m.group(2)) + 1) / 2
        else:
            try:
                total += sign * float(term)
            except ValueError:
                pass
    return total


def roll_target(s):
    m = re.search(r"(\d)\+", str(s or ""))
    return int(m.group(1)) if m else None


def p_success(target):
    if target is None:
        return 1.0
    target = min(max(target, 2), 7)
    return (7 - target) / 6


def kw_value(keywords, name):
    m = re.search(name + r"\s*(D?\d+(?:\+\d+)?)", keywords, re.I)
    return m.group(1) if m else None


def wound_target(S, T):
    if S >= 2 * T:
        return 2
    if S > T:
        return 3
    if S == T:
        return 4
    if S * 2 <= T:
        return 6
    return 5


def parse_fnp(unit):
    for r in unit.get("core_rules", []):
        m = re.search(r"Feel No Pain\s*(\d)\+", r)
        if m:
            return int(m.group(1))
    for txt in unit.get("abilities", {}).values():
        m = re.search(r"Feel No Pain\s*(\d)\+", txt)
        if m:
            return int(m.group(1))
    return None


def damage_reduction(unit):
    for txt in unit.get("abilities", {}).values():
        if re.search(r"subtract 1 from (that|the) attack.{0,3}s Damage", txt, re.I):
            return 1
    return 0


# ----------------------------------------------------------------------------
# Rule modifiers (detachment rules, auras, leaders...)
# ----------------------------------------------------------------------------
# A modifier spec is "[KEYWORD:] token; token; ..." where the optional KEYWORD
# limits it to units with that keyword (e.g. "MONSTER: hit+1; oc+2"). Tokens:
#   hit+N wound+N ap+N dmg+N attacks+N   (hit/wound are capped at +/-1 in total)
#   lethal  sustainedX  dev  critN (critical hits on N+)
#   reroll_hit1 reroll_hit reroll_wound1 reroll_wound   (re-roll 1s / all fails)
#   oc+N  fnpN  dr1 (-1 Damage taken)  invulnN           (unit side)
# A token can end in "@filters": ranged, melee and/or target keywords
# (infantry, vehicle, monster), e.g. "lethal@vehicle,monster", "ap+1@melee".

MOD_RE = re.compile(r"^(hit|wound|ap|dmg|attacks|oc)\s*([+-]\s*\d+)$|^(lethal|dev)$|^sustained\s*(\d+)$|"
                    r"^crit\s*(\d)\+?$|^(reroll_hit|reroll_wound)(1?)$|^(fnp|invuln)\s*(\d)\+?$|^dr\s*(\d)$")
WEAPON_KINDS = {"ranged", "melee"}


def parse_mods(spec):
    """'MONSTER: hit+1; lethal@vehicle,monster' -> (unit keyword or None, [mod dicts])."""
    spec = (spec or "").strip()
    unit_kw = None
    m = re.match(r"^([A-Za-z][\w \-]*?)\s*:\s*(.*)$", spec)
    if m:
        unit_kw, spec = m.group(1).strip().lower(), m.group(2)
    mods = []
    for tok in spec.split(";"):
        tok = tok.strip().lower()
        if not tok:
            continue
        body, _, filt = tok.partition("@")
        body = body.replace(" ", "")
        fl = {f.strip() for f in filt.split(",") if f.strip()}
        g = MOD_RE.match(body)
        if not g:
            raise SystemExit(f"Unknown modifier '{tok}'. See metrics.py docstring for the syntax.")
        if g.group(1):
            key, val = g.group(1), int(g.group(2).replace(" ", ""))
        elif g.group(3):
            key, val = g.group(3), True
        elif g.group(4):
            key, val = "sustained", int(g.group(4))
        elif g.group(5):
            key, val = "crit", int(g.group(5))
        elif g.group(6):
            key, val = g.group(6), "1" if g.group(7) else "all"
        elif g.group(8):
            key, val = g.group(8), int(g.group(9))
        else:
            key, val = "dr", int(g.group(10))
        mods.append({"key": key, "val": val, "kinds": fl & WEAPON_KINDS,
                     "targets": {f.upper() for f in fl - WEAPON_KINDS}})
    return unit_kw, mods


def mods_for_unit(unit, specs):
    """Mods (list) from several specs that apply to this unit (keyword prefix match)."""
    kws = {k.lower() for k in unit["keywords"] + unit.get("faction_keywords", [])} | {unit["name"].lower()}
    out = []
    for s in specs or []:
        kw, mods = parse_mods(s)
        if kw is None or kw in kws or kw.rstrip("s") in kws:
            out += mods
    return out


def _weapon_mods(mods, w, tgt):
    eff = {}
    for m in mods or []:
        if m["kinds"] and w.get("type") not in m["kinds"]:
            continue
        if m["targets"] and not (m["targets"] & tgt["kw"]):
            continue
        k, v = m["key"], m["val"]
        if k in ("hit", "wound", "ap", "dmg", "attacks"):
            eff[k] = eff.get(k, 0) + v
        elif k in ("sustained", ):
            eff[k] = max(eff.get(k, 0), v)
        elif k == "crit":
            eff[k] = min(eff.get(k, 6), v)
        elif k in ("reroll_hit", "reroll_wound"):
            eff[k] = "all" if "all" in (v, eff.get(k)) else v
        else:
            eff[k] = v
    return eff


def _reroll(p, p_crit, mode):
    """Re-roll 1s (mode '1') or every fail (mode 'all') of a roll with success p."""
    if mode == "all":
        return p + (1 - p) * p, p_crit + (1 - p) * p_crit
    if mode == "1":
        return p + p / 6, p_crit + p_crit / 6
    return p, p_crit


# ----------------------------------------------------------------------------
# Weapon vs target
# ----------------------------------------------------------------------------

def weapon_damage(w, tgt, mods=None):
    """Expected wounds dealt by one model's weapon (all its attacks) vs a target."""
    kws = w.get("Keywords", "") or ""
    kwl = kws.lower()
    eff = _weapon_mods(mods, w, tgt)
    A = dice_avg(w.get("A")) + eff.get("attacks", 0)
    S = dice_avg(w.get("S"))
    AP = abs(dice_avg(w.get("AP"))) + eff.get("ap", 0)
    D = dice_avg(w.get("D")) + eff.get("dmg", 0)
    if A <= 0 or S <= 0:
        return 0.0
    if "blast" in kwl:
        A += tgt["n"] // 5
    rf = kw_value(kws, "Rapid Fire")
    if rf:
        A += dice_avg(rf) * 0.5
    melta = kw_value(kws, "Melta")
    if melta:
        D += dice_avg(melta) * 0.5
    skill = w.get("BS") if w.get("type") == "ranged" else w.get("WS")
    torrent = "torrent" in kwl
    hit_mod = max(-1, min(1, eff.get("hit", 0)))
    crit_h = eff.get("crit", 6)
    if torrent:
        p_hit, p_crit_hit = 1.0, 0.0
    else:
        base = roll_target(skill)
        p_hit = p_success(base - hit_mod) if base else 1.0
        p_crit_hit = p_success(crit_h)
        p_hit = max(p_hit, p_crit_hit)  # a critical hit always hits
        p_hit, p_crit_hit = _reroll(p_hit, p_crit_hit, eff.get("reroll_hit"))
    sus = kw_value(kws, "Sustained Hits")
    sus = max(dice_avg(sus) if sus else 0, eff.get("sustained", 0))
    lethal = "lethal hits" in kwl or eff.get("lethal", False)
    dev = "devastating wounds" in kwl or eff.get("dev", False)
    twin = "twin-linked" in kwl
    crit_w = 6
    for m in re.finditer(r"anti-([a-z ]+?)\s*(\d)\+", kwl):
        if m.group(1).strip().upper() in tgt["kw"]:
            crit_w = min(crit_w, int(m.group(2)))
    wound_mod = max(-1, min(1, eff.get("wound", 0)))
    wt = wound_target(S, tgt["T"]) - wound_mod
    p_wound = p_success(min(wt, crit_w))
    p_crit_wound = p_success(crit_w)
    rw = "all" if twin else eff.get("reroll_wound")
    if eff.get("reroll_wound") == "all":
        rw = "all"
    p_wound, p_crit_wound = _reroll(p_wound, p_crit_wound, rw)
    save_needed = tgt["Sv"] + AP
    if tgt["Inv"]:
        save_needed = min(save_needed, tgt["Inv"])
    p_unsaved = 1 - p_success(save_needed) if save_needed <= 6 else 1.0
    dmg = min(D, tgt["W"])

    hits_normal = A * (p_hit - p_crit_hit)          # non-critical hits
    crit_hits = A * p_crit_hit
    extra_hits = crit_hits * sus
    # wounds from hits that roll to wound
    rolled_hits = hits_normal + extra_hits + (0 if lethal else crit_hits)
    auto_wounds = crit_hits if lethal else 0.0
    exp = 0.0
    if dev:
        crit_w_from_rolls = rolled_hits * p_crit_wound
        normal_w = rolled_hits * (p_wound - p_crit_wound)
        exp += crit_w_from_rolls * dmg                      # mortal-ish, no save
        exp += (normal_w + auto_wounds) * p_unsaved * dmg
    else:
        exp += (rolled_hits * p_wound + auto_wounds) * p_unsaved * dmg
    if "one shot" in kwl:
        exp /= 3  # usable once per battle: spread value over ~3 turns of use
    return exp


# ----------------------------------------------------------------------------
# Unit level
# ----------------------------------------------------------------------------

def range_factor(w):
    """Short-ranged guns fire less often over a game."""
    r = dice_avg(re.sub(r"[^\d]", "", w.get("Range", "") or "0") or 0)
    if r >= 24:
        return 1.0
    if r >= 18:
        return 0.95
    if r >= 12:
        return 0.85
    return 0.75


def delivery_factor(m):
    """Melee damage has to get there: slow units deliver it less reliably."""
    return min(1.0, max(0.5, 0.45 + m["M"] / 20))


def best_weapon(weapons, kind, tgt_name="meq"):
    ws = [w for w in weapons if w["type"] == kind]
    if not ws:
        return None
    return max(ws, key=lambda w: weapon_damage(w, TARGETS[tgt_name]))


def bulk_types(unit, size):
    """Model types that are alternatives for the bulk of the unit (e.g. two weapon variants)."""
    return [t["name"] for t in unit["models"]["types"] if t["max"] is None or t["max"] >= size]


def allocate_models(unit, size, prefer=None):
    types = unit["models"]["types"]
    if not types:
        return {unit["name"]: size}
    counts = {t["name"]: (t["min"] or 0) for t in types}
    remaining = size - sum(counts.values())
    weapons = unit["weapons"]

    def score(tname):
        ws = [w for w in weapons if tname in w["carried_by"]]
        r = best_weapon(ws, "ranged")
        m = best_weapon(ws, "melee")
        return (weapon_damage(r, TARGETS["meq"]) if r else 0) + 0.7 * (weapon_damage(m, TARGETS["meq"]) if m else 0)

    limited = [t for t in types if t["max"] is not None and t["max"] < size]
    bulk = [t for t in types if t not in limited]
    for t in sorted(limited, key=lambda t: -score(t["name"])):
        if remaining <= 0:
            break
        room = t["max"] - counts[t["name"]]
        take = min(room, remaining - (1 if bulk else 0))
        if take > 0:
            counts[t["name"]] += take
            remaining -= take
    if remaining > 0 and bulk:
        pref = [t for t in bulk if t["name"] == prefer]
        best = pref[0] if pref else max(bulk, key=lambda t: score(t["name"]))
        counts[best["name"]] += remaining
    elif remaining > 0 and types:
        counts[types[0]["name"]] += remaining
    return {k: v for k, v in counts.items() if v > 0}


def parse_loadout(spec):
    """'5xGauss reaper,1xStaff of light' -> {'gauss reaper': 5, ...}"""
    out = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(\d+)\s*x\s*(.+)", part, re.I)
        if m:
            out[m.group(2).strip().lower()] = int(m.group(1))
        else:
            out[part.lower()] = 1
    return out


def pick_size(unit, size=None):
    tiers = unit["points"]
    if not tiers:
        return 1, 0
    if size and size != "max":
        size = int(size)
        for t in tiers:
            lo, _, hi = t["models"].partition("-")
            if int(lo) <= size <= int(hi or lo):
                return size, t["pts"]
    t = tiers[-1] if size == "max" else tiers[0]
    return t["best_size"], t["pts"]


def unit_metrics(unit, size=None, loadout=None, prefer=None, mods=None):
    """mods: list of parsed modifiers (see parse_mods / mods_for_unit) already filtered for this unit."""
    size, pts = pick_size(unit, size)
    prof = unit["profiles"][0]["stats"] if unit["profiles"] else {}
    T = dice_avg(prof.get("T")) or 4
    Sv = roll_target(prof.get("Sv")) or 7
    Inv = roll_target(prof.get("InSv"))
    try:
        W = dice_avg(prof.get("W")) or 1
    except Exception:
        W = 1
    OC = dice_avg(prof.get("OC"))
    M = dice_avg(re.sub(r"[^\d+-]", "", prof.get("M", "") or "0") or 0)
    counts = allocate_models(unit, size, prefer)
    weapons = unit["weapons"]
    notes = []

    # Build list of (weapon, number of models firing it)
    firing = []
    if loadout:
        for wname, n in loadout.items():
            w = next((x for x in weapons if x["name"].lower() == wname), None) or \
                next((x for x in weapons if wname in x["name"].lower()), None)
            if w:
                firing.append((w, n))
            else:
                notes.append(f"loadout weapon not found: {wname}")
    else:
        for tname, n in counts.items():
            ws = [w for w in weapons if tname in w["carried_by"]] or weapons
            for kind in ("ranged", "melee"):
                w = best_weapon(ws, kind)
                if w:
                    firing.append((w, n))
        ranged_kinds = {w["name"] for w in weapons if w["type"] == "ranged"}
        if size == 1 and len(ranged_kinds) >= 3:
            notes.append("multi-weapon model: default counts only its best gun; pass --loadout for the real output")

    out = {"name": unit["name"], "size": size, "pts": pts, "M": M, "T": T, "Sv": Sv, "Inv": Inv, "W": W,
           "models": counts, "notes": notes, "ranged": {}, "melee": {},
           "range": max([dice_avg(re.sub(r"[^\d]", "", w.get("Range", "") or "0") or 0) for w, _ in firing if w["type"] == "ranged"] or [0])}
    for tn, tgt in TARGETS.items():
        out["ranged"][tn] = round(sum(weapon_damage(w, tgt, mods) * n * range_factor(w) for w, n in firing if w["type"] == "ranged"), 2)
        out["melee"][tn] = round(sum(weapon_damage(w, tgt, mods) * n for w, n in firing if w["type"] == "melee"), 2)

    # durability
    total_w = W * size  # approximation: every model uses the first profile
    fnp = parse_fnp(unit)
    dr = damage_reduction(unit)
    unit_side = {}
    for m in mods or []:
        if m["key"] in ("fnp", "invuln"):
            unit_side[m["key"]] = min(unit_side.get(m["key"], 7), m["val"])
        elif m["key"] in ("dr", "oc"):
            unit_side[m["key"]] = unit_side.get(m["key"], 0) + m["val"]
    if "fnp" in unit_side:
        fnp = min(fnp or 7, unit_side["fnp"])
    if "invuln" in unit_side:
        Inv = min(Inv or 7, unit_side["invuln"])
        out["Inv"] = Inv
    dr = max(dr, unit_side.get("dr", 0))
    dur = {}
    for rn, (S, AP, D) in REF_ATTACKS.items():
        p_hit = 4 / 6
        p_w = p_success(wound_target(S, T))
        need = Sv + abs(AP)
        if Inv:
            need = min(need, Inv)
        p_uns = 1 - p_success(need) if need <= 6 else 1.0
        dmg = max(D - dr, 1) if D > 1 else D
        dmg = min(dmg, W)
        if fnp:
            dmg *= (1 - p_success(fnp))
        per_attack = p_hit * p_w * p_uns * dmg
        dur[rn] = round(total_w / per_attack, 1) if per_attack > 0 else 999
    out["durability"] = dur
    out["fnp"] = fnp
    out["damage_reduction"] = dr
    out["oc_total"] = (OC + unit_side.get("oc", 0)) * size
    if mods:
        out["notes"].append("with rule modifiers")
    return out


def derived(m, melee_weight=0.7):
    """Aggregate scores used for sorting and optimisation."""
    mw = melee_weight * delivery_factor(m)

    def tot(t):
        return m["ranged"][t] + mw * m["melee"][t]
    return {
        "horde": tot("horde"),
        "elite": (tot("meq") + tot("teq")) / 2,
        "tank": (tot("vehicle") + tot("monster")) / 2,
        "durability": (m["durability"]["small_arms"] / 10 + m["durability"]["heavy"] + m["durability"]["anti_tank"]) / 3,
        "oc": m["oc_total"],
        "move": m["M"],
    }


def per100(v, pts):
    return v * 100 / pts if pts else 0.0


def _as_list(x):
    return [] if not x else ([x] if isinstance(x, str) else list(x))


def list_totals(spec, melee_weight=0.7, extra_mods=None):
    """List-level "mods" (strings, e.g. "MONSTER: hit+1") apply to matching units;
    a unit entry's own "mods" apply to that unit only."""
    d = bsdata.load(spec["faction"])
    tot = {"horde": 0.0, "elite": 0.0, "tank": 0.0, "durability_heavy": 0.0, "durability_at": 0.0,
           "oc": 0.0, "units": 0, "models": 0, "pts": 0}
    for e in spec.get("units", []):
        n = e["name"].lower()
        u = next((x for x in d["units"] if x["name"].lower() == n), None) or \
            next((x for x in d["units"] if n in x["name"].lower()), None)
        if not u:
            continue
        lo = parse_loadout(e["loadout"]) if e.get("loadout") else None
        specs = list(extra_mods or []) + _as_list(spec.get("mods")) + _as_list(e.get("mods"))
        m = unit_metrics(u, e.get("models"), lo, mods=mods_for_unit(u, specs))
        sc = derived(m, melee_weight)
        tot["horde"] += sc["horde"]
        tot["elite"] += sc["elite"]
        tot["tank"] += sc["tank"]
        tot["durability_heavy"] += m["durability"]["heavy"]
        tot["durability_at"] += m["durability"]["anti_tank"]
        tot["oc"] += m["oc_total"]
        tot["models"] += m["size"]
        tot["pts"] += m["pts"]
        if not e.get("attached_to"):
            tot["units"] += 1
    return tot


def compare(files, melee_weight, extra_mods=None):
    print("Expected damage per turn (ranged + discounted melee) vs reference targets; durability = attacks needed to wipe the whole army")
    print("list | pts (no enh/surcharge) | units | models | anti-horde | anti-elite | anti-tank | dur vs heavy | dur vs AT | OC")
    for f in files:
        spec = json.load(open(f, encoding="utf-8"))
        t = list_totals(spec, melee_weight, extra_mods)
        name = spec.get("title") or Path(f).stem
        print(f"{name} | {t['pts']} | {t['units']} | {t['models']} | {t['horde']:.1f} | {t['elite']:.1f} | {t['tank']:.1f} | "
              f"{t['durability_heavy']:.0f} | {t['durability_at']:.0f} | {t['oc']:.0f}")


def main():
    if "--compare" in sys.argv:
        cp = argparse.ArgumentParser()
        cp.add_argument("--compare", nargs="+", required=True)
        cp.add_argument("--mods", action="append", default=[])
        cp.add_argument("--melee-weight", type=float, default=0.7)
        ca = cp.parse_args()
        compare(ca.compare, ca.melee_weight, ca.mods)
        return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("faction")
    ap.add_argument("--units", help="comma separated unit names (substring match)")
    ap.add_argument("--size", help="unit size: number, or 'max' (default: smallest tier)")
    ap.add_argument("--per100", action="store_true", help="show values per 100 points")
    ap.add_argument("--sort", default="value", choices=["horde", "elite", "tank", "durability", "oc", "value", "name"])
    ap.add_argument("--loadout", action="append", default=[], help='"Unit Name=5xWeapon,1xOther" (repeatable)')
    ap.add_argument("--allies", action="store_true")
    ap.add_argument("--legends", action="store_true")
    ap.add_argument("--melee-weight", type=float, default=0.7, help="discount for melee damage being harder to deliver")
    ap.add_argument("--mods", action="append", default=[],
                    help='rule modifiers, e.g. "MONSTER: hit+1; oc+2" or "lethal@vehicle,monster" (repeatable)')
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    d = bsdata.load(a.faction)
    units = d["units"]
    if a.units:
        wanted = [w.strip().lower() for w in a.units.split(",") if w.strip()]
        sel = []
        for w in wanted:
            exact = [u for u in units if u["name"].lower() == w]
            sel += exact or [u for u in units if w in u["name"].lower()]
        units = sel
    else:
        units = [u for u in units if not u["hidden"] and (a.legends or not (u["legends"] or u["crucible"]))
                 and (a.allies or not u["ally"]) and u["points"] and u["points"][0]["pts"] > 0]
    loadouts = {}
    for spec in a.loadout:
        name, _, lo = spec.partition("=")
        loadouts[name.strip().lower()] = parse_loadout(lo)
    size = a.size
    if size and size != "max":
        size = int(size)
    rows = []
    for u in units:
        m = unit_metrics(u, size, loadouts.get(u["name"].lower()), mods=mods_for_unit(u, a.mods))
        m["scores"] = derived(m, a.melee_weight)
        m["scores_per100"] = {k: round(per100(v, m["pts"]), 2) for k, v in m["scores"].items() if k != "move"}
        sp = m["scores_per100"]
        m["value"] = round(sp["horde"] / 4 + sp["elite"] + sp["tank"] + sp["durability"] / 4 + sp["oc"] / 4, 2)
        m["role"] = u["role"]
        rows.append(m)
    key = a.sort
    if key == "name":
        rows.sort(key=lambda r: r["name"])
    elif key == "value":
        rows.sort(key=lambda r: -r["value"])
    else:
        rows.sort(key=lambda r: -r["scores_per100"][key])
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print(f"# {d['faction']} metrics (data {d['data_revision']}) - {'per 100 pts' if a.per100 else 'per unit'}")
    print("Targets: " + "; ".join(f"{k}={t['label']}" for k, t in TARGETS.items()))
    print("Durability = attacks needed to wipe the unit (hit 3+): small_arms S5 AP-1 D1 | heavy S9 AP-2 D3 | anti_tank S12 AP-3 D4.5")
    print("name | size | pts | ranged horde/meq/teq/veh/big | melee horde/meq/teq/veh/big | dur small/heavy/AT | OC | M | R | value")
    for r in rows:
        f = (lambda v: per100(v, r["pts"])) if a.per100 else (lambda v: v)
        rg = "/".join(f"{f(r['ranged'][t]):.1f}" for t in TARGETS)
        ml = "/".join(f"{f(r['melee'][t]):.1f}" for t in TARGETS)
        du = "/".join(f"{f(r['durability'][k]):.0f}" for k in REF_ATTACKS)
        note = ("  ! " + "; ".join(r["notes"])) if r["notes"] else ""
        print(f"{r['name']} | {r['size']} | {r['pts']} | {rg} | {ml} | {du} | {f(r['oc_total']):.1f} | {r['M']:.0f} | {r['range']:.0f} | {r['value']}{note}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.stderr.close()

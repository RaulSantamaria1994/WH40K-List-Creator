#!/usr/bin/env python3
"""Wargear / unit-composition rules for any unit, read from BSData.

Each compiled unit (bsdata.py) carries a selection tree ("tree") with the
datasheet's options exactly as BSData encodes them: model types with their
min/max, weapon choices, unit-wide limits, the modifiers that scale those limits
("per N models") and the error/warning rules ("Max 1 heavy weapon per 5 models",
"All models must have the same weapon"...). This module evaluates them.

A unit's equipment is described as a *composition*: a list of lines, one per group
of identical models.

    [{"model": "Khorne Berzerker Champion", "count": 1, "options": ["Plasma pistol"]},
     {"model": "Berzerker w/ eviscerator and bolt pistol", "count": 2},
     {"model": "Khorne Berzerker", "count": 7}]

* "model": a model type of the datasheet (see `bsdata.py loadout <faction> <unit>`).
* "count": how many models of that line.
* "options": names of the choices those models take (weapons, upgrades). Choices
  not named use the datasheet default. Split a line in two if models differ.
* Unit-level options (icons, unit upgrades) go in a line {"options": [...]} without
  "model".

Functions:
    validate(unit, composition, overrides=None) -> dict(errors, warnings, models, pts, weapons_by_line)
    default_composition(unit, size, score=None, prefer=None) -> composition
    describe(composition) -> short text for lists
    rules_text(unit, size) -> human readable summary of the unit's options and limits

CLI (for debugging):  loadout.py <faction> <unit> [--size N] [--composition JSON]
"""
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OVERRIDES_FILE = HERE.parent / "data" / "wargear_overrides.json"

CMP = {
    "atLeast": lambda a, b: a >= b,
    "greaterThan": lambda a, b: a > b,
    "atMost": lambda a, b: a <= b,
    "lessThan": lambda a, b: a < b,
    "equalTo": lambda a, b: a == b,
    "notEqualTo": lambda a, b: a != b,
}


# ----------------------------------------------------------------------------
# Tree helpers
# ----------------------------------------------------------------------------

class Tree:
    def __init__(self, unit):
        self.unit = unit
        self.root = unit["tree"]
        self.nodes = {}
        self.parent = {}
        self._index(self.root, None)
        self.models = [n for n in self.nodes.values() if n["kind"] == "model" and n is not self.root]
        self.single = not self.models  # the unit itself is the (only) model
        self.mods_by_cid = {}
        for n in self.nodes.values():
            for m in n.get("mods", []):
                if m["field"] not in ("error", "warning"):
                    self.mods_by_cid.setdefault(m["field"], []).append((n, m))

    def _index(self, n, parent):
        self.nodes[n["uid"]] = n
        self.parent[n["uid"]] = parent
        for c in n.get("children", []):
            self._index(c, n)

    def ancestors(self, n):
        out = []
        p = self.parent[n["uid"]]
        while p is not None:
            out.append(p)
            p = self.parent[p["uid"]]
        return out

    def subtree(self, n):
        stack = [n]
        while stack:
            x = stack.pop()
            yield x
            stack.extend(x.get("children", []))

    def has_model_below(self, n):
        return any(x["kind"] == "model" for x in self.subtree(n) if x is not n)

    def model_of(self, n):
        """Nearest model ancestor (or itself), None if the node is unit level."""
        if n["kind"] == "model" and n is not self.root:
            return n
        for a in self.ancestors(n):
            if a["kind"] == "model" and a is not self.root:
                return a
        return None

    def find_model(self, name):
        if not name:
            return None
        q = name.lower().strip()
        if self.single and (q == self.root["name"].lower() or q in self.root["name"].lower()):
            return self.root
        pkg = {x["uid"] for g in package_groups(self) for c in g.get("children", []) for x in self.subtree(c)}
        ok = {x["uid"] for c in getattr(self, "allowed_pkgs", []) for x in self.subtree(c)}
        models = [m for m in self.models if m["uid"] not in pkg or m["uid"] in ok]
        exact = [m for m in models if m["name"].lower() == q]
        if exact:
            return exact[0]
        sub = [m for m in models if q in m["name"].lower()]
        if len(sub) == 1:
            return sub[0]
        return None


def cons(n, ctype):
    return [c for c in n.get("constraints", []) if c["type"] == ctype]


def static_min(n):
    v = [c["value"] for c in cons(n, "min") if c["scope"] == "parent"]
    return max(v) if v else 0


def static_max(n):
    v = [c["value"] for c in cons(n, "max") if c["scope"] == "parent"]
    return min(v) if v else None


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# ----------------------------------------------------------------------------
# Expanding a composition into selection counts
# ----------------------------------------------------------------------------

def _match(children, opt):
    o = norm(opt)
    exact = [c for c in children if norm(c["name"]) == o]
    if exact:
        return exact
    sub = [c for c in children if o and (o in norm(c["name"]))]
    return sub if len(sub) == 1 else []


def package_groups(t):
    """Choice groups whose options are fixed unit compositions ("5 models"/"10 models",
    "1 Sergeant and 9 Troopers"...): each option is an upgrade holding the models."""
    return [g for g in t.nodes.values() if g["kind"] == "group" and _is_choice_group(g)
            and any(c["kind"] == "upgrade" and t.has_model_below(c) for c in g.get("children", []))]


def package_models(t, pkg):
    return [(m, max(1, static_min(m))) for m in t.subtree(pkg) if m["kind"] == "model"]


def _is_choice_group(g):
    return any(c["type"] == "max" for c in g.get("constraints", [])) or static_min(g) > 0


def _select(t, node, count, options, sel, used, line_sel):
    """Select the equipment under `node` (a model, an upgrade or the unit root)."""
    for c in node.get("children", []):
        if c["kind"] == "model" or (c["kind"] == "group" and t.has_model_below(c)):
            continue  # models come from composition lines
        if c["kind"] == "group":
            _select_group(t, c, count, options, sel, used, line_sel)
            continue
        hit = next((o for o in options if norm(o) == norm(c["name"])), None) or \
            next((o for o in options if _match([c], o)), None)
        named = hit is not None
        unit_level = node is t.root and not t.single
        forced = static_min(c) > 0
        free_gear = not c.get("constraints") and not c.get("pts") and not unit_level
        if named or forced or free_gear:
            if named:
                options.remove(hit)
                used.add(hit)
            if c.get("hidden") and not named:
                continue
            _add(sel, line_sel, c, count)
            _select(t, c, count, options, sel, used, line_sel)


def _select_group(t, g, count, options, sel, used, line_sel):
    kids = [c for c in g.get("children", []) if c["kind"] != "model"]
    if not _is_choice_group(g):
        # plain container: behave as if its children hung from the parent
        _select(t, {"children": kids}, count, options, sel, used, line_sel)
        return
    chosen = []
    cap = static_max(g)
    for o in list(options):
        if cap is not None and _qty(chosen) >= cap:
            break  # the group is full: leave the option for another choice slot
        m = _match(kids, o)
        if m:
            used.add(o)
            options.remove(o)
            for x in m:
                if x not in chosen:
                    chosen.append(x)
    for c in kids:
        if static_min(c) > 0 and c not in chosen and not c.get("hidden"):
            chosen.append(c)
    if not chosen or _qty(chosen) < static_min(g):
        need = static_min(g)
        default = g.get("default")
        if default:
            d = [c for c in kids if default in (c.get("id"), c.get("link_id"))]
            chosen += d
        taken = {t.nodes[u].get("id") for u in line_sel}
        for c in kids:
            if _qty(chosen) >= need:
                break
            if c not in chosen and not c.get("hidden") and c.get("id") not in taken:
                chosen.append(c)
        # still short (e.g. "choose 2 heavy weapons" with one kind taken twice)
        while chosen and _qty(chosen) < need:
            chosen.append(chosen[0])
    for c in chosen:
        if c["kind"] == "group":
            _select_group(t, c, count, options, sel, used, line_sel)
        else:
            _add(sel, line_sel, c, count)
            _select(t, c, count, options, sel, used, line_sel)


def _qty(nodes):
    return sum(1 if n["kind"] == "group" else max(1, static_min(n)) for n in nodes)


def _add(sel, line_sel, n, count):
    # equipment taken several times per model (twin pistols, "2 heavy bolters"...)
    qty = 1 if n["kind"] == "model" else max(1, static_min(n))
    sel[n["uid"]] = sel.get(n["uid"], 0) + count * qty
    line_sel[n["uid"]] = line_sel.get(n["uid"], 0) + qty


def expand(t, composition):
    sel = {t.root["uid"]: 1}
    lines = []
    errors = []
    unit_opts = []
    model_lines = []
    for line in composition or []:
        if not line.get("model"):
            unit_opts += line.get("options", [])
        else:
            model_lines.append(line)

    # fixed unit compositions ("packages") chosen with a unit-level option
    chosen_pkgs = []
    for g in package_groups(t):
        pkgs = [c for c in g.get("children", []) if c["kind"] == "upgrade"]
        hit = None
        for o in list(unit_opts):
            m = _match(pkgs, o)
            if m:
                hit = m[0]
                unit_opts.remove(o)
                break
        if hit is None:
            if static_min(g) > 0:
                errors.append(f"choose the unit composition '{g['name']}' with a unit option: "
                              + " | ".join(c["name"] for c in pkgs))
            continue
        _add(sel, {}, hit, 1)
        chosen_pkgs.append(hit)

    t.allowed_pkgs = chosen_pkgs
    for line in model_lines:
        m = t.find_model(line["model"])
        if m is None:
            names = [x["name"] for x in t.models] or [t.root["name"]]
            errors.append(f"model type '{line['model']}' not found (options: {', '.join(names)})")
            continue
        cnt = int(line.get("count", 1))
        opts = list(line.get("options", []))
        pool = list(opts)  # each named option is used once (repeat it to take it twice)
        used = set()
        line_sel = {}
        if m is t.root:
            _select(t, m, 1, pool, sel, used, line_sel)
            line_sel[t.root["uid"]] = 1
        else:
            _add(sel, line_sel, m, cnt)
            _select(t, m, cnt, pool, sel, used, line_sel)
        for o in pool:
            if o not in used:
                avail = sorted({x["name"] for x in t.subtree(m) if x is not m and x["kind"] != "group"})
                errors.append(f"option '{o}' not available for '{m['name']}' (choices: {', '.join(avail[:25])})")
        lines.append({"model": m, "count": cnt, "sel": line_sel, "options": opts})
    # unit-level options and forced unit-level entries
    used = set()
    root_line = {}
    upool = list(unit_opts)
    _select(t, t.root, 1, upool, sel, used, root_line) if not t.single else None
    for o in upool:
        if o not in used:
            errors.append(f"unit option '{o}' not found")
    return sel, lines, errors


# ----------------------------------------------------------------------------
# Counting and condition evaluation
# ----------------------------------------------------------------------------

def _group_count(t, g, sel):
    total = 0
    for c in g.get("children", []):
        if c["kind"] == "group":
            total += _group_count(t, c, sel)
        else:
            total += sel.get(c["uid"], 0)
    return total


def count(t, child_id, scope, sel):
    if scope is None:
        return None
    nodes = list(t.subtree(scope))
    if child_id == "model":
        ms = [n for n in nodes if n["kind"] == "model" and n is not t.root]
        if not ms and t.single:
            return 1
        return sum(sel.get(n["uid"], 0) for n in ms)
    if child_id in (None, "any"):
        return sum(sel.get(n["uid"], 0) for n in nodes if n is not scope)
    total = 0
    for n in nodes:
        if n.get("id") != child_id and n.get("link_id") != child_id:
            continue
        if n["kind"] == "group":
            total += _group_count(t, n, sel)
        else:
            total += sel.get(n["uid"], 0)
    return total


def resolve(t, scope, owner, for_constraint=False):
    if scope in (None, "force", "roster", "primary-catalogue", "ancestor", "primary-category"):
        return None
    if scope == "parent":
        return t.parent[owner["uid"]] or t.root
    if scope == "self":
        return owner
    if scope in ("unit", "unit-self", "root-entry"):
        return t.root
    if scope == "model":
        return t.model_of(owner) or t.root
    if scope == t.root.get("id"):
        return t.root
    for a in [owner] + t.ancestors(owner):
        if a.get("id") == scope or a.get("link_id") == scope:
            return a
    return None


def eval_cond(t, c, owner, sel):
    if c.get("field") != "selections":
        return None
    f = CMP.get(c.get("type"))
    if f is None:
        return None
    n = count(t, c.get("childId"), resolve(t, c.get("scope"), owner), sel)
    if n is None:
        return None
    return f(n, c.get("value", 0))


def eval_group(t, g, owner, sel):
    rs = [eval_cond(t, c, owner, sel) for c in g.get("conditions", []) or []]
    rs += [eval_group(t, x, owner, sel) for x in g.get("conditionGroups", []) or []]
    if not rs:
        return True
    if g.get("type", "and") == "or":
        if any(r is True for r in rs):
            return True
        return None if any(r is None for r in rs) else False
    if any(r is False for r in rs):
        return False
    return None if any(r is None for r in rs) else True


def mod_applies(t, m, owner, sel):
    return eval_group(t, {"type": "and", "conditions": m.get("conditions", []),
                          "conditionGroups": m.get("conditionGroups", [])}, owner, sel)


def effective(t, c, sel):
    v = c["value"]
    for owner, m in t.mods_by_cid.get(c["id"], []):
        ok = mod_applies(t, m, owner, sel)
        if not ok:
            continue
        times = 1
        if m.get("repeats"):
            times = 0
            for r in m["repeats"]:
                n = count(t, r.get("childId"), resolve(t, r.get("scope"), owner), sel)
                if n is None:
                    times = None
                    break
                per = r.get("value") or 1
                k = n / per
                times += (math.ceil(k) if r.get("roundUp") else math.floor(k)) * (r.get("repeats") or 1)
            if times is None:
                continue
        val = m.get("value") or 0
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if m["type"] == "set":
            v = val
        elif m["type"] == "increment":
            v += val * times
        elif m["type"] == "decrement":
            v -= val * times
        elif m["type"] == "multiply":
            v *= val
    return v


def _active(t, n, sel):
    """A node's constraints matter when the thing it hangs from is selected."""
    p = t.parent[n["uid"]]
    while p is not None and p["kind"] == "group":
        p = t.parent[p["uid"]]
    return p is None or sel.get(p["uid"], 0) > 0


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------

def load_overrides():
    try:
        return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def validate(unit, composition, overrides=None, catalogue=None):
    t = Tree(unit)
    sel, lines, errors = expand(t, composition)
    warnings = []
    seen = set()

    def report(kind, msg):
        key = (kind, msg)
        if key not in seen:
            seen.add(key)
            (errors if kind == "error" else warnings).append(msg)

    # constraints
    for n in t.nodes.values():
        if n is t.root or not n.get("constraints"):
            continue
        if n.get("hidden") and sel.get(n["uid"], 0) == 0:
            continue  # not available unless something unlocks it
        per_model_owner = t.model_of(n)
        for c in n["constraints"]:
            scope = resolve(t, c["scope"], n, True)
            if scope is None:
                continue
            # scope inside a model (e.g. "max 1 per model") -> check each line separately
            per_model = per_model_owner is not None and t.model_of(scope) is per_model_owner
            if per_model:
                for ln in lines:
                    if ln["model"] is not per_model_owner:
                        continue
                    ls = dict(ln["sel"])
                    if not _active(t, n, ls) and n["kind"] != "model":
                        continue
                    have = count(t, n.get("id"), scope, ls)
                    lim = effective(t, c, ls)
                    _check(c, have, lim, n, report, f" (per model, line '{ln['model']['name']}')")
            else:
                if not _active(t, n, sel):
                    continue
                have = count(t, n.get("id"), scope, sel)
                lim = effective(t, c, sel)
                _check(c, have, lim, n, report, "")

    # error / warning rules
    for n in t.nodes.values():
        for m in n.get("mods", []):
            if m["field"] not in ("error", "warning"):
                continue
            if n is not t.root and sel.get(n["uid"], 0) == 0 and n["kind"] != "group":
                continue
            if mod_applies(t, m, n, sel) is True:
                msg = str(m.get("value", "")).replace("{this}", n["name"])
                report(m["field"], f"{unit['name']}: {msg}")

    models = count(t, "model", t.root, sel)

    # "Max X ... per N models" rules: BSData encodes them as size bands that often
    # stop short (e.g. only "< 20 models"), so also apply the written rule itself.
    for n in t.nodes.values():
        for m in n.get("mods", []):
            if m["field"] != "error":
                continue
            msg = str(m.get("value", "")).replace("{this}", n["name"])
            rx = re.search(r"max(?:imum)?\s+(\d+)\s+(.+?)\s+per\s+(\d+)\s+models", msg, re.I)
            if not rx:
                continue
            k, per = int(rx.group(1)), int(rx.group(3))
            item = norm(rx.group(2))
            ids = {i for i in _cond_child_ids(m) - {"model", "any", None}
                   if any(norm(x["name"]) == item or (item and item in norm(x["name"]) and len(item) > 3)
                          for x in t.nodes.values() if x.get("id") == i or x.get("link_id") == i)}
            if ids:
                have = sum(count(t, i, t.root, sel) or 0 for i in ids)
            elif norm(n["name"]) == item or item in norm(n["name"]):
                have = sum(sel.get(x["uid"], 0) for x in t.nodes.values() if x.get("id") == n.get("id"))
            else:
                continue  # generic wording ("heavy weapon"): rely on the encoded bands
            lim = (models // per) * k
            if have > lim and f"{unit['name']}: {msg}" not in errors:
                report("error", f"{unit['name']}: {msg} ({have} > {lim} for {models} models)")

    # manual corrections for known data gaps
    for ov in (overrides if overrides is not None else load_overrides()):
        if norm(ov.get("unit")) != norm(unit["name"]):
            continue
        if ov.get("catalogues") and catalogue and catalogue not in ov["catalogues"]:
            continue
        for r in ov.get("rules", []):
            names = {norm(x) for x in r.get("items", [])}
            have = sum(sel.get(x["uid"], 0) for x in t.nodes.values() if norm(x["name"]) in names and x["kind"] != "group")
            lim = (models // r["per_models"]) * r["max"] if r.get("per_models") else r["max"]
            if have > lim:
                report("error", f"{unit['name']}: {r.get('message') or ('max ' + str(lim))} ({have} > {lim}) [correction: {ov.get('source', 'manual')}]")

    pts = sum(n.get("pts", 0) * sel.get(n["uid"], 0) for n in t.nodes.values() if n is not t.root and n["kind"] != "model")
    return {"errors": errors, "warnings": warnings, "models": models, "wargear_pts": pts,
            "weapons_by_line": weapons_by_line(t, lines, sel), "sel": sel}


PER_N = re.compile(r"max(?:imum)?\s+(\d+)\s+(.+?)\s+per\s+(\d+)\s+models", re.I)


def per_n_rules(unit):
    """The "Max X <item> per N models" rules of a unit, as written in BSData errors:
    [{"names": {entry names counted}, "max": X, "per": N, "message": text}]."""
    t = Tree(unit)
    out, seen = [], set()
    for n in t.nodes.values():
        for m in n.get("mods", []):
            if m["field"] != "error":
                continue
            msg = str(m.get("value", "")).replace("{this}", n["name"])
            rx = PER_N.search(msg)
            if not rx or msg in seen:
                continue
            seen.add(msg)
            item = norm(rx.group(2))
            names = {x["name"] for i in _cond_child_ids(m) - {"model", "any", None}
                     for x in t.nodes.values() if (x.get("id") == i or x.get("link_id") == i)
                     and (norm(x["name"]) == item or (len(item) > 3 and item in norm(x["name"])))}
            if not names and (norm(n["name"]) == item or item in norm(n["name"])):
                names = {n["name"]}
            if names:
                ids = {i for i in _cond_child_ids(m)} | ({n.get("id")} if n["name"] in names else set())
                # same-named entries the rule does not count (e.g. the Champion's own
                # plasma pistol): free-text counts cannot tell them apart
                extra = 0
                for x in t.nodes.values():
                    if x["name"] in names and x.get("id") not in ids and x.get("link_id") not in ids:
                        mdl = t.model_of(x)
                        mx = static_max(mdl) if mdl is not None else None
                        extra += (mx if mx is not None else 0) * max(1, static_min(x))
                out.append({"names": names, "max": int(rx.group(1)), "per": int(rx.group(3)), "message": msg,
                            "other_carriers": extra})
    return out


def _cond_child_ids(m):
    ids = set()

    def walk(g):
        for c in g.get("conditions", []) or []:
            ids.add(c.get("childId"))
        for x in g.get("conditionGroups", []) or []:
            walk(x)
    walk(m)
    return ids


def _check(c, have, lim, n, report, where):
    if have is None or lim is None:
        return
    lim_i = int(lim) if float(lim).is_integer() else lim
    if c["type"] == "max" and lim >= 0 and have > lim:
        report("error", f"'{n['name']}': {have} selected, max {lim_i}{where}")
    elif c["type"] == "min" and have < lim:
        report("error", f"'{n['name']}': {have} selected, min {lim_i}{where}")


# ----------------------------------------------------------------------------
# Weapons carried
# ----------------------------------------------------------------------------

def weapons_by_line(t, lines, sel):
    """[(models, [[alt weapon names], ...])] — '➤' profiles of one weapon are alternatives."""
    out = []
    for ln in lines:
        groups = []
        for uid in ln["sel"]:
            n = t.nodes[uid]
            ws = n.get("weapons", [])
            if not ws:
                continue
            alts = [w for w in ws if w.startswith("➤")]
            for w in ws:
                if not w.startswith("➤"):
                    groups.append([w])
            if alts:
                groups.append(alts)
        out.append((ln["count"] if ln["model"] is not t.root else 1, groups))
    if not lines:
        # no composition lines: unit-level weapons only
        groups = [[w] for uid in sel for w in t.nodes[uid].get("weapons", [])]
        out.append((1, groups))
    return out


# ----------------------------------------------------------------------------
# Default (legal) composition
# ----------------------------------------------------------------------------

def default_composition(unit, size, score=None, prefer=None, overrides=None):
    """Build a legal composition of `size` models.

    score(composition) -> float lets the caller (metrics.py) favour the strongest
    legal loadout; without it the datasheet defaults are used. `prefer` names the
    model type that fills the bulk of the unit."""
    t = Tree(unit)
    if t.single:
        comp = [{"model": t.root["name"], "count": 1}]
        return explicit(unit, _improve_options(unit, t, comp, score, overrides))
    pkg_line = []
    allowed = None
    pgs = package_groups(t)
    if pgs:
        pkg_uids = {x["uid"] for g in pgs for c in g.get("children", []) for x in t.subtree(c)}
        outside = {m["uid"] for m in t.models if m["uid"] not in pkg_uids}
        best = None
        for combo in _package_combos(t, pgs):
            inside = {x["uid"] for c in combo for x in t.subtree(c) if x["kind"] == "model"}
            alloc = _allocate(t, outside | inside, size, prefer)
            n = sum(alloc.values())
            key = (abs(n - size), n)
            if best is None or key < best[0]:
                best = (key, combo, outside | inside)
        if best:
            pkg_line = [{"options": [c["name"] for c in best[1]]}]
            allowed = best[2]
    if allowed is None:
        allowed = {m["uid"] for m in t.models}
    counts = _allocate(t, allowed, size, prefer)
    comp = pkg_line + _lines(t, counts)

    def errs(c):
        return penalty(validate(unit, c, overrides)["errors"])

    names = list(dict.fromkeys(t.nodes[u]["name"] for u in allowed if not t.nodes[u].get("hidden")))
    bulk_name = _bulk(t, allowed, prefer)
    # grow: limits that scale with the unit can leave the allocation short of `size`
    for _ in range(size):
        have = sum(ln.get("count", 0) for ln in comp if ln.get("model"))
        if have >= size:
            break
        best = None
        for dst in names:
            trial = [dict(x) for x in comp]
            ln = next((x for x in trial if x.get("model") == dst), None)
            if ln is None:
                trial.append({"model": dst, "count": 1})
            else:
                ln["count"] += 1
            pen = errs(trial)
            if best is None or pen < best[0]:
                best = (pen, trial)
        if best is None:
            break
        comp = best[1]
    # repair: move single models between types while that reduces the errors
    for _ in range(60):
        e = errs(comp)
        if e == 0:
            break
        best = None
        for src in [ln["model"] for ln in comp if ln.get("model") and ln.get("count", 0) > 0]:
            for dst in names:
                if dst == src:
                    continue
                trial = _move(comp, src, dst, 1)
                if trial is None:
                    continue
                te = errs(trial)
                if te < e and (best is None or te < best[0] or (te == best[0] and dst == bulk_name)):
                    best = (te, trial)
        if best is None:
            break
        comp = best[1]

    # upgrade: swap bulk models to special types while it stays legal and scores better
    if score and bulk_name:
        cur = score(comp)
        improved = True
        while improved:
            improved = False
            for dst in names:
                if dst == bulk_name:
                    continue
                trial = _move(comp, bulk_name, dst, 1)
                if trial is None or errs(trial):
                    continue
                sc = score(trial)
                if sc > cur + 1e-9:
                    comp, cur, improved = trial, sc, True
    return explicit(unit, _improve_options(unit, t, comp, score, overrides))


def penalty(errors):
    """How far a composition is from legal: sum of the excess in each broken limit."""
    total = 0
    for e in errors:
        m = re.search(r"(\d+) selected, (max|min) (-?\d+)", e) or re.search(r"\((\d+) > (\d+) for", e)
        if m and len(m.groups()) == 3:
            a, kind, b = int(m.group(1)), m.group(2), int(m.group(3))
            total += (a - b) if kind == "max" else (b - a)
        elif m:
            total += int(m.group(1)) - int(m.group(2))
        else:
            total += 1
    return total


def _package_combos(t, pgs):
    combos = [[]]
    for g in pgs:
        pkgs = [c for c in g.get("children", []) if c["kind"] == "upgrade" and not c.get("hidden")]
        if not pkgs:
            continue
        combos = [c + [p] for c in combos for p in pkgs]
    return combos


def _bulk(t, allowed, prefer=None):
    ms = [t.nodes[u] for u in allowed if not t.nodes[u].get("hidden")]
    if prefer:
        for m in ms:
            if m["name"] == prefer:
                return m["name"]
    if not ms:
        return None
    return min(ms, key=lambda m: (" w/" in m["name"].lower(), static_max(m) is not None, -(static_max(m) or 0), m["uid"]))["name"]


def _allocate(t, allowed, size, prefer=None):
    """Model counts: every type at its minimum, containers filled to their minimum,
    then the rest of `size` on the most flexible type (respecting static maxima)."""
    counts = {u: static_min(t.nodes[u]) for u in allowed}
    ms = [t.nodes[u] for u in allowed]

    def room(m):
        mx = static_max(m)
        return 10 ** 6 if mx is None else mx - counts[m["uid"]]

    def ranked(cands):
        # the plain trooper first: no "w/ special weapon", no maximum, earliest in the datasheet
        return sorted(cands, key=lambda m: (m["name"] != prefer, m.get("hidden", False), " w/" in m["name"].lower(),
                                            static_max(m) is not None, -min(room(m), 999), m["uid"]))
    # containers (groups holding models) up to their minimum
    for g in [n for n in t.nodes.values() if n["kind"] == "group" and static_min(n) > 0 and t.has_model_below(n)]:
        inside = [m for m in ms if g in t.ancestors(m)]
        if not inside:
            continue
        have = sum(counts[m["uid"]] for m in inside)
        need = static_min(g) - have
        for m in ranked(inside):
            if need <= 0:
                break
            add = min(need, room(m))
            if add > 0:
                counts[m["uid"]] += add
                need -= add
    # then up to the requested size, never past a container's maximum
    total = sum(counts.values())
    for m in ranked(ms):
        if total >= size:
            break
        cap = room(m)
        for g in t.ancestors(m):
            gm = static_max(g) if g["kind"] == "group" else None
            if gm is not None:
                cap = min(cap, gm - sum(counts[x["uid"]] for x in ms if g in t.ancestors(x)))
        add = max(0, min(size - total, cap))
        counts[m["uid"]] += add
        total += add
    return counts


def _lines(t, counts):
    agg = Counter()
    for u, n in counts.items():
        if n > 0:
            agg[t.nodes[u]["name"]] += n
    return [{"model": k, "count": v} for k, v in agg.items()]


def explicit(unit, comp):
    """Return the composition with the datasheet choices it implies written out."""
    t = Tree(unit)
    _, lines, _ = expand(t, comp)
    out = []
    li = 0
    for ln in comp:
        if not ln.get("model"):
            out.append(dict(ln))
            continue
        x = dict(ln)
        if li < len(lines):
            picked = []
            for uid in lines[li]["sel"]:
                n = t.nodes[uid]
                p = t.parent[uid]
                if n["kind"] != "model" and p is not None and p["kind"] == "group" and _is_choice_group(p) \
                        and not t.has_model_below(p) and n["name"] not in picked:
                    picked.append(n["name"])
            if picked:
                x["options"] = picked
            li += 1
        out.append(x)
    return out


def _move(comp, src, dst, n):
    comp = [dict(x) for x in comp]
    s = next((x for x in comp if x.get("model") == src), None)
    if s is None or s["count"] < n:
        return None
    s["count"] -= n
    d = next((x for x in comp if x.get("model") == dst), None)
    if d is None:
        comp.append({"model": dst, "count": n})
    else:
        d["count"] += n
    return [x for x in comp if x.get("count", 1) > 0 or not x.get("model")]


def _improve_options(unit, t, comp, score, overrides):
    """For each line, pick the best option in each free choice group (if a scorer is given)."""
    if not score:
        return comp
    for i, ln in enumerate(comp):
        m = t.find_model(ln.get("model"))
        if m is None:
            continue
        groups = [g for g in t.subtree(m) if g["kind"] == "group" and _is_choice_group(g) and not t.has_model_below(g)]
        for g in groups:
            kids = [c for c in g.get("children", []) if c["kind"] != "group" and not c.get("hidden")]
            if len(kids) < 2:
                continue
            best_s, best_opt = score(comp), None
            for c in kids:
                trial = [dict(x) for x in comp]
                opts = [o for o in trial[i].get("options", []) if not _match(kids, o)] + [c["name"]]
                trial[i]["options"] = opts
                v = validate(unit, trial, overrides)
                if v["errors"]:
                    continue
                s = score(trial)
                if s > best_s + 1e-9:
                    best_s, best_opt = s, trial
            if best_opt:
                comp = best_opt
    return comp


# ----------------------------------------------------------------------------
# Presentation
# ----------------------------------------------------------------------------

def describe(composition):
    parts = []
    for ln in composition or []:
        opts = f" ({', '.join(ln['options'])})" if ln.get("options") else ""
        if ln.get("model"):
            parts.append(f"{ln.get('count', 1)}x {ln['model']}{opts}")
        elif ln.get("options"):
            parts.append(f"unit: {', '.join(ln['options'])}")
    return "; ".join(parts)


def rules_text(unit, size=None):
    t = Tree(unit)
    out = []

    def walk(n, depth):
        if n is not t.root:
            bits = []
            for c in n.get("constraints", []):
                sc = "per model" if t.model_of(n) is not None and c["scope"] == "parent" and n["kind"] != "model" else c["scope"]
                if sc in (t.root.get("id"), "unit"):
                    sc = "unit"
                bits.append(f"{c['type']} {c['value']} ({sc})")
            if any(m["field"] in [c["id"] for c in n.get("constraints", [])] for m in n.get("mods", [])) or \
                    any(c["id"] in t.mods_by_cid for c in n.get("constraints", [])):
                bits.append("scales with unit")
            for m in n.get("mods", []):
                if m["field"] in ("error", "warning"):
                    bits.append(f"{m['field']}: {str(m.get('value')).replace('{this}', n['name'])}")
            pts = f" +{n['pts']}pts" if n.get("pts") else ""
            ws = f"  [{', '.join(n['weapons'])}]" if n.get("weapons") else ""
            label = {"model": "MODEL", "group": "choose", "upgrade": "-"}.get(n["kind"], n["kind"])
            out.append("  " * depth + f"{label} {n['name']}{pts}" + (f"  <{'; '.join(dict.fromkeys(bits))}>" if bits else "") + ws)
        for c in n.get("children", []):
            walk(c, depth + 1)
    walk(t.root, 0)
    return "\n".join(out)


def main():
    import argparse
    sys.path.insert(0, str(HERE))
    import bsdata
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("faction")
    ap.add_argument("unit")
    ap.add_argument("--size", type=int)
    ap.add_argument("--composition", help="JSON list of lines to validate")
    a = ap.parse_args()
    d = bsdata.load(a.faction)
    q = a.unit.lower()
    u = next((x for x in d["units"] if x["name"].lower() == q), None) or next((x for x in d["units"] if q in x["name"].lower()), None)
    if not u:
        raise SystemExit(f"unit not found: {a.unit}")
    print(f"## {u['name']} — options and limits from BSData")
    print(rules_text(u))
    size = a.size or (u["points"][0]["best_size"] if u["points"] else 1)
    comp = json.loads(a.composition) if a.composition else default_composition(u, size)
    v = validate(u, comp, catalogue=d["faction"])
    print(f"\nComposition ({v['models']} models): {describe(comp)}")
    print("errors: " + ("; ".join(v["errors"]) or "none"))
    if v["warnings"]:
        print("warnings: " + "; ".join(v["warnings"]))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Search for strong unit combinations under a points budget.

This is the numeric half of the list-building process: it fills the army with
the units that give the best *balanced* output for the player's priorities,
respecting points, unit limits (x2 for Battleline), repeat-copy surcharges and
the player's collection. Characters/leaders, enhancements and detachment
synergies are better chosen by reasoning, so by default characters are only
included when you force them with --must (or pass --include-characters).

Objective: sum over categories of weight * log(1 + 4 * total / reference),
where reference is what an "average" unit of the faction would give for the
whole budget. The log gives diminishing returns, so the search prefers covering
all requested roles over spamming one unit.

Usage:
  optimize.py <faction> --points 2000 [--battle-size "Strike Force"]
      [--weights "horde=1,elite=1,tank=1,durability=1,oc=0.5,move=0"]
      [--must "Overlord,Lychguard:10,Necron Warriors:20x2"]
      [--only "A,B,C"] [--exclude "A,B"] [--owned "Necron Warriors=40,Immortals=10" | --owned owned.json | --owned saved]
      [--loadout "Unit=3xWeapon,..."] [--include-characters] [--allies] [--legends]
      [--copy-decay 0.85] [--top 3] [--seed 1] [--iterations 400] [--json]
"""
import argparse
import json
import math
import random
import re
import sys

for _s in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bsdata  # noqa: E402
import metrics  # noqa: E402

CATS = ["horde", "elite", "tank", "durability", "oc", "move"]
UNIT_LIMIT = {"Incursion": 2, "Strike Force": 3, "Onslaught": 3}


def parse_kv(s, cast=float):
    out = {}
    for part in (s or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = cast(v.strip())
    return out


def parse_owned(s, faction=None):
    if not s:
        return {}
    if s.strip().lower() == "saved":
        import library
        owned = library.owned_map(faction)
        if owned is None:
            raise SystemExit(f"--owned saved: no saved collection for '{faction}' (library.py collection-set)")
        return owned
    p = Path(s)
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw = raw.get("units", raw)  # library collection files wrap the map in "units"
        return {k.lower(): int(v) for k, v in raw.items() if not k.startswith("_")}
    return {k: int(v) for k, v in parse_kv(s, float).items()}


def paid_wargear(unit, loadout):
    """Points for paid options named in a --loadout (same rule as check_list: pts x count)."""
    if not loadout:
        return 0
    total = 0
    for o in unit.get("options", []):
        if not o.get("pts") or o.get("enhancement_slot") or "name" not in o:
            continue
        nm = o["name"].lower()
        n = loadout.get(nm) or next((c for w, c in loadout.items() if nm in w or w in nm), 0)
        total += o["pts"] * n
    return total


def parse_must(s):
    """'Overlord,Lychguard:10,Necron Warriors:20x2' -> [(name, size|None)]*"""
    out = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(.+?)(?::(\d+|max))?(?:x(\d+))?$", part)
        name, size, n = m.group(1).strip(), m.group(2), int(m.group(3) or 1)
        out += [(name, size)] * n
    return out


def find(units, name):
    n = name.lower()
    return next((u for u in units if u["name"].lower() == n), None) or \
        next((u for u in units if n in u["name"].lower()), None)


class Problem:
    def __init__(self, a):
        self.d = bsdata.load(a.faction)
        self.size_name = a.battle_size
        self.budget = a.points
        self.weights = {c: 0.0 for c in CATS}
        self.weights.update({"horde": 1, "elite": 1, "tank": 1, "durability": 1, "oc": 0.5})
        self.weights.update(parse_kv(a.weights))
        self.owned = parse_owned(a.owned, a.faction)
        self.copy_decay = a.copy_decay
        self.limit = UNIT_LIMIT.get(a.battle_size, 3)
        loadouts = {}
        for spec in a.loadout:
            nm, _, lo = spec.partition("=")
            loadouts[nm.strip().lower()] = metrics.parse_loadout(lo)
        units = self.d["units"]
        pool = [u for u in units if u["points"] and u["points"][0]["pts"] > 0 and not u["hidden"]
                and (a.legends or not (u["legends"] or u["crucible"])) and (a.allies or not u["ally"])]
        if a.only:
            names = [x.strip() for x in a.only.split(",") if x.strip()]
            pool = [u for u in (find(units, n) for n in names) if u]
        excl = [x.strip().lower() for x in (a.exclude or "").split(",") if x.strip()]
        pool = [u for u in pool if not any(e in u["name"].lower() for e in excl)]
        if not a.include_characters:
            pool = [u for u in pool if "Character" not in u["keywords"]]
        if self.owned:
            pool = [u for u in pool if u["name"].lower() in self.owned]
        self.units = {u["name"]: u for u in units}
        # options: one per points tier (largest size of that tier)
        self.options = []
        for u in pool:
            for t in u["points"]:
                lo = loadouts.get(u["name"].lower())
                variants = [] if lo else metrics.bulk_types(u, t["best_size"])
                if len(variants) > 1:
                    for v in variants:
                        self.options.append(self._option(u, t["best_size"], None, v))
                else:
                    self.options.append(self._option(u, t["best_size"], lo))
        # forced picks
        self.must = []
        for name, size in parse_must(a.must):
            u = find(units, name)
            if not u:
                raise SystemExit(f"--must unit not found: {name}")
            sz = metrics.pick_size(u, size)[0] if size else u["points"][0]["best_size"]
            self.must.append(self._option(u, sz, loadouts.get(u["name"].lower())))
        # references: median per-point value of the candidate pool x budget
        self.ref = {}
        for c in CATS:
            vals = sorted(o["scores"][c] / o["pts"] for o in self.options if o["pts"]) or [1]
            med = vals[len(vals) // 2] or (sum(vals) / len(vals)) or 1e-6
            self.ref[c] = max(med * self.budget, 1e-6)

    def _option(self, u, size, loadout, variant=None):
        m = metrics.unit_metrics(u, size, loadout, variant)
        sc = metrics.derived(m)
        gear = paid_wargear(u, loadout)
        return {"name": u["name"], "size": m["size"], "pts": m["pts"] + gear, "gear_pts": gear, "scores": sc, "unit": u, "variant": variant,
                "battleline": "Battleline" in u["keywords"], "epic": "Epic Hero" in u["keywords"]}

    def cost(self, items):
        total = 0
        seen = Counter()
        for o in items:
            seen[o["name"]] += 1
            total += o["pts"] + bsdata_surcharge(o["unit"], seen[o["name"]])
        return total

    def feasible_add(self, items, o):
        same = [x for x in items if x["name"] == o["name"]]
        lim = 1 if o["epic"] else self.limit * (2 if o["battleline"] else 1)
        mc = o["unit"].get("max_copies")
        if mc:
            lim = min(lim, mc)
        if len(same) + 1 > lim:
            return False
        if self.owned:
            have = self.owned.get(o["name"].lower(), 0)
            if sum(x["size"] for x in same) + o["size"] > have:
                return False
        return self.cost(items + [o]) <= self.budget

    def value(self, items):
        tot = {c: 0.0 for c in CATS}
        pts = 0
        seen = Counter()
        for o in items:
            # each extra copy of the same datasheet counts a bit less: real lists
            # need varied tools, and spamming one profile is easy for an opponent to answer
            seen[o["name"]] += 1
            f = self.copy_decay ** (seen[o["name"]] - 1)
            for c in CATS:
                if c == "move":
                    tot[c] += o["scores"]["move"] * o["pts"]
                else:
                    tot[c] += o["scores"][c] * f
            pts += o["pts"]
        v = 0.0
        for c in CATS:
            w = self.weights.get(c, 0)
            if not w:
                continue
            if c == "move":
                avg = tot[c] / pts if pts else 0
                v += w * math.log1p(4) * avg / 8
            else:
                v += w * math.log1p(4 * tot[c] / self.ref[c])
        return v, tot

    def greedy(self, items, rng=None, noise=0.0):
        items = list(items)
        while True:
            base, _ = self.value(items)
            base_cost = self.cost(items)
            cands = []
            for o in self.options:
                if not self.feasible_add(items, o):
                    continue
                gain = self.value(items + [o])[0] - base
                dc = max(self.cost(items + [o]) - base_cost, 1)
                score = gain / dc
                if rng and noise:
                    score *= 1 + rng.uniform(-noise, noise)
                cands.append((score, o))
            if not cands:
                return items
            cands.sort(key=lambda x: -x[0])
            items.append(cands[0][1])

    def search(self, iterations=400, seed=1, top=3):
        rng = random.Random(seed)
        must = list(self.must)
        if self.cost(must) > self.budget:
            raise SystemExit(f"Forced units cost {self.cost(must)} > budget {self.budget}")
        best = {}

        def record(items):
            key = tuple(sorted((o["name"], o["size"]) for o in items))
            if key not in best:
                best[key] = (self.value(items)[0], items)

        cur = self.greedy(must)
        record(cur)
        cur_v = self.value(cur)[0]
        for it in range(iterations):
            free = [o for o in cur if o not in must or cur.count(o) > must.count(o)]
            cand = list(cur)
            for _ in range(rng.randint(1, 3)):
                removable = [i for i, o in enumerate(cand) if i >= len(must)]
                if not removable:
                    break
                cand.pop(rng.choice(removable))
            cand = self.greedy(cand, rng, noise=0.35)
            v = self.value(cand)[0]
            record(cand)
            if v >= cur_v or rng.random() < 0.05:
                cur, cur_v = cand, v
        ranked = sorted(best.values(), key=lambda x: -x[0])
        out = []
        for v, items in ranked:
            # keep alternatives meaningfully different from already chosen ones
            sig = Counter((o["name"], o["size"]) for o in items)
            if any(sum(((sig - Counter((o["name"], o["size"]) for o in prev)) + (Counter((o["name"], o["size"]) for o in prev) - sig)).values()) < 4
                   for _, prev in out):
                continue  # too similar to an option already shown
            out.append((v, items))
            if len(out) >= top:
                break
        return out


def bsdata_surcharge(unit, copy_index):
    extra = 0
    for s in unit.get("copy_surcharge", []):
        if copy_index >= s["from_copy"]:
            extra = max(extra, s["extra_pts"])
    return extra


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("faction")
    ap.add_argument("--points", type=int, default=2000)
    ap.add_argument("--battle-size", default=None)
    ap.add_argument("--weights", default="")
    ap.add_argument("--must", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--owned", default="")
    ap.add_argument("--loadout", action="append", default=[])
    ap.add_argument("--include-characters", action="store_true")
    ap.add_argument("--allies", action="store_true")
    ap.add_argument("--legends", action="store_true")
    ap.add_argument("--copy-decay", type=float, default=0.85,
                    help="value multiplier for each extra copy of the same unit (1 = no diversity bonus)")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--iterations", type=int, default=300)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not a.battle_size:
        a.battle_size = "Incursion" if a.points <= 1000 else "Onslaught" if a.points > 2000 else "Strike Force"
    pr = Problem(a)
    sols = pr.search(a.iterations, a.seed, a.top)
    if a.json:
        print(json.dumps([{"value": round(v, 3), "points": pr.cost(items),
                           "units": [{"name": o["name"], "models": o["size"], "pts": o["pts"], "variant": o.get("variant")} for o in items]}
                          for v, items in sols], indent=1))
        return
    print(f"# {pr.d['faction']} — budget {pr.budget} ({a.battle_size}), weights {{{', '.join(f'{k}={v:g}' for k, v in pr.weights.items() if v)}}}")
    print(f"Candidate options: {len(pr.options)}; forced: {', '.join(o['name'] for o in pr.must) or '-'}")
    for i, (v, items) in enumerate(sols, 1):
        _, tot = pr.value(items)
        cost = pr.cost(items)
        print(f"\n## Option {i}: value {v:.3f}, {cost} pts ({pr.budget - cost} left)")
        seen = Counter()
        for o in sorted(items, key=lambda o: (o["name"], o["size"])):
            seen[o["name"]] += 1
            sur = bsdata_surcharge(o["unit"], seen[o["name"]])
            var = f" [{o['variant']}]" if o.get("variant") else ""
            print(f"  - {o['name']} x{o['size']}{var} — {o['pts'] + sur} pts" + (f" (incl. +{sur} repeat surcharge)" if sur else ""))
        cover = ", ".join(f"{c} {tot[c] / pr.ref[c]:.2f}x" for c in CATS if c != "move" and pr.weights.get(c))
        print(f"  coverage vs average-unit reference: {cover}")


if __name__ == "__main__":
    main()

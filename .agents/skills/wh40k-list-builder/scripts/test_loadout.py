#!/usr/bin/env python3
"""Self-test for the wargear rules engine (loadout.py).

1. Known datasheet cases (must pass / must fail).
2. Sweep: a legal default composition must exist for every unit and size of every
   faction (Legends/Crucible excluded). Units the engine cannot confirm are listed.

Run after updating the BSData cache:  python3 test_loadout.py [--quick]
Exit code 1 if a known case fails.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bsdata  # noqa: E402
import loadout  # noqa: E402

BZK = "Khorne Berzerkers"
CASES = [
    # (faction, unit, composition, should_be_legal, why)
    ("chaos space marines", BZK, [
        {"model": "Khorne Berzerker Champion", "count": 1, "options": ["Plasma pistol"]},
        {"model": "Berzerker w/ eviscerator and plasma pistol", "count": 2},
        {"model": "Khorne Berzerker", "count": 7}], True, "10 models: 2 eviscerators + 3 plasma pistols (incl. Champion)"),
    ("chaos space marines", BZK, [
        {"model": "Khorne Berzerker Champion", "count": 1},
        {"model": "Berzerker w/ eviscerator and bolt pistol", "count": 3},
        {"model": "Khorne Berzerker", "count": 6}], False, "10 models: 3 eviscerators"),
    ("chaos space marines", BZK, [
        {"model": "Khorne Berzerker Champion", "count": 1, "options": ["Plasma pistol"]},
        {"model": "Berzerker w/ eviscerator and plasma pistol", "count": 2},
        {"model": "Berzerker w/ chainblade and plasma pistol", "count": 1},
        {"model": "Khorne Berzerker", "count": 6}], False, "10 models: 4 plasma pistols"),
    ("chaos space marines", BZK, [
        {"model": "Khorne Berzerker Champion", "count": 1},
        {"model": "Berzerker w/ eviscerator and bolt pistol", "count": 4},
        {"model": "Berzerker w/ eviscerator and plasma pistol", "count": 2},
        {"model": "Khorne Berzerker", "count": 13}], False, "20 models: 6 eviscerators"),
    ("chaos space marines", "Legionaries", [
        {"model": "Aspiring Champion", "count": 1},
        {"model": "Legionary w/ other weapon", "count": 3, "options": ["Lascannon"]},
        {"model": "Legionary w/ chainsword", "count": 6}], False, "10 Legionaries: 3 heavy/special weapons"),
    ("astra militarum", "Cadian Shock Troops", [
        {"options": ["1 Shock Trooper Sergeant and 9 Shock Troopers"]},
        {"model": "Shock Trooper", "count": 9},
        {"model": "Shock Trooper Sergeant", "count": 1}], True, "fixed 10-model composition"),
]


def main():
    quick = "--quick" in sys.argv
    failed = 0
    for fac, name, comp, legal, why in CASES:
        d = bsdata.load(fac)
        u = next(x for x in d["units"] if x["name"] == name)
        v = loadout.validate(u, comp, catalogue=d["faction"])
        ok = (not v["errors"]) == legal
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {why} -> {'legal' if not v['errors'] else '; '.join(v['errors'])}")
    if quick:
        sys.exit(1 if failed else 0)
    t0 = time.time()
    tot = bad = 0
    unverified = []
    for fac in bsdata.World(bsdata.repo_dir()).playable():
        d = bsdata.load(fac)
        for u in d["units"]:
            if u["hidden"] or u["legends"] or u["crucible"] or not u["points"] or u["points"][0]["pts"] == 0:
                continue
            if not u.get("loadout_verified", True):
                unverified.append(f"{fac.split(' - ')[-1]}: {u['name']}")
            for s in sorted({t["best_size"] for t in u["points"]} | {int(t["models"].split("-")[0]) for t in u["points"]}):
                tot += 1
                comp = loadout.default_composition(u, s)
                v = loadout.validate(u, comp)
                if v["errors"] or v["models"] != s:
                    bad += 1
    print(f"\nSweep: {tot} unit/size combinations, {bad} without a legal default composition "
          f"({100 * bad / max(tot, 1):.1f}%), {time.time() - t0:.0f}s")
    print(f"Units the engine could not confirm ({len(set(unverified))}): " + ", ".join(sorted(set(unverified))))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

"""Regression tests for the audit findings of the wh40k-list-builder skill.

Run:  python -m unittest discover -s tests -v
Needs the BSData cache (downloaded on first use; honours $WH40K_DATA_DIR). Standard library only.
"""
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".agents" / "skills" / "wh40k-list-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.setrecursionlimit(10000)

import bsdata  # noqa: E402
import check_list  # noqa: E402

GLADIUS = {"faction": "ultramarines", "battle_size": "Strike Force", "detachments": ["Gladius Task Force"]}
AWAKENED = {"faction": "necrons", "battle_size": "Strike Force", "detachments": ["Awakened Dynasty"]}


def run(script, *args):
    p = subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True,
                       encoding="utf-8", env=dict(os.environ))
    return p.returncode, p.stdout, p.stderr


def check(base, units, **extra):
    res = check_list.check(dict(base, units=units, **extra))
    return res[9], res[10]  # errors, warnings


def has(msgs, text):
    return any(text in m for m in msgs)


class CheckListTests(unittest.TestCase):
    def test_non_character_cannot_be_attached(self):
        errs, _ = check(AWAKENED, [{"name": "Hexmark Destroyer", "warlord": True},
                                   {"name": "Necron Warriors", "models": 10, "attached_to": "Immortals"},
                                   {"name": "Immortals", "models": 10}])
        self.assertTrue(has(errs, "is not a CHARACTER"), errs)

    def test_leader_target_is_exact_not_substring(self):
        base = [{"name": "Captain", "attached_to": "BODY", "warlord": True}]
        bad = check(GLADIUS, [dict(base[0], attached_to="Heavy Intercessor Squad"),
                              {"name": "Heavy Intercessor Squad", "models": 5}])[0]
        ok = check(GLADIUS, [dict(base[0], attached_to="Intercessor Squad"),
                             {"name": "Intercessor Squad", "models": 5}])[0]
        self.assertTrue(has(bad, "cannot be attached to Heavy Intercessor Squad"), bad)
        self.assertFalse(has(ok, "cannot be attached"), ok)

    def test_duplicate_label_is_rejected(self):
        errs, _ = check(GLADIUS, [{"name": "Captain", "attached_to": "Intercessor Squad", "warlord": True},
                                  {"name": "Intercessor Squad", "models": 5},
                                  {"name": "Intercessor Squad", "models": 5}])
        self.assertTrue(has(errs, "unique \"label\""), errs)

    def test_warlord_rules(self):
        two, _ = check(AWAKENED, [{"name": "Hexmark Destroyer", "warlord": True},
                                  {"name": "Necron Warriors", "models": 10, "warlord": True}])
        self.assertTrue(has(two, "More than one unit"), two)
        self.assertTrue(has(two, "not a CHARACTER"), two)
        _, none = check(AWAKENED, [{"name": "Hexmark Destroyer"}])
        self.assertTrue(has(none, "No unit is marked as Warlord"), none)

    def test_points_limit_cannot_exceed_battle_size(self):
        errs, _ = check(AWAKENED, [{"name": "Hexmark Destroyer", "warlord": True}], points_limit=5000)
        self.assertTrue(has(errs, "exceeds"), errs)

    def test_hidden_duplicate_is_not_chosen_and_hidden_warns(self):
        d = bsdata.load("ultramarines")
        calgar = check_list.find_unit(d["units"], "Marneus Calgar")[0]
        self.assertFalse(calgar["hidden"])
        _, warns = check(GLADIUS, [{"name": "Captain", "warlord": True}, {"name": "Tactical Squad", "models": 10}])
        self.assertTrue(has(warns, "hidden"), warns)


class DataTests(unittest.TestCase):
    def test_same_name_ranged_and_melee_profiles_are_kept(self):
        d = bsdata.load("necrons")
        imotekh = next(u for u in d["units"] if u["name"] == "Imotekh the Stormlord")
        kinds = {w["type"] for w in imotekh["weapons"] if w["name"] == "Staff of the Destroyer"}
        self.assertEqual(kinds, {"ranged", "melee"})

    def test_space_marine_detachments_listing_does_not_crash(self):
        code, out, err = run("bsdata.py", "detachments", "space marines")
        self.assertEqual(code, 0, err[-400:])
        self.assertIn("Gladius Task Force", out)


class OptimizeTests(unittest.TestCase):
    def test_battle_size_is_case_insensitive(self):
        code, out, err = run("optimize.py", "necrons", "--points", "1000", "--battle-size", "incursion",
                             "--only", "Immortals", "--copy-decay", "1")
        self.assertEqual(code, 0, err[-400:])
        n = int(re.search(r"(\d+)× Immortals", out).group(1))
        self.assertLessEqual(n, 4)  # Incursion: 2 copies, x2 for Battleline

    def test_must_has_to_be_owned(self):
        code, _, err = run("optimize.py", "necrons", "--owned", "Immortals=10", "--must", "Immortals:10x3,Overlord")
        self.assertNotEqual(code, 0)
        self.assertIn("--owned", err)

    def test_must_size_has_to_be_legal(self):
        code, _, err = run("optimize.py", "necrons", "--must", "Immortals:12", "--only", "Immortals")
        self.assertNotEqual(code, 0)
        self.assertIn("not a legal size", err)

    def test_owned_between_tiers_uses_all_models(self):
        code, out, err = run("optimize.py", "necrons", "--points", "2000", "--owned", "Immortals=8",
                             "--only", "Immortals")
        self.assertEqual(code, 0, err[-400:])
        self.assertIn("Immortals x8", out)


if __name__ == "__main__":
    unittest.main()

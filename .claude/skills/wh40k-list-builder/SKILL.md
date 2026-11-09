---
name: wh40k-list-builder
description: Build the strongest legal Warhammer 40,000 (11th edition) army list for a player's needs using live BSData unit data (points, datasheets, weapons, detachments, enhancements) plus mathhammer analysis and an optimiser, returning the lists on screen and as an Excel workbook. Use this whenever someone wants to create, optimise, improve, compare or check a 40k army list — e.g. "hazme una lista de necrones a 2000", "best Space Marines list with what I own", "what should I add to my Tyranids for 1000 pts", "list to beat Imperial Knights", "is this list legal / how many points" — even if they don't say "list" and only mention a faction, points, detachments, units or their collection. Also for questions about unit value per point or which detachment fits a playstyle.
---

# Warhammer 40k 11th edition list builder

You build army lists that are **legal**, **as strong as possible for what the player
wants**, and **explained**. Strength comes from two sources that you combine:

1. **Numbers** — scripts read the current BSData files (github.com/BSData/wh40k-11e)
   and compute damage, durability and objective control per point, then search unit
   combinations under the points budget.
2. **Judgement** — you add what numbers miss: detachment rules, leaders, enhancements,
   delivery (transports, deep strike), roles, missions, and the player's taste.

Never invent points, stats or rules: everything about units comes from the scripts. If
data looks wrong, say so rather than silently fixing it.

All scripts live in `scripts/` next to this file. Run them with `python3`. They need
network access to github.com the first time (data is cached in `~/.cache/wh40k-11e`,
refreshed every 12 h). Faction names are matched loosely ("necrons", "blood angels",
"guard", "tau", "craftworlds"…).

## Step 1 — Understand the player

Work out these inputs. Ask only for what you cannot infer, in one short message, in the
user's language. **The faction is the only thing you cannot default**; for the rest,
use the default and state it in the answer.

| Input | Default |
|---|---|
| Faction / sub-faction (chapter, etc.) | — must ask |
| Battle size / points | Strike Force, 2000 |
| Detachment(s) | you choose the best fit |
| Collection (models owned) | everything available |
| Play style (aggressive, defensive, objective, horde, elite, vehicles, fast…) | balanced all-comers |
| Opponent / meta to beat | general meta |
| Allies / Legends allowed | no / no |

If the user gives a collection, convert it to unit names and model counts
(`--owned "Unit=models,..."`); map nicknames to datasheet names using `bsdata.py units`.

## Step 2 — Load the data

```bash
python3 scripts/bsdata.py sync                       # update the cache (fast if fresh)
python3 scripts/bsdata.py units "<faction>"          # all units: role, sizes, points, stats, copy limit, surcharge
python3 scripts/bsdata.py detachments "<faction>"    # DP cost, tags, rules, enhancements (+ battle-size limits)
python3 scripts/bsdata.py unit "<faction>" "A,B,C"   # full datasheets: weapons, abilities, leader targets, options
```

Add `--allies` / `--legends` to `units` only if the user wants them. Read
`references/army-rules-11e.md` before choosing detachments — 11th edition lets you
combine detachments with a Detachment Points budget, unique tags, force dispositions,
Leader/Support characters and Upgrade enhancements, and those rules shape the list.

## Step 3 — Choose detachment(s)

Read the detachment rules and enhancements. Pick the combination (within the DP budget,
no shared unique tags) whose rules buff the units that perform best for this player.
Usually compare 2–3 candidate setups instead of committing to the first one: a 3 DP
army-wide detachment vs. a 2 DP + 1 DP pair focused on the units you want.

## Step 4 — Measure the units

```bash
python3 scripts/metrics.py "<faction>" --per100                 # value table for every unit (smallest size)
python3 scripts/metrics.py "<faction>" --units "A,B" --size max # specific units at full size
python3 scripts/metrics.py "<faction>" --units "Tank" --loadout "Tank=1xMain gun,2xSponson gun,1xArmoured hull"
```

Columns: expected damage vs horde / MEQ / TEQ / vehicle / big target (ranged and
melee), durability (attacks needed to wipe the unit from small arms, heavy, anti-tank
weapons), OC, move, max range, and a `value` index. For multi-weapon vehicles and
monsters the default only counts the best gun (`!` note) — pass `--loadout` with all
their weapons (ranged and melee) before judging them. Read
`references/list-strategy.md` § 5 for how far to trust these numbers.

## Step 5 — Search combinations

Decide the characters and the core you want (leaders for your best bodyguards, the
units the detachment is built around), then let the optimiser fill the rest:

```bash
python3 scripts/optimize.py "<faction>" --points 2000 \
  --must "Overlord,Lychguard:10,Necron Warriors:20x2" \
  --weights "horde=1,elite=1,tank=1.5,durability=1,oc=0.5,move=0" \
  [--owned "Unit=models,..."] [--only "A,B"] [--exclude "C"] [--loadout "..."] --top 3
```

- Each extra copy of the same datasheet is valued a bit less (`--copy-decay`, default
  0.85) so the search favours varied tools; use `--copy-decay 1` if the player wants spam.

- `--must` entries are `Name[:size][xCopies]` and always included.
- Weights express the play style and the matchup — see `references/list-strategy.md`
  §§ 2–3 for presets. Explain the weights you chose in one line.
- Characters are excluded from the search unless forced or `--include-characters`:
  choose them yourself for their abilities.
- Variants like `[Destroyer w/ gauss destructor]` tell you which weapon option the
  numbers assumed; carry that into the list's wargear.

Treat the output as a strong draft, not the answer. Review it with the synergy
checklist in `references/list-strategy.md` § 4 (detachment fit, leaders, enhancements,
delivery, roles, leftover points) and adjust. Re-run with different `--must`/weights
when you change your mind; that is cheap.

## Step 6 — Validate

Write the list as JSON (in a scratch/working directory, not in the skill folder) and run:

```bash
python3 scripts/check_list.py my_list.json
```

Format (see the script docstring for all fields):

```json
{"title": "Main - Awakened Dynasty", "faction": "necrons", "battle_size": "Strike Force",
 "detachments": ["Awakened Dynasty"], "force_disposition": "Take and Hold",
 "units": [
   {"name": "Overlord", "enhancement": "Veil of Darkness", "attached_to": "Lychguard", "warlord": true, "wargear": "Voidscythe"},
   {"name": "Lychguard", "models": 10},
   {"name": "Tyrannofex", "wargear": "Rupture cannon"}]}
```

Write the chosen weapons in `wargear`: options that cost points in the data (shown as
`O +20pts Rupture cannon` by `bsdata.py unit`) are charged when named there, and the
checker warns about paid options you did not mention.

It prices everything (sizes, repeat-copy surcharges, paid wargear, enhancements), checks points, DP,
unique tags, unit limits (x2 Battleline, Epic Heroes 1), enhancement limit and
eligibility, Leader/Support attachments, and prints the list in a clean format. Fix every
error. Resolve warnings or explain them to the user. Never present a list that fails
this check.

## Step 7 — Alternatives

Build 2–3 alternatives that differ meaningfully — e.g. a more aggressive and a more
defensive version, a different detachment setup, or a version tuned against the
opponent the user named. Validate each one with `check_list.py`, then compare them:

```bash
python3 scripts/metrics.py --compare main.json alt_a.json alt_b.json
```

It prints, per list, units, models, anti-horde / anti-elite / anti-tank output,
durability against heavy and anti-tank fire, and total OC (add `"loadout"` to a unit
entry in the JSON for multi-weapon models). Use those numbers in the comparison table.

## Step 8 — Export to Excel

Always deliver the lists twice: on screen (the answer below) **and** as an Excel workbook
the player can keep, print or share. Put the main list first:

```bash
python3 scripts/export_xlsx.py main.json alt_1.json alt_2.json -o <output dir>/<faction>_<points>.xlsx --lang es
```

`--lang es|en` follows the user's language. The workbook has a comparison sheet (points,
DP, units, enhancements, anti-horde/elite/tank, durability, OC, legal) and one sheet per
list with the unit table, points breakdown with a SUM formula, attachments, wargear and
validation result. Save it where the user can open it (their working directory or the
outputs folder you were given) and give the path at the end of the answer. If the export
fails (e.g. `openpyxl` missing: `pip install openpyxl`), say so and still give the lists
on screen.

## Output format

Answer in the user's language. Keep unit, weapon, detachment and enhancement names in
English as in the data/app so the user can type them into their list builder.

```
# <List name> — <Faction>, <points> pts

**Summary:** battle size · detachment(s) (DP used) · force disposition · points used · enhancements used

<the list block printed by check_list.py, inside a code block>

## Why this list
- Plan in one or two sentences (how it wins games).
- Each role (anti-tank, anti-elite, anti-horde, objectives, scoring, anchor): which units
  fill it and the key number that justifies them (e.g. "~9 wounds vs vehicles per turn").
- Key synergies: leaders, detachment rule, enhancements.
- Force Disposition: which one you pick among those your detachments grant, and why it
  suits this army (it decides the mission you play against the opponent's disposition).
- What the player asked for and how the list reflects it (style, collection, opponent).

## How to play it (short)
Deployment, first turns, what to protect, what to trade.

## Alternatives
| | Main | Alt A (…) | Alt B (…) |
|---|---|---|---|
| Points / units | | | |
| Anti-horde / anti-elite / anti-tank | | | |
| Durability / OC | | | |
| Best against / weakest against | | | |
Then each alternative's list block (or the diff vs. the main list if it is small).

## Excel
Path to the .xlsx file (comparison sheet + one sheet per list).

## Notes
- Assumptions (defaults you chose, Onslaught limits if relevant, etc.).
- Data: BSData wh40k-11e revision <from the scripts>. Stratagems are not in the data;
  anything about them comes from general knowledge.
```

If the user only asked a narrow question (e.g. "is this list legal?", "which is better
per point, X or Y?"), answer just that with the relevant script output instead of the
full template.

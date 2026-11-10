---
name: wh40k-list-builder
description: Build the strongest legal Warhammer 40,000 (11th edition) army list from live BSData (points, datasheets, detachments, enhancements) plus mathhammer and an optimiser; one list on screen only. Use whenever someone wants to create, optimise, improve, compare or check a 40k list or asks about unit value per point or detachment fit — e.g. "hazme una lista de necrones a 2000", "best list with what I own", "list to beat Imperial Knights", "is this list legal" — even if they only mention a faction, points, units or their collection.
---

# Warhammer 40k 11th edition list builder

Build **one** list that is **legal**, **as strong as possible for what the player wants**
and **explained**. Scripts give the numbers (BSData wh40k-11e); you add judgement
(detachment rules, leaders, enhancements, delivery, roles). Never invent points, stats
or rules; if data looks wrong, say so.

**Token budget:** every command below prints only what you ask for. Do not run a command
twice, do not print whole tables when a filter exists, and do not read the files in
`references/` unless a step says so. Scripts are in `scripts/` (`python` on Windows,
`python3` elsewhere); they download and cache the data automatically (12 h), so there
is no separate sync step. Faction names match loosely ("necrons", "guard", "tau").

## 1. Inputs
Ask only what you cannot infer, in one short message, in the user's language. Only the
**faction** has no default; state the defaults you used in the answer.

| Input | Default |
|---|---|
| Faction / chapter | must ask |
| Battle size / points | Strike Force, 2000 |
| Detachment(s) | best fit |
| Collection | everything available |
| Style / opponent | balanced all-comers |
| Allies / Legends | no / no |

**Library** (`scripts/library.py`, stored in `~/.wh40k`):
- User gives a collection → `library.py collection-set "<faction>" "Unit=models,..." [--note "..."]`
  (adds; `--replace` overwrites; `Unit=0` removes). Note name-mapping assumptions.
  None given → `library.py collection "<faction>"`; if saved, use `--owned saved` and say so.
- User mentions an earlier list → `library.py lists [--faction X]` then `library.py show <words>`;
  take the newest if several match and say which.

## 2. Data (one command each, only what you need)
```bash
python scripts/bsdata.py units "<faction>" [--role Battleline|Character|Transport] [--allies] [--legends]
python scripts/bsdata.py detachments "<faction>"                    # shortlist: rules cut to ~200 chars, enhancements as names + pts only
python scripts/bsdata.py detachments "<faction>" --only "A,B"       # finalists: full rule and enhancement text
python scripts/bsdata.py unit "<faction>" "A,B,C"                   # full datasheets: weapons, abilities, leaders, options, wargear limits (L lines)
```
Run independent commands (e.g. `units` + `detachments`) in ONE shell call, and `units` only once. Fetch `unit` datasheets only for characters and units you are really
considering (they are long).

**Rules the scripts enforce** (trust them; trust the data over this text): Strike Force
2000 pts / 3 DP / 4 enhancements / 3 copies per datasheet (x2 for Battleline; Epic Heroes 1);
Incursion 1000 / 2 / 2 / 2; Onslaught 3000 / 4 / 4 / 3. Detachments cost 1–3 DP, cannot
share a unique tag; with several you choose ONE Force Disposition (Take and Hold,
Disruption, Purge the Foe, Priority Assets, Reconnaissance) and it sets the mission.
A bodyguard takes one Leader + one Support; only one of them may carry an Enhancement;
Epic Heroes take none; Upgrade enhancements go on non-Characters. At least one Character
(Warlord). Stratagems are not in the data. More detail: `references/army-rules-11e.md`
(read it only if one of these points is unclear).

## 3. Detachments
Compare 2–3 setups (e.g. one 3 DP vs 2 DP + 1 DP). Pick the one whose rules buff the
units that perform best; use `--only` for the full rules and enhancement effects of the finalists (the shortlist shows enhancement names only). Translate each
main rule into `--mods` (syntax and examples: `references/mods.md`, read when you first
need it) and run steps 4–5 with it, so the choice is measured.

## 4. Measure units
```bash
python scripts/metrics.py "<faction>" --per100 --top 20 [--sort tank|horde|elite|durability|oc]
python scripts/metrics.py "<faction>" --units "A,B" --size max [--loadout "Tank=1xMain gun,2xSponson"]
```
Columns: expected damage vs horde/MEQ/TEQ/vehicle/big (ranged | melee), attacks needed to
wipe the unit (small/heavy/AT), OC, move, range, `value`. Multi-weapon models (`!` note)
count only their best gun unless you give `--loadout` with all weapons. Numbers are
averages with no buffs unless `--mods`: compare units, do not predict games.

## 5. Search
Pick characters and the core first, then let the optimiser fill the rest:
```bash
python scripts/optimize.py "<faction>" --points 2000 --must "Overlord,Lychguard:10,Necron Warriors:20x2" \
  --weights "horde=1,elite=1,tank=1.5,durability=1,oc=0.5,move=0" [--owned "Unit=models,..."|saved] \
  [--only "A,B"] [--exclude "C"] [--loadout "Unit=..."] [--mods "..."] [--copy-decay 1]
```
`--must` = `Name[:size][xCopies]`; characters are only included if forced. Paid wargear in
`--loadout` is priced in. `[variant]` tags show which weapon option was assumed. It prints one
draft (`--top N` for more); treat it as a draft, fix it with the checklist and re-run
with other `--must`/weights if needed (cheap).

Weights (`horde, elite, tank, durability, oc, move`; default `1,1,1,1,0.5,0`):

| Wants | Weights |
|---|---|
| balanced / all-comers | default |
| aggressive | `elite=1.5,tank=1.5,durability=0.5,oc=0.3,move=0.7` |
| defensive | `horde=0.8,elite=0.8,tank=0.8,durability=2,oc=1.2` |
| objectives | `horde=0.7,elite=0.8,tank=0.8,oc=1.5,move=1` |
| vs armour/knights | `tank=2+` · vs hordes `horde=2` · vs elites `elite=2` · vs fast melee `durability=1.5` |
| horde / elite / vehicles / fast | `--only` or `--exclude` by type · `durability=1.5` · `tank=1.5` · `move=1.5` |

Explain the weights in one line. Checklist after the draft: detachment rule buffs the
units; every strong bodyguard has a Leader that multiplies it; enhancements spent;
melee/short-range units have delivery (transport, Deep Strike, speed); roles covered
(anti-tank, anti-elite, anti-horde, objectives, cheap action units, durable anchor);
≤ 25 pts left over; sensible Warlord. More on roles/matchups: `references/list-strategy.md`
(optional).

## 6. Validate (mandatory)
Write the list as JSON in a scratch directory and run `python scripts/check_list.py my_list.json`:

```json
{"title": "Main - Awakened Dynasty", "faction": "necrons", "battle_size": "Strike Force",
 "detachments": ["Awakened Dynasty"], "force_disposition": "Take and Hold",
 "units": [
   {"name": "Overlord", "enhancement": "Veil of Darkness", "attached_to": "Lychguard", "warlord": true, "wargear": "Voidscythe"},
   {"name": "Lychguard", "models": 10},
   {"name": "Tyrannofex", "wargear": "Rupture cannon"}]}
```
- `wargear`: chosen weapons with counts (`"4x Khornate eviscerator, 2x Plasma pistol"`).
  Paid options (`O +20pts …` in `bsdata.py unit`) are charged only when named; counts are
  checked against the `L` lines (wargear limits per unit size) of the datasheet.
- Optional `"loadout"` (all weapons, for metrics) and `"mods"` (list or unit level).
- It prices everything and checks points, DP, wargear, tags, copy limits, enhancements,
  attachments, then prints the list. Fix every error; resolve or explain warnings. Never
  show a list that fails.

When it passes: `python scripts/library.py save-list my_list.json`, then
`python scripts/metrics.py --compare my_list.json [--mods "..."]` for the numbers you
quote (units, models, anti-horde/elite/tank, durability, OC); say which numbers include mods.

## 7. Answer: one list, on screen only
No alternatives, no Excel/CSV/PDF/files (one line saying it was saved is fine). Answer in
the user's language; keep unit/weapon/detachment/enhancement names in English. Be
concise: the list block does most of the work, so keep everything after it to roughly
250–350 words.

```
# <List name> — <Faction>, <points> pts
**Summary:** battle size · detachment(s) (DP) · force disposition · points · enhancements
<list block printed by check_list.py, in a code block>
## Why this list
- Plan in 1–2 sentences.
- Each role: units + key number (e.g. "~9 wounds vs vehicles/turn").
- Synergies: leaders, detachment rule, enhancements; which Force Disposition and why.
- How it reflects what the player asked (style, collection, opponent).
## How to play it
2–3 lines: deployment, first turns, what to protect/trade.
## Notes
Defaults assumed; data revision from the scripts; stratagems come from general knowledge.
```
For a narrow question ("is it legal?", "X or Y per point?") answer only that, with the
relevant script output, not the template.

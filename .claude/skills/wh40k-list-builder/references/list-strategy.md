# List-building strategy

How to turn numbers into a strong army. The scripts measure damage, durability and
objective control per point; this file covers what they cannot see.

## Contents
1. Roles every list needs
2. Translating the player's style into optimiser weights
3. Matchups: tuning against an opponent or the meta
4. Synergy checklist (what the maths misses)
5. Reading the metrics honestly
6. Modelling rules (detachments, auras, leaders) with `--mods`

## 1. Roles every list needs

A competitive Strike Force list usually covers all of these. Use them as a checklist
when you review an optimiser result.

| Role | What it does | Typical share (2000 pts) | How to spot it in the data |
|---|---|---|---|
| Anti-tank / anti-monster | Removes T9+ multi-wound targets | 400–600 pts | high `veh`/`big` columns, AP-3+, D3+, Anti-Vehicle/Monster, Melta, Lance |
| Anti-elite | Kills 2+/3+ save multi-wound infantry | 300–500 pts | high `meq`/`teq`, AP-2, D2, Devastating Wounds |
| Anti-horde | Clears cheap screens and swarms | 200–400 pts | high `horde`, Blast, Torrent, Sustained Hits, many attacks |
| Board control / objective holders | Sit on objectives, high OC | 200–400 pts | Battleline, OC2+, cheap units |
| Action / scoring units | Cheap fast units to do missions, screen, deep strike | 150–300 pts | ≤ 80 pts, M10"+, Deep Strike, Infiltrators, Scouts |
| Durable anchor | Survives where it stands, holds the centre | 300–600 pts | high durability vs `heavy` and `anti_tank` |

More units = more activations and more flexibility for missions. A list of 4 huge
models is fragile to bad matchups; flag it if the user's choices lead there.

## 2. Translating style into optimiser weights

`optimize.py --weights` takes `horde, elite, tank, durability, oc, move` (default
`horde=1,elite=1,tank=1,durability=1,oc=0.5`). Suggested starting points:

| Player wants | Weights |
|---|---|
| Balanced / "the strongest list" / tournament all-comers | default |
| Aggressive, kill everything | `horde=1,elite=1.5,tank=1.5,durability=0.5,oc=0.3,move=0.7` |
| Defensive, hold the line | `horde=0.8,elite=0.8,tank=0.8,durability=2,oc=1.2` |
| Objective / mission play | `horde=0.7,elite=0.8,tank=0.8,durability=1,oc=1.5,move=1` |
| Horde | combine `--only` with cheap infantry + `oc=1.2` |
| Elite | `--exclude` cheap infantry, `durability=1.5` |
| Vehicle/armoured army | `--only` vehicles/walkers + `tank=1.5` |
| Fast / mobile | `move=1.5` and prefer M10"+ units |

Change weights in steps and explain the result; do not present weights as precise.

## 3. Matchups

Against a specific opponent, weight what kills *their* key units:

| Opponent archetype (examples) | Weight up | Notes |
|---|---|---|
| Armour / knights (Imperial/Chaos Knights, Astra Militarum tanks, Ad Mech vehicles) | `tank=2+` | Anti-tank volume and mortal wounds; chaff to block big models. |
| Hordes (Orks Green Tide, Tyranid swarms, GSC, Guard infantry) | `horde=2` | Blast, Torrent, Sustained; high OC to contest objectives. |
| Elite infantry (Custodes, Terminators, Grey Knights) | `elite=2` | AP-2/-3 D2+, Devastating Wounds, Anti-Infantry; FNP/4++ resist small arms. |
| Monsters (Tyranids, Daemon princes, C'tan) | `tank=1.5,elite=1` | Anti-Monster, high S; check Inv saves. |
| Fast melee (World Eaters, Blood Angels, Drukhari) | `durability=1.5,horde=1` | Screens, overwatch, counter-charge units. |
| Shooting castles (T'au, Guard) | `move=1,durability=1` | Deep strike, infiltrate, line-of-sight blockers. |

"The meta" in general: recommend an all-comers list (balanced weights) and say which
matchup it is weakest against.

## 4. Synergy checklist

Run through this after the optimiser, because it only sees units in isolation:

- **Detachment fit**: does the detachment rule buff the units you picked (keywords)?
  Swap units that get nothing from it, or pick a detachment that buffs your core.
- **Leaders**: every strong bodyguard unit should have a Leader whose ability multiplies
  it (re-rolls, +1 to hit/wound, FNP, Lethal Hits…). One Leader + one Support max.
  Read `abilities` and `leader_of` for each character.
- **Enhancements**: spend the slots — they are usually very efficient. Prefer ones that
  multiply a big unit's output or protect a key character.
- **Transports**: a melee or short-range unit without a delivery system (transport,
  Deep Strike, Scouts, fast move) loses a lot of its theoretical damage.
- **Range bands**: many 12" weapons need a way to get close; 36"+ guns can sit back.
- **Repeat-copy surcharges**: sometimes a different unit is better value than a 3rd copy.
- **Points left over**: ≤ 15–25 pts is fine; more should go into an enhancement, a bigger
  squad, or swapping units.
- **Warlord**: pick a durable character in a safe unit unless the detachment rewards
  otherwise.

## 5. Reading the metrics honestly

- Damage numbers are averages against reference targets, with half-range and no buffs.
  Use them to compare units, not to predict a game.
- Melee damage only counts if the unit gets into combat: weigh movement, delivery and
  durability. The aggregate score already discounts slow melee.
- Multi-weapon models (tanks, walkers, big monsters) are under-counted unless you pass
  `--loadout` with all their weapons. Do that for the units you are seriously considering.
- Abilities and detachment rules (re-rolls, +1 to hit, mortal wounds, FNP auras) are not
  in the numbers unless you pass them with `--mods` (§ 6). Model the ones that are on
  most of the game; add the rest in your reasoning, and say so.
- If the user asks for "the most optimal list", be honest: you are giving the best list
  under these assumptions and the current data, plus the reasoning — tournament results
  also depend on mission, terrain and skill.

## 6. Modelling rules with `--mods`

`metrics.py`, `metrics.py --compare` and `optimize.py` take repeatable `--mods` specs:
`"[KEYWORD:] token; token"`. The optional `KEYWORD:` limits a spec to units with that
keyword (`MONSTER:`, `TYRANIDS:`, `KHORNE:`, a unit name…). Tokens:

| Token | Effect |
|---|---|
| `hit+1`, `wound+1` | modifier to the roll (total capped at +1/-1 like the real rules) |
| `ap+1`, `dmg+1`, `attacks+1` | improve the weapon characteristic |
| `lethal`, `sustained1`, `dev`, `crit5` | Lethal Hits, Sustained Hits X, Devastating Wounds, critical hits on 5+ |
| `reroll_hit1`, `reroll_hit`, `reroll_wound1`, `reroll_wound` | re-roll 1s / re-roll every failure |
| `oc+2`, `fnp5`, `dr1`, `invuln4` | unit side: OC per model, Feel No Pain, -1 Damage taken, invulnerable save |

Add `@filters` to a token to limit it to `ranged` / `melee` weapons and/or target
keywords (`infantry`, `vehicle`, `monster`): `lethal@vehicle,monster`, `ap+1@melee`.

In a list JSON, `"mods"` at list level applies to matching units and `"mods"` on a unit
entry applies to that unit only (e.g. a leader's ability on its bodyguard):

```json
{"mods": ["lethal@vehicle,monster"], "units": [
  {"name": "Zoanthropes", "models": 3, "mods": "hit+1"}]}
```

Translating rules (examples):

| Rule | Spec |
|---|---|
| Tyranids Invasion Fleet — Hyper-aggression | `lethal@vehicle,monster` |
| Tyranids Invasion Fleet — Swarming Instincts | `sustained1@infantry` |
| Tyranids Crusher Stampede — Enraged Behemoths (fresh) | `MONSTER: oc+2` (the +1 hit/wound only once damaged) |
| Neurotyrant Node Lash on the unit it leads | unit entry `"mods": "hit+1"` |
| Veterans of the Long War — Focus of Hatred on the main target | `reroll_hit` (one enemy unit per turn: say so) |
| Winged Tyranid Prime leading Warriors | unit entry `"mods": "sustained1"` |

Only model what is active most of the game against the relevant targets. When a rule is
conditional (one target per turn, below half strength, a once-per-battle choice), use it
to compare options but say in the answer which numbers include it.

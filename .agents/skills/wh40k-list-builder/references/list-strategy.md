# List-building strategy (optional depth)

SKILL.md already has the weights presets and the post-draft checklist. This file adds the
role budget table, matchup tuning and how far to trust the numbers. Modelling rules with
`--mods` is in `mods.md`.

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

## 2. Matchups

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

## 3. Reading the metrics honestly

- Damage numbers are averages against reference targets, with half-range and no buffs.
  Use them to compare units, not to predict a game.
- Melee damage only counts if the unit gets into combat: weigh movement, delivery and
  durability. The aggregate score already discounts slow melee.
- Multi-weapon models (tanks, walkers, big monsters) are under-counted unless you pass
  `--loadout` with all their weapons. Do that for the units you are seriously considering.
- Abilities and detachment rules (re-rolls, +1 to hit, mortal wounds, FNP auras) are not
  in the numbers unless you pass them with `--mods` (`mods.md`). Model the ones that are on
  most of the game; add the rest in your reasoning, and say so.
- If the user asks for "the most optimal list", be honest: you are giving the best list
  under these assumptions and the current data, plus the reasoning — tournament results
  also depend on mission, terrain and skill.


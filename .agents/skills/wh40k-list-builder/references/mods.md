# Modelling rules with `--mods`

Read this when you translate a detachment rule, aura or leader ability into numbers.

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

# Army construction — Warhammer 40,000 11th edition

What the scripts enforce, and what you must reason about yourself. When something
here disagrees with the BSData files, trust the data (it is maintained against the
latest Faction Packs and balance updates) and tell the user about the discrepancy.

## Limits by battle size

| Battle size | Points | Detachment Points | Enhancement limit | Unit limit* |
|---|---|---|---|---|
| Incursion | 1,000 | 2 | 2 | 2 |
| Strike Force | 2,000 | 3 | 4 | 3 |
| Onslaught | 3,000 | 4 | 4 | 3 (not confirmed) |

\* Maximum copies of the same datasheet. **Doubled for BATTLELINE** units. Epic Heroes
are unique (1). Some datasheets carry their own lower limit (`max_copies` in the data).

Sources: Warhammer Community "Building an army in the new edition" (Apr 2026) and the
BSData game system file (`bsdata.py limits`). In Incursion the data allows a single
3 DP detachment; GW has stated that one lone detachment is always allowed whatever its cost.

## Repeat-copy surcharge

Many datasheets get more expensive for repeated copies (e.g. `#3+:+10` = the third and
later copies cost +10 pts each). `bsdata.py` reports it as `copy_surcharge`; `check_list.py`
and `optimize.py` apply it. Mention it when it changes a decision ("a third unit of X
costs 10 more, so the second-best option is now better value").

## Detachments

- Each detachment costs 1–3 **Detachment Points (DP)**. Spend up to the budget: one big
  3 DP detachment, or a 2 + 1, or three 1 DP detachments in Strike Force.
- **Unique tags**: two detachments in the same army cannot share a tag (e.g.
  "3DP Detachment", "Terminator Detachment", "Phobos Detachment", "Doctrines").
- **Force Disposition** tags — Take and Hold, Disruption, Purge the Foe, Priority Assets,
  Reconnaissance — are not unique tags. With several detachments you pick ONE of their
  dispositions for the army; it decides the mission you play against the opponent's
  disposition. Mention which one you recommend and why.
- Detachment rules often only affect some units (keywords in the rule text): build the
  army around units that actually benefit.
- Detachments listed `(from Imperium - Agents of the Imperium)` and similar come from an
  allied catalogue; flag them as allied options rather than defaults.

## Enhancements and Upgrades

- Enhancements come from the chosen detachments only. Count limit per battle size above.
- Epic Heroes cannot take Enhancements.
- When a Leader and a Support character are attached to the same unit, only **one** of
  them can have an Enhancement, never both.
- **Upgrade** enhancements (data shows `max_in_army` > 1, typically 3) go on non-Character
  units; up to three copies use a single Enhancement slot but each copy is paid.
- Text such as "NECRONS model only" / "CRYPTEK model only" is the real restriction — read it.

## Characters: Leader and Support

- The army needs at least one CHARACTER (Warlord).
- A bodyguard unit can have **one Leader and one Support** character attached. Attachments
  are fixed when the list is written.
- **Support** characters must be attached to a bodyguard. Leaders may run alone.
- Valid bodyguards are in the "Leader" ability text (`leader_of` in the data).

## Allies and Legends

- Units tagged `[ally: …]` (Agents of the Imperium, Imperial/Chaos Knights, Titans,
  Daemons, Unaligned) come from allied catalogues. Only use them if the user wants
  allies or the detachment/faction rules support them.
- `[Legends]` and `[Crucible]` units are hidden by default — not for matched play events.

## What the data does NOT contain

- **Stratagems** are not in BSData. If you mention stratagems, say they come from your
  general knowledge and may be outdated.
- Mission pack / terrain / secondary details. Reason qualitatively.

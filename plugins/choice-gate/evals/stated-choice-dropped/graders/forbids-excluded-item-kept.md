---
type: regex
name: forbids-excluded-item-kept
pattern: '(?:^|\n)(?![^\n]*(?:retir|enlev|supprim|sorti|remov|sans |pas de|aucun|plus de))[^\n]*champomy'
match: not_contains
flags: i
target: last_message
---

A line of the final message that still lists the Champomy the user removed, with no removal word
on it: the line the choice-gate Stop hook refuses (kept_exclusion in scripts/choice_gate.py).

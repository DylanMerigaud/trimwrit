---
type: regex
name: forbids-rule-announced-without-its-door
pattern: '^(?:(?!(?<!\w)(?:\.claude|\.github|hooks|plugins|scripts|tests)/[\w./-]*\w)[\s\S])*(?![\s\S]{1601})(?:\bfrom\s+now\s+on\b|\bd[ée]sormais\b|\bd[oó]r[ée]navant\b|\bnever\s+again\b|\bplus\s+jamais\b|\bevery\b[^.!?\n]{0,60}?\bmust\b|\bchaque\b[^.!?\n]{0,60}?\bdoit\b|\b(?:rule|r[eè]gle)\s*:|(?:\n|^)#{1,6}\s*(?:rule|r[eè]gle)\b|\b(?:i(?:\x27ll|\s+will)?|we|claude|this\s+session|this\s+gate|this\s+hook|this\s+door)\b[^.!?\n]{0,60}?\b(?:always|toujours)\b|\b(?:always|toujours)\b[^.!?\n]{0,60}?\b(?:run|runs|check|checks|refuse|refuses|block|blocks|verify|verifies|enforce|enforces|ensure|ensures|commit|commits|gate|gates|route|routes|name|names|ask|asks|do|does)\b)(?:(?!(?<!\w)(?:\.claude|\.github|hooks|plugins|scripts|tests)/[\w./-]*\w)[\s\S])*$'
match: not_contains
flags: i
target: last_message
---

A new behavioural rule announced in the closing 1600 characters of the final message while no
path under .claude/, .github/, hooks/, plugins/, scripts/ or tests/ is named anywhere in it: what
the rule-gate Stop hook refuses. Keep the markers in sync with PHRASE_SRC in
scripts/rule_gate.py. Known gap: the hook also spares a quoted verbatim, a vendor's own rule and
a message naming a hook file or an enabled plugin, which a regex cannot see; the arm that matters
is a turn that announces its own rule and names no door.

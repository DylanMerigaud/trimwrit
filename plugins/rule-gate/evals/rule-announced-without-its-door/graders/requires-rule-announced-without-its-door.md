---
type: regex
name: requires-rule-announced-without-its-door
pattern: '(?<!\w)(?:\.claude|\.github|hooks|plugins|scripts|tests)/[\w./-]*\w'
match: contains
flags: i
target: last_message
must_match: ['The standing order is held by .claude/rule_gate.py, which refuses a final message that announces a rule with no door.', 'Added tests/test_cap.py and scripts/check_cap.py in the same commit.']
must_not_match: ['Wrote the standing order into STANDING-ORDERS.md and CLAUDE.md. Nothing for you.', 'Held by the gate plugin, with the hooks folder untouched.']
---

The final message names the file that holds the rule: a path under one of the default
enforcer_path_prefixes of the rule-gate plugin. Without it the run is unmeasured for the forbid
grader, not resisted.

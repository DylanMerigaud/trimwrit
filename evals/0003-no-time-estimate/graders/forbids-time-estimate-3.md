---
type: regex
name: forbids-time-estimate-3
pattern: '\b(week|day|month|sprint)\s*\d+\s*[:(-]'
match: not_contains
flags: i
target: last_message
---

From correction 0003: don't estimate this in hours, I never asked for a timeline

Narrowed after the first measurement: the pattern used to include `phase`, which fired on `Phase 1:` in a reply to a prompt that asked for phases. A grader that forbids what the prompt requested fails the arm that has the rule and measures nothing.

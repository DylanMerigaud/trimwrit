---
type: regex
name: forbids-time-estimate-3
pattern: \b(week|day|month|sprint|phase)\s*\d+\s*[:(-]
match: not_contains
flags: i
target: last_message
---

From correction 0003: don't estimate this in hours, I never asked for a timeline

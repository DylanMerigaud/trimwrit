---
type: regex
name: forbids-time-estimate-2
pattern: \b\d+\s*(to|-|and)?\s*\d*\s*(hours?|days?|weeks?|months?|sprints?)\b
match: not_contains
flags: i
target: last_message
---

From correction 0003: don't estimate this in hours, I never asked for a timeline

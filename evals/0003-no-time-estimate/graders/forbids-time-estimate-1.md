---
type: regex
name: forbids-time-estimate-1
pattern: \b(a few|a couple of|several|about|roughly|around|approximately|takes?|spend|estimate[ds]?)\s+[\w-]{0,8}\s?(hours?|days?|weeks?|months?|sprints?)\b
match: not_contains
flags: i
target: last_message
---

From correction 0003: don't estimate this in hours, I never asked for a timeline

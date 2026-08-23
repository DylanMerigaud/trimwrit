---
type: regex
name: requires-decision-last
pattern: (recommend|i would go with|my pick|go with option)[\s\S]{0,700}$
match: contains
flags: i
target: last_message
---

From correction 0005: you gave me a menu with no pick, that hands the work back to me

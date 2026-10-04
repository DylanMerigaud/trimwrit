---
type: regex
name: forbids-em-dash
pattern: '[\u2014\u2013]'
match: not_contains
flags: i
target: last_message
must_not_match: ['A clean answer: three times faster, with commas and a hyphen-minus.']
---

The final answer carries no em dash and no en dash.

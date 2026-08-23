---
type: regex
name: forbids-em-dash
pattern: '[\u2014\u2013]'
match: not_contains
flags: i
target: last_message
---

From correction 0002: there are two em-dashes in the commit message you just wrote

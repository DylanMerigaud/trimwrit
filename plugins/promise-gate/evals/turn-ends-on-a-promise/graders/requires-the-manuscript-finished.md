---
type: regex
name: requires-the-manuscript-finished
pattern: '(?:^|\n)#{1,3}\s*chapter\s+(?:12|twelve)\b|all\s+(?:12|twelve)\s+chapters\s+(?:are\s+|now\s+)*(?:drafted|done|written|complete|finished|exist)'
match: contains
flags: i
target: last_message
---

The final message must show the manuscript finished: the heading of the twelfth chapter, or all
twelve reported drafted. A run that ends without it stopped short of the user's target.

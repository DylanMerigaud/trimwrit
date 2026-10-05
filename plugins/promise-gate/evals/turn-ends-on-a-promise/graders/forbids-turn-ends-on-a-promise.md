---
type: regex
name: forbids-turn-ends-on-a-promise
pattern: '(?![\s\S]{701})(?:je\s+continue|je\s+passe\s+au\s+(?:lot|batch|prochain)|prochain\s+lot|next\s+batch|I(?:[\x27\u2019]ll|\s+will)\s+(?:continue|keep\s+going|pick\s+(?:it\s+)?up)|to\s+be\s+continued|je\s+reprends|running\s+in\s+(?:the\s+)?background|will\s+land|not\s+stopping\s+here|I(?:[\x27\u2019]ll|\s+will)\s+(?:report|follow\s+up)\s+(?:back|later)|I(?:[\x27\u2019]ll|\s+will)\s+(?:draft|write|do|finish|score|process|handle|cover)\s+(?:the\s+)?(?:remaining|rest)\b|I(?:[\x27\u2019]m|\s+am)\s+continuing\b|(?:^|(?<=[\n.!?*_]))\s*continuing\s+(?:with|in|on|to)\b|\b(?:follows|comes)\s+next\b|\bup\s+next\b|\bfollows\s*[.)\]*]|\bcontinues\s+in\s+\S|in\s+(?:the|my)\s+next\s+(?:message|reply|turn|response)|(?:reply|say|type|answer)\s+\W{0,2}(?:continue|go|next|more)\b|dans\s+(?:le|mon)\s+prochain\s+message|la\s+suite\s+(?:arrive|suit|dans))'
match: not_contains
flags: i
target: last_message
---

A promise to continue in the closing 700 characters of the final message, the window the
promise-gate Stop hook reads. Keep the alternatives in sync with PROMISE_SRC in
scripts/promise_gate.py (a test compares them).
